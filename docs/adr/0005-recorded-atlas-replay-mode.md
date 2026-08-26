# 0005 — Recorded Atlas replay mode

## Status
Accepted — 2026-08-26

## Context
The desk's Atlas rail is a subprocess against the installed `atlas-flight` CLI (ADR 0001's amendment): browser OAuth + OS-keyring auth that cannot exist in a deployed container, against a sandbox that demonstrably flaps (Slice 0's capture ended in a pay-transport TIMEOUT, 17 `TICKETING_PENDING` polls over ~45 minutes, then a terminal `SERVICE_REQUEST_FAILED`). Demo day and deployment cannot depend on that rail being healthy — but the rubric scores "a build that only runs in a demo" at 1, so the alternative also cannot be a silent fake. Slice 0 captured the real envelopes (`backend/data/recorded/booking_envelopes.json`); S9 makes them replayable as a second rail with the honesty register intact.

## Decision
Add a recorded-replay rail as a subclass at the transport layer, selected by ONE strict env switch:

- **`WAYPOINT_ATLAS_MODE` (env), default live.** `read_atlas_mode()` parses strictly and case-normalized: ONLY the exact value `recorded` selects replay; unset, empty, typo, or anything else reads live. Fail-to-live never endangers money because **money safety rests on the two existing fail-closed write gates** — the human arm-switch `WAYPOINT_LIVE_BOOKING` and `ticketing_live()` (loop `_comparison_mode`) — never on this switch. The recorded container sets `recorded` explicitly.
- **`RecordedAtlasClient(AtlasClient)` overrides ONLY the transport** (`_run_json`, `_run_read_only`, `search`), so every write-path method runs through the identical inherited parse logic — the same-parser guarantee, with **zero edits to `client.py`**, the file proven live. Matching is normalized command verb + sequence index from the recording manifest; an unmatched call raises typed `AtlasError("NO_RECORDING")` — fail closed: nothing is served, never wrong data. Two documented control overrides serve replay hygiene: per-cycle cursor reset (`reset_ticketing_cache`) and a clock-free `poll_until_ticketed`. No clock, no random, no sleep, no subprocess anywhere in the replay client.
- **Recorded never wears a live label.** The client exposes `mode_label = "recorded"`; the loop probes it (existing `getattr` precedent) and the wire label becomes **"recorded ticketing (replay)"** with a matching gate disclosure wherever it would otherwise say "live ticketing". Comparisons and all other disclosures stay byte-identical.
- **Composite-recording disclosure rule.** The capture is composite and may lack a TICKETED tail. The manifest beside the recording names every step the replay serves: captured entries by `seq`, any RECONSTRUCTED step inline and flagged (a reconstruction must be proven by the order's own captured later state — e.g. a lost pay envelope when a genuine TICKETED status was captured). A TICKETED envelope is **never fabricated** — only served when genuinely captured. The manifest's disclosure rides the wire on the meta event; when the tail is missing, the replay honestly ends the way the capture ended.

## Consequences
- Demo and deployment decouple from sandbox health: the recorded rail replays real envelopes deterministically (two identical cycles are byte-identical after normalizing the documented volatile fields), with no credentials, no CLI, no outbound calls.
- Honesty is load-bearing, per 0002's standard: the rail says what it is on the wire; the provenance register (manifest) is auditable against the raw capture file.
- The live rail is untouched and stays the default; `client.py` received zero edits (drift between the clients is guarded by a public-surface contract test).
- Supersedes nothing: 0001's subprocess transport remains the live path; this ADR adds the replay rail beside it. ADR 0003/0004's two gates still own money — replay only changes WHERE the envelopes come from, never who executes.
- Desk cycles are serialized process-wide: a single module-level lock in `routes.py` wraps the agent run, so the single-active-cycle determinism guarantee holds under concurrent seeds — two cycles can never interleave the replay cursor.
