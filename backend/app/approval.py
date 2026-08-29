"""Pre-trip approval — the G4 checkpoint and its decision (S5).

Backend-side, exactly like `app/travelers.py`: the STORE is the source of
truth, the decision logic lives here (unit-testable without the bot or the
HTTP layer), and both callers — the agent loop at the checkpoint, the
`/approve` route at the decision — go through this module.

The shape (03-program-design.md §"Cycle with approval pin (G4)"):

    first `book` pick on a NORMAL position
        -> set_approved_offer(pos) + identity snapshot + approval token
        -> lifecycle 'released' --CAS--> 'pending_approval'
        -> DeskEvent(pending_approval, itinerary + identity snapshot)
        -> END the cycle (persist-and-resume; NEVER wait inside the cycle,
           the process-wide CYCLE_LOCK is held)

    POST /approve {approve} -> ledger note -> CAS back to 'released'
                            -> _start_cycle -> the resumed cycle runs PINNED
    POST /approve {hold}    -> ledger note -> CAS back to 'released', pin
                               dropped -> the write is skipped; whenever a
                               cycle next runs it judges normally

The approval slot is ONE-SHOT: the `pending_approval -> released` CAS has
exactly one winner, so a replayed approve gets 410 (same semantics as an
escalation slot).

Identity is snapshotted HERE, at approval time, and never re-derived later
(Gate 3 decision 4) — the S6 travel pack reads `store.offer_snapshot`.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from app.codes import hash_code
from app.db.store import DeskStore, LedgerInput
from app.events import DeskEvent, EventSink
from app.models import Position

logger = logging.getLogger(__name__)

# Ledger markers — the durable audit record of the approval round. Scanned
# only as markers (a `like` prefix), never parsed for data.
APPROVAL_REQUESTED_MARKER = "approval_requested:"
APPROVAL_DECIDED_MARKER = "approval_decided:"

# Gate 2 decision 1: exactly ONE re-judgment + one fresh approval request
# when the pinned offer becomes unbookable, then hold and disclose. The
# allowance is per pin lineage (L12): a fresh FIRST-TIME approval round
# (the checkpoint) resets `reapproval_count` via `reset_reapproval=True`,
# while a re-approval request never resets it — so within one pin lineage
# the count stays capped at REAPPROVAL_CAP.
REAPPROVAL_CAP = 1

ApprovalChoice = Literal["approve", "hold"]
ApprovalOutcome = Literal["approved", "held", "gone"]


def build_identity_snapshot(
    pos: Position, offer, price: Decimal
) -> dict:
    """The identity of the offer being signed off, frozen at approval time.

    Honesty register: `carrier` and `cabin` are read defensively because
    `map_offer` does not carry them yet — that mapping is S6 scope. They
    persist as "" today and start carrying real values the moment the
    mapper does, with no change here and no re-derivation at TICKETED.
    """
    segments = []
    for seg in getattr(offer, "segments", None) or []:
        segments.append({
            "dep_airport": seg.dep_airport,
            "arr_airport": seg.arr_airport,
            "dep_time": seg.dep_time.isoformat(),
            "arr_time": seg.arr_time.isoformat(),
            "flight_number": seg.flight_number,
            "carrier": getattr(seg, "carrier", ""),
            "direction": seg.direction,
        })
    return {
        "position_id": pos.id,
        "trip_label": pos.trip_label,
        "origin": pos.origin,
        "dest": pos.dest,
        "depart_date": str(pos.depart_date),
        "pax": pos.pax,
        "offer_id": pos.atlas_offer_id or "",
        "price": str(price),
        "currency": getattr(offer, "currency", "") or "USD",
        "total_minutes": getattr(offer, "total_minutes", 0),
        "carrier": getattr(offer, "carrier", ""),
        "cabin": getattr(offer, "cabin_class", ""),
        "segments": segments,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


async def request_approval(
    store: DeskStore,
    sink: EventSink | None,
    desk_id: str,
    pos: Position,
    offer,
    price: Decimal,
    *,
    reason: str,
    reapproval_count: int = 0,
    reset_reapproval: bool = False,
) -> bool:
    """Stop the desk and ask the manager. Returns True iff THIS call opened
    the approval round (the CAS winner).

    ORDER IS A SAFETY PROPERTY: the lifecycle flip goes FIRST, the pin +
    snapshot + token hash second. A crash between them then leaves a desk
    in 'pending_approval' with nothing pinned — approving it simply
    resumes unpinned and the position is judged (and re-checkpointed)
    from scratch. The reverse order would leave a desk 'released' with a
    pin nobody approved, and the very next cycle would BUY it. Always
    fail towards "ask again", never towards "book it".
    """
    won = await asyncio.to_thread(store.try_request_approval, desk_id)
    if not won:
        # Already pending (or closed): never ask twice in one cycle.
        return False

    token = secrets.token_urlsafe(16)
    token_hash = await asyncio.to_thread(hash_code, token)
    snapshot = build_identity_snapshot(pos, offer, price)
    await asyncio.to_thread(
        store.set_approved_offer,
        desk_id,
        snapshot["offer_id"],
        snapshot,
        token_hash,
        reset_reapproval,
    )

    await asyncio.to_thread(
        store.append_ledger,
        desk_id,
        [LedgerInput(
            kind="adjust",
            amount=Decimal("0"),
            position_id=pos.id,
            note=(
                f"{APPROVAL_REQUESTED_MARKER} pre-trip approval requested "
                f"at {price} — {reason}; nothing books until the manager "
                f"approves (reapproval_count={reapproval_count})"
            ),
        )],
    )

    if sink is not None:
        sink.publish(DeskEvent(
            type="pending_approval",
            desk_id=desk_id,
            payload={
                # The manager-only approval credential for THIS round. It
                # travels to the manager's chat and nowhere else; a
                # traveler session never receives it.
                "approval_token": token,
                "reason": reason,
                "reapproval_count": reapproval_count,
                "itinerary": snapshot,
                "price": str(price),
                "currency": snapshot["currency"],
                # No traveler PII here — identity means FLIGHT identity.
                "manager_chat_id": None,
            },
        ))
    logger.info(
        "pending_approval opened for desk %s position %s at %s",
        desk_id, pos.id, price,
    )
    return True


async def apply_decision(
    store: DeskStore, desk_id: str, choice: ApprovalChoice
) -> ApprovalOutcome:
    """Record the manager's Approve/Hold and release the lifecycle.

    Returns 'approved' | 'held' | 'gone'. 'gone' means the CAS lost — the
    slot was already decided (or the desk was never pending), which the
    route answers with 410.

    HOLD semantics (Gate 3 amendment): the write is skipped and the pin is
    dropped, so the position is judged normally whenever a cycle next runs.
    Hold does NOT start a cycle — "skip the write this cycle" is the whole
    instruction, and re-firing here would just re-ask for approval.

    ATOMICITY (M-H2): the hold is ONE store statement —
    `try_hold_approval` flips the lifecycle AND drops the pin/snapshot/
    token hash in the same UPDATE. There is no longer a crash window
    between "decided" and "unpinned", and a losing hold can never wipe a
    winning approve's pin/snapshot: the UPDATE only matches a row that is
    still 'pending_approval'.
    """
    if choice == "hold":
        decided = await asyncio.to_thread(store.try_hold_approval, desk_id)
    else:
        decided = await asyncio.to_thread(store.try_decide_approval, desk_id)
    if not decided:
        return "gone"

    note = (
        f"{APPROVAL_DECIDED_MARKER} manager chose '{choice}' — "
        + (
            "offer pinned; the resumed cycle executes it without "
            "re-judgment (wall invariants still apply)"
            if choice == "approve"
            else "pin dropped; the write is skipped and the position is "
                 "judged normally on the next cycle"
        )
    )
    await asyncio.to_thread(
        store.append_ledger,
        desk_id,
        [LedgerInput(kind="adjust", amount=Decimal("0"), note=note)],
    )
    return "approved" if choice == "approve" else "held"
