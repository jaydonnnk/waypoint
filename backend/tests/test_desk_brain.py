"""S3 desk-brain tests — judgment with a stubbed transport.

NO network, NO real Qwen: every test injects a transport (or forces the
deterministic fallback), so the suite is hermetic. The fallback path is
exercised via a raising transport (never by reading env keys).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

from app.agent.brain import DeskBrain
from app.fixture import VOLATILITY_PRIORS
from app.models import DeskAction, Position

NOW = datetime.now(timezone.utc)


async def _raising_transport(messages):
    raise RuntimeError("LLM unavailable (stub)")


def make_position(
    pos_id: str = "desk-t-pos-1",
    origin: str = "AAA",
    dest: str = "BBB",
    cost: Decimal = Decimal("100.00"),
    mark: Decimal = Decimal("120.00"),
    status: str = "held",
) -> Position:
    """Unknown route pair → mid_haul band (0.03–0.10)."""
    return Position(
        id=pos_id,
        trip_label="test leg",
        origin=origin,
        dest=dest,
        depart_date=NOW.date(),
        pax=1,
        status=status,
        cost_basis=cost,
        mark_price=mark,
        mark_at=NOW,
        mark_stale=False,
    )


def judge_sync(brain: DeskBrain, positions: list[Position]) -> list[DeskAction]:
    return asyncio.run(brain.judge(
        positions, VOLATILITY_PRIORS,
        meter_left=20, budget_left=Decimal("12000"),
        contingency_left=Decimal("600"),
    ))


# --- deterministic fallback path (prior-band rule) -------------------------


def test_brain_books_when_mark_above_prior_band():
    """Mark ran past the curated band top (+20% ≥ 10%) → book."""
    brain = DeskBrain(transport=_raising_transport)
    actions = judge_sync(brain, [make_position(mark=Decimal("120.00"))])
    assert len(actions) == 1
    assert actions[0].kind == "book"
    assert actions[0].position_id == "desk-t-pos-1"


def test_brain_holds_when_mark_below_band():
    """Mark below the band (−1% < 3% floor) → hold."""
    brain = DeskBrain(transport=_raising_transport)
    actions = judge_sync(brain, [make_position(mark=Decimal("99.00"))])
    assert len(actions) == 1
    assert actions[0].kind == "hold"


def test_brain_logs_admitted_loss_with_threshold_note():
    """Held too long past the disclosed band floor → admitted-loss note."""
    brain = DeskBrain()
    # −$62 on a 700 basis = −8.86%, past the mid_haul floor (−3%).
    pos = make_position(cost=Decimal("700.00"), mark=Decimal("638.00"))
    found = brain.admitted_loss(pos, VOLATILITY_PRIORS)
    assert found is not None
    amount, note = found
    assert amount == Decimal("-62.00")
    assert "held too long" in note
    assert "threshold adjusted" in note
    assert "−3%" in note  # the disclosed curated threshold
    # A booked position never admits a loss.
    booked = make_position(status="booked")
    assert brain.admitted_loss(booked, VOLATILITY_PRIORS) is None


def test_resolve_price_change_absorbs_small_requotes_large():
    """Boundary at the contingency remainder (inclusive absorb)."""
    brain = DeskBrain()
    assert brain.resolve_price_change(
        Decimal("50"), Decimal("60")) == "absorb"
    # Exact boundary: delta == contingency remainder → still absorbed.
    assert brain.resolve_price_change(
        Decimal("60"), Decimal("60")) == "absorb"
    assert brain.resolve_price_change(
        Decimal("60.01"), Decimal("60")) == "requote"


# --- the LLM seam (stubbed transport) --------------------------------------


def test_stubbed_llm_response_parses_into_desk_actions():
    """A well-formed Qwen JSON reply becomes DeskActions verbatim."""
    positions = [make_position("p-1"), make_position("p-2")]
    payload = json.dumps([
        {"position_id": "p-1", "kind": "book",
         "rationale": "mark past band; meter and budget both ample"},
        {"position_id": "p-2", "kind": "hold",
         "rationale": "inside band; conserve contingency"},
    ])

    async def stub_transport(messages):
        assert "p-1" in messages[0]["content"]  # all positions batched
        assert "meter" in messages[0]["content"].lower()
        return payload

    brain = DeskBrain(transport=stub_transport)
    actions = judge_sync(brain, positions)
    assert [a.position_id for a in actions] == ["p-1", "p-2"]
    assert [a.kind for a in actions] == ["book", "hold"]
    assert "deterministic fallback" not in actions[0].rationale


def test_markdown_fenced_llm_response_still_parses(monkeypatch):
    """MJ3 — Qwen commonly wraps the array in ```json fences (or a line of
    prose). The outermost [...] is extracted so a VALID reply is used, not
    silently dropped to the deterministic fallback (which would forfeit x2)."""
    positions = [make_position("p-1")]
    fenced = (
        "Here is my recommendation:\n```json\n"
        '[{"position_id": "p-1", "kind": "book", '
        '"rationale": "mark past band, budget ample"}]'
        "\n```\n"
    )

    async def fenced_transport(messages):
        return fenced

    brain = DeskBrain(transport=fenced_transport)
    actions = judge_sync(brain, positions)
    assert [a.position_id for a in actions] == ["p-1"]
    assert actions[0].kind == "book"
    assert "deterministic fallback" not in actions[0].rationale  # LLM used


def test_llm_failure_degrades_to_identical_shape_fallback():
    """Transport failure → the prior-band fallback with the SAME shape."""
    positions = [make_position("p-1"), make_position("p-2", mark=Decimal("99.00"))]
    brain = DeskBrain(transport=_raising_transport)
    actions = judge_sync(brain, positions)
    direct = brain.fallback_actions(positions, VOLATILITY_PRIORS)
    assert actions == direct  # identical DeskAction shapes
    assert {a.position_id for a in actions} == {"p-1", "p-2"}
    assert all(a.kind in ("book", "hold", "escalate") for a in actions)
    assert all("deterministic fallback" in a.rationale for a in actions)


def test_malformed_llm_json_degrades_to_fallback():
    """Broken JSON / missing positions never leak through."""
    positions = [make_position("p-1")]

    async def bad_transport(messages):
        return '[{"position_id": "WRONG", "kind": "book", "rationale": "x"}]'

    brain = DeskBrain(transport=bad_transport)
    actions = judge_sync(brain, positions)
    assert actions == brain.fallback_actions(positions, VOLATILITY_PRIORS)
