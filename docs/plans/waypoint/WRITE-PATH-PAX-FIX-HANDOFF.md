# Waypoint — Write-Path Passenger-Payload Fix, Handoff for Qoder

Not a numbered slice (S1–S8 are done/committed per `00-status.md`). This is a
**blocker fix** discovered while live-probing the S2 Day-4 gate on 2026-08-25:
the desk's real write path has never been *capable* of reaching `TICKETED`,
independent of the Atlas ticketing-activation flag. Two separate bugs, found
in sequence, live, today. One is already fixed in the working tree
(uncommitted — your call whether to keep it as-is or fold into your own pass).
The second is the real work here.

## Read first
- `docs/external/atlas-integration.md` — Atlas state (ticketing confirmed LIVE
  2026-08-25, re-probed fresh again today, still `AUTHORIZED` /
  `ticketing_available=true`).
- `.agents/skills/atlas-flight-booking/references/passenger-input.md` — the
  **authoritative payload contract**. Read this before touching anything;
  the bug is a mismatch against this doc, not against Waypoint's own tests.
- `docs/session_transfer.md` — background on why this was unproven until today.

## What was proven live today (in order)

1. **`AtlasClient().auth_status()`**, called fresh: `AUTHORIZED`,
   `search_available=true`, `ticketing_available=true`, no blocker. Confirms
   the platform-side gate is open right now — do not re-derive this from
   older docs, they've flapped before.

2. **`pytest tests/test_atlas_write_path.py::test_live_write_path_tickets -m live`**
   (env `WAYPOINT_WRITE_PATH=1`) — **PASSED**, once. Real
   `verify → order create → pay → order status == TICKETED`, sandbox money,
   real order. This is the first time that assertion has ever passed in this
   repo's history. A second immediate run on the same static passenger
   identity correctly got `DUPLICATE_BOOKING_SUSPECTED` from the sandbox
   itself — expected duplicate-guard behavior, not a bug, and evidence the
   first run really landed.

3. **The real desk cycle**, run end-to-end against the live server
   (`WAYPOINT_LIVE_BOOKING=1`, `POST /api/desk/seed`, one escalation
   approved via `POST .../decision`): reached a real `book` pick, hit the
   escalation gate correctly (amount over authority cap), human-approved,
   then failed with `OFFER_EXPIRED` — because the seeded demo route
   (`DAC→LHR`, the injected fare-spike scenario) returned
   `SEARCH_NO_RESULTS` from Atlas for that date. **Sandbox inventory gap,
   not a code bug** — confirmed by calling `client.search("DAC","LHR",...)`
   directly. Flagging separately below since it blocks the S7 escalation
   demo beat specifically.

4. **`DEMO_PAX_JSON` (the actual production constant), driven directly**
   against a route with confirmed live inventory (`SIN→NRT`,
   `2026-09-04`, real `search` → `verify` → `create_order`): failed with
   `PASSENGER_INFO_INVALID`. This is the real bug. See below.

## Bug 1 — contact block missing (FIXED in working tree, uncommitted)

`backend/app/agent/loop.py:72` (`DEMO_PAX_JSON`) and
`backend/tests/test_atlas_write_path.py:36` (`_build_pax_json`) both built
an order-create payload with **no top-level `contact` object**. The skill
reference (`passenger-input.md:39`) requires one (`name` always; `email` /
`mobile` optional unless the sandbox specifically asks for them via
`details.fields`). Both call sites now send one — this is what made step 2
above pass. Kept in the working tree; not committed (per your standing
rule — you commit, no AI co-author). Fine to keep as-is or fold into
whatever you land in the fix below.

## Bug 2 — `DEMO_PAX_JSON`'s whole passenger shape is wrong (NOT fixed — this is the ask)

Comparing `DEMO_PAX_JSON` (`backend/app/agent/loop.py:72-89`) against
`passenger-input.md:20-38` — the field names don't match the sandbox's
actual contract at all:

| `DEMO_PAX_JSON` has | Sandbox expects | Ref |
|---|---|---|
| `first_name` + `last_name` | one `name` field, `"FAMILY/GIVEN"` | passenger-input.md:24 |
| `type` | `passenger_type` | passenger-input.md:25 |
| `gender: "male"` | `gender: "M"` | passenger-input.md:26 |
| `birth_date` | `birthday` | passenger-input.md:27 |
| flat `document_type` / `document_number` / `document_expiry` | nested `document: {type, number, issuing_country, expires}` | passenger-input.md:29-34 |
| — (missing entirely) | `traveler_id`, **required**, per passenger | passenger-input.md:5, :23 |

The `traveler_id` gap is the structural one: the reference doc is explicit —
*"Carry each `traveler_id` and `passenger_type` from `data.travelers`; never
ask the user to invent IDs."* `traveler_id` only exists after a real `verify`
call returns it (`VerifyResult.travelers: list[dict]`,
`backend/app/models.py:186`, populated from the envelope at
`backend/app/atlas/client.py:389`). A module-level constant, computed once at
import time, **cannot ever contain a valid `traveler_id`** — it doesn't exist
yet when the constant is built. This is not a data-entry mistake, it's the
wrong shape of solution: a static payload for a field that is only known at
verify-time.

**The correct pattern already exists in this repo** —
`backend/tests/test_atlas_write_path.py:36-63` (`_build_pax_json`) builds the
payload *from* the `VerifyResult` it just received, pulling `traveler_id` and
`passenger_type` off `verify_result.travelers`. That function is the model to
follow. Confirmed live today (step 2 above) — it's the one that actually
reached `TICKETED`.

## The fix

Replace `DEMO_PAX_JSON` (a static constant) with a function built the same
way as the test's `_build_pax_json`, called from inside `_write_position`
(`backend/app/agent/loop.py`, the write path — search for where
`DEMO_PAX_JSON` is currently passed to `create_order`, around line 680)
**after** `verify` returns and **before** `create_order` is called, using
`verified.travelers` for the `traveler_id`/`passenger_type` pairs.

Concretely: `_write_position` already holds `verified` (the `VerifyResult`)
at the point it currently reaches for `DEMO_PAX_JSON`. Build the pax JSON
from `verified.travelers` right there instead of importing a pre-built
constant. Real name/document/nationality fields stay demo-disclosed sandbox
values (this is sandbox money only, ADR precedent for disclosed synthetic
data) — only the *shape* and the *traveler_id* sourcing need to be correct.

**Do not touch:** the guard order (verify → confirm_price-if-increased →
budget check → create_order → pay → poll_until_ticketed), the no-retry
discipline on writes, or the `AtlasUnknownOrder` / `AtlasQueryOnly` handling
around `create_order`/`pay`. All of that is correct and already proven live
in step 2 — the only thing wrong is the payload shape fed into it.

## Verify the fix

Same probe I used — re-run against a route known to have live inventory
(`SIN→NRT`, `2026-09-04`; `DAC→LHR` is a separate, unrelated inventory gap,
see below) and confirm `order status` reaches `TICKETED`. Then re-run
`pytest tests/test_atlas_write_path.py -m live` (`WAYPOINT_WRITE_PATH=1`) to
confirm nothing regressed there.

## Separate, lower-priority finding: DAC→LHR has no live sandbox inventory

Not part of this fix — flagging so it doesn't get conflated. The seeded
demo portfolio's injected escalation scenario
(`backend/app/fixture.py`, position 2, `DAC→LHR`) returns
`SEARCH_NO_RESULTS` from the real sandbox for its seeded date. This means
**the S7 demo escalation beat cannot complete a real write today**, even
after the pax-payload fix — the position never gets a bookable
`atlas_offer_id` to write against in the first place (fails at reprice, not
at write). Options, not decided: (a) pick a different curated route for the
injected scenario that has confirmed live inventory, (b) keep it
comparison-mode-only for the demo and accept that beat never shows a real
write, (c) re-probe closer to demo day in case it's a transient sandbox gap
rather than a permanent one. Your call — this doesn't block the pax-payload
fix above, which is provable on `SIN→NRT` regardless.

## Working tree state right now

- `backend/app/agent/loop.py` — contact-block fix applied (Bug 1), payload
  shape (Bug 2) still broken, uncommitted.
- `backend/tests/test_atlas_write_path.py` — contact-block fix applied,
  uncommitted. This file's `_build_pax_json` is otherwise already correct —
  it's the reference implementation for the loop.py fix.
- No commits made. No server left running (the live-armed dev server used
  for today's probes was stopped).
