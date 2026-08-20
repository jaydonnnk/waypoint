# Waypoint — Session Handoff for Qoder

Waypoint's planning (software-factory Gates 1–4, all approved) was done in a separate tool (Claude Code). **Your job is to build the code, in Qoder, starting at Slice 1.** This file is the session signal that is NOT already written in the spec docs. Read it, then read the specs it points to.

## Read these first (your spec, in order)
- `docs/plans/waypoint/00-status.md` — gate approvals, all locked decisions, judging north-star, the 3 guards, the build workflow.
- `docs/plans/waypoint/01-product.md` — what Waypoint is (a rules-aware rebooking engine).
- `docs/plans/waypoint/02-architecture.md` — stack + system shape.
- `docs/plans/waypoint/03-program-design.md` — **your primary build spec**: files, types, signatures, call stack, test plan.
- `docs/plans/waypoint/04-slices.md` — build order. You are on **Slice 1**.
- `docs/adr/0001..0003` — the non-negotiable decisions and *why*.
- `docs/external/atlas-integration.md` — Atlas creds/env/UAT state.
- `docs/plans/waypoint/mockups/*.html` — the visual target for the 3 screens.

## Session signal not captured in those docs

### Why this idea (it survived a reality-check the docs don't record)
~25 ideas were brainstormed over 3 rounds; almost all were killed as already-served (Hopper/Kaiban auto-rebooking, Midway/Tripmatch meet-in-the-middle, maritime crew-travel tools, price-drop rebookers, error-fare finders). Visa-aware recovery survived because it sits on a **structural blind spot**: mainstream tools cannot filter by the passenger's passport, so they rebook people onto connections they legally can't board. That blind spot is the entire moat — keep the passport/visa constraint visible and central in everything.

### State of the world
- **Atlas sandbox gate PASSED:** real connecting itineraries return (SIN→NRT 2026-09-04: 16 of 19 options connect via SGN / ICN / PUS / DMK, $236–$691). Search works today.
- **Sandbox ticketing is NOT active** (`TICKETING_ACTIVATION_REQUIRED`). UAT test cases must be passed first (modules selected in ATRIP: Flight Booking [Core], Ticket Fulfillment, Webhook Notification, Refund). Booking/verify/pay are blocked until then. **Slice 5 depends on this; Slices 1–4 and 6 do not** — build those in parallel; stub booking in Slice 5 until ticketing clears.
- A throwaway Slice-1 tracer was built in the planning tool to prove the design, then **deleted**. You rebuild Slice 1 fresh, in Qoder.

### Traps (named temptations — do not repeat)
- **Do not let the demo read as "flight delayed → here are alternatives."** That's the Level-1 floor most teams submit. The passport/visa constraint must be visible and load-bearing.
- **Do not put the LLM (Qwen) inside deterministic steps** — transit-visa lookup, fare-difference math, and payment execution are plain code. Qwen only *ranks and narrates* the already-classified options. (The rubric penalizes "AI for AI's sake.")
- **Do not overclaim visa accuracy.** Curated `(hub × nationality)` table, stated openly as an approximation, per-cell provenance. Fail-closed: missing/unknown → blocked from auto-booking.
- **Do not skip the 3 guards** (step budget + give-up; re-read/verify before every write; assert a real ticket was issued, not a 200 OK). They are both correctness and scored points.
- **Fail-closed rejects uncurated hubs.** The scripted demo route must run through curated hubs and contain BOTH a cheaper `airside_ok:no` trap (SGN) AND a pricier `airside_ok:yes` legal pick (ICN), or the agent completes nothing.
- **The two-gate split is load-bearing:** the AI *sees and narrates every* option (advise = open); code enforces that only all-allowed offers auto-book (execute = walled). The AI can never unblock a rule.

### Working style (how the operator, Jaydon, runs this)
- Honesty over agreeableness. Never invent a number, fare, or fact. Flag uncertainty explicitly. No opening praise.
- Plain language, no buzzwords. Explain your reasoning — Jaydon is learning while building.
- Gate discipline: propose a plan and get his approval before large changes (Qoder Spec Mode fits this).
- **Jaydon controls all git commits/sync himself.** Do not auto-commit. No AI co-author trailer on commits.
- A separate reviewer (Claude Code) cross-checks your output against these specs; Jaydon reconciles.

## Slice 1 — the immediate task
Build the tracer bullet exactly as scoped in `04-slices.md` → Slice 1:
- Repo scaffold: **Next.js** frontend + **Python FastAPI** backend (per `02-architecture.md`), **SQLite** wired but unused yet.
- The **3 screens** (visual target = `mockups/*.html`) driven by a **hardcoded** `RecoveryResult` streamed over **SSE**.
- Endpoints: `POST /api/disruptions` → returns a trip id; `GET /api/trips/{id}/stream` → SSE of the agent's live steps; `GET /api/trips/{id}/recovery` → the final result.
- Canned demo data: cheapest **$236 via SGN** (blocked — Vietnam self-transfer visa) struck out; chosen **$458 via ICN** (allowed — Korea airside); fare diff **+$92** auto-settled; a PNR + ticket number. **Shapes must mirror the Gate 3 domain types** so Slices 2–5 swap in real logic without changing the frontend contract.
- **No Atlas, no rules, no LLM in Slice 1.** It only proves the pipe.
- Done = open the browser, click "Recover my trip", watch the steps stream, land on the recovered before/after screen.
