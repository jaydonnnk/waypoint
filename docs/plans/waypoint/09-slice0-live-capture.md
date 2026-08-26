# S0 — Pax fix + live capture attempt (Recorded-Mode Engine, Slice 0)

Date: 2026-08-25. Plan: `Waypoint_Recorded-Mode_Engine` (approved).
Predecessor context: `WRITE-PATH-PAX-FIX-HANDOFF.md` (Bug 1 + Bug 2).

## What was fixed

### Bug 1 — missing top-level `contact` block (pre-existing working-tree fix, kept)
Both the desk write path and `tests/test_atlas_write_path.py` omitted the
top-level `contact` object required by
`.agents/skills/atlas-flight-booking/references/passenger-input.md`. The
uncommitted working-tree fix was kept as-is.

### Bug 2 — static `DEMO_PAX_JSON` replaced by a verify-driven builder (this slice)
The module-level constant `DEMO_PAX_JSON` was removed entirely from
`backend/app/agent/loop.py` and replaced by `_build_demo_pax_json(verified_travelers)`:

- Called inside `_write_position` **after** `verify` and **before**
  `create_order` (loop.py:699), from `verified.travelers` — the ONLY legal
  source of `traveler_id` / `passenger_type` (carry, never invent).
- Shape per `passenger-input.md`: one `name` field `"FAMILY/GIVEN"`,
  `passenger_type`, `gender` `"M"`, `birthday`, `nationality`, nested
  `document {type, number, issuing_country, expires}`, plus the top-level
  `contact` block (Bug 1).
- Untouched, per the handoff contract: the guard order
  (verify → confirm-price-if-increased → budget check → authority-cap check →
  create_order → pay → poll_until_ticketed), the no-retry discipline, and the
  `AtlasUnknownOrder` / `AtlasQueryOnly` handling. `backend/app/atlas/client.py`
  received zero edits.

### Bug 3 — duplicate demo identities rejected for multi-passenger orders (found THIS slice, live)
First live capture run (SIN→NRT, 2 adults) failed at `order create` with
`PASSENGER_INFO_INVALID` (empty `details.fields`): the builder gave every
passenger the SAME demo identity and passport number, which the upstream
validation rejects when a booking carries more than one traveler. Fixed in
the same builder: given-name suffix and document number now vary by
passenger index (`DEMO/WAYPOINTA` + `DEMO000001`, `DEMO/WAYPOINTB` +
`DEMO000002`, …). Yesterday's proven run (`tests/test_atlas_write_path.py`,
AMS→MAA) used ONE passenger, which is why the single-identity shape passed
there. Re-run with distinct identities: `order create` returned
`PAYMENT_CONFIRMATION_REQUIRED` — payload accepted.

## Capture script

`backend/scripts/capture_booking.py`:

- `CapturingAtlasClient(AtlasClient)` — subclass IN THE SCRIPT; overrides the
  transport surface only (`_run_json`, `_run_read_only`, `search`, plus an
  `order_status` override that widens READ timeouts — see below). Every raw
  envelope is teed as a JSON-lines entry `{seq, step, cmd, envelope,
  captured_at}` to `backend/data/recorded/booking_envelopes.json` BEFORE any
  parse decision; transport/parse failures get a synthesized code-only
  envelope so no failure goes uncaptured. `seq` is globally monotonic across
  appended runs.
- Driver: search SIN→NRT 2026-09-04 (2 adults) → cheapest bookable → verify →
  confirm-price only if `increased` → `create_order` (payload from
  `verified.travelers`) → `pay` → `poll_until_ticketed`; prints `order_no`
  and exits 0 only on a real `TICKETED`.
- Double-gated like `tests/test_atlas_write_path.py`: refuses unless env
  `WAYPOINT_WRITE_PATH=1`, and refuses unless the sandbox reports
  `AUTHORIZED` + `ticketing_available=true`.
- `AtlasUnknownOrder` / `DUPLICATE_BOOKING_SUSPECTED` → query-only recovery
  (ONE `order status` read via `follow_up_query_only`, never re-create).
  Pay failure or pay TIMEOUT → never re-pay; query-only recovery via
  `order status` (the pay may have landed despite the transport timeout).
- Codes/counts only are printed — never passenger data (passenger-input.md).

## Capture outcome (2026-08-25, run log)

Gates: `AUTHORIZED`, `ticketing_available=true` (both runs).

| run | step | envelope code | outcome |
|---|---|---|---|
| 1 | auth_status | `AUTHORIZED` | gate open |
| 1 | search (SIN→NRT 2026-09-04, 2 pax) | `FLIGHT_SEARCHED` | 8 offers |
| 1 | verify | `OFFER_VERIFIED` | price_change=unchanged, 2 travelers |
| 1 | create_order | **`PASSENGER_INFO_INVALID`** | Bug 3 found (identical identities) |
| 2 | auth_status | `AUTHORIZED` | gate open |
| 2 | search | `FLIGHT_SEARCHED` | 8 offers, cheapest current = 323.0 |
| 2 | verify | `OFFER_VERIFIED` | price_change=unchanged, 2 travelers |
| 2 | create_order | **`PAYMENT_CONFIRMATION_REQUIRED`** | order `TESTA20260825233427052` created — Bug-2/3 payload accepted |
| 2 | pay | **`TIMEOUT`** | the CLI `order pay` subprocess exceeded the client's 90s write cap (no envelope came back) |
| — | order status (recovery, patient probe) | `SERVICE_REQUEST_FAILED` | first direct probe, retryable=false |
| — | order status (recovery, widened read timeout) | `TICKETING_PENDING` ×17, then `SERVICE_REQUEST_FAILED` | order advanced past payment; ticketing still pending after ~45 min of polling, then the sandbox flapped to a terminal error |

Interpretation (branch-on-code): the pay transport timeout is ambiguous by
construction, so no re-pay was attempted — query-only recovery only. The
order later reporting `TICKETING_PENDING` means the sandbox accepted the
payment and is working ticketing; the ticket had not been asserted by the
end of the session. The sandbox intermittently answers only after minutes
(an `order status` envelope arrived once after ~3 minutes), which is why the
capture subclass widens READ timeouts (`RECOVERY_READ_TIMEOUT_SECONDS=240`)
while writes keep the client's own caps.

## Envelope inventory (`backend/data/recorded/booking_envelopes.json`)

JSON-lines, globally monotonic `seq`, entries `{seq, step, cmd, envelope, captured_at}`:

1. auth_status → `AUTHORIZED`
2. search → `FLIGHT_SEARCHED` (SIN→NRT)
3. verify → `OFFER_VERIFIED`
4. create_order → `PASSENGER_INFO_INVALID`
5. auth_status → `AUTHORIZED`
6. search → `FLIGHT_SEARCHED`
7. verify → `OFFER_VERIFIED`
8. create_order → `PAYMENT_CONFIRMATION_REQUIRED` (order_no TESTA20260825233427052)
9. pay → `TIMEOUT` (synthesized code-only entry; no envelope returned)
10. order_status_recovery → `TIMEOUT` (60s client cap)
11–27. order_status_recovery → `TICKETING_PENDING` (17 polls across ~45 min)
28. order_status_recovery → `SERVICE_REQUEST_FAILED` (terminal_error, retryable=false — sandbox flap; polling ended)

### Recording manifest note (composite/partial — S9 must honor this)
The recording is **composite and partial**: run 1 contributed seq 1–4 (ending
in the now-fixed `PASSENGER_INFO_INVALID`), run 2 contributed seq 5–9 (ending
in a pay transport `TIMEOUT`), and the recovery polls contributed seq 10+.
There is **no pay-success envelope and no TICKETED envelope** in the file.
S9's replay fixture therefore cannot yet serve a complete booking cycle from
this capture alone; options: (a) re-run the capture on a healthy sandbox day
(a fresh successful run appends a complete sequence; S9 should match the LAST
complete sequence), (b) fall back to the envelope sequence proven by
`tests/test_atlas_write_path.py` on 2026-08-25 (the handoff doc's proven
run), (c) composite manifest disclosure in the recording metadata. The
`step` labels + `cmd` verbs captured here are exactly the match keys S9's
`RecordedAtlasClient` needs (verb + sequence index).

## Verification

- Full backend gate (from `backend/` with `.venv`): `python -m pytest -q` →
  **114 passed, 0 failed, 4 deselected** (live).
- `python -m pytest tests/test_atlas_write_path.py -m live` was NOT run this
  session: step 3 did not complete cleanly (pay TIMEOUT → TICKETING_PENDING),
  and a live write-path run on a currently slow sandbox would spend sandbox
  money without new signal. Run it on the next healthy-sandbox session with
  `WAYPOINT_WRITE_PATH=1`.
- No commits made (per standing rule); no server left running.
