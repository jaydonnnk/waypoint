"""S7 risk-auditor tests — hermetic like test_desk_brain.py.

NO network, NO real Qwen: every test injects a transport (or forces the
deterministic fallback), so the suite never touches the network. The
fallback path is exercised via raising/slow/garbage transports. The
fallback itself is asserted to be PURE code — it never invents a number;
every figure it prints is read off the blotter objects it is handed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.agent.auditor import (
    AUDITOR_LABEL,
    LINE_MAX_CHARS,
    SOURCE_AGENT,
    SOURCE_FALLBACK,
    RiskAuditor,
    fallback_challenge,
)
from app.models import Mandate, Position

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    """Make the up-front key check pass so the transport-injected tests
    exercise the AGENT path regardless of the outer shell's env. The value
    is a test placeholder — no injected transport ever reads it, and the
    no-key degrade test below deletes it explicitly."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-placeholder-not-used")


def make_position(
    pos_id: str,
    origin: str = "JFK",
    dest: str = "LIS",
    cost: Decimal = Decimal("610.00"),
    mark: Decimal = Decimal("588.00"),
    trip_label: str = "Transatlantic client visit",
) -> Position:
    return Position(
        id=pos_id,
        trip_label=trip_label,
        origin=origin,
        dest=dest,
        depart_date=NOW.date(),
        pax=1,
        status="held",
        cost_basis=cost,
        mark_price=mark,
        mark_at=NOW,
        mark_stale=False,
    )


def make_positions() -> list[Position]:
    """JFK→LIS carries the worst delta (−$22); SIN→NRT is positive."""
    return [
        make_position("desk-t-pos-1"),  # 588 - 610 = −22.00
        make_position(
            "desk-t-pos-2", origin="SIN", dest="NRT",
            cost=Decimal("445.00"), mark=Decimal("462.00"),
            trip_label="Regional sales run",
        ),
    ]


def make_mandate() -> Mandate:
    return Mandate(
        id="desk-t",
        holder="t",
        created_at=NOW,
        budget_total=Decimal("12000.00"),
        authority_cap=Decimal("1500.00"),
        contingency_pct=0.05,
        currency="USD",
    )


LEDGER_TAIL = [
    {
        "id": 1, "ts": NOW, "kind": "adjust", "amount": Decimal("0"),
        "position_id": None, "ref": None,
        "note": "portfolio seeded — cost bases are demo seeds",
    },
    {
        "id": 2, "ts": NOW, "kind": "trade", "amount": Decimal("0"),
        "position_id": "desk-t-pos-1", "ref": None,
        "note": "comparison mode — decision 'hold' logged, not executed",
    },
]


def read_sync(auditor: RiskAuditor, breaches: int = 0) -> tuple[str, str]:
    return asyncio.run(auditor.read(
        make_mandate(), make_positions(), LEDGER_TAIL, breaches,
    ))


# --- the deterministic fallback is PURE code -------------------------------


def test_fallback_names_worst_delta_position_and_quotes_breach_count():
    """Pure-code one-liner: names the worst mark-vs-cost position
    (JFK→LIS −$22.00 here) and quotes the code-computed breach count."""
    line = fallback_challenge(make_positions(), policy_breaches=0)
    assert "JFK" in line and "LIS" in line
    assert "−$22.00" in line  # the blotter figure, never invented
    assert "0" in line  # the code-computed breach count, quoted
    # Deterministic: same inputs, same line — no randomness, no LLM.
    assert line == fallback_challenge(make_positions(), policy_breaches=0)
    # Without a breach count, no count clause is appended.
    assert "breaches" not in fallback_challenge(make_positions())
    # Empty blotter still yields a line — never raises.
    assert fallback_challenge([], policy_breaches=0)


def test_fallback_never_invents_numbers():
    """Every figure in the fallback line comes off the given blotter:
    swap the positions, and the line's numbers follow them exactly."""
    positions = [
        make_position(
            "desk-t-pos-9", origin="GRU", dest="MIA",
            cost=Decimal("690.00"), mark=Decimal("655.00"),
        ),
    ]
    line = fallback_challenge(positions, policy_breaches=2)
    assert "GRU" in line and "MIA" in line
    assert "−$35.00" in line  # 655 - 690, read off the position
    assert "2" in line


# --- the LLM seam (stubbed transport) --------------------------------------


def test_agent_path_returns_line_and_agent_source():
    """A valid one-sentence reply rides through verbatim with source
    "agent"; the prompt carries the code-computed breach count as a fact
    and forbids inventing/recomputing numbers."""
    challenge = (
        "Held JFK→LIS through a −$22.00 mark while SIN→NRT gained — "
        "was the timing deliberate?"
    )
    seen: dict[str, str] = {}

    async def stub_transport(messages):
        seen["prompt"] = messages[0]["content"]
        return challenge

    auditor = RiskAuditor(transport=stub_transport)
    line, source = read_sync(auditor, breaches=0)
    assert line == challenge
    assert source == SOURCE_AGENT
    # The prompt hands over the breach count as a FACT it may quote only.
    prompt = seen["prompt"]
    assert "NEVER recompute" in prompt
    assert "NEVER invent" in prompt
    # Already-classified blotter data only: trip labels, routes, cost vs
    # mark, ledger kinds/notes.
    assert "Transatlantic client visit" in prompt
    assert "JFK" in prompt and "LIS" in prompt
    assert "decision 'hold' logged" in prompt
    # Honesty register: a second-pass heuristic, never an authority.
    assert AUDITOR_LABEL in prompt


def test_transport_error_degrades_to_fallback():
    """Transport failure → the deterministic line, source flagged."""
    async def raising(messages):
        raise RuntimeError("LLM unavailable (stub)")

    auditor = RiskAuditor(transport=raising)
    line, source = read_sync(auditor, breaches=0)
    assert source == SOURCE_FALLBACK
    assert line == fallback_challenge(make_positions(), 0)


def test_timeout_degrades_to_fallback():
    """A slow transport past the bounded wait degrades — never hangs."""
    async def slow(messages):
        await asyncio.sleep(1.0)
        return "too late"

    auditor = RiskAuditor(transport=slow, timeout=0.05)
    line, source = read_sync(auditor)
    assert source == SOURCE_FALLBACK
    assert line == fallback_challenge(make_positions(), 0)


@pytest.mark.parametrize("garbage", [
    "",                                        # empty
    "   ",                                     # whitespace only
    "First sentence. Second sentence.",        # two sentences
    "One line\nand another",                   # multiline
    "x" * (LINE_MAX_CHARS + 1),                # over the length cap
])
def test_garbage_output_degrades_to_fallback(garbage):
    """Anything that is not ONE sentence within the cap degrades."""
    async def bad(messages):
        return garbage

    auditor = RiskAuditor(transport=bad)
    line, source = read_sync(auditor)
    assert source == SOURCE_FALLBACK
    assert line == fallback_challenge(make_positions(), 0)


def test_non_string_reply_degrades_and_read_never_raises():
    """A non-str reply is garbage too — `read` degrades and NEVER raises,
    whatever the transport returns."""
    async def wrong_type(messages):
        return 12345

    auditor = RiskAuditor(transport=wrong_type)
    line, source = read_sync(auditor)
    assert source == SOURCE_FALLBACK
    assert line


def test_no_key_and_no_transport_degrades_up_front(monkeypatch):
    """No DASHSCOPE_API_KEY and no injected transport → fallback without
    attempting any call (brain.py:105 precedent)."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    auditor = RiskAuditor()
    line, source = read_sync(auditor)
    assert source == SOURCE_FALLBACK
    assert line == fallback_challenge(make_positions(), 0)
