# Status: Waypoint — corporate travel treasury agent

The treasury concept is the locked direction. Gates were re-approved for it on 2026-08-21 after the visa-pivot was abandoned; the spec package was moved into this folder and re-branded Waypoint on 2026-08-22 (Phase 0).

- Gate 1 — Product: RE-APPROVED 2026-08-21 (treasury concept)
- Gate 2 — Architecture: RE-APPROVED 2026-08-21 (refit-in-place)
- Gate 3 — Program Design: RE-APPROVED 2026-08-21 (write-path contract aligned to the Atlas skill references 2026-08-22)
- Gate 4 — Slice plan: RE-APPROVED 2026-08-21 (S1–S8)

## Current spec package (read these)
- `docs/plans/waypoint/01-product.md` — what Waypoint is (the corporate travel treasury).
- `docs/plans/waypoint/02-architecture.md` — stack, module map, flow, SSE + Atlas command contract.
- `docs/plans/waypoint/03-program-design.md` — **primary build spec**: files, types, signatures, call stack, test plan.
- `docs/plans/waypoint/04-slices.md` — build order S1–S8.
- `docs/adr/0001..0004` — the non-negotiable decisions (0001 amended 2026-08-21; 0004 = two gates + curated priors applied to money).
- `docs/external/atlas-integration.md` — Atlas CLI/auth/env/UAT state.

History (do NOT build from): `docs/plans/waypoint/_archive-visa-pivot/` — the superseded visa-pivot Gate 1–4 docs, each banner-marked SUPERSEDED. `05-direction-y-consolidation.md` and `06-idea-pivot-transfer.md` record the pivot journey.

## Slices (S1–S8, per 04-slices.md)
- [x] S1 — D2 — Data foundation + desk SSE route: mandate/positions/ledger/budgets tables + seeded portfolio + `POST /api/desk/seed` + SSE `meta`.
- [x] S2 — D3–D4 — CRITICAL PATH: Atlas write-path proof — **DAY-4 GATE FALLEN BACK, not the live proof.** The real E2E booking (verify → create → pay → `TICKETED`) has never once succeeded (search-step `INTERNAL_ERROR`/9999 outage, re-confirmed 2026-08-23 — see session record); desk runs in the written, honest comparison-mode fallback the slice itself specifies. Downstream slices (S3–S5) built and demo identically on this fallback per the sequencing note below — nothing is blocked — but the live write path remains unproven.
- [x] S3 — D5 — Desk brain (the judgment layer): hold/book judgment, curated volatility priors, mark-to-market, admitted-loss log.
- [x] S4 — D6 — Reconciliation + allocation + escalation: auto-reconcile sandbox payments, `PRICE_CHANGED` absorb-vs-re-quote, pre-order seat alloc from realized savings (ledger-only fallback), one-click escalation.
- [x] S5 — D7 — Frontend refit: mandate → desk → close, one SSE stream, replay-safe.
- [x] S6 — D8a — Hardening: error-code routing, give-up paths, no-retry discipline, meter enforcement. Verify-and-fill done: all 4 `DeskStatus` values genuinely reachable and honest (`budget_exhausted` now emitted via a post-settle check), every give-up path flushes settle through the one-transaction `store.settle` and carries real P&L/losses so the books tie out, meter proven to hard-stop at 20 by test; suite re-baselined at 83 passed / 3 deselected (live) / 0 failed — see `S6-HANDOFF.md`.
- [ ] S7 — D8b — Risk officer + demo choreography: close endpoint, auditor line, injected scenarios, cold-open replay.
- [ ] S8 — D9 — Demo rehearsal + video: rehearse to time, record, exercise every fallback.

Sequencing: S2's day-4 gate is the only hard dependency; S3–S7 run identically in comparison mode if the booking rail fails.

## Atlas state (probe 2026-08-21)
- `atlas-flight auth status --json` → **AUTHORIZED**; `search_available=true`, `ticketing_available=false`, blocker **TICKETING_ACTIVATION_REQUIRED**.
- **Comparison mode is the default until activation**: sandbox offers come back `price_status=reference` / `bookable=false`, so decisions are logged and marked but no orders execute. The write path (S2) becomes live only once ticketing activates — see `docs/external/atlas-integration.md`.
- Seat and Refund UAT modules are currently "Skipped" in ATRIP — the alloc beat degrades to ledger-only (`SEAT_UNAVAILABLE` fallback) if that holds on demo day.

## Build workflow (unchanged)
ALL implementation code is built in **Qoder** (Qwen) — required for the 20% Use-of-Qoder gate (80%+ of core built in Qoder or the category scores 0). Planning docs (this package + ADRs) are built in Claude Code; that is planning, not core code. Claude Code's role: per-slice briefs + review of Qoder's output. Jaydon controls all git commits/sync (no AI co-author trailer).
