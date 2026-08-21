"""DeskAgent — the desk orchestration loop.

S1 body is the minimal HONEST cycle: re-read the world from the store →
emit meta (mandate + 20/20 search meter + comparison-mode disclosure) →
paced step events → terminal result. The judgment + write path land in S3;
nothing is ordered while sandbox ticketing is blocked (comparison mode,
labeled on the wire).

The tracer's stable shape is kept: `run(desk_id, emit) -> DeskResult`,
the step budget (Guard: bounded loop), the paced step emit, and `_finish`
for terminal-result emission incl. the graceful give-up path.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Awaitable, Callable

from app.atlas.client import AtlasClient
from app.db.store import DeskStore
from app.models import DeskResult

# emit takes a JSON-serializable dict event and returns an awaitable.
Emit = Callable[[dict], Awaitable[None]]

# Search-budget meter: 20 searches per cycle (02-architecture.md §Flow).
METER_MAX = 20

# Honest day-4 fallback label — shown while ticketing is not activated.
COMPARISON_MODE_LABEL = "comparison mode \u2014 ticketing not activated"


class DeskAgent:
    def __init__(
        self,
        step_budget: int = 12,
        atlas: AtlasClient | None = None,
        store: DeskStore | None = None,
    ):
        # Guard: bounded loop. Exceeding the budget gives up gracefully.
        self.step_budget = step_budget
        # Injectable for tests; defaults hit the real sandbox / real DB.
        self.atlas = atlas or AtlasClient()
        self.store = store or DeskStore()

    async def run(self, desk_id: str, emit: Emit) -> DeskResult:
        step = 0

        async def emit_step(text: str) -> None:
            nonlocal step
            step += 1
            await asyncio.sleep(0.5)  # paced so the stream reads live
            await emit({"type": "step", "n": step, "text": text})

        # --- GUARD: re-read the world before anything else (never act on
        # --- cached state).
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
        comparison = self._comparison_mode()
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

        # S1 stops after the re-read: the judgment + write path land in S3.
        # Give-up path (kept from the tracer): if the step budget is already
        # exhausted, close honestly instead of pretending work happened.
        if step >= self.step_budget:
            await emit_step("Step budget exhausted \u2014 giving up gracefully")
            return await self._finish(desk_id, emit, DeskResult(
                desk_id=desk_id, status="failed", pnl=Decimal("0"),
                losses_admitted=0, step_count=step,
            ))

        await emit_step(
            "Judgment + write path land in S3 \u2014 cycle closes in "
            f"{mode}"
        )

        return await self._finish(desk_id, emit, DeskResult(
            desk_id=desk_id, status="closed", pnl=Decimal("0"),
            losses_admitted=0, step_count=step, comparison_mode=comparison,
        ))

    def _comparison_mode(self) -> bool:
        """The write-path gate. True while ticketing is blocked: decisions
        are logged + marked but NO write commands run (labeled on the
        wire). Fail-closed on any probe absence or error."""
        probe = getattr(self.atlas, "ticketing_live", None)
        if probe is None:
            return True
        try:
            return not probe()
        except Exception:  # noqa: BLE001 — normalized posture only
            return True

    @staticmethod
    async def _finish(
        desk_id: str, emit: Emit, result: DeskResult
    ) -> DeskResult:
        del desk_id
        await emit({"type": "result", "result": result.model_dump(mode="json")})
        return result
