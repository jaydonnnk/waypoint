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
    UniqueConstraint,
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
    # Display-only trip context (operator-provided, not derived).
    team_size: Mapped[int] = mapped_column(Integer, default=1)
    destination_label: Mapped[str] = mapped_column(String, default="")
    trip_purpose: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    # Waybot lifecycle gate (S1). DEFAULT 'released' so every pre-existing
    # desk — and every ungated seed — keeps today's behavior exactly.
    # awaiting_travelers | released | pending_approval | closed.
    lifecycle: Mapped[str] = mapped_column(String, default="released")
    # URL-safe [A-Za-z0-9_-] deep-link token (<=64); indexed for token->desk.
    invite_token: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    # Salted hash of the release code — plaintext is NEVER stored.
    confirmation_code_hash: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    # G4 (S5): the offer the manager signed off, pinned for the resumed cycle.
    approved_offer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # G2 (S7): {airlines:[IATA], cabin, depart_after, arrive_by}; absent -> no filter.
    policy_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # G4 re-approval cap (1) for the unbookable-pin edge (S5).
    reapproval_count: Mapped[int] = mapped_column(Integer, default=0)
    # Confirmation-code attempt cap (5) — wrong-code guessers throttled with 429; verify-first, no lockout (S4).
    code_attempts: Mapped[int] = mapped_column(Integer, default=0)


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


class TravelerRow(Base):
    """One captured traveler on a desk (S3 write path). MRZ-derived fields
    only — the raw passport image is never persisted. Purged at desk close."""

    __tablename__ = "travelers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    desk_id: Mapped[str] = mapped_column(ForeignKey("mandate.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)  # 1..team_size
    family_name: Mapped[str] = mapped_column(String)
    given_name: Mapped[str] = mapped_column(String)
    gender: Mapped[str] = mapped_column(String)          # "M"/"F"
    birthday: Mapped[str] = mapped_column(String)        # "YYYY-MM-DD"
    nationality: Mapped[str] = mapped_column(String)     # ISO-2
    doc_type: Mapped[str] = mapped_column(String, default="PP")
    doc_number: Mapped[str] = mapped_column(String)
    issuing_country: Mapped[str] = mapped_column(String)
    doc_expiry: Mapped[str] = mapped_column(String)      # "YYYY-MM-DD"
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_mobile: Mapped[str | None] = mapped_column(String, nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class ChatBindingRow(Base):
    """Binds one private Telegram chat to one traveler slot on one desk (S2),
    so a re-sent photo updates the same slot rather than a new row."""

    __tablename__ = "chat_bindings"
    __table_args__ = (
        # No two chats may claim the same slot on the same desk (M3 fix).
        UniqueConstraint("desk_id", "slot", name="uq_chat_bindings_desk_slot"),
    )

    telegram_chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    desk_id: Mapped[str] = mapped_column(ForeignKey("mandate.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)


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
