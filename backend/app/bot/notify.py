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

# bot_data key holding {desk_id: approval_token}. The per-round manager
# credential is delivered to the manager's chat as BUTTONS, never as text,
# and the token itself stays in the bot process — Telegram callback_data
# is capped at 64 bytes, so the button carries only the desk_id and the
# handler looks the token up here.
APPROVAL_TOKENS_KEY = "_approval_tokens"


def make_notify_handler(application: "Application") -> DeskEventHandler:
    """Build and return the async handler that the EventSink calls."""

    async def _on_event(event: DeskEvent) -> None:
        if event.type == "travelers_complete":
            await _notify_travelers_complete(application, event)
        elif event.type == "pending_approval":
            await _notify_pending_approval(application, event)
        # Other event types are no-ops this slice (S6/S9).

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


def _itinerary_lines(itinerary: dict) -> list[str]:
    """Render the approved-offer identity as plain text.

    Flight identity only — the snapshot carries no traveler PII by
    construction, so nothing here needs masking. Carrier/cabin print only
    once `map_offer` carries them (S6); today they are absent and the
    lines simply omit them rather than printing empty labels.
    """
    lines: list[str] = []
    for seg in itinerary.get("segments") or []:
        carrier = seg.get("carrier") or ""
        flight = " ".join(x for x in (carrier, seg.get("flight_number", "")) if x)
        lines.append(
            f"• {seg.get('dep_airport', '???')} → "
            f"{seg.get('arr_airport', '???')}"
            + (f"  {flight}" if flight.strip() else "")
            + (f"  dep {seg.get('dep_time', '')[:16].replace('T', ' ')}"
               if seg.get("dep_time") else "")
        )
    if not lines:
        lines.append(
            f"• {itinerary.get('origin', '???')} → "
            f"{itinerary.get('dest', '???')} on "
            f"{itinerary.get('depart_date', 'the requested date')}"
        )
    return lines


async def _notify_pending_approval(
    application: "Application", event: DeskEvent
) -> None:
    """Push the priced itinerary + Approve/Hold buttons to the manager.

    Same manager-identity seam (and same documented-open limitation) as
    travelers_complete: the chat id rides the payload, and an absent one
    is logged and skipped rather than guessed. The round's approval token
    is stashed in bot_data — the buttons carry only the desk_id, because
    Telegram caps callback_data at 64 bytes.
    """
    token = event.payload.get("approval_token")
    if token:
        application.bot_data.setdefault(APPROVAL_TOKENS_KEY, {})[
            event.desk_id
        ] = token

    manager_chat_id = event.payload.get("manager_chat_id")
    if not manager_chat_id:
        logger.warning(
            "pending_approval for desk %s but no manager_chat_id in payload",
            event.desk_id,
        )
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    itinerary = event.payload.get("itinerary") or {}
    price = event.payload.get("price", "?")
    currency = event.payload.get("currency", "")
    reapproval = event.payload.get("reapproval_count", 0)
    header = (
        "🛫 Approve this trip?" if not reapproval
        else "🔁 The approved flight expired — approve the replacement?"
    )
    text = "\n".join([
        header,
        f"{itinerary.get('trip_label', 'Trip')} — {price} {currency}".strip(),
        *_itinerary_lines(itinerary),
        "",
        "Nothing is booked until you tap Approve. "
        "The price and your budget cap are re-checked in code at booking "
        "time, so a move beyond them escalates instead of booking.",
    ])
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"wbapprove:{event.desk_id}"),
        InlineKeyboardButton("⏸ Hold", callback_data=f"wbhold:{event.desk_id}"),
    ]])
    try:
        await application.bot.send_message(
            chat_id=int(manager_chat_id), text=text, reply_markup=keyboard
        )
    except Exception:  # noqa: BLE001 — fire-and-forget
        logger.exception(
            "Failed to send pending_approval to chat %s (isolated)",
            manager_chat_id,
        )
