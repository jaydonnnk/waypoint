"""RecoveryAgent — the orchestration loop.

Slice 2: the SEARCH step is real — AtlasClient hits the Atlas sandbox and
the `options` event carries real itineraries. Rules verdicts, the judge
decision, and the booking result stay canned (Slices 3-5), so the flow
still completes end-to-end to Screen 3. The Gate 3 signature —
`run(trip_id, emit) -> RecoveryResult` — is stable; the `emit` contract
drives the SSE stream on Screen 2.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Awaitable, Callable

from app import fixture
from app.atlas.client import AtlasClient, AtlasError, AtlasNoResults
from app.data.loaders import load_iata_city, load_iata_country
from app.models import OfferAssessment, RankedDecision, RecoveryResult

# emit takes a JSON-serializable dict event and returns an awaitable.
Emit = Callable[[dict], Awaitable[None]]

# The demo broken leg (Screen 1's cancelled Scoot SIN->NRT flight).
DEMO_ORIGIN = "SIN"
DEMO_DEST = "NRT"
DEMO_DEP_DATE = date(2026, 9, 4)
DEMO_PAX = 1

# Bookable candidates must carry a live price (ADR: reference = comparison
# only, never executable).
_BOOKABLE_STATUS = ("current", "verified")


class RecoveryAgent:
    def __init__(self, step_budget: int = 12, atlas: AtlasClient | None = None):
        # Guard #1 (guide §26): bounded loop. Slice 6 enforces give-up on exceed.
        self.step_budget = step_budget
        # Injectable for tests; the default hits the real sandbox.
        self.atlas = atlas or AtlasClient()

    async def run(self, trip_id: str, emit: Emit) -> RecoveryResult:
        await emit({
            "type": "meta",
            "trip_id": trip_id,
            "step_budget": self.step_budget,
        })

        iata, cities = load_iata_country(), load_iata_city()
        step = 0

        async def emit_step(text: str) -> None:
            nonlocal step
            step += 1
            await asyncio.sleep(0.5)  # paced so the stream reads live
            await emit({"type": "step", "n": step, "text": text})

        await emit_step(
            f"Re-read trip state \u2014 {DEMO_ORIGIN}\u2192{DEMO_DEST} "
            "(Scoot TR866) is cancelled"
        )
        await emit_step(
            f"Searching Atlas sandbox {DEMO_ORIGIN}\u2192{DEMO_DEST} \u00b7 "
            f"{DEMO_DEP_DATE.isoformat()} \u00b7 {DEMO_PAX} adult\u2026"
        )

        # --- REAL SEARCH (read path only — no verify/order/pay) -----------
        try:
            # The CLI subprocess is synchronous; keep the event loop free.
            offers = await asyncio.to_thread(
                self.atlas.search, DEMO_ORIGIN, DEMO_DEST, DEMO_DEP_DATE, DEMO_PAX
            )
        except AtlasNoResults as exc:
            # route_not_supported / no_flight / sold_out -> clean give-up.
            await emit_step(
                f"Atlas found no flights ({exc.reason or 'no results'}) "
                "\u2014 giving up cleanly"
            )
            return await self._finish(trip_id, emit, RecoveryResult(
                trip_id=trip_id, status="no_legal_option", chosen=None,
                rejected_cheapest=None, order=None, step_count=step,
                rationale=None,
            ))
        except AtlasError as exc:
            await emit_step(
                f"Atlas search failed ({exc.code}) \u2014 giving up cleanly"
            )
            return await self._finish(trip_id, emit, RecoveryResult(
                trip_id=trip_id, status="failed", chosen=None,
                rejected_cheapest=None, order=None, step_count=step,
                rationale=None,
            ))

        # --- Candidate filter: bookable + current/verified fares first ----
        candidates = [
            o for o in offers
            if o.bookable and o.price_status in _BOOKABLE_STATUS
        ]
        comparison_only = False
        if not candidates:
            # Ticketing not activated yet (TICKETING_ACTIVATION_REQUIRED):
            # the sandbox returns comparison-mode offers only. Surface them
            # flagged as reference fares; Slice 5's verify promotes real
            # candidates once ticketing clears.
            candidates = offers
            comparison_only = True
        if not candidates:
            await emit_step("No usable offers returned \u2014 giving up cleanly")
            return await self._finish(trip_id, emit, RecoveryResult(
                trip_id=trip_id, status="no_legal_option", chosen=None,
                rejected_cheapest=None, order=None, step_count=step,
                rationale=None,
            ))

        if comparison_only:
            await emit_step(
                f"Found {len(offers)} options \u2014 ticketing not activated "
                f"yet, surfacing {len(candidates)} comparison fares"
            )
        else:
            await emit_step(
                f"Found {len(offers)} options \u2014 {len(candidates)} bookable "
                "candidates at current fares"
            )

        # --- Advise gate: real offers x CANNED verdicts (rules = Slice 3) -
        assessments = [
            OfferAssessment(
                offer=offer,
                verdicts=fixture.slice2_verdicts(),
                executable=False,  # nothing auto-executable before Slice 3
            )
            for offer in candidates
        ]
        await emit_step(
            "Transit + passport rules canned until Slice 3 \u2014 fail-closed "
            "placeholders"
        )
        await emit({
            "type": "options",
            "assessments": [
                fixture.assessment_payload(a, iata, cities) for a in assessments
            ],
        })

        # --- CANNED decision (Qwen judge = Slice 4): cheapest candidate ---
        # Honest framing: this is the TOP CANDIDATE, not a vetted pick — no
        # rules have run and nothing is booked yet.
        await emit_step("Weighing candidates \u2014 rules + ranking canned until Slices 3-4\u2026")
        chosen = candidates[0]  # offers arrive sorted cheapest-first
        decision = RankedDecision(
            chosen_offer_id=chosen.id,
            rationale=fixture.CANNED_DECISION_NOTE,
        )
        await emit({
            "type": "decision",
            "chosen_offer_id": decision.chosen_offer_id,
            "rationale": decision.rationale,
        })

        # --- No settlement yet (booking = Slice 5). Guard #3: never assert
        # --- a ticket that wasn't issued -> status "pending", order None.
        await emit_step(
            "Top candidate locked \u2014 rules, ranking & booking land in Slices 3-5"
        )
        result = fixture.build_result(trip_id, step_count=step, chosen=chosen)
        return await self._finish(trip_id, emit, result)

    @staticmethod
    async def _finish(
        trip_id: str, emit: Emit, result: RecoveryResult
    ) -> RecoveryResult:
        del trip_id
        await emit({"type": "result", "result": result.model_dump(mode="json")})
        return result
