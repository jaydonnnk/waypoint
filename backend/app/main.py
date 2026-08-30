"""Waypoint backend — FastAPI application."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import STORE, router
from app.db.database import init_db
from app.events import SINK

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Default allowed origins — the exact pre-S10 list. A deployment EXTENDS
# this list via WAYPOINT_CORS_ORIGIN (comma-separated); the localhost
# defaults always stay allowed.
CORS_ORIGIN_ENV = "WAYPOINT_CORS_ORIGIN"
DEFAULT_CORS_ORIGINS = ["http://localhost:3000", "http://localhost:3001"]


def _cors_origins() -> list[str]:
    """Env-overridable CORS origins (S10, ADR 0007). Unset/empty keeps
    the default localhost list; otherwise the comma-separated value
    (whitespace-trimmed, blank entries dropped) EXTENDS the defaults —
    a deployment origin never locks the local dev frontend out."""
    raw = os.environ.get(CORS_ORIGIN_ENV)
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    merged = list(DEFAULT_CORS_ORIGINS)
    for origin in origins:
        if origin not in merged:
            merged.append(origin)
    return merged


# Backoff ceiling for the supervised bot restart (seconds).
_BOT_BACKOFF_MAX = 30.0
# Max consecutive restart attempts before giving up (M1 circuit breaker).
_BOT_MAX_CONSECUTIVE_FAILURES = 5
# The single in-process pub/sub bus now lives in app.events (S5: the
# agent loop publishes to it as well, and routes.py builds the AGENT).
# Re-exported here so `app.main.SINK` keeps resolving.


def _is_unrecoverable(exc: BaseException) -> bool:
    """True for errors that will never self-heal on retry (M1 fix).

    InvalidToken / Forbidden from a revoked bot token would hammer the
    Telegram API indefinitely under the retry loop. Catch them here and
    give up immediately."""
    try:
        from telegram.error import Forbidden, InvalidToken
        return isinstance(exc, (InvalidToken, Forbidden))
    except ImportError:
        return False


async def _supervised_bot(application) -> None:
    """Run the bot's polling loop with backoff restart.

    python-telegram-bot's Application.run_polling() is synchronous and
    blocking; instead we use initialize/start/updater.start_polling/stop
    to integrate with the FastAPI event loop. A crash in the polling loop
    restarts after exponential backoff (capped), and the global error
    handler on the Application catches per-update exceptions so a single
    bad update never kills the whole task.

    M1 fix: unrecoverable errors (InvalidToken, Forbidden) bail
    immediately; a consecutive-failure budget caps transient retries.
    """
    backoff = 1.0
    consecutive_failures = 0
    while True:
        try:
            await application.initialize()
            # Capture the REAL bot identity (getMe ran inside initialize)
            # so GET /api/waybot can hand the frontend the actual username
            # for the t.me share link — never a hardcoded name.
            try:
                from app.bot import set_bot_username

                set_bot_username(application.bot.username)
                logger.info(
                    "Waybot identity: @%s", application.bot.username
                )
            except Exception:  # noqa: BLE001 — identity is cosmetic
                logger.exception("Waybot username capture failed (ignored)")
            await application.start()
            await application.updater.start_polling(drop_pending_updates=True)
            logger.info("Waybot polling started")
            consecutive_failures = 0  # reset on successful start
            # Block until the task is cancelled (lifespan shutdown).
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.info("Waybot supervised task cancelled — shutting down")
            raise
        except Exception as exc:  # noqa: BLE001 — restart on transient crash
            if _is_unrecoverable(exc):
                logger.error(
                    "Waybot token invalid or revoked — giving up: %s", exc
                )
                return
            consecutive_failures += 1
            if consecutive_failures >= _BOT_MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "Waybot hit %d consecutive failures — giving up",
                    consecutive_failures,
                )
                return
            logger.exception(
                "Waybot polling crashed (%d/%d) — restarting in %.0fs",
                consecutive_failures,
                _BOT_MAX_CONSECUTIVE_FAILURES,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BOT_BACKOFF_MAX)
        finally:
            try:
                if application.updater and application.updater.running:
                    await application.updater.stop()
                if application.running:
                    await application.stop()
                await application.shutdown()
            except Exception:  # noqa: BLE001 — cleanup best-effort
                logger.exception("Waybot cleanup error (ignored)")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Wire the desk tables (mandate/positions/ledger/budgets) on startup —
    # S1's first real DB writes. Demo data: drop-and-recreate guard inside.
    init_db()

    # S2: build + start the Telegram bot, gated on WAYPOINT_BOT_TOKEN.
    # Absent/empty → bot skipped, app runs exactly as before.
    bot_task = None
    notify_handler = None
    bot_token = os.environ.get("WAYPOINT_BOT_TOKEN")
    try:
        from app.bot import build_application

        application = build_application(bot_token, SINK, STORE)
        if application is not None:
            notify_handler = application.bot_data.get("_notify_handler")
            bot_task = asyncio.create_task(_supervised_bot(application))
            logger.info("Waybot wired into lifespan (supervised task)")
    except Exception:  # noqa: BLE001 — bot failure must never block the app
        logger.exception("Waybot build failed — app continues bot-less")

    yield

    # Shutdown: unsubscribe the notify handler (M2) then cancel the bot task.
    if notify_handler is not None:
        SINK.unsubscribe(notify_handler)
    if bot_task is not None:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Waypoint backend", lifespan=lifespan)

# The Next.js dev server (Screen 1-3) calls the backend cross-origin, and
# EventSource honors CORS — so the frontend origin must be allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/waybot")
async def waybot() -> dict:
    """The live Waybot's Telegram username, derived from WAYPOINT_BOT_TOKEN
    via getMe at bot startup. `null` when the app runs bot-less (no
    token / build failed) — the frontend then hides the share link rather
    than rendering a broken t.me URL. Lives here (not in api/routes.py)
    to keep app.bot import-isolated: only main.py imports from it."""
    from app.bot import get_bot_username

    return {"username": get_bot_username()}
