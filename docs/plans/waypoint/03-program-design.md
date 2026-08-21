# Program Design: Waypoint

## Two gates, applied to money (the core mental model)
- **Advise gate — open.** The desk brain sees *every* position: marks, priors, meter state, remaining budget, contingency. Qwen reasons over all of it and narrates each book/hold call — including the ones it lost ("held too long, −$62").
- **Execute gate — walled, fail-closed.** Code executes only picks that pass: amount ≤ `authority_cap`, within remaining budget, offer freshly verified. Over-cap → escalate with two priced options + recommendation; nothing settles until the one human click. Code re-checks after the LLM picks; the AI never free-forms inside settlement (ADR 0003 applied to money — this is what keeps the x2 and avoids x0.5).

## Files
```
backend/
  app/
    main.py              # FastAPI app, CORS, mounts routes + SSE          (stays)
    api/routes.py        # desk/seed, desk state, stream, close, escalation decision  (refit)
    models.py            # Offer/Layover stay; desk result types replace RecoveryResult (refit)
    agent/loop.py        # DeskAgent — orchestration, 3 guards, execute wall  (refit; keeps run(id, emit) + step_budget + give-up)
    agent/brain.py       # DeskBrain — book/hold/escalate judgment, absorb-vs-re-quote  (NEW)
    agent/auditor.py     # risk-officer: reads the blotter, challenges one trade (NEW)
    atlas/client.py      # AtlasClient — search() stays; + verify/confirm_price/create_order/pay/order_status/seat_select (additive)
    data/loaders.py      # stays (IATA maps)
    db/schema.py         # mandate, positions, ledger, budgets — FIRST REAL DB WRITES
    db/database.py       # engine/session wiring
    fixture.py           # curated volatility priors + seeded portfolio (refit)
  tests/
    test_atlas_mapping.py       # stays
    test_atlas_sandbox_live.py  # stays
    test_atlas_write_path.py    # NEW — opt-in live write-path proof
    test_desk_brain.py          # NEW — judgment tests with seeded priors
    test_desk_pipe.py           # NEW — stub-client pipe tests
frontend/                # 3 refit screens + SSE client (S5)
```

## Fare model (NO fake ML)
- **Curated per-route-type volatility priors** in `fixture.py` — disclosed approximation, the ADR 0002 precedent for honest curation with provenance. No model training, no scores dressed as predictions.
- **Live market microstructure** — bounded re-query fan-out: one `atlas-flight search` per date (no flex-date API exists; fan-out is agent-side), always shown on screen as *"re-read the world before every write"*.
- **Search-budget meter** — 20 searches/cycle, always visible. Exhausted → decisions run on **stale marks with disclosed uncertainty** (`mark` events flagged stale).

## Types & signatures (no bodies)
```python
# models.py (additions; Offer/Layover unchanged)
class Mandate(BaseModel):  # DB `mandate` table is the source of truth
    id: str; holder: str; created_at: datetime
    budget_total: Decimal; authority_cap: Decimal; contingency_pct: float; currency: str
class Position(BaseModel):
    id: str; trip_label: str; origin: str; dest: str; depart_date: date; pax: int
    status: Literal["held", "booked"]
    cost_basis: Decimal; mark_price: Decimal; mark_at: datetime; mark_stale: bool
class DeskAction(BaseModel):
    position_id: str; kind: Literal["book", "hold", "escalate"]; rationale: str
class DeskResult(BaseModel):
    desk_id: str
    status: Literal["closed", "escalated", "budget_exhausted", "failed"]
    pnl: Decimal; losses_admitted: int; step_count: int
class VerifyResult(BaseModel):   # from `offer verify --offer-id`
    offer_id: str; booking_id: str                    # booking_id provenance = the verify response
    price_change: Literal["unchanged", "decreased", "increased"]
    previous_price: Decimal; current_price: Decimal; currency: str
    seat_supported: bool; baggage_supported: bool
class OrderRef(BaseModel):       # from `order create --booking-id`
    payment_confirmation_id: str; order_no: str       # the ONLY source for `order pay`

# agent/brain.py
class DeskBrain:
    def judge(self, positions, priors, meter_left: int, budget_left: Decimal) -> list[DeskAction]: ...
    # advise gate: sees everything; recommends; never executes.
    def resolve_price_change(self, delta: Decimal, contingency_left: Decimal) -> Literal["absorb", "requote"]: ...

# agent/loop.py
class DeskAgent:
    def __init__(self, atlas, brain, auditor, store, step_budget: int = 12): ...
    async def run(self, desk_id: str, emit: Callable[[dict], None]) -> DeskResult: ...

# atlas/client.py (additive)
class AtlasClient:
    def verify(self, offer_id: str) -> VerifyResult: ...                              # returns price_change + booking_id
    def confirm_price(self, booking_id: str) -> None: ...                             # CONDITIONAL — only on verify-reported increase
    def create_order(self, booking_id: str, pax_json: str) -> OrderRef: ...           # never retried; returns payment_confirmation_id + order_no
    def pay(self, payment_confirmation_id: str) -> "PaymentResult": ...               # single-use; ID from THAT create_order response; never retried
    def order_status(self, order_no: str) -> "OrderStatus": ...                       # poll until TICKETED
    def seat_select(self, booking_id: str, traveler_id: str, segment_id: str, seat_id: str) -> "SeatResult": ...
    # booking-stage op, bound to booking_id — runs BEFORE order create, never on order_no
```

## Call stack (one desk cycle, main path)
```
POST /api/desk/seed
  DeskAgent.run(desk_id, emit)
    Store.reload_desk(desk_id)                       # GUARD: re-read world (positions/budget/ledger fresh)
    emit meta (mandate + meter)
    for position in portfolio (meter-gated fan-out):
      offers = AtlasClient.search(position.route, date)   # one search per date; emit mark (old/new)
      Store.update_mark(position, offers)                  # meter-- ; exhausted -> stale mark + disclose
    actions = DeskBrain.judge(...)                          # ADVISE gate: LLM sees all; emit trade per pick
    for action in actions:
      # EXECUTE wall: fail-closed re-check in CODE
      if action.amount > mandate.authority_cap or over budget:
        emit escalate (two priced options + recommendation); await one human click
      if action.kind == "book":
        v = AtlasClient.verify(offer_id)                        # GUARD: stale check before write; yields price_change + booking_id
        if v.price_change == "increased":                       # CONDITIONAL branch
          AtlasClient.confirm_price(v.booking_id)               # only on verify-reported increase; unchanged/decreased skip it
        if realized_savings and v.seat_supported:               # ALLOC beat: booking-stage, PRE-ORDER, bound to booking_id
          AtlasClient.seat_select(v.booking_id, traveler_id, segment_id, seat_id)
          # on SEAT_UNAVAILABLE: skip the seat, emit alloc as ledger-only — never block the order
        ref = AtlasClient.create_order(v.booking_id, pax_json)  # --seat-policy continue-without-seat as fallback; never retried;
                                                                #   unknown -> order status only. Returns payment_confirmation_id + order_no
        AtlasClient.pay(ref.payment_confirmation_id)            # ID from THAT create_order response; single-use, NEVER retried
        AtlasClient.order_status(ref.order_no)                  # GUARD: assert TICKETED, not 200 OK
        Store.record_trade(...) ; emit reconcile ; emit alloc   # PRICE_CHANGED -> absorb | requote (never 2nd order)
    emit result (P&L, losses admitted, step_count)
  GET /api/desk/{id}/close
    Auditor.read(blotter) -> one-line challenge of one trade  # weekly close
```

## Test plan (names → what each asserts)
Deterministic / mapping (existing suite stays green — `test_atlas_mapping.py`):
- `test_seed_persists_mandate_positions_budgets` — first real DB writes land; seed is re-readable.
Stub-client pipe tests (`test_desk_pipe.py`, stub AtlasClient):
- `test_seed_emits_meta_with_mandate_and_meter` — stream starts with mandate card + 20/20 meter.
- `test_reprice_fan_out_is_meter_gated` — 21st search blocked; `mark` flagged stale with disclosure.
- `test_execute_wall_blocks_over_cap_and_emits_escalate` — pick above cap never reaches `create_order`.
- `test_pay_never_retried_on_failure` — failed pay → query-only follow-up via `order status`, no second `order pay`.
- `test_no_second_order_on_price_changed` — `PRICE_CHANGED` → absorb-or-requote; `create_order` called exactly once.
- `test_ticket_asserted_before_success` — no `TICKETED` → position never marked booked.
- `test_agent_respects_step_budget_and_gives_up` — forced loop stops at budget, emits why.
Desk-brain judgment tests (`test_desk_brain.py`, seeded priors):
- `test_brain_books_when_mark_above_prior_band` / `test_brain_holds_when_mark_below_band`.
- `test_brain_logs_admitted_loss_with_threshold_note` — "held too long, −$62, threshold adjusted".
- `test_resolve_price_change_absorbs_small_requotes_large` — boundary at contingency remainder.
- `test_alloc_funds_seat_select_only_from_realized_savings` — no savings → no alloc; empty seat list / `SEAT_UNAVAILABLE` → `seat_select` never called and alloc asserts a **ledger-only** fallback entry.
Opt-in live sandbox (skipped unless env flag, mirrors `test_atlas_sandbox_live.py`):
- `test_live_write_path_tickets` — verify → [confirm-price only if verify reports an increase] → create → pay (with the confirmation ID from the create response) → status `TICKETED` on one route.
- `test_live_seat_select_pre_order` — `booking seat select` against the verify-returned `booking_id` BEFORE `order create`; if the seat list is empty on the route, the alloc lands ledger-only instead.

## Demo engineering
- **Replay-safe SSE** — buffer + replay, idempotent-by-index step rendering, StrictMode absorption, retained task handles (all STAYS from the tracer). The cold-open mid-trade replays a seeded cycle; nothing is faked client-side.
- **Demo beats (3:00):** 0:00–0:10 cold open mid-trade (toast "BOOKED DAC→LHR now, +$220 vs hold model", P&L +$1,840) · 0:10–0:40 mandate card + search meter · 0:40–1:20 live hold/book call with on-screen re-query · 1:20–1:50 admitted loss · 1:50–2:20 autonomous allocation — realized savings fund a **pre-order** `booking seat select` (booking-stage, bound to the verify-returned `booking_id`; order create carries `--seat-policy continue-without-seat` as fallback; on `SEAT_UNAVAILABLE` the alloc degrades to a ledger-only entry, shown honestly) + reconciliation card · 2:20–2:50 escalation beat (spike > cap → two priced options + recommendation → one click → executes) · 2:50–3:00 weekly close (P&L, zero policy breaches, risk-officer verdict).
- **Rehearsal checklist:** meter reset to 20/20; loss scenario injected; spike scenario armed on the escalation position; seat-select target checked via `booking seat list` on the booking stage (pre-order) — if the list is empty or seats are unavailable, the alloc beat runs ledger-only (scripted fallback, shown honestly); SSE buffer pre-warmed for cold open; full run ≤ 3:00 timed twice.
- **Fallback order for beats:** (1) live write path (day-4 gate cleared); (2) comparison mode — decisions still logged and marked, judgment layer still demos; (3) SSE-buffer replay of the last good cycle. Every beat has a scripted fallback — no half-finished screen (4/2/0 tiers).

## Qoder usage plan (≥ 80% of core built in Qoder — eligibility rule)
- **Spec Mode** for each gate and each slice: plan-before-code; the plan is the review evidence (scores AI Development 3–4).
- **Quest Mode** per slice S1–S8: one Quest per slice, done-criteria = Quest acceptance tests. Experts mode for S5 (three screens in parallel) if warranted.
- **Repo Wiki** (`.qoder/repowiki`) regenerated at S1 and S5 as the onboarding artifact.
- Per the build workflow in `00-status.md`: implementation code is built in Qoder; this doc package is planning, not code.

## Risk register (with kill-switches)
| risk | kill-switch |
|---|---|
| Sandbox rate limits / slow fan-out | search meter (20/cycle) always enforced; portfolio scoped to 5–6 trips |
| Live book/pay fails | **DAY-4 GATE** → honest comparison mode (decisions logged + marked; judgment layer still demos) |
| Sandbox returns `reference`-only offers | comparison mode; no fabricated tickets, honest `pending` labels |
| Cerebral-dashboard demo (the failure mode that killed the pivot) | cold open mid-trade + admitted-loss beat; script the human story before the tech |
| LLM judgment flaky on demo day | deterministic fallback (prior-band rule) emits identical events; narration degrades gracefully |
| `db/schema.py` never written before | tables land in S1 behind `test_seed_persists...`; drop-and-recreate (demo data only) |
| Subprocess CLI latency mid-demo | pre-run cycle + SSE-buffer replay for the cold open |

## Least confident decisions (challenge these)
1. **`booking_id` provenance — RESOLVED (2026-08-22):** the `--booking-id` for `confirm-price` and `order create` comes from the `offer verify` response; `order pay` takes the `payment_confirmation_id` returned by that same `order create`. Kept flagged for the day-3 live proof against `cli-contract.md`.
2. **`--passengers-stdin` JSON shape** — confirm on first live order; wrong shape = `BOOKING_INPUT_INVALID`.
3. **Seat availability** on the chosen sandbox route — `booking seat list` may return nothing; confirm day-4, fall back to baggage or drop the alloc beat to ledger-only.
4. **Volatility prior calibration** — curated per route type, demo routes only; the band thresholds are guesses until D5 data lands.
5. **Meter size = 20/cycle** — placeholder; tune once one real cycle's fan-out count is known.
