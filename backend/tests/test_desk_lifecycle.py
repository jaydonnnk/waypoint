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


# ---------------------------------------------------------------------------
# S3: travelers_complete fires once, backend-side
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_travelers_complete_fires_once_backend(tmp_db, stub_agent, stub_auditor):
    """Nth insert fires exactly one travelers_complete; a resubmit on the
    same slot does NOT refire.

    Backend-side (03-program-design §2): the fire decision lives in
    app.travelers (store = source of truth), NOT in bot internals — this
    test imports no bot module. Dedupe is DB-backed (a ledger marker), so
    it is restart-safe.

    FAILS pre-S3: add_traveler, get_team_size, has_ledger_marker, and
    app.travelers.maybe_fire_travelers_complete do not exist.
    """
    from app.bot.mrz import MrzFields
    from app.db.store import DeskStore
    from app.events import EventSink
    from app.travelers import maybe_fire_travelers_complete

    store = DeskStore()
    sink = EventSink()

    publish_calls: list = []
    original_publish = sink.publish

    def tracking_publish(event):
        publish_calls.append(event)
        original_publish(event)

    sink.publish = tracking_publish

    # Seed a gated desk with team_size=2.
    with TestClient(app) as client:
        body = client.post(
            "/api/desk/seed", json={"gated": True, "team_size": 2}
        ).json()
        desk_id = body["desk_id"]

    fields1 = MrzFields(
        family_name="TAN", given_name="WEI", gender="M",
        birthday="1990-01-01", nationality_iso2="SG",
        doc_number="E1111111", issuing_country="SG", doc_expiry="2030-01-01",
    )
    fields2 = MrzFields(
        family_name="LIM", given_name="AH", gender="F",
        birthday="1992-06-15", nationality_iso2="SG",
        doc_number="E2222222", issuing_country="SG", doc_expiry="2031-06-15",
    )

    # First traveler (1/2) — should NOT fire.
    store.add_traveler(desk_id, slot=1, fields=fields1)
    fired = await maybe_fire_travelers_complete(store, sink, desk_id)
    assert fired is False
    tc_events = [e for e in publish_calls if e.type == "travelers_complete"]
    assert len(tc_events) == 0

    # Second traveler (2/2) — SHOULD fire exactly once.
    store.add_traveler(desk_id, slot=2, fields=fields2)
    fired = await maybe_fire_travelers_complete(store, sink, desk_id)
    assert fired is True
    tc_events = [e for e in publish_calls if e.type == "travelers_complete"]
    assert len(tc_events) == 1

    # Resubmit on slot 1 (update, not insert) — must NOT refire.
    store.add_traveler(desk_id, slot=1, fields=fields1)
    fired = await maybe_fire_travelers_complete(store, sink, desk_id)
    assert fired is False
    tc_events = [e for e in publish_calls if e.type == "travelers_complete"]
    assert len(tc_events) == 1  # still 1, not 2


# ---------------------------------------------------------------------------
# S5: pre-trip approval + pinned resume (G4)
# ---------------------------------------------------------------------------
#
# Every test below FAILS against pre-S5 code: the approval checkpoint, the
# `/approve` endpoint, `store.get_approval` / `set_approved_offer` /
# `bump_reapproval` and the pinned mark do not exist there, so a gated desk
# books straight through with no manager in the loop.


class _CountingBrain:
    """Brain stub that COUNTS judgments per position.

    The whole point of the pin is that an approved position is executed
    without being re-judged — so the proof is a call count, not a log line.
    """

    def __init__(self, kind: str = "book"):
        self.kind = kind
        self.judge_calls = 0
        self.judged_positions: list[str] = []
        self.last_source = None

    async def judge(self, held, priors, meter_left, budget_left, contingency_left):
        from app.models import DeskAction

        self.judge_calls += 1
        self.judged_positions.extend(p.id for p in held)
        return [
            DeskAction(position_id=p.id, kind=self.kind, rationale="stub")
            for p in held
        ]

    def admitted_loss(self, pos, priors):
        return None

    def resolve_price_change(self, delta, contingency_left):
        return "absorb" if delta <= contingency_left else "requote"

    def judged_count(self, position_id: str) -> int:
        return self.judged_positions.count(position_id)


def _seed_gated_desk(code: str = "S5CODE", mark="500.00", budget="12000.00"):
    """Seed a GATED desk already past /confirm (lifecycle 'released') with
    one held position and one captured traveler — the shape a Waybot desk
    has when its first cycle runs. Returns (desk_id, position_id)."""
    from datetime import datetime, timezone
    from decimal import Decimal
    from uuid import uuid4

    from app.api import routes as _routes
    from app.bot.mrz import MrzFields
    from app.db.store import DeskStore
    from app.models import Budget, Mandate, Position

    now = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)
    desk_id = f"desk-{uuid4().hex[:8]}"
    mandate = Mandate(
        id=desk_id, holder="t", created_at=now, team_size=1,
        budget_total=Decimal(budget), authority_cap=Decimal("1500.00"),
        contingency_pct=0.05, currency="USD",
    )
    pos = Position(
        id=f"{desk_id}-pos-1", trip_label="SIN->NRT", origin="AAA",
        dest="BBB", depart_date=now.date(), pax=1, status="held",
        cost_basis=Decimal("480.00"), mark_price=Decimal(mark),
        mark_at=now, mark_stale=False,
    )
    budgets = [Budget(
        desk_id=desk_id, period="2026-W38", allocated=Decimal(budget),
        contingency=Decimal("600.00"),
    )]
    store = DeskStore()
    store.seed_desk(
        mandate, [pos], budgets,
        "released",                       # already confirmed by the manager
        f"tok-{uuid4().hex[:8]}",         # GATED: the checkpoint keys on this
        _routes._hash_code(code),
    )
    store.add_traveler(desk_id, slot=1, fields=MrzFields(
        family_name="TAN", given_name="WEI", gender="M",
        birthday="1990-01-01", nationality_iso2="SG",
        doc_number="E1111111", issuing_country="SG", doc_expiry="2030-01-01",
    ))
    return desk_id, pos.id


def _write_stub(price="500.00"):
    """WriteStubAtlas wired for a GATED desk: verify carries one traveler_id
    so the pax builder can zip it with the stored roster (carry, never
    invent) instead of holding."""
    from tests.test_desk_pipe import WriteStubAtlas

    stub = WriteStubAtlas(offer_price=price, ticketing=True)
    stub.verify_result = stub.verify_result.model_copy(update={
        "travelers": [{"traveler_id": "tid-1", "passenger_type": "adult"}],
    })
    return stub


def test_approve_pins_offer(tmp_db, stub_auditor, monkeypatch):
    """The manager approves; the resumed cycle books THAT offer and the
    brain never judges the pinned position again.

    Proof of "no re-judgment" is a call-counting brain stub: its judgment
    count for the pinned position is frozen across the resume.
    """
    import asyncio
    from decimal import Decimal

    from app.approval import apply_decision
    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer, run_cycle

    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id, pos_id = _seed_gated_desk()
    store = DeskStore()
    stub = _write_stub("500.00")

    def _matching_search(origin, dest, dep, pax):
        stub.search_calls.append((origin, dest, dep, pax))
        # The fresh search must price the APPROVED offer itself — H1a
        # degrades any pinned mark whose fresh best offer diverges.
        return [make_offer("off-approved", "500.00")]

    stub.search = _matching_search
    brain = _CountingBrain(kind="book")
    agent = DeskAgent(
        step_budget=30, pace=0, atlas=stub, brain=brain, store=store,
    )

    # --- cycle 1: judgment says book -> the approval checkpoint stops it.
    result, _events = run_cycle(agent, desk_id)
    assert result.status == "escalated"
    assert stub.create_calls == 0, "nothing may book before the manager approves"
    approval = store.get_approval(desk_id)
    assert approval.lifecycle == "pending_approval"
    assert approval.approved_offer_id  # the pin
    assert approval.pinned_position_id == pos_id
    # The identity snapshot is persisted AT APPROVAL for the S6 pack.
    snapshot = store.offer_snapshot(desk_id)
    assert snapshot["price"] == "500.00"
    assert snapshot["segments"][0]["flight_number"] == "WP001"
    judged_before = brain.judged_count(pos_id)
    assert judged_before == 1  # judged exactly once, pre-approval

    # --- the manager approves.
    assert asyncio.run(apply_decision(store, desk_id, "approve")) == "approved"
    assert store.get_lifecycle(desk_id) == "released"

    # --- cycle 2 (the resume): PINNED. No re-judgment, and it books.
    result2, events2 = run_cycle(agent, desk_id)
    assert brain.judged_count(pos_id) == judged_before, (
        "the pinned position must NOT be re-judged on the resumed cycle"
    )
    assert brain.judge_calls == 1, (
        "a single-position pinned desk makes zero judgment calls on resume"
    )
    trades = [e for e in events2 if e["type"] == "trade"]
    assert trades and trades[0]["rationale"].startswith("pinned_resume")
    assert stub.create_calls == 1
    _mandate, positions, _budgets, _tail = store.reload_desk(desk_id)
    booked = next(p for p in positions if p.id == pos_id)
    assert booked.status == "booked"
    assert booked.ticket_asserted is True
    assert booked.atlas_offer_id == approval.approved_offer_id
    assert booked.atlas_order_no == "ord-1"
    assert result2.status == "closed"
    assert Decimal(snapshot["price"]) == Decimal("500.00")


def test_pinned_price_move_beyond_contingency_escalates(
    tmp_db, stub_auditor, monkeypatch,
):
    """A price move BEYOND the contingency on the PINNED path escalates —
    it never silently books what the manager did not approve.

    The numbers are chosen so the move is invisible to every pre-existing
    guard: 1200 is under the 1500 authority cap and far under the budget,
    so pre-S5 code books it without a murmur. Only the pin's own test —
    fresh mark minus the approved 500 is 700, past the 600 contingency
    remainder — catches it.
    """
    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer, run_cycle

    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id, pos_id = _seed_gated_desk(mark="500.00")
    store = DeskStore()
    # The manager approved this offer at 500.
    store.set_approved_offer(desk_id, "off-approved", snapshot={
        "position_id": pos_id, "offer_id": "off-approved", "price": "500.00",
        "currency": "USD", "segments": [],
    })

    stub = _write_stub("1200.00")  # the world moved under the approval

    def _matching_search(origin, dest, dep, pax):
        stub.search_calls.append((origin, dest, dep, pax))
        # The fresh search prices the APPROVED offer itself at the MOVED
        # price (H1a: a divergent best offer would escalate for a second,
        # additive reason and mask whether the contingency gate alone
        # fires). 1200 - approved 500 = 700 > the 600 contingency.
        return [make_offer("off-approved", "1200.00")]

    stub.search = _matching_search
    brain = _CountingBrain(kind="book")
    agent = DeskAgent(
        step_budget=30, pace=0, atlas=stub, brain=brain, store=store,
        escalation_wait=0.05,
    )
    result, events = run_cycle(agent, desk_id)

    trades = [
        e for e in events
        if e["type"] == "trade" and e["position_id"] == pos_id
    ]
    assert trades, "the pinned position must still produce a mark"
    assert trades[0]["kind"] == "escalate"
    assert "pinned_resume" in trades[0]["rationale"]
    assert "contingency" in trades[0]["rationale"]
    # It escalated, and NOTHING was written on a guess.
    assert [e for e in events if e["type"] == "escalate"]
    assert stub.create_calls == 0
    assert stub.pay_calls == 0
    _mandate, positions, _budgets, _tail = store.reload_desk(desk_id)
    assert next(p for p in positions if p.id == pos_id).status == "held"
    assert result.status == "escalated"


def test_unbookable_pin_one_reapproval_then_hold(
    tmp_db, stub_auditor, monkeypatch,
):
    """An UNBOOKABLE pin (OFFER_EXPIRED) buys exactly ONE re-judgment and
    one fresh approval round; the second time the desk holds and discloses.
    """
    import asyncio

    from app.approval import apply_decision
    from app.atlas.client import AtlasError
    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer, run_cycle

    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id, pos_id = _seed_gated_desk(mark="500.00")
    store = DeskStore()
    store.set_approved_offer(desk_id, "off-approved", snapshot={
        "position_id": pos_id, "offer_id": "off-approved", "price": "500.00",
        "currency": "USD", "segments": [],
    })

    stub = _write_stub("500.00")

    def _matching_search(origin, dest, dep, pax):
        stub.search_calls.append((origin, dest, dep, pax))
        # The fresh search prices the APPROVED offer itself (H1a: a
        # divergent best offer would degrade the pinned mark before the
        # write path even sees the expiry this test exercises).
        return [make_offer("off-approved", "500.00")]

    stub.search = _matching_search

    def _expired(offer_id):
        raise AtlasError("OFFER_EXPIRED")

    stub.verify = _expired
    brain = _CountingBrain(kind="book")
    agent = DeskAgent(
        step_budget=30, pace=0, atlas=stub, brain=brain, store=store,
    )

    # --- round 1: expired -> ONE re-judgment -> a NEW approval request.
    result, events = run_cycle(agent, desk_id)
    codes = [e.get("code") for e in events if e["type"] == "error"]
    assert "OFFER_EXPIRED" in codes
    assert "PIN_UNBOOKABLE_HELD" not in codes
    approval = store.get_approval(desk_id)
    assert approval.reapproval_count == 1
    assert approval.lifecycle == "pending_approval", (
        "the replacement offer must go back to the manager, not book itself"
    )
    assert stub.create_calls == 0
    assert result.status == "escalated"

    # The manager approves the replacement.
    assert asyncio.run(apply_decision(store, desk_id, "approve")) == "approved"

    # --- round 2: expired AGAIN -> the one re-approval is spent -> HOLD.
    result2, events2 = run_cycle(agent, desk_id)
    held_errors = [
        e for e in events2
        if e["type"] == "error" and e.get("code") == "PIN_UNBOOKABLE_HELD"
    ]
    assert held_errors, "the second expiry must hold and disclose"
    assert "re-approval is spent" in held_errors[0]["disclosure"]
    assert store.get_approval(desk_id).reapproval_count == 1  # capped at 1
    assert store.get_lifecycle(desk_id) == "released"  # no third ask
    assert stub.create_calls == 0
    _mandate, positions, _budgets, _tail = store.reload_desk(desk_id)
    assert next(p for p in positions if p.id == pos_id).status == "held"
    assert result2.status == "escalated"


def test_pinned_divergent_fresh_offer_escalates(
    tmp_db, stub_auditor, monkeypatch,
):
    """H1 — the contingency gate must never book against the WRONG
    offer's price. The resume search returns TWO offers: a cheaper
    DIFFERENT one (Y at 510) and the approved one (X). The fan-out marks
    the position at Y's 510, so the mark-time contingency test measures
    510 vs the approved 500 (+10) — invisible — while verify(X) reports
    the approved offer rose to 1100, still WITHIN the test's 1500 cap
    and 12000 budget. Pre-fix code would have booked at 1100 silently.
    Only the divergence check (fresh offer id != approved id) catches it:
    the mark degrades to escalate and nothing is written.
    """
    from decimal import Decimal

    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer, run_cycle

    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id, pos_id = _seed_gated_desk(mark="500.00")
    store = DeskStore()
    # The manager approved offer X at 500.
    store.set_approved_offer(desk_id, "off-approved", snapshot={
        "position_id": pos_id, "offer_id": "off-approved", "price": "500.00",
        "currency": "USD", "segments": [],
    })

    stub = _write_stub("500.00")
    # verify(X) reports the approved offer rose to 1100 — within the
    # test's cap/budget, so no pre-existing guard would stop a write
    # that reached it.
    stub.verify_result = stub.verify_result.model_copy(update={
        "price_change": "increased",
        "previous_price": Decimal("500.00"),
        "current_price": Decimal("1100.00"),
    })

    def _two_offers(origin, dest, dep, pax):
        stub.search_calls.append((origin, dest, dep, pax))
        # Cheapest-first (the real client sorts): Y (510, different
        # offer id) beats the approved X — the fan-out marks 510.
        return [
            make_offer("off-cheaper", "510.00"),
            make_offer("off-approved", "1100.00"),
        ]

    stub.search = _two_offers
    brain = _CountingBrain(kind="book")
    agent = DeskAgent(
        step_budget=30, pace=0, atlas=stub, brain=brain, store=store,
        escalation_wait=0.05,
    )
    result, events = run_cycle(agent, desk_id)

    trades = [
        e for e in events
        if e["type"] == "trade" and e["position_id"] == pos_id
    ]
    assert trades, "the pinned position must still produce a mark"
    assert trades[0]["kind"] == "escalate"
    assert "pinned_resume" in trades[0]["rationale"]
    assert "no longer matches" in trades[0]["rationale"]
    # It escalated, and NOTHING was written on a guess.
    assert [e for e in events if e["type"] == "escalate"]
    assert stub.create_calls == 0
    assert stub.pay_calls == 0
    _mandate, positions, _budgets, _tail = store.reload_desk(desk_id)
    assert next(p for p in positions if p.id == pos_id).status == "held"
    assert result.status == "escalated"


@pytest.mark.parametrize("code", ["OFFER_EXPIRED", "BOOKING_EXPIRED"])
def test_unbookable_codes_classify_as_unbookable(
    tmp_db, stub_auditor, monkeypatch, code,
):
    """L11 — BOOKING_EXPIRED classifies exactly like OFFER_EXPIRED (the
    Atlas error-handling reference treats them identically): an
    UNBOOKABLE pin buys the one re-judgment + one fresh approval round,
    never a silent close on the error."""
    from app.atlas.client import AtlasError
    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer, run_cycle

    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id, pos_id = _seed_gated_desk(mark="500.00")
    store = DeskStore()
    store.set_approved_offer(desk_id, "off-approved", snapshot={
        "position_id": pos_id, "offer_id": "off-approved", "price": "500.00",
        "currency": "USD", "segments": [],
    })
    stub = _write_stub("500.00")

    def _matching_search(origin, dest, dep, pax):
        stub.search_calls.append((origin, dest, dep, pax))
        # The fresh search prices the APPROVED offer itself (H1a: a
        # divergent best offer would escalate at mark time and never
        # reach the write-path expiry this test classifies).
        return [make_offer("off-approved", "500.00")]

    stub.search = _matching_search

    def _expired(offer_id):
        raise AtlasError(code)

    stub.verify = _expired
    brain = _CountingBrain(kind="book")
    agent = DeskAgent(
        step_budget=30, pace=0, atlas=stub, brain=brain, store=store,
    )
    result, events = run_cycle(agent, desk_id)

    codes = [e.get("code") for e in events if e["type"] == "error"]
    assert code in codes
    approval = store.get_approval(desk_id)
    assert approval.lifecycle == "pending_approval", (
        f"{code} must buy the one re-judgment + fresh approval round"
    )
    assert approval.reapproval_count == 1
    assert stub.create_calls == 0
    assert result.status == "escalated"


def test_pinned_verified_price_beyond_contingency_fails_write(
    tmp_db, stub_auditor, monkeypatch,
):
    """H1b — the WRITE-TIME contingency guard bites on its own.

    The mark-time gates are deliberately kept silent: the fresh search
    returns the APPROVED offer itself priced only 5 above the approved
    500 (within the 600 contingency remainder, and the offer ids match),
    so `_pinned_mark` stays `book`. Only verify reports the rise — 1150,
    still under the 1500 authority cap and the 12000 budget, but 650 >
    the 600 contingency — so the execute wall's re-check of the VERIFIED
    price against approved + contingency is the ONE thing standing
    between this write and a silent booking. Pre-fix code (guard
    mutated away) books it.
    """
    from decimal import Decimal

    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer, run_cycle

    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    desk_id, pos_id = _seed_gated_desk(mark="500.00")
    store = DeskStore()
    # The manager approved offer X at 500.
    store.set_approved_offer(desk_id, "off-approved", snapshot={
        "position_id": pos_id, "offer_id": "off-approved", "price": "500.00",
        "currency": "USD", "segments": [],
    })

    stub = _write_stub("500.00")
    # verify(X) reports the approved offer rose to 1150 — within cap and
    # budget, but 1150 - 500 = 650 > the 600 contingency remainder.
    stub.verify_result = stub.verify_result.model_copy(update={
        "price_change": "increased",
        "previous_price": Decimal("500.00"),
        "current_price": Decimal("1150.00"),
    })

    def _matching_search(origin, dest, dep, pax):
        stub.search_calls.append((origin, dest, dep, pax))
        # The fresh search prices the APPROVED offer itself at 505 —
        # within contingency (505 - 500 = 5 <= 600) and the SAME offer
        # id, so the mark stays `book`: no divergence, no mark-time
        # contingency escalate. The write wall is the only gate left.
        return [make_offer("off-approved", "505.00")]

    stub.search = _matching_search
    brain = _CountingBrain(kind="book")
    agent = DeskAgent(
        step_budget=30, pace=0, atlas=stub, brain=brain, store=store,
    )
    _result, events = run_cycle(agent, desk_id)

    errors = [
        e for e in events
        if e["type"] == "error" and e.get("position_id") == pos_id
    ]
    assert any(e.get("code") == "CONTINGENCY_EXCEEDED" for e in errors), (
        "the write-time guard must stop a verified price beyond "
        "approved + contingency"
    )
    # Nothing was written on a guess.
    assert stub.create_calls == 0
    assert stub.pay_calls == 0
    _mandate, positions, _budgets, _tail = store.reload_desk(desk_id)
    assert next(p for p in positions if p.id == pos_id).status == "held"


def test_fresh_first_time_approval_round_resets_reapproval_count(tmp_db):
    """L12 — reapproval_count is per pin LINEAGE, not desk lifetime: a
    fresh FIRST-TIME approval round (the checkpoint path, which passes
    reset_reapproval=True) zeroes a spent count.

    Shipped version: the DIRECT one — a desk whose count is bumped to the
    spent-lineage state (exactly what round 1 of
    test_unbookable_pin_one_reapproval_then_hold produces via
    bump_reapproval) opens a fresh first-time round through
    request_approval(reset_reapproval=True), the same seam the agent
    loop's checkpoint uses, and the count reads back 0.
    """
    import asyncio
    from decimal import Decimal

    from app.approval import request_approval
    from app.db.store import DeskStore
    from tests.test_desk_pipe import make_offer

    desk_id, pos_id = _seed_gated_desk(mark="500.00")
    store = DeskStore()

    # A spent lineage: the one re-approval allowance is consumed.
    assert store.bump_reapproval(desk_id) == 1
    assert store.get_approval(desk_id).reapproval_count == 1

    # A fresh FIRST-TIME round on the same desk (checkpoint seam).
    _mandate, positions, _budgets, _tail = store.reload_desk(desk_id)
    pos = next(p for p in positions if p.id == pos_id)
    pos.atlas_offer_id = "off-approved"
    offer = make_offer("off-approved", "500.00")
    opened = asyncio.run(request_approval(
        store, None, desk_id, pos, offer, Decimal("500.00"),
        reason="fresh first-time round resets the lineage",
        reset_reapproval=True,
    ))
    assert opened is True
    approval = store.get_approval(desk_id)
    assert approval.lifecycle == "pending_approval"
    assert approval.reapproval_count == 0
