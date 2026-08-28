# Program Design: Waybot — G1–G6 Gap-Closure Program

The decisions the agent would otherwise make silently mid-implementation. Signatures only — no bodies. A human should read these and say "right" or "wrong" in seconds. Anchored to real code read on 2026-08-28.

## Files

### New — `backend/app/bot/`
- `__init__.py` — `build_application(token, sink, store) -> Application | None`; returns None when token is falsy (app runs bot-less).
- `handlers.py` — Telegram update handlers: `/start` deep-link bind, photo ingest, confirm/fix inline callbacks, optional-contact prompts, typed-entry fallback state machine.
- `extract.py` — Qwen-VL passport OCR over the brain.py httpx/DashScope transport pattern. Returns a raw MRZ/field dict; performs NO trust decisions.
- `mrz.py` — ICAO 9303 TD3 check-digit validator + field parse (7-3-1 weights). Pure, deterministic, no I/O. The acceptance gate.
- `notify.py` — the domain-event sink subscriber: turns `travelers_complete | pending_approval | ticketed | disruption | close_summary` into manager/traveler messages.
- `session.py` — per-chat conversation state (which slot, awaiting-photo vs awaiting-typed-field), keyed by `telegram_chat_id`.

### New — backend core
- `backend/app/events.py` — the typed domain-event sink (`DeskEvent`, `EventSink`). In-process pub/sub; the loop publishes, the bot subscribes. The ONE place every announced moment is enumerated.
- `backend/app/pax.py` — the real-traveler pax builder (moves + replaces `_build_demo_pax_json`; keeps a demo fallback for empty rosters so existing tests stand).

### New — tests
- `backend/tests/test_mrz.py` — check-digit vectors (valid + each single-field corruption fails).
- `backend/tests/test_pax_builder.py` — stored travelers zipped with verify traveler_ids; carry-not-invent; duplicate-doc rejection shape.
- `backend/tests/test_desk_lifecycle.py` — seed-without-start; confirm gate; approve-resume-pin; one-reapproval cap.
- `backend/tests/test_waybot_security.py` — the 7 guards (see §Test plan).
- `backend/tests/test_policy_filter.py` — G2 cheapest-among-policy-passing; zero-pass → escalation.

### Changed — backend
- `schema.py` — `MandateRow` gains 5 columns; new `TravelerRow`, `ChatBindingRow`.
- `database.py` — append 3 backfilled mandate columns to `_MANDATE_COLUMN_BACKFILL` (the other 2 — `approved_offer_id`, `policy_json` — are also existing-table columns, so all 5 go through the shim).
- `store.py` — traveler/chat-binding/lifecycle/policy/offer-snapshot methods.
- `api/routes.py` — `SeedRequest` gains policy fields; `seed_desk` stops firing the task; new `/confirm`, `/approve`, `/travelers`; extract the register+create_task two-liner into `_start_cycle(desk_id)` (the shared resume primitive) and call it from confirm and approve.
- `agent/loop.py` — approval checkpoint after judgment; pinned-resume branch; policy passed to search + client-side offer filter; pax builder swap; `ticketed`/`disruption` sink publishes.
- `atlas/client.py` — `search(..., airlines=None)`; `map_offer` keeps `carrier` + `cabin_class` on `Offer`.
- `models.py` — `Offer` gains `carrier`, `cabin_class`; `Segment` gains `carrier` (for per-leg pack render).
- `main.py` — build + start/stop the bot in `lifespan`, gated on `WAYPOINT_BOT_TOKEN`.

### Changed — frontend
- `lib/types.ts` — desk type gains `lifecycle`, `invite_token`, masked `travelers[]`, `verified_count`, `policy`.
- `lib/api.ts` — `confirmDesk`, `approveDesk` fetchers; seed returns token + code.
- `app/page.tsx` — post-seed share card (link + code + progress) instead of navigate.
- `app/desk/[deskId]/page.tsx` — pre-stream code-entry + named-roster panel, gated on `lifecycle === 'awaiting_travelers'`.

## Types & signatures

### Domain events (`app/events.py`)
```python
DeskEventType = Literal[
    "travelers_complete", "pending_approval", "ticketed", "disruption", "close_summary",
    "pinned_resume", "fallback_used",   # provenance/honesty-register events
]

@dataclass(frozen=True)
class DeskEvent:
    type: DeskEventType
    desk_id: str
    payload: dict  # typed per-type by construction; never raw PII (masked upstream)

class EventSink:
    def publish(self, event: DeskEvent) -> None: ...
    def subscribe(self, handler: Callable[[DeskEvent], Awaitable[None]]) -> None: ...
    # In-process fan-out. Delivery is FIRE-AND-FORGET with per-subscriber try/except:
    # a subscriber (bot) raising cannot break the cycle, and vice versa (symmetric isolation).
```

### Schema (`app/db/schema.py`)
```python
class MandateRow(Base):
    # ...existing 10 columns unchanged...
    lifecycle: Mapped[str] = mapped_column(String, default="released")  # awaiting_travelers|released|pending_approval|closed
    invite_token: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    confirmation_code_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_offer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    policy_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reapproval_count: Mapped[int] = mapped_column(Integer, default=0)  # cap 1 (unbookable-pin edge)
    code_attempts: Mapped[int] = mapped_column(Integer, default=0)     # cap 5 → reissue

class TravelerRow(Base):
    __tablename__ = "travelers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    desk_id: Mapped[str] = mapped_column(ForeignKey("mandate.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)
    family_name: Mapped[str]; given_name: Mapped[str]
    gender: Mapped[str]; birthday: Mapped[str]            # "M"/"F"; "YYYY-MM-DD"
    nationality: Mapped[str]                               # ISO-2 (mapped from MRZ ISO-3)
    doc_type: Mapped[str] = mapped_column(default="PP")
    doc_number: Mapped[str]; issuing_country: Mapped[str]; doc_expiry: Mapped[str]
    contact_email: Mapped[str | None]; contact_mobile: Mapped[str | None]
    verified_at: Mapped[datetime]

class ChatBindingRow(Base):
    __tablename__ = "chat_bindings"
    telegram_chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    desk_id: Mapped[str] = mapped_column(ForeignKey("mandate.id"), index=True)
    slot: Mapped[int] = mapped_column(Integer)
```
`reapproval_count` and `code_attempts` are on the existing `mandate` table → they also join `_MANDATE_COLUMN_BACKFILL` (INTEGER NOT NULL DEFAULT 0). Total shim additions: 7 columns.

### MRZ gate (`app/bot/mrz.py`) — pure, the deterministic acceptance
```python
@dataclass(frozen=True)
class MrzFields:
    family_name: str; given_name: str; gender: str; birthday: str
    nationality_iso2: str; doc_number: str; issuing_country: str; doc_expiry: str

def parse_td3(line1: str, line2: str) -> MrzFields | None: ...   # None = structurally invalid
def check_digit(data: str) -> str: ...                            # 7-3-1 weighted mod-10
def validate(fields_raw: dict) -> MrzFields | None: ...           # all 4 check digits pass → fields; else None
# validate() is the ONLY path by which a photo becomes a stored traveler.
# ISO-3 → ISO-2 nationality via app.data.loaders (reuse existing tooling).
# FAIL-CLOSED: ISO-3 not in the curated CSV → validate() returns None → typed-entry fallback
#   (never send an unmapped nationality into a write). The typed fallback ALSO validates
#   nationality against the same curated country CSV — never free text.
#   Extraction quality is recorded: a `fallback_used` provenance event fires when typed entry was used.
```

### Extractor (`app/bot/extract.py`) — untrusted, mirrors brain transport
```python
async def extract_passport(image_bytes: bytes, *, transport=None) -> dict:
    """Qwen-VL over DASHSCOPE_API_KEY + the OpenAI-compat base URL brain.py uses.
    Returns a raw field dict (MRZ lines + parsed guesses). Makes NO trust
    decision — validate() is the gate. Image bytes are never persisted or logged."""
```

### Pax builder (`app/pax.py`) — the G1 write-path swap
```python
def build_pax_json(desk_id: str, verified_travelers: list[dict], store: DeskStore) -> PaxBuild:
    """Real travelers from `store.list_travelers(desk_id)`, ZIPPED with
    verify's traveler_ids (carry, never invent — passenger-input.md).
    Same envelope as the live-proven _build_pax_json; distinct doc per pax.

    FALLBACK IS KEYED ON DESK KIND, NEVER ON DATA PRESENCE (safety):
    - GATED desk (seeded WITH the invite gate) missing/short travelers at
      write time → returns PaxBuild(hold=True). The wall HOLDS + ESCALATES;
      it must NEVER silently book demo identities for a gated desk.
    - UNGATED desk (legacy/recorded, no invite_token) → demo envelope, so
      recorded-mode and existing tests stay byte-safe.
    Returns pax_source = 'collected' | 'demo' as a provenance label."""

@dataclass(frozen=True)
class PaxBuild:
    pax_json: str | None       # None when hold
    pax_source: Literal["collected", "demo"]
    hold: bool = False         # gated desk without a full roster → hold+escalate
```
Call-site change at `loop.py:725`: `_build_demo_pax_json(verified.travelers)` → `build_pax_json(desk_id, verified.travelers, self.store)`; on `.hold`, emit `OFFER_EXPIRED`-style hold + escalation, never write. `pax_source` rides the booking provenance event.

### Store (`app/db/store.py`) additions
```python
def seed_desk(..., lifecycle="awaiting_travelers", invite_token=None, code_hash=None, policy_json=None) -> str: ...
def add_traveler(self, desk_id: str, slot: int, fields: MrzFields, email=None, mobile=None) -> None: ...
def list_travelers(self, desk_id: str) -> list[dict]: ...          # pax builder + roster
def verified_count(self, desk_id: str) -> int: ...
def set_lifecycle(self, desk_id: str, lifecycle: str) -> None: ...
def get_lifecycle(self, desk_id: str) -> str: ...
def bind_chat(self, chat_id: str, token: str) -> tuple[str, int] | None: ...  # (desk_id, slot) or None
def bump_code_attempts(self, desk_id: str) -> int: ...             # returns new count
def set_approved_offer(self, desk_id: str, offer_id: str) -> None: ...
def bump_reapproval(self, desk_id: str) -> int: ...
def purge_travelers(self, desk_id: str) -> None: ...               # at close
def offer_snapshot(self, desk_id: str) -> dict: ...               # for the G5 pack
```

### Routes (`app/api/routes.py`)
```python
def _start_cycle(desk_id: str) -> None:                            # THE shared resume primitive
    state = DeskState(desk_id=desk_id); DESKS[desk_id] = state
    state.task = asyncio.create_task(_run_desk(state))             # (the current 310-313 two-liner, extracted)

class SeedRequest(BaseModel):
    # ...existing budget/team fields...
    airlines: list[str] = []           # IATA
    cabin: str | None = None
    depart_after: str | None = None    # "HH:MM"
    arrive_by: str | None = None

@router.post("/desk/seed")             # persist awaiting_travelers; NO _start_cycle
async def seed_desk(request: SeedRequest | None = None) -> dict:   # -> {desk_id, invite_token, confirmation_code}

@router.post("/desk/{desk_id}/confirm")
async def confirm(desk_id: str, body: ConfirmRequest) -> dict:     # hash-check; 403 wrong; 410 second call; 429 over attempt cap → reissue; then _start_cycle

@router.post("/desk/{desk_id}/approve")
async def approve(desk_id: str, body: ApproveRequest) -> dict:     # {choice: approve|hold}; approve → ledger note + _start_cycle (pinned resume)

@router.get("/desk/{desk_id}/travelers")
async def travelers(desk_id: str) -> dict:                         # masked roster + verified_count
```

### Atlas (`app/atlas/client.py`, `app/models.py`)
```python
def search(self, origin, dest, dep, pax, airlines: list[str] | None = None) -> list[Offer]: ...  # repeated --airline
class Offer(BaseModel):
    # ...existing...
    carrier: str = ""          # marketing carrier (from raw; today dropped)
    cabin_class: str = ""      # from raw envelope (30 hits); today dropped
class Segment(BaseModel):
    # ...existing...
    carrier: str = ""          # per-leg, for pack render "SQ 32"
```

## Call stack

### Seed → capture → release (G1)
```
page.tsx seed → POST /desk/seed → fixture.seeded_portfolio → STORE.seed_desk(lifecycle=awaiting_travelers, token, code_hash)
  → returns {desk_id, invite_token, confirmation_code}   [NO task started]
Telegram /start?token → handlers.bind → STORE.bind_chat → session=awaiting_photo
photo → handlers.on_photo → extract_passport → mrz.validate
  → ok: masked confirm card → (optional contact) → STORE.add_traveler → bot deleteMessage
  → fail: typed-entry fallback → same mrz.validate gate
STORE.verified_count == team_size → loop/store publishes DeskEvent(travelers_complete) → notify pings manager
manager enters code → POST /desk/{id}/confirm → hash compare → set_lifecycle(released) → _start_cycle
```

### Cycle with approval pin (G4)
```
_run_desk → CYCLE_LOCK → AGENT.run:
  reread → build actions PER POSITION:
      approved positions (approved_offer_id set on the position) → PINNED mark (offer = approved), emit `pinned_resume` provenance
      unapproved positions → judge normally (reprice + brain.judge)
  → ONE execute wall over all actions (pinned + judged compose; the branch lives ONLY in mark construction, never a 2nd wall)
      first book pick on a NORMAL position → set_approved_offer(pos); set_lifecycle(pending_approval)
        publish DeskEvent(pending_approval, itinerary + identity snapshot) → notify Approve/Hold → END cycle (no in-cycle wait)
POST /desk/{id}/approve(approve) → ledger note → set_lifecycle(released) → _start_cycle → pinned position resumes
  wall on the pinned offer (SAME invariants — they fire on the pinned path too):
     price increased within budget/cap → confirm_price (unchanged)
     price move BEYOND contingency/cap → escalation, NOT a silent book
     unbookable (OFFER_EXPIRED) AND reapproval_count<1 → bump_reapproval → judge that position once more → new pending_approval
     unbookable AND reapproval_count>=1 → give_up(hold) + disclose
  → order create (build_pax_json; pax_source label) → pay → poll TICKETED → mark_booked → publish DeskEvent(ticketed, paid+order_no)
Hold: `approve(hold)` (or timeout) = skip the write THIS cycle, resume normal judgment next cycle; the approval slot is
  ONE-SHOT (410 on replay, same as escalation slots).
```

### Travel pack + duty of care (G5/G6)
```
approval time → STORE.snapshot_offer_identity(desk_id): segments, carrier, flight numbers, cabin (persisted, survives restart)
DeskEvent(ticketed, {paid_amount, order_no}) → notify.on_ticketed:
    pack = approved IDENTITY snapshot  +  ACTUAL money (paid_amount + order_no from write/ledger)
    (never re-derive identity at TICKETED; money attaches late because a contingency-absorbed delta staled the estimate)
    → per-traveler pack + manager summary (disclose: confirmation reference, not airline PNR)
scheduled order_status poll (existing retry policy) → status change → publish DeskEvent(disruption) → notify alerts, honest label
```

### travelers_complete — BACKEND-side (the store is source of truth)
```
add_traveler endpoint, AFTER insert: if verified_count(desk_id)==team_size AND lifecycle==awaiting_travelers:
    fire DeskEvent(travelers_complete) ONCE (dedupe guard) + ledger note
```
Bot-side counting is rejected: it drifts on reject/dedupe/resubmit. Bot stays a thin I/O adapter (advise/execute symmetry: bot collects, backend decides). Unit-testable without the bot.

## Test plan

- `test_mrz.py::test_valid_td3_passes` — a known-good TD3 pair → all fields; `::test_each_field_corruption_fails` — flip one digit in doc/DOB/expiry/composite → `validate()` returns None (four cases).
- `test_pax_builder.py::test_carries_verify_traveler_ids` — traveler_id from verify, name/doc from stored rows, pax_source=collected; `::test_ungated_desk_demo_fallback` — legacy/recorded desk (no invite_token) → demo envelope, pax_source=demo (byte-safe); `::test_gated_desk_missing_travelers_holds` — gated desk short a roster → PaxBuild.hold=True, wall holds+escalates, NEVER demo identities; `::test_distinct_docs_per_pax` — no two pax share a doc number.
- `test_desk_lifecycle.py::test_seed_does_not_start_cycle` — after seed, no DeskState/task, lifecycle awaiting_travelers; `::test_confirm_wrong_code_no_start` — 403, still awaiting; `::test_confirm_starts_cycle` — right code → task exists, lifecycle released; `::test_approve_pins_offer` — resumed cycle books the pinned offer, no re-judgment (brain.judge not called — assert via a counting stub); `::test_pinned_price_move_beyond_contingency_escalates` — verify increase past contingency/cap on the pinned path → escalation, not silent book (wall invariants fire on the pinned path too); `::test_unbookable_pin_one_reapproval_then_hold` — first unbookable re-judges once (reapproval_count→1), second holds+discloses; `::test_travelers_complete_fires_once_backend` — Nth insert fires exactly one travelers_complete, resubmit does not refire.
- `test_policy_filter.py::test_cheapest_among_policy_passing` — offers filtered by airline/cabin/time, cheapest survivor chosen; `::test_zero_pass_escalates` — empty survivor set → escalation, never a silent violation.
- `test_waybot_security.py` (style of `test_injection_containment.py` — assume the attack succeeded, assert nothing that matters changed):
  1. `test_code_hashed_constant_time_attempt_cap` — plaintext never stored; >5 wrong → reissue/lock.
  2. `test_leaked_token_cannot_release` — valid token + wrong/absent code → no cycle start.
  3. `test_traveler_session_cannot_confirm_or_approve` — bot-path identity has no release/approve authority.
  4. `test_checksum_and_dup_and_oversize_rejected` — bad checksum / duplicate doc / oversized photo never becomes a traveler.
  5. `test_no_pii_in_events_or_disk` — scan every emitted DeskEvent + ledger note for doc-number/DOB patterns → fail on hit; assert no image artifact on disk.
  6. `test_hostile_mrz_name_contained` — a passport "name" carrying an injection string flows only into pax JSON, never into a brain prompt or CLI arg; wall still books only TICKETED.
  7. `test_confirm_and_approve_single_use` — second confirm/approve → 410.

Every new test must fail against pre-change code (no test that can pass today).

## Least confident decisions — RESOLVED at Gate 3 approval

1. **Pinned-resume = branch, made PER-POSITION.** The branch lives ONLY in mark construction (approved offer vs. judged mark); then ONE wall runs over pinned + judged actions together. Approved positions execute pinned, unapproved ones judge normally in the same cycle — mixed/portfolio desks compose for free, no scattered `if pinned`. A `pinned_resume` provenance event records "executed the approved offer, no re-judgment." If a third mode ever appears, extract the wall then — not now.
2. **`travelers_complete` = BACKEND-side.** Fired from the acceptance endpoint after insert (`count==team_size AND lifecycle==awaiting_travelers → once, dedupe guard, ledger note`). Store is source of truth; bot-side counting drifts on reject/dedupe/resubmit. Bot stays a thin I/O adapter.
3. **Nationality miss = fail-closed.** ISO-3 not in the curated CSV → `validate()` None → typed-entry fallback, which ALSO validates against the same curated CSV (never free text). `fallback_used` provenance event records extraction quality.
4. **Pack = identity@approval + money@TICKETED.** Persist the identity snapshot (segments, carrier, flight numbers, cabin) at approval; attach the ACTUAL paid amount + `order_no` at TICKETED (a contingency-absorbed delta stales the approved estimate). Never re-derive identity at TICKETED.
5. **Bot in `lifespan` with isolation guards.** Supervised task + backoff restart; python-telegram-bot global error handler (one bad update can't kill the task); timeout-bounded async extraction (Qwen-VL never blocks the loop); sink delivery fire-and-forget with per-subscriber try/except (symmetric isolation). **Deploy note: single-worker constraint** — polling under multiple workers double-consumes updates.

## Amendments folded in (Gate 3 review)

- **pax fallback keyed on desk KIND, not data presence** (see §Pax builder): gated desk missing travelers → hold+escalate; only ungated (legacy/recorded) desks get demo identities. `pax_source = collected | demo` surfaced as provenance.
- **Hold semantics**: `approve(hold)`/timeout = skip the write this cycle, resume normal judgment next cycle; approval slot is one-shot (410 on replay).
- **Frontend `types.ts` is explicitly one of the four frontend files** carrying the new fields (lifecycle, invite_token, masked travelers, verified_count, policy) — it has silently broken this project before; a build-time type check gates the slice.
