# External: Atlas integration & environment

Durable operational context. No secret values are stored here — only names, locations, and state.

## Skill / CLI
- Atlas Flight Booking Skill (Path 02), open-source: `github.com/atlas-doc/atlas-flight-booking-skill`.
- CLI binary: `atlas-flight` (installed as a `uv` tool, v0.3.12). Also registered as a Qoder skill in this repo (`.agents/`, `skills-lock.json`).
- **Decision (AMENDED 2026-08-21 — see `docs/adr/0001-fork-atlas-skill-sandbox-auto-approve.md` §Amendment):** sandbox auto-approve is achieved by the backend calling the `atlas-flight` CLI directly as a subprocess (`--json`, no conversational checkpoints); **no fork is required for transport**. Never auto-approve against production.

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
- **RESOLVED (2026-08-21):** raw `depTime`/`arrTime` datetime format is **`%Y%m%d%H%M`** (e.g. `202609041435`), proven against live sandbox responses in `backend/app/atlas/client.py`. The docs' `YYYYMMSS` was a typo. Drives layover-duration math.

## Gate check (PASSED)
Sandbox returns rich connecting inventory. SIN→NRT, 2026-09-04, 1 adult: 19 options, 16 connecting via SGN / ICN / PUS / DMK, price $236–$691, layovers 1.75–13.4h, some overnight. Enough genuine trade-off for real reroute reasoning.

## Ticketing activation (OPEN — blocks verify/book/settle)
- **Observed status (probe 2026-08-21, `atlas-flight auth status --json`):** AUTHORIZED, `search_available=true`, **`ticketing_available=false`**, blocker **`TICKETING_ACTIVATION_REQUIRED`**. Search works; verify/order/pay/ticket do not. Do NOT record ticketing as live — an earlier note claiming `ticketing_available=true` was not reproducible and is withdrawn; treat `false` as the current state until a new probe proves otherwise. Comparison mode (decisions logged and marked, no orders) is the default until activation.
- Unlock path: ATRIP → **UAT Testing** → select modules → pass sandbox test cases per module.
- **Modules selected:** Flight Booking (Core), Ticket Fulfillment, Webhook Notification, Refund. (Skipped: VCC, Baggage, Seat, Regenerate Order, Post-ticketing Baggage, Void — not used by Waypoint.)
- **Observed 2026-08-21:** the **Seat** and **Refund** UAT modules show as **"Skipped"**. Flagged against the desk spec's seat-select alloc beat: if seats stay skipped/unavailable (`SEAT_UNAVAILABLE`), the savings allocation degrades to a **ledger-only** entry and order create carries `--seat-policy continue-without-seat` (per `docs/plans/waypoint/02-architecture.md` + `03-program-design.md`).
- UAT reference routes: Flight Booking connection `6E AMS→MAA`, direct `FA DUR→CPT`; Refund `7C PUS→CJU`.
- **Sequence:** pass Flight Booking's 2 cases FIRST (they mint the ticketed order the other three modules depend on) → Ticket Fulfillment → Refund → Webhook (pushed to a registered callback, not a chat pull — may need a callback URL configured; verify).

## Waypoint's use of the API (planned)
- Search + verify: read segments → map each `arrAirport`/`stopCities` to a country → apply curated transit-visa table for the hero passport.
- Order + pay: forked auto-approve in sandbox = the autonomous fare-difference settlement (deterministic; no LLM in this step).
- Assert real outcome: confirm `queryOrderDetails.do` shows an issued ticket/PNR before declaring success.
