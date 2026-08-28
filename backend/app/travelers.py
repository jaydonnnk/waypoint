"""Backend-side travelers_complete firing (03-program-design.md §2).

The STORE is the source of truth for traveler counts — the bot is a thin
I/O adapter. This module owns the "did the Nth traveler just complete the
roster?" decision so the lifecycle test never imports bot internals, and
so bot-side counting (which drifts on reject/dedupe/resubmit) is avoided.

Dedupe is DB-backed (a ledger marker note), so it is RESTART-SAFE — an
in-memory set would double-fire after a restart. A process-level asyncio
lock serializes the check-and-fire so two concurrent confirms in the
single worker cannot both fire (see the ADR 0007 single-worker note).
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from app.db.store import DeskStore, LedgerInput
from app.events import DeskEvent, EventSink

logger = logging.getLogger(__name__)

# The ledger marker that records travelers_complete already fired for a
# desk. Scanned for dedupe (restart-safe) — never parsed for data.
TRAVELERS_COMPLETE_MARKER = "travelers_complete:"

# Serialize check-and-fire within the process (single worker, ADR 0007).
_fire_lock = asyncio.Lock()


async def maybe_fire_travelers_complete(
    store: DeskStore,
    sink: EventSink,
    desk_id: str,
    manager_chat_id: str | None = None,
) -> bool:
    """Fire travelers_complete ONCE iff verified_count == team_size AND
    lifecycle == awaiting_travelers AND it has not already fired.

    Returns True iff it fired this call. Dedupe is a ledger marker note
    (restart-safe); the process lock serializes the check-and-fire.

    manager_chat_id: for S3 the manager-identity seam is whoever seeded
    the desk (future: explicit manager binding). If None, the notify
    handler logs and skips per S2 — omission is acceptable this slice.
    """
    async with _fire_lock:
        try:
            count = await asyncio.to_thread(store.verified_count, desk_id)
            team_size = await asyncio.to_thread(store.get_team_size, desk_id)
            lifecycle = await asyncio.to_thread(store.get_lifecycle, desk_id)
        except Exception:  # noqa: BLE001 — never break the caller
            logger.exception("travelers_complete check failed for %s", desk_id)
            return False

        if not (count >= team_size and lifecycle == "awaiting_travelers"):
            return False

        try:
            already = await asyncio.to_thread(
                store.has_ledger_marker, desk_id, TRAVELERS_COMPLETE_MARKER
            )
        except Exception:  # noqa: BLE001
            logger.exception("travelers_complete dedupe read failed for %s", desk_id)
            return False
        if already:
            return False

        # Ledger note (the dedupe marker AND the audit record) — written
        # BEFORE publish so a publish-time crash can't un-dedupe.
        try:
            await asyncio.to_thread(
                store.append_ledger,
                desk_id,
                [LedgerInput(
                    kind="adjust",
                    amount=Decimal("0"),
                    note=(
                        f"{TRAVELERS_COMPLETE_MARKER} all {team_size} "
                        "travelers verified — ready for release"
                    ),
                )],
            )
        except Exception:  # noqa: BLE001
            logger.exception("travelers_complete ledger note failed for %s", desk_id)
            return False

        sink.publish(DeskEvent(
            type="travelers_complete",
            desk_id=desk_id,
            payload={
                "manager_chat_id": manager_chat_id,
                "verified_count": team_size,
            },
        ))
        logger.info(
            "travelers_complete fired for desk %s (%d/%d)",
            desk_id, count, team_size,
        )
        return True
