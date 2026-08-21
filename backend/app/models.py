"""Waypoint domain types — the Gate 3 contract.

These shapes ARE the frontend/API contract. Slice 1 fills them with
hardcoded fixture data; Slices 2-5 fill them with real Atlas search /
rules / judge / execute output WITHOUT changing the shapes, so the
frontend never moves.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

# 3-state verdict, not a bool. `unknown` is intentional and load-bearing:
# fail-closed depends on it (see ADR 0002 / 0003).
VerdictStatus = Literal["allowed", "blocked", "unknown"]

PriceStatus = Literal["reference", "current", "verified"]
# "pending" = Slice 2's honest intermediate state: real search found a top
# candidate, but rules (Slice 3), ranking (Slice 4) and booking (Slice 5)
# haven't run yet — nothing is rejected and nothing is booked.
RecoveryStatus = Literal[
    "recovered", "pending", "no_legal_option", "needs_override", "failed"
]


class Passenger(BaseModel):
    name: str
    passport_country: str  # ISO-2 nationality — the thing nobody else checks
    passport_expiry: date
    doc_number: str
    issuing_country: str


class Segment(BaseModel):
    dep_airport: str
    arr_airport: str
    dep_time: datetime
    arr_time: datetime
    flight_number: str
    direction: str = "outbound"
    status: Literal["active", "cancelled"] = "active"


class Layover(BaseModel):
    airport: str
    country: str
    city: str = ""  # display city from data/iata_city.csv; "" if unknown
    hours: float
    # SECONDARY hint only, never decisive (ADR 0002): ticket structure
    # softens messaging but never flips a verdict.
    same_ticket: bool


class Offer(BaseModel):
    id: str
    atlas_offer_id: str
    price: Decimal
    currency: str = "USD"
    total_minutes: int
    segments: list[Segment]
    price_status: PriceStatus = "current"
    bookable: bool = True
    # Ticket-structure hint: False = self-transfer (separate tickets, must
    # clear immigration + recheck bags). Feeds Layover.same_ticket.
    same_ticket: bool = True

    def layovers(
        self,
        iata: dict[str, str],
        cities: dict[str, str] | None = None,
    ) -> list[Layover]:
        """Compute the connecting airports between consecutive segments.

        `iata` maps airport IATA -> ISO-2 country (data/iata_country.csv
        via app.data.loaders). `cities` optionally maps IATA -> display
        city so the frontend never re-hardcodes geography.
        """
        out: list[Layover] = []
        for i in range(len(self.segments) - 1):
            cur, nxt = self.segments[i], self.segments[i + 1]
            hours = (nxt.dep_time - cur.arr_time).total_seconds() / 3600.0
            out.append(
                Layover(
                    airport=cur.arr_airport,
                    country=iata.get(cur.arr_airport, "??"),
                    city=(cities or {}).get(cur.arr_airport, ""),
                    hours=round(hours, 2),
                    same_ticket=self.same_ticket,
                )
            )
        return out


class RuleVerdict(BaseModel):
    rule_name: str
    status: VerdictStatus
    reason: str
    source: str | None = None  # per-cell provenance (ADR 0002)
    last_checked: date | None = None


class OfferAssessment(BaseModel):
    offer: Offer
    verdicts: list[RuleVerdict]
    # True iff EVERY verdict.status == "allowed". The execute wall (ADR 0003)
    # auto-books only executable offers; code re-checks this after any pick.
    executable: bool


class RankedDecision(BaseModel):
    chosen_offer_id: str  # MUST be an executable offer; code re-checks
    rationale: str  # narrates the rejected blocked/unknown options too


class Order(BaseModel):
    order_no: str
    pnr: str
    ticket_number: str
    original_fare: Decimal
    new_fare: Decimal
    fare_diff: Decimal
    currency: str = "USD"
    settled: bool
    # Asserted via queryOrderDetails (a real issued ticket), not a 200 OK.
    ticket_asserted: bool


class RecoveryResult(BaseModel):
    trip_id: str
    status: RecoveryStatus
    chosen: Offer | None
    rejected_cheapest: Offer | None
    order: Order | None
    step_count: int
    rationale: str | None
    # Layovers of chosen + rejected offers, so Screen 3 reads country/city
    # from the wire instead of a hardcoded map (added in Slice 2; additive).
    layovers: list[Layover] = []
