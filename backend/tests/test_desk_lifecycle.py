"""S1 TRACER — the invite gate: seed-without-start -> confirm -> cycle fires.

Each test here FAILS against pre-S1 code: the `gated` seed field, the
/confirm endpoint, and the lifecycle column do not exist yet, so the gated
seed would start the cycle immediately (desk lands in DESKS) and /confirm
would 404.

Isolation mirrors test_desk_pipe: a throwaway SQLite file (never the shared
waypoint.db) and a stubbed agent/auditor so no live Atlas/Qwen call runs.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.loop import DeskAgent
from app.api import routes
from app.db import database
from app.main import app


class _StubAtlas:
    """Deterministic stand-in (S1 never searches; an empty result closes)."""

    def search(self, origin, dest, dep, pax):
        return []


class _StubAuditor:
    async def read(self, mandate, positions, ledger_tail, policy_breaches):
        return ("stub line", "agent")


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_waypoint.db'}",
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
def stub_agent(monkeypatch):
    monkeypatch.setattr(
        routes, "AGENT", DeskAgent(step_budget=12, atlas=_StubAtlas())
    )


@pytest.fixture()
def stub_auditor(monkeypatch):
    monkeypatch.setattr(routes, "AUDITOR", _StubAuditor())


def test_seed_does_not_start_cycle(tmp_db, stub_agent, stub_auditor):
    """A gated seed persists 'awaiting_travelers' + token + code hash and
    does NOT start the cycle — no DeskState/task is registered."""
    with TestClient(app) as client:
        resp = client.post("/api/desk/seed", json={"gated": True, "team_size": 3})
        assert resp.status_code == 200
        body = resp.json()
        desk_id = body["desk_id"]
        # Token is URL-safe and within the deep-link length limit.
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body["invite_token"])
        # The one-time plaintext code rides the response; only its hash is stored.
        assert body["confirmation_code"]
        # No cycle started: the desk is NOT in the live registry.
        assert desk_id not in routes.DESKS

    # Persisted lifecycle is the held state, and the plaintext code is not
    # recoverable from the store (hash only).
    assert routes.STORE.get_lifecycle(desk_id) == "awaiting_travelers"
    _token, code_hash = routes.STORE.get_invite(desk_id)
    assert code_hash and body["confirmation_code"] not in code_hash


def test_confirm_wrong_code_no_start(tmp_db, stub_agent, stub_auditor):
    """A wrong code -> 403 and no state change: still awaiting, no cycle."""
    with TestClient(app) as client:
        body = client.post("/api/desk/seed", json={"gated": True}).json()
        desk_id = body["desk_id"]
        resp = client.post(f"/api/desk/{desk_id}/confirm", json={"code": "WRONGONE"})
        assert resp.status_code == 403
        assert desk_id not in routes.DESKS

    assert routes.STORE.get_lifecycle(desk_id) == "awaiting_travelers"


def test_confirm_starts_cycle(tmp_db, stub_agent, stub_auditor):
    """The right code flips lifecycle 'released' and fires the cycle —
    a DeskState/task now exists, and the cycle runs to close."""
    with TestClient(app) as client:
        body = client.post("/api/desk/seed", json={"gated": True}).json()
        desk_id = body["desk_id"]
        resp = client.post(
            f"/api/desk/{desk_id}/confirm", json={"code": body["confirmation_code"]}
        )
        assert resp.status_code == 200
        # The shared resume primitive registered the desk and started the task.
        assert desk_id in routes.DESKS
        assert routes.DESKS[desk_id].task is not None
        # Join the background cycle so it completes before teardown.
        final = client.get(f"/api/desk/{desk_id}/close")
        assert final.status_code == 200
        assert final.json()["result"]["status"] == "closed"

    assert routes.STORE.get_lifecycle(desk_id) == "released"


def test_release_cas_is_single_winner(tmp_db, stub_agent, stub_auditor):
    """H1 guard: the release is an atomic compare-and-set, so exactly ONE
    caller ever flips awaiting_travelers->released. Two concurrent correct
    confirms would both pass the code check, but only the CAS winner starts
    the cycle — the loser gets rowcount 0 and no second _run_desk spawns."""
    with TestClient(app) as client:
        desk_id = client.post("/api/desk/seed", json={"gated": True}).json()["desk_id"]

    # The primitive under the race: first release wins, second loses.
    assert routes.STORE.try_release(desk_id) is True
    assert routes.STORE.try_release(desk_id) is False
    assert routes.STORE.get_lifecycle(desk_id) == "released"
