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
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
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
# GET /api/waybot — the share link's bot identity (task 6)
# ---------------------------------------------------------------------------


def _fake_application(username: str) -> MagicMock:
    """A stand-in Application whose initialize/start/shutdown are awaitable
    and whose bot reports a fixed username — the supervised startup must
    capture it without ever touching Telegram."""
    application = MagicMock()
    application.bot.username = username
    application.bot_data = {}
    application.initialize = AsyncMock()
    application.start = AsyncMock()
    application.stop = AsyncMock()
    application.shutdown = AsyncMock()
    application.updater.start_polling = AsyncMock()
    application.updater.stop = AsyncMock()
    application.updater.running = True
    application.running = True
    return application


def _poll_waybot(client: TestClient, attempts: int = 100) -> dict:
    """The supervised bot task starts asynchronously with lifespan — poll
    until the captured username lands instead of racing the first GET."""
    for _ in range(attempts):
        body = client.get("/api/waybot").json()
        if body.get("username") is not None:
            return body
        time.sleep(0.05)
    return client.get("/api/waybot").json()


class TestWaybotEndpoint:
    """GET /api/waybot exposes the live bot's Telegram username (derived
    from WAYPOINT_BOT_TOKEN via getMe at startup) and null when bot-less.

    FAILS against pre-task-6 code: the endpoint did not exist (404), so
    neither assertion could pass — the share link's username was hardcoded
    in the frontend instead.
    """

    @pytest.fixture(autouse=True)
    def _reset_holder(self):
        """The captured username is module state in app.bot — clear it
        before AND after each test so results are order-independent."""
        from app import bot as bot_module

        bot_module.set_bot_username(None)
        yield
        bot_module.set_bot_username(None)

    def test_no_token_username_null(self, tmp_db):
        """Bot-less app (conftest unsets WAYPOINT_BOT_TOKEN) -> null."""
        from app.main import app

        with TestClient(app) as client:
            resp = client.get("/api/waybot")
            assert resp.status_code == 200
            assert resp.json() == {"username": None}

    def test_stubbed_bot_username_captured(self, tmp_db, monkeypatch):
        """With a faked bot build, the supervised startup captures
        application.bot.username and the endpoint returns it."""
        from app import bot as bot_module
        from app.main import app

        fake = _fake_application("waypointdemobot")
        monkeypatch.setattr(
            bot_module,
            "build_application",
            lambda token, sink, store: fake,
        )

        with TestClient(app) as client:
            body = _poll_waybot(client)
            assert body == {"username": "waypointdemobot"}
            # The real startup path ran on the fake (initialize called).
            fake.initialize.assert_awaited()


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


# ---------------------------------------------------------------------------
# S5 approval push: _notify_pending_approval (task 14)
# ---------------------------------------------------------------------------


def _approval_payload(*, manager_chat_id=None, token="appr-token-1") -> dict:
    """Fabricate the pending_approval payload approval.py publishes:
    token, reason, reapproval_count, identity-snapshot itinerary, price,
    currency, manager_chat_id (None in production — seam S3 M10)."""
    return {
        "approval_token": token,
        "reason": "pre-trip approval requested",
        "reapproval_count": 0,
        "itinerary": {
            "trip_label": "Offsite Q3",
            "origin": "SIN",
            "dest": "HND",
            "depart_date": "2026-09-15",
            "currency": "USD",
            "segments": [{
                "dep_airport": "SIN",
                "arr_airport": "HND",
                "dep_time": "2026-09-15T08:30:00",
                "arr_time": "2026-09-15T16:45:00",
                "flight_number": "12",
                "carrier": "SQ",
                "direction": "outbound",
            }],
        },
        "price": "450.00",
        "currency": "USD",
        "manager_chat_id": manager_chat_id,
    }


class TestNotifyPendingApproval:
    """pending_approval → itinerary + Approve/Hold buttons to the manager."""

    @pytest.mark.asyncio
    async def test_notify_pending_approval_sends_buttons(self):
        """With manager_chat_id set: send_message carries the itinerary
        essentials, wbapprove:/wbhold: buttons, and the token is stashed
        in bot_data keyed by desk_id."""
        from app.bot.notify import APPROVAL_TOKENS_KEY, make_notify_handler

        mock_bot = AsyncMock()
        mock_application = MagicMock()
        mock_application.bot = mock_bot
        mock_application.bot_data = {}  # real dict — token stash is asserted

        handler = make_notify_handler(mock_application)
        desk_id = "desk-appr-1"
        payload = _approval_payload(manager_chat_id="424242")

        await handler(DeskEvent(
            type="pending_approval", desk_id=desk_id, payload=payload
        ))

        mock_bot.send_message.assert_called_once()
        kwargs = mock_bot.send_message.call_args.kwargs
        assert kwargs["chat_id"] == 424242
        text = kwargs["text"]
        # Itinerary essentials: route, flight, price line, consent language.
        assert "SIN" in text and "HND" in text
        assert "SQ 12" in text
        assert "450.00 USD" in text
        assert "Approve this trip" in text
        # Buttons: exactly Approve + Hold with the desk_id-only payloads.
        markup = kwargs["reply_markup"]
        buttons = [b for row in markup.inline_keyboard for b in row]
        assert [b.callback_data for b in buttons] == [
            f"wbapprove:{desk_id}", f"wbhold:{desk_id}"
        ]
        # Token stays in-process — never in the 64-byte callback_data.
        assert mock_application.bot_data[APPROVAL_TOKENS_KEY][desk_id] == (
            payload["approval_token"]
        )

    @pytest.mark.asyncio
    async def test_notify_pending_approval_no_manager_chat_skips(self):
        """No manager_chat_id in payload → logged and skipped, no send."""
        from app.bot.notify import make_notify_handler

        mock_bot = AsyncMock()
        mock_application = MagicMock()
        mock_application.bot = mock_bot
        mock_application.bot_data = {}

        handler = make_notify_handler(mock_application)
        await handler(DeskEvent(
            type="pending_approval",
            desk_id="desk-appr-2",
            payload=_approval_payload(manager_chat_id=None),
        ))

        mock_bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# S5 approval click: _on_approval_click (task 14)
# ---------------------------------------------------------------------------


def _fake_httpx(monkeypatch, status_code: int) -> list:
    """Replace httpx.AsyncClient with a capturing async-context stand-in;
    returns the list of created clients (empty ⇒ no HTTP call was made)."""
    created: list = []

    class _Client:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.posts: list = []
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            self.posts.append((url, json))
            resp = MagicMock()
            resp.status_code = status_code
            return resp

    monkeypatch.setattr("httpx.AsyncClient", _Client)
    return created


class TestApprovalClick:
    """Manager taps Approve/Hold → POST /api/desk/{id}/approve."""

    def _make_query(self, data: str) -> MagicMock:
        query = MagicMock()
        query.data = data
        query.edit_message_text = AsyncMock()
        return query

    def _make_context(self, store: DeskStore, tokens: dict) -> MagicMock:
        from app.bot.notify import APPROVAL_TOKENS_KEY

        context = MagicMock()
        context.bot_data = {"store": store, APPROVAL_TOKENS_KEY: tokens}
        return context

    @pytest.mark.asyncio
    async def test_on_approval_click_approve_200(
        self, tmp_db, store, monkeypatch
    ):
        """200 → confirmation edit, correct URL/body, token spent."""
        from app.bot.handlers import _on_approval_click

        desk_id = "desk-appr-200"
        created = _fake_httpx(monkeypatch, 200)
        tokens = {desk_id: "appr-token-200"}
        context = self._make_context(store, tokens)
        query = self._make_query(f"wbapprove:{desk_id}")

        await _on_approval_click(query, context, "chat_manager")

        assert len(created) == 1
        url, body = created[0].posts[0]
        assert url.endswith(f"/api/desk/{desk_id}/approve")
        assert body == {"choice": "approve", "code": "appr-token-200"}
        query.edit_message_text.assert_awaited_once()
        assert "✅" in query.edit_message_text.await_args.args[0]
        # One-shot: the token is dropped after a successful decision.
        assert desk_id not in tokens

    @pytest.mark.asyncio
    async def test_on_approval_click_approve_410(
        self, tmp_db, store, monkeypatch
    ):
        """410 (round already spent) → 'already decided' edit."""
        from app.bot.handlers import _on_approval_click

        desk_id = "desk-appr-410"
        _fake_httpx(monkeypatch, 410)
        context = self._make_context(store, {desk_id: "appr-token-410"})
        query = self._make_query(f"wbapprove:{desk_id}")

        await _on_approval_click(query, context, "chat_manager")

        query.edit_message_text.assert_awaited_once()
        assert "already decided" in query.edit_message_text.await_args.args[0]

    @pytest.mark.asyncio
    async def test_on_approval_click_approve_403(
        self, tmp_db, store, monkeypatch
    ):
        """403 (credential mismatch) → unauthorised edit."""
        from app.bot.handlers import _on_approval_click

        desk_id = "desk-appr-403"
        _fake_httpx(monkeypatch, 403)
        context = self._make_context(store, {desk_id: "appr-token-403"})
        query = self._make_query(f"wbapprove:{desk_id}")

        await _on_approval_click(query, context, "chat_manager")

        query.edit_message_text.assert_awaited_once()
        assert "isn't authorised" in (
            query.edit_message_text.await_args.args[0]
        )

    @pytest.mark.asyncio
    async def test_on_approval_click_refuses_traveler_binding(
        self, tmp_db, store, monkeypatch
    ):
        """A chat bound as a traveler on the SAME desk is refused before
        any HTTP call, and the stashed token is NOT spent."""
        from app.bot.handlers import _on_approval_click

        desk_id, invite_token = _seed_gated_desk(store)
        store.bind_chat("chat_traveler", invite_token)

        created = _fake_httpx(monkeypatch, 200)
        tokens = {desk_id: "appr-token-x"}
        context = self._make_context(store, tokens)
        query = self._make_query(f"wbapprove:{desk_id}")

        await _on_approval_click(query, context, "chat_traveler")

        assert created == []  # no httpx client ever constructed
        query.edit_message_text.assert_awaited_once()
        assert "Travellers can't approve" in (
            query.edit_message_text.await_args.args[0]
        )
        assert desk_id in tokens  # refusal spends nothing
