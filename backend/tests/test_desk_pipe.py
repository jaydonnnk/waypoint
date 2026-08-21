"""S1 desk pipe tests — first real DB writes + the SSE meta contract.

Isolation: the seed test NEVER touches the shared waypoint.db — it swaps
the engine/SessionLocal for a throwaway SQLite file (tmp_path) and reads
the seeded rows back via a DIRECT session. The stream test injects a stub
Atlas client through the route-level AGENT DI (no live calls; S1 doesn't
search anyway). Live-sandbox coverage stays in test_atlas_sandbox_live.py
(opt-in, `-m live`).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agent.loop import DeskAgent
from app.api import routes
from app.db import database
from app.db.schema import BudgetRow, LedgerRow, MandateRow, PositionRow
from app.main import app


class StubAtlas:
    """Deterministic stand-in for AtlasClient (S1 never searches)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def search(self, origin, dest, dep, pax):
        self.calls.append((origin, dest, dep, pax))
        return []


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """Point the app at a throwaway SQLite file — never the shared DB."""
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
    return engine


@pytest.fixture()
def stub_agent(monkeypatch):
    """Route the HTTP endpoints through a stubbed agent (no live calls)."""
    stub = StubAtlas()
    monkeypatch.setattr(routes, "AGENT", DeskAgent(step_budget=12, atlas=stub))
    return stub


def test_seed_persists_mandate_positions_budgets(tmp_db, stub_agent):
    """POST /api/desk/seed lands the first real DB writes — and the seed is
    re-readable via a DIRECT session on the throwaway file."""
    with TestClient(app) as client:
        resp = client.post("/api/desk/seed")
        assert resp.status_code == 200
        desk_id = resp.json()["desk_id"]
        assert desk_id.startswith("desk-")
        # Join the background cycle before the client context closes.
        final = client.get(f"/api/desk/{desk_id}/close")
        assert final.status_code == 200
        assert final.json()["status"] == "closed"

    with database.SessionLocal() as session:
        # Mandate persisted with its full field set (incl. holder/timestamp).
        mandate = session.get(MandateRow, desk_id)
        assert mandate is not None
        assert mandate.holder == "Waypoint Demo Desk"
        assert mandate.created_at is not None
        assert mandate.budget_total == Decimal("12000.00")
        assert mandate.authority_cap == Decimal("1500.00")
        assert mandate.contingency_pct == Decimal("0.05")
        assert mandate.currency == "USD"

        # 5-6 held positions, all linked to the desk (= mandate id).
        positions = (
            session.execute(
                select(PositionRow).where(PositionRow.desk_id == desk_id)
            )
            .scalars()
            .all()
        )
        assert 5 <= len(positions) <= 6
        assert all(p.status == "held" for p in positions)
        assert all(p.ticket_asserted is False for p in positions)
        # The injected escalation-spike position: cheap basis, mark above cap.
        spike = next(p for p in positions if p.origin == "DAC")
        assert spike.mark_price > mandate.authority_cap
        assert spike.cost_basis < spike.mark_price / 2

        # Budget lines persisted for the desk.
        budgets = (
            session.execute(
                select(BudgetRow).where(BudgetRow.desk_id == desk_id)
            )
            .scalars()
            .all()
        )
        assert budgets and all(b.desk_id == desk_id for b in budgets)
        assert all(b.spent == Decimal("0") for b in budgets)

        # The blotter carries the seed disclosure (honesty on the record).
        ledger = (
            session.execute(
                select(LedgerRow).where(LedgerRow.desk_id == desk_id)
            )
            .scalars()
            .all()
        )
        assert any("seeded" in (row.note or "") for row in ledger)


def test_seed_emits_meta_with_mandate_and_meter(tmp_db, stub_agent):
    """The stream opens with the mandate card + a full 20/20 search meter."""
    with TestClient(app) as client:
        desk_id = client.post("/api/desk/seed").json()["desk_id"]
        seen: list[dict] = []
        with client.stream("GET", f"/api/desk/{desk_id}/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    seen.append(json.loads(line[len("data: "):]))
                    if seen[-1]["type"] == "result":
                        break

    assert seen, "stream emitted no events"
    meta = seen[0]
    assert meta["type"] == "meta"
    assert meta["meter"] == {"used": 0, "max": 20}
    # The mandate payload rides on the wire (id = desk_id, full field set).
    mandate = meta["mandate"]
    assert mandate["id"] == desk_id
    assert mandate["holder"] == "Waypoint Demo Desk"
    assert mandate["budget_total"] == "12000.00"
    assert mandate["authority_cap"] == "1500.00"
    assert mandate["currency"] == "USD"
    assert mandate["created_at"]
    # Honesty labels while ticketing is blocked.
    assert "comparison mode" in meta["mode"]
    # Cycle terminates honestly: closed, zero P&L, comparison mode.
    result = seen[-1]["result"]
    assert result["status"] == "closed"
    assert result["comparison_mode"] is True
