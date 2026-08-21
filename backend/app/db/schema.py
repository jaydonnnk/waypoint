"""SQLAlchemy desk tables per 02-architecture.md §Data — first real DB writes.

Desk linkage: one desk per mandate — `mandate.id` IS the desk_id, and
`positions` / `ledger` / `budgets` all FK back to it. `ledger` +
`positions` are the persisted evidence of every decision (blotter =
audit trail → Compliance & Safety).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MandateRow(Base):
    """The mandate is the desk: its `id` is the desk_id (one desk per mandate)."""

    __tablename__ = "mandate"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    budget_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    authority_cap: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    contingency_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    currency: Mapped[str] = mapped_column(String(3))
    holder: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    desk_id: Mapped[str] = mapped_column(
        ForeignKey("mandate.id"), index=True
    )
    trip_label: Mapped[str] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String(3))
    dest: Mapped[str] = mapped_column(String(3))
    depart_date: Mapped[date] = mapped_column(Date)
    pax: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="held")  # held|booked
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    mark_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    mark_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mark_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    atlas_offer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    atlas_order_no: Mapped[str | None] = mapped_column(String, nullable=True)
    # Guard: assert a real issued ticket (TICKETED), not a 200 OK.
    ticket_asserted: Mapped[bool] = mapped_column(Boolean, default=False)


class LedgerRow(Base):
    __tablename__ = "ledger"
    __table_args__ = (
        # The blotter is read back per desk, newest-first.
        Index("ix_ledger_desk_ts", "desk_id", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk_id: Mapped[str] = mapped_column(ForeignKey("mandate.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    kind: Mapped[str] = mapped_column(String)  # trade|alloc|reconcile|loss|adjust
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True
    )
    ref: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class BudgetRow(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    desk_id: Mapped[str] = mapped_column(ForeignKey("mandate.id"))
    period: Mapped[str] = mapped_column(String)
    allocated: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    contingency: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
