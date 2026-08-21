"""S1 desk endpoints (refit of the tracer's 3-endpoint set).

- POST /api/desk/seed                                       -> seeds mandate + portfolio, starts the desk cycle, returns desk_id
- GET  /api/desk/{desk_id}/stream                           -> SSE of the agent's live cycle (drives Screen 2)
- GET  /api/desk/{desk_id}                                  -> desk state snapshot: positions/ledger/budgets + search meter
- GET  /api/desk/{desk_id}/close                            -> the final DeskResult (drives Screen 3)
- POST /api/desk/{desk_id}/escalations/{esc_id}/decision    -> the one human click (approve option A/B)

Desk state is held in-memory per desk (single-process dev server); the DB
(mandate/positions/ledger/budgets) is the durable evidence via DeskStore.
The SSE generator buffers + replays so a client that connects after the
agent started still sees every event.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import fixture
from app.agent.loop import METER_MAX, DeskAgent
from app.db.store import DeskStore
from app.models import DeskResult

router = APIRouter(prefix="/api", tags=["waypoint"])

CLOSE_WAIT_SECONDS = 60.0

# One store + one agent for the process. AGENT must stay a module-level
# attribute so tests can monkeypatch it (stub-client DI).
STORE = DeskStore()


@dataclass
class DeskState:
    desk_id: str
    events: list[dict] = field(default_factory=list)
    result: DeskResult | None = None
    done: bool = False
    # Condition gates event append/notify and stream replay/tail.
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    # RETAIN the background task handle. A bare asyncio.create_task() result
    # is only weakly referenced by the loop and can be garbage-collected
    # mid-flight if nothing holds it. Keeping it on DeskState (which lives
    # in DESKS for the desk's lifetime) guarantees the cycle runs to
    # completion.
    task: asyncio.Task | None = None
    # Search-budget meter for the current cycle (20 searches/cycle).
    meter_used: int = 0
    # esc_id -> {"event": asyncio.Event, "choice": "A"|"B"|None}. S3's
    # execute wall registers an escalation here and awaits the human click.
    escalations: dict[str, dict] = field(default_factory=dict)


# In-memory desk registry, keyed by desk_id.
DESKS: dict[str, DeskState] = {}


def _register_escalation(desk_id: str, esc_id: str) -> dict | None:
    """Execute-wall hook: register the asyncio.Event the loop awaits on
    the desk's escalations map. None (unknown desk) = fail closed."""
    state = DESKS.get(desk_id)
    if state is None:
        return None
    slot = {"event": asyncio.Event(), "choice": None}
    state.escalations[esc_id] = slot
    return slot


def _clear_escalation(desk_id: str, esc_id: str) -> None:
    """Slot hygiene (fix 8): drop the escalation once the loop's bounded
    wait timed out or the click landed. A late POST then sees a gone slot
    and gets a 410 instead of a misleading 200."""
    state = DESKS.get(desk_id)
    if state is not None:
        state.escalations.pop(esc_id, None)


def _report_meter(desk_id: str, used: int) -> None:
    """Mirror the cycle's live meter onto DeskState for the snapshot."""
    state = DESKS.get(desk_id)
    if state is not None:
        state.meter_used = used


AGENT = DeskAgent(
    step_budget=12,
    store=STORE,
    escalation_slot=_register_escalation,
    escalation_clear=_clear_escalation,
    meter_report=_report_meter,
)


async def _emit(state: DeskState, event: dict) -> None:
    """Append one event to the desk's buffer and wake any waiting stream."""
    async with state.cond:
        state.events.append(event)
        state.cond.notify_all()


async def _run_desk(state: DeskState) -> None:
    """Run the agent, then mark the desk done (with result or an error event)."""
    try:
        result = await AGENT.run(state.desk_id, lambda ev: _emit(state, ev))
        async with state.cond:
            state.result = result
            state.done = True
            state.cond.notify_all()
    except Exception:  # noqa: BLE001 — surface failures on the stream
        # Code-only error event (fix 3): the raw exception detail stays
        # server-side and never rides the wire.
        async with state.cond:
            state.events.append({"type": "error", "code": "DESK_CYCLE_FAILED"})
            state.done = True
            state.cond.notify_all()


def _get_state(desk_id: str) -> DeskState:
    state = DESKS.get(desk_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown desk")
    return state


class EscalationDecision(BaseModel):
    choice: Literal["A", "B"]


@router.post("/desk/seed")
async def seed_desk() -> dict:
    """Seed the mandate + portfolio (disclosed seeds) and kick off the cycle.

    The desk_id IS the mandate id (one desk per mandate). Persistence lands
    via DeskStore BEFORE the agent task starts, so the cycle's first
    re-read-the-world always finds its data.
    """
    mandate, positions, budgets = fixture.seeded_portfolio()
    desk_id = await asyncio.to_thread(
        STORE.seed_desk, mandate, positions, budgets
    )
    state = DeskState(desk_id=desk_id)
    DESKS[desk_id] = state
    # Retain the handle on DeskState so the task isn't GC'd mid-flight.
    state.task = asyncio.create_task(_run_desk(state))
    return {"desk_id": desk_id}


@router.get("/desk/{desk_id}/stream")
async def stream(desk_id: str) -> StreamingResponse:
    """SSE feed of the desk cycle. Replays buffered events first."""
    state = _get_state(desk_id)

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


@router.get("/desk/{desk_id}")
async def desk_snapshot(desk_id: str) -> dict:
    """Desk state snapshot: positions/ledger/budgets + the search meter."""
    state = _get_state(desk_id)
    snapshot = await asyncio.to_thread(STORE.desk_state, desk_id)
    snapshot["meter"] = {"used": state.meter_used, "max": METER_MAX}
    snapshot["done"] = state.done
    return snapshot


@router.get("/desk/{desk_id}/close")
async def close(desk_id: str) -> DeskResult:
    """Await completion (bounded) and return the final DeskResult."""
    state = _get_state(desk_id)
    try:
        async with state.cond:
            await asyncio.wait_for(
                state.cond.wait_for(lambda: state.done), CLOSE_WAIT_SECONDS
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="desk cycle did not finish in time")
    if state.result is None:
        raise HTTPException(status_code=500, detail="desk cycle failed")
    return state.result


@router.post("/desk/{desk_id}/escalations/{esc_id}/decision")
async def escalation_decision(
    desk_id: str, esc_id: str, decision: EscalationDecision
) -> dict:
    """The one human click: approve option A or B for a pending escalation.

    Stores the choice and signals the asyncio.Event that the execute wall
    awaits. A slot that is absent — never registered, or already timed
    out / consumed (fix 8 slot hygiene) — answers 410 Gone; nothing
    executes on a guess, and a late click never reads as a 200. The desk
    itself being unknown still 404s via `_get_state`.
    """
    state = _get_state(desk_id)
    escalation = state.escalations.get(esc_id)
    if escalation is None:
        raise HTTPException(status_code=410, detail="escalation gone")
    escalation["choice"] = decision.choice
    escalation["event"].set()
    return {"desk_id": desk_id, "esc_id": esc_id, "choice": decision.choice}
