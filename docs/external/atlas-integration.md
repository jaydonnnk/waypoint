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

## Ticketing activation (LIVE — verify/book/settle now reachable, 2026-08-25)
- **Observed status (probe 2026-08-25, `atlas-flight auth status --json`, independently reproduced twice):** AUTHORIZED, `search_available=true`, **`ticketing_available=true`**, no blocker. The 2026-08-21 `false` reading and the earlier-withdrawn `true` note are both superseded — this is a fresh, reproduced probe, not a restored claim.
- **This changes runtime behavior, not just documentation status.** `_comparison_mode()` in `backend/app/agent/loop.py` runs comparison mode only when EITHER gate blocks: the `WAYPOINT_LIVE_BOOKING` env switch is unarmed, OR `ticketing_live()` reads false. The second gate no longer blocks. If `WAYPOINT_LIVE_BOOKING=1` is set when a desk cycle starts, the write path (verify → order → pay) now runs for real against sandbox — not a simulation. Confirm the env var is unset before seeding a desk unless a real sandbox order is intended.
- Unlock path: ATRIP → **UAT Testing** → select modules → pass sandbox test cases per module.
- **Modules selected:** Flight Booking (Core), Ticket Fulfillment, Webhook Notification, Refund. (Skipped: VCC, Baggage, Seat, Regenerate Order, Post-ticketing Baggage, Void — not used by Waypoint.)
- **Observed 2026-08-21:** the **Seat** and **Refund** UAT modules show as **"Skipped"**. Flagged against the desk spec's seat-select alloc beat: if seats stay skipped/unavailable (`SEAT_UNAVAILABLE`), the savings allocation degrades to a **ledger-only** entry and order create carries `--seat-policy continue-without-seat` (per `docs/plans/waypoint/02-architecture.md` + `03-program-design.md`).
- UAT reference routes: Flight Booking connection `6E AMS→MAA`, direct `FA DUR→CPT`; Refund `7C PUS→CJU`.
- **Sequence:** pass Flight Booking's 2 cases FIRST (they mint the ticketed order the other three modules depend on) → Ticket Fulfillment → Refund → Webhook (pushed to a registered callback, not a chat pull — may need a callback URL configured; verify).

## Known issues

### Windows keyring CredWrite error 1783 on multi-offer searches (WORKED AROUND 2026-08-25)
- **Symptom:** `atlas-flight search` (and every write call) fails with `error: (1783, 'CredWrite', 'The stub received bad data.')`. `auth status` still works — it is a read-only cached check. Reproduced live on v0.3.12 (latest published).
- **Confirmed root cause:** the CLI's default Windows Credential Manager keyring backend stores one search result's secrets in a SINGLE credential blob (`SearchSecrets.offers` dict). Windows caps generic credential blobs at ~2560 bytes; any search returning more than a handful of offers (routine — SIN→NRT returns ~19) overflows the cap and `CredWrite` fails. This is **blob-size overflow, NOT credential-count**: an earlier theory that clearing old keyring entries would fix it was tested and DISPROVED. This is a bug in the third-party `atlas-flight` CLI, not in Waypoint code (Waypoint only shells out via `subprocess.run` in `backend/app/atlas/client.py`).
- **Fix, part 1 — one-time per-machine environment setup (NOT app code, cannot be scripted into the repo):** install `keyrings.alt` into the atlas-flight CLI's own isolated venv, e.g. `uv pip install --python <path-to-that-venv>/Scripts/python.exe keyrings.alt`, then run `atlas-flight auth login` ONCE with `PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring` set to populate the new file-based store. The login is THREE-step and the last step is easy to miss: `auth login` only stages a pending token (~10-min window) and prints the browser URL; after the browser says "authorization complete", you MUST run `atlas-flight auth poll` (same env) to exchange the pending token for real credentials — `auth status` alone does NOT perform the exchange and keeps reporting `AUTHORIZATION_REQUIRED` (bit us live on 2026-08-25).
- **Fix, part 2 — app code:** `backend/app/atlas/client.py` does `os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyrings.alt.file.PlaintextKeyring")` at import time, so every CLI subprocess the backend spawns resolves the working file-based backend. `setdefault` keeps a local developer override possible.
- **Tradeoff (disclosed):** the plaintext file backend stores sandbox-only credentials unencrypted on disk. The encrypted `keyrings.alt` variant needs an interactive `getpass()` prompt per unlock and cannot work from a non-interactive subprocess. Credentials remain sandbox-only.
- **Upstream:** worth reporting to the ATRIP/Atlas team — their own UAT reference routes will hit the same wall if they return enough offers. Workaround, not a Waypoint design choice.

## Waypoint's use of the API (planned)
- Search + verify: read segments → map each `arrAirport`/`stopCities` to a country → apply curated transit-visa table for the hero passport.
- Order + pay: forked auto-approve in sandbox = the autonomous fare-difference settlement (deterministic; no LLM in this step).
- Assert real outcome: confirm `queryOrderDetails.do` shows an issued ticket/PNR before declaring success.
