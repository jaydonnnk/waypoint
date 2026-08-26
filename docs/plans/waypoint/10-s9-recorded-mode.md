# S9 — Recorded Atlas replay mode (spec + outcome)

Slice S9 of the Recorded-Mode Engine plan. Decision record: `docs/adr/0005-recorded-atlas-replay-mode.md`. Status: **DONE 2026-08-26** — gate 136 passed / 0 failed / 4 deselected (3 live + 1 eval).

The desk's Atlas rail is a subprocess against the installed `atlas-flight` CLI against a sandbox that demonstrably flaps. Slice 0 captured the real envelopes; S9 makes them replayable as a second rail with the honesty register intact.

## Step-0 outcome (bounded query-only re-poll)

One bounded, read-only re-poll session for the real TICKETED envelope of order `TESTA20260825233427052` (`backend/scripts/repoll_ticketed.py`, ≤10 minutes, never re-create / never re-pay):

- 4 polls of `atlas-flight order status --order-no TESTA20260825233427052 --json`, ~45 s apart — the sandbox answered fast each time (alive again), but the order stayed **`TICKETING_PENDING`** on every poll.
- The 4 poll envelopes were tee'd into the capture as seq 29–32 (step `order_status_s9_poll`) — append-only, exactly as captured.
- **No TICKETED envelope arrived → the recording stays composite.** Per the honesty rule, nothing was fabricated; the replay serves the capture exactly as recorded.

## Capture inventory (what the recording holds)

Raw artifact: `backend/data/recorded/booking_envelopes.json` — JSON-lines, one `{seq, step, cmd, envelope, captured_at}` per line (32 entries after Step 0). Honesty register: `backend/data/recorded/manifest.json` (regenerable via `backend/scripts/build_replay_manifest.py`).

| seq | step | code | role in replay |
|---|---|---|---|
| 1 | auth_status | AUTHORIZED | run 1 gate (superseded) |
| 2 | search | FLIGHT_SEARCHED | run 1 (superseded) |
| 3 | verify | OFFER_VERIFIED | run 1 (superseded) |
| 4 | create_order | PASSENGER_INFO_INVALID | run 1 failed create (Bug 3 found live) |
| **5** | **auth_status** | **AUTHORIZED** | **replay gate (last auth = replay anchor)** |
| **6** | **search** | **FLIGHT_SEARCHED** (8 offers, SIN→NRT 2026-09-04) | **replay search** |
| **7** | **verify** | **OFFER_VERIFIED** (price unchanged, 2 travelers) | **replay verify** |
| **8** | **create_order** | **PAYMENT_CONFIRMATION_REQUIRED** (order TESTA20260825233427052) | **replay create** |
| **9** | **pay** | **TIMEOUT** (transport lost the envelope) | **replay pay — the honest end** |
| 10–28 | order status ×17, then SERVICE_REQUEST_FAILED | TICKETING_PENDING → sandbox flap | recovery history (not scripted) |
| 29–32 | order status ×4 | TICKETING_PENDING | Step-0 re-poll (not scripted) |

The manifest's `script` is seq 5→6→7→8→9, every entry `provenance: "captured"`, `reconstructed_steps: []`, `ticketed_captured: false`, `composite: true`.

## Composite disclosure (honesty rule, enforced)

- A TICKETED envelope is **never fabricated** — only served when genuinely captured. The manifest's builder will only ever mark ticketing as captured from a real `order status` envelope with code TICKETED.
- Reconstruction is permitted for ONE class of step and is always flagged: a pay envelope the transport lost (TIMEOUT) may be reconstructed as `TICKETING_PENDING` **only when a genuine TICKETED envelope was captured afterwards** (the order's own state proves the payment landed). That case does not apply today — there is no TICKETED capture — so the replay serves the captured pay TIMEOUT verbatim and the cycle ends the way the capture ended (error event `TIMEOUT`, position held, nothing booked).
- The manifest's `wire_disclosure` rides the wire on the meta event's disclosures: *"recorded Atlas replay — composite capture with NO TICKETED envelope (sandbox flapped); the cycle replays the capture exactly as recorded, never live"*.
- Recorded is **never labelled live anywhere**: the client exposes `mode_label = "recorded"`; the loop's getattr-probe (the `reset_ticketing_cache` precedent) turns any would-be "live ticketing" label into **"recorded ticketing (replay)"** with the manifest's disclosure. Comparisons and all other disclosures stay byte-identical.

## Design (what changed)

- `backend/app/atlas/config.py` — `read_atlas_mode()`: strict parse of `WAYPOINT_ATLAS_MODE`, case-normalized; ONLY exact `recorded` selects replay; unset/typo/padded/anything else → `live`. Money safety never rests on this switch — it rests on the two existing fail-closed write gates (`WAYPOINT_LIVE_BOOKING` exact-"1" arm + `ticketing_live()`).
- `backend/app/atlas/recorded.py` — `RecordedAtlasClient(AtlasClient)` overrides ONLY the transport (`_run_json`, `_run_read_only`, `search`). Every write-path method (`verify`, `confirm_price`, `create_order`, `pay`, `order_status`, `auth_status`, seats) runs through the identical inherited parsers — the same-parser guarantee, **zero edits to `client.py`**. Matching = normalized command verb (leading non-flag tokens) + sequence index from the manifest script; an unmatched call raises typed `AtlasError("NO_RECORDING")` — fail closed. Two documented control overrides: `reset_ticketing_cache` (per-cycle cursor rewind — what makes consecutive cycles deterministic) and `poll_until_ticketed` (clock-free; the recording IS the timeline). No subprocess, no clock, no random, no sleep anywhere in the replay client.
- `backend/app/agent/loop.py` — ~4 additive lines after the live-ticketing label branch: the recorded wire label + client-supplied gate disclosure.
- `backend/app/api/routes.py` — the ONE seam: `build_atlas()` branches on `read_atlas_mode()`; `AGENT` gains `atlas=build_atlas()`. Nothing else changes.

## Determinism guarantee

Two full `DeskAgent.run` cycles on two freshly seeded identical desks (one SIN→NRT position so the reprice fan-out has no cross-thread queue race), shared recorded client, `pace=0`, fallback brain (no `DASHSCOPE_API_KEY`), throwaway SQLite:

- Emitted SSE event lists are **byte-identical** after normalizing ONLY the documented volatile fields — the desk uuid and `mark_at`/`created_at` wall-clock stamps (`test_recorded_determinism.py`). Blotter rows identical.
- The replay script is a pristine snapshot rewound on every `reset_ticketing_cache` (the loop's existing per-cycle hook), so every cycle replays from the first scripted envelope.
- No randomness sources exist in the replay path: no subprocess, no clock, no `random`; a subprocess tripwire proves zero process spawns per cycle.

## Test evidence

- `backend/tests/test_recorded_mode.py` — `read_atlas_mode` strict parse (typo/padded/unset → live), replay-through-real-parser (recorded search yields the identical Offer list a live parse of the same envelope yields; verify/create/pay parse the captured envelopes; pay TIMEOUT raises typed), fail-closed `NO_RECORDING` on unscripted calls, clock-free poll, missing/malformed artifacts refuse construction, subprocess tripwire, honesty register (manifest provenance), and the **contract-drift guard** (public callable surface of `AtlasClient` vs `RecordedAtlasClient` identical).
- `backend/tests/test_recorded_determinism.py` — the two-cycle byte-identity gate above.
- Gate: `cd backend; .venv\Scripts\python -m pytest` → **136 passed / 0 failed / 4 deselected** (baseline before S9: 114 passed).

## Running recorded mode

```powershell
cd backend
$env:WAYPOINT_ATLAS_MODE="recorded"; $env:WAYPOINT_LIVE_BOOKING="1"
.venv\Scripts\python -m uvicorn app.main:app
```

Seed + one cycle via the existing API (`POST /api/desk/seed`, stream, `/close`). The meta event says `recorded ticketing (replay)` with the composite disclosure; no `atlas-flight` subprocess ever spawns. Unset the mode (or any typo) and the desk is back on the live rail, unchanged. The recorded container sets `recorded` explicitly.
