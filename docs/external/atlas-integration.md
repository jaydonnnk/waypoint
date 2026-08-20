# External: Atlas integration & environment

Durable operational context. No secret values are stored here — only names, locations, and state.

## Skill / CLI
- Atlas Flight Booking Skill (Path 02), open-source: `github.com/atlas-doc/atlas-flight-booking-skill`.
- CLI binary: `atlas-flight` (installed as a `uv` tool, v0.3.12). Also registered as a Qoder skill in this repo (`.agents/`, `skills-lock.json`).
- **Decision:** we will FORK this skill so the price-increase and payment checkpoints can auto-approve in **sandbox only** (see `docs/adr/`). Never auto-approve against production.

## Auth & environment
- Auth: ATRIP OAuth via browser; token + credentials live in the **OS keyring** (Windows Credential Manager). Never in env vars, code, or these docs.
- Sandbox access key id lives in the ATRIP profile (AK/SK tab). Secret key stays in keyring — do not paste anywhere.
- Switch env: `atlas-flight environment use sandbox --json` (and `... use production ...`). After switching, start a fresh search — do NOT reuse earlier offers.

## API surface (confirmed)
- Flow: `search.do → verify.do → order.do → pay.do → queryOrderDetails.do`; alt `getOffers.do`, `getOfferPrice.do`.
- Search response: `fromSegments[] / retSegments[]`, each segment has `depAirport`, `arrAirport` (3-letter IATA), `depTime`, `arrTime`, `flightNumber`, `stopCities` (`null`/blank = nonstop). **No direct-only filter** in the request — connections come back mixed; filter client-side.
- `price_status`: `reference` (comparison only) vs `current`/`verified` (bookable). Only bookable offers can proceed to order.
- Groups also include **Webhook & Incident APIs** (candidate real disruption trigger) and **Refund**.
- Docs: `resources.atriptech.com/api-document/readme-1` · full machine map at `resources.atriptech.com/llms.txt`.
- **TODO:** confirm raw `depTime`/`arrTime` datetime format on first live order (docs show a likely typo `YYYYMMSS`/`YYYYMMDD`). Drives layover-duration math.

## Gate check (PASSED)
Sandbox returns rich connecting inventory. SIN→NRT, 2026-09-04, 1 adult: 19 options, 16 connecting via SGN / ICN / PUS / DMK, price $236–$691, layovers 1.75–13.4h, some overnight. Enough genuine trade-off for real reroute reasoning.

## Ticketing activation (OPEN — blocks verify/book/settle)
- Current status: `TICKETING_ACTIVATION_REQUIRED`. Search works; verify/order/pay/ticket do not.
- Unlock path: ATRIP → **UAT Testing** → select modules → pass sandbox test cases per module.
- **Modules selected:** Flight Booking (Core), Ticket Fulfillment, Webhook Notification, Refund. (Skipped: VCC, Baggage, Seat, Regenerate Order, Post-ticketing Baggage, Void — not used by Waypoint.)
- UAT reference routes: Flight Booking connection `6E AMS→MAA`, direct `FA DUR→CPT`; Refund `7C PUS→CJU`.
- **Sequence:** pass Flight Booking's 2 cases FIRST (they mint the ticketed order the other three modules depend on) → Ticket Fulfillment → Refund → Webhook (pushed to a registered callback, not a chat pull — may need a callback URL configured; verify).

## Waypoint's use of the API (planned)
- Search + verify: read segments → map each `arrAirport`/`stopCities` to a country → apply curated transit-visa table for the hero passport.
- Order + pay: forked auto-approve in sandbox = the autonomous fare-difference settlement (deterministic; no LLM in this step).
- Assert real outcome: confirm `queryOrderDetails.do` shows an issued ticket/PNR before declaring success.
