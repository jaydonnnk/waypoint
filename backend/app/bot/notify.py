"""Domain-event sink subscriber — turns DeskEvents into Telegram messages.

S2 scope: `travelers_complete` → ping the manager. The manager's chat_id
is passed in the event payload (`manager_chat_id`); S3 populates this
from the backend when firing the event (the backend is the source of
truth for traveler counts, not the bot — 03-program-design.md §2).
For this slice the manager ping is the only implemented event; S5/S6/S9
add pending_approval, ticketed, and disruption handlers.

The subscriber is FIRE-AND-FORGET from the sink's perspective: any
exception here is caught by EventSink._deliver and logged, never
propagated to the cycle loop.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application

from app.events import DeskEvent, DeskEventHandler

logger = logging.getLogger(__name__)


def make_notify_handler(application: "Application") -> DeskEventHandler:
    """Build and return the async handler that the EventSink calls."""

    async def _on_event(event: DeskEvent) -> None:
        if event.type == "travelers_complete":
            await _notify_travelers_complete(application, event)
        # Other event types are no-ops this slice (S5/S6/S9).

    return _on_event


async def _notify_travelers_complete(
    application: "Application", event: DeskEvent
) -> None:
    """Send a 'all travelers verified' ping to the manager.

    The payload carries `manager_chat_id` (set by the test hook or by the
    backend when it fires the event in S3). If absent, log and skip —
    the manager hasn't bound a chat yet.
    """
    manager_chat_id = event.payload.get("manager_chat_id")
    if not manager_chat_id:
        logger.warning(
            "travelers_complete for desk %s but no manager_chat_id in payload",
            event.desk_id,
        )
        return

    desk_id_short = event.desk_id[:8]
    text = (
        f"✅ All travelers verified for desk {desk_id_short}…\n"
        "Open the desk page to review names and enter your release code."
    )
    try:
        await application.bot.send_message(
            chat_id=int(manager_chat_id), text=text
        )
    except Exception:  # noqa: BLE001 — fire-and-forget
        logger.exception(
            "Failed to send travelers_complete ping to chat %s (isolated)",
            manager_chat_id,
        )
