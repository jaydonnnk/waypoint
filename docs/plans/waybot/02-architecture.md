# Architecture: Waybot — G1–G6 Gap-Closure Program

Altitude: what touches what, which endpoints/tables/env vars exist, and the end-to-end call order. Signatures and test names are Gate 3. Every file:line below was read against the repo on 2026-08-28.

## Fit — existing modules this touches

| Module | Today | Change |
|---|---|---|
| `backend/app/api/routes.py` | `POST /desk/seed` persists (307-309) then immediately fires the cycle (313); `DESKS: dict` in-memory registry (137); `CYCLE_LOCK` serializes `AGENT.run` (234) | Split seed from start. Add confirm/approve/traveler endpoints. Reuse the exact register+create_task two-liner (310-313) as the shared "resume" primitive. |
| `backend/app/db/schema.py` | `MandateRow` (mandate == desk) | Add lifecycle + invite_token + code-hash columns to `MandateRow`; add `TravelerRow` + `ChatBindingRow` (new tables). |
| `backend/app/db/database.py` | `_backfill_mandate_columns` idempotent ALTER shim (57-71), SQLite-only, runs in `init_db` (117) | Append the 3 new mandate columns to `_MANDATE_COLUMN_BACKFILL` (50-54). New tables need no shim — `create_all` builds them. |
| `backend/app/db/store.py` | `DeskStore`, only module opening DB sessions | Add traveler CRUD, lifecycle read/write, chat-binding CRUD, offer-snapshot read for the pack. |
| `backend/app/agent/loop.py` | one cycle: reread → reprice fan-out (274) → brain judgment (327) → execute wall (344-448) → settle (453). Search called once (514). Pax built once (`_build_demo_pax_json`, 725). | G1: pax builder reads stored travelers. G4: insert an approval checkpoint between judgment (327) and the execute wall (344). G2: pass policy into `search` + client-side offer filter. |
| `backend/app/atlas/client.py` | `search(origin,dest,dep,pax)` (261); `map_offer` drops carrier/cabin (157) | G2: add `airlines` to `search`. G5: keep carrier + cabin_class on `Offer`. |
| `backend/app/agent/brain.py` | httpx → DashScope OpenAI-compat, qwen-plus (40-45) | Reused as the transport pattern for the Qwen-VL OCR call — not modified; the bot's extractor mirrors it. |
| `backend/app/main.py` | `lifespan` runs `init_db()` only (36-40) | Start/stop the Telegram bot in `lifespan`, gated on `WAYPOINT_BOT_TOKEN` (absent → bot skipped, app unaffected). |
| `frontend/app/page.tsx` | start screen: seed → navigate to desk | After seed, render the share card (link + code + live progress) instead of navigating. |
| `frontend/app/desk/[deskId]/page.tsx` | streams the cycle | Add a pre-stream code-entry panel + named-roster review (only shown while `awaiting_travelers`). |
| `frontend/lib/types.ts`, `frontend/lib/api.ts` | desk contract types + fetch helpers | Add every new field/endpoint (both silently break if a field is missing). |

New isolated package: `backend/app/bot/` (Telegram handlers, passport extractor, ICAO validator). Nothing else imports from it except `main.py` lifespan wiring — keeps the bot removable.

## Endpoints

| Route | Verb | Purpose |
|---|---|---|
| `/api/desk/seed` | POST | **Changed**: persist desk in `awaiting_travelers`, generate invite_token + hash the code, **do not** start the cycle. Returns `{desk_id, invite_token, confirmation_code}` (plaintext code returned once, only the hash stored). |
| `/api/desk/{id}` | GET | **Extended**: include lifecycle state + traveler roster (masked) + N/N verified count, so the share card and roster render. |
| `/api/desk/{id}/confirm` | POST | Validate the code against the hash; on match, flip to `released` and fire the cycle via the shared resume primitive. Wrong code → 403, no state change. |
| `/api/desk/{id}/approve` | POST | G4: record approval (ledger note + pin the approved offer), flip out of `pending_approval`, resume the cycle. Body `{choice: approve|hold}`. |
| `/api/desk/{id}/travelers` | GET | Internal/ops read of the masked roster (also feeds the bot's "N/N" ping). |

Bot ingress is **not** a REST route — python-telegram-bot owns its own update channel (polling locally; webhook is a later deploy concern). The bot writes travelers through `DeskStore`, the same facade the routes use.

## Data

New columns on **`mandate`** (existing table → go through the backfill shim, constant defaults so old DBs self-heal):

- `lifecycle TEXT NOT NULL DEFAULT 'released'` — `awaiting_travelers | released | pending_approval | closed`. Default `released` so every pre-existing desk keeps today's behavior.
- `invite_token TEXT` (nullable) — URL-safe `[A-Za-z0-9_-]`, ≤64 chars (Telegram deep-link limit). Indexed for the bot's token→desk lookup.
- `confirmation_code_hash TEXT` (nullable) — salted hash; plaintext code never stored.
- `approved_offer_id TEXT` (nullable) — G4: the offer the manager signed off, pinned for the resumed cycle.
- `policy_json TEXT` (nullable) — G2: `{airlines:[IATA], cabin, depart_after, arrive_by}`; absent → no policy filter (today's behavior).

New table **`travelers`** (created fresh by `create_all`, no shim):

- `id` PK, `desk_id` FK→mandate, `slot` (1..team_size), plus MRZ-derived fields: `family_name`, `given_name`, `gender`, `birthday`, `nationality` (ISO-2), `doc_type`, `doc_number`, `issuing_country`, `doc_expiry`, and optional `contact_email`, `contact_mobile`. `verified_at`.
- Queries: insert-on-confirm, `SELECT … WHERE desk_id=` (roster + N/N count + pax builder), purge `WHERE desk_id=` at desk close.

New table **`chat_bindings`** (created fresh):

- `telegram_chat_id` PK, `desk_id` FK, `slot`. Binds one private Telegram chat to one traveler slot on one desk (so a re-sent photo updates the same slot, not a new row). Queries: upsert on `/start`, lookup on every photo.

**Retention:** the raw passport image is never persisted — parsed fields only. Travelers rows purged at desk close. Bot calls Telegram `deleteMessage` on the uploaded photo after extraction (Telegram keeps it server-side otherwise).

## Flow — end-to-end call order (main path)

1. **Seed.** `page.tsx` → `POST /api/desk/seed` → `fixture.seeded_portfolio` → `STORE.seed_desk` (lifecycle `awaiting_travelers`, token + code-hash persisted). **No `create_task`.** Returns token + one-time plaintext code. Share card renders.
2. **Traveler capture (G1).** Traveler taps `t.me/Bot?start=<token>` → bot `/start` handler resolves token→desk via `DeskStore`, upserts `chat_bindings` slot → prompts for photo → photo → **Qwen-VL extract** (brain.py transport pattern) → **ICAO 9303 check-digit gate** → masked confirm card (inline keyboard) → optional contact prompt → `STORE.add_traveler`. Checksum fail → typed-entry fallback, same gate. Bot `deleteMessage` on the photo.
3. **Ready.** On N/N verified, bot pings the manager chat "all verified". Manager opens the desk page, reviews the **named** roster, enters the code.
4. **Confirm → release.** `POST /api/desk/{id}/confirm` → hash-check → lifecycle `released` → **shared resume primitive** (register `DeskState` + `asyncio.create_task(_run_desk)`). Persistence already preceded the task, so the long human wait was safe.
5. **Cycle to judgment.** `_run_desk` → `CYCLE_LOCK` → `AGENT.run`: reread world → reprice fan-out (search now carries `policy.airlines`; offers filtered client-side by cabin/time → cheapest **policy-passing** offer; zero pass → escalation) → brain judgment picks.
6. **Approval checkpoint (G4).** If lifecycle is not yet `pending_approval` for this desk and the pick is a *book*: persist `pending_approval` + `approved_offer_id=<chosen>`, emit the priced itinerary event, **end the cycle** (no in-cycle wait). Bot pushes the itinerary + Approve/Hold to the manager.
7. **Approve → resume.** `POST /api/desk/{id}/approve` (approve) → ledger note + lifecycle back to `released` → shared resume primitive fires the cycle again. This run sees `approved_offer_id` pinned and goes to the execute wall for that offer. Hold/timeout → honest give-up (consistent with escalation philosophy).
8. **Execute wall.** Unchanged invariants: fresh `verify` before write, budget/cap re-checked (unwaivable), pax payload built by `_build_demo_pax_json` **now reading stored travelers** zipped with verify's traveler_ids (carry, never invent) → order create → pay → poll until TICKETED asserted → `mark_booked` → settle.
9. **Travel pack (G5).** On TICKETED, build packs from the stored offer snapshot (carrier + cabin_class kept on `Offer`, flight numbers from `Segment.flight_number`) + order_no + price. Bot pushes per-traveler packs + manager summary, disclosing "confirmation reference, not airline PNR".
10. **Duty of care (G6).** A scheduled read-only `order_status` poll (existing retry policy) watches the ticketed order; a status change → bot disruption alert (travelers + manager), honestly labeled — no rebooking verb exists.

## External

- **Telegram Bot API** via `python-telegram-bot` (new dep). Env: `WAYPOINT_BOT_TOKEN` (absent → bot disabled, whole app still runs). Deep links `t.me/<bot>?start=<token>`. Polling locally; webhook is a deploy-time switch, not in MVP scope.
- **DashScope Qwen-VL** for OCR — reuses the existing `DASHSCOPE_API_KEY` and the OpenAI-compat base URL already used by `brain.py` (`DASHSCOPE_BASE_URL` override respected). No new key, no new SDK — plain httpx, model id `qwen-vl-*`.
- **Atlas CLI** — unchanged surface: `--airline` already accepted by new-search (cli-contract.md:40); no new verbs. Recorded mode ignores search args, so the `--airline` extension stays replay byte-safe.

## Decisions resolved at Gate 2 approval (were open, now locked)

1. **G4 = pin, effort S–M.** Adopt `approved_offer_id`. Resume **skips re-judgment** and goes straight to the execute wall with a fresh `verify` on the pinned offer; a price *move* on the pinned offer hits the existing escalation path. **Hard stop on the edge case:** if the pinned offer becomes *unbookable entirely* (not merely pricier — e.g. OFFER_EXPIRED), allow **exactly one** re-judgment + one fresh approval request, then hold and disclose. A `reapproval_count` (cap 1) bounds the ping-pong.
2. **Bot ↔ cycle = in-process typed domain-event sink (callback, not polling).** The loop emits named moments — `travelers_complete`, `pending_approval`, `ticketed`, `disruption`, plus close summaries — to a sink; the bot registers as a subscriber. Single-process today (registry is in-memory), so the callback has no polling lag. Multi-process later → the sink swaps for an SSE/webhook consumer without touching the loop. Polling kept only as a fallback.
3. **Security = code, not a slide.** The 7-guard list below becomes a test module in the style of `tests/test_injection_containment.py` (assert the guarantee holds when the attack succeeds). Enumerated in Gate 3 §Test plan and §Files.

### The 7 security guards (→ `tests/test_waybot_security.py`)
1. Confirmation code: hashed-only storage (PBKDF2, iterations stored in-hash), constant-time compare, attempt cap (5) that throttles wrong-code guessers (6th → 429) **without ever locking out the code-holder** — the check is verify-first, so the correct code always releases (no reissue endpoint exists; verify-first is the anti-lockout design, not reissue). TTL expiry (default 24h, anchored to seed time).
2. Invite token: random 128-bit, single-purpose (binds chat→desk only). Leaked link cannot release — release requires the code.
3. Role separation: traveler (bot) sessions submit passports only; release/approve authority lives only on the code path. A traveler session cannot call confirm/approve.
4. Submission integrity: checksum gate before storage; `team_size` cap; duplicate doc numbers rejected (also a sandbox rule); **oversized** photos rejected before extraction (pre-download `file_size` gate + authoritative post-download check). Malformed/non-image blobs are not explicitly rejected — they fail closed through `extract_passport`'s exception into the typed-entry fallback.
5. PII minimization: image bytes never touch DB or disk (memory-only, deleted post-extract, `deleteMessage` on Telegram); traveler rows purged at desk close; events/logs masked — a test scans emitted events for doc-number/DOB patterns and fails on a hit.
6. Untrusted-text containment: MRZ-derived strings flow only into structured pax JSON (stdin), never into brain prompts or CLI args. Mirrored injection test with hostile names.
7. One-shot semantics: confirm and approve are single-use (second call → 410, like escalation slots), so approvals cannot replay.
