# Status: Waypoint — passport-aware flight disruption-recovery agent

- Gate 1 — Product: APPROVED 2026-08-20
- Gate 2 — Architecture: APPROVED 2026-08-20
- Gate 3 — Program Design: APPROVED 2026-08-20
- Gate 4 — Slice plan: APPROVED 2026-08-20

## Slices
- [x] Slice 1 — tracer bullet: mocked end-to-end, 3 screens + SSE, hardcoded recovery (BUILT IN QODER; reviewed + proven 2026-08-20: 3/3 backend tests pass, `next build` clean)
- [x] Slice 2 — real Atlas sandbox search (read path), NormalizedOffer→Offer (BUILT IN QODER; reviewed + honesty-corrected + proven 2026-08-20: 16/16 tests + live smoke, `next build` clean. Screen 3 shows an honest `status="pending"` state — no fabricated rejection/rationale/ticket until Slices 3-5.)
- [ ] Slice 3 — rules engine: visa + passport rules, curated data, fail-closed + freshness
- [ ] Slice 4 — Qwen judge (advise gate): rank + narrate over all offers
- [ ] Slice 5 — execute gate: fork auto-approve, verify→order→pay→assert ticket [needs UAT ticketing]
- [ ] Slice 6 — guards + audit: step budget, give-up, SQLite persistence
- [ ] Slice 7 — triggers (webhook + injected) + demo polish
Sequencing: slices 1–4 + 6 need NO ticketing — build now. Slice 5 blocks on UAT; stub booking until it clears.

## Slice-2 findings (carry forward)
- **Atlas transport = subprocess, not library import.** The skill's library entrypoint needs Python ≥3.12 but the backend runs 3.11, so `AtlasClient` subprocesses `atlas-flight ... --json`. Fine for stateless search. **WATCH for Slice 5:** booking is a stateful multi-step flow (search→verify→order→pay→query) + needs the auto-approve fork (ADR 0001). Before Slice 5, consider bumping the backend to Python 3.12 to enable the clean library seam (fork + session) rather than subprocess-per-step.
- **Sandbox returns comparison-mode only** right now (all offers `reference`/`bookable=false`) because ticketing is pending. The loop degrades gracefully (surfaces reference fares, flagged). Bookable candidates appear automatically once UAT clears — no code change.
- Confirmed Atlas datetime format = `YYYYMMDDHHMM` (docs' `YYYYMMSS` was a typo). Parser in `atlas/client.py` is multi-format tolerant.
- `same_ticket` is derived from carrier-continuity (the public contract drops `separateBookings`) — SECONDARY hint only per [[0002]], never decisive.

## Locked decisions (from planning)
- **Framing:** autonomous disruption-RECOVERY agent, positioned as a **rules-aware rebooking engine** ("checks the rules of your trip, not just the price"). Passport/transit-visa = the sharpest rule, NOT the whole idea. It must stay visible + load-bearing or the project collapses to the Level-1 "detect delay, suggest alts" floor that most teams submit.
- **v1 scope = general rules engine + 2 LIVE rules:** (1) transit-visa eligibility [hero], (2) passport 6-month validity [near-free, expiry already in payload — proves the engine isn't a one-trick lookup]. Engine built so more rules plug into one check interface; other rules (onward-ticket, health, MCT, loyalty, policy, carbon) are named roadmap, NOT built in v1. "Vision in v1" = the general engine is real in v1; we just ship 2 rules, not 8.
- **Team:** 2 people. Ambition: max — aim for a narrow Level-4, not a cut-down version.
- **Autonomy:** fork the open-source Atlas Skill and auto-approve verify/payment in **SANDBOX ONLY** (no real charges). Keep AI **out of** deterministic steps (visa lookup, fare-difference math, payment execution) to avoid the x0.5 "AI for AI's sake" penalty; AI drives only the **reroute judgment** (weighing price × time × visa × layover).
- **Data honesty:** transit-visa rules = curated table for ~6 hubs, stated openly as an approximation. Tourist-visa matrix (`ilyankou/passport-index-dataset`) as the base layer. Never claim global accuracy.
- **Disruption trigger:** prefer a real Atlas Webhook/Incident if the sandbox supports it; else inject a simulated cancellation, disclosed as injected.
- **Scope cuts:** one-way outbound only for the demo, one hero passport, ~6 curated transit hubs, current/bookable offers only.
- **Stack (Gate 2):** Qwen via DashScope (reasoning); Next.js/React front + Python FastAPI back; SQLite. Atlas = forked skill imported as a **library** (not CLI subprocess). Disruption trigger = **both** real Atlas webhook AND injected fallback.
- **Rules engine (Gate 3):** curated table keyed **`(hub × passport-nationality)`** with per-cell `airside_ok` + `max_hours` + provenance (`source`,`last_checked`) + coarse `has_airside_zone`. **Fail-closed:** missing/unknown → blocked from auto-execution. Ticket structure = secondary hint only, never decisive. See [[0002]].
- **Advise/execute split (Gate 3):** AI SEES all offers, labels ✅/⛔/⚠️, narrates rejections (advise = open); code auto-books+settles ONLY all-allowed offers, blocked/unknown need human override (execute = fail-closed wall). See [[0003]].
- **Demo choreography:** fail-closed rejects uncurated hubs → the scripted route MUST run through curated hubs and contain BOTH a cheaper `airside_ok:no` trap AND a pricier `airside_ok:yes` legal pick (candidates: SGN=trap, ICN=legal). Don't leave to sandbox chance.
- **Freshness (Gate 3):** cells trusted ≤ 6mo (airside) / ≤ 3mo (entry-fallback); past window → unknown → fail-closed. Explicit PROXY for "re-read before write" — price/seat gets a REAL live Atlas `verify`; visa has no live API, so freshness is the honest stand-in. Demo says so; never imply live visa verification.
- **Unknown posture:** `unknown` is the correct honest label for uncurated territory, kept intentionally (fail-closed depends on it). Minimize in demo by curating all demo-route hubs × demo passports (→ ~zero unknown on the happy path); keep ONE staged aged/unknown cell for the safety beat. Do NOT fabricate cells to erase unknown. Real coverage = curate the ~30–50 hubs carrying most connections, long tail stays honestly unknown.

## Judging north-star (Alibaba x Atlas rubric — 40 pts, 3-min demo)
- **Innovation 30% / 12** (Business-Form, Scenario-Experience, Ops-Cost) + AI multiplier — target **x2 on the reroute judgment** (needs base ≥2 to unlock).
- **Feasibility 30% / 12** (Operating Scale, Compliance & Safety, Cost Controllability). Must NOT be demo-only (demo-only → Operating Scale 1 → forces Cost 0).
- **Use of Qoder 20% / 8** — **80%+ of core built in Qoder or this scores 0.** Build in Qoder from the start.
- **Demo 20% / 8** (Completeness, Presentation) — strict 4/2/0 tiers. Show the FULL loop end-to-end in 3 min.
- **Target level: L4** (dependency-graph re-plan + settle fare difference). Visa constraint = what lifts it off the L1 floor.

## Three agent-failure guards to SHOW on screen (guide §26-31)
- **Infinite loop** → step budget + explicit give-up.
- **Stale data** → re-read/verify before every write (Atlas `verify` + `price_status`).
- **False success** → assert the real outcome (PNR/ticket actually issued), not a 200 OK.

## Open dependencies
- **Sandbox TICKETING activation** via ATRIP → UAT Testing. Blocks verify/book/settle. Modules selected: Flight Booking (Core), Ticket Fulfillment, Webhook Notification, Refund. See `docs/external/atlas-integration.md`.

## Notes for a fresh session
Read every doc in this folder + `docs/external/atlas-integration.md` before continuing. Atlas Skill CLI = `atlas-flight` (uv tool v0.3.12), auth in OS keyring, env currently sandbox. **Gate check PASSED:** sandbox returns rich connecting itineraries (SIN→NRT on 2026-09-04: 16 of 19 options connect, via SGN/ICN/PUS/DMK, price $236–$691, layovers 1.75–13.4h) — enough genuine trade-off for real reroute reasoning.

**BUILD WORKFLOW (from Slice 1 onward):** ALL implementation code is built in **Qoder** (Qwen), not Claude Code — required for the 20% Use-of-Qoder gate (80%+ of core must be Qoder-built or the category scores 0). Gates 1–3 planning docs (product/architecture/program-design/ADRs) were intentionally built here in Claude Code; that is fine (planning, not core code). Claude Code's role now: per-slice build briefs + cross-checking/reviewing Qoder's output; Jaydon makes the final call. Do NOT write implementation code in Claude Code.
