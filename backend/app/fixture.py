"""Demo data + canned choreography.

Slice 2: the SEARCH is real (Atlas sandbox), so this module's offers now
serve as deterministic test/stub data only. Verdicts, the decision, and
the booking result remain canned until Slices 3-5 replace them — nothing
downstream of the Gate 3 types changes shape.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.data.loaders import load_iata_city, load_iata_country
from app.models import (
    Offer,
    OfferAssessment,
    Passenger,
    RecoveryResult,
    RuleVerdict,
    Segment,
)

# The sandbox-verified demo date for SIN -> NRT.
_DEMO_DATE = "2026-09-04"

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
    """Deterministic reroute candidates for tests / stub clients.

    Shape-identical to real Atlas offers (Slice 2 probe: 2-segment
    connection itineraries, USD totals) but with stable ids + prices so
    the pipe tests stay deterministic without live calls.
    """
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


def slice2_verdicts() -> list[RuleVerdict]:
    """Placeholder verdicts until the rules engine lands (Slice 3).

    Honest + fail-closed: transit is `unknown` (nothing is auto-executable
    yet); the passport check is free — expiry is already in the payload.
    """
    return [
        RuleVerdict(
            rule_name="transit_visa",
            status="unknown",
            reason="Rules engine lands in Slice 3 \u2014 fail-closed until then",
        ),
        RuleVerdict(
            rule_name="passport_validity",
            status="allowed",
            reason="Passport valid to 2031-05-10 (> 6 months beyond travel)",
        ),
    ]


# Advise-gate narration (ADR 0003) is Slice 4's real Qwen output — Slice 2
# carries NO rationale rather than a canned story the real data contradicts.
CANNED_DECISION_NOTE = (
    "Slice 2 placeholder: cheapest candidate leads. Real ranking + "
    "narration land with the Qwen judge in Slice 4."
)


def build_result(trip_id: str, step_count: int, chosen: Offer) -> RecoveryResult:
    """Honest intermediate result (Slice 2).

    Real search found a top candidate — but nothing is REJECTED (the rules
    engine lands in Slice 3) and nothing is BOOKED (Slice 5). Guard #3:
    never assert a PNR/ticket that wasn't issued, so order stays None and
    the status is `pending`, not `recovered`.
    """
    iata, cities = load_iata_country(), load_iata_city()
    return RecoveryResult(
        trip_id=trip_id,
        status="pending",
        chosen=chosen,
        rejected_cheapest=None,
        order=None,
        step_count=step_count,
        rationale=None,
        layovers=list(chosen.layovers(iata, cities)),
    )


def assessment_payload(
    assessment: OfferAssessment,
    iata: dict[str, str],
    cities: dict[str, str],
) -> dict:
    """Serialize one assessment for the SSE `options` event."""
    return {
        "offer": assessment.offer.model_dump(mode="json"),
        "layovers": [
            layover.model_dump(mode="json")
            for layover in assessment.offer.layovers(iata, cities)
        ],
        "verdicts": [v.model_dump(mode="json") for v in assessment.verdicts],
        "executable": assessment.executable,
    }
