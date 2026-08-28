"""S2 — Waybot skeleton: bind_chat, build_application, notify subscriber,
/start handler.

Each test here FAILS against pre-S2 code:
- bind_chat does not exist on DeskStore pre-S2 → AttributeError.
- build_application does not exist in app.bot pre-S2 → ImportError.
- The notify subscriber is new code with no pre-S2 equivalent.
- The /start handler is new code with no pre-S2 equivalent.

Isolation: throwaway SQLite (never waypoint.db), stubbed bot (no live
Telegram). Tests use a real DeskStore + real EventSink on the temp DB.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.store import DeskStore
from app.events import DeskEvent, EventSink


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_waybot.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False),
    )
    database.Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def store():
    return DeskStore()


def _seed_gated_desk(
    store: DeskStore, *, team_size: int = 4, token: str = "test-token-abc123"
) -> tuple[str, str]:
    """Seed a gated desk, return (desk_id, invite_token)."""
    from app import fixture

    mandate, positions, budgets = fixture.seeded_portfolio(team_size=team_size)
    desk_id = store.seed_desk(
        mandate,
        positions,
        budgets,
        lifecycle="awaiting_travelers",
        invite_token=token,
        code_hash="salt$fakehash",
    )
    return desk_id, token


# ---------------------------------------------------------------------------
# bind_chat tests
# ---------------------------------------------------------------------------


class TestBindChat:
    """bind_chat: token→desk→slot lookup + upsert."""

    def test_valid_token_binds(self, tmp_db, store):
        """A valid invite token resolves to the desk and assigns slot 1."""
        desk_id, token = _seed_gated_desk(store)
        result = store.bind_chat("chat_100", token)
        assert result is not None
        assert result[0] == desk_id
        assert result[1] == 1  # first slot

    def test_unknown_token_returns_none(self, tmp_db, store):
        """An unknown token returns None — no binding created."""
        _seed_gated_desk(store)
        result = store.bind_chat("chat_100", "nonexistent-token")
        assert result is None
        # L3 fix: verify no binding row was created.
        from app.db.schema import ChatBindingRow

        with database.SessionLocal() as session:
            count = session.query(ChatBindingRow).count()
        assert count == 0

    def test_rebind_same_chat_same_desk_idempotent(self, tmp_db, store):
        """Re-sending the same token from the same chat reuses the slot
        (no new row — idempotent)."""
        desk_id, token = _seed_gated_desk(store)
        r1 = store.bind_chat("chat_100", token)
        r2 = store.bind_chat("chat_100", token)
        assert r1 == r2  # same (desk_id, slot)

    def test_second_chat_gets_next_slot(self, tmp_db, store):
        """A second chat binding to the same desk gets the next slot."""
        desk_id, token = _seed_gated_desk(store)
        r1 = store.bind_chat("chat_100", token)
        r2 = store.bind_chat("chat_200", token)
        assert r1[1] == 1
        assert r2[1] == 2
        assert r1[0] == r2[0] == desk_id

    def test_rebind_different_desk_replaces(self, tmp_db, store):
        """If a chat was bound to desk A and now taps desk B's link, the
        binding switches to desk B (new slot)."""
        from app import fixture

        desk_id_a, token_a = _seed_gated_desk(store)
        # Seed a second desk with a different token.
        mandate, positions, budgets = fixture.seeded_portfolio()
        token_b = "test-token-desk-b"
        desk_id_b = store.seed_desk(
            mandate,
            positions,
            budgets,
            lifecycle="awaiting_travelers",
            invite_token=token_b,
            code_hash="salt$fakehash",
        )
        store.bind_chat("chat_100", token_a)
        r2 = store.bind_chat("chat_100", token_b)
        assert r2[0] == desk_id_b
        assert r2[1] == 1

    # --- M4 gate tests ---

    def test_bind_rejects_released_desk(self, tmp_db, store):
        """bind_chat returns None when the desk is already released."""
        desk_id, token = _seed_gated_desk(store)
        store.set_lifecycle(desk_id, "released")
        result = store.bind_chat("chat_100", token)
        assert result is None

    def test_bind_rejects_when_full(self, tmp_db, store):
        """bind_chat returns None when team_size bindings already exist."""
        desk_id, token = _seed_gated_desk(store, team_size=2)
        r1 = store.bind_chat("chat_100", token)
        r2 = store.bind_chat("chat_200", token)
        assert r1 is not None
        assert r2 is not None
        # Third chat exceeds team_size=2.
        r3 = store.bind_chat("chat_300", token)
        assert r3 is None


# ---------------------------------------------------------------------------
# build_application tests
# ---------------------------------------------------------------------------


class TestBuildApplication:
    """build_application returns None on falsy token or missing dep."""

    def test_none_on_falsy_token(self, tmp_db, store):
        """Empty/None token → None (bot disabled)."""
        from app.bot import build_application

        sink = EventSink()
        assert build_application(None, sink, store) is None
        assert build_application("", sink, store) is None

    def test_returns_application_on_valid_token(self, tmp_db, store):
        """A non-empty token + installed python-telegram-bot → Application."""
        from app.bot import build_application

        sink = EventSink()
        app = build_application("fake-bot-token:123", sink, store)
        # python-telegram-bot is installed, so we get a real Application.
        assert app is not None
        # The store is wired into bot_data.
        assert app.bot_data.get("store") is store

    def test_build_subscribes_to_sink(self, tmp_db, store):
        """M5 fix: build_application subscribes the notify handler to the
        sink — deleting that line would break this test."""
        from app.bot import build_application

        sink = EventSink()
        assert len(sink._subscribers) == 0
        app = build_application("fake-bot-token:123", sink, store)
        assert app is not None
        assert len(sink._subscribers) == 1
        # The handler is the same object stashed on bot_data (M2 ref).
        assert sink._subscribers[0] is app.bot_data["_notify_handler"]


# ---------------------------------------------------------------------------
# /start handler tests (M6)
# ---------------------------------------------------------------------------


class TestStartHandler:
    """The /start deep-link handler: parses the token, calls bind_chat,
    replies to the user."""

    @pytest.mark.asyncio
    async def test_start_valid_token(self, tmp_db, store):
        """Valid deep-link token → success reply with slot number."""
        from app.bot.handlers import _start

        desk_id, token = _seed_gated_desk(store)

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = [token]
        context.bot_data = {"store": store}

        await _start(update, context)

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args.args[0]
        assert "✅" in reply_text
        assert "#1" in reply_text  # slot 1

    @pytest.mark.asyncio
    async def test_start_bad_token(self, tmp_db, store):
        """Invalid deep-link token → warning reply, no binding."""
        from app.bot.handlers import _start

        _seed_gated_desk(store)

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = ["bogus-token"]
        context.bot_data = {"store": store}

        await _start(update, context)

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args.args[0]
        assert "⚠️" in reply_text

    @pytest.mark.asyncio
    async def test_start_no_args(self, tmp_db, store):
        """Plain /start with no deep-link payload → welcome message."""
        from app.bot.handlers import _start

        update = MagicMock()
        update.effective_chat.id = 12345
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.args = []
        context.bot_data = {"store": store}

        await _start(update, context)

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args.args[0]
        assert "welcome" in reply_text.lower() or "Welcome" in reply_text


# ---------------------------------------------------------------------------
# notify subscriber tests
# ---------------------------------------------------------------------------


class TestNotifySubscriber:
    """The notify handler turns travelers_complete into a manager ping."""

    @pytest.mark.asyncio
    async def test_travelers_complete_sends_message(self, tmp_db, store):
        """A travelers_complete event with a manager_chat_id triggers
        bot.send_message to that chat."""
        from app.bot.notify import make_notify_handler

        mock_bot = AsyncMock()
        mock_application = MagicMock()
        mock_application.bot = mock_bot

        handler = make_notify_handler(mock_application)

        event = DeskEvent(
            type="travelers_complete",
            desk_id="desk-abc-123",
            payload={"manager_chat_id": "99999"},
        )
        await handler(event)

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args
        assert call_kwargs.kwargs.get("chat_id") == 99999 or (
            call_kwargs.args and call_kwargs.args[0] == 99999
        )
        sent_text = (
            call_kwargs.kwargs.get("text", "")
            or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else "")
        )
        assert "verified" in sent_text.lower() or "✅" in sent_text

    @pytest.mark.asyncio
    async def test_travelers_complete_no_manager_chat_skips(
        self, tmp_db, store
    ):
        """travelers_complete without manager_chat_id → no send (log only)."""
        from app.bot.notify import make_notify_handler

        mock_bot = AsyncMock()
        mock_application = MagicMock()
        mock_application.bot = mock_bot

        handler = make_notify_handler(mock_application)

        event = DeskEvent(
            type="travelers_complete",
            desk_id="desk-abc-123",
            payload={},
        )
        await handler(event)

        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sink_delivers_to_notify(self, tmp_db, store):
        """End-to-end: publish a travelers_complete through the EventSink,
        and the subscribed notify handler receives it."""
        from app.bot.notify import make_notify_handler

        mock_bot = AsyncMock()
        mock_application = MagicMock()
        mock_application.bot = mock_bot

        sink = EventSink()
        handler = make_notify_handler(mock_application)
        sink.subscribe(handler)

        event = DeskEvent(
            type="travelers_complete",
            desk_id="desk-xyz-789",
            payload={"manager_chat_id": "55555"},
        )
        sink.publish(event)

        # Give the fire-and-forget task time to run.
        await asyncio.sleep(0.1)

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args
        assert call_kwargs.kwargs.get("chat_id") == 55555 or (
            call_kwargs.args and call_kwargs.args[0] == 55555
        )


# ---------------------------------------------------------------------------
# L3: invite_token index on shim-upgraded DBs
# ---------------------------------------------------------------------------


class TestInviteTokenIndex:
    """L3 fix: _ensure_invite_token_index creates the index on shim DBs."""

    def test_index_exists_after_init_db(self, tmp_db):
        """The invite_token index exists on mandate after create_all."""
        with database.engine.connect() as conn:
            rows = conn.execute(text("PRAGMA index_list(mandate)")).fetchall()
        index_names = [r[1] for r in rows]
        assert any("invite_token" in name for name in index_names)
