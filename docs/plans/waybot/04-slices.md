# Vertical Slices: Waybot — G1–G6 Gap-Closure Program

Tracer-bullet first. Each slice ends in a working, testable state and is proven before the next. One capability per slice. No horizontal building.

## Build order

### Slice 1 — TRACER: seed-without-start → code → cycle fires (end to end, no bot, no travelers)
The skeleton the whole program hangs on, wired through and demonstrable.
- Schema: 7 mandate columns + shim entries; `TravelerRow`, `ChatBindingRow` tables. `app/events.py` sink (publish/subscribe, fire-and-forget).
- Routes: extract `_start_cycle(desk_id)`; `seed_desk` persists `awaiting_travelers` + token + code-hash and does **not** start; `POST /confirm` hash-checks → `_start_cycle`.
- Frontend: `page.tsx` share card (link + code, static progress 0/N); desk page code-entry panel gated on `awaiting_travelers`; `types.ts` + `api.ts` new fields.
- **Proof:** seed a desk → assert no task running (still `awaiting_travelers`) → POST /confirm with the code → cycle runs to close exactly as today. `test_desk_lifecycle` seed-no-start / wrong-code / right-code green. **The existing full suite still passes** (default desk path unchanged: lifecycle defaults `released`).

### Slice 2 — Waybot skeleton + deep-link bind (real bot, no passports yet)
- `app/bot/` skeleton: `build_application(token, sink, store)`; `/start?token=` → `bind_chat` → session. Started in `main.py` lifespan, gated on `WAYPOINT_BOT_TOKEN` (absent → skipped), supervised + backoff + global error handler.
- Bot subscribes to the sink; `travelers_complete` (fired manually via a test hook this slice) → manager ping.
- **Proof:** run locally with a token; tap the deep link → chat binds to the desk → a manually published `travelers_complete` reaches the manager chat. App still boots with no token.

### Slice 3 — Passport extraction + MRZ gate + G1 write-path swap (G1 CLOSED)
- `bot/extract.py` (Qwen-VL over brain transport), `bot/mrz.py` (TD3 check-digit gate, ISO-3→ISO-2 fail-closed), typed-entry fallback (CSV-validated), `deleteMessage` + no-persist image.
- `app/pax.py` `build_pax_json` with **PaxBuild (hold on gated desk missing roster; demo only for ungated)**; swap the `loop.py:725` call site. `add_traveler`; backend `travelers_complete` (dedupe).
- **Proof:** `test_mrz`, `test_pax_builder` (carry-not-invent, gated-hold, ungated-demo, distinct-docs), `test_travelers_complete_fires_once_backend` green. Recorded-mode e2e: real names appear in the order payload; replay stays byte-safe. Bot photo → masked confirm → stored.

### Slice 4 — Security guard module (the promises become failing tests)
Land the guards as code + `test_waybot_security.py` (7 cases), in the injection-suite style.
- Code-hash constant-time + attempt cap + TTL; 128-bit token single-purpose; role separation; submission integrity (checksum/dup/oversize); PII masking in events/logs + no image artifact; MRZ-as-data containment; confirm/approve one-shot (410).
- **Proof:** all 7 security tests green; the PII-scan test actually fails if a doc number is unmasked (verify by temporarily unmasking → red → re-mask → green).

### Slice 5 — Pre-trip approval, pinned resume (G4 CLOSED)
- Approval checkpoint after judgment (per-position); `set_approved_offer` + `pending_approval` + identity snapshot; end cycle; `DeskEvent(pending_approval)` → bot Approve/Hold. `POST /approve` → `_start_cycle` pinned. One-reapproval cap; hold one-shot.
- **Proof:** `test_approve_pins_offer` (no re-judgment — brain-call-counting stub), `test_pinned_price_move_beyond_contingency_escalates`, `test_unbookable_pin_one_reapproval_then_hold` green. Live/recorded: manager taps Approve → the pinned offer books.

### Slice 6 — Travel pack (G5 CLOSED, bounded)
- `map_offer` keeps carrier + cabin_class; `Offer`/`Segment` fields; identity snapshot at approval; `DeskEvent(ticketed, paid+order_no)` → per-traveler packs + manager summary; "reference not PNR" disclosure.
- **Proof:** on TICKETED, each traveler chat receives a pack with correct flight numbers/carrier and the actual paid amount + order_no; disclosure present.

### Slice 7 — Policy filter (G2 CLOSED to data limits)
- `search(..., airlines=)`; client-side cabin/time filter on Offer/Segment; cheapest policy-passing; zero-pass → escalation; filters disclosed in decision events. Manager sets policy at seed (`SeedRequest` fields + start-screen inputs).
- **Proof:** `test_policy_filter` (cheapest-among-passing, zero-pass-escalates) green; replay unaffected (recorded ignores `--airline`).

### Slice 8 — Trip construction (G3 PARTIAL)
- Bot trip-spec: city→IATA via `iata_city.csv` with confirm; depart window; pax from roster. Seed builds a single-position desk from the spec. Multi-date = one search per candidate date.
- **Proof:** bot collects a spec → a single-position desk seeds from it → books. (Meeting-anchor scheduling stays out.)

### Slice 9 — Duty of care (G6 PARTIAL)
- Scheduled read-only `order_status` poll (existing retry policy); status change → `DeskEvent(disruption)` → bot alert, honest label. No rebooking verb.
- **Proof:** a simulated status change pushes a disruption alert to travelers + manager, labeled read-only.

## MVP line

Slices 1–5 = the MVP (S0 + G1 + G4): the Gate-1 announcement is true after Slice 5. Slices 6–9 are the refinement backlog, each independently shippable and demoable.

## Discipline (standing)

- Every new test must fail against pre-change code.
- Prove each slice (run it / curl it / browser it), check it in `00-status.md`, then ask before the next.
- After Slice 3 and Slice 5 (the biggest diffs), nudge for a human code read.
- Recorded-mode stays byte-safe throughout (the gate makes zero Atlas calls; ungated desks keep demo pax).

## Permanently out of scope
Hotels/ground transport, post-ticket rebooking/refunds, seat assignment (module inactive), live visa rules, loyalty numbers, airline PNR delivery.
