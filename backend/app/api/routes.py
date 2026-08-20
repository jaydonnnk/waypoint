"""Slice-1 endpoints (the handoff's exact 3-endpoint set).

- POST /api/disruptions          -> seeds the disrupted trip, starts recovery, returns trip_id
- GET  /api/trips/{id}/stream    -> SSE of the agent's live steps (drives Screen 2)
- GET  /api/trips/{id}/recovery  -> the final RecoveryResult (drives Screen 3)

State is held in-memory per trip (single-process dev server). Persistence
to SQLite is Slice 6. The SSE generator buffers + replays so a client that
connects after the agent started still sees every event.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent.loop import RecoveryAgent
from app.models import RecoveryResult

router = APIRouter(prefix="/api", tags=["waypoint"])

RECOVERY_WAIT_SECONDS = 60.0

# One agent for the process. Step budget is Guard #1 (bounded loop).
AGENT = RecoveryAgent(step_budget=12)


@dataclass
class TripState:
    trip_id: str
    events: list[dict] = field(default_factory=list)
    result: RecoveryResult | None = None
    done: bool = False
    # Condition gates event append/notify and stream replay/tail.
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    # RETAIN the background task handle. A bare asyncio.create_task() result
    # is only weakly referenced by the loop and can be garbage-collected
    # mid-flight if nothing holds it. Keeping it on TripState (which lives in
    # TRIPS for the trip's lifetime) guarantees the recovery runs to completion.
    task: asyncio.Task | None = None


# In-memory trip store, keyed by trip_id.
TRIPS: dict[str, TripState] = {}


async def _emit(state: TripState, event: dict) -> None:
    """Append one event to the trip's buffer and wake any waiting stream."""
    async with state.cond:
        state.events.append(event)
        state.cond.notify_all()


async def _run_recovery(state: TripState) -> None:
    """Run the agent, then mark the trip done (with result or an error event)."""
    try:
        result = await AGENT.run(state.trip_id, lambda ev: _emit(state, ev))
        async with state.cond:
            state.result = result
            state.done = True
            state.cond.notify_all()
    except Exception as exc:  # noqa: BLE001 — surface failures on the stream
        async with state.cond:
            state.events.append({"type": "error", "message": str(exc)})
            state.done = True
            state.cond.notify_all()


def _get_state(trip_id: str) -> TripState:
    state = TRIPS.get(trip_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown trip")
    return state


@router.post("/disruptions")
async def inject_disruption() -> dict:
    """Seed the (already-disrupted) demo trip and kick off recovery.

    Slice 1 folds the cancellation into the seed so Screen 1 matches the
    mockup; Slice 7 feeds this from a real Atlas webhook / injected trigger.
    """
    trip_id = f"trip-{uuid4().hex[:8]}"
    state = TripState(trip_id=trip_id)
    TRIPS[trip_id] = state
    # Retain the handle on TripState so the task isn't GC'd mid-flight.
    state.task = asyncio.create_task(_run_recovery(state))
    return {"trip_id": trip_id}


@router.get("/trips/{trip_id}/stream")
async def stream(trip_id: str) -> StreamingResponse:
    """SSE feed of the agent's live steps. Replays buffered events first."""
    state = _get_state(trip_id)

    async def event_stream():
        sent = 0
        while True:
            async with state.cond:
                # Wait until there's something new to send, or we're done.
                await state.cond.wait_for(
                    lambda: len(state.events) > sent or state.done
                )
                pending = state.events[sent:]
                sent = len(state.events)
                done = state.done
            # Yield outside the lock so client backpressure can't deadlock emit.
            for event in pending:
                yield f"data: {json.dumps(event, default=str)}\n\n"
            if done:
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/trips/{trip_id}/recovery")
async def recovery(trip_id: str) -> RecoveryResult:
    """Await completion (bounded) and return the final RecoveryResult."""
    state = _get_state(trip_id)
    try:
        async with state.cond:
            await asyncio.wait_for(
                state.cond.wait_for(lambda: state.done), RECOVERY_WAIT_SECONDS
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="recovery did not finish in time")
    if state.result is None:
        raise HTTPException(status_code=500, detail="recovery failed")
    return state.result
