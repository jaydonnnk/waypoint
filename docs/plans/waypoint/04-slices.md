# Slices: Waypoint

Vertical slices over **9 days** (team of 2, ship **30 Aug 2026**). **S0 = day 1 = this spec package + gate approvals.** Slices S1–S8 = days 2–9. Each ends in a working, testable state; every slice refits existing files — no parallel surface. **S2 is the critical path** with a hard day-4 gate.

## Refit map
| disposition | exact paths | notes |
|---|---|---|
| STAYS AS-IS | `backend/app/atlas/client.py` | `search()` + mapping + exceptions; write-path methods are additive |
| STAYS AS-IS | `backend/app/data/loaders.py`, `backend/data/iata_city.csv`, `backend/data/iata_country.csv` | unchanged |
| STAYS AS-IS | SSE plumbing | streaming headers, buffer+replay, EventSource consumption, idempotent-by-index step rendering, StrictMode absorption, auto-navigation, retained task handles + `asyncio.Condition` |
| STAYS AS-IS | `backend/tests/test_atlas_mapping.py`, `backend/tests/test_atlas_sandbox_live.py` | must stay green through every slice |
| REFITS IN PLACE | `frontend/app/page.tsx` | disrupted hero → mandate form |
| REFITS IN PLACE | `frontend/app/recovering/[tripId]/page.tsx` | options table → blotter + search meter + new events |
| REFITS IN PLACE | `frontend/app/recovered/[tripId]/page.tsx` | receipt → weekly close |
| REFITS IN PLACE | `backend/app/api/routes.py` | `POST /api/disruptions` → `POST /api/desk/seed`; GET recovery → close/P&L |
| REFITS IN PLACE | `backend/app/agent/loop.py` | RecoveryAgent → DeskAgent; keeps `run(id, emit)` + step_budget + give-up paths |
| REFITS IN PLACE | `backend/app/models.py` | RecoveryResult/RecoveryStatus → desk result types; Offer/Layover stay |
| REFITS IN PLACE | `backend/app/fixture.py` | canned verdicts → curated priors + seeded portfolio |
| NEW | `backend/app/atlas/client.py` methods | `verify` / `confirm_price` / `create_order` / `pay` / `order_status` / `seat_select` |
| NEW | `backend/app/db/schema.py` tables | `mandate`, `positions` (held/booked, cost basis, marks), `ledger` (trades, allocations, reconciliations), `budgets` — first real DB writes |
| NEW | `backend/app/agent/brain.py`, `backend/app/agent/auditor.py` | desk brain (judgment) + risk-officer line |
| NEW | `backend/tests/test_desk_brain.py`, `backend/tests/test_desk_pipe.py`, `backend/tests/test_atlas_write_path.py` | desk-brain + pipe + opt-in live write-path tests |

## Build order

**S1 — D2 — Data foundation + desk SSE route.**
*Goal:* ledger/mandate/positions/budgets tables + seeded portfolio + desk SSE route.
*Files:* `backend/app/db/schema.py`, `backend/app/db/database.py`, `backend/app/api/routes.py`, `backend/app/fixture.py`, `backend/app/models.py`.
*Done:* `POST /api/desk/seed` persists a mandate + 5–6 positions + budgets; `GET /api/desk/{desk_id}/stream` emits `meta` with mandate + meter 20/20; `test_seed_persists_mandate_positions_budgets` + `test_seed_emits_meta_with_mandate_and_meter` pass; existing suite green.
*Demo checkpoint:* mandate card + search meter render from a live stream.
*Protects:* Feasibility (audit trail / Operating Scale), Demo completeness.

**S2 — D3–D4 — CRITICAL PATH: Atlas write-path proof.**
*Goal:* one real sandbox booking, end to end.
*Files:* `backend/app/atlas/client.py` (additive write-path methods), `backend/tests/test_atlas_write_path.py` (opt-in live).
*Done:* on one sandbox route: `offer verify` → `booking confirm-price` (only if verify reports a price increase) → `order create` → `order pay` (with the `payment_confirmation_id` from that create response) → `order status` asserted `TICKETED`, plus pre-order `booking seat list`/`seat select` (booking stage, `booking_id`-bound); `order pay` single-use and never retried; branching on `code` only.
*DAY-4 GATE:* if live book/pay fails, switch to **honest comparison mode** — decisions still logged and marked, so at least the judgment layer demos.
*Demo checkpoint:* a real sandbox ticket + seat assignment persisted with `ticket_asserted=true`.
*Protects:* Innovation x2 (makes autonomous settlement real), Feasibility.

**S3 — D5 — Desk brain (the judgment layer).**
*Goal:* hold/book judgment, volatility priors, mark-to-market, admitted-loss log.
*Files:* `backend/app/agent/brain.py` (new), `backend/app/fixture.py` (curated priors), `backend/app/agent/loop.py` (DeskAgent refit), `backend/tests/test_desk_brain.py`.
*Done:* mark-to-market over meter-gated fan-out; book/hold calls with rationale; admitted loss logged ("held too long, −$62, threshold adjusted"); all desk-brain + pipe tests pass against the stub client; step budget + give-up live.
*Demo checkpoint:* beats 0:40–1:20 (live hold/book with on-screen re-query) and 1:20–1:50 (admitted loss).
*Protects:* Innovation x2 (discretionary timing under uncertainty), Scenario-Experience.

**S4 — D6 — Reconciliation + allocation + escalation.**
*Goal:* the fintech beats.
*Files:* `backend/app/agent/loop.py`, `backend/app/api/routes.py` (escalation decision endpoint), `backend/app/models.py`.
*Done:* sandbox payments auto-reconciled against the ledger; on `PRICE_CHANGED`: absorb-from-contingency vs re-quote, never a second order; realized savings autonomously fund a real **pre-order** `booking seat select` (booking stage; ledger-only fallback on `SEAT_UNAVAILABLE`); fare spike exceeding the authority cap → `escalate` with two priced options + recommendation → one human click → executes. Tests `test_pay_never_retried_on_failure`, `test_no_second_order_on_price_changed`, `test_alloc_funds_seat_select_only_from_realized_savings` pass.
*Demo checkpoint:* beats 1:50–2:20 (allocation + reconciliation card) and 2:20–2:50 (escalation).
*Protects:* proposition 05 (reconciliation / agentic commerce), Compliance & Safety (human-in-loop at the mandate edge), x2.

**S5 — D7 — Frontend refit.**
*Goal:* mandate → desk → close, one refit pass.
*Files:* `frontend/app/page.tsx`, `frontend/app/recovering/[tripId]/page.tsx`, `frontend/app/recovered/[tripId]/page.tsx`, `frontend/lib/api.ts`, `frontend/lib/types.ts`, `frontend/lib/format.ts`.
*Done:* mandate form seeds the desk; blotter renders `mark`/`trade`/`loss`/`alloc`/`reconcile`/`escalate` idempotent-by-index with the meter always visible; weekly close shows P&L + admitted losses + verdict; replay-safe (buffer+replay, StrictMode absorbed); `next build` clean.
*Demo checkpoint:* cold open (0:00–0:10) through weekly close (2:50–3:00) runs screen-to-screen.
*Protects:* Demo 4/2/0 tiers — no half-finished screen.

**S6 — D8a — Hardening.**
*Goal:* error-code routing, give-up paths, no-retry discipline, meter enforcement.
*Files:* `backend/app/agent/loop.py`, `backend/app/atlas/client.py`, `backend/app/api/routes.py`, pipe tests.
*Done:* every non-success `code` routes per the contract table (no `message` parsing anywhere); give-up paths (budget exhausted, meter exhausted, step budget) emit disclosed reasons; tests assert `order pay`/`order create` are never retried; meter hard-stops the 21st search.
*Demo checkpoint:* a forced failure stops gracefully on screen with a disclosed reason.
*Protects:* Compliance & Safety, Cost Controllability (meter = bounded spend), Feasibility.

**S7 — D8b — Risk officer + demo choreography.**
*Goal:* the close's multi-agent flavor + scripted beats wired.
*Files:* `backend/app/agent/auditor.py` (new), `backend/app/fixture.py` (injected loss + spike scenarios), `backend/app/api/routes.py` (close).
*Done:* `GET /api/desk/{desk_id}/close` returns P&L, zero-policy-breach count, and the risk-officer's one-line challenge of one trade; loss + spike injections are one-flag; cold-open replay pre-warmed.
*Demo checkpoint:* beat 2:50–3:00 lands; the auditor line reads as a second agent, labeled honestly.
*Protects:* proposition 07 (light), Presentation.

**S8 — D9 — Demo rehearsal + video.**
*Goal:* rehearse to time, record.
*Done:* full runbook passes twice back-to-back within 3:00; every fallback (comparison mode, SSE replay) exercised once on camera-day dry run; disclosure register visible in-frame where required.
*Demo checkpoint:* the final 3-min video.
*Protects:* Demo (both tiers), all of the above by not breaking them.

## Sequencing note
S2's day-4 gate is the only hard dependency; everything downstream (S3–S7) runs identically in comparison mode, so no later slice blocks on the booking rail. S1 lands the DB early because `schema.py` was never written. Frontend is deliberately late (S5) — the backend events are the contract, and the screens just render them. Rehearsal (S8) gets a full day because the demo is scored in 4/2/0 tiers.
