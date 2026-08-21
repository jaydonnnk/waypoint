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
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from app.agent.brain import DeskBrain
from app.atlas.client import AtlasError, AtlasQueryOnly, AtlasUnknownOrder
from app.db.store import DeskStore, LedgerInput, MarkUpdate
from app.fixture import VOLATILITY_PRIORS
from app.models import DeskResult, Position

# emit takes a JSON-serializable dict event and returns an awaitable.
Emit = Callable[[dict], Awaitable[None]]

# Search-budget meter: 20 searches per cycle (02-architecture.md §Flow).
METER_MAX = 20

# Honest day-4 fallback label — shown while ticketing is not activated.
COMPARISON_MODE_LABEL = "comparison mode \u2014 ticketing not activated"

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

# Canned sandbox passenger manifest for the write path (demo data; real
# traveler docs never live in the repo). order create is fail-closed on
# BOOKING_INPUT_INVALID upstream — the shape is disclosed as demo.
DEMO_PAX_JSON = json.dumps(
    {
        "passengers": [
            {
                "type": "adult",
                "first_name": "Waypoint",
                "last_name": "Demo",
                "gender": "male",
                "birth_date": "1990-01-01",
                "nationality": "SG",
                "document_type": "passport",
                "document_number": "DEMO000001",
                "document_expiry": "2030-01-01",
            }
        ]
    }
)


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

        # Comparison mode = decisions logged + marked, NO write commands.
        # Fail-closed: any doubt about ticketing keeps the desk read-only.
        # Per-cycle cache reset (fix 7): a mid-run ticketing activation
        # takes effect next cycle, and the probe runs at most once here.
        reset_probe = getattr(self.atlas, "reset_ticketing_cache", None)
        if reset_probe is not None:
            reset_probe()
        comparison = await self._comparison_mode()
        mode = COMPARISON_MODE_LABEL if comparison else "live ticketing"

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
            ],
        })

        await emit_step(
            f"Re-read the world \u2014 {len(positions)} positions, "
            f"{len(budgets)} budget lines and the ledger loaded fresh "
            "from the DB"
        )

        # Give-up path (kept from the tracer): if the step budget is
        # exhausted, close honestly instead of pretending work happened.
        if step >= self.step_budget:
            return await self._give_up(desk_id, emit, step)

        # --- deterministic desk math (CODE owns this, never the LLM).
        spent = sum((b.spent for b in budgets), Decimal("0"))
        budget_left = mandate.budget_total - spent
        contingency_left = sum((b.contingency for b in budgets), Decimal("0"))

        held = [p for p in positions if p.status == "held"]
        meter_used = await self._reprice_fan_out(desk_id, emit, held)
        await emit_step(
            f"Repriced {len(held)} positions \u2014 meter at "
            f"{meter_used}/{METER_MAX}; stale marks carry disclosed "
            "uncertainty"
        )
        if step >= self.step_budget:
            return await self._give_up(desk_id, emit, step)

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
            return await self._give_up(desk_id, emit, step)

        # --- EXECUTE WALL + write path, strictly sequential per position.
        # Capture the starting remainders so the settle step can persist
        # exactly what the write path consumed (fix 5): spent = budget
        # delta, contingency delta = absorbed PRICE_CHANGED amounts.
        budget_start = budget_left
        contingency_start = contingency_left
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
                    # Bounded wait expired (or no human reachable): give up
                    # honestly on the whole cycle.
                    return await self._finish(desk_id, emit, DeskResult(
                        desk_id=desk_id, status="escalated",
                        pnl=self._pnl(positions),
                        losses_admitted=losses_admitted, step_count=step,
                        comparison_mode=comparison,
                    ))
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

            if comparison:
                # Comparison mode logs the decision and skips to settle.
                settle.append(LedgerInput(
                    kind="trade", amount=Decimal("0"),
                    position_id=pos.id,
                    note="comparison mode \u2014 decision 'book' logged, "
                         "not executed (no write commands)",
                ))
                continue

            budget_left, contingency_left = await self._write_position(
                desk_id, emit, pos, budget_left, contingency_left, settle,
            )
            if step >= self.step_budget:
                return await self._give_up(desk_id, emit, step)

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

            # order create — WRITE, NEVER retried.
            try:
                ref = await asyncio.to_thread(
                    self.atlas.create_order,
                    verified.booking_id,
                    DEMO_PAX_JSON,
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
            settle.append(LedgerInput(
                kind="trade", amount=verified.current_price,
                position_id=pos.id, ref=ref.order_no,
                note="booked \u2014 TICKETED asserted, sandbox money",
            ))
            # Alloc beat — S4 SEAM: hardcoded ledger-only today; replace with
            # a seat_list probe funded by realized savings when S4 lands.
            settle.append(LedgerInput(
                kind="alloc", amount=Decimal("0"), position_id=pos.id,
                ref="ledger_only",
                note="seat service unavailable \u2014 alloc degraded to "
                     "ledger-only",
            ))
            await emit({
                "type": "alloc",
                "position_id": pos.id,
                "amount": "0",
                "seat_ref": "ledger_only",
                "disclosure": "seat service unavailable \u2014 ledger-only",
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
        self, desk_id: str, emit: Emit, step: int
    ) -> DeskResult:
        """Step budget exceeded → graceful give-up (S1 shape kept)."""
        await emit({
            "type": "step", "n": step + 1,
            "text": "Step budget exhausted \u2014 giving up gracefully",
        })
        return await self._finish(desk_id, emit, DeskResult(
            desk_id=desk_id, status="failed", pnl=Decimal("0"),
            losses_admitted=0, step_count=step,
        ))

    async def _comparison_mode(self) -> bool:
        """The write-path gate. True while ticketing is blocked: decisions
        are logged + marked but NO write commands run (labeled on the
        wire). Fail-closed on any probe absence or error. The auth-status
        probe is a blocking subprocess, so it runs off the event loop via
        asyncio.to_thread (fix 2) — never inline on the loop."""
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
