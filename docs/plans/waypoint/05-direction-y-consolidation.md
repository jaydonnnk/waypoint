# Direction Y Consolidation — Waypoint Pivot (AGREED, pending Jaydon cross-check)

Date: 2026-08-21 · Status: **AGREED in discussion; NOT yet rolled out into plan docs (01/03/04/00).**
This file is the single record of what was discussed and agreed, so it can be cross-checked before the Gate-4 amendment is applied.

---

## 1. Why we pivoted (decision history)

1. The original signature moment — "cheapest flight transits a hub your passport can't legally transit, agent rejects it and books the legal one" — **cannot be staged honestly** on live sandbox data for a strong passport:
   - Sandbox returns only SEA + Indian connecting hubs, which are airside-liberal for short layovers.
   - Schengen/US hubs (where traps concentrate) never appear.
   - Self-transfer is not reliably detectable in the data → cannot be truthfully flagged.
2. Ticketing is not activated on the sandbox account → booking/settlement is stubbed until UAT clears.
3. The interim workaround (passport-expiry co-hero) was judged **too weak to headline**: it's a trip-level block (fails every option equally), known/table-stakes territory, and it removes the option-vs-option contrast that made the pitch agentic. Kept as a secondary beat only.
4. Reading the hackathon guide changed the picture:
   - The guide's own **Level-4 benchmark** = "treat itinerary as a dependency graph — autonomously re-plan every downstream leg and settle fare difference in real time." The single-leg story reads as Level 1 ("detect delay, suggest alts" is the guide's literal Level-1 example).
   - The **x2 AI multiplier** ("impossible without AI") requires a visible judgment call, not a deterministic lookup.
   - Feasibility sub-dimensions (Operating Scale, Compliance & Safety, Cost Controllability) map directly to our existing ADRs and three-guards design — if shown on screen.

## 2. What was approved

**Direction A** (trip-graph re-planner) **with B as demo persona** (family, mixed passports) **and C as the third act** (budget-bounded agent that gives up gracefully), plus the later reframing (commitment node, recovery-not-planner positioning). Also approved: **the entitlement agent as stretch scope.**

### One-liner (agreed direction)

> "A cancelled flight doesn't kill one flight — it kills your whole trip. Waypoint re-plans every downstream promise, checks it against every passport in your party, and settles the fare itself."

Supporting framing lines:
- "Every rebooking tool treats your trip as one ticket. Your trip is actually ten promises. Waypoint re-plans the promises."
- "The airline has an agent for its books. Until now, you never had one for your trip."

## 3. Positioning (locked understanding)

- Waypoint is a **recovery agent** (mid-trip, reactive, thing-already-broken), **NOT a planner**.
- Explicitly **not Wanderlog**: Wanderlog = pre-trip collaborative planning, a crowded space (guide's Level-2 example is literally "generate travel plan from social media"). Recovery is near-empty white space.
- Category comparison: "the airline's operations control desk, but for the passenger, and autonomous."
- The word "planner" should never appear in the pitch.

## 4. Demo scenario (agreed shape)

- **Persona:** family of four, **mixed nationalities** (exact passports chosen by the Day-1 scenario hunt — must be passports the sandbox hubs actually discriminate against; also raises the probability that at least one hub is hostile to at least one member).
- **Trip:** round trip **SIN → BKK → SIN**, with a **declared commitment node**: *"sister's wedding, Friday 6pm."*
- **The graph** (a round trip is enough — the commitment is the node that creates the dependency):

```
┌─────────┐     ┌──────────────────────┐     ┌─────────┐
│ Leg 1   │────▶│ WEDDING — Fri 6pm    │────▶│ Leg 2   │
│ SIN→BKK │     │ (hard deadline node) │     │ BKK→SIN │
└─────────┘     └──────────────────────┘     └─────────┘
```

- **Disruption:** Leg 1 cancelled.
- **The judgment call (x2 moment), shown and narrated on Screen 2:**
  - Cheapest rebook arrives Saturday → legal and affordable but **useless** (misses the wedding). A normal rebooker books it.
  - Fastest legal rebook arrives Friday 4pm, +$89, but routes via a hub **one sibling's passport can't transit**.
  - Waypoint rejects both with stated reasons and books the combo that clears **all four passports AND protects the 6pm deadline**.
- **Payoff line:** "wedding protected, cost +$89" — contrasted against the original framing's much smaller fare-diff number.
- **Optional cascade beat:** outbound slips a day → agent flags "return now lands the day after; that costs a hotel night and a work day — here's the alternative that doesn't."
- **Before/after graph visual** on Screens 2/3: graph goes red on disruption, green after re-plan.

## 5. The innovation map (what nobody else offers)

Main innovation in one sentence: **every other tool rebooks a flight; Waypoint re-plans against your legal identity and your life's deadlines — two dimensions no consumer rebooking product even reads.**

| What nobody offers | Who "almost" does it | Why they don't |
|---|---|---|
| Passport-eligibility filtering on rebooking options | Airlines at check-in (Timatic) | It's a gate, not a planner — it denies you *after* you booked the wrong flight |
| Commitment-aware re-planning ("must arrive by Fri 6pm") | Nobody named | OTAs don't know your commitments and don't ask |
| Multi-passport group optimization ("one combo all four can legally take") | Nobody | OTAs price per seat and assume identical documents |
| Autonomous advise→execute with fail-closed guards | No consumer product | This is the agentic layer itself |

### Score path (from ~28 toward top band)

- **Innovation → 10–11:** secure x2 by making the judgment call *visible and narrated* (agent weighing cheaper-but-late / illegal-for-one-passport / +$89-but-protects-everything). Silent correctness scores x1; narrated trade-off scores x2. Never let the LLM compute visa rules or fare math (x0.5 trap) — deterministic checks stay in code per ADRs; LLM does orchestration/judgment/narration only.
- **Feasibility → 10–11:** show all three guards on screen — step budget ticking, `verify`-before-`pay` re-read, ticket assertion before success. Plus Act 3 performing Cost Controllability live.
- **Demo → 8/8:** strict 4/2/0 tiers; one half-finished screen drops a whole tier. Full loop, rehearsed with a timer, ticketing stub *disclosed* not hidden.
- **Qoder → 8/8:** 80%+ gate — ALL remaining implementation built in Qoder with spec runs preserved as evidence (existing workflow rule, unchanged).

## 6. The "no recovery" layers (approved)

When no legal/viable rebooking exists:

1. **Graceful give-up + fallback plan** — already Slice 6, promoted to **Act 3** of the demo. The only agent that knows its own limits and fails *profitably* instead of silently. Zero extra cost. **BUILD — mandatory.**
2. **Trip-Damage Metric** — novel business metric: total at-risk value of the disruption (hotel nights, the wedding, prepaid tour, work day). On give-up: "No rebooking fits. Best available: full refund on unused segments + tomorrow's first flight + one hotel night = **$213 spent to cap damage at $1,900**." ~half day. **BUILD.**
3. **Entitlement Agent** — see §7. **STRETCH: build only if day 5 shows slack; else narrate over the design.**
4. **Proactive immunity** (Waypoint watches the trip *before* disruption — passport-expiry warnings, buffer-risk restructuring) — **SAY ONLY**: one closing vision slide ("today we recover; tomorrow we prevent"). Do NOT build in the hackathon window.

## 7. The entitlement agent (approved as stretch)

### How it differs from the airline apology email ("please refer to the email for refund steps / hop on the next flight")

1. **Passive instructions vs. autonomous execution.** The email makes *you* work (find the form, upload docs, hit deadlines, chase portals — the friction is designed to discourage; take-up is famously low). The agent files the claim and executes the refund itself; the passenger does nothing.
2. **The airline's offer is blind to your trip — the agent judges it.** "Take our next flight" is exactly the blindness we attack: their next flight may arrive after the wedding, or connect through a hub one sibling's passport can't transit. The agent treats the airline's offer as an *input to evaluate*, not an answer to accept:

```
Airline offer: "Next JL flight, tomorrow 9:00, no extra cost."
Agent verdict: ⛔ arrives after the wedding
               ⛔ connection via HAN blocked for Priya's passport
Agent counter: rebook partner flight today 14:30 (duty-of-care),
               file hotel + meals claim for the delay,
               file compensation claim — executed, $312 recovered.
```

3. **Fragmented vs. consolidated.** Refund portal + duty-of-care form + compensation portal + travel insurance → collapsed into one executed outcome, one receipt.

### Scope guard

- Sandbox refund module is on the UAT list but unproven → runs behind a **disclosed stub** until UAT clears (same treatment as booking).
- Stretch only: day-5 slack check decides build vs. narrate-over-design.

## 8. Day-1 scenario hunt (hard gate, unchanged from earlier agreement)

**Goal:** land one specific *passport × route* pair where the live sandbox genuinely returns an illegal-for-that-passport option plus a legal one.

Protocol:
1. Run sandbox searches across 4–6 candidate O&D pairs.
2. Enumerate every connecting hub that appears.
3. Cross-check hubs against the curated rule table for 3–5 candidate passports (weak/mixed nationalities the SEA/Indian hubs actually discriminate against — e.g., Sri Lankan, Bangladeshi, Pakistani, Nigerian, Indian for hubs that require it).
4. Also test the **same-city airport-change trap**: itineraries arriving one airport and departing the other (Bangkok BKK/DMK, Jakarta CGK/HLP) *force* an immigration crossing — computable with certainty from IATA codes, no guessing, honest for any passport needing that country's visa. Data-discovered rule; great story.
5. **Gate: by end of day 2**, either hold a concrete trap scenario or commit to the fallback.

**Fallback:** the old X choreography survives as a subset — passport-expiry co-hero with honest fixture-proven transit rule. Weaker but shippable.

## 9. Slice deltas vs. current 04-slices.md

| Current slice | Verdict under Y |
|---|---|
| Slice 1–2 (built: SSE pipe, real Atlas search, mapping) | **Keep 100%** — unchanged |
| NEW: trip schema | Trip = legs + commitment node; multi-passport party; trip-damage calculator; downstream-feasibility check (connection time vs next leg, arrival vs deadline) |
| Slice 3 (rules engine) | **Keep, widen:** evaluate N passports per offer; curate hubs for family nationalities; add same-city airport-change rule |
| Slice 4 (Qwen judge) | **Expand:** narrates the deadline + multi-passport trade-off — this IS the x2 moment; must show rejected options with reasons |
| Slice 5 (execute gate) | **Keep** — unchanged (stubbed until UAT) |
| Slice 6 (guards) | **Promoted to Act 3:** budget ceiling, graceful give-up, fallback plan, trip-damage cap number |
| Slice 7 (choreography + polish) | **Rewritten:** family round trip + wedding commitment; graph before/after UI; disclosed ticketing stub; rehearsed to time |
| NEW stretch: entitlement agent | Judge the airline's default offer; execute refund/claim behind stub |

**Honest cost:** ~2–3 days of net-new work (trip schema + commitment field = data model + comparison checks, no hotel/events integration; multi-passport = N× the same rule call; graph UI = one new component on existing screens). Tight but real with ~8 days out and Slices 1–2 done.

## 10. Risks and constraints (honest register)

| Risk | Mitigation |
|---|---|
| No live transit trap found by end of day 2 | Hard gate → fallback to X choreography (survives as subset); graph/deadline story stands even if visa beat degrades to fixture-proven |
| Schedule overrun on graph UI / trip schema | Cut order: entitlement stretch first, then Act 3 polish, then fall back to X choreography |
| Ticketing never activates before demo | Disclosed stub + two-gate design shown; honesty scores better than glossing |
| Judges call it AI-for-AI's-sake (x0.5) | Deterministic checks stay in code (ADR position); LLM only judges/narrates/orchestrates |
| Curated visa table trust | Every rule shown in the video must be verifiable by a judge in ~30s of Googling; cite provenance on screen; never fabricate cells |

## 11. Expected outcome

- X (current path): ~24–30/40, with a single point of failure (no trap → weak expiry demo, no contrast).
- Y (adopted): ~28–35/40, worst case degrades **into** X rather than into nothing.
- The gap between 28 and 40 is **showing, not having** — the ADRs and guard designs already exist; Y's job is to make each a visible beat in 180 seconds.

## 12. Open items for cross-check

- [ ] Confirm exact family nationalities after the scenario hunt (day 1–2).
- [ ] Confirm the commitment beat (wedding) vs. alternatives (cruise/event) — wedding currently assumed.
- [ ] Confirm entitlement agent stays stretch (day-5 slack check).
- [ ] After cross-check: apply as Gate-4 amendment to 01-product.md, 03-program-design.md, 04-slices.md, 00-status.md (marked REVISED 2026-08-21, history preserved — not silently rewritten).
- [ ] Implementation stays in Qoder per existing workflow rule; this consolidation and the amendment are planning artifacts (Claude Code role: briefs + review).
