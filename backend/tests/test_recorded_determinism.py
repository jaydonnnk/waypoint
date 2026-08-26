"""S9 determinism gate — two full recorded cycles are byte-identical.

In-gate (non-live): the recorded client replays the Slice 0 capture; the
brain runs its deterministic fallback (DASHSCOPE_API_KEY deleted, so no
Qwen dependency); the store is a throwaway SQLite file; pace=0. The ONLY
normalized volatile fields are the documented ones — the desk uuid and
created_at/mark_at wall-clock stamps. Everything else compares raw,
including the full SSE event lists and the blotter rows.

Scenario: ONE held SIN->NRT position (pax 2, cost 270.00). The replayed
search marks it at the captured cheapest fare 323.00 (+19.6% vs cost,
past the long_haul band top 14%) so the fallback brain says book; the
write path replays verify + create, then ends EXACTLY the way the
composite capture ended — the captured pay TIMEOUT (error event, position
held, zero ledger writes by the cycle). One position = one search, so the
reprice fan-out has no cross-thread queue race to hide behind.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import fixture
from app.agent.loop import DeskAgent
from app.api import routes
from app.atlas.recorded import RecordedAtlasClient
from app.db import database
from app.db.schema import LedgerRow
from app.db.store import DeskStore
from app.main import app
from app.models import Budget, Mandate, Position

SEED_AT = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Point the app at a throwaway SQLite file — never the shared DB."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_recorded.db'}",
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
def recorded_env(monkeypatch):
    """Hermetic recorded cycle: fallback brain only (no Qwen key), the
    write gates armed so the replay runs the write path against the
    capture, and no atlas-mode ambiguity."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    monkeypatch.setenv("WAYPOINT_ATLAS_MODE", "recorded")


def seed_recorded_desk() -> str:
    """Seed ONE SIN->NRT position straight into the throwaway DB — the
    exact route/date/pax the capture holds a search envelope for."""
    desk_id = f"desk-{uuid4().hex[:8]}"
    mandate = Mandate(
        id=desk_id, holder="recorded-determinism", created_at=SEED_AT,
        budget_total=Decimal("12000.00"), authority_cap=Decimal("1500.00"),
        contingency_pct=0.05, currency="USD",
    )
    positions = [Position(
        id=f"{desk_id}-pos-1", trip_label="SIN\u2192NRT replay leg",
        origin="SIN", dest="NRT", depart_date=datetime(2026, 9, 4).date(),
        pax=2, status="held", cost_basis=Decimal("270.00"),
        mark_price=Decimal("350.00"), mark_at=SEED_AT, mark_stale=False,
    )]
    budgets = [Budget(
        desk_id=desk_id, period="2026-W38",
        allocated=Decimal("12000.00"), contingency=Decimal("600.00"),
    )]
    DeskStore().seed_desk(mandate, positions, budgets)
    return desk_id


def run_cycle(agent, desk_id):
    """Run one full cycle, collecting the wire events."""
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    result = asyncio.run(agent.run(desk_id, emit))
    return result, events


# The documented volatile fields — ONLY these are normalized before the
# byte comparison: the desk uuid (ids) and wall-clock stamps.
VOLATILE_KEYS = {"mark_at", "created_at"}


def normalize(obj, desk_id: str):
    """Replace the desk uuid (wherever it rides inside strings) and the
    volatile timestamp values; everything else passes through untouched."""
    if isinstance(obj, dict):
        return {
            key: ("<volatile>" if key in VOLATILE_KEYS else normalize(value, desk_id))
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [normalize(item, desk_id) for item in obj]
    if isinstance(obj, str):
        return obj.replace(desk_id, "<desk>")
    return obj


def dump(events, desk_id) -> str:
    return json.dumps(
        normalize(events, desk_id), sort_keys=True, default=str,
        ensure_ascii=False,
    )


def ledger_rows(desk_id) -> list[tuple]:
    """The blotter minus volatile columns (id/ts): kind/amount/position
    (normalized)/ref/note."""
    with database.SessionLocal() as session:
        rows = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == desk_id)
        ).scalars().all()
    return [
        (
            row.kind,
            str(row.amount),
            (row.position_id or "").replace(desk_id, "<desk>") or None,
            row.ref,
            row.note,
        )
        for row in rows
    ]


def test_two_recorded_cycles_are_byte_identical(tmp_db, recorded_env):
    """Deliverable 6: two full DeskAgent.run cycles against ONE shared
    recorded client (per-cycle cursor rewind via reset_ticketing_cache)
    emit byte-identical SSE event lists after normalizing ONLY the desk
    uuid and mark_at/created_at — and identical blotter rows."""
    atlas = RecordedAtlasClient()
    agent = DeskAgent(step_budget=12, atlas=atlas, store=DeskStore(), pace=0)

    desk_a, desk_b = seed_recorded_desk(), seed_recorded_desk()
    result_a, events_a = run_cycle(agent, desk_a)
    result_b, events_b = run_cycle(agent, desk_b)

    # The composite replay's honest shape, asserted once on cycle A.
    meta = next(e for e in events_a if e["type"] == "meta")
    assert meta["mode"] == "recorded ticketing (replay)"
    assert any("recorded Atlas replay" in d for d in meta["disclosures"])
    assert any(
        e["type"] == "error" and e["code"] == "TIMEOUT" for e in events_a
    )  # the cycle ends the way the capture ended
    assert result_a.status == "closed"
    assert result_a.comparison_mode is False
    assert result_a.pnl == Decimal("53.00")  # 323.00 mark vs 270.00 cost

    # BYTE-IDENTICAL after normalizing only the documented volatiles.
    assert dump(events_a, desk_a) == dump(events_b, desk_b)
    assert normalize(result_a.model_dump(mode="json"), desk_a) == \
        normalize(result_b.model_dump(mode="json"), desk_b)
    # Blotter tie-out: only the seed disclosure row — the cycle wrote
    # nothing (pay TIMEOUT), and both desks agree exactly.
    assert ledger_rows(desk_a) == ledger_rows(desk_b)
    assert all(kind == "adjust" for kind, *_ in ledger_rows(desk_a))


def test_recorded_cycle_spawns_no_subprocess(tmp_db, recorded_env, monkeypatch):
    """Boot-level honesty in-gate: a full recorded cycle makes ZERO
    subprocess spawns — no atlas-flight, nothing."""
    spawned: list = []

    def trap(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("recorded mode must never spawn a process")

    monkeypatch.setattr(subprocess, "run", trap)
    monkeypatch.setattr(subprocess, "Popen", trap)

    atlas = RecordedAtlasClient()
    agent = DeskAgent(step_budget=12, atlas=atlas, store=DeskStore(), pace=0)
    desk_id = seed_recorded_desk()
    result, events = run_cycle(agent, desk_id)

    assert result.status == "closed"
    assert spawned == []


def test_late_subscriber_gets_the_identical_stream(
    tmp_db, recorded_env, monkeypatch
):
    """Late-subscriber parity: a stream client that connects AFTER the
    recorded cycle settled receives the FULL buffered replay — identical
    to the direct-emit list off the SAME agent after normalizing ONLY
    the documented volatile fields (desk uuid + created_at/mark_at).
    Driven through routes.py exactly like test_desk_pipe.py: monkeypatch
    routes.AGENT with the recorded agent, then seed + stream endpoints."""
    atlas = RecordedAtlasClient()
    agent = DeskAgent(step_budget=12, atlas=atlas, store=DeskStore(), pace=0)

    # Reference cycle: direct emits, SAME seeded portfolio shape the seed
    # endpoint uses (default scenario injection), SAME agent — the
    # per-cycle cursor rewind makes the two runs interchangeable.
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    _, direct_events = run_cycle(agent, mandate.id)

    monkeypatch.setattr(routes, "AGENT", agent)
    with TestClient(app) as client:
        desk_id = client.post("/api/desk/seed").json()["desk_id"]
        # Let the cycle settle BEFORE subscribing — the late subscriber.
        settled = {"done": False}
        for _ in range(2000):
            settled = client.get(f"/api/desk/{desk_id}").json()
            if settled["done"]:
                break
            time.sleep(0.02)
        assert settled["done"], "recorded cycle did not settle"

        buffered: list[dict] = []
        with client.stream("GET", f"/api/desk/{desk_id}/stream") as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    buffered.append(json.loads(line[len("data: "):]))

    assert buffered, "late subscriber received no events"
    assert buffered[-1]["type"] in ("result", "error")  # the full record
    # Identical after the SAME documented volatile-field normalization.
    assert dump(buffered, desk_id) == dump(direct_events, mandate.id)
