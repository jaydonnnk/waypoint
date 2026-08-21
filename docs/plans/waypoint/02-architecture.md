# Architecture: Waypoint

## Fit — refit in place, no parallel surface
Waypoint refits the existing repo; nothing is rebuilt beside it. The existing 3-screen IA (setup → live agent → outcome) maps 1:1 to **mandate → desk → close**.

- **Frontend** — Next.js/React. Three refit screens (mandate form / blotter + meter / weekly close) consuming one SSE stream. Scoped to the demo surface only.
- **Backend** — Python 3.11 FastAPI. Hosts the desk loop, desk brain (judgment), Atlas write path, and the **first real DB writes** (`db/schema.py` exists but was never written).
- **Atlas** — `atlas-flight` CLI via **subprocess** (the skill's library entrypoint needs Python ≥ 3.12; backend stays 3.11). Env = sandbox; auth in **OS keyring** — the backend never reads secrets. Sandbox money only.
- **Judgment model** — Qwen via DashScope (`DASHSCOPE_API_KEY`, value never in repo).

Deterministic code owns: ledger arithmetic, authority-cap checks, reconciliation math, order/pay execution. **Qwen owns only the judgment** (book-vs-hold, absorb-vs-re-quote, escalation recommendation). Per ADR 0003, code re-checks caps **after** the LLM picks; the AI never free-forms inside settlement (avoids the x0.5 penalty).

## Module map (reuse / refit / new)
- **STAYS AS-IS:** `backend/app/atlas/client.py` (`search()` + mapping + exceptions; write-path methods are additive), `backend/app/data/loaders.py` + `backend/data/iata_*.csv`, the full SSE plumbing (streaming headers, buffer+replay, EventSource consumption, idempotent-by-index step rendering, StrictMode absorption, auto-navigation, retained task handles + `asyncio.Condition`), `backend/tests/test_atlas_mapping.py`, `backend/tests/test_atlas_sandbox_live.py`.
- **REFITS IN PLACE:** `frontend/app/page.tsx` (disrupted hero → mandate form), `frontend/app/recovering/[tripId]/page.tsx` (options table → blotter + search meter), `frontend/app/recovered/[tripId]/page.tsx` (receipt → weekly close), `backend/app/api/routes.py`, `backend/app/agent/loop.py` (RecoveryAgent → **DeskAgent**; keeps `run(id, emit)` + step budget + give-up paths), `backend/app/models.py` (Offer/Layover stay), `backend/app/fixture.py` (canned verdicts → curated priors + seeded portfolio).
- **NEW:** `AtlasClient` write-path methods (`verify` / `confirm_price` / `create_order` / `pay` / `order_status` / `seat_select`); DB tables `mandate`, `positions`, `ledger`, `budgets`; risk-officer auditor line; desk-brain tests.

## Endpoints (backend REST + stream)
- `POST /api/desk/seed` — create mandate + seeded portfolio of 5–6 positions (refits `POST /api/disruptions`).
- `GET  /api/desk/{desk_id}` — desk state: positions, ledger, search meter.
- `GET  /api/desk/{desk_id}/stream` — **SSE** stream of the desk cycle (reuses the existing stream route pattern + buffer/replay).
- `GET  /api/desk/{desk_id}/close` — weekly close: P&L, admitted losses, risk-officer line.
- `POST /api/desk/{desk_id}/escalations/{esc_id}/decision` — the one human click (approve option A/B).

## Data (SQLite) — first real DB writes
- `mandate` (id, budget_total, authority_cap, contingency_pct, currency, holder, created_at)
- `positions` (id, desk_id, trip_label, origin, dest, depart_date, pax, status[held|booked], cost_basis, mark_price, mark_at, atlas_offer_id, atlas_order_no, ticket_asserted)
- `ledger` (id, desk_id, ts, kind[trade|alloc|reconcile|loss|adjust], amount, position_id, ref, note)
- `budgets` (id, desk_id, period, allocated, spent, contingency, created_at)

`ledger` + `positions` are the persisted evidence of every decision (blotter = audit trail → Compliance & Safety).

## Flow (one desk cycle, end to end)
1. **Seed** — mandate + 5–6 positions with seeded cost bases (disclosed).
2. **Re-read** — reload positions, budget, ledger fresh (**GUARD: never act on cached world**).
3. **Reprice** — bounded fan-out: one `atlas-flight search` per position × candidate date, meter-gated at 20 searches/cycle (**GUARD, shown on screen: "re-read the world before every write"**). Meter exhausted → decide on stale marks, uncertainty disclosed.
4. **Judge** — DeskBrain scores each position `book` / `hold` / `escalate` with rationale (advise gate: LLM sees all positions, marks, priors, meter, remaining budget).
5. **Execute wall** — code re-checks the pick: amount ≤ `authority_cap`, within remaining budget, offer freshly verified. Over cap → `escalate` with two priced options + recommendation; nothing executes until the human click.
6. **Write path** — `offer verify` (returns `price_change` + `booking_id`) → `booking confirm-price --booking-id` **only if verify reports an increase** (unchanged/decreased → straight to order create) → `order create --booking-id --passengers-stdin` (returns `data.payment_confirmation_id` + `order_no`) → `order pay --confirmation-id` using the confirmation ID from **that** order-create response (single-use, **never retried**, never sourced from confirm-price) → poll `order status --order-no` until **TICKETED** (**GUARD: assert the real outcome, not 200 OK**).
7. **Settle** — ledger entries: trade; reconcile (on `PRICE_CHANGED`: absorb-from-contingency vs re-quote — judgment call, never a second order); alloc (realized savings fund a **pre-order** `booking seat select` bound to `booking_id`, placed before `order create` with `--seat-policy continue-without-seat` as fallback; on `SEAT_UNAVAILABLE` the alloc degrades to a **ledger-only** entry).
8. **Close** — weekly P&L incl. admitted losses; risk-officer reads the blotter and challenges one trade.

## SSE event contract (`GET /api/desk/{desk_id}/stream`)
| event | payload | meaning |
|---|---|---|
| `meta` | mandate + search meter (n/20) | cycle start; the mandate card |
| `step` | index + narration | ordered, idempotent-by-index reasoning step |
| `mark` | position_id, old/new price, search ref | live reprice result (fan-out visible) |
| `trade` | position_id, `book`/`hold`, rationale | the discretionary timing call |
| `loss` | position_id, amount, note | admitted loss ("held too long, −$62, threshold adjusted") |
| `alloc` | position_id, amount, seat ref or `ledger_only` | savings → pre-order `booking seat select` (booking-stage, `booking_id`-bound); ledger-only entry on `SEAT_UNAVAILABLE` |
| `reconcile` | payment vs ledger, resolution | auto-reconciliation incl. `PRICE_CHANGED` handling |
| `escalate` | esc_id, two priced options, recommendation | mandate edge; waits for the human click |
| `result` | cycle P&L, losses, step_count | terminal state of the cycle |
| `error` | normalized `code` only | never raw message / HTTP status |

## Atlas command usage & retry rules
| command | use | retry rule |
|---|---|---|
| `search --origin --destination --depart --adults --json` | reprice fan-out (one search per date) | read-only; `retryable=true` → at most one identical retry |
| `offer verify --offer-id` | freshness re-read before every write; returns `price_change` + `booking_id` | read-only; at most one identical retry |
| `booking confirm-price --booking-id` | **conditional — only when verify reports a price increase**; unchanged/decreased skip straight to order create | read-only; at most one identical retry |
| `order create --booking-id --passengers-stdin` | order creation; response returns `data.payment_confirmation_id` + `order_no` | **write; NEVER retried.** On `ORDER_CREATION_UNKNOWN` / `DUPLICATE_BOOKING_SUSPECTED` → query `order status` only |
| `order pay --confirmation-id` | settlement; the confirmation ID is the one returned by **that** order-create response (single-use) | **write; single-use; NEVER retried under any circumstance** |
| `order status --order-no` | outcome assertion (poll until `TICKETED`) | read-only; `TICKETING_PENDING` is continuing, not failure |
| `booking seat list` / `booking seat select` | savings allocation — booking-stage, bound to `booking_id`, runs BEFORE `order create` (with `--seat-policy continue-without-seat` on create as fallback) | list read-only; select = write, never retried |

Branch on `code`, **never** `message`; internal codes and numeric HTTP statuses never reach the UI.

## Error-handling contract (desk behavior per code)
- `AUTHORIZATION_REQUIRED` → present `data.authorization_url`, stop the turn; resume only when authorized.
- `SEARCH_NO_RESULTS` → hold on stale mark, disclose uncertainty.
- `OFFER_EXPIRED` → re-search within meter; meter exhausted → hold, disclose.
- `SEAT_UNAVAILABLE` → skip seat selection and continue to order create (`--seat-policy continue-without-seat`); the alloc degrades to a ledger-only entry. Seats never block the write path.
- `PRICE_CHANGED` → reconcile judgment (absorb from contingency vs re-quote). **Never create another order** — re-verify/re-quote first.
- `PAYMENT_*` / `ORDER_CREATION_UNAVAILABLE` → stop the affected position; follow up query-only via `order status` using `order_no`.
- `SERVICE_TEMPORARILY_UNAVAILABLE` (`retryable=true`) → at most one identical retry of the read-only call; writes never retried.
- Step budget exceeded → give up gracefully, emit `result` + why (the loop keeps RecoveryAgent's give-up paths).

## Honesty / disclosure register
| element | status | how disclosed |
|---|---|---|
| Money | sandbox only | banner on desk + close screens |
| Cost bases & history | seeded | noted on mandate card |
| Volatility priors | curated per route type, **no ML** | "disclosed approximation" label (ADR 0002 precedent) |
| Fare spikes / losses in demo | injected | labeled in event note |
| Risk-officer line | generated from the blotter | labeled "auditor read" |
| Stale-mark decisions | meter exhausted | `mark` events flagged stale + uncertainty |
| Settlement/refund legs | own ledger | "Atlas has no refund/change rail" stated at close |
| Comparison mode (day-4 fallback) | decisions logged, no orders | labeled comparison mode on screen |

## External
- **Atlas sandbox** via `atlas-flight` CLI subprocess — auth in OS keyring, env = sandbox. Commands per table above; no webhooks used.
- **Qwen** via Alibaba DashScope — `DASHSCOPE_API_KEY` (value never in repo).
- No other third-party services; all other data is bundled (priors in `fixture.py`, IATA maps in `backend/data/`).
