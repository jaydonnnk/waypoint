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
    # Create the desk tables on the throwaway engine. The two S1 tests get
    # them via the TestClient lifespan (init_db); the direct-seed S3 tests
    # bypass that path, so build the schema here for every case.
    database.Base.metadata.create_all(bind=engine)
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


# ==========================================================================
# S3 — desk brain + execute wall. Stub AtlasClient + stub brain transport;
# NO network, NO real Qwen. The write-path cases run the stub in LIVE mode
# (ticketing_live -> True) so the execute path runs against the stub.
# ==========================================================================

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app import fixture
from app.atlas.client import AtlasError, AtlasQueryOnly
from app.db.store import DeskStore
from app.models import (
    Budget,
    Mandate,
    Offer,
    OrderRef,
    OrderStatus,
    PaymentResult,
    Position,
    Segment,
    VerifyResult,
)

S3_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def make_offer(atlas_id: str, price) -> Offer:
    return Offer(
        id=f"opt-{atlas_id}",
        atlas_offer_id=atlas_id,
        price=Decimal(str(price)),
        total_minutes=120,
        segments=[Segment(
            dep_airport="AAA", arr_airport="BBB",
            dep_time=S3_NOW, arr_time=S3_NOW, flight_number="WP001",
        )],
    )


class WriteStubAtlas:
    """Deterministic write-path stand-in (live mode by default)."""

    def __init__(self, offer_price=None, ticketing=True):
        self.search_calls: list[tuple] = []
        self.verify_calls = 0
        self.confirm_calls = 0
        self.create_calls = 0
        self.pay_calls = 0
        self.status_calls = 0
        self.ticketing = ticketing
        self.offer_price = offer_price  # None -> search returns []
        self.verify_result = VerifyResult(
            offer_id="off-1", booking_id="bk-1",
            price_change="unchanged",
            previous_price=Decimal("500.00"),
            current_price=Decimal("500.00"), currency="USD",
        )
        self.create_error: Exception | None = None
        self.pay_error: Exception | None = None
        self.ticketed = True
        self.status_code = "TICKETED"

    def ticketing_live(self):
        return self.ticketing

    def search(self, origin, dest, dep, pax):
        self.search_calls.append((origin, dest, dep, pax))
        if self.offer_price is None:
            return []
        return [make_offer(f"off-{len(self.search_calls)}", self.offer_price)]

    def verify(self, offer_id):
        self.verify_calls += 1
        return self.verify_result

    def confirm_price(self, booking_id):
        self.confirm_calls += 1

    def create_order(self, booking_id, pax_json, seat_policy=None):
        self.create_calls += 1
        if self.create_error is not None:
            raise self.create_error
        return OrderRef(payment_confirmation_id="pc-1", order_no="ord-1")

    def pay(self, payment_confirmation_id):
        self.pay_calls += 1  # counted exactly — never retried
        if self.pay_error is not None:
            raise self.pay_error
        return PaymentResult(code="TICKETED", order_no="ord-1", ticketed=True)

    def order_status(self, order_no):
        self.status_calls += 1
        return OrderStatus(
            code=self.status_code, order_no=order_no,
            ticketed=self.ticketed,
        )

    def poll_until_ticketed(self, order_no, deadline=90.0, base_delay=2.0):
        status = self.order_status(order_no)
        return status, status.ticketed

    def follow_up_query_only(self, signal):
        return self.order_status(signal.order_no or "ord-1")


def seed_simple_desk(mark="500.00", cost="480.00", count=1):
    """Seed a minimal desk straight into the throwaway DB."""
    desk_id = f"desk-{uuid4().hex[:8]}"
    mandate = Mandate(
        id=desk_id, holder="t", created_at=S3_NOW,
        budget_total=Decimal("12000.00"),
        authority_cap=Decimal("1500.00"),
        contingency_pct=0.05, currency="USD",
    )
    positions = [
        Position(
            id=f"{desk_id}-pos-{n}", trip_label=f"leg {n}",
            origin="AAA", dest="BBB", depart_date=S3_NOW.date(), pax=1,
            status="held", cost_basis=Decimal(cost),
            mark_price=Decimal(mark), mark_at=S3_NOW, mark_stale=False,
        )
        for n in range(1, count + 1)
    ]
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


def make_agent(stub, **kwargs):
    kwargs.setdefault("step_budget", 30)
    kwargs.setdefault("pace", 0)
    kwargs.setdefault("escalation_wait", 0.2)
    return DeskAgent(atlas=stub, store=DeskStore(), **kwargs)


def test_reprice_fan_out_is_meter_gated(tmp_db):
    """22 positions → exactly 20 searches; the 21st is never invoked and
    the leftovers keep stale marks with disclosed uncertainty."""
    desk_id = seed_simple_desk(mark="480.00", cost="480.00", count=22)
    stub = WriteStubAtlas(offer_price="481.00", ticketing=False)
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert len(stub.search_calls) == 20  # hard stop — 21st never invoked
    marks = [e for e in events if e["type"] == "mark"]
    assert len(marks) == 22
    stale = [e for e in marks if e.get("stale")]
    assert len(stale) == 2
    for event in stale:
        assert "uncertainty disclosed" in event["disclosure"]
    assert result.status == "closed"


def test_execute_wall_blocks_over_cap_and_emits_escalate(tmp_db):
    """The spike (mark 1790 > cap 1500) never reaches create_order; the
    escalate event carries two priced options + a recommendation."""
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    stub = WriteStubAtlas(offer_price=None, ticketing=True)
    done_event = asyncio.Event()
    done_event.set()  # pre-set: the click arrives instantly ("B" = hold)

    def slot_choice_b(desk_id, esc_id):
        return {"event": done_event, "choice": "B"}

    agent = make_agent(stub, escalation_slot=slot_choice_b)
    result, events = run_cycle(agent, mandate.id)

    escalates = [e for e in events if e["type"] == "escalate"]
    assert len(escalates) == 1
    esc = escalates[0]
    assert esc["esc_id"]
    assert len(esc["options"]) == 2
    assert all("price" in option for option in esc["options"])
    assert esc["recommendation"] in ("A", "B")
    # The over-cap pick NEVER reaches order create (wall is fail-closed).
    assert stub.create_calls == 0
    assert stub.verify_calls == 0
    assert result.status == "closed"


def test_escalation_click_wakes_waiting_cycle_and_executes(tmp_db):
    """The one human click ("A") wakes the awaiting cycle and the chosen
    option executes — re-checked through the wall (budget never waived)."""
    desk_id = seed_simple_desk(mark="1800.00", cost="1000.00", count=1)
    stub = WriteStubAtlas(offer_price="1800.00", ticketing=True)
    slots: dict[str, dict] = {}

    def slot_factory(desk_id_arg, esc_id):
        slot = {"event": asyncio.Event(), "choice": None}
        slots[esc_id] = slot
        return slot

    agent = make_agent(stub, escalation_slot=slot_factory, escalation_wait=10)

    async def scenario():
        events: list[dict] = []
        task = asyncio.create_task(agent.run(desk_id, lambda e: _collect(events, e)))
        for _ in range(500):  # wait for the escalation to register
            if slots:
                break
            await asyncio.sleep(0.01)
        assert slots, "execute wall never registered the escalation"
        slot = next(iter(slots.values()))
        slot["choice"] = "A"  # the human click
        slot["event"].set()
        return await task, events

    async def _collect(events, event):
        events.append(event)

    result, events = asyncio.run(scenario())
    assert stub.create_calls == 1  # the chosen option executed
    assert stub.pay_calls == 1
    assert result.status == "closed"
    assert result.comparison_mode is False


def test_pay_never_retried_on_failure(tmp_db):
    """A failed pay is called EXACTLY once; the position stays held."""
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    stub.pay_error = AtlasError("PAYMENT_FAILED")
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert stub.pay_calls == 1  # single-use; NEVER retried
    errors = [e for e in events if e["type"] == "error"]
    assert any(e["code"] == "PAYMENT_FAILED" for e in errors)
    assert result.status == "closed"
    # Position never booked without a ticket.
    with database.SessionLocal() as session:
        row = session.execute(
            select(PositionRow).where(PositionRow.id == desk_id + "-pos-1")
        ).scalar_one()
        assert row.status == "held"
        assert row.ticket_asserted is False


def test_no_second_order_on_price_changed(tmp_db):
    """PRICE_CHANGED → absorb-or-requote reconcile; create_order exactly
    once — NEVER a second order."""
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    stub.verify_result = VerifyResult(
        offer_id="off-1", booking_id="bk-1", price_change="unchanged",
        previous_price=Decimal("500.00"), current_price=Decimal("510.00"),
        currency="USD",
    )
    stub.create_error = AtlasQueryOnly("PRICE_CHANGED", None)
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert stub.create_calls == 1  # never a second order
    assert stub.pay_calls == 0
    reconciles = [e for e in events if e["type"] == "reconcile"]
    assert len(reconciles) == 1
    assert reconciles[0]["resolution"] == "absorb"  # 10 ≤ 600 contingency
    assert result.status == "closed"


def test_ticket_asserted_before_success(tmp_db):
    """No TICKETED envelope → the position is NEVER marked booked."""
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    stub.ticketed = False
    stub.status_code = "TICKETING_PENDING"
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert stub.pay_calls == 1
    with database.SessionLocal() as session:
        row = session.execute(
            select(PositionRow).where(PositionRow.id == desk_id + "-pos-1")
        ).scalar_one()
        assert row.status == "held"          # assert TICKETED, not 200 OK
        assert row.ticket_asserted is False
    errors = [e for e in events if e["type"] == "error"]
    assert any(e["code"] == "TICKETING_PENDING" for e in errors)
    assert result.status == "closed"


def test_agent_respects_step_budget_and_gives_up(tmp_db):
    """Forced budget exhaustion → graceful give-up emitting why."""
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    stub = WriteStubAtlas(offer_price=None, ticketing=False)
    agent = DeskAgent(step_budget=1, atlas=stub, store=DeskStore(), pace=0)
    result, events = run_cycle(agent, mandate.id)

    assert result.status == "failed"
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert any("Step budget exhausted" in t for t in texts)


def test_comparison_mode_zero_write_calls_when_blocked(tmp_db):
    """Ticketing blocked → identical cycle but NO write commands at all."""
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    stub = WriteStubAtlas(offer_price="450.00", ticketing=False)
    agent = make_agent(stub)
    result, events = run_cycle(agent, mandate.id)

    assert stub.verify_calls == 0
    assert stub.create_calls == 0
    assert stub.pay_calls == 0
    assert result.comparison_mode is True
    assert result.status == "closed"
    # Decisions still land on the blotter, labeled comparison mode.
    with database.SessionLocal() as session:
        ledger = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == mandate.id)
        ).scalars().all()
    assert any("comparison mode" in (row.note or "") for row in ledger)


def test_escalation_decision_endpoint_wakes_the_slot(tmp_db):
    """POST .../decision stores the choice and sets the asyncio.Event the
    loop awaits; unknown escalations 404."""
    state = routes.DeskState(desk_id="desk-x")
    slot = {"event": asyncio.Event(), "choice": None}
    state.escalations["esc-1"] = slot
    routes.DESKS["desk-x"] = state
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/desk/desk-x/escalations/esc-1/decision",
                json={"choice": "A"},
            )
            assert resp.status_code == 200
            assert slot["choice"] == "A"
            assert slot["event"].is_set()
            missing = client.post(
                "/api/desk/desk-x/escalations/nope/decision",
                json={"choice": "A"},
            )
            assert missing.status_code == 404
    finally:
        routes.DESKS.pop("desk-x", None)
