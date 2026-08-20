"""Hardcoded Slice-1 demo data (canned choreography).

Every number here mirrors the mockups and the sandbox gate-check figures.
Slices 2-5 replace this module's ROLE (not the shapes): real Atlas search,
real rules, real Qwen narration, real booking. Nothing downstream of the
Gate 3 types changes.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.models import (
    Offer,
    OfferAssessment,
    Order,
    Passenger,
    RecoveryResult,
    RuleVerdict,
    Segment,
)

# Demo IATA -> ISO-2 map. Slice 3 replaces this with data/iata_country.csv.
DEMO_IATA: dict[str, str] = {
    "SIN": "SG",
    "SGN": "VN",
    "DMK": "TH",
    "ICN": "KR",
    "NRT": "JP",
}

# The sandbox-verified demo date for SIN -> NRT.
_DEMO_DATE = "2026-09-04"

# Original fare paid on the now-cancelled leg (drives the +$92 diff).
ORIGINAL_FARE = Decimal("366")

DEMO_PASSENGER = Passenger(
    name="TEST/TRAVELER",
    passport_country="IN",
    passport_expiry=date(2031, 5, 10),
    doc_number="Z1234567",
    issuing_country="IN",
)


def _seg(dep: str, arr: str, dep_t: str, arr_t: str, flight: str,
         status: str = "active") -> Segment:
    return Segment(
        dep_airport=dep,
        arr_airport=arr,
        dep_time=datetime.fromisoformat(f"{_DEMO_DATE}T{dep_t}:00"),
        arr_time=datetime.fromisoformat(f"{_DEMO_DATE}T{arr_t}:00"),
        flight_number=flight,
        status=status,  # type: ignore[arg-type]
    )


# The disrupted original leg (what Screen 1 shows as CANCELLED).
DEMO_CANCELLED_LEG = _seg("SIN", "NRT", "01:00", "11:15", "TR866", status="cancelled")


def demo_offers() -> list[Offer]:
    """The three reroute candidates (cheapest-trap, second-trap, legal pick)."""
    sgn = Offer(
        id="opt-sgn",
        atlas_offer_id="atlas-sgn-001",
        price=Decimal("236"),
        total_minutes=960,  # 16h
        segments=[
            _seg("SIN", "SGN", "03:00", "04:25", "TR2070"),
            _seg("SGN", "NRT", "09:25", "19:00", "VJ128"),
        ],
        same_ticket=False,  # self-transfer: clear immigration + recheck bags
    )
    dmk = Offer(
        id="opt-dmk",
        atlas_offer_id="atlas-dmk-001",
        price=Decimal("480"),
        total_minutes=1020,  # 17h
        segments=[
            _seg("SIN", "DMK", "05:40", "07:10", "FD356"),
            _seg("DMK", "NRT", "13:10", "22:40", "XJ606"),
        ],
        same_ticket=False,  # landside transfer at Don Mueang
    )
    icn = Offer(
        id="opt-icn",
        atlas_offer_id="atlas-icn-001",
        price=Decimal("458"),
        total_minutes=780,  # 13h
        segments=[
            _seg("SIN", "ICN", "01:30", "09:00", "KE642"),
            _seg("ICN", "NRT", "11:30", "14:30", "KE703"),
        ],
        same_ticket=True,  # single ticket, airside transit
    )
    return [sgn, dmk, icn]


def _passport_ok() -> RuleVerdict:
    return RuleVerdict(
        rule_name="passport_validity",
        status="allowed",
        reason="Passport valid to 2031-05-10 (> 6 months beyond travel)",
    )


def demo_assessments() -> list[OfferAssessment]:
    """Offers x the two live rules (transit-visa + passport validity).

    Slice 3 computes these for real; Slice 1 hardcodes them. The two-rule
    shape is mirrored now so the engine isn't read as a one-trick lookup.
    """
    sgn, dmk, icn = demo_offers()
    return [
        OfferAssessment(
            offer=sgn,
            verdicts=[
                RuleVerdict(
                    rule_name="transit_visa",
                    status="blocked",
                    reason="self-transfer needs Vietnam visa",
                ),
                _passport_ok(),
            ],
            executable=False,
        ),
        OfferAssessment(
            offer=dmk,
            verdicts=[
                RuleVerdict(
                    rule_name="transit_visa",
                    status="blocked",
                    reason="landside transfer needs Thai visa",
                ),
                _passport_ok(),
            ],
            executable=False,
        ),
        OfferAssessment(
            offer=icn,
            verdicts=[
                RuleVerdict(
                    rule_name="transit_visa",
                    status="allowed",
                    reason="airside transit OK for IN passport",
                ),
                _passport_ok(),
            ],
            executable=True,
        ),
    ]


# Canned agent steps. Six of them, so the budget reads "6 / 12 used".
DEMO_STEPS: list[str] = [
    "Re-read trip state \u2014 SIN\u2192NRT (Scoot TR866) is cancelled",
    "Searched alternatives \u2014 19 options found (Atlas)",
    "Filtered to 3 bookable candidates at current fares",
    "Re-verified live availability & price (no stale offers)",
    "Checking transit + passport rules for India \U0001F1EE\U0001F1F3 passport\u2026",
    "Weighing price \u00d7 time \u00d7 visa \u00d7 layover\u2026",
]

# Advise-gate narration (ADR 0003): sees ALL options, narrates the rejections,
# picks from the executable ones. Slice 4 replaces this with real Qwen output.
DEMO_RATIONALE = (
    "Cheapest is $236 via Ho Chi Minh (SGN), but that's a self-transfer: an "
    "India passport must clear Vietnamese immigration and a visa is required, "
    "so denial-at-gate risk is real \u2014 rejected. $480 via Bangkok (DMK) also "
    "needs landside entry \u2014 rejected. Picked $458 via Seoul (ICN): airside "
    "transit is legal on this passport and it's the fastest at 13h. Paying $92 "
    "more to stay boardable."
)


def build_result(trip_id: str, step_count: int) -> RecoveryResult:
    """Assemble the final hardcoded RecoveryResult (status = recovered)."""
    assessments = demo_assessments()
    chosen = next(a.offer for a in assessments if a.executable)
    rejected_cheapest = min((a.offer for a in assessments), key=lambda o: o.price)

    order = Order(
        order_no="ATRIP-88412076",
        pnr="WPX9K2",
        ticket_number="999-2408117736",
        original_fare=ORIGINAL_FARE,
        new_fare=chosen.price,
        fare_diff=chosen.price - ORIGINAL_FARE,
        settled=True,
        ticket_asserted=True,
    )
    return RecoveryResult(
        trip_id=trip_id,
        status="recovered",
        chosen=chosen,
        rejected_cheapest=rejected_cheapest,
        order=order,
        step_count=step_count,
        rationale=DEMO_RATIONALE,
    )


def assessment_payload(assessment: OfferAssessment) -> dict:
    """Serialize one assessment for the SSE `options` event."""
    return {
        "offer": assessment.offer.model_dump(mode="json"),
        "layovers": [
            layover.model_dump(mode="json")
            for layover in assessment.offer.layovers(DEMO_IATA)
        ],
        "verdicts": [v.model_dump(mode="json") for v in assessment.verdicts],
        "executable": assessment.executable,
    }
