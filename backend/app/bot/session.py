"""Per-chat conversation state (S3 — full state machine).

Keyed by telegram_chat_id. Tracks which desk/slot the chat is bound to
and which phase of the passport-capture flow the traveler is in:

  idle → awaiting_photo → awaiting_confirm → (optional) awaiting_typed → done

In-memory only — restartable from the chat_bindings DB table. The session
is the fast-path lookup so the photo handler doesn't hit the DB on every
update.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Phase = Literal[
    "idle",
    "awaiting_photo",
    "awaiting_confirm",
    "awaiting_typed",
    "done",
]


@dataclass
class ChatSession:
    """One traveler's conversation state."""

    desk_id: str
    slot: int
    phase: Phase = "awaiting_photo"
    # Stash the message_id of the traveler's photo so we can deleteMessage
    # after extraction (image bytes never persisted — Telegram retains it
    # server-side otherwise).
    photo_message_id: int | None = None
    # Stash the confirm-card message_id so we can edit/remove it on redo.
    confirm_message_id: int | None = None


class SessionStore:
    """In-memory per-chat session registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def bind(self, chat_id: str, desk_id: str, slot: int) -> ChatSession:
        """Create or update the session for a chat. A re-bind (same chat,
        new token) replaces the session — the traveler switched desks.
        Phase starts at 'awaiting_photo' so the traveler is immediately
        prompted to send their passport."""
        session = ChatSession(desk_id=desk_id, slot=slot, phase="awaiting_photo")
        self._sessions[chat_id] = session
        return session

    def get(self, chat_id: str) -> ChatSession | None:
        return self._sessions.get(chat_id)

    def remove(self, chat_id: str) -> None:
        self._sessions.pop(chat_id, None)
