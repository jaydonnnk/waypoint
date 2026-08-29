"""DeskAgent — the desk orchestration loop (S3 body).

One cycle: re-read the world (GUARD #2) → emit meta (mandate + 20/20
search meter + mode label + disclosures) → meter-gated reprice fan-out →
DeskBrain judgment (advise gate) → EXECUTE WALL (deterministic code
re-checks every pick AFTER the LLM, fail-closed) → write path (LIVE mode
only; comparison mode logs decisions and skips every write command) →
settle the ledger → terminal result.

Two gates (ADR 0003/0004): the brain recommends; this loop owns ledger
math, authority-cap checks, budget checks and order/pay execution. The
stable S1 shape is kept: `run(desk_id, emit) -> DeskResult`, the step
budget + graceful give-up, METER_MAX = 20, `_finish`, and the
comparison-mode label.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from app.agent.brain import DeskBrain
from app.approval import REAPPROVAL_CAP, request_approval
from app.atlas.client import AtlasError, AtlasQueryOnly, AtlasUnknownOrder
from app.db.store import ApprovalState, DeskStore, LedgerInput, MarkUpdate
from app.events import DeskEvent, EventSink
from app.fixture import VOLATILITY_PRIORS
from app.models import DeskAction, DeskResult, Position
from app.provenance import build_rails

logger = logging.getLogger(__name__)

# emit takes a JSON-serializable dict event and returns an awaitable.
Emit = Callable[[dict], Awaitable[None]]

# Search-budget meter: 20 searches per cycle (02-architecture.md §Flow).
METER_MAX = 20

# Human-controlled live-booking switch (Slice 4 Step 1). Armed ONLY when
# the env var reads exactly "1"; default (unset/anything else) is OFF, so
# ordinary dev/demo runs ALWAYS stay in comparison mode — regardless of
# what the flapping ticketing probe reports.
LIVE_BOOKING_ENV = "WAYPOINT_LIVE_BOOKING"

# Comparison-mode labels — the wire says WHICH of the two gates blocks:
# the human switch (not armed) or the ticketing probe (unavailable).
COMPARISON_NOT_ARMED_LABEL = (
    "comparison mode \u2014 live booking not armed"
)
COMPARISON_TICKETING_LABEL = (
    "comparison mode \u2014 ticketing unavailable"
)

# Bounded fan-out concurrency (small semaphore; meter is the hard stop).
FANOUT_CONCURRENCY = 4

# Bounded wait for the ONE human click on an escalation.
DEFAULT_ESCALATION_WAIT = 300.0

# G4 (S5): the write-path failures that mean the APPROVED offer can no
# longer be bought at all — as opposed to merely costing more (which the
# budget/cap/contingency invariants already own). Only these buy the
# position its one re-judgment + fresh approval round. BOOKING_EXPIRED is
# treated identically to OFFER_EXPIRED by the Atlas error-handling
# reference (L11).
UNBOOKABLE_CODES = frozenset({"OFFER_EXPIRED", "BOOKING_EXPIRED"})

# Sentinel: a write NOT covered by an approval pin. The pinned-write
# contingency invariant in `_write_position` keys on identity with this
# object, so a MISSING approved price (None) on a pinned write stays
# distinguishable from "not pinned at all" and can fail closed (H1b).
_NOT_PINNED: object = object()

# The escalation slot seam: (desk_id, esc_id) -> {"event": asyncio.Event,
# "choice": None} registered on the desk state, or None when no human is
# reachable (fail closed — nothing executes on a guess).
EscalationSlot = Callable[[str, str], "dict | None"]
# Slot hygiene: (desk_id, esc_id) -> None removes the slot once the wait
# timed out or the click landed (a late POST then gets a 410, never a
# misleading 200).
EscalationClear = Callable[[str, str], None]
# Meter mirror for GET /api/desk/{id} snapshots: (desk_id, used) -> None.
MeterReport = Callable[[str, int], None]

# One-time sandbox passenger manifest builder for the write path (demo
# data; real traveler docs never live in the repo). The payload shape is
# disclosed as demo per passenger-input.md, and traveler_id /
# passenger_type are CARRIED from the verify response — a static
# constant can never hold a traveler_id (it only exists after verify),
# so the payload is built at write time, per call.
def _build_demo_pax_json(verified_travelers: list[dict]) -> str:
    """One-time order-create stdin payload built from the verify-returned
    traveler IDs (carry, never invent — passenger-input.md). Same shape as
    the live-proven `_build_pax_json` in tests/test_atlas_write_path.py:
    one `name` field "FAMILY/GIVEN", `passenger_type`, `gender` "M"/"F",
    `birthday`, nested `document`, plus a top-level `contact` block.
    Each passenger gets its OWN demo identity (given name + document
    number vary by index) — two travelers sharing one identity/doc
    number are rejected upstream as PASSENGER_INFO_INVALID (found live
    on SIN->NRT with 2 adults, 2026-08-25). Sandbox demo identities
    only; nothing here is printed or logged."""
    travelers = verified_travelers or [
        {"traveler_id": "", "passenger_type": "adult"}
    ]
    passengers = [
        {
            "traveler_id": t.get("traveler_id", ""),
            # Per-index suffix keeps every demo identity distinct.
            "name": f"DEMO/WAYPOINT{chr(ord('A') + i)}",
            "passenger_type": t.get("passenger_type", "adult"),
            "gender": "M",
            "birthday": "1990-01-01",
            "nationality": "SG",
            "document": {
                "type": "PP",
                "number": f"DEMO00000{i + 1}",
                "issuing_country": "SG",
                "expires": "2030-01-01",
            },
        }
        for i, t in enumerate(travelers)
    ]
    return json.dumps({
        "passengers": passengers,
        "contact": {
            "name": "DEMO/WAYPOINT",
            "email": "demo@waypoint.test",
            "mobile": "0065-90000001",
        },
    })


class DeskAgent:
    def __init__(
        self,
        step_budget: int = 12,
        atlas=None,
        brain: DeskBrain | None = None,
        store: DeskStore | None = None,
        escalation_slot: EscalationSlot | None = None,
        escalation_clear: EscalationClear | None = None,
        meter_report: MeterReport | None = None,
        escalation_wait: float = DEFAULT_ESCALATION_WAIT,
        pace: float = 0.5,
        sink: EventSink | None = None,
    ):
        # Guard: bounded loop. Exceeding the budget gives up gracefully.
        self.step_budget = step_budget
        # Injectable for tests; defaults hit the real sandbox / real DB.
        self.atlas = atlas or self._default_atlas()
        self.brain = brain or DeskBrain()
        self.store = store or DeskStore()
        # The one human click seam (routes.py registers the asyncio.Event
        # on the DeskState escalations hook); None in bare tests.
        self.escalation_slot = escalation_slot
        # Removes a used/expired escalation slot (slot hygiene); None-safe.
        self.escalation_clear = escalation_clear
        self.meter_report = meter_report
        self.escalation_wait = escalation_wait
        # Paced step emit so the stream reads live (0 in tests).
        self.pace = pace
        # The typed domain-event sink (G4/S5): pending_approval and the
        # pinned_resume provenance moment are announced here so the Waybot
        # can reach the manager. None in bare tests — publishing is
        # always optional, never load-bearing for the cycle.
        self.sink = sink

    @staticmethod
    def _default_atlas():
        from app.atlas.client import AtlasClient  # local: keep DI light

        return AtlasClient()

    async def run(self, desk_id: str, emit: Emit) -> DeskResult:
        step = 0

        async def emit_step(text: str) -> None:
            nonlocal step
            step += 1
            if self.pace > 0:
                await asyncio.sleep(self.pace)  # paced so the stream reads live
            await emit({"type": "step", "n": step, "text": text})

        # --- GUARD #2: re-read the world before anything else (never act
        # --- on cached state).
        try:
            mandate, positions, budgets, _ledger_tail = await asyncio.to_thread(
                self.store.reload_desk, desk_id
            )
        except Exception:  # noqa: BLE001 — normalized code only, no raw message
            await emit({"type": "error", "code": "DESK_STATE_INVALID"})
            return await self._finish(desk_id, emit, DeskResult(
                desk_id=desk_id, status="failed", pnl=Decimal("0"),
                losses_admitted=0, step_count=step,
            ))

        # Provenance reads only THIS cycle's judgment; absent → fail-to-least-live fallback label.
        self.brain.last_source = None

        # Comparison mode = decisions logged + marked, NO write commands.
        # Fail-closed: any doubt about ticketing keeps the desk read-only.
        # Per-cycle cache reset (fix 7): a mid-run ticketing activation
        # takes effect next cycle, and the probe runs at most once here.
        reset_probe = getattr(self.atlas, "reset_ticketing_cache", None)
        if reset_probe is not None:
            reset_probe()
        # The human switch is read ONCE; BOTH the comparison decision and
        # the blocking-gate label derive from that single read (no
        # label-only TOCTOU between the gate and the wire label).
        armed = self._live_booking_armed()
        comparison = await self._comparison_mode(armed)
        if comparison:
            # Honest about WHICH of the two gates blocks the write path.
            mode = (COMPARISON_NOT_ARMED_LABEL
                    if not armed
                    else COMPARISON_TICKETING_LABEL)
            gate_disclosure = f"{mode} \u2014 no write commands run"
        else:
            mode = "live ticketing"
            gate_disclosure = (
                "live booking armed AND ticketing live \u2014 write "
                "commands enabled"
            )
        # Recorded mode (S9, ADR 0005): a replay client NEVER wears the
        # live label — getattr probe mirrors the reset_ticketing_cache
        # precedent above; the client supplies its own gate disclosure.
        recorded = getattr(self.atlas, "mode_label", None) == "recorded"
        if not comparison and recorded:
            mode = "recorded ticketing (replay)"
            gate_disclosure = getattr(self.atlas, "gate_disclosure", gate_disclosure)

        # --- meta: the mandate card + full search meter + mode label.
        await emit({
            "type": "meta",
            "desk_id": desk_id,
            "mandate": mandate.model_dump(mode="json"),
            "meter": {"used": 0, "max": METER_MAX},
            "mode": mode,
            "disclosures": [
                "sandbox money only",
                "cost bases seeded",
                "volatility priors curated \u2014 no ML",
                gate_disclosure,
            ],
            # Per-rail provenance (S12, ADR 0006): ADDITIVE field — `mode`
            # and `disclosures` above stay byte-identical, and old replays
            # without it render nothing (frontend reducer guard). Pure
            # builder; missing inputs fail to the least-live label.
            "rails": build_rails(
                atlas=self.atlas,
                brain=self.brain,
                comparison=comparison,
                live_ticketing=(not comparison and not recorded),
            ),
        })

        await emit_step(
            f"Re-read the world \u2014 {len(positions)} positions, "
            f"{len(budgets)} budget lines and the ledger loaded fresh "
            "from the DB"
        )

        # --- deterministic desk math (CODE owns this, never the LLM).
        # Computed BEFORE the first give-up gate: pure math on the
        # just-loaded data (no emits, no Atlas calls), so every give-up
        # path — including this one — sees the REAL remainders instead
        # of an inert zero sentinel.
        spent = sum((b.spent for b in budgets), Decimal("0"))
        budget_left = mandate.budget_total - spent
        contingency_left = sum((b.contingency for b in budgets), Decimal("0"))

        # Capture the starting remainders RIGHT HERE, before ANY give-up
        # gate, so every give-up / settle path persists exactly what the
        # write path consumed (fix 5): spent = budget delta, contingency
        # delta = absorbed PRICE_CHANGED amounts. Nothing between the
        # reload above and the write path mutates either remainder.
        budget_start = budget_left
        contingency_start = contingency_left

        # Give-up path (kept from the tracer): if the step budget is
        # exhausted, close honestly instead of pretending work happened.
        if step >= self.step_budget:
            return await self._give_up(
                desk_id, emit, step,
                status="failed",
                text="Step budget exhausted \u2014 giving up gracefully",
                positions=positions, losses_admitted=0,
                comparison_mode=comparison, settle=[],
                budget_start=budget_start, budget_left=budget_left,
                contingency_start=contingency_start,
                contingency_left=contingency_left,
            )

        # --- G4 (S5): read the pin ONCE per cycle, in one transaction.
        # DESK KIND decides, exactly like the pax builder: only a GATED
        # desk (invite_token set) ever stops for pre-trip approval, so
        # ungated/legacy/recorded desks keep today's behavior byte-for-byte
        # and this gate makes zero Atlas calls. A read failure FAILS
        # CLOSED on a gated desk (L13): silently continuing would run the
        # desk ungated past its own gate, so the cycle ends with a code.
        # Ungated desks keep the approval=None degradation.
        try:
            approval: ApprovalState | None = await asyncio.to_thread(
                self.store.get_approval, desk_id
            )
        except Exception:  # noqa: BLE001 — probe the desk kind, then decide
            # L13 observability: the fail-closed posture below is the
            # behavior; this is the trace of WHY the desk stopped.
            logger.exception("approval state unreadable for desk %s", desk_id)
            approval = None
            gated_probe = False
            try:
                invite_token, _code_hash = await asyncio.to_thread(
                    self.store.get_invite, desk_id
                )
                gated_probe = bool(invite_token)
            except Exception:  # noqa: BLE001 — cannot even read the kind
                logger.warning(
                    "invite probe failed for desk %s \u2014 kind unknown, "
                    "posture decided assuming ungated", desk_id,
                )
                gated_probe = False
            if gated_probe:
                await emit({"type": "error", "code": "DESK_STATE_INVALID"})
                return await self._give_up(
                    desk_id, emit, step,
                    status="failed",
                    text="Approval state unreadable on a gated desk "
                         "\u2014 failing closed instead of running "
                         "past its own gate",
                    positions=positions, losses_admitted=0,
                    comparison_mode=comparison, settle=[],
                    budget_start=budget_start, budget_left=budget_left,
                    contingency_start=contingency_start,
                    contingency_left=contingency_left,
                )

        held = [p for p in positions if p.status == "held"]
        meter_used, offers_by_pos = await self._reprice_fan_out(
            desk_id, emit, held
        )
        await emit_step(
            f"Repriced {len(held)} positions \u2014 meter at "
            f"{meter_used}/{METER_MAX}; stale marks carry disclosed "
            "uncertainty"
        )
        if step >= self.step_budget:
            return await self._give_up(
                desk_id, emit, step,
                status="failed",
                text="Step budget exhausted \u2014 giving up gracefully",
                positions=positions, losses_admitted=0,
                comparison_mode=comparison, settle=[],
                budget_start=budget_start, budget_left=budget_left,
                contingency_start=contingency_start,
                contingency_left=contingency_left,
            )

        # --- admitted losses: the brain supplies the note; the LOOP logs
        # --- the loss via the ledger (kind="loss").
        settle: list[LedgerInput] = []
        losses_admitted = 0
        for pos in held:
            found = self.brain.admitted_loss(pos, VOLATILITY_PRIORS)
            if found is None:
                continue
            amount, note = found
            losses_admitted += 1
            settle.append(LedgerInput(
                kind="loss", amount=amount, position_id=pos.id, note=note,
            ))
            await emit({
                "type": "loss",
                "position_id": pos.id,
                "amount": str(amount),
                "note": note,
                "disclosure": "injected demo scenario \u2014 labeled",
            })

        # --- MARK CONSTRUCTION (G4, Gate 3 decision 1). THE pin branch
        # --- lives here and ONLY here: an approved position becomes a
        # --- PINNED mark, every other position is judged normally, and the
        # --- single execute wall below runs over both together. There is
        # --- no second wall and no scattered `if pinned` past this point.
        pinned_pos: Position | None = None
        pinned_pos_id = approval.pinned_position_id if approval else None
        if pinned_pos_id is not None:
            pinned_pos = next(
                (p for p in held if p.id == pinned_pos_id), None
            )

        pinned_actions: list[DeskAction] = []
        pinned_approved_price: Decimal | None = None
        if pinned_pos is not None and approval is not None:
            pinned_mark, pinned_approved_price = self._pinned_mark(
                approval, pinned_pos, budget_left, contingency_left,
                mandate.authority_cap,
                fresh_offer=offers_by_pos.get(pinned_pos.id),
            )
            pinned_actions = [pinned_mark]
            # The fan-out above just persisted whatever offer the fresh
            # search returned. Put the APPROVED one back on the row so the
            # blotter records the offer this desk is actually pinned to —
            # otherwise the persisted evidence would name an offer the
            # write path never touches. No Atlas call, pinned desks only.
            await asyncio.to_thread(
                self.store.update_marks, desk_id,
                [MarkUpdate(
                    position_id=pinned_pos.id,
                    mark_price=pinned_pos.mark_price,
                    mark_at=datetime.now(timezone.utc),
                    mark_stale=pinned_pos.mark_stale,
                    atlas_offer_id=pinned_pos.atlas_offer_id,
                )],
            )
            await self._announce_pinned_resume(
                desk_id, emit, approval, pinned_pos, pinned_actions[0]
            )

        # --- JUDGE: advise gate. ONE batched call per cycle; the brain
        # --- never raises (degrades to the deterministic fallback). The
        # --- pinned position is NOT judged — that is what "pinned" means,
        # --- and it is why a resumed cycle makes zero judgment calls on a
        # --- single-position desk.
        to_judge = [
            p for p in held
            if pinned_pos is None or p.id != pinned_pos.id
        ]
        judged = await self.brain.judge(
            to_judge, VOLATILITY_PRIORS, METER_MAX - meter_used,
            budget_left, contingency_left,
        ) if to_judge else []
        actions = pinned_actions + list(judged)
        for action in actions:
            await emit({
                "type": "trade",
                "position_id": action.position_id,
                "kind": action.kind,
                "rationale": action.rationale,
            })
        await emit_step(
            f"Judgment in \u2014 {len(actions)} picks; the execute wall "
            "re-checks every one in code"
        )
        if step >= self.step_budget:
            return await self._give_up(
                desk_id, emit, step,
                status="failed",
                text="Step budget exhausted \u2014 giving up gracefully",
                positions=positions, losses_admitted=losses_admitted,
                comparison_mode=comparison, settle=settle,
                budget_start=budget_start, budget_left=budget_left,
                contingency_start=contingency_start,
                contingency_left=contingency_left,
            )

        # --- EXECUTE WALL + write path, strictly sequential per position.
        # (budget_start / contingency_start were captured right after the
        # desk math so every give-up / settle path sees the same base.)
        status = "closed"
        pos_by_id = {p.id: p for p in positions}
        for action in actions:
            pos = pos_by_id.get(action.position_id)
            if pos is None or pos.status != "held":
                continue  # never act on a vanished/booked position
            if action.kind == "hold":
                if comparison:
                    settle.append(LedgerInput(
                        kind="trade", amount=Decimal("0"),
                        position_id=pos.id,
                        note="comparison mode \u2014 decision 'hold' "
                             "logged, not executed",
                    ))
                continue

            # --- G4 APPROVAL CHECKPOINT (S5). The FIRST book pick on a
            # NORMAL (unapproved) position of a GATED desk stops the desk:
            # pin the offer, snapshot its identity, flip to
            # 'pending_approval', announce it, and END the cycle. NEVER a
            # wait inside the cycle — the process-wide CYCLE_LOCK is held,
            # so the human beat is persist-and-resume (POST /approve →
            # _start_cycle → the pinned branch above).
            # Two conditions keep the blast radius exactly where it belongs:
            #   - GATED desk only (ungated/legacy/recorded are untouched);
            #   - LIVE write path only — in comparison mode nothing books,
            #     so there is nothing to approve and the logged-decision
            #     path below stays byte-identical.
            if (
                action.kind == "book"
                and approval is not None
                and approval.gated
                and not comparison
                and (pinned_pos is None or pos.id != pinned_pos.id)
            ):
                opened = await request_approval(
                    self.store, self.sink, desk_id, pos,
                    offers_by_pos.get(pos.id), pos.mark_price,
                    reason=(
                        "first book pick on this position \u2014 pre-trip "
                        "approval required"
                    ),
                    reapproval_count=approval.reapproval_count,
                    # L12: a fresh FIRST-TIME approval round starts a
                    # fresh re-approval allowance.
                    reset_reapproval=True,
                )
                if opened:
                    await emit_step(
                        f"Pre-trip approval requested for {pos.id} at "
                        f"{pos.mark_price} \u2014 the desk stops here; nothing "
                        "books until the manager approves"
                    )
                    give_up_text = (
                        "Cycle ended awaiting pre-trip approval \u2014 the "
                        "desk resumes on the approved offer, pinned, when "
                        "the manager clicks Approve"
                    )
                else:
                    # L10: honor the return value — the CAS lost (the desk
                    # is already pending or closed), so NO approval round
                    # was opened by this cycle. The wire must not claim
                    # one was; the desk stayed 'released'.
                    logger.warning(
                        "pre-trip approval round NOT opened for desk %s "
                        "position %s \u2014 the desk stayed released",
                        desk_id, pos.id,
                    )
                    await emit_step(
                        f"Pre-trip approval could not be opened for "
                        f"{pos.id} \u2014 the desk stayed released; "
                        "nothing books this cycle"
                    )
                    give_up_text = (
                        "Cycle ended \u2014 the pre-trip approval round "
                        "could not be opened (the desk stayed released); "
                        "nothing booked"
                    )
                return await self._give_up(
                    desk_id, emit, step,
                    status="escalated",
                    text=give_up_text,
                    positions=positions,
                    losses_admitted=losses_admitted,
                    comparison_mode=comparison, settle=settle,
                    budget_start=budget_start, budget_left=budget_left,
                    contingency_start=contingency_start,
                    contingency_left=contingency_left,
                )

            # book (or an escalate pick) meets the wall.
            amount = pos.mark_price
            over_cap = amount > mandate.authority_cap
            over_budget = amount > budget_left
            # Cap waiver: only a human escalation "A" click waives the
            # authority cap for THIS position; the normal path never does.
            cap_waived = False
            if action.kind == "escalate" or over_cap or over_budget:
                # L4: a pinned escalate mark carries the mark's own
                # rationale onto the wire instead of the generic fallback;
                # brain-flagged escalations keep today's behavior.
                escalate_reason = (
                    action.rationale
                    if action.kind == "escalate"
                    and pinned_pos is not None and pos.id == pinned_pos.id
                    else None
                )
                choice = await self._escalation_beat(
                    desk_id, emit, pos, amount, over_cap, over_budget,
                    comparison, escalate_reason=escalate_reason,
                )
                if comparison:
                    # Decisions logged, nothing waits and nothing executes.
                    settle.append(LedgerInput(
                        kind="trade", amount=Decimal("0"),
                        position_id=pos.id,
                        note="comparison mode \u2014 escalation logged, "
                             "no execution",
                    ))
                    continue
                if choice is None:
                    # Bounded wait expired (or no human reachable): give
                    # up honestly on the whole cycle — flushing whatever
                    # already sits on the settle list (admitted losses
                    # persist here too; nothing executes on a guess).
                    return await self._give_up(
                        desk_id, emit, step,
                        status="escalated",
                        text="Escalation wait expired \u2014 giving up "
                             "without executing; nothing runs on a guess",
                        positions=positions,
                        losses_admitted=losses_admitted,
                        comparison_mode=comparison, settle=settle,
                        budget_start=budget_start, budget_left=budget_left,
                        contingency_start=contingency_start,
                        contingency_left=contingency_left,
                    )
                if choice != "A":
                    continue  # the click said hold — nothing executes
                # The chosen option re-enters the wall: the click waives
                # ONLY the cap; budget is never waived.
                if over_budget:
                    await emit({
                        "type": "error",
                        "code": "BUDGET_EXCEEDED",
                        "position_id": pos.id,
                    })
                    continue
                cap_waived = True  # human approved book-now over the cap

            if comparison:
                # Comparison mode logs the decision and skips to settle.
                settle.append(LedgerInput(
                    kind="trade", amount=Decimal("0"),
                    position_id=pos.id,
                    note="comparison mode \u2014 decision 'book' logged, "
                         "not executed (no write commands)",
                ))
                continue

            budget_before = budget_left
            write_outcome: dict = {}
            budget_left, contingency_left = await self._write_position(
                desk_id, emit, pos, budget_left, contingency_left, settle,
                authority_cap=mandate.authority_cap, cap_waived=cap_waived,
                outcome=write_outcome,
                # H1b: thread the approved price into the PINNED write so
                # the wall can enforce contingency against the offer the
                # manager signed off (not whatever the mark measured).
                approved_price=(
                    pinned_approved_price
                    if pinned_pos is not None and pos.id == pinned_pos.id
                    else _NOT_PINNED
                ),
            )
            # --- G4 unbookable-pin edge (Gate 2 decision 1). The approved
            # offer can no longer be bought AT ALL (not merely pricier —
            # budget/cap/contingency own that). Exactly ONE re-judgment +
            # one fresh approval round is allowed; after that the desk
            # holds and discloses. The reapproval_count (cap 1) bounds the
            # ping-pong.
            if (
                pinned_pos is not None
                and approval is not None
                and pos.id == pinned_pos.id
                and pos.status != "booked"
                and write_outcome.get("code") in UNBOOKABLE_CODES
            ):
                resumed = await self._reapprove_pinned(
                    desk_id, emit, approval, pos, offers_by_pos,
                    budget_left, contingency_left, meter_used,
                    # L11 follow-up: the ACTUAL unbookable code (either
                    # OFFER_EXPIRED or BOOKING_EXPIRED) rides the wire/
                    # disclosure text — never a hardcoded one.
                    unbookable_code=write_outcome["code"],
                )
                if resumed == "requested":
                    unbookable_text = (
                        "Approved offer expired \u2014 re-judged once and "
                        "asked the manager to approve the replacement; "
                        "nothing booked on a guess"
                    )
                elif resumed == "held":
                    unbookable_text = (
                        "Approved offer expired again \u2014 the one "
                        "re-approval is spent, so the desk HOLDS and "
                        "discloses instead of booking something the "
                        "manager never saw"
                    )
                else:
                    # L10: request_approval lost its CAS — no fresh round
                    # was opened; the wire must not claim one was.
                    unbookable_text = (
                        "Approved offer expired and the replacement "
                        "could not open a fresh approval round (the desk "
                        "stayed released) \u2014 nothing booked on a guess"
                    )
                return await self._give_up(
                    desk_id, emit, step,
                    status="escalated",
                    text=unbookable_text,
                    positions=positions,
                    losses_admitted=losses_admitted,
                    comparison_mode=comparison, settle=settle,
                    budget_start=budget_start, budget_left=budget_left,
                    contingency_start=contingency_start,
                    contingency_left=contingency_left,
                )
            if pos.status == "booked":
                # Disclose the booking as a COUNTED step (S6): a booked
                # position is real progress the step budget must see —
                # this is what keeps the give-up gate below reachable.
                # The price shown is the budget delta = the real verified
                # price the write path consumed.
                await emit_step(
                    f"Booked {pos.id} at {budget_before - budget_left} "
                    "\u2014 TICKETED asserted, sandbox money"
                )
            if step >= self.step_budget:
                return await self._give_up(
                    desk_id, emit, step,
                    status="failed",
                    text="Step budget exhausted \u2014 giving up "
                         "gracefully",
                    positions=positions, losses_admitted=losses_admitted,
                    comparison_mode=comparison, settle=settle,
                    budget_start=budget_start, budget_left=budget_left,
                    contingency_start=contingency_start,
                    contingency_left=contingency_left,
                )

        # --- settle: one ledger transaction for the cycle's entries, plus
        # --- persisted budget consumption (fix 5) so the guard survives a
        # --- restart instead of resetting on the next cycle.
        if settle:
            spend_total = budget_start - budget_left
            contingency_used_total = contingency_start - contingency_left
            await asyncio.to_thread(
                self.store.settle,
                desk_id, settle, spend_total, contingency_used_total,
            )
        await emit_step(
            f"Settled {len(settle)} ledger entries in one transaction; "
            f"P&L computed in code \u2014 cycle closes in {mode}"
        )

        # Budget-exhaustion label (GAP 1): AFTER settle, if the remaining
        # budget cannot cover even the cheapest still-held position, the
        # cycle says so instead of closing "closed". Label only — this is
        # NOT a write blocker (the per-booking guard owns that) and it
        # uses last-known in-memory marks: zero Atlas calls here. Strict
        # `<` — exactly-affordable still closes normally.
        held_left = [p for p in positions if p.status == "held"]
        if held_left and budget_left < min(p.mark_price for p in held_left):
            cheapest_mark = min(p.mark_price for p in held_left)
            status = "budget_exhausted"
            await emit_step(
                f"Budget exhausted \u2014 remaining budget {budget_left} "
                f"is below the cheapest held mark {cheapest_mark} "
                "(last-known marks, uncertainty disclosed; budget is "
                "never waived)"
            )

        return await self._finish(desk_id, emit, DeskResult(
            desk_id=desk_id, status=status, pnl=self._pnl(positions),
            losses_admitted=losses_admitted, step_count=step,
            comparison_mode=comparison,
        ))

    # ------------------------------------------------------------------
    # G4 pinned resume (S5) — mark construction, provenance, and the
    # one-shot re-approval for an expired pin.
    # ------------------------------------------------------------------

    @staticmethod
    def _pinned_mark(
        approval: ApprovalState,
        pos: Position,
        budget_left: Decimal,
        contingency_left: Decimal,
        authority_cap: Decimal,
        fresh_offer=None,
    ) -> tuple[DeskAction, Decimal | None]:
        """Build the PINNED mark for an approved position.

        Returns (action, approved_price) — the approved price rides along
        so the execute wall can enforce it at WRITE time too (H1b).

        The offer id is forced back to the APPROVED one: this same cycle
        just repriced the position and may have found a different cheapest
        offer, but "pinned" means the desk buys what the manager signed
        off — not what the search happened to return afterwards.
    
        The mark degrades to `escalate` when:
        - the fresh search's best offer is NOT the approved one (H1a) —
          the mark price then belongs to a DIFFERENT offer than the one
          the manager signed off, so the contingency test below would be
          measuring the wrong offer's price; re-confirm before booking;
        - the approved price is absent/unparseable (L1) — never book
          against a price we cannot verify; escalate instead;
        - the fresh price has moved BEYOND what the approval covers.
        The contingency test is the one this branch owns; the cap/budget
        tests are stated too so the rationale names the real reason, and
        the execute wall enforces them a second time regardless —
        INCLUDING contingency: for pinned writes `_write_position`
        re-checks the VERIFIED price against the approved price + the
        contingency remainder (CONTINGENCY_EXCEEDED), belt and braces,
        never a silent book on the pinned path.
        """
        pos.atlas_offer_id = approval.approved_offer_id
        fresh = pos.mark_price
        approved_price: Decimal | None = None
        raw_price = approval.snapshot.get("price")
        if raw_price is not None:
            try:
                approved_price = Decimal(str(raw_price))
            except (ArithmeticError, ValueError):
                approved_price = None

        reasons: list[str] = []
        if approved_price is None:
            # L1: fail CLOSED — an unverifiable approved price escalates
            # instead of skipping the contingency test and booking.
            reasons.append(
                "approved price unverifiable \u2014 escalating instead "
                "of booking"
            )
        else:
            move = fresh - approved_price
            if move > contingency_left:
                reasons.append(
                    f"price moved {move} above the approved "
                    f"{approved_price}, beyond the {contingency_left} "
                    "contingency remainder"
                )
        # H1a: divergence — the fresh mark must BE the approved offer's
        # price. When the fresh search priced a different offer, the
        # contingency test above measured the wrong offer.
        if (
            fresh_offer is not None
            and getattr(fresh_offer, "atlas_offer_id", None)
            != approval.approved_offer_id
        ):
            reasons.append(
                "approved offer no longer matches the fresh priced "
                "mark \u2014 re-confirm before booking"
            )
        if fresh > authority_cap:
            reasons.append("mark above the single-trade authority cap")
        if fresh > budget_left:
            reasons.append("mark above the remaining budget")

        if reasons:
            return DeskAction(
                position_id=pos.id,
                kind="escalate",
                rationale=(
                    "pinned_resume \u2014 " + "; ".join(reasons)
                    + "; the manager's approval does not cover this move, "
                    "so it escalates instead of booking"
                ),
            ), approved_price
        return DeskAction(
            position_id=pos.id,
            kind="book",
            rationale=(
                "pinned_resume \u2014 executing the offer the manager "
                "approved; no re-judgment, wall invariants still apply"
            ),
        ), approved_price

    async def _announce_pinned_resume(
        self,
        desk_id: str,
        emit: Emit,
        approval: ApprovalState,
        pos: Position,
        action: DeskAction,
    ) -> None:
        """Provenance: this position executed the APPROVED offer and was
        never re-judged.

        The stream already carries it — the pinned mark rides the ordinary
        `trade` emit with a `pinned_resume —` rationale, so no new SSE
        event type is invented here. This adds the typed sink moment so
        the Waybot can say the same thing to the manager.
        """
        del emit  # the stream side is the pinned mark's own trade event
        if self.sink is not None:
            self.sink.publish(DeskEvent(
                type="pinned_resume",
                desk_id=desk_id,
                payload={
                    "position_id": pos.id,
                    "offer_id": approval.approved_offer_id,
                    "mark_kind": action.kind,
                    "approved_price": approval.snapshot.get("price"),
                    "reapproval_count": approval.reapproval_count,
                },
            ))

    async def _reapprove_pinned(
        self,
        desk_id: str,
        emit: Emit,
        approval: ApprovalState,
        pos: Position,
        offers_by_pos: dict,
        budget_left: Decimal,
        contingency_left: Decimal,
        meter_used: int,
        unbookable_code: str,
    ) -> str:
        """The approved offer is unbookable. Spend the ONE re-approval if
        it is still available, otherwise hold and disclose.

        Returns "requested" (a fresh approval round is open) or "held".
        """
        if approval.reapproval_count >= REAPPROVAL_CAP:
            await emit({
                "type": "error",
                "code": "PIN_UNBOOKABLE_HELD",
                "position_id": pos.id,
                "disclosure": (
                    "the approved offer expired a second time — the "
                    "one allowed re-approval is spent, so the desk holds; "
                    "nothing books that the manager never saw"
                ),
            })
            return "held"

        count = await asyncio.to_thread(self.store.bump_reapproval, desk_id)
        # Exactly one re-judgment, scoped to THIS position.
        rejudged = await self.brain.judge(
            [pos], VOLATILITY_PRIORS, METER_MAX - meter_used,
            budget_left, contingency_left,
        )
        pick = next(
            (a for a in rejudged if a.position_id == pos.id), None
        )
        offer = offers_by_pos.get(pos.id)
        if pick is None or pick.kind != "book" or offer is None:
            await emit({
                "type": "error",
                "code": "PIN_UNBOOKABLE_HELD",
                "position_id": pos.id,
                "disclosure": (
                    "the approved offer expired and the re-judgment did "
                    "not produce a bookable replacement — holding"
                ),
            })
            return "held"

        # The replacement the manager will be asked to approve.
        pos.atlas_offer_id = offer.atlas_offer_id
        opened = await request_approval(
            self.store, self.sink, desk_id, pos, offer, pos.mark_price,
            reason=(
                f"the approved offer is no longer bookable "
                f"({unbookable_code}) \u2014 one re-judgment allowed, "
                "fresh approval required"
            ),
            reapproval_count=count,
            # L12: a re-approval NEVER resets the counter — the allowance
            # is per pin lineage, and this is the lineage spending it.
            reset_reapproval=False,
        )
        if not opened:
            # L10: honor the return value — the CAS lost, so no fresh
            # round was opened; the desk stayed 'released'.
            logger.warning(
                "re-approval round NOT opened for desk %s position %s "
                "\u2014 the desk stayed released",
                desk_id, pos.id,
            )
            return "not_opened"
        return "requested"

    # ------------------------------------------------------------------
    # Reprice fan-out — one search per position × candidate date,
    # meter-gated at 20 (hard stop; check BEFORE dispatch, adjacent to
    # decrement), bounded concurrency, skip-and-continue on failures.
    # ------------------------------------------------------------------

    async def _reprice_fan_out(
        self, desk_id: str, emit: Emit, held: list[Position]
    ) -> tuple[int, dict]:
        """Returns (meter_used, {position_id: chosen Offer}).

        The chosen offer is kept (S5) because the approval checkpoint has
        to snapshot the offer's IDENTITY — segments, flight numbers, the
        carrier/cabin once the mapper carries them — and re-deriving it
        later would need a second Atlas call. Positions whose reprice
        failed or returned nothing are simply absent from the map.
        """
        meter_used = 0
        meter_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(FANOUT_CONCURRENCY)

        async def reprice_one(pos: Position) -> tuple[str, Position, object]:
            nonlocal meter_used
            async with semaphore:
                # GUARD: meter check BEFORE dispatch, adjacent to the
                # decrement — the 21st search is never invoked.
                async with meter_lock:
                    if meter_used >= METER_MAX:
                        return ("exhausted", pos, None)
                    meter_used += 1
                    if self.meter_report is not None:
                        self.meter_report(desk_id, meter_used)
                try:
                    offers = await asyncio.to_thread(
                        self.atlas.search,
                        pos.origin, pos.dest, pos.depart_date, pos.pax,
                    )
                except Exception:  # noqa: BLE001 — stale mark + disclose
                    return ("failed", pos, None)
                return ("ok", pos, offers)

        results = await asyncio.gather(*(reprice_one(p) for p in held))

        updates: list[MarkUpdate] = []
        offers_by_pos: dict[str, object] = {}
        now = datetime.now(timezone.utc)
        for outcome, pos, offers in results:
            if outcome == "ok" and offers:
                best = offers[0]  # AtlasClient sorts cheapest-first
                offers_by_pos[pos.id] = best
                old = pos.mark_price
                pos.mark_price = best.price
                pos.mark_stale = False
                pos.atlas_offer_id = best.atlas_offer_id
                updates.append(MarkUpdate(
                    position_id=pos.id, mark_price=best.price, mark_at=now,
                    mark_stale=False, atlas_offer_id=best.atlas_offer_id,
                ))
                await emit({
                    "type": "mark",
                    "position_id": pos.id,
                    "old": str(old),
                    "new": str(best.price),
                    "search_ref": best.id,
                    "meter_used": meter_used,
                })
            else:
                # Skip-and-continue: stale mark, uncertainty disclosed.
                reason = {
                    "exhausted": "search meter exhausted at 20",
                    "failed": "reprice failed \u2014 holding stale mark",
                    "ok": "no offers returned",
                }[outcome]
                pos.mark_stale = True
                updates.append(MarkUpdate(
                    position_id=pos.id, mark_price=pos.mark_price,
                    mark_at=now, mark_stale=True,
                ))
                await emit({
                    "type": "mark",
                    "position_id": pos.id,
                    "old": str(pos.mark_price),
                    "new": str(pos.mark_price),
                    "search_ref": None,
                    "stale": True,
                    "disclosure": f"{reason} \u2014 uncertainty disclosed",
                    "meter_used": meter_used,
                })

        if updates:
            await asyncio.to_thread(self.store.update_marks, desk_id, updates)
        return meter_used, offers_by_pos

    # ------------------------------------------------------------------
    # Escalation beat — over cap / over budget → two priced options +
    # recommendation; NOTHING executes until the one human click.
    # ------------------------------------------------------------------

    async def _escalation_beat(
        self,
        desk_id: str,
        emit: Emit,
        pos: Position,
        amount: Decimal,
        over_cap: bool,
        over_budget: bool,
        comparison: bool,
        escalate_reason: str | None = None,
    ) -> "str | None":
        esc_id = f"esc-{pos.id}"
        why = []
        if over_cap:
            why.append("amount above single-trade authority cap")
        if over_budget:
            why.append("amount above remaining budget")
        options = [
            {
                "key": "A",
                "label": f"book now at {amount} (manual approval)",
                "price": str(amount),
            },
            {
                "key": "B",
                "label": "hold \u2014 re-check next cycle, no execution",
                "price": str(pos.mark_price),
            },
        ]
        await emit({
            "type": "escalate",
            "esc_id": esc_id,
            "position_id": pos.id,
            # L4: a pinned escalate mark names its own reasons here;
            # brain-flagged escalations keep the generic fallback.
            "reason": (
                "; ".join(why)
                or escalate_reason
                or "brain flagged for review"
            ),
            "options": options,
            "recommendation": "B",
            "disclosures": [
                "sandbox money only",
                "nothing executes until the one human click",
            ] + (["comparison mode \u2014 logged, not executed"]
                 if comparison else []),
        })
        if comparison:
            # Comparison mode: log the beat, never block the cycle.
            return "B"
        if self.escalation_slot is None:
            return None  # no human reachable — fail closed
        slot = self.escalation_slot(desk_id, esc_id)
        if slot is None:
            return None
        try:
            await asyncio.wait_for(
                slot["event"].wait(), timeout=self.escalation_wait
            )
        except asyncio.TimeoutError:
            self._clear_escalation(desk_id, esc_id)  # slot hygiene (fix 8)
            return None  # bounded wait expired → give-up path
        choice = slot.get("choice")
        self._clear_escalation(desk_id, esc_id)  # slot hygiene (fix 8)
        return choice

    def _clear_escalation(self, desk_id: str, esc_id: str) -> None:
        """Remove the escalation slot once the wait timed out or the click
        landed — a late POST then gets a 410, never a misleading 200."""
        if self.escalation_clear is not None:
            self.escalation_clear(desk_id, esc_id)

    # ------------------------------------------------------------------
    # Write path (LIVE mode only) — strictly sequential per position.
    # GUARD #3: verify (freshness re-read) before every write; assert
    # TICKETED, not 200 OK. Returns (budget_left, contingency_left).
    # ------------------------------------------------------------------

    async def _write_position(
        self,
        desk_id: str,
        emit: Emit,
        pos: Position,
        budget_left: Decimal,
        contingency_left: Decimal,
        settle: list[LedgerInput],
        authority_cap: Decimal,
        cap_waived: bool = False,
        outcome: dict | None = None,
        approved_price: Decimal | None | object = _NOT_PINNED,
    ) -> tuple[Decimal, Decimal]:
        """`outcome` (S5, optional) is filled with {"code": ...} for the
        failure that stopped this write, so the caller can tell an
        UNBOOKABLE approved offer apart from a merely-too-expensive one.
        The emitted error events are unchanged — this only records what
        was already announced.

        `approved_price` (H1b): for a PINNED write, the price the manager
        approved — the wall enforces contingency against it a SECOND time
        (the mark-time test measured the fresh mark, which may have been
        a different offer's price). `_NOT_PINNED` for ordinary writes;
        None on a pinned write fails closed (never book unpinned-priced).
        CONTINGENCY_EXCEEDED is a money failure like BUDGET_EXCEEDED —
        retryable classification, deliberately NOT in UNBOOKABLE_CODES."""

        async def fail(code: str) -> None:
            if outcome is not None:
                outcome["code"] = code
            await emit({
                "type": "error", "code": code, "position_id": pos.id,
            })

        try:
            offer_id = pos.atlas_offer_id
            if not offer_id:
                await fail("OFFER_EXPIRED")
                return budget_left, contingency_left

            # GUARD #3: re-read the world before the write (fresh verify).
            verified = None
            try:
                verified = await asyncio.to_thread(self.atlas.verify, offer_id)
                if verified.price_change == "increased":
                    # CONDITIONAL: confirm-price ONLY on a verify-reported
                    # increase; unchanged/decreased skip straight to create.
                    await asyncio.to_thread(
                        self.atlas.confirm_price, verified.booking_id
                    )
            except AtlasQueryOnly as signal:
                new_contingency = await self._reconcile(
                    desk_id, emit, pos, signal, verified=verified,
                    contingency_left=contingency_left, settle=settle,
                    outcome=outcome,
                )
                return budget_left, new_contingency
            except AtlasError as exc:
                await fail(exc.code)
                return budget_left, contingency_left

            # BUDGET INVARIANT (fix 1): the execute wall re-checks the REAL
            # verified price against budget_left RIGHT BEFORE the write.
            # verify may report `increased` (and confirm_price may proceed),
            # so the mark-based wall check is not enough — the desk must
            # never book above the remaining budget. Budget is NEVER waived
            # (an escalated "A" click waives only the authority cap). Fail
            # closed: code-only error, position stays held, nothing written.
            if verified.current_price > budget_left:
                await fail("BUDGET_EXCEEDED")
                return budget_left, contingency_left

            # AUTHORITY-CAP INVARIANT (BK1): re-check the REAL verified price
            # against the per-decision cap. The mark-time wall check is not
            # enough — an intra-cycle rise can push a within-cap mark over the
            # cap, which would otherwise book with no human click and breach
            # the "zero authority-cap breaches" invariant. Skip ONLY when a
            # human escalation click already waived the cap for this position;
            # budget stays enforced above. Fail closed: code-only error,
            # position stays held, nothing written.
            if not cap_waived and verified.current_price > authority_cap:
                await fail("AUTHORITY_CAP_EXCEEDED")
                return budget_left, contingency_left

            # CONTINGENCY INVARIANT (H1b): a PINNED write is re-checked at
            # WRITE time against the price the manager actually approved —
            # the mark-time gate may have measured a different offer's
            # price. Money failure, same class as BUDGET_EXCEEDED: the
            # position stays held, nothing written, and the pin is NOT
            # unbookable (a later cycle can re-run it). Fail closed when a
            # pinned write somehow carries no approved price at all.
            if approved_price is not _NOT_PINNED:
                if approved_price is None:
                    await fail("DESK_STATE_INVALID")
                    return budget_left, contingency_left
                if verified.current_price - approved_price > contingency_left:
                    await fail("CONTINGENCY_EXCEEDED")
                    return budget_left, contingency_left

            # order create — WRITE, NEVER retried. The pax payload is
            # built from THIS verify's travelers (traveler_id carried,
            # never invented — passenger-input.md). S3: real travelers
            # for gated desks, demo for ungated (byte-safe). The builder
            # opens DB sessions, so it runs off the event loop via
            # asyncio.to_thread (the store-access convention).
            from app.pax import build_pax_json
            pax_build = await asyncio.to_thread(
                build_pax_json, desk_id, verified.travelers, self.store
            )
            if pax_build.hold:
                # Gated desk missing/short roster → hold + escalate,
                # NEVER silently book demo identities.
                await fail("PAX_ROSTER_INCOMPLETE")
                return budget_left, contingency_left
            pax_json = pax_build.pax_json
            pax_source = pax_build.pax_source
            try:
                ref = await asyncio.to_thread(
                    self.atlas.create_order,
                    verified.booking_id,
                    pax_json,
                    "continue-without-seat",
                )
            except AtlasUnknownOrder as signal:
                # An order MAY exist: query-only follow-up, never re-create.
                try:
                    await asyncio.to_thread(
                        self.atlas.follow_up_query_only, signal
                    )
                except AtlasError:
                    pass
                await fail(signal.code)
                return budget_left, contingency_left
            except AtlasQueryOnly as signal:  # PRICE_CHANGED et al.
                new_contingency = await self._reconcile(
                    desk_id, emit, pos, signal, verified=verified,
                    contingency_left=contingency_left, settle=settle,
                    outcome=outcome,
                )
                return budget_left, new_contingency
            except AtlasError as exc:
                await fail(exc.code)
                return budget_left, contingency_left

            # order pay — WRITE, single-use, NEVER retried. The
            # confirmation id is the one from THAT create_order response.
            try:
                payment = await asyncio.to_thread(
                    self.atlas.pay, ref.payment_confirmation_id
                )
            except AtlasError as exc:
                await fail(exc.code)
                return budget_left, contingency_left
            if payment.query_only:
                # The ONLY follow-up is `order status` — never re-pay.
                try:
                    await asyncio.to_thread(
                        self.atlas.follow_up_query_only,
                        AtlasQueryOnly(payment.code,
                                       payment.order_no or ref.order_no),
                    )
                except AtlasError:
                    pass
                await fail(payment.code)
                return budget_left, contingency_left

            # GUARD: assert the real outcome — TICKETED and only TICKETED.
            status, ticketed = await asyncio.to_thread(
                self.atlas.poll_until_ticketed, ref.order_no
            )
            if not ticketed:
                await fail(status.code or "TICKETING_PENDING")
                return budget_left, contingency_left

            await asyncio.to_thread(
                self.store.mark_booked, pos.id, ref.order_no, True
            )
            pos.status = "booked"
            pos.ticket_asserted = True
            budget_left -= verified.current_price
            # pax_source rides the booking provenance (S3): 'collected'
            # = real captured travelers on a gated desk; 'demo' = ungated
            # legacy/recorded desk demo identities (byte-safe).
            settle.append(LedgerInput(
                kind="trade", amount=verified.current_price,
                position_id=pos.id, ref=ref.order_no,
                note=(
                    f"booked \u2014 TICKETED asserted, sandbox money; "
                    f"pax_source={pax_source}"
                ),
            ))
            # Alloc beat — S4 SEAM (Branch B: cut, ledger-only). The seat
            # module is NOT activated on this sandbox account (Step 2's live
            # proof skipped on TICKETING_ACTIVATION_REQUIRED; Seat UAT was
            # marked "Skipped" at ATRIP activation), so no seat_list /
            # seat_select is ever attempted and nothing is retried. The
            # alloc degrades to a ledger-only entry funded solely from
            # realized savings (none realized on this beat → zero).
            settle.append(LedgerInput(
                kind="alloc", amount=Decimal("0"), position_id=pos.id,
                ref="ledger_only",
                note="seat selection unavailable \u2014 seat module not "
                     "activated on this sandbox account; alloc degraded "
                     "to ledger-only",
            ))
            await emit({
                "type": "alloc",
                "position_id": pos.id,
                "amount": "0",
                "seat_ref": "ledger_only",
                "disclosure": "seat selection unavailable \u2014 seat "
                              "module not activated on this sandbox "
                              "account; ledger-only",
            })
            return budget_left, contingency_left
        except AtlasError as exc:
            await fail(exc.code)
            return budget_left, contingency_left

    async def _reconcile(
        self,
        desk_id: str,
        emit: Emit,
        pos: Position,
        signal: AtlasQueryOnly,
        verified,
        contingency_left: Decimal,
        settle: list[LedgerInput],
        outcome: dict | None = None,
    ) -> Decimal:
        """PRICE_CHANGED → absorb-from-contingency vs re-quote (judgment
        boundary in the brain, math in code). NEVER a second order.
        Returns the updated contingency remainder. `outcome` (S5) records
        a non-PRICE_CHANGED signal code for the caller, same as the write
        path's own failures — an OFFER_EXPIRED arriving as a query-only
        signal must still register as "the pin is unbookable"."""
        if signal.code != "PRICE_CHANGED":
            if outcome is not None:
                outcome["code"] = signal.code
            await emit({
                "type": "error", "code": signal.code, "position_id": pos.id,
            })
            return contingency_left
        if verified is not None:
            delta = max(
                verified.current_price - pos.mark_price, Decimal("0")
            )
        else:
            delta = Decimal("0")
        resolution = self.brain.resolve_price_change(delta, contingency_left)
        if resolution == "absorb":
            contingency_left -= delta
            note = (
                f"PRICE_CHANGED absorbed {delta} from contingency \u2014 "
                "never a second order"
            )
        else:
            note = (
                f"PRICE_CHANGED {delta} exceeds contingency remainder "
                f"{contingency_left} \u2014 re-quote next cycle, never a "
                "second order"
            )
        settle.append(LedgerInput(
            kind="reconcile", amount=delta if resolution == "absorb"
            else Decimal("0"), position_id=pos.id, note=note,
        ))
        await emit({
            "type": "reconcile",
            "position_id": pos.id,
            "delta": str(delta),
            "resolution": resolution,
            "disclosure": "never a second order \u2014 re-verify/re-quote "
                          "first",
        })
        return contingency_left

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    @staticmethod
    def _pnl(positions: list[Position]) -> Decimal:
        """Cycle P&L — deterministic ledger math in CODE (mark vs seeded
        cost basis), never LLM output."""
        return sum(
            (p.mark_price - p.cost_basis for p in positions), Decimal("0")
        )

    async def _give_up(
        self,
        desk_id: str,
        emit: Emit,
        step: int,
        *,
        status: str,
        text: str,
        positions: list[Position],
        losses_admitted: int,
        comparison_mode: bool,
        settle: list[LedgerInput],
        budget_start: Decimal,
        budget_left: Decimal,
        contingency_start: Decimal,
        contingency_left: Decimal,
    ) -> DeskResult:
        """Bounded give-up — honest on EVERY exit path (S1 shape kept).

        Emits the disclosed step line, then flushes whatever the cycle
        already put on the settle list through the SAME one-transaction
        settle call as the normal close (no Atlas calls here), and
        finishes with REAL desk math: pnl from marks vs seeded cost
        bases, the true admitted-loss count, and the true mode flag —
        never the model default for comparison_mode."""
        await emit({"type": "step", "n": step + 1, "text": text})
        if settle:
            await asyncio.to_thread(
                self.store.settle,
                desk_id, settle,
                budget_start - budget_left,
                contingency_start - contingency_left,
            )
        return await self._finish(desk_id, emit, DeskResult(
            desk_id=desk_id, status=status, pnl=self._pnl(positions),
            losses_admitted=losses_admitted, step_count=step,
            comparison_mode=comparison_mode,
        ))

    @staticmethod
    def _live_booking_armed() -> bool:
        """The human switch, read fresh per cycle (never cached): armed
        ONLY when WAYPOINT_LIVE_BOOKING reads exactly "1". Unset or any
        other value keeps the desk in comparison mode."""
        return os.environ.get(LIVE_BOOKING_ENV, "") == "1"

    async def _comparison_mode(self, armed: bool) -> bool:
        """The write-path gate — two INDEPENDENT gates, fail-closed. Real
        writes fire ONLY when BOTH hold: (1) the human switch
        WAYPOINT_LIVE_BOOKING is armed ("1"), AND (2) ticketing_live()
        currently reads true. Either gate blocking → decisions are logged
        + marked but NO write commands run (labeled on the wire).
        Fail-closed on any probe absence or error. The auth-status probe
        is a blocking subprocess, so it runs off the event loop via
        asyncio.to_thread (fix 2) — never inline on the loop; while the
        switch is unarmed the probe is skipped entirely. `armed` is the
        caller's single env read (run() derives the wire label from the
        SAME value — no second read, no label-only TOCTOU)."""
        if not armed:
            return True
        probe = getattr(self.atlas, "ticketing_live", None)
        if probe is None:
            return True
        try:
            return not await asyncio.to_thread(probe)
        except Exception:  # noqa: BLE001 — normalized posture only
            return True

    @staticmethod
    async def _finish(
        desk_id: str, emit: Emit, result: DeskResult
    ) -> DeskResult:
        del desk_id
        await emit({"type": "result", "result": result.model_dump(mode="json")})
        return result
