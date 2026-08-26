"""Seeded demo portfolio + curated volatility priors (honesty-first).

S1 honesty register: cost bases are SEEDED and volatility priors are
CURATED per route type — no ML, no scores dressed as predictions. Both
carry provenance notes and are disclosed on the mandate card / meta event
(ADR 0002 precedent: honest curation with provenance).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.models import Budget, Mandate, Position

# Curated per-route-type volatility bands (fraction-of-fare move considered
# "normal" between reprices). Provenance is part of the data — the desk
# never surfaces a band without its disclosure note.
PRIOR_PROVENANCE = (
    "curated approximation — no ML (ADR 0002 precedent); disclosed, "
    "never dressed as a prediction"
)

VOLATILITY_PRIORS: dict[str, dict] = {
    "short_haul": {
        "label": "short-haul (< 3h)",
        "band_pct": (0.02, 0.07),
        "note": PRIOR_PROVENANCE,
    },
    "mid_haul": {
        "label": "mid-haul (3–6h)",
        "band_pct": (0.03, 0.10),
        "note": PRIOR_PROVENANCE,
    },
    "long_haul": {
        "label": "long-haul (6–12h)",
        "band_pct": (0.05, 0.14),
        "note": PRIOR_PROVENANCE,
    },
    "transcontinental": {
        "label": "transcontinental (> 12h)",
        "band_pct": (0.07, 0.18),
        "note": PRIOR_PROVENANCE,
    },
}

# Disclosed on the mandate card (02-architecture.md honesty register).
SEED_DISCLOSURES = (
    "sandbox money only",
    "cost bases seeded — demo seeds, not historical fact",
    "volatility priors curated per route type — no ML",
)

# Demo mandate parameters (deterministic values; the desk id is unique).
BUDGET_TOTAL = Decimal("12000.00")
AUTHORITY_CAP = Decimal("1500.00")
CONTINGENCY_PCT = 0.05


def seeded_portfolio(
    inject_scenario: bool = True,
) -> tuple[Mandate, list[Position], list[Budget]]:
    """Deterministic demo portfolio: 6 positions with seeded cost bases.

    One-flag scenario injection (S7, DECISION 1 — default ON):

    - inject_scenario=True (default): position 2 (DAC→LHR) has its mark
      above the authority cap (arms the escalation beat); positions 3
      (JFK→LIS) and 6 (GRU→MIA) carry mark-below-cost unrealized losses.
    - inject_scenario=False (clean baseline for rehearsal): same six
      routes, but pos-2's mark sits in-band under the cap near cost, and
      pos-3/6 marks sit at or above cost — no spike, no scripted losses.

    Everything else is plain deterministic data (no randomness beyond the
    unique desk id).
    """
    desk_id = f"desk-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    if inject_scenario:
        spike_label = "London client meeting"
        spike_mark = Decimal("1790.00")   # over the 1500 cap — arms the beat
        lis_mark = Decimal("588.00")      # −22 vs cost — unrealized loss
        mia_mark = Decimal("655.00")      # −35 vs cost — unrealized loss
    else:
        spike_label = "London client meeting"
        spike_mark = Decimal("845.00")    # in-band, near cost, under the cap
        lis_mark = Decimal("623.00")      # >= cost — no injected loss
        mia_mark = Decimal("702.00")      # >= cost — no injected loss
    mandate = Mandate(
        id=desk_id,
        holder="Waypoint Demo Desk",
        created_at=now,
        budget_total=BUDGET_TOTAL,
        authority_cap=AUTHORITY_CAP,
        contingency_pct=CONTINGENCY_PCT,
        currency="USD",
    )

    def pos(
        n: int,
        trip_label: str,
        origin: str,
        dest: str,
        depart: date,
        pax: int,
        cost_basis: Decimal,
        mark_price: Decimal,
    ) -> Position:
        return Position(
            id=f"{desk_id}-pos-{n}",
            trip_label=trip_label,
            origin=origin,
            dest=dest,
            depart_date=depart,
            pax=pax,
            status="held",
            cost_basis=cost_basis,
            mark_price=mark_price,
            mark_at=now,
            mark_stale=False,
        )

    positions = [
        pos(
            1, "Regional sales run", "SIN", "NRT", date(2026, 9, 18), 2,
            Decimal("445.00"), Decimal("462.00"),
        ),
        pos(
            2, spike_label, "DAC", "LHR",
            date(2026, 9, 25), 1, Decimal("820.00"), spike_mark,
        ),
        pos(
            3, "Transatlantic client visit", "JFK", "LIS", date(2026, 10, 2), 1,
            Decimal("610.00"), lis_mark,
        ),
        pos(
            4, "Team offsite leg", "BKK", "ICN", date(2026, 9, 29), 3,
            Decimal("388.00"), Decimal("401.00"),
        ),
        pos(
            5, "Conference return", "SYD", "SIN", date(2026, 10, 9), 2,
            Decimal("705.00"), Decimal("742.00"),
        ),
        pos(
            6, "Q4 planning trip", "GRU", "MIA", date(2026, 10, 16), 1,
            Decimal("690.00"), mia_mark,
        ),
    ]

    budgets = [
        Budget(
            desk_id=desk_id, period="2026-W38",
            allocated=Decimal("6000.00"), contingency=Decimal("300.00"),
        ),
        Budget(
            desk_id=desk_id, period="2026-W39",
            allocated=Decimal("4000.00"), contingency=Decimal("200.00"),
        ),
        Budget(
            desk_id=desk_id, period="2026-W40",
            allocated=Decimal("2000.00"), contingency=Decimal("100.00"),
        ),
    ]
    return mandate, positions, budgets
