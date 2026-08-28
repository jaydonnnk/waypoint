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
