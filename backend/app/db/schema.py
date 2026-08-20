"""SQLAlchemy tables per 02-architecture.md (Data section).

Created on startup by init_db(). NOT written by the Slice-1 flow — the
audit-trail persistence (verdicts / decisions / orders) lands in Slice 6.
`rule_verdicts` and `decisions` are the persisted evidence the agent
reasoned correctly (Compliance & Operating-Scale points).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PassengerRow(Base):
    __tablename__ = "passengers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    passport_country: Mapped[str] = mapped_column(String(2))
    passport_expiry: Mapped[date] = mapped_column(Date)
    doc_number: Mapped[str] = mapped_column(String)
    issuing_country: Mapped[str] = mapped_column(String(2))


class TripRow(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    passenger_id: Mapped[int] = mapped_column(ForeignKey("passengers.id"))
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SegmentRow(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    dep_airport: Mapped[str] = mapped_column(String(3))
    arr_airport: Mapped[str] = mapped_column(String(3))
    dep_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arr_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    flight_number: Mapped[str] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String, default="outbound")
    status: Mapped[str] = mapped_column(String, default="active")  # active|cancelled


class OfferRow(Base):
    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    atlas_offer_id: Mapped[str] = mapped_column(String)
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    total_minutes: Mapped[int] = mapped_column(Integer)
    segments_json: Mapped[str] = mapped_column(Text)  # serialized segments
    price_status: Mapped[str] = mapped_column(String)  # reference|current|verified
    bookable: Mapped[bool] = mapped_column(Boolean, default=True)


class RuleVerdictRow(Base):
    __tablename__ = "rule_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    rule_name: Mapped[str] = mapped_column(String)
    # 3-state per Gate 3 (allowed|blocked|unknown). The architecture doc's
    # `allowed` bool is generalized here so fail-closed `unknown` persists.
    status: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    chosen_offer_id: Mapped[str] = mapped_column(String)
    rejected_cheapest_offer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id"))
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id"))
    atlas_order_no: Mapped[str] = mapped_column(String)
    pnr: Mapped[str] = mapped_column(String)
    ticket_number: Mapped[str] = mapped_column(String)
    fare_diff: Mapped[float] = mapped_column(Numeric(10, 2))
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Guard #3 (guide §31): assert a real issued ticket, not a 200 OK.
    ticket_asserted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
