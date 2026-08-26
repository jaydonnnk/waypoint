"""DeskStore — the ONLY module that opens DB sessions (one per operation).

Pure-sync facade over the desk tables. Routes and the agent loop wrap each
call in `asyncio.to_thread` so the event loop stays free (the engine is the
existing sync one — `check_same_thread=False`).

GUARD: `reload_desk` is the "re-read the world" checkpoint — every cycle
starts by loading mandate/positions/budgets/ledger-tail fresh in ONE
transaction, never acting on cached state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from app.db import database
from app.db.schema import BudgetRow, LedgerRow, MandateRow, PositionRow
from app.models import Budget, Mandate, Position

# How much of the blotter `reload_desk` pulls back (newest-first tail).
LEDGER_TAIL_LIMIT = 50


@dataclass(frozen=True)
class MarkUpdate:
    """One reprice result to persist (the `mark` event's DB evidence)."""

    position_id: str
    mark_price: Decimal
    mark_at: datetime
    mark_stale: bool = False
    atlas_offer_id: str | None = None


@dataclass(frozen=True)
class LedgerInput:
    """One ledger entry to append (kind: trade|alloc|reconcile|loss|adjust)."""

    kind: str
    amount: Decimal
    position_id: str | None = None
    ref: str | None = None
    note: str | None = None


def _to_mandate(row: MandateRow) -> Mandate:
    return Mandate(
        id=row.id,
        holder=row.holder,
        team_size=row.team_size,
        destination_label=row.destination_label,
        trip_purpose=row.trip_purpose,
        created_at=row.created_at,
        budget_total=row.budget_total,
        authority_cap=row.authority_cap,
        contingency_pct=float(row.contingency_pct),
        currency=row.currency,
    )


def _to_position(row: PositionRow) -> Position:
    return Position(
        id=row.id,
        trip_label=row.trip_label,
        origin=row.origin,
        dest=row.dest,
        depart_date=row.depart_date,
        pax=row.pax,
        status=row.status,
        cost_basis=row.cost_basis,
        mark_price=row.mark_price,
        mark_at=row.mark_at,
        mark_stale=row.mark_stale,
        atlas_offer_id=row.atlas_offer_id,
        atlas_order_no=row.atlas_order_no,
        ticket_asserted=row.ticket_asserted,
    )


def _to_budget(row: BudgetRow) -> Budget:
    return Budget(
        id=row.id,
        desk_id=row.desk_id,
        period=row.period,
        allocated=row.allocated,
        spent=row.spent,
        contingency=row.contingency,
    )


def _to_ledger_dict(row: LedgerRow) -> dict:
    return {
        "id": row.id,
        "ts": row.ts,
        "kind": row.kind,
        "amount": row.amount,
        "position_id": row.position_id,
        "ref": row.ref,
        "note": row.note,
    }


class DeskStore:
    """Sync desk persistence facade. One session per operation."""

    def seed_desk(
        self,
        mandate: Mandate,
        positions: list[Position],
        budgets: list[Budget],
    ) -> str:
        """Persist the seeded portfolio. Returns the desk_id (= mandate.id).

        Honesty: one `adjust` ledger entry discloses that the cost bases are
        demo seeds, so the blotter itself carries the provenance note.
        """
        with database.SessionLocal() as session:
            session.add(
                MandateRow(
                    id=mandate.id,
                    budget_total=mandate.budget_total,
                    authority_cap=mandate.authority_cap,
                    contingency_pct=Decimal(str(mandate.contingency_pct)),
                    currency=mandate.currency,
                    holder=mandate.holder,
                    team_size=mandate.team_size,
                    destination_label=mandate.destination_label,
                    trip_purpose=mandate.trip_purpose,
                    created_at=mandate.created_at,
                )
            )
            for pos in positions:
                session.add(
                    PositionRow(
                        id=pos.id,
                        desk_id=mandate.id,
                        trip_label=pos.trip_label,
                        origin=pos.origin,
                        dest=pos.dest,
                        depart_date=pos.depart_date,
                        pax=pos.pax,
                        status=pos.status,
                        cost_basis=pos.cost_basis,
                        mark_price=pos.mark_price,
                        mark_at=pos.mark_at,
                        mark_stale=pos.mark_stale,
                        atlas_offer_id=pos.atlas_offer_id,
                        atlas_order_no=pos.atlas_order_no,
                        ticket_asserted=pos.ticket_asserted,
                    )
                )
            for budget in budgets:
                session.add(
                    BudgetRow(
                        desk_id=budget.desk_id or mandate.id,
                        period=budget.period,
                        allocated=budget.allocated,
                        spent=budget.spent,
                        contingency=budget.contingency,
                    )
                )
            session.add(
                LedgerRow(
                    desk_id=mandate.id,
                    kind="adjust",
                    amount=Decimal("0"),
                    note=(
                        "portfolio seeded — cost bases are demo seeds "
                        "(disclosed, not historical fact); sandbox money only"
                    ),
                )
            )
            session.commit()
        return mandate.id

    def reload_desk(
        self, desk_id: str
    ) -> tuple[Mandate, list[Position], list[Budget], list[dict]]:
        """GUARD: re-read the world fresh in ONE transaction.

        Returns (mandate, positions, budgets, ledger_tail). Raises KeyError
        for an unknown desk.
        """
        with database.SessionLocal() as session:
            mandate_row = session.get(MandateRow, desk_id)
            if mandate_row is None:
                raise KeyError(f"unknown desk: {desk_id}")
            position_rows = (
                session.execute(
                    select(PositionRow)
                    .where(PositionRow.desk_id == desk_id)
                    .order_by(PositionRow.id)
                )
                .scalars()
                .all()
            )
            budget_rows = (
                session.execute(
                    select(BudgetRow)
                    .where(BudgetRow.desk_id == desk_id)
                    .order_by(BudgetRow.id)
                )
                .scalars()
                .all()
            )
            tail_rows = (
                session.execute(
                    select(LedgerRow)
                    .where(LedgerRow.desk_id == desk_id)
                    .order_by(LedgerRow.id.desc())
                    .limit(LEDGER_TAIL_LIMIT)
                )
                .scalars()
                .all()
            )
            return (
                _to_mandate(mandate_row),
                [_to_position(row) for row in position_rows],
                [_to_budget(row) for row in budget_rows],
                [_to_ledger_dict(row) for row in reversed(tail_rows)],
            )

    def update_marks(self, desk_id: str, marks: list[MarkUpdate]) -> None:
        """Persist a batch of reprice results in one transaction."""
        del desk_id  # position ids are globally unique; desk kept for callers
        with database.SessionLocal() as session:
            for mark in marks:
                row = session.get(PositionRow, mark.position_id)
                if row is None:
                    continue  # never crash the fan-out on a stale id
                row.mark_price = mark.mark_price
                row.mark_at = mark.mark_at
                row.mark_stale = mark.mark_stale
                if mark.atlas_offer_id is not None:
                    row.atlas_offer_id = mark.atlas_offer_id
            session.commit()

    def append_ledger(self, desk_id: str, entries: list[LedgerInput]) -> None:
        """Append blotter entries in a single transaction."""
        with database.SessionLocal() as session:
            for entry in entries:
                session.add(
                    LedgerRow(
                        desk_id=desk_id,
                        kind=entry.kind,
                        amount=entry.amount,
                        position_id=entry.position_id,
                        ref=entry.ref,
                        note=entry.note,
                    )
                )
            session.commit()

    def settle(
        self,
        desk_id: str,
        entries: list[LedgerInput],
        spend: Decimal = Decimal("0"),
        contingency_used: Decimal = Decimal("0"),
    ) -> None:
        """The cycle's settle — ONE transaction for the blotter entries AND
        the budget consumption (fix 5): waterfall `spend` onto budget lines'
        `spent` (bounded by each line's remaining allocation) and
        `contingency_used` onto their `contingency`. Persisting these here
        (instead of only decrementing in-memory) means a second cycle re-reads
        the real spent and the "budget is never waived" guard survives a
        restart instead of resetting to zero."""
        with database.SessionLocal() as session:
            for entry in entries:
                session.add(
                    LedgerRow(
                        desk_id=desk_id,
                        kind=entry.kind,
                        amount=entry.amount,
                        position_id=entry.position_id,
                        ref=entry.ref,
                        note=entry.note,
                    )
                )
            if spend > 0 or contingency_used > 0:
                rows = (
                    session.execute(
                        select(BudgetRow)
                        .where(BudgetRow.desk_id == desk_id)
                        .order_by(BudgetRow.id)
                    )
                    .scalars()
                    .all()
                )
                remaining_spend = spend
                for row in rows:
                    if remaining_spend <= 0:
                        break
                    headroom = row.allocated - row.spent
                    applied = min(headroom, remaining_spend)
                    if applied > 0:
                        row.spent = row.spent + applied
                        remaining_spend -= applied
                remaining_contingency = contingency_used
                for row in rows:
                    if remaining_contingency <= 0:
                        break
                    applied = min(row.contingency, remaining_contingency)
                    if applied > 0:
                        row.contingency = row.contingency - applied
                        remaining_contingency -= applied
            session.commit()

    def mark_booked(
        self, position_id: str, order_no: str, ticket_asserted: bool
    ) -> None:
        """Flip a position to booked — only ever with an asserted ticket."""
        with database.SessionLocal() as session:
            row = session.get(PositionRow, position_id)
            if row is None:
                raise KeyError(f"unknown position: {position_id}")
            row.status = "booked"
            row.atlas_order_no = order_no
            row.ticket_asserted = ticket_asserted
            session.commit()

    def desk_state(self, desk_id: str) -> dict:
        """Snapshot for GET /api/desk/{desk_id} (positions/ledger/budgets)."""
        mandate, positions, budgets, ledger_tail = self.reload_desk(desk_id)
        return {
            "desk_id": desk_id,
            "mandate": mandate.model_dump(mode="json"),
            "positions": [p.model_dump(mode="json") for p in positions],
            "budgets": [b.model_dump(mode="json") for b in budgets],
            "ledger": [
                {
                    "id": entry["id"],
                    "ts": str(entry["ts"]),
                    "kind": entry["kind"],
                    "amount": str(entry["amount"]),
                    "position_id": entry["position_id"],
                    "ref": entry["ref"],
                    "note": entry["note"],
                }
                for entry in ledger_tail
            ],
        }
