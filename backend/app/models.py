"""Waypoint domain types — the desk contract.

These shapes ARE the frontend/API contract. S1 fills them with a seeded
portfolio (disclosed); later slices fill them with real Atlas reprice /
judgment / execute output WITHOUT changing the shapes, so the frontend
never moves. Offer/Layover/Segment stay verbatim from the tracer.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

PriceStatus = Literal["reference", "current", "verified"]

PositionStatus = Literal["held", "booked"]
DeskStatus = Literal["closed", "escalated", "budget_exhausted", "failed"]
DeskActionKind = Literal["book", "hold", "escalate"]


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


class Mandate(BaseModel):
    """The desk's mandate — DB `mandate` table is the source of truth.

    Desk linkage: `id` IS the desk_id (one desk per mandate).
    """

    id: str
    holder: str
    # Display-only trip context (operator-provided, not derived).
    team_size: int = Field(default=1, ge=1, le=50)
    destination_label: str = ""
    trip_purpose: str = ""
    created_at: datetime
    budget_total: Decimal
    authority_cap: Decimal
    contingency_pct: float
    currency: str


class Position(BaseModel):
    """One held/booked travel position on the desk blotter."""

    id: str
    trip_label: str
    origin: str
    dest: str
    depart_date: date
    pax: int
    status: PositionStatus = "held"
    cost_basis: Decimal
    mark_price: Decimal
    mark_at: datetime
    mark_stale: bool = False
    atlas_offer_id: str | None = None
    atlas_order_no: str | None = None
    ticket_asserted: bool = False


class Budget(BaseModel):
    """One period budget line. `id` is DB-assigned (0 until persisted)."""

    id: int = 0
    desk_id: str
    period: str
    allocated: Decimal
    spent: Decimal = Decimal("0")
    contingency: Decimal


class DeskAction(BaseModel):
    """Advise-gate pick for one position; the execute wall re-checks it."""

    position_id: str
    kind: DeskActionKind
    rationale: str


class DeskResult(BaseModel):
    """Terminal state of one desk cycle (the `result` SSE event payload)."""

    desk_id: str
    status: DeskStatus
    pnl: Decimal
    losses_admitted: int
    step_count: int
    # Honest day-4 fallback flag: True while sandbox ticketing is blocked —
    # decisions are logged and marked, nothing is ordered (labeled on screen).
    comparison_mode: bool = True


class CloseReport(BaseModel):
    """Weekly-close payload returned ONLY by GET /api/desk/{desk_id}/close.

    Wraps the bare DeskResult — which stays the `result` SSE event payload,
    byte-identical — with the close-only extras (S7). Split per ADR
    0003/0004: `policy_breaches` is deterministic code scanning the blotter
    (never an LLM); `auditor_line` is the risk auditor's narration over
    already-classified data — a second-pass heuristic challenge, never an
    oracle or authority. `auditor_source`: "agent" | "deterministic-fallback".
    `auditor_plain` (task #8, additive): one short plain-English sentence
    built IN CODE from the same structured blotter facts the auditor read —
    never parsed out of `auditor_line`. None only if that builder degrades;
    the verbatim `auditor_line` stays the record of truth either way.
    """

    result: DeskResult
    policy_breaches: int
    auditor_line: str
    auditor_source: str
    auditor_plain: str | None = None


# --- Atlas write path (S2). Offer/Layover above stay verbatim. -------------

PriceChange = Literal["unchanged", "decreased", "increased"]


class VerifyResult(BaseModel):
    """`offer verify --offer-id` outcome. `booking_id` provenance is the
    verify response itself — it is the ONLY source for confirm-price /
    order create (02-architecture §Flow step 6)."""

    offer_id: str
    booking_id: str
    price_change: PriceChange
    previous_price: Decimal
    current_price: Decimal
    currency: str
    seat_supported: bool = False
    baggage_supported: bool = False
    # Opaque CLI-provided traveler IDs/types (passenger-input.md: carry,
    # never invent). Empty until a live verify fills it.
    travelers: list[dict] = []


class OrderRef(BaseModel):
    """`order create` outcome. `payment_confirmation_id` is single-use and
    the ONLY legal input to `order pay`; `order_no` the only key for
    `order status`."""

    payment_confirmation_id: str
    order_no: str


class AuthStatus(BaseModel):
    """`auth status` snapshot — drives the comparison-mode switch."""

    code: str
    authorized: bool = False
    search_available: bool = False
    ticketing_available: bool = False
    ticketing_blocker: str | None = None


class PaymentResult(BaseModel):
    """`order pay` outcome — branch on `code`, never `message`.
    `query_only` = the ONLY legal follow-up is `order status` (never
    re-pay, never re-create)."""

    code: str
    order_no: str | None = None
    ticketed: bool = False
    pending_ticketing: bool = False  # TICKETING_PENDING = continuing
    query_only: bool = False


class OrderStatus(BaseModel):
    """`order status` outcome. A position is "booked" ONLY when
    `ticketed` (code == TICKETED) — never on an accepted response."""

    code: str
    order_no: str | None = None
    ticketed: bool = False


class SeatSelection(BaseModel):
    """`booking seat select` outcome. `available=False` (SEAT_UNAVAILABLE)
    means the alloc degrades to ledger-only; the main flow continues."""

    available: bool
    code: str
