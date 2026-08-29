# Status: Waybot — G1–G6 Gap-Closure Program

- Gate 1 — Product: APPROVED 2026-08-28
- Gate 2 — Architecture: APPROVED 2026-08-28
- Gate 3 — Program Design: APPROVED 2026-08-28 (6 amendments folded: per-position pin, backend travelers_complete, fail-closed nationality+CSV typed fallback, pack identity@approval/money@TICKETED, bot isolation guards, pax fallback by desk-kind)
- Gate 4 — Slice plan: APPROVED 2026-08-28 (9 slices; MVP = S1–S5; build not yet started)

## Slices (finalized at Gate 4 — see 04-slices.md)
- [x] S1 — TRACER: seed-without-start → code → cycle fires (schema+sink+_start_cycle+confirm+share card) — built 2026-08-28; 3 lifecycle tests + full suite (175) green, frontend type-check clean
- [x] S2 — Waybot skeleton + deep-link bind (bot in lifespan, subscribes to sink) — built 2026-08-28; 17 new tests + full suite (193) green, frontend type-check clean; L4 invite_token index fix landed; Qoder cross-check: H1+8M+3L all fixed (see MISTAKES.md §Slice 2)
- [x] S3 — Passport extraction + MRZ gate + G1 write-path swap (G1 CLOSED) — built 2026-08-29; full suite (219) green, frontend type-check clean; mrz.py (ICAO TD3 7-3-1 gate, calendar+expiry+fail-closed nationality), extract.py (Qwen-VL OCR), pax.py (gated-hold/ungated-demo), handlers.py (photo+confirm+typed), session.py (full state machine), store additions (add/list/purge_travelers, has_ledger_marker), app/travelers.py (backend-side travelers_complete, DB-backed dedupe), loop.py swap (pax_source provenance). Qoder cross-check: 0 High; 10M+6L fixed, 3 documented-open (M5 purge/M8 demo-contract/M10 manager_chat_id — see MISTAKES.md §Slice 3)
- [x] S4 — Security guard module (7 guards → tests) — built 2026-08-29; 29 security tests (27 pass + 2 xfail approve stubs for S5) + full suite green (250 collected = 248 pass + 2 xfail, 4 deselected), frontend type-check clean; KDF upgrade (pbkdf2_hmac, scheme-tagged back-compat), attempt cap (5 wrong → 429), TTL expiry, 409→410 one-shot reconcile, photo size guard (oversize only — malformed blobs fail closed via extract fallback, not explicit rejection), PII scan (bite-proven RED/GREEN), hostile-name containment, store.bump_code_attempts wired. **S4-review hardening (2026-08-29):** round 1 — verify-first attempt cap (H1 no-lockout DoS closed), atomic bump (H3), TTL default→24h + iterations-in-hash (H2/L3), PBKDF2 off the event loop (L2), pre-download photo size gate (L1), tolerant env parses (L4), frontend 410/429 outcomes (M5), functional rewrites of the oversized-photo + no-image-on-disk tests (M2/M3, previously grep-only), KDF iteration-floor assertion (M1), hostile-name docstring scoping (M4), guard-4 wording narrowed to oversize-only rejection (M6 honest-doc disposition), regression-guard relabels of `test_constant_time_compare` / `test_legacy_sha256_still_verifies` (L5); round 2 — KDF verify on a dedicated bounded ThreadPoolExecutor(2) + counter freezes past cap (H-new1), stored iters outside [1, 1_000_000] fail closed (M-new1), shared `int_env` in app/config.py with minimums — TTL min=0, cap min=1, max-photo min=1 (M-new2), no-disk test also scans changed pre-existing files + test DB (M-new3), frontend throttled copy reworded, dead 3-part `pbkdf2$salt$digest` verify branch deleted (DB check proved no such hashes exist; only legacy 2-part salt$digest and current 4-part formats remain), schema.py comment fixed
- **S4 hardening — Task #8 (2026-08-29):** in-app sliding-window `/confirm` rate limit (default 10 requests/60s per `desk_id`, env-tunable `WAYPOINT_CONFIRM_RATE_LIMIT`, min 1) closing the request-VOLUME layer the attempt cap never covered (request count itself, before any TTL/KDF work). Burst guard, not an auth layer: transient throttle bounded by the window (a flooded desk delays even a correct code by at most one window — never permanent, unlike the old lockout), exact under the pinned single-worker deployment (degrades to limit×instances if ever scaled), lazy-eviction memory hygiene.
- [ ] S5 — Pre-trip approval, pinned resume (G4 CLOSED) ← MVP complete here
- [ ] S6 — Travel pack (G5 CLOSED, bounded)
- [ ] S7 — Policy filter (G2 CLOSED to data limits)
- [ ] S8 — Trip construction (G3 PARTIAL)
- [ ] S9 — Duty of care (G6 PARTIAL)
- [ ] S3-deferred (M5): wire `store.purge_travelers(desk_id)` into the desk-close path when the close slice lands — traveler PII must not outlive the desk

## Code facts verified against repo (2026-08-28)
- Persist precedes task: routes.py:307-313 (STORE.seed_desk then asyncio.create_task) — seed-without-start is a clean split.
- CYCLE_LOCK serializes process-wide: routes.py:234 wraps AGENT.run — DO NOT block inside cycle; use persist-and-resume.
- Pax write seam single call site: loop.py:725 `_build_demo_pax_json(verified.travelers)`; traveler_id carried from verify (loop.py:92, `t.get("traveler_id","")`) — carry never invent.
- Brain transport reusable for OCR: brain.py:40-45, httpx→DashScope OpenAI-compat, qwen-plus; swap model→qwen-vl, same key (DASHSCOPE_API_KEY).
- cabin_class in raw envelopes (30 hits) but dropped by map_offer (zero cabin refs in atlas/*.py) — additive mapping needed for G2/G5.
- Repeatable `--airline` flag exists: cli-contract.md:40.
- Order status returns status+order_no ONLY, no pnr/record_locator/ticket_number — G5 "confirmation ref not PNR" is honest.
- No cancel/refund/change verbs in CLI — G6 rebooking permanently out.
- In-memory desk registry: routes.py:137 `DESKS: dict = {}` — restart loses unreleased desks.
- Idempotent ALTER TABLE backfill shim real: database.py:57-118 `_backfill_mandate_columns` (PRAGMA table_info, SQLite-only, in init_db). New columns land on mandate table (desk_id == mandate id).
- FastAPI lifespan exists: main.py:36-44 — bot can run in lifespan.
- Segment.dep_time (models.py:26) + flight_number (models.py:28) present; Segment has NO carrier field — carrier read from raw in map_offer (client.py:153) but not persisted. Offer model models.py:43.

## Open security carve-out (must land in a Gate 1/3 slide + code)
Passport photo = NEW untrusted LLM-input channel feeding a WRITE. The "injection-proof: code re-checks every LLM pick" guarantee covers budget/cap, NOT passenger identity. Mitigations required, not optional:
1. MRZ fields consumed as DATA only (structured extract, never instruction-following).
2. traveler_id still comes from Atlas verify, never from the photo — photo controls name/DOB/doc, Atlas controls booking identity.
3. Anyone with the link can upload ANY passport — manager reviews the NAMED verified list before entering the release code (not just the N/N count).
4. Telegram retains the uploaded photo server-side regardless of local delete — must call bot deleteMessage on the upload, not only purge the temp file.
5. invite_token: desk-scoped, single-purpose, expires on desk close, upload rate-limit.

## Known-open holes after S1 (Qoder cross-check 2026-08-28 — see MISTAKES.md)
- ~~**Confirmation code has NO attempt cap and NO TTL yet**~~ — FIXED in S4 (hardened S4-review): `store.bump_code_attempts` wired and made ATOMIC (single UPDATE, no read-modify-write race). `/confirm` is now VERIFY-FIRST — the cap throttles wrong-code guessers (5 wrong → 429) but the correct code ALWAYS releases, so an attacker who knows only the shared `desk_id` can no longer permanently brick the release gate (there is no reissue endpoint; verify-first is the fix, not reissue). TTL (env-tunable `WAYPOINT_CODE_TTL`) default raised 3600s → **86400s (24h)** since roster collection over Telegram can exceed an hour and an expired code cannot be reissued. TTL still anchored to `mandate.created_at` (== code-issue time at seed); a dedicated `code_issued_at` column remains the more-correct long-term anchor if reissue ever lands. One-shot semantics reconciled to 410.
- ~~**Code hash is single-round salted SHA-256**~~ — FIXED for NEW codes in S4; **transitional legacy path retained** (not an unqualified close): new hashes are `hashlib.pbkdf2_hmac` (260k iters, OWASP 2023), now tagged `pbkdf2$<iters>$salt$digest` with the iteration count stored IN the string so future tuning never orphans an at-rest hash (S4-review L3). The legacy `salt$digest` single-round SHA-256 scheme STILL verifies indefinitely (back-compat) and is never upgraded-on-verify — pointless here because release codes are single-use (verified once, then the desk is released). Only pre-S4 DB rows carry it; every new seed is pbkdf2.
- ~~**`invite_token` index only exists on fresh DBs**~~ — FIXED in S2: `_ensure_invite_token_index()` adds `CREATE INDEX IF NOT EXISTS` in `init_db`, so shim-upgraded DBs get the index too.
- H1 (confirm double-start race) — FIXED in S1 via atomic CAS `DeskStore.try_release` (test_release_cas_is_single_winner).

## Notes for a fresh session
Read every doc in this folder before continuing. The full 7-gap plan the user supplied is the source; it is code-accurate. Caveman chat mode is on (docs stay normal prose).
