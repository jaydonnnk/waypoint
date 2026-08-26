"""Brain eval cases — S13 (opt-in, OUTSIDE the quality gate).

EVERY scenario in this file is INVENTED. The positions, prices, routes,
budgets and meters are fabricated desk states built to sit exactly on the
edges of the curated prior bands and the execute wall's checks. None of
this data is real traveler demand, real fares, or real Atlas output.

Expectations are STRUCTURAL BANDS, never prose (Orkestr cases.ts posture):
each case carries a `band` — the frozenset of DeskAction kinds a sane desk
brain may legally pick for that scenario. The harness REPORTS whether the
live Qwen pick and the deterministic fallback pick land in the band; it
never asserts on model quality (see tests/evals/test_brain_eval.py).

Band edges covered (curated priors: mid_haul 3–10%, long_haul 5–14%):
above-band spike, exact band-top boundary, in-band hold, below-floor loss,
deep loss, stale mark, over-cap book, budget-starved book, meter-exhausted,
and an escalation-worthy stale over-cap spike.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models import Position

# Fixed clock + departure so the cases are reproducible scenario shapes.
CASE_MARK_AT = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
CASE_DEPART = date(2026, 9, 15)

# Desk defaults shared by most cases (the seeded-demo mandate shape):
# authority cap 1500, budget 12000, contingency 600, full 20/20 meter.
DEFAULT_CAP = Decimal("1500.00")
DEFAULT_BUDGET_LEFT = Decimal("12000.00")
DEFAULT_CONTINGENCY_LEFT = Decimal("600.00")
DEFAULT_METER_LEFT = 20


@dataclass(frozen=True)
class BrainCase:
    id: str
    # One-line description of the edge this case pins (structure, not prose
    # expectations — the band below is the expectation).
    tests: str
    position: Position
    # The expected action band: the frozenset of legal DeskAction kinds for
    # this scenario. Membership is REPORTED by the harness, never asserted.
    band: frozenset
    # Desk state handed to DeskBrain.judge and to the execute-wall check.
    meter_left: int = DEFAULT_METER_LEFT
    budget_left: Decimal = DEFAULT_BUDGET_LEFT
    contingency_left: Decimal = DEFAULT_CONTINGENCY_LEFT
    authority_cap: Decimal = DEFAULT_CAP


def _pos(
    pos_id: str,
    origin: str,
    dest: str,
    cost: str,
    mark: str,
    stale: bool = False,
) -> Position:
    """One invented held position (route pairs chosen from the curated
    ROUTE_TYPES table so the prior band is deterministic)."""
    return Position(
        id=pos_id,
        trip_label=f"eval {pos_id}",
        origin=origin,
        dest=dest,
        depart_date=CASE_DEPART,
        pax=1,
        status="held",
        cost_basis=Decimal(cost),
        mark_price=Decimal(mark),
        mark_at=CASE_MARK_AT,
        mark_stale=stale,
    )


BRAINS_CASES: list[BrainCase] = [
    # --- above-band spikes: the mark ran past the curated band top ------
    BrainCase(
        id="c01-spike-mid",
        tests="mid-haul mark +15% ran past the 10% band top — lock the fare",
        position=_pos("c01", "BKK", "ICN", "1000.00", "1150.00"),
        band=frozenset({"book"}),
    ),
    BrainCase(
        id="c02-spike-long",
        tests="long-haul mark +20% ran past the 14% band top — lock the fare",
        position=_pos("c02", "SIN", "NRT", "1000.00", "1200.00"),
        band=frozenset({"book"}),
    ),
    # --- exact band-top boundary: fallback books on >= -------------------
    BrainCase(
        id="c03-band-top-exact",
        tests="mark exactly +10.0% == mid-haul band top (boundary, >= books)",
        position=_pos("c03", "BKK", "ICN", "1000.00", "1100.00"),
        band=frozenset({"book", "hold"}),
    ),
    # --- in-band holds ----------------------------------------------------
    BrainCase(
        id="c04-in-band-mid",
        tests="mid-haul mark +5% sits inside the 3–10% band — timing not "
              "triggered",
        position=_pos("c04", "BKK", "ICN", "1000.00", "1050.00"),
        band=frozenset({"hold"}),
    ),
    BrainCase(
        id="c05-in-band-long",
        tests="long-haul mark +8% sits inside the 5–14% band — hold",
        position=_pos("c05", "SIN", "NRT", "1000.00", "1080.00"),
        band=frozenset({"hold"}),
    ),
    # --- below-floor losses (the admitted-loss threshold region) ---------
    BrainCase(
        id="c06-below-floor",
        tests="mid-haul mark −4% moved past the −3% band floor — the desk "
              "was wrong; no booking",
        position=_pos("c06", "BKK", "ICN", "1000.00", "960.00"),
        band=frozenset({"hold", "escalate"}),
    ),
    BrainCase(
        id="c07-deep-loss",
        tests="long-haul mark −25% far past the −5% floor — hold or "
              "escalate, never book",
        position=_pos("c07", "DAC", "LHR", "2000.00", "1500.00"),
        band=frozenset({"hold", "escalate"}),
    ),
    # --- stale mark: uncertainty disclosed, lean hold ---------------------
    BrainCase(
        id="c08-stale-in-band",
        tests="stale mark inside the band — uncertainty means lean hold",
        position=_pos("c08", "BKK", "ICN", "1000.00", "1060.00", stale=True),
        band=frozenset({"hold"}),
    ),
    # --- over-cap book: band says book, the wall says escalate -----------
    BrainCase(
        id="c09-over-cap",
        tests="mark 1790 sits far above band AND above the 1500 authority "
              "cap — book needs the human click",
        position=_pos("c09", "DAC", "LHR", "800.00", "1790.00"),
        band=frozenset({"book", "escalate"}),
    ),
    # --- budget-starved book: band top passed but the desk cannot pay ----
    BrainCase(
        id="c10-budget-starved",
        tests="mark +20% past band top but budget_left 900 cannot cover the "
              "1200 mark — locking a fare you cannot pay is not legal",
        position=_pos("c10", "SIN", "NRT", "1000.00", "1200.00"),
        budget_left=Decimal("900.00"),
        band=frozenset({"hold", "escalate"}),
    ),
    # --- meter-exhausted: above band but zero searches left --------------
    BrainCase(
        id="c11-meter-exhausted",
        tests="mark +15% above band but the search meter is at 0/20 — no "
              "fresh price, so hold or escalate on disclosed uncertainty",
        position=_pos("c11", "BKK", "ICN", "1000.00", "1150.00"),
        meter_left=0,
        band=frozenset({"hold", "escalate"}),
    ),
    # --- escalation-worthy: stale + above band + over cap -----------------
    BrainCase(
        id="c12-stale-over-cap",
        tests="stale mark +70% above band AND above the cap — the one case "
              "a human must touch; booking on a stale over-cap price is "
              "never legal",
        position=_pos("c12", "SIN", "NRT", "1000.00", "1700.00", stale=True),
        band=frozenset({"escalate", "hold"}),
    ),
]
