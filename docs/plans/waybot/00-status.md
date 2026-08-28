# Status: Waybot — G1–G6 Gap-Closure Program

- Gate 1 — Product: APPROVED 2026-08-28
- Gate 2 — Architecture: APPROVED 2026-08-28
- Gate 3 — Program Design: APPROVED 2026-08-28 (6 amendments folded: per-position pin, backend travelers_complete, fail-closed nationality+CSV typed fallback, pack identity@approval/money@TICKETED, bot isolation guards, pax fallback by desk-kind)
- Gate 4 — Slice plan: APPROVED 2026-08-28 (9 slices; MVP = S1–S5; build not yet started)

## Slices (finalized at Gate 4 — see 04-slices.md)
- [x] S1 — TRACER: seed-without-start → code → cycle fires (schema+sink+_start_cycle+confirm+share card) — built 2026-08-28; 3 lifecycle tests + full suite (175) green, frontend type-check clean
- [x] S2 — Waybot skeleton + deep-link bind (bot in lifespan, subscribes to sink) — built 2026-08-28; 17 new tests + full suite (193) green, frontend type-check clean; L4 invite_token index fix landed; Qoder cross-check: H1+8M+3L all fixed (see MISTAKES.md §Slice 2)
- [ ] S3 — Passport extraction + MRZ gate + G1 write-path swap (G1 CLOSED)
- [ ] S4 — Security guard module (7 guards → failing tests)
- [ ] S5 — Pre-trip approval, pinned resume (G4 CLOSED) ← MVP complete here
- [ ] S6 — Travel pack (G5 CLOSED, bounded)
- [ ] S7 — Policy filter (G2 CLOSED to data limits)
- [ ] S8 — Trip construction (G3 PARTIAL)
- [ ] S9 — Duty of care (G6 PARTIAL)

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
- **Confirmation code has NO attempt cap and NO TTL yet** (plan defers to S4). Because `page.tsx` now seeds every desk `gated:true`, `/confirm` is the LIVE path, not dormant. The `code_attempts` column exists but is not yet incremented/read; no expiry column. The 32-bit code (`secrets.token_hex(4)`) is only adequate *with* a cap — pull the cap/TTL/one-shot-410 forward in S4 and treat this as a live security gap until then.
- **Code hash is single-round salted SHA-256** (S1). Constant-time compare is in place; storage strength is not — swap for a slow KDF when S4 lands.
- ~~**`invite_token` index only exists on fresh DBs**~~ — FIXED in S2: `_ensure_invite_token_index()` adds `CREATE INDEX IF NOT EXISTS` in `init_db`, so shim-upgraded DBs get the index too.
- H1 (confirm double-start race) — FIXED in S1 via atomic CAS `DeskStore.try_release` (test_release_cas_is_single_winner).

## Notes for a fresh session
Read every doc in this folder before continuing. The full 7-gap plan the user supplied is the source; it is code-accurate. Caveman chat mode is on (docs stay normal prose).
