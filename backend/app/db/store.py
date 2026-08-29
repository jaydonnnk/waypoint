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

from sqlalchemy import func, select, update

from app.db import database
from app.db.schema import (
    BudgetRow,
    ChatBindingRow,
    LedgerRow,
    MandateRow,
    PositionRow,
    TravelerRow,
)
from app.models import Budget, Mandate, Position

# TYPE_CHECKING-only import to avoid circular ref.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bot.mrz import MrzFields

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
        lifecycle: str = "released",
        invite_token: str | None = None,
        code_hash: str | None = None,
        policy_json: str | None = None,
    ) -> str:
        """Persist the seeded portfolio. Returns the desk_id (= mandate.id).

        Honesty: one `adjust` ledger entry discloses that the cost bases are
        demo seeds, so the blotter itself carries the provenance note.

        The gate args default to today's behavior EXACTLY: `lifecycle`
        'released' (matching the schema default), no invite token, no code
        hash. An ungated seed (no gate args) is byte-identical to the
        pre-S1 write. A gated seed (Waybot) passes lifecycle
        'awaiting_travelers' + token + code_hash so the cycle is held for
        the confirm step.
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
                    lifecycle=lifecycle,
                    invite_token=invite_token,
                    confirmation_code_hash=code_hash,
                    policy_json=policy_json,
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

    def set_lifecycle(self, desk_id: str, lifecycle: str) -> None:
        """Flip the desk lifecycle state. Raises KeyError for an unknown desk."""
        with database.SessionLocal() as session:
            row = session.get(MandateRow, desk_id)
            if row is None:
                raise KeyError(f"unknown desk: {desk_id}")
            row.lifecycle = lifecycle
            session.commit()

    def try_release(self, desk_id: str) -> bool:
        """Atomic compare-and-set: flip 'awaiting_travelers' -> 'released' in
        ONE UPDATE and report whether THIS caller won. Returns True exactly
        once per gated desk; a concurrent second correct-code confirm gets
        rowcount 0 -> False, so only one caller ever reaches _start_cycle
        (closes the check-then-act double-start race). An unknown or
        already-released desk returns False (no exception — the caller has
        already 404'd/409'd on the prior lifecycle read)."""
        with database.SessionLocal() as session:
            result = session.execute(
                update(MandateRow)
                .where(
                    MandateRow.id == desk_id,
                    MandateRow.lifecycle == "awaiting_travelers",
                )
                .values(lifecycle="released")
            )
            session.commit()
            return result.rowcount == 1

    def get_lifecycle(self, desk_id: str) -> str:
        """Read the desk lifecycle state. Raises KeyError for an unknown desk."""
        with database.SessionLocal() as session:
            row = session.get(MandateRow, desk_id)
            if row is None:
                raise KeyError(f"unknown desk: {desk_id}")
            return row.lifecycle

    def get_invite(self, desk_id: str) -> tuple[str | None, str | None]:
        """(invite_token, confirmation_code_hash) for a desk. Raises KeyError
        for an unknown desk. The hash is the gate the confirm route checks;
        the plaintext code is never stored, so it cannot be read back."""
        with database.SessionLocal() as session:
            row = session.get(MandateRow, desk_id)
            if row is None:
                raise KeyError(f"unknown desk: {desk_id}")
            return row.invite_token, row.confirmation_code_hash

    def verified_count(self, desk_id: str) -> int:
        """How many travelers have been captured on this desk (S3 populates
        the rows; S1 always reads 0)."""
        with database.SessionLocal() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(TravelerRow)
                    .where(TravelerRow.desk_id == desk_id)
                ).scalar_one()
            )

    def bind_chat(
        self, chat_id: str, token: str
    ) -> tuple[str, int] | None:
        """Resolve an invite token→desk and upsert a chat_bindings row.

        Returns (desk_id, slot) on success; None if:
        - token is unknown
        - desk lifecycle is not 'awaiting_travelers' (already released/closed)
        - desk has reached team_size bindings (full)

        Slot assignment: if the chat already has a binding for this desk,
        reuse the same slot (idempotent re-bind); otherwise assign the
        next free slot (max existing slot + 1, or 1 if none).
        """
        with database.SessionLocal() as session:
            # Look up the desk by invite_token.
            mandate = session.execute(
                select(MandateRow).where(MandateRow.invite_token == token)
            ).scalar_one_or_none()
            if mandate is None:
                return None
            desk_id = mandate.id

            # Gate: only accept bindings while the desk is awaiting travelers.
            if mandate.lifecycle != "awaiting_travelers":
                return None

            # Check for an existing binding (re-bind = same slot).
            existing = session.get(ChatBindingRow, chat_id)
            if existing is not None and existing.desk_id == desk_id:
                return (desk_id, existing.slot)

            # Gate: team_size cap — don't assign more slots than the desk allows.
            bound_count = session.execute(
                select(func.count())
                .select_from(ChatBindingRow)
                .where(ChatBindingRow.desk_id == desk_id)
            ).scalar_one()
            if bound_count >= mandate.team_size:
                return None

            # Assign next free slot for this desk.
            max_slot = session.execute(
                select(func.max(ChatBindingRow.slot)).where(
                    ChatBindingRow.desk_id == desk_id
                )
            ).scalar()
            slot = (max_slot or 0) + 1

            # Upsert: if the chat was bound to a DIFFERENT desk, replace.
            if existing is not None:
                existing.desk_id = desk_id
                existing.slot = slot
            else:
                session.add(
                    ChatBindingRow(
                        telegram_chat_id=chat_id,
                        desk_id=desk_id,
                        slot=slot,
                    )
                )
            session.commit()
            return (desk_id, slot)

    def add_traveler(
        self,
        desk_id: str,
        slot: int,
        fields: "MrzFields",
        email: str | None = None,
        mobile: str | None = None,
    ) -> str:
        """Insert (or upsert by desk_id+slot) a verified traveler.

        Returns the traveler row id. Duplicate doc_number on the same desk
        is rejected (raises ValueError).
        """
        import uuid

        with database.SessionLocal() as session:
            # Reject duplicate doc_number on the same desk.
            existing_doc = (
                session.execute(
                    select(TravelerRow).where(
                        TravelerRow.desk_id == desk_id,
                        TravelerRow.doc_number == fields.doc_number,
                    )
                )
                .scalars()
                .first()
            )
            if existing_doc is not None and existing_doc.slot != slot:
                raise ValueError(
                    f"duplicate doc_number {fields.doc_number} on desk {desk_id}"
                )

            # Upsert by desk_id+slot: replace if same slot re-submits.
            existing_slot = (
                session.execute(
                    select(TravelerRow).where(
                        TravelerRow.desk_id == desk_id,
                        TravelerRow.slot == slot,
                    )
                )
                .scalars()
                .first()
            )
            if existing_slot is not None:
                existing_slot.family_name = fields.family_name
                existing_slot.given_name = fields.given_name
                existing_slot.gender = fields.gender
                existing_slot.birthday = fields.birthday
                existing_slot.nationality = fields.nationality_iso2
                existing_slot.doc_number = fields.doc_number
                existing_slot.issuing_country = fields.issuing_country
                existing_slot.doc_expiry = fields.doc_expiry
                # Keep an already-captured contact if this resubmit omits it
                # (a photo redo shouldn't wipe a previously typed email/mobile).
                if email is not None:
                    existing_slot.contact_email = email
                if mobile is not None:
                    existing_slot.contact_mobile = mobile
                existing_slot.verified_at = datetime.now()
                session.commit()
                return existing_slot.id

            traveler_id = str(uuid.uuid4())
            session.add(
                TravelerRow(
                    id=traveler_id,
                    desk_id=desk_id,
                    slot=slot,
                    family_name=fields.family_name,
                    given_name=fields.given_name,
                    gender=fields.gender,
                    birthday=fields.birthday,
                    nationality=fields.nationality_iso2,
                    doc_number=fields.doc_number,
                    issuing_country=fields.issuing_country,
                    doc_expiry=fields.doc_expiry,
                    contact_email=email,
                    contact_mobile=mobile,
                )
            )
            session.commit()
            return traveler_id

    def list_travelers(self, desk_id: str) -> list[dict]:
        """All verified travelers on a desk, ordered by slot."""
        with database.SessionLocal() as session:
            rows = (
                session.execute(
                    select(TravelerRow)
                    .where(TravelerRow.desk_id == desk_id)
                    .order_by(TravelerRow.slot)
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": r.id,
                    "slot": r.slot,
                    "family_name": r.family_name,
                    "given_name": r.given_name,
                    "gender": r.gender,
                    "birthday": r.birthday,
                    "nationality": r.nationality,
                    "doc_type": r.doc_type,
                    "doc_number": r.doc_number,
                    "issuing_country": r.issuing_country,
                    "doc_expiry": r.doc_expiry,
                    "contact_email": r.contact_email,
                    "contact_mobile": r.contact_mobile,
                }
                for r in rows
            ]

    def purge_travelers(self, desk_id: str) -> None:
        """Delete all travelers on a desk (desk close)."""
        with database.SessionLocal() as session:
            session.execute(
                TravelerRow.__table__.delete().where(
                    TravelerRow.desk_id == desk_id
                )
            )
            session.commit()

    def get_team_size(self, desk_id: str) -> int:
        """Read the mandate's team_size. Raises KeyError for unknown desk."""
        with database.SessionLocal() as session:
            row = session.get(MandateRow, desk_id)
            if row is None:
                raise KeyError(f"unknown desk: {desk_id}")
            return row.team_size

    def get_code_attempts(self, desk_id: str) -> int:
        """Read the mandate's current code_attempts WITHOUT writing. Raises
        KeyError for an unknown desk. Used by the /confirm route's cap check
        (H-new1): when the counter is already at/over the cap, a wrong code
        answers 429 with no bump UPDATE — the flood stops writing."""
        with database.SessionLocal() as session:
            row = session.get(MandateRow, desk_id)
            if row is None:
                raise KeyError(f"unknown desk: {desk_id}")
            return int(row.code_attempts)

    def bump_code_attempts(self, desk_id: str) -> int:
        """Atomically increment and return the new code_attempts count.

        Used by the /confirm route to enforce the attempt cap (5 wrong codes
        → 429). Raises KeyError for an unknown desk.

        The increment is a single UPDATE (``code_attempts = code_attempts +
        1``) so SQLite serializes concurrent bumps — never a Python-side
        read-modify-write that two racing sessions could lose. The read-back
        SELECT runs in the SAME transaction, so it observes this bump's value.
        """
        with database.SessionLocal() as session:
            result = session.execute(
                update(MandateRow)
                .where(MandateRow.id == desk_id)
                .values(code_attempts=MandateRow.code_attempts + 1)
            )
            if result.rowcount != 1:
                raise KeyError(f"unknown desk: {desk_id}")
            new_count = session.execute(
                select(MandateRow.code_attempts).where(
                    MandateRow.id == desk_id
                )
            ).scalar_one()
            session.commit()
            return int(new_count)

    def has_ledger_marker(self, desk_id: str, marker: str) -> bool:
        """True if any ledger note on this desk starts with `marker`.

        Restart-safe dedupe for one-shot desk events (e.g.
        travelers_complete): the marker lives in the durable blotter, so a
        process restart never re-fires the event.
        """
        with database.SessionLocal() as session:
            row = (
                session.execute(
                    select(LedgerRow)
                    .where(
                        LedgerRow.desk_id == desk_id,
                        LedgerRow.note.like(f"{marker}%"),
                    )
                    .limit(1)
                )
                .scalars()
                .first()
            )
            return row is not None

    def desk_state(self, desk_id: str) -> dict:
        """Snapshot for GET /api/desk/{desk_id} (positions/ledger/budgets)."""
        mandate, positions, budgets, ledger_tail = self.reload_desk(desk_id)
        lifecycle = self.get_lifecycle(desk_id)
        return {
            "desk_id": desk_id,
            "lifecycle": lifecycle,
            "verified_count": self.verified_count(desk_id),
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
