"""Telegram update handlers (S2 — /start deep-link only).

Photo ingest, confirm/fix inline callbacks, and the typed-entry fallback
state machine land in S3. This slice wires the deep-link bind so a tap
on `t.me/Bot?start=<token>` resolves the token→desk and upserts a
chat_bindings row.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.bot.session import SessionStore
from app.db.store import DeskStore

logger = logging.getLogger(__name__)

# Module-level session store (per-chat conversation state). Skeleton
# this slice — full state machine (awaiting-photo / awaiting-typed-field)
# lands in S3.
SESSIONS = SessionStore()


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start deep-link handler: parse `?start=<token>`, resolve via
    DeskStore.bind_chat, upsert chat_bindings, reply with confirmation
    or an error. The token is Telegram's deep-link payload — it arrives
    as the first element of `context.args`.
    """
    chat_id = str(update.effective_chat.id)

    # No deep-link payload → plain /start (no token).
    if not context.args:
        await update.message.reply_text(
            "Welcome to Waybot! Use the share link from your manager "
            "to get started."
        )
        return

    token = context.args[0]
    store: DeskStore = context.bot_data["store"]

    # L1 fix: bind_chat is sync (opens a DB session); run off the event
    # loop so it doesn't block the bot's update processing.
    # L2 fix: catch store errors and reply with a generic message so the
    # traveler doesn't get silence.
    try:
        result = await asyncio.to_thread(store.bind_chat, chat_id, token)
    except Exception:  # noqa: BLE001 — traveler must always get a reply
        logger.exception("bind_chat failed for chat %s (isolated)", chat_id)
        await update.message.reply_text(
            "⚠️ Something went wrong. Please try again in a moment."
        )
        return

    if result is None:
        await update.message.reply_text(
            "⚠️ That link isn't valid, has expired, or the team is full. "
            "Ask your manager for a fresh share link."
        )
        return

    desk_id, slot = result
    # Record the binding in the per-chat session (S3 will track
    # awaiting-photo / awaiting-typed-field here).
    SESSIONS.bind(chat_id, desk_id, slot)

    await update.message.reply_text(
        f"✅ You're linked to desk {desk_id[:8]}… as traveler #{slot}.\n"
        "Send a photo of your passport when you're ready."
    )
    logger.info("chat %s bound to desk %s slot %d", chat_id, desk_id, slot)


def register_handlers(application: Application, store: DeskStore) -> None:
    """Wire the bot's command handlers and inject the store into bot_data."""
    application.bot_data["store"] = store
    application.add_handler(CommandHandler("start", _start))
    # Global error handler: one bad update can't kill the polling task.
    application.add_error_handler(_error_handler)


async def _error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log and swallow — one bad update must never crash the bot."""
    logger.exception(
        "Unhandled exception in update %s (isolated)",
        update,
        exc_info=context.error,
    )
