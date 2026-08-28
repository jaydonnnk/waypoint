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
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from app.agent.brain import DeskBrain
from app.atlas.client import AtlasError, AtlasQueryOnly, AtlasUnknownOrder
from app.db.store import DeskStore, LedgerInput, MarkUpdate
from app.fixture import VOLATILITY_PRIORS
from app.models import DeskResult, Position
from app.provenance import build_rails

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

        held = [p for p in positions if p.status == "held"]
        meter_used = await self._reprice_fan_out(desk_id, emit, held)
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

        # --- JUDGE: advise gate. ONE batched call per cycle; the brain
        # --- never raises (degrades to the deterministic fallback).
        actions = await self.brain.judge(
            held, VOLATILITY_PRIORS, METER_MAX - meter_used,
            budget_left, contingency_left,
        )
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

            # book (or an escalate pick) meets the wall.
            amount = pos.mark_price
            over_cap = amount > mandate.authority_cap
            over_budget = amount > budget_left
            # Cap waiver: only a human escalation "A" click waives the
            # authority cap for THIS position; the normal path never does.
            cap_waived = False
            if action.kind == "escalate" or over_cap or over_budget:
                choice = await self._escalation_beat(
                    desk_id, emit, pos, amount, over_cap, over_budget,
                    comparison,
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
            budget_left, contingency_left = await self._write_position(
                desk_id, emit, pos, budget_left, contingency_left, settle,
                authority_cap=mandate.authority_cap, cap_waived=cap_waived,
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
    # Reprice fan-out — one search per position × candidate date,
    # meter-gated at 20 (hard stop; check BEFORE dispatch, adjacent to
    # decrement), bounded concurrency, skip-and-continue on failures.
    # ------------------------------------------------------------------

    async def _reprice_fan_out(
        self, desk_id: str, emit: Emit, held: list[Position]
    ) -> int:
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
        now = datetime.now(timezone.utc)
        for outcome, pos, offers in results:
            if outcome == "ok" and offers:
                best = offers[0]  # AtlasClient sorts cheapest-first
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
        return meter_used

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
            "reason": "; ".join(why) or "brain flagged for review",
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
    ) -> tuple[Decimal, Decimal]:
        try:
            offer_id = pos.atlas_offer_id
            if not offer_id:
                await emit({
                    "type": "error", "code": "OFFER_EXPIRED",
                    "position_id": pos.id,
                })
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
                )
                return budget_left, new_contingency
            except AtlasError as exc:
                await emit({
                    "type": "error", "code": exc.code,
                    "position_id": pos.id,
                })
                return budget_left, contingency_left

            # BUDGET INVARIANT (fix 1): the execute wall re-checks the REAL
            # verified price against budget_left RIGHT BEFORE the write.
            # verify may report `increased` (and confirm_price may proceed),
            # so the mark-based wall check is not enough — the desk must
            # never book above the remaining budget. Budget is NEVER waived
            # (an escalated "A" click waives only the authority cap). Fail
            # closed: code-only error, position stays held, nothing written.
            if verified.current_price > budget_left:
                await emit({
                    "type": "error",
                    "code": "BUDGET_EXCEEDED",
                    "position_id": pos.id,
                })
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
                await emit({
                    "type": "error",
                    "code": "AUTHORITY_CAP_EXCEEDED",
                    "position_id": pos.id,
                })
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
                await emit({
                    "type": "error",
                    "code": "PAX_ROSTER_INCOMPLETE",
                    "position_id": pos.id,
                })
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
                await emit({
                    "type": "error", "code": signal.code,
                    "position_id": pos.id,
                })
                return budget_left, contingency_left
            except AtlasQueryOnly as signal:  # PRICE_CHANGED et al.
                new_contingency = await self._reconcile(
                    desk_id, emit, pos, signal, verified=verified,
                    contingency_left=contingency_left, settle=settle,
                )
                return budget_left, new_contingency
            except AtlasError as exc:
                await emit({
                    "type": "error", "code": exc.code,
                    "position_id": pos.id,
                })
                return budget_left, contingency_left

            # order pay — WRITE, single-use, NEVER retried. The
            # confirmation id is the one from THAT create_order response.
            try:
                payment = await asyncio.to_thread(
                    self.atlas.pay, ref.payment_confirmation_id
                )
            except AtlasError as exc:
                await emit({
                    "type": "error", "code": exc.code,
                    "position_id": pos.id,
                })
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
                await emit({
                    "type": "error", "code": payment.code,
                    "position_id": pos.id,
                })
                return budget_left, contingency_left

            # GUARD: assert the real outcome — TICKETED and only TICKETED.
            status, ticketed = await asyncio.to_thread(
                self.atlas.poll_until_ticketed, ref.order_no
            )
            if not ticketed:
                await emit({
                    "type": "error",
                    "code": status.code or "TICKETING_PENDING",
                    "position_id": pos.id,
                })
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
            await emit({
                "type": "error", "code": exc.code, "position_id": pos.id,
            })
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
    ) -> Decimal:
        """PRICE_CHANGED → absorb-from-contingency vs re-quote (judgment
        boundary in the brain, math in code). NEVER a second order.
        Returns the updated contingency remainder."""
        if signal.code != "PRICE_CHANGED":
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
