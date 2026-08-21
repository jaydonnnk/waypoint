> SUPERSEDED — see docs/plans/waypoint/01-product.md
# Slices: Waypoint

Vertical slices, build order. Each ends in a working, testable state. Slice 1 is the tracer bullet; every later slice replaces a mock with real logic or adds exactly one capability.

## Build order

**Slice 1 — Tracer bullet (mocked end-to-end).**
Repo scaffold (Next.js front + FastAPI back). The 3 screens wired to `POST /api/disruptions`, which returns a HARDCODED `RecoveryResult`; SSE emits canned steps. No Atlas, no rules, no LLM.
*Ends:* open browser → click "Recover my trip" → watch the canned flow run end to end (cheap SGN struck out → ICN picked → fake ticket). Proves the whole pipe (front ↔ back ↔ stream ↔ screens).

**Slice 2 — Real Atlas search (read path).**
Import the forked skill as a library; `/api/disruptions` runs a REAL sandbox search for a hardcoded broken leg; map `NormalizedOffer → Offer`. Screen 2 lists real options.
*Ends:* real connecting itineraries (SGN/ICN/PUS/DMK) render live. *Test:* `test_offer_mapping_preserves_all_layover_airports`. *No ticketing needed.*

**Slice 3 — Rules engine: 2 live rules + fail-closed + freshness.**
Author curated `transit_hubs.yaml` (demo hubs, with provenance) + `passport_index.csv` + `iata_country.csv`. `TransitVisaRule` + `PassportValidityRule` assess each real offer → `allowed`/`blocked`/`unknown` with freshness. Placeholder pick = cheapest executable (deterministic, no LLM yet).
*Ends:* on real data, the cheapest option shows struck-out ⛔ and a legal one is highlighted — the core contrast. *Tests:* all rule / fail-closed / stale-cell / execute-filter tests + `test_agent_picks_cheapest_EXECUTABLE_not_cheapest_overall`.

**Slice 4 — Qwen judge (advise gate).**
`RerouteJudge.rank` over ALL assessments → chosen (executable) + narration; screen 2 streams the AI reasoning, screen 3 shows the rationale over the rejected options.
*Ends:* AI narrates *why* it rejected the cheap illegal one and picked the legal one. *Test:* `test_judge_sees_all_and_narrates_rejected`. *Dep:* `DASHSCOPE_API_KEY`.

**Slice 5 — Execute gate: book + settle + assert.**
Fork the skill (add sandbox-only auto-approve). `verify → create_order → pay → get_order`; execute wall (only executable offers); assert PNR + ticket before success. Screen 3 shows the real settled fare + PNR/ticket.
*Ends:* full autonomous rebooking completes in sandbox with a real test ticket. *Tests:* `test_agent_reverifies_before_booking`, `test_agent_asserts_ticket_before_success`, `test_execute_wall_rejects_blocked_and_unknown`. *Dep:* **UAT ticketing activated** + the fork.

**Slice 6 — Guards + audit persistence.**
Step budget + give-up (`no_legal_option`) path; SQLite persists verdicts / decisions / orders; the audit trail is visible.
*Ends:* forced no-legal-option → graceful give-up; the DB holds the full decision trail. *Tests:* `test_agent_respects_step_budget`, `test_agent_gives_up_when_no_executable_option`, `test_recovery_persists_verdicts_and_decision`.

**Slice 7 — Triggers + polish.**
Real `POST /api/webhooks/atlas` (best-effort) + injected `/api/disruptions`; wire the demo choreography (curated trap + legal hubs); the "also caught: passport expires too soon" beat; presentation polish for the 3-min demo.
*Ends:* demo-ready — both triggers work, the scripted route runs the full loop, rehearsed to time.

## Sequencing note (works around the open dependency)
Slices **1–4 and 6** need **no ticketing** — build them now while UAT activation is pending. Only **slice 5** (real booking) blocks on ticketing. Until UAT clears, slice 5 uses a **stubbed booking** (returns a mock ticket) so the pipeline stays end-to-end; swap in real book+settle the moment ticketing is active. This keeps the whole team unblocked.
