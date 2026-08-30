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
import json
import math
import os
import re
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import fixture
from app.approval import apply_decision
from app.codes import KDF_ITERATIONS, hash_code, verify_code
from app.config import int_env
from app.agent.auditor import (
    SOURCE_FALLBACK,
    RiskAuditor,
    fallback_challenge,
    plain_challenge,
)
from app.agent.loop import DEFAULT_ESCALATION_WAIT, METER_MAX, DeskAgent
from app.db.store import DeskStore
from app.events import SINK
from app.models import Budget, CloseReport, DeskResult, Mandate, Position

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


# The release-credential KDF now lives in app/codes.py (S5): the agent
# loop mints a SECOND manager credential — the per-round approval token —
# and it cannot import this module (routes imports the loop). Behavior is
# unchanged; these private aliases keep every existing call site and the
# S4 security tests (`routes._hash_code` / `routes._verify_code` /
# `routes._KDF_ITERATIONS`) pointing at the same functions.
_KDF_ITERATIONS = KDF_ITERATIONS
_hash_code = hash_code
_verify_code = verify_code


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
    # G4 (S5): the loop announces pending_approval / pinned_resume on the
    # one in-process sink the Waybot subscribes to.
    sink=SINK,
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


class ApproveRequest(BaseModel):
    """The manager's pre-trip Approve/Hold, body of POST /desk/{id}/approve.

    `code` is the MANAGER credential and mirrors /confirm exactly — role
    separation is enforced by holding a secret a traveler never receives.
    Two values verify, both manager-only:
      - the desk's release code (what the manager typed at /confirm), or
      - the per-round approval token, which rides the pending_approval
        event into the manager's Telegram chat and nowhere else.
    A bot-path/traveler identity holds neither (the invite token is a
    different value and verifies against neither hash), so it gets a 403.
    """

    choice: Literal["approve", "hold"]
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
    # L2: PBKDF2 (260k rounds) is ~50-150ms of CPU — run it in a thread so
    # the single-worker event loop (and its live SSE streams) never stalls.
    code_hash = await asyncio.to_thread(_hash_code, confirmation_code)
    desk_id = await asyncio.to_thread(
        STORE.seed_desk,
        mandate,
        positions,
        budgets,
        "awaiting_travelers",
        invite_token,
        code_hash,
    )
    return {
        "desk_id": desk_id,
        "invite_token": invite_token,
        "confirmation_code": confirmation_code,
    }


# Fail-closed dev gate (INJECT_SCENARIO_ENV precedent): the debug seed
# below only exists when this reads exactly "1" — unset or anything else
# is a 404, never reachable in a normal deploy or linked from the UI.
DEBUG_SEED_ENV = "WAYPOINT_ALLOW_DEBUG_SEED"


def _debug_seed_armed() -> bool:
    return os.environ.get(DEBUG_SEED_ENV) == "1"


@router.post("/desk/seed-demo-single")
async def seed_demo_single(request: SeedRequest | None = None) -> dict:
    """DEV-ONLY: seed exactly ONE position — DUR->CPT, 1 adult, 2026-09-20
    — matching `backend/data/recorded/booking_envelopes.json`'s genuinely
    captured TICKETED sandbox ticket (order TESTA20260830223723623, PNR
    S22178) verbatim.

    Exists for a clean recorded-mode demo: the real 6-position portfolio
    (`/desk/seed`) fans one search per position, but the recording only
    carries ONE scripted search, so five of six positions fail closed to
    a disclosed stale mark (by design, not a bug — see
    docs/external/atlas-integration.md). One position means one search,
    one clean reprice, no stale-mark noise.

    `cost_basis` is set below the capture's $64.11 fare on purpose, so
    the fallback brain's book/lock branch fires and the write path
    (verify -> create -> pay -> order status) runs end-to-end against the
    same single scripted envelope per verb, ending in a genuine TICKETED.

    404s unless WAYPOINT_ALLOW_DEBUG_SEED=1. Never wired into the seed
    form; hit it directly (curl / Postman) for demo prep, then open
    /desk/{desk_id} in the browser.
    """
    if not _debug_seed_armed():
        raise HTTPException(status_code=404, detail="not found")

    req = request or SeedRequest()
    desk_id = f"desk-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    mandate = Mandate(
        id=desk_id,
        holder="Waypoint Debug Seed — DUR-CPT capture",
        team_size=1,
        destination_label="Cape Town, South Africa",
        trip_purpose="Demo — matches the recorded sandbox ticket",
        created_at=now,
        budget_total=req.budget_total,
        authority_cap=req.authority_cap,
        contingency_pct=req.contingency_pct,
        currency="USD",
    )
    position = Position(
        id=f"{desk_id}-pos-1",
        trip_label="Durban → Cape Town (recorded capture)",
        origin="DUR",
        dest="CPT",
        depart_date=date(2026, 9, 20),
        pax=1,
        status="held",
        # Below the capture's real $64.11 fare on purpose (see docstring):
        # trips the fallback brain's "book" branch, not "hold".
        cost_basis=Decimal("50.00"),
        mark_price=Decimal("50.00"),
        mark_at=now,
        mark_stale=False,
    )
    cents = Decimal("0.01")
    pct = Decimal(str(req.contingency_pct))
    budget = Budget(
        desk_id=desk_id, period="2026-W38",
        allocated=req.budget_total,
        contingency=(req.budget_total * pct).quantize(cents),
    )

    seeded_id = await asyncio.to_thread(
        STORE.seed_desk, mandate, [position], [budget]
    )
    _start_cycle(seeded_id)
    return {"desk_id": seeded_id}


# Shared tolerant int env read (app.config, M-new2 consolidation): a
# malformed OR below-minimum override falls back to the default instead of
# crashing app import (config-typo DoS) or disabling the guard it tunes.

# Confirmation-code attempt cap (S4 guard 1). ENV-tunable. minimum=1: a
# negative/zero override falls back to 5 rather than disabling the throttle.
CODE_ATTEMPT_CAP = int_env("WAYPOINT_CODE_ATTEMPT_CAP", 5, minimum=1)
# Confirmation-code TTL in seconds (S4 guard 1): how long after seed a code
# stays valid, anchored to mandate.created_at (== code issue time at seed).
# 0 = no TTL (dev). Default 24h (H2): roster collection over Telegram can
# realistically exceed an hour, and there is no reissue path, so a code that
# died at 1h would strand the desk in awaiting_travelers permanently.
CODE_TTL_SECONDS = int_env("WAYPOINT_CODE_TTL", 86400, minimum=0)

# H-new1: BOUNDED KDF concurrency for /confirm. Chosen: a dedicated small
# ThreadPoolExecutor over a module-level asyncio.Semaphore. The Semaphore
# was rejected because asyncio primitives bind to the first event loop that
# awaits them and then RAISE on any other loop — a module-level Semaphore
# would break across TestClient loops/process restarts. The executor is
# loop-agnostic, and its internal queue bounds concurrency to 2 verify KDFs
# at a time WITHOUT ever rejecting — excess confirms simply queue, so the
# correct-code-always-releases contract is preserved (throttle = queue,
# never refusal). A /confirm flood now pins at most 2 threads, not the
# whole default executor of the single-worker app.
_KDF_VERIFY_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="kdf-verify"
)

# ---- Sliding-window request-volume limiter on /confirm (task #8) --------
# Request-VOLUME layer, keyed by desk_id — distinct from the attempt cap
# above (which throttles wrong-CODE volume). Semantics, honestly stated:
#
# - BURST GUARD, NOT AN AUTH LAYER. It limits how many /confirm requests
#   one desk_id may consume per window; it never authenticates anything.
# - TRANSIENT AND BOUNDED: a desk under flood may delay even a CORRECT
#   code by at most one window (default 60s). NEVER permanent — unlike a
#   lockout, the throttle fully clears as old timestamps slide out.
# - FIRST CHECK in /confirm: a throttled request never reaches the TTL
#   check or the KDF, so a flood burns zero KDF CPU and zero DB writes.
# - EXACT under this deployment: one pinned uvicorn worker (--workers 1 in
#   both Dockerfiles) means per-process memory IS the global state. If the
#   app is ever scaled to N workers, the limit degrades to limit×instances
#   (each worker keeps its own window) — still a throttle, never a bypass.
# - MEMORY HYGIENE: expired timestamps are pruned on every access, and a
#   desk whose deque empties drops its dict entry (lazy eviction), so the
#   map only ever holds desks active within the last window.
# - The time source is a module attribute so tests can monkeypatch it and
#   slide the window without sleeping (same DI pattern as AGENT/AUDITOR).
CONFIRM_RATE_WINDOW_SECONDS = 60.0
CONFIRM_RATE_LIMIT_CAP = int_env("WAYPOINT_CONFIRM_RATE_LIMIT", 10, minimum=1)

_CONFIRM_HITS: dict[str, deque] = {}
# Hygiene lock: the route body runs single-threaded on the 1-worker event
# loop, but the KDF verify path runs in executor threads — keep mutations
# guarded anyway.
_CONFIRM_HITS_LOCK = threading.Lock()

# Time source for the window. monotonic: immune to wall-clock jumps.
_confirm_clock = time.monotonic


def _confirm_allowed(desk_id: str) -> bool:
    """Sliding-window admission for /confirm. True + recorded timestamp =
    proceed; False = the desk already consumed CONFIRM_RATE_LIMIT_CAP
    requests inside CONFIRM_RATE_WINDOW_SECONDS — answer 429 without
    recording (throttled requests must not extend their own throttle)."""
    now = _confirm_clock()
    cutoff = now - CONFIRM_RATE_WINDOW_SECONDS
    with _CONFIRM_HITS_LOCK:
        hits = _CONFIRM_HITS.get(desk_id)
        if hits is not None:
            # Prune expired timestamps on EVERY access.
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                # Lazy eviction: the window drained -> drop the dict entry so
                # the map only holds desks active within the last window.
                del _CONFIRM_HITS[desk_id]
                hits = None
        if hits is None:
            hits = deque()
        if len(hits) >= CONFIRM_RATE_LIMIT_CAP:
            return False
        hits.append(now)
        _CONFIRM_HITS[desk_id] = hits
        return True


@router.post("/desk/{desk_id}/confirm")
async def confirm(desk_id: str, body: ConfirmRequest) -> dict:
    """Release a gated desk: check the plaintext code against the stored
    hash (constant-time). Wrong code -> 403, attempt counter bumped. The
    correct code -> lifecycle 'released' + start the cycle via the shared
    resume primitive. Unknown desk -> 404; an already-released desk -> 410
    (one-shot semantics — the gate fires exactly once). Code past TTL -> 410
    (expired).

    Request-volume throttle (task #8): the FIRST check — before the
    lifecycle read, the TTL check, and the KDF. If this desk_id already
    consumed its window of confirms, answer 429 immediately (distinct
    from the wrong-attempt 429 below). Burst guard, not an auth layer:
    transient for at most one window, never permanent — see the limiter
    comment block above the route.

    Attempt cap (S4 guard 1) — the throttle targets GUESSERS, not the
    code-holder: 5 wrong codes are 403, the 6th is 429. But the check is
    verify-FIRST, so the correct code ALWAYS releases regardless of how many
    wrong attempts preceded it. This closes the DoS where an attacker who
    only knows the shared desk_id could spam wrong codes and permanently
    brick the release gate (there is no reissue endpoint to unlock it)."""
    if not _confirm_allowed(desk_id):
        raise HTTPException(
            status_code=429, detail="rate limited — try again shortly"
        )

    try:
        lifecycle = await asyncio.to_thread(STORE.get_lifecycle, desk_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown desk")
    if lifecycle != "awaiting_travelers":
        # Already released/closed — one-shot semantics (L7 reconciled to 410).
        raise HTTPException(status_code=410, detail="desk already released")

    # TTL check (S4 guard 1): the code expires CODE_TTL_SECONDS after seed.
    if CODE_TTL_SECONDS > 0:
        from datetime import datetime, timezone

        mandate_row = await asyncio.to_thread(
            lambda: STORE.reload_desk(desk_id)[0]
        )
        created = mandate_row.created_at
        # SQLite may return timezone-naive datetimes; coerce to UTC.
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age > CODE_TTL_SECONDS:
            raise HTTPException(status_code=410, detail="code expired")

    # Verify FIRST (L2: PBKDF2 off the event loop, on the BOUNDED KDF
    # executor — H-new1 — so a /confirm flood can't starve the app). The
    # correct code is never blocked by the attempt cap — only wrong guesses
    # are throttled.
    _token, code_hash = await asyncio.to_thread(STORE.get_invite, desk_id)
    correct = await asyncio.get_running_loop().run_in_executor(
        _KDF_VERIFY_EXECUTOR, _verify_code, body.code, code_hash
    )
    if not correct:
        # H-new1: read the counter FIRST — if it is already at/over the cap,
        # answer 429 WITHOUT the bump UPDATE, so a /confirm flood stops
        # producing one DB write per attack request. Verify-first ordering
        # above still lets a correct code release at any time.
        attempts = await asyncio.to_thread(STORE.get_code_attempts, desk_id)
        if attempts >= CODE_ATTEMPT_CAP:
            raise HTTPException(
                status_code=429, detail="too many wrong attempts"
            )
        # Bump the guesser's counter atomically; the 6th wrong try -> 429.
        attempts = await asyncio.to_thread(STORE.bump_code_attempts, desk_id)
        if attempts > CODE_ATTEMPT_CAP:
            raise HTTPException(
                status_code=429, detail="too many wrong attempts"
            )
        raise HTTPException(status_code=403, detail="wrong code")

    # Atomic release (H1 double-start race): the lifecycle flip IS the race guard. Two
    # concurrent correct-code confirms both pass the checks above, but the
    # CAS lets exactly one win — only the winner starts the cycle, so
    # DESKS is never overwritten and no second _run_desk (double booking)
    # is spawned. The loser gets 410 (one-shot — already released).
    released = await asyncio.to_thread(STORE.try_release, desk_id)
    if not released:
        raise HTTPException(status_code=410, detail="desk already released")
    _start_cycle(desk_id)
    return {"desk_id": desk_id, "lifecycle": "released"}


@router.post("/desk/{desk_id}/approve")
async def approve(desk_id: str, body: ApproveRequest) -> dict:
    """G4 pre-trip approval: the manager signs off the priced itinerary the
    cycle stopped on, or holds it.

    Shape mirrors /confirm deliberately:
    - unknown desk -> 404;
    - a desk that is not `pending_approval` -> 410 (ONE-SHOT: a second
      approve on the same slot always lands here, because the first one's
      compare-and-set already moved the lifecycle — same semantics as an
      escalation slot);
    - wrong/absent manager credential -> 403 (role separation: a traveler
      holds the invite token, which verifies against neither hash);
    - approve -> ledger note + lifecycle `released` + `_start_cycle`, THE
      shared resume primitive, and the resumed cycle runs the offer
      PINNED (no re-judgment; the execute wall's invariants still fire).
    - hold -> ledger note + lifecycle `released` with the pin DROPPED. The
      write is skipped and the position is judged normally whenever a
      cycle next runs; hold deliberately does NOT start one, since that
      would immediately re-ask for the approval just declined.

    No attempt cap / rate limiter here: unlike /confirm this route is only
    reachable inside the narrow `pending_approval` window, the credential
    is single-round, and the very first successful call closes the slot.

    L3: the approval token is PER-ROUND. When it is the credential that
    verified, the approval state is re-read immediately before the
    decision and the hash must still match — a new approval round opened
    in between mints a new token and supersedes this one (410). The
    release-code path is exempt: it is not round-scoped.
    """
    try:
        lifecycle = await asyncio.to_thread(STORE.get_lifecycle, desk_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown desk")
    if lifecycle != "pending_approval":
        raise HTTPException(
            status_code=410, detail="no approval is pending on this desk"
        )

    approval = await asyncio.to_thread(STORE.get_approval, desk_id)
    _token, code_hash = await asyncio.to_thread(STORE.get_invite, desk_id)
    loop = asyncio.get_running_loop()
    # PBKDF2 off the event loop, on the SAME bounded executor /confirm uses
    # (H-new1) so an approve flood cannot starve the app either.
    authorized = await loop.run_in_executor(
        _KDF_VERIFY_EXECUTOR, _verify_code, body.code, code_hash
    )
    via_token = False
    if not authorized and approval.approval_token_hash:
        via_token = await loop.run_in_executor(
            _KDF_VERIFY_EXECUTOR,
            _verify_code,
            body.code,
            approval.approval_token_hash,
        )
        authorized = authorized or via_token
    if not authorized:
        raise HTTPException(status_code=403, detail="not authorized to approve")

    if via_token:
        # L3 cross-round TOCTOU: between the hash read above and this
        # decision, a new approval round may have opened (new pin, new
        # token). Re-read and require the SAME hash to still be pinned;
        # otherwise this token belongs to a superseded round -> 410.
        fresh = await asyncio.to_thread(STORE.get_approval, desk_id)
        if fresh.approval_token_hash != approval.approval_token_hash:
            raise HTTPException(
                status_code=410,
                detail="approval round superseded \u2014 re-fetch the latest",
            )

    outcome = await apply_decision(STORE, desk_id, body.choice)
    if outcome == "gone":
        # Lost the compare-and-set: another caller already decided this slot.
        raise HTTPException(
            status_code=410, detail="approval already decided"
        )
    resumed = outcome == "approved"
    if resumed:
        _start_cycle(desk_id)
    return {
        "desk_id": desk_id,
        "choice": body.choice,
        "lifecycle": "released",
        "resumed": resumed,
    }


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
