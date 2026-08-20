# Architecture: Waypoint

## Fit
New app, two halves, one repo:

- **Frontend** — Next.js/React. The 3 demo screens + a live agent-reasoning stream. Talks to the backend over REST + an SSE event stream. Scoped to the demo surface only (no extra pages).
- **Backend** — Python FastAPI. Hosts everything that thinks: the recovery agent loop, the rules engine, Atlas integration, Qwen calls, SQLite.
- **Atlas integration** — the **forked** `atlas-flight-booking` skill, used by the backend as an imported library (reuse its auth/keyring, env config, and typed `NormalizedOffer`/`NormalizedSegment` models). CLI-subprocess is the fallback if the library seam is painful. The fork adds a **sandbox-only auto-approve** for the price/payment checkpoints (see ADR 0001).
- **Rules engine** — a pluggable `Rule` interface inside the backend. v1 rules: `TransitVisaRule`, `PassportValidityRule`. Data-backed (see Data).

Deterministic code owns: rules checks, fare-difference math, order/pay execution. **Qwen owns only the reroute judgment** (rank the *legal* options under price × time × layover, with a written rationale). This split is deliberate — keeps AI out of the deterministic steps to avoid the x0.5 penalty, and puts it where the x2 judgment lives.

## Endpoints (backend REST + stream)
- `POST /api/trips` — seed a booked trip (passenger profile + segments). Demo setup.
- `POST /api/disruptions` — inject a cancellation on a trip → kicks off recovery. (Disclosed-as-injected trigger.)
- `POST /api/webhooks/atlas` — receive a real Atlas Incident/webhook → same recovery entrypoint. (Preferred trigger if sandbox supports it.)
- `GET  /api/trips/{id}` — trip + current status.
- `GET  /api/trips/{id}/recovery` — the recovery result (chosen vs rejected, fare diff, ticket).
- `GET  /api/trips/{id}/stream` — **SSE** stream of the agent's live reasoning steps (drives screen 2).

## Data (SQLite)
- `passengers` (id, name, passport_country, passport_expiry, doc_number, issuing_country)
- `trips` (id, passenger_id, status, created_at)
- `segments` (id, trip_id, dep_airport, arr_airport, dep_time, arr_time, flight_number, direction, status[active|cancelled])
- `offers` (id, trip_id, atlas_offer_id, price, currency, total_minutes, segments_json, price_status, bookable) — recovery candidates
- `rule_verdicts` (id, offer_id, rule_name, allowed, reason) — **the audit of every rules check**
- `decisions` (id, trip_id, chosen_offer_id, rejected_cheapest_offer_id, rationale, step_count, created_at)
- `orders` (id, trip_id, offer_id, atlas_order_no, pnr, ticket_number, fare_diff, settled, ticket_asserted, created_at)

Main queries: insert offers on recovery search → insert rule_verdicts per offer → select legal offers (`rule_verdicts.allowed` all true) → record decision → record order after pay + ticket assertion. `rule_verdicts` + `decisions` are the persisted evidence the agent reasoned correctly (helps Operating Scale + Compliance).

**Bundled data files (not services):** `passport-index` matrix (CSV, tourist-visa base layer), curated transit-hub table (YAML, ~6 hubs, airside-vs-immigration + hour thresholds), IATA→country map (CSV).

## Flow (main path, end to end)
1. **Setup** — seed a booked, ticketed trip (passenger + segments) from a real Atlas search+order once ticketing is activated; fixture until then.
2. **Trigger** — Atlas webhook → `/api/webhooks/atlas`, or injected `/api/disruptions`. Mark a segment `cancelled`.
3. **Agent loop** (bounded by a **step budget**):
   1. Re-read trip state (never act on cached world).
   2. Search Atlas alternatives for the broken leg (forked skill). Store `offers`.
   3. Rules engine: run each rule on each offer → store `rule_verdicts`. Keep only all-allowed offers.
   4. **No legal option → give up gracefully** (guard) and surface why.
   5. **Qwen ranks the legal options** (price × time × layover) → pick + rationale.
   6. Re-verify the chosen offer live (`verify`; **stale guard**). Price moved → log old/new, auto-approve (sandbox).
   7. Order + pay (forked **auto-approve, sandbox**) = the autonomous fare-difference settlement (deterministic, no LLM).
   8. **Assert real outcome** — `queryOrderDetails` shows PNR + ticket issued. Only then mark success.
   9. Emit every step to the SSE stream (screen 2).
4. **Present** (screen 3) — rejected cheapest vs chosen legal, fare diff settled, PNR/ticket.

The three guards (step budget / re-read-verify / assert-outcome) are wired into the loop itself, and each is visible on screen — they are both correctness and scored Feasibility.

## External
- **Atlas sandbox** via forked `atlas-flight` — auth in OS keyring (no env var), env = sandbox. Uses search / verify / order / pay / queryOrderDetails + webhook/incident.
- **Qwen** via Alibaba DashScope — env var name `DASHSCOPE_API_KEY` (value never in repo).
- **Atlas webhook callback** — a public URL registered in ATRIP for the real disruption trigger; env name `WAYPOINT_PUBLIC_URL`. (Tunnel in dev.)
- No other third-party services; passport/visa/IATA data is bundled.
