"""Waybot — Telegram bot package (S2).

Import-isolated: if python-telegram-bot is missing or WAYPOINT_BOT_TOKEN
is unset, `build_application` returns None and the app runs bot-less.
Nothing outside this package imports FROM it except main.py lifespan.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application

    from app.db.store import DeskStore
    from app.events import EventSink

logger = logging.getLogger(__name__)


def build_application(
    token: str | None,
    sink: "EventSink",
    store: "DeskStore",
) -> "Application | None":
    """Build the bot Application, or None when the bot should be skipped.

    Returns None when:
    - token is falsy (WAYPOINT_BOT_TOKEN unset/empty)
    - python-telegram-bot is not installed

    The caller (main.py lifespan) starts/stops the returned Application;
    this function only wires handlers and the sink subscriber. The
    notify_handler is stashed on the Application so lifespan can
    unsubscribe it on shutdown (M2 fix).
    """
    if not token:
        logger.info("WAYPOINT_BOT_TOKEN not set — bot disabled")
        return None

    try:
        from telegram.ext import ApplicationBuilder
    except ImportError:
        logger.warning(
            "python-telegram-bot not installed — bot disabled"
        )
        return None

    from app.bot.handlers import register_handlers
    from app.bot.notify import make_notify_handler

    application = ApplicationBuilder().token(token).build()

    # Wire /start deep-link + photo + callback + typed-entry handlers.
    register_handlers(application, store)

    # Stash the sink in bot_data so handlers can publish events.
    application.bot_data["sink"] = sink

    # Subscribe the notify handler to the domain-event sink so desk events
    # (travelers_complete, etc.) reach the manager's Telegram chat.
    # Stash the handler ref so lifespan can unsubscribe on shutdown (M2).
    notify_handler = make_notify_handler(application)
    sink.subscribe(notify_handler)
    application.bot_data["_notify_handler"] = notify_handler

    return application
