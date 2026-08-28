"""Per-chat conversation state (S2 skeleton).

Keyed by telegram_chat_id. Tracks which desk/slot the chat is bound to,
and (in S3) which phase of the passport-capture flow the traveler is in
(awaiting-photo, awaiting-typed-field, done).

In-memory only — restartable from the chat_bindings DB table. The session
is the fast-path lookup so the photo handler doesn't hit the DB on every
update.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ChatSession:
    """One traveler's conversation state."""

    desk_id: str
    slot: int
    # S3 adds: "awaiting_photo" | "awaiting_typed" | "done"
    phase: Literal["idle", "awaiting_photo", "awaiting_typed", "done"] = "idle"


class SessionStore:
    """In-memory per-chat session registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    def bind(self, chat_id: str, desk_id: str, slot: int) -> ChatSession:
        """Create or update the session for a chat. A re-bind (same chat,
        new token) replaces the session — the traveler switched desks."""
        session = ChatSession(desk_id=desk_id, slot=slot)
        self._sessions[chat_id] = session
        return session

    def get(self, chat_id: str) -> ChatSession | None:
        return self._sessions.get(chat_id)

    def remove(self, chat_id: str) -> None:
        self._sessions.pop(chat_id, None)
