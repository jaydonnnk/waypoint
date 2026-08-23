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


def seed_simple_desk(mark="500.00", cost="480.00", count=1, budget="12000.00"):
    """Seed a minimal desk straight into the throwaway DB. `budget` is
    threaded into BOTH Mandate.budget_total and Budget.allocated; the
    default keeps existing callers byte-identical."""
    desk_id = f"desk-{uuid4().hex[:8]}"
    mandate = Mandate(
        id=desk_id, holder="t", created_at=S3_NOW,
        budget_total=Decimal(budget),
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
        allocated=Decimal(budget), contingency=Decimal("600.00"),
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
    # The meter-exhausted leftovers name the METER as the reason.
    assert any(
        "search meter exhausted at 20" in e["disclosure"] for e in stale
    )
    assert result.status == "closed"


def test_budget_exhausted_emits_status_and_disclosed_reason(
    tmp_db, monkeypatch,
):
    """GAP 1 — remaining budget below the cheapest held mark → the cycle
    closes `budget_exhausted` (strict `<`), names the budget remainder
    and the cheapest mark in a disclosed step, keeps REAL pnl, and never
    touches the write path (this is a label after settle)."""
    monkeypatch.delenv("WAYPOINT_LIVE_BOOKING", raising=False)
    desk_id = seed_simple_desk(mark="500.00", cost="400.00", budget="400.00")
    stub = WriteStubAtlas(offer_price=None)  # marks stay stale-held
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert result.status == "budget_exhausted"  # 400 < cheapest mark 500
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert any(
        "Budget exhausted" in t and "400.00" in t and "500.00" in t
        and "never waived" in t
        for t in texts
    )
    assert result.pnl == Decimal("100.00")  # real mark-vs-cost, not zero
    assert stub.verify_calls == stub.create_calls == stub.pay_calls == 0


def test_budget_exactly_affordable_still_closes_not_budget_exhausted(
    tmp_db, monkeypatch,
):
    """GAP 1 boundary — the check is STRICT `<`: budget EQUAL to the
    cheapest held mark is exactly affordable, so the cycle closes
    `closed`, never `budget_exhausted`."""
    monkeypatch.delenv("WAYPOINT_LIVE_BOOKING", raising=False)
    desk_id = seed_simple_desk(mark="500.00", cost="400.00", budget="500.00")
    stub = WriteStubAtlas(offer_price=None)  # marks stay stale-held at 500
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert result.status == "closed"  # 500 is NOT < 500
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert not any("Budget exhausted" in t for t in texts)


def test_execute_wall_blocks_over_cap_and_emits_escalate(tmp_db, monkeypatch):
    """The spike (mark 1790 > cap 1500) never reaches create_order; the
    escalate event carries two priced options + a recommendation."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
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


def test_escalation_click_wakes_waiting_cycle_and_executes(
    tmp_db, monkeypatch,
):
    """The one human click ("A") wakes the awaiting cycle and the chosen
    option executes — re-checked through the wall (budget never waived)."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
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


def test_pay_never_retried_on_failure(tmp_db, monkeypatch):
    """A failed pay is called EXACTLY once; the position stays held."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
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


def test_no_second_order_on_price_changed(tmp_db, monkeypatch):
    """PRICE_CHANGED → absorb-or-requote reconcile; create_order exactly
    once — NEVER a second order."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
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
    # The absorbed delta is persisted, not just decremented in memory (fix 5):
    # the budget line's contingency drops 600 -> 590 across a restart.
    with database.SessionLocal() as session:
        budget = session.execute(
            select(BudgetRow).where(BudgetRow.desk_id == desk_id)
        ).scalar_one()
        assert budget.contingency == Decimal("590.00")


def test_ticket_asserted_before_success(tmp_db, monkeypatch):
    """No TICKETED envelope → the position is NEVER marked booked."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
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


def test_agent_respects_step_budget_and_gives_up(tmp_db, monkeypatch):
    """Forced budget exhaustion → graceful give-up emitting why."""
    monkeypatch.delenv("WAYPOINT_LIVE_BOOKING", raising=False)
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    stub = WriteStubAtlas(offer_price=None, ticketing=False)
    agent = DeskAgent(step_budget=1, atlas=stub, store=DeskStore(), pace=0)
    result, events = run_cycle(agent, mandate.id)

    assert result.status == "failed"
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert any("Step budget exhausted" in t for t in texts)
    # The give-up carries REAL portfolio math, not zeros.
    expected_pnl = sum(
        (p.mark_price - p.cost_basis for p in positions), Decimal("0")
    )
    assert result.pnl == expected_pnl
    # Honest mode flag — not the DeskResult model default artifact.
    assert result.comparison_mode is True


def test_comparison_mode_zero_write_calls_when_blocked(tmp_db, monkeypatch):
    """Ticketing blocked → identical cycle but NO write commands at all.
    The arm switch IS armed here, so this case exercises the ticketing
    probe gate itself (not the switch short-circuit)."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
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
    # Armed + probe reads unavailable → the wire names the PROBE gate.
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["mode"] == "comparison mode \u2014 ticketing unavailable"
    # Decisions still land on the blotter, labeled comparison mode.
    with database.SessionLocal() as session:
        ledger = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == mandate.id)
        ).scalars().all()
    assert any("comparison mode" in (row.note or "") for row in ledger)


def test_comparison_mode_when_switch_unarmed_despite_ticketing_live(
    tmp_db, monkeypatch,
):
    """Slice 4 Step 1 — the arm switch is INDEPENDENT of the flapping
    ticketing probe: WAYPOINT_LIVE_BOOKING unset + ticketing_live=True →
    still ZERO write calls (verify/create_order/pay never invoked) and the
    wire says WHICH gate blocks (live booking not armed)."""
    # Default posture enforced: strip any outer-shell arm so the switch is
    # genuinely UNARMED for this test.
    monkeypatch.delenv("WAYPOINT_LIVE_BOOKING", raising=False)
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    stub = WriteStubAtlas(offer_price="450.00", ticketing=True)
    agent = make_agent(stub)
    result, events = run_cycle(agent, mandate.id)

    assert stub.verify_calls == 0
    assert stub.create_calls == 0
    assert stub.pay_calls == 0
    assert result.comparison_mode is True
    assert result.status == "closed"
    # The meta event names the BLOCKING gate: the human switch, not the
    # ticketing probe (which reads live here).
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["mode"] == "comparison mode \u2014 live booking not armed"
    assert any(
        "live booking not armed" in d for d in meta["disclosures"]
    )
    # Decisions still land on the blotter, labeled comparison mode.
    with database.SessionLocal() as session:
        ledger = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == mandate.id)
        ).scalars().all()
    assert any("comparison mode" in (row.note or "") for row in ledger)


@pytest.mark.parametrize(
    "value", ["true", "yes", " 1", "1 ", "0", "", "01"],
)
def test_arm_switch_strict_one_non_one_values_stay_unarmed(
    tmp_db, monkeypatch, value,
):
    """Slice 4 — the arm switch is STRICT: armed ONLY when
    WAYPOINT_LIVE_BOOKING reads exactly "1". Any other value (truthy
    words, padded variants, empty) stays UNARMED even with
    ticketing_live=True → ZERO write calls and comparison mode."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", value)
    mandate, positions, budgets = fixture.seeded_portfolio()
    DeskStore().seed_desk(mandate, positions, budgets)
    stub = WriteStubAtlas(offer_price="450.00", ticketing=True)
    agent = make_agent(stub)
    result, events = run_cycle(agent, mandate.id)

    assert stub.verify_calls == 0
    assert stub.create_calls == 0
    assert stub.pay_calls == 0
    assert result.comparison_mode is True
    assert result.status == "closed"
    # The wire names the BLOCKING gate: the human switch, not the probe.
    meta = next(e for e in events if e["type"] == "meta")
    assert meta["mode"] == "comparison mode \u2014 live booking not armed"


def test_escalation_decision_endpoint_wakes_the_slot(tmp_db):
    """POST .../decision stores the choice and sets the asyncio.Event the
    loop awaits; a gone slot (never registered, or cleared after
    timeout/completion — fix 8) answers 410, never a misleading 200."""
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
            # A never-seen / cleared slot is GONE (slot hygiene).
            missing = client.post(
                "/api/desk/desk-x/escalations/nope/decision",
                json={"choice": "A"},
            )
            assert missing.status_code == 410
            # Simulate the loop clearing a consumed slot, then a late click:
            routes._clear_escalation("desk-x", "esc-1")
            assert "esc-1" not in state.escalations
            late = client.post(
                "/api/desk/desk-x/escalations/esc-1/decision",
                json={"choice": "B"},
            )
            assert late.status_code == 410
    finally:
        routes.DESKS.pop("desk-x", None)


# ==========================================================================
# Review-round hardening (3-way review fixes)
# ==========================================================================


def test_budget_invariant_blocks_verified_price_above_budget(
    tmp_db, monkeypatch,
):
    """Fix 1 — verify reports `increased` and the confirmed price exceeds
    budget_left → code-only BUDGET_EXCEEDED, create_order NEVER runs and the
    position stays held (budget is never waived, fail closed)."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    stub.verify_result = VerifyResult(
        offer_id="off-1", booking_id="bk-1", price_change="increased",
        previous_price=Decimal("500.00"), current_price=Decimal("20000.00"),
        currency="USD",
    )
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert stub.confirm_calls == 1   # confirm-price proceeded on `increased`
    assert stub.create_calls == 0    # the budget invariant is fail-closed
    assert stub.pay_calls == 0
    errors = [e for e in events if e["type"] == "error"]
    assert any(
        e["code"] == "BUDGET_EXCEEDED"
        and e["position_id"] == desk_id + "-pos-1"
        for e in errors
    )
    # Error events stay code-only — no raw message text on the wire.
    assert all("message" not in e for e in errors)
    # The position was never booked.
    with database.SessionLocal() as session:
        row = session.execute(
            select(PositionRow).where(PositionRow.id == desk_id + "-pos-1")
        ).scalar_one()
        assert row.status == "held"
        assert row.ticket_asserted is False
    assert result.status == "closed"


def test_authority_cap_invariant_blocks_verified_price_above_cap(
    tmp_db, monkeypatch,
):
    """BK1 — mark is within the cap (so the mark-time wall does NOT escalate),
    but verify reports `increased` and the confirmed price exceeds the
    authority cap → code-only AUTHORITY_CAP_EXCEEDED, create_order NEVER runs,
    the position stays held. The cap is re-checked against the REAL verified
    price, not just the stale mark (no cap breach without a human click)."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    # cap 1500, budget 12000; mark 500 is within cap, verified 1600 is over
    # cap but under budget — the budget guard alone would let it through.
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    stub.verify_result = VerifyResult(
        offer_id="off-1", booking_id="bk-1", price_change="increased",
        previous_price=Decimal("500.00"), current_price=Decimal("1600.00"),
        currency="USD",
    )
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert stub.confirm_calls == 1   # confirm-price proceeded on `increased`
    assert stub.create_calls == 0    # the cap invariant is fail-closed
    assert stub.pay_calls == 0
    errors = [e for e in events if e["type"] == "error"]
    assert any(
        e["code"] == "AUTHORITY_CAP_EXCEEDED"
        and e["position_id"] == desk_id + "-pos-1"
        for e in errors
    )
    assert all("message" not in e for e in errors)  # code-only on the wire
    with database.SessionLocal() as session:
        row = session.execute(
            select(PositionRow).where(PositionRow.id == desk_id + "-pos-1")
        ).scalar_one()
        assert row.status == "held"
        assert row.ticket_asserted is False
    assert result.status == "closed"


def test_budget_spent_persists_after_stubbed_live_booking(
    tmp_db, monkeypatch,
):
    """Fix 5 — the booked amount is persisted to budgets.spent in the settle
    transaction, so a second cycle re-reads the real spent instead of a
    guard that resets to zero."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    assert stub.create_calls == 1 and stub.pay_calls == 1
    assert result.status == "closed"
    with database.SessionLocal() as session:
        budget = session.execute(
            select(BudgetRow).where(BudgetRow.desk_id == desk_id)
        ).scalar_one()
        assert budget.spent == Decimal("500.00")  # consumption persisted
        row = session.execute(
            select(PositionRow).where(PositionRow.id == desk_id + "-pos-1")
        ).scalar_one()
        assert row.status == "booked"
        assert row.ticket_asserted is True


def test_escalation_slot_removed_on_timeout(tmp_db, monkeypatch):
    """Fix 8 — when the bounded escalation wait times out, the slot is
    removed from the registry (slot hygiene) so a late decision hits a gone
    slot, and the cycle gives up honestly."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id = seed_simple_desk(mark="1800.00", cost="1000.00", count=1)
    stub = WriteStubAtlas(offer_price=None, ticketing=True)
    registry: dict[str, dict] = {}
    cleared: list[str] = []

    def slot_factory(desk_id_arg, esc_id):
        slot = {"event": asyncio.Event(), "choice": None}
        registry[esc_id] = slot
        return slot

    def clear_hook(desk_id_arg, esc_id):
        registry.pop(esc_id, None)
        cleared.append(esc_id)

    agent = make_agent(
        stub, escalation_slot=slot_factory, escalation_clear=clear_hook,
        escalation_wait=0.05,
    )
    result, events = run_cycle(agent, desk_id)

    assert result.status == "escalated"  # nobody clicked → give-up path
    assert cleared and registry == {}    # the slot was removed on timeout
    assert stub.create_calls == 0        # nothing executed on a guess
    # The give-up reason is disclosed on the wire: the escalate event
    # carries a non-empty reason string.
    escalates = [e for e in events if e["type"] == "escalate"]
    assert escalates and escalates[0]["reason"]
    # The NEW escalated give-up step text rode the wire (S6: the
    # escalated exit discloses its own step line, not silence).
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert any("Escalation wait expired" in t for t in texts)


def test_init_db_drops_empty_legacy_tables(tmp_db):
    """Fix 9 — init_db's all-empty guard also counts + drops the legacy visa
    orphan tables, but a single legacy row vetoes the whole drop."""
    from sqlalchemy import inspect, text

    with database.engine.begin() as conn:
        conn.execute(text('CREATE TABLE "orders" (id INTEGER PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE "trips" (id INTEGER PRIMARY KEY)'))
    database.init_db()
    inspector = inspect(database.engine)
    assert not inspector.has_table("orders")   # legacy orphan dropped
    assert not inspector.has_table("trips")    # legacy orphan dropped
    assert inspector.has_table("mandate")      # desk tables recreated

    # No-data-loss guard: any legacy row anywhere vetoes the drop.
    with database.engine.begin() as conn:
        conn.execute(
            text('CREATE TABLE "decisions" (id INTEGER PRIMARY KEY)')
        )
        conn.execute(text('INSERT INTO "decisions" (id) VALUES (1)'))
    database.init_db()
    assert inspect(database.engine).has_table("decisions")  # preserved


# ==========================================================================
# Slice 4 Step 4 — the seat-select alloc beat (Branch B: cut, ledger-only).
# The seat module is NOT activated on this sandbox account (live proof
# skipped on TICKETING_ACTIVATION_REQUIRED; Seat UAT "Skipped"), so the
# loop never attempts seat_list / seat_select and the alloc degrades to a
# disclosed ledger-only entry.
# ==========================================================================


class NoSeatStubAtlas(WriteStubAtlas):
    """Write-path stub where the seat module is unavailable: any seat
    call is a tripwire — the loop must NEVER attempt one."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.seat_list_calls = 0
        self.seat_select_calls = 0

    def seat_list(self, booking_id):
        self.seat_list_calls += 1
        raise AssertionError(
            "seat_list must never be called — seat module not activated"
        )

    def seat_select(self, booking_id, seat_ref):
        self.seat_select_calls += 1
        raise AssertionError(
            "seat_select must never be called — seat module not activated"
        )


def test_alloc_degrades_to_ledger_only_when_seat_unavailable(
    tmp_db, monkeypatch,
):
    """Slice 4 Step 4 (Branch B) — seat selection is unavailable on this
    sandbox account, so after a stubbed live book the alloc beat degrades
    to the honest ledger-only path: NO seat_list / seat_select is ever
    attempted (and nothing is retried), the alloc funds nothing beyond
    realized savings (zero here), and BOTH the wire event and the ledger
    row say seat selection is unavailable — ledger-only."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = NoSeatStubAtlas(offer_price="500.00", ticketing=True)
    agent = make_agent(stub)
    result, events = run_cycle(agent, desk_id)

    # The write path ran to TICKETED exactly once (book, then the alloc).
    assert stub.create_calls == 1 and stub.pay_calls == 1
    # No real seat call attempted — the degrade is a cut, not a probe.
    assert stub.seat_list_calls == 0
    assert stub.seat_select_calls == 0
    # Exactly ONE alloc event, degraded to ledger-only: zero amount
    # (funded only from realized savings; none realized) and a plain
    # disclosure saying seat selection is unavailable on this account.
    allocs = [e for e in events if e["type"] == "alloc"]
    assert len(allocs) == 1
    alloc = allocs[0]
    assert alloc["position_id"] == desk_id + "-pos-1"
    assert alloc["amount"] == "0"
    assert alloc["seat_ref"] == "ledger_only"
    assert "seat selection unavailable" in alloc["disclosure"]
    assert "ledger-only" in alloc["disclosure"]
    # No error events — the degrade is an honest fallback, not a failure.
    assert not [e for e in events if e["type"] == "error"]
    assert result.status == "closed"
    # The degrade lands on the blotter too: one alloc row, ledger_only
    # ref, zero amount, note saying the seat module is not activated.
    with database.SessionLocal() as session:
        rows = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == desk_id)
        ).scalars().all()
    alloc_rows = [r for r in rows if r.kind == "alloc"]
    assert len(alloc_rows) == 1
    assert alloc_rows[0].ref == "ledger_only"
    assert alloc_rows[0].amount == Decimal("0")
    assert "seat selection unavailable" in (alloc_rows[0].note or "")
    assert "ledger-only" in (alloc_rows[0].note or "")


# ==========================================================================
# Slice 6 (GAP 2b) — ledger tie-out on give-up paths: NO result-vs-DB
# disagreement. Whatever the cycle already put on the settle list is
# flushed in the same one-transaction settle call before the give-up
# result, so the blotter and the wire always agree.
# ==========================================================================


def test_give_up_persists_admitted_loss_row_in_comparison_mode(
    tmp_db, monkeypatch,
):
    """GAP 2b(a) — comparison mode: an admitted loss lands a ledger row
    BEFORE the step-budget gate fires, and the give-up flush persists it.
    The wire's losses_admitted matches the persisted loss-row count.

    Inducing the loss (same pattern as the brain contract): a held
    position whose mark moved against the desk past the curated band
    floor (AAA→BBB is mid_haul, floor −3%; −4% here). step_budget=3 puts
    the gate right after the judgment step — after the loss loop, before
    the execute wall."""
    monkeypatch.delenv("WAYPOINT_LIVE_BOOKING", raising=False)
    desk_id = seed_simple_desk(mark="480.00", cost="500.00")
    stub = WriteStubAtlas(offer_price=None)  # marks stay put → loss holds
    agent = make_agent(stub, step_budget=3)
    result, events = run_cycle(agent, desk_id)

    assert result.status == "failed"
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert any("Step budget exhausted" in t for t in texts)
    # The loss was admitted on the wire...
    losses = [e for e in events if e["type"] == "loss"]
    assert len(losses) == 1
    # ...and persisted to the blotter by the give-up flush.
    with database.SessionLocal() as session:
        rows = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == desk_id)
        ).scalars().all()
    loss_rows = [r for r in rows if r.kind == "loss"]
    assert len(loss_rows) == 1
    assert loss_rows[0].amount == Decimal("-20.00")
    # No result-vs-DB disagreement on the give-up path.
    assert result.losses_admitted == len(loss_rows)


def test_give_up_after_live_booking_persists_trade_row_and_spend(
    tmp_db, monkeypatch,
):
    """GAP 2b(b) — LIVE mode: the gate right after `_write_position`
    fires after a successful booking. The give-up flush persists the
    booked trade row AND the budget consumption in one transaction, and
    the result carries the real pnl (no Atlas calls in the give-up).

    Step count: reload (1), reprice (2), judgment (3) — the write path
    itself emits no step until a booking lands; the booked-position
    disclosure step (4) is what makes the post-write gate reachable, so
    step_budget=4 lets the booking complete and trips that gate (the
    judgment gate at step 3 does not fire since 3 < 4)."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id = seed_simple_desk(mark="500.00", cost="400.00")
    stub = WriteStubAtlas(offer_price="500.00", ticketing=True)
    agent = make_agent(stub, step_budget=4)
    result, events = run_cycle(agent, desk_id)

    # The booking fully executed before the gate fired.
    assert stub.create_calls == 1 and stub.pay_calls == 1
    assert result.status == "failed"
    texts = [e.get("text", "") for e in events if e["type"] == "step"]
    assert any("Step budget exhausted" in t for t in texts)
    # Real pnl on the wire — mark vs seeded cost, not zero.
    assert result.pnl == Decimal("100.00")
    # Non-vacuous mode plumbing: the model default is True, so only a
    # live-mode give-up proves the flag is genuinely passed through.
    assert result.comparison_mode is False
    # The blotter and the budget line agree with the executed booking.
    with database.SessionLocal() as session:
        rows = session.execute(
            select(LedgerRow).where(LedgerRow.desk_id == desk_id)
        ).scalars().all()
        budget = session.execute(
            select(BudgetRow).where(BudgetRow.desk_id == desk_id)
        ).scalar_one()
        row = session.execute(
            select(PositionRow).where(PositionRow.id == desk_id + "-pos-1")
        ).scalar_one()
    trade_rows = [r for r in rows if r.kind == "trade"]
    assert len(trade_rows) == 1
    assert trade_rows[0].amount == Decimal("500.00")  # the verified price
    assert trade_rows[0].ref == "ord-1"
    assert budget.spent == Decimal("500.00")  # spend persisted on give-up
    assert row.status == "booked" and row.ticket_asserted is True
