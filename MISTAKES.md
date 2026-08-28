# MISTAKES — Waybot build (Qoder cross-check findings + resolutions)

Log of issues an external reviewer (Qoder) caught in a completed slice, and
what was done about each. One section per slice review.

## Slice 1 — TRACER (reviewed 2026-08-28)

### Fixed this slice

- **H1 (High) — TOCTOU race in `/confirm` double-started the cycle.**
  The release was a non-atomic check-then-act across four `to_thread` hops
  (`get_lifecycle` → `get_invite` → `set_lifecycle` → `_start_cycle`). Two
  concurrent correct-code confirms both passed the check and both called
  `_start_cycle`, overwriting `DESKS[desk_id]` and spawning a second
  `_run_desk` — a doubled write path (double booking) in live mode.
  **Fix:** added `DeskStore.try_release(desk_id)` — an atomic
  `UPDATE mandate SET lifecycle='released' WHERE id=? AND lifecycle='awaiting_travelers'`
  returning `rowcount == 1`. `/confirm` now starts the cycle ONLY if the CAS
  won; the loser gets 409. Regression test: `test_release_cas_is_single_winner`.
  Files: backend/app/db/store.py, backend/app/api/routes.py.

- **M2 (Medium) — start page hardcoded `gated:true` with no fallback.**
  Against a pre-S1 backend that ignores `gated`, the cycle starts server-side
  and the response is `{desk_id}` only, so the share card rendered with blank
  link/code over a live, unviewed desk. **Fix:** if `result.invite_token` is
  absent, `router.push(/desk/${desk_id})` instead of showing the card.
  File: frontend/app/page.tsx.

- **L2 (Low) — EventSink isolation gaps.** Added `unsubscribe(handler)` (a
  torn-down subscriber would otherwise leak forever) and wrapped the
  `loop.create_task` scheduling in per-subscriber try/except so even a
  scheduling fault cannot break the publisher (the "symmetric isolation"
  guarantee now holds end to end, not just inside `_deliver`).
  File: backend/app/events.py.

### Documented as known-open (plan-deferred, not fixed this slice)

- **M1 (Medium) — code has no attempt cap / no TTL, and the gate is the live
  path.** `code_attempts` column exists but is never incremented/read; no
  expiry. Plan defers cap/TTL/one-shot-410 to Slice 4, but since `page.tsx`
  seeds every desk `gated:true`, `/confirm` is live now. Recorded as a live
  security gap in `00-status.md`; pull the cap/TTL forward in S4.
- **L1 (Low) — single-round salted SHA-256 over a 32-bit code space.** If the
  DB leaks, the keyspace is exhaustible offline. Compare is constant-time;
  storage strength is the gap. Swap for a slow KDF in S4. Noted in status.
- **L4 (Low) — `invite_token` index missing on shim-upgraded DBs.** Harmless
  in S1 (PK lookups only); becomes a full scan when S2's token→desk lookup
  lands. Add `CREATE INDEX IF NOT EXISTS` to the shim in S2. Noted in status.

### Acknowledged, no action (cosmetic / wording / by-design)

- **L3** — GET `/desk/{id}` semantic widening (persisted-but-not-live desks
  return 200 `done:false` instead of 404). Deliberate — needed so the
  awaiting-desk code panel renders without a 404. Unknown desks still 404.
- **L5** — "byte-unchanged" invariant refined: the SEED RESPONSE is
  byte-unchanged for ungated desks; the GET snapshot is additive-only
  (`lifecycle`/`verified_count` keys). All consumers are key-selective; suite
  green.
- **L6** — transient "can't reach this booking" banner flash on gated desk
  pages before the snapshot resolves. Cosmetic, new-flow-only. (Mitigated:
  the streamDead banner is now suppressed while `awaiting`.)
- **L7** — second `/confirm` returns 409 vs the design doc's 410. Effectively
  single-use today; reconcile with 03-program-design.md when S4 lands formal
  one-shot semantics.

### Process note
- Branch had zero commits over `main` at review time (whole diff in the
  working tree). Commit is pending the user's go — user asked not to commit
  unless requested.
- Untracked `personA.md`, `personB.md`, `docs/evidence/runtime-env-check.md`
  are pre-existing and outside the slice — confirm intent before committing.

## Slice 2 — Waybot skeleton (reviewed 2026-08-28)

### Fixed this slice

- **H1 — pytest-asyncio not in requirements.txt.** The 3 async tests
  (including the only end-to-end subscriber test) silently skipped in
  manifest-built environments. The "10 tests green" claim did not
  reproduce in a clean venv.
  **Fix:** added `pytest-asyncio>=0.21` to requirements.txt.

- **M1 — _supervised_bot retried forever on unrecoverable errors.** A
  revoked/invalid WAYPOINT_BOT_TOKEN produced InvalidToken on every
  initialize(), hammering the Telegram API indefinitely.
  **Fix:** added `_is_unrecoverable()` to catch InvalidToken/Forbidden
  and bail immediately; added a consecutive-failure budget (5) for
  transient faults. File: main.py.

- **M2 — notify handler never unsubscribed from SINK.** Each lifespan
  startup subscribed a fresh closure; shutdown only cancelled the task.
  Restart cycles accumulated subscribers pointing at dead Applications.
  **Fix:** stash the handler on `application.bot_data["_notify_handler"]`
  in build_application; call `SINK.unsubscribe(handler)` in the shutdown
  leg of lifespan. Files: bot/__init__.py, main.py.

- **M3 — bind_chat slot assignment non-atomic, uniqueness unenforced.**
  Concurrent binds could write duplicate slots; S3's photo-capture keys
  on slot.
  **Fix:** added `UniqueConstraint("desk_id", "slot")` on ChatBindingRow.
  File: schema.py.

- **M4 — bind_chat ignored lifecycle and team_size.** Binding succeeded
  after release/close, slots grew past team_size, leaked share links
  stayed usable forever.
  **Fix:** bind_chat now rejects when `lifecycle != "awaiting_travelers"`
  and when bound count `>= team_size`. Tests: test_bind_rejects_released_desk,
  test_bind_rejects_when_full. File: store.py.

- **M5 — sink subscription untested through build_application.** Deleting
  the `sink.subscribe(notify_handler)` line left all tests passing.
  **Fix:** added test_build_subscribes_to_sink: asserts subscriber list
  grows and the handler matches the stashed ref. File: test_waybot.py.

- **M6 — /start handler had zero automated tests.** Deep-link arg
  parsing, invalid-token reply, SESSIONS.bind, and no-args branch were
  unexecuted code.
  **Fix:** added test_start_valid_token, test_start_bad_token,
  test_start_no_args (all drive _start with stubbed Update/context).
  File: test_waybot.py.

- **M7 — notify.py docstring claimed wrong slot-0 manager discovery.**
  Implementation only reads `event.payload["manager_chat_id"]`; nothing
  ever registers a slot-0 manager binding.
  **Fix:** corrected docstring to match the real contract (payload-based,
  S3 populates manager_chat_id from the backend). Manager-identity seam
  is an explicit pre-S3 obligation. File: notify.py.

- **M8 — Existing TestClient tests acquired network side effects when
  WAYPOINT_BOT_TOKEN was set.** Any dev/CI machine exporting a real
  token would build a PTB Application and spawn real Telegram polling.
  **Fix:** autouse conftest fixture doing
  `monkeypatch.delenv("WAYPOINT_BOT_TOKEN", raising=False)`. File:
  tests/conftest.py.

- **L1 — Sync store.bind_chat blocked the bot event loop.** Every other
  route uses asyncio.to_thread.
  **Fix:** `await asyncio.to_thread(store.bind_chat, ...)` in _start.
  File: handlers.py.

- **L2 — bind_chat exception gave the traveler silence.** The global
  error handler logged but the traveler got no reply.
  **Fix:** try/except in _start with a generic "try again" reply.
  File: handlers.py.

- **L3 — Three uncovered behaviors.** Added: test_index_exists_after_init_db
  (PRAGMA index_list assertion), test_unknown_token_returns_none now
  verifies chat_bindings is empty. File: test_waybot.py.

### Acknowledged, no action (by design)

- Reviewers confirmed import isolation, token-unset boot path,
  _ensure_invite_token_index idempotency, PTB version compat, and no
  regressions in the existing suite.

### Post-fix proof
- Full suite: 193 passed (176 existing + 17 new), 4 deselected, frontend
  tsc clean. All async tests execute (pytest-asyncio in manifest).

## Slice 3 — Passport extraction + MRZ gate + G1 (reviewed 2026-08-29)

Qoder verdict: no High findings. Core safety guarantees held (recorded-mode
byte-safety intact, MRZ-as-data containment holds at every sink, gated-hold
invariant holds at the write wall). 12 Medium + 7 Low.

### Fixed this slice

- **M1 — MRZ alias codes padded with `<` failed ISO-3 lookup.** German `D<<`
  (and any padded alias) shunted a whole issuing country to typed entry.
  **Fix:** `_iso3_to_iso2` strips `<` before lookup (nationality + issuing).
  Test: test_alias_code_with_filler_maps. File: bot/mrz.py.
- **M2 — validate() could raise instead of returning None.** `check_digit`
  raised ValueError on illegal chars; a non-string VL output raised
  AttributeError — neither caught, so the traveler got silence, no fallback.
  **Fix:** `parse_td3` wraps its body in try/except → None; `validate`
  coerces via str() and guards. Tests: test_illegal_char_does_not_raise,
  test_non_string_ocr_output_validate_none. File: bot/mrz.py.
- **M3 — no calendar/expiry validation.** Feb 30 passed; expired passports
  booked and failed late at Atlas. **Fix:** `_mrz_date_to_iso` uses
  `datetime.date()` (rejects Feb 30 etc.); expiry-not-past check fails
  closed at the gate on BOTH the photo and typed paths. Tests:
  test_expired_passport_rejected, test_build_typed_fields_rejects_expired,
  test_build_typed_fields_rejects_bad_calendar_date. File: bot/mrz.py.
- **M4 — empty verify travelers on a gated desk fabricated `traveler_id: ""`.**
  Violated carry-never-invent. **Fix:** gated desk + empty verify →
  PaxBuild(hold=True). Test: test_gated_empty_verify_holds_never_invents.
  File: pax.py.
- **M6 — MRZ names interpolated into a `parse_mode="Markdown"` reply.** An
  unbalanced `*`/`_`/`[` from OCR would crash the send after the session
  already moved to awaiting_confirm (invisible card, stranded session).
  **Fix:** dropped parse_mode — plain text is the safe sink for untrusted
  extracted strings. File: bot/handlers.py.
- **M7 — iso3_to_iso2.csv untracked + not COPY'd into Dockerfiles.** Fresh
  clone broke test_mrz; a bot-enabled container raised FileNotFoundError
  mid-handler. **Fix:** `git add`ed the CSV; added `COPY data/iso3_to_iso2.csv`
  to both backend/Dockerfile and backend/Dockerfile.live.
- **M9 — travelers_complete fired from the bot module, not backend.** Spec
  (§2) mandates backend-side (store = source of truth; bot is a thin I/O
  adapter). **Fix:** moved the fire decision to `app/travelers.py`
  (`maybe_fire_travelers_complete`); the lifecycle test now imports no bot
  module. Dedupe is DB-backed (a ledger marker via `store.has_ledger_marker`)
  so it is RESTART-SAFE, and a process asyncio.Lock serializes check-and-fire
  (kills the double-fire race, Low). Files: app/travelers.py, db/store.py,
  bot/handlers.py, tests/test_desk_lifecycle.py.
- **M11 — pax_source provenance never surfaced.** **Fix:** `build_pax_json`
  return read into `pax_source`; the booked `trade` ledger note now carries
  `pax_source={collected|demo}`. File: agent/loop.py.
- **M12 — spec-mandated failure paths untested.** **Fix:** added tests for
  unmapped-ISO-3 → None (test_unmapped_iso3_fails_closed),
  validate_typed_nationality accept+reject, build_typed_fields accept+reject,
  and the builder's OWN duplicate-doc branch via a fake store
  (test_builder_holds_on_duplicate_docs). Files: tests/test_mrz.py,
  tests/test_pax_builder.py.
- **Low — typed entry bypassed the mrz gate module.** **Fix:** typed path now
  routes through `mrz.build_typed_fields` (same CSV/calendar/expiry rules as
  the photo path). File: bot/handlers.py.
- **Low — duplicate-doc ValueError echoed the raw doc number + desk_id** into
  the Telegram reply. **Fix:** generic "already registered on this trip"
  reply on both the confirm and typed paths — no raw doc/desk in user text.
  File: bot/handlers.py.
- **Low — photo cleanup incomplete (Confirm-only; Redo/resend leaked).**
  **Fix:** `_try_delete` helper; deleteMessage now runs on Redo and on a
  resend-before-confirm too. File: bot/handlers.py.
- **Low — slot upsert nulled contact_email/mobile on resubmit.** **Fix:**
  add_traveler keeps an existing contact when the resubmit omits it. File:
  db/store.py.
- **Low — build_pax_json ran blocking SQLite in the async loop.** **Fix:**
  wrapped in `asyncio.to_thread` at the loop call site. File: agent/loop.py.
- **Low — _TRAVELERS_COMPLETE_FIRED racy + restart-lossy.** Superseded by the
  M9 fix (DB-backed dedupe + process lock).

### Documented as decisions / deferred (NOT fixed this slice)

- **M5 — purge_travelers is unwired (retention "purge at desk close").**
  There is NO `lifecycle="closed"` writer anywhere in the codebase yet —
  desk-close is not a real seam in S3. purge_travelers is implemented and
  now unit-tested (test_purge_removes_all), but its call site lands with the
  desk-close slice. ASSIGNED: wire purge into the close path when that slice
  is built.
- **M8 — default recorded compose (gated frontend + no bot token) cannot
  complete a booking end-to-end.** By design G1 now fail-closes gated desks
  without a roster (PAX_ROSTER_INCOMPLETE). Recorded FIXTURES are all
  ungated so determinism/replay tests are unaffected — the gap is only the
  live frontend seeding `gated:true` against a bot-less recorded backend.
  NEEDS a demo-plan decision (see below) — awaiting user.
- **M10 — manager_chat_id passed as None.** Per the S3 task spec this is
  acceptable ("if no manager chat is known, omit it — notify handler logs
  and skips per S2"). The manager-identity seam (whoever seeded the desk)
  is wired through the payload; explicit manager binding is a later slice.
  `maybe_fire_travelers_complete` accepts a manager_chat_id param ready for
  that wiring.
- **Low — optional-contact prompts absent.** add_traveler's email/mobile
  params exist but no handler collects them yet. Deferred (Gate-1 decision:
  contact is optional). 
- **M11 — trade ledger note now carries a `; pax_source={collected|demo}`
  suffix** (intentional S3 provenance per M11). Anything pinned to the old
  literal note string ("booked — TICKETED asserted, sandbox money") will see
  the new suffix — no in-repo consumer breaks (verified by review).
- **M9 — conscious trade-off: the `travelers_complete` ledger marker is
  committed BEFORE `sink.publish`.** A crash in that window permanently
  drops the manager ping for that desk (dedupe wins over notify). Release
  not blocked — manager can still review the roster and enter the code.

### Post-fix proof
- Full suite: 219 passed (193 + 26 new/updated across test_mrz,
  test_pax_builder, test_desk_lifecycle), 4 deselected, frontend tsc clean.
