"""S1 desk endpoints (refit of the tracer's 3-endpoint set).

- POST /api/desk/seed                                       -> seeds mandate + portfolio, starts the desk cycle, returns desk_id
- GET  /api/desk/{desk_id}/stream                           -> SSE of the agent's live cycle (drives Screen 2)
- GET  /api/desk/{desk_id}                                  -> desk state snapshot: positions/ledger/budgets + search meter
- GET  /api/desk/{desk_id}/close                            -> the CloseReport: wrapped DeskResult + code-computed breach count + auditor line (drives Screen 3)
- POST /api/desk/{desk_id}/escalations/{esc_id}/decision    -> the one human click (approve option A/B)

Desk state is held in-memory per desk (single-process dev server); the DB
(mandate/positions/ledger/budgets) is the durable evidence via DeskStore.
The SSE generator buffers + replays so a client that connects after the
agent started still sees every event.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import fixture
from app.agent.auditor import (
    SOURCE_FALLBACK,
    RiskAuditor,
    fallback_challenge,
    plain_challenge,
)
from app.agent.loop import DEFAULT_ESCALATION_WAIT, METER_MAX, DeskAgent
from app.db.store import DeskStore
from app.models import CloseReport, DeskResult

router = APIRouter(prefix="/api", tags=["waypoint"])

CLOSE_WAIT_SECONDS = 60.0

# Outer belt over the auditor's own bounded wait — narration must never
# turn a finished close into a 500 or a long hang.
AUDITOR_WAIT_SECONDS = 8.0

# One-flag scenario injection (S7, DECISION 1). Strict parse, INVERTED
# default vs WAYPOINT_LIVE_BOOKING: armed unless the env reads EXACTLY
# "0" — unset or any other value keeps the scripted loss+spike scenario
# ON (the demo default).
INJECT_SCENARIO_ENV = "WAYPOINT_INJECT_SCENARIO"

# S10 (ADR 0007): the escalation click wait, env-overridable. In the
# recorded container nobody is there to click, and the 300s demo default
# would stretch every boot-seeded cycle past five minutes; compose sets a
# short wait so the cycle expires the escalation and gives up gracefully
# (loop's bounded-wait path — fail closed). Unset / unparsable / negative
# keeps the exact demo default.
ESCALATION_WAIT_ENV = "WAYPOINT_ESCALATION_WAIT"


def _escalation_wait() -> float:
    raw = os.environ.get(ESCALATION_WAIT_ENV)
    if raw is None:
        return DEFAULT_ESCALATION_WAIT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_ESCALATION_WAIT
    if not math.isfinite(value) or value < 0.0:
        return DEFAULT_ESCALATION_WAIT
    return value

# HUMAN_WAIVER_MARKER — honesty register (S7 review ruling):
# A blotter trade row carrying this marker in its note is treated as
# authorized by the one human escalation click — an over-cap trade WITH
# it is no breach. The honest state of this hook today:
#
# 1. FORWARD-COMPAT ONLY. The write path (loop.py, frozen since S6) does
#    NOT yet emit this marker in any ledger note, so the exclusion branch
#    below never fires today. It exists so a future slice can light it up.
# 2. KNOWN-FRAGILE INTERIM HOOK. Substring-matching a free-text note
#    violates the discipline this codebase enforces everywhere else
#    (atlas/client.py: branch on structured code, NEVER on message text).
#    Rewording a note someday would silently break the breach count.
# 3. FOR THE FUTURE LIVE-BOOKING SLICE: the correct fix is a STRUCTURED
#    waiver field on the ledger row (a real column/flag), and
#    count_policy_breaches must branch on THAT — never on note text. Do
#    NOT "complete" the note-prefix/substring approach later.
HUMAN_WAIVER_MARKER = "human waiver"

# One store + one agent for the process. AGENT must stay a module-level
# attribute so tests can monkeypatch it (stub-client DI).
STORE = DeskStore()

# ---- Waybot invite gate (S1) --------------------------------------------
# The deep-link token is desk-scoped and single-purpose (binds chat->desk;
# it can NOT release — release needs the code). 16 random bytes -> a
# ~22-char URL-safe [A-Za-z0-9_-] string, well under Telegram's 64-char
# deep-link limit. The confirmation code is a short human string the
# manager types; only a salted hash is stored (plaintext never persisted).
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _new_invite_token() -> str:
    token = secrets.token_urlsafe(16)
    # Guard the contract at the source: URL-safe alphabet, <=64 chars.
    assert _TOKEN_RE.fullmatch(token), f"non-url-safe token: {token!r}"
    return token


def _new_confirmation_code() -> str:
    """A short, unambiguous release code (8 hex chars, uppercased)."""
    return secrets.token_hex(4).upper()


def _hash_code(code: str) -> str:
    """Salted SHA-256 of the code -> 'salt$digest'. Plaintext never stored."""
    salt = secrets.token_hex(16)
    digest = hashlib.sha256(f"{salt}{code}".encode()).hexdigest()
    return f"{salt}${digest}"


def _verify_code(code: str, stored: str | None) -> bool:
    """Constant-time check of a plaintext code against the stored hash."""
    if not stored or "$" not in stored:
        return False
    salt, digest = stored.split("$", 1)
    candidate = hashlib.sha256(f"{salt}{code}".encode()).hexdigest()
    return hmac.compare_digest(candidate, digest)


def _start_cycle(desk_id: str) -> DeskState:
    """THE shared resume primitive: register a DeskState and fire the cycle
    task (the pre-S1 two-liner, extracted). Persistence has already
    preceded this, so the cycle's first re-read of the world finds its
    data. Retains the task handle on DeskState so it isn't GC'd mid-flight.
    Called by the ungated seed path AND by confirm (and later approve)."""
    state = DeskState(desk_id=desk_id)
    DESKS[desk_id] = state
    state.task = asyncio.create_task(_run_desk(state))
    return state


def build_atlas():
    """The ONE Atlas-rail seam (S9, ADR 0005): branch on the strict
    WAYPOINT_ATLAS_MODE parse — ONLY exact "recorded" selects the replay
    client; unset/typo/anything else keeps the live AtlasClient (today's
    behavior). Money safety never rests on this switch: it rests on the
    two fail-closed write gates (WAYPOINT_LIVE_BOOKING + ticketing_live),
    so fail-to-live cannot endanger money."""
    from app.atlas.config import read_atlas_mode  # local: keep DI light

    if read_atlas_mode() == "recorded":
        from app.atlas.recorded import RecordedAtlasClient

        return RecordedAtlasClient()
    from app.atlas.client import AtlasClient

    return AtlasClient()


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

# One cycle at a time, process-wide (ADR 0005's single-active-cycle
# determinism guarantee): the recorded replay cursor is per-client state,
# so two interleaved cycles would serve each other's envelopes — the lock
# serializes every desk cycle.
CYCLE_LOCK = asyncio.Lock()


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
    atlas=build_atlas(),
    store=STORE,
    escalation_slot=_register_escalation,
    escalation_clear=_clear_escalation,
    meter_report=_report_meter,
    escalation_wait=_escalation_wait(),
)

# The S7 risk auditor — routes-layer placement (DECISION 4): it runs ONCE
# at close time on the settled blotter, never inside the cycle loop, so
# DeskAgent gets no new param. Module-level so tests can monkeypatch it
# (same pattern as AGENT).
AUDITOR = RiskAuditor()


def count_policy_breaches(
    authority_cap: Decimal, ledger_tail: list[dict]
) -> int:
    """Deterministic authority-cap breach count — PURE code (ADR 0003/0004;
    never an LLM verdict). Scans the blotter's `trade` rows: any executed
    amount above the mandate's authority cap WITHOUT a human-waiver marker
    is a breach.

    Honesty register:
    - Window: `ledger_tail` is the NEWEST LEDGER_TAIL_LIMIT (50) rows from
      `DeskStore.reload_desk` — an over-cap trade row pushed out of that
      window would not be counted. Unreachable for a one-cycle demo
      blotter, but stated explicitly rather than implied.
    - The waiver exemption (HUMAN_WAIVER_MARKER substring on the note) is
      FORWARD-COMPAT ONLY today: loop.py (frozen since S6) never emits the
      marker, and substring-matching free text is a known-fragile interim
      hook — the future live-booking slice must replace it with a
      structured waiver field on the ledger row (see the constant's note).
    - In comparison mode (the demo default) nothing books over cap, so the
      count is structurally 0 — but it is genuinely scanned off the
      blotter data here, never hardcoded."""
    breaches = 0
    for row in ledger_tail:
        if row.get("kind") != "trade":
            continue
        amount = row.get("amount")
        if amount is None or amount <= authority_cap:
            continue
        if HUMAN_WAIVER_MARKER in (row.get("note") or ""):
            continue  # forward-compat hook — see HUMAN_WAIVER_MARKER note
        breaches += 1
    return breaches


async def _emit(state: DeskState, event: dict) -> None:
    """Append one event to the desk's buffer and wake any waiting stream."""
    async with state.cond:
        state.events.append(event)
        state.cond.notify_all()


async def _run_desk(state: DeskState) -> None:
    """Run the agent, then mark the desk done (with result or an error event)."""
    try:
        async with CYCLE_LOCK:  # ADR 0005: single-active-cycle determinism
            result = await AGENT.run(
                state.desk_id, lambda ev: _emit(state, ev)
            )
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


def _inject_scenario_armed() -> bool:
    """Strict env parse (WAYPOINT_LIVE_BOOKING precedent, inverted default):
    the scripted loss+spike scenario is armed UNLESS the value reads
    exactly "0" — unset or anything else stays armed (demo default)."""
    return os.environ.get(INJECT_SCENARIO_ENV) != "0"


class SeedRequest(BaseModel):
    """Ops-manager budget constraints for the seed. Every field defaults to
    the historical hardcoded demo value, so an absent/partial body seeds
    exactly as before. contingency_pct is a FRACTION (0.05 == 5%); the
    bounds mirror the form's declared input ranges — money values must be
    positive, contingency caps at 0.25 (the UI's 0–25%)."""

    budget_total: Decimal = Field(default=Decimal("12000.00"), gt=Decimal("0"))
    authority_cap: Decimal = Field(default=Decimal("1500.00"), gt=Decimal("0"))
    contingency_pct: float = Field(default=0.05, ge=0.0, le=0.25)
    # Display-only trip context — free text, persisted and echoed as-is.
    team_size: int = Field(default=1, ge=1, le=50)
    destination_label: str = Field(default="")
    trip_purpose: str = Field(default="")
    # Waybot invite gate (S1). Default False keeps the pre-S1 seed EXACTLY:
    # lifecycle 'released', no token/code, cycle starts immediately. When
    # True, the seed holds the desk in 'awaiting_travelers' with an invite
    # token + hashed release code and does NOT start the cycle — /confirm
    # starts it once the manager enters the code.
    gated: bool = Field(default=False)


class ConfirmRequest(BaseModel):
    """The manager's release code (plaintext, checked against the stored
    hash). Body of POST /desk/{id}/confirm."""

    code: str


@router.post("/desk/seed")
async def seed_desk(request: SeedRequest | None = None) -> dict:
    """Seed the mandate + portfolio (disclosed seeds) and kick off the cycle.

    The desk_id IS the mandate id (one desk per mandate). Persistence lands
    via DeskStore BEFORE the agent task starts, so the cycle's first
    re-read of the world always finds its data. The loss+spike scenario
    injection is one-flag (WAYPOINT_INJECT_SCENARIO; "0" disarms).

    The body is OPTIONAL: no body (or a partial one) falls back to the
    historical defaults, keeping pre-constraint callers byte-compatible.

    Invite gate (S1): `gated=False` (default) is the pre-S1 path EXACTLY —
    lifecycle 'released', no token/code, cycle started here. `gated=True`
    holds the desk in 'awaiting_travelers' with an invite token + hashed
    release code and does NOT start the cycle (the /confirm route starts
    it), returning the token + one-time plaintext code alongside the id.
    """
    req = request or SeedRequest()
    mandate, positions, budgets = fixture.seeded_portfolio(
        inject_scenario=_inject_scenario_armed(),
        budget_total=req.budget_total,
        authority_cap=req.authority_cap,
        contingency_pct=req.contingency_pct,
        team_size=req.team_size,
        destination_label=req.destination_label,
        trip_purpose=req.trip_purpose,
    )

    if not req.gated:
        # Pre-S1 path, byte-unchanged: persist 'released', start the cycle,
        # return only the desk_id.
        desk_id = await asyncio.to_thread(
            STORE.seed_desk, mandate, positions, budgets
        )
        _start_cycle(desk_id)
        return {"desk_id": desk_id}

    # Gated path: hold in 'awaiting_travelers' with token + code hash; do
    # NOT start the cycle. The plaintext code is returned exactly once —
    # only its hash is stored.
    invite_token = _new_invite_token()
    confirmation_code = _new_confirmation_code()
    desk_id = await asyncio.to_thread(
        STORE.seed_desk,
        mandate,
        positions,
        budgets,
        "awaiting_travelers",
        invite_token,
        _hash_code(confirmation_code),
    )
    return {
        "desk_id": desk_id,
        "invite_token": invite_token,
        "confirmation_code": confirmation_code,
    }


@router.post("/desk/{desk_id}/confirm")
async def confirm(desk_id: str, body: ConfirmRequest) -> dict:
    """Release a gated desk: check the plaintext code against the stored
    hash (constant-time). Wrong code -> 403, no state change. Right code
    -> lifecycle 'released' + start the cycle via the shared resume
    primitive. Unknown desk -> 404; an already-released desk -> 409 (the
    gate is single-use in spirit — the cycle is already running)."""
    try:
        lifecycle = await asyncio.to_thread(STORE.get_lifecycle, desk_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown desk")
    if lifecycle != "awaiting_travelers":
        # Already released/closed — nothing to confirm; do not restart.
        raise HTTPException(status_code=409, detail="desk not awaiting confirm")

    _token, code_hash = await asyncio.to_thread(STORE.get_invite, desk_id)
    if not _verify_code(body.code, code_hash):
        raise HTTPException(status_code=403, detail="wrong code")

    # Atomic release (H1): the lifecycle flip IS the race guard. Two
    # concurrent correct-code confirms both pass the checks above, but the
    # CAS lets exactly one win — only the winner starts the cycle, so
    # DESKS is never overwritten and no second _run_desk (double booking)
    # is spawned. The loser is treated as a late/duplicate confirm (409).
    released = await asyncio.to_thread(STORE.try_release, desk_id)
    if not released:
        raise HTTPException(status_code=409, detail="desk not awaiting confirm")
    _start_cycle(desk_id)
    return {"desk_id": desk_id, "lifecycle": "released"}


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
    """Desk state snapshot: positions/ledger/budgets + lifecycle + meter.

    A gated desk in 'awaiting_travelers' has no live DeskState yet (the
    cycle hasn't started), so fall back to a persisted-only snapshot with
    a zeroed meter and done=False. This lets the share card / code-entry
    panel render before release without a 404. An unknown desk still 404s.
    """
    state = DESKS.get(desk_id)
    try:
        snapshot = await asyncio.to_thread(STORE.desk_state, desk_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown desk")
    if state is not None:
        snapshot["meter"] = {"used": state.meter_used, "max": METER_MAX}
        snapshot["done"] = state.done
    else:
        snapshot["meter"] = {"used": 0, "max": METER_MAX}
        snapshot["done"] = False
    return snapshot


@router.get("/desk/{desk_id}/close")
async def close(desk_id: str) -> CloseReport:
    """Await completion (bounded), then return the S7 weekly-close report:
    the bare DeskResult wrapped with the code-computed policy-breach count
    and the risk auditor's one-line challenge. 504/500/404 semantics are
    unchanged; the auditor is narration only — its failure degrades to a
    deterministic line and NEVER turns a close into an error."""
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
    # Re-read the settled blotter fresh (ONE transaction) for the audit.
    mandate, positions, _budgets, ledger_tail = await asyncio.to_thread(
        STORE.reload_desk, desk_id
    )
    policy_breaches = count_policy_breaches(mandate.authority_cap, ledger_tail)
    # The auditor owns narration only. Bounded twice over (its own ~6s
    # timeout plus this outer wait) and wrapped so any exception/timeout
    # degrades to the deterministic challenge — never a 500.
    try:
        line, source = await asyncio.wait_for(
            AUDITOR.read(mandate, positions, ledger_tail, policy_breaches),
            timeout=AUDITOR_WAIT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — degrade, never crash the close
        line = fallback_challenge(positions, policy_breaches)
        source = SOURCE_FALLBACK
    # Task #8: plain-English twin of the auditor line, built IN CODE from
    # the same structured blotter facts (never parsed out of `line`).
    # Defensive degrade to None — the frontend falls back to the verbatim
    # line, so a builder hiccup can never turn a close into an error.
    try:
        plain = plain_challenge(mandate, positions, ledger_tail, policy_breaches)
    except Exception:  # noqa: BLE001 — degrade, never crash the close
        plain = None
    return CloseReport(
        result=state.result,
        policy_breaches=policy_breaches,
        auditor_line=line,
        auditor_source=source,
        auditor_plain=plain,
    )


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
