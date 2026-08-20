"""RecoveryAgent — the orchestration loop.

Slice 1 is a scripted tracer: it emits canned steps (paced so the stream
reads live) and returns the hardcoded RecoveryResult from `fixture`. The
Gate 3 signature — `run(trip_id, emit) -> RecoveryResult` — is real and
stable; Slices 2-5 replace the BODY with
    re-read -> search -> rules -> judge(advise) -> execute-wall -> book -> assert.
The `emit` contract is what drives the SSE stream on Screen 2.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app import fixture
from app.models import RankedDecision, RecoveryResult

# emit takes a JSON-serializable dict event and returns an awaitable.
Emit = Callable[[dict], Awaitable[None]]


class RecoveryAgent:
    def __init__(self, step_budget: int = 12):
        # Guard #1 (guide §26): bounded loop. Slice 6 enforces give-up on exceed.
        self.step_budget = step_budget

    async def run(self, trip_id: str, emit: Emit) -> RecoveryResult:
        await emit({
            "type": "meta",
            "trip_id": trip_id,
            "step_budget": self.step_budget,
        })

        # Canned steps, paced so the stream visibly progresses.
        for n, text in enumerate(fixture.DEMO_STEPS, start=1):
            await asyncio.sleep(0.7)
            await emit({"type": "step", "n": n, "text": text})

        # Advise gate (open): every option is surfaced with its verdict.
        assessments = fixture.demo_assessments()
        await emit({
            "type": "options",
            "assessments": [fixture.assessment_payload(a) for a in assessments],
        })

        await asyncio.sleep(0.9)
        decision = RankedDecision(
            chosen_offer_id="opt-icn",
            rationale=fixture.DEMO_RATIONALE,
        )
        await emit({
            "type": "decision",
            "chosen_offer_id": decision.chosen_offer_id,
            "rationale": decision.rationale,
        })

        await asyncio.sleep(0.6)
        result = fixture.build_result(trip_id, step_count=len(fixture.DEMO_STEPS))
        await emit({"type": "result", "result": result.model_dump(mode="json")})
        return result
