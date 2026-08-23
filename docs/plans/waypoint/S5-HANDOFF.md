# Waypoint — Slice 5 Handoff for Qoder (Frontend Refit)

You are building **Slice 5: the frontend refit** in Qoder. Backend Slices 1–4 are
done, merged to `main`, 79 tests green. Your job is the three screens + SSE client
that render the desk's live cycle. **No backend changes** — the backend event
contract below is frozen; you render it exactly as-is.

## Read these first (your spec, in order)
- `docs/plans/waypoint/00-status.md` — locked decisions, build workflow, Atlas state.
- `docs/plans/waypoint/01-product.md` — what Waypoint is (a corporate travel **treasury desk**).
- `docs/plans/waypoint/02-architecture.md` — stack, SSE + Atlas command contract.
- `docs/plans/waypoint/04-slices.md` → **S5** — your done-criteria.
- `docs/plans/waypoint/03-program-design.md` → **Demo beats (3:00)** at line ~135 — your visual/content target.
- `docs/adr/0001..0004` — the non-negotiable decisions.

## The one-paragraph frame
Waypoint is a **corporate travel treasury agent**: a mandate (budget + authority
cap) seeds a desk of travel "positions"; the agent marks them to market, judges
hold-vs-book, executes bookings behind a code wall, admits losses honestly,
reconciles price changes, allocates realized savings, and escalates spikes over
the authority cap to one human click. The demo runs mandate → live desk → weekly
close, in one SSE stream, in **comparison mode** (sandbox ticketing is not
activated, so decisions are logged + marked but nothing is ordered — this is
labeled on screen, not hidden).

---

## CRITICAL: the current frontend is the WRONG concept — delete it wholesale

`frontend/` today is 100% the **archived visa-recovery** concept (trip disruption,
passports, rebooking). Every type, endpoint, and screen is wrong:
- `lib/types.ts` — `Offer`/`Segment`/`RuleVerdict`/`RecoveryResult`, SSE events
  `options`/`decision`/`result`. **All wrong.** Replace the whole file.
- `lib/api.ts` — hits `POST /api/disruptions`, `GET /api/trips/{id}/stream`,
  `GET /api/trips/{id}/recovery`. **All wrong endpoints.** Replace the whole file.
- `app/page.tsx`, `app/recovering/[tripId]/page.tsx`, `app/recovered/[tripId]/page.tsx`
  — visa screens. Refit to the desk (see routes below).
- **`lib/format.ts`** — imports `Offer` and has `viaAirport(offer)`; replacing
  `types.ts` without rewriting this **fails `next build`**. In scope. (Port the
  reusable bits — `formatHours`, generic helpers — drop the visa-specific ones.)
- **`app/layout.tsx`** (metadata/title copy) and **`app/globals.css`**
  (visa-named classes) also carry the dead concept — refit both. Port the reusable
  design tokens + terminal-style stream CSS + the existing EventSource+index pattern.
- The 3 files in `docs/plans/waypoint/mockups/*.html` are **stale visa mockups** —
  do NOT copy them. Build visuals from the Demo-beats spec instead.

Do not preserve any visa/passport/disruption concept. If a symbol mentions
`visa`, `passport`, `disruption`, `recovery`, `RuleVerdict` — it goes.

---

## The frozen backend contract (render exactly this — do not invent fields)

### Endpoints (`backend/app/api/routes.py`, prefix `/api`)
- `POST /api/desk/seed` → seeds mandate + portfolio, starts the cycle, returns `{ "desk_id": string }`.
- `GET  /api/desk/{desk_id}/stream` → **SSE** of the live cycle. Server buffers +
  replays every event, so a late client still sees the full stream from event 0.
- `GET  /api/desk/{desk_id}` → snapshot `{ positions, ledger, budgets, meter:{used,max}, done }`.
- `GET  /api/desk/{desk_id}/close` → the final `DeskResult` (bounded 60s wait; 504 if not done, 500 if failed).
- `POST /api/desk/{desk_id}/escalations/{esc_id}/decision` → body `{ "choice": "A" | "B" }`.
  The one human click. Returns `{ desk_id, esc_id, choice }`. A gone/consumed slot → **410**.

`API_URL` stays `process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"`.
SSE frames are `data: <json>\n\n` (plain `text/event-stream`; use `EventSource`
or a fetch-stream reader).

### SSE event catalog (verbatim field names from `loop.py`)
Every event is one JSON object with a `type`. The full set:

| `type` | fields | role |
|---|---|---|
| `meta` | `desk_id`, `mandate` (Mandate), `meter:{used,max}`, `mode` (string label), `disclosures` (string[]) | **header** — mandate card + meter + mode label + disclosure register. Emitted first. |
| `step` | `n` (int), `text` (string) | narration line — the agent's live steps |
| `loss` | `position_id`, `amount` (string), `note`, `disclosure` | admitted-loss blotter row |
| `trade` | `position_id`, `kind` (string), `rationale` | judgment pick blotter row |
| `mark` | `position_id`, `old`, `new` (strings), `search_ref` (string\|null), `stale?` (bool), `disclosure?`, `meter_used` (int) | mark-to-market row; `stale:true` = held stale mark, disclosed |
| `escalate` | `esc_id`, `position_id`, `reason`, `options` (exactly 2, shape below), `recommendation` (currently hardcoded `"B"`), `disclosures` (string[]) | escalation row → see the escalation-beat decision below (gated + annotated in comparison mode) |
| `reconcile` | `position_id`, `delta` (string), `resolution` (`"absorb"` \| `"requote"`), `disclosure` | price-change reconciliation row |
| `alloc` | `position_id`, `amount` (string), `seat_ref` (string, e.g. `"ledger_only"`), `disclosure` | savings-allocation row |
| `error` | `code` (string), `position_id?` | disclosed failure — render the **code**, never a raw message |
| `result` | `result` (DeskResult) | terminal event — cycle done |

**Blotter rows = the 6 types** `loss` · `trade` · `mark` · `escalate` · `reconcile`
· `alloc`. `meta` drives the header, `step` the narration feed, `error` a disclosed
failure line, `result` the transition to close.

There is **no `book` event** — a successful booking shows as a `trade` row plus
the terminal `result`. Do not invent one.

### Payload shapes (mirror `backend/app/models.py`; Decimals arrive as strings)
```ts
interface Mandate {
  id: string; holder: string; created_at: string;
  budget_total: string; authority_cap: string;
  contingency_pct: number; currency: string;
}
interface Position {
  id: string; trip_label: string; origin: string; dest: string;
  depart_date: string; pax: number; status: "held" | "booked" | string;
  cost_basis: string; mark_price: string; mark_at: string;
  mark_stale: boolean; atlas_offer_id: string | null;
  atlas_order_no: string | null; ticket_asserted: boolean;
}
interface Budget {
  id: number; desk_id: string; period: string;
  allocated: string; spent: string; contingency: string;
}
interface DeskResult {
  desk_id: string; status: string; pnl: string;
  losses_admitted: number; step_count: number;
  comparison_mode: boolean;
}
```
The `escalate.options` wire shape (verified) — build the union against this:
```ts
interface EscalationOption { key: "A" | "B"; label: string; price: string; }
// escalate.options is always exactly [A, B]; A = "book now (manual approval)",
// B = "hold — re-check next cycle". recommendation is currently always "B".
```

Rewrite `lib/types.ts` to exactly these + a `StreamEvent` union matching the
event catalog above. Delete every old visa type.

---

## The three screens (files, per 04-slices S5)

**Screen 1 — Mandate → seed the desk.** `frontend/app/page.tsx`
- A mandate card / "open the desk" action. `POST /api/desk/seed` → get `desk_id` →
  route to the desk screen. (Seeds are disclosed/canned — the mandate is
  server-seeded; the form can be a confirm-and-go, not a full data-entry form.)

**Screen 2 — The live desk / blotter.** (desk route, e.g. `app/desk/[deskId]/page.tsx` —
you may rename the two old dynamic routes rather than keep `recovering`/`recovered`.)
- **Cold-open beat (0:00–0:10) — DECISION: live from real events.** The scripted
  wow toast ("BOOKED …, +$220 vs hold, P&L +$1,840", per `03` ~line 135) renders
  ONLY from real `trade`/`mark` events as the stream replays, with the
  comparison-mode label present. Key point: **P&L and marks are genuinely
  mark-to-market and real even in comparison mode** — only the literal word
  "BOOKED" isn't (nothing tickets). So the book beat reads as *"book decision
  logged"*, and the P&L counter animates off real mark numbers. No static faked
  card, no "BOOKED ticket" text the backend didn't emit. The wow happens live
  in-stream.
- Open the SSE stream `GET /api/desk/{desk_id}/stream`.
- Header from `meta`: mandate card (holder, budget_total, authority_cap, currency),
  the **search meter `used/max` always visible**, the **mode label** (e.g.
  "comparison mode — live booking not armed"), and the **disclosure register**
  (render every string in `disclosures`).
- Blotter: render `loss`/`trade`/`mark`/`escalate`/`reconcile`/`alloc` rows.
  **Idempotent-by-index** — buffer + replay means the same event can arrive on a
  reconnect/replay; key rows by their arrival index so a replay never double-appends.
- `step` events → a live narration feed.
- `escalate` → **DECISION: gated + annotated** (comparison mode is the demo's
  actual mode). The backend auto-resolves to `"B"` instantly and **never registers
  a decision slot** in comparison mode ([loop.py:488](../../../backend/app/agent/loop.py)),
  so any decision POST is **guaranteed to 410** — 410 is the normal path, not a
  race. So: render the 2 priced `options` + the `recommendation` for the beat, but
  present them as *"auto-resolved to B (hold) — comparison mode; in live mode this
  is your one human click."* **Do NOT wire fake-interactive buttons that always
  410.** Keep the human-in-loop concept visible (two priced options + recommendation
  shown) without theater. (If `WAYPOINT_LIVE_BOOKING` is ever armed AND ticketing
  live, the slot IS registered and real buttons + the decision POST apply — build
  the POST path but gate it behind the live mode, off by default.)
- `error` → a disclosed failure line showing the `code` (never a raw message).
- On `result` → enable/route to the close screen.

**Screen 3 — Weekly close.** (close route, e.g. `app/close/[deskId]/page.tsx`)
- `GET /api/desk/{desk_id}/close` → `DeskResult`.
- **Branch on `result.status`, NOT on HTTP codes.** HTTP 500 means the cycle
  *crashed*; a logically failed/escalated desk returns **200** with
  `DeskResult.status` in `"closed" | "failed" | "escalated" | "budget_exhausted"`
  (wire value is `"closed"`, NOT `"done"` — per `models.py` `DeskStatus`).
  504 means "still running — retry". Render each status honestly.
- Render **P&L** (`pnl`), **admitted losses** (`losses_admitted`), status/**verdict**
  (`status`), step count, and the **comparison-mode** flag honestly.
- (S7 later enriches close with a risk-officer line — don't build that now, just
  don't design the layout so it can't hold one line more.)

---

## Hard requirements (these are scored / demo-critical)
1. **Replay-safe — wipe-and-rebuild, never append.** Server replay is
   **unconditional and full**, and there is **no global sequence field** on events
   (only `step.n`, which is step-local). So on every (re)connect the client must
   **rebuild blotter state from event 0 keyed by arrival index** — appending to
   existing state after a reconnect double-renders everything. Absorb React
   **StrictMode** double-mount the same way: close the stream in the effect cleanup
   and rebuild from replay. One clean stream on screen.
2. **Meter always visible — SET, never increment.** The `used/max` search meter
   shows at all times on Screen 2 (bounded-spend proof — Cost Controllability). The
   meter value rides only on `meta.meter` and `mark.meter_used`, and the reprice
   fan-out is **concurrent** — successive marks can carry duplicate or out-of-order
   values. Always **set** the meter to the received value; never `+= 1`.
3. **Disclosures rendered, never hidden.** The `meta.disclosures[]` register and
   the comparison-mode label are visible in-frame. Honesty over polish — the whole
   point is that nothing is faked. `mark.stale`, `error.code`, alloc `ledger_only`,
   "sandbox money only" all show plainly.
4. **`next build` clean.** No type errors, no unused visa cruft. Verify with plain
   `next build` (rtk isn't wired in this repo). CORS allows only `localhost:3000`,
   so the dev frontend must run on 3000.
5. **Screen-to-screen demo runs.** Cold open → mandate → live desk → weekly close,
   end to end, no dead screen (4/2/0 demo tiers — a half-finished screen scores 0).

## Traps — do not repeat
- **Do not touch the backend contract.** Endpoints, event names, field names are
  frozen. If something seems missing, render what exists honestly; flag it to
  Jaydon — do not invent a field or an endpoint.
- **Do not copy the visa mockups.** `mockups/*.html` are the dead concept. Build
  from the Demo-beats spec (`03-program-design.md` ~line 135).
- **Do not fake booking success.** Comparison mode is the honest default; the mode
  label and disclosures say so. Never render a "BOOKED / TICKETED" state the
  backend didn't emit.
- **Do not add an LLM anywhere in the frontend.** The screens only render backend
  events. (Qwen lives server-side in the desk brain; the rubric penalizes "AI for
  AI's sake".)

## Working style (how Jaydon runs this)
- Honesty over agreeableness. Never invent a number, field, or endpoint. Flag
  uncertainty explicitly. No opening praise.
- Plain language, explain your reasoning — Jaydon is learning while building.
- Propose a plan and get approval before large changes (Qoder Spec Mode fits).
- **Jaydon controls all git commits/sync himself. Do not auto-commit. No AI
  co-author trailer on commits.**
- A separate reviewer (Claude Code) cross-checks your output against this spec and
  the frozen contract; Jaydon reconciles.

## Done =
Open the browser, seed a desk, watch the meter + blotter + disclosures stream live
in comparison mode, land on the weekly close showing P&L + admitted losses +
verdict. `next build` clean. No visa concept survives anywhere in `frontend/`.
