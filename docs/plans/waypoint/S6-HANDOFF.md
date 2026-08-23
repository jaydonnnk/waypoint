# Waypoint — Slice 6 Handoff for Qoder (Hardening)

You are building **Slice 6: hardening** in Qoder. Backend S1–S4 + frontend S5 are
done and merged to `main` (`next build` clean). Suite re-baselined post-S6 via
pytest from `backend/`: **83 passed, 3 deselected (live), 0 failed**.

**READ THIS FIRST: S6 is a VERIFY-AND-FILL slice, not a build-from-scratch.**
Most of what `04-slices.md` lists under S6 was already built as part of S3/S4's
correctness hardening. Your job is (1) **verify** the existing guarantees hold and
are tested, and (2) **fill** the specific real gaps below. Do NOT rebuild what
already works — you'll only regress tested behavior.

## Read these first
- `docs/plans/waypoint/00-status.md` — locked decisions, Atlas state, build workflow.
- `docs/plans/waypoint/04-slices.md` → **S6** (line 63) — your done-criteria.
- `docs/plans/waypoint/02-architecture.md` — the Atlas command + error-code contract table.
- `docs/adr/0004` — the two-gate / fail-closed money discipline.

## S6 done-criteria (from 04-slices.md, verbatim)
> every non-success `code` routes per the contract table (no `message` parsing
> anywhere); give-up paths (budget exhausted, meter exhausted, step budget) emit
> disclosed reasons; tests assert `order pay`/`order create` are never retried;
> meter hard-stops the 21st search.

---

## ALREADY BUILT — verify only, do NOT rebuild (with evidence)

1. **Error-code routing, never `message` parsing.** `backend/app/atlas/client.py`
   header contract (lines ~17-22) + `_offers_from_envelope` and every method branch
   on envelope `code` only. Verify: grep the whole `app/` for any `.get("message")`
   or `["message"]` used in a branch/decision — there should be **zero**. `message`
   may only be logged server-side, never routed on.
2. **No-retry discipline.** The retry policy lives in **exactly one place** —
   `_run_read_only` (client.py ~235): a read-only call gets at most ONE identical
   retry, only when `retryable=true`. **Writes never go through it** — `order create`
   / `order pay` / `seat select` are never retried, even if `retryable=true` appears.
   Already tested: `test_pay_never_retried_on_failure`, `test_no_second_order_on_price_changed`.
3. **Meter hard-stop.** `METER_MAX = 20` (loop.py:35); `_reprice_fan_out` checks the
   meter **BEFORE dispatch** (loop.py L455-459, "GUARD: meter check BEFORE
   dispatch, adjacent to the decrement"),
   so no 21st `atlas-flight search` subprocess is ever spawned. Verify it's a true
   hard stop (no CLI call), not just a post-hoc discard.
4. **Step-budget give-up.** `_give_up` (loop.py ~771) emits a disclosed step + closes.

If any of the four regressed, restore it — but the expectation is they all hold.

---

## THE REAL GAPS — this is your actual S6 work

### GAP 1 (load-bearing) — `budget_exhausted` is declared but NEVER emitted
`DeskStatus = Literal["closed", "escalated", "budget_exhausted", "failed"]`
(`models.py:19`). But in `loop.py` the cycle status is set `status = "closed"` at
line 262 and **never reassigned to `"budget_exhausted"`** anywhere. So a desk that
runs out of budget still closes as `"closed"` — dishonest — and the **S5 frontend
already has a live branch for `budget_exhausted` that can never fire** (dead code
on both sides).
- **Fix:** set `status = "budget_exhausted"` and emit a **disclosed give-up
  reason** on the wire before `_finish`. The frontend already renders it.
- **DECIDED (b):** "budget exhausted" = `budget_left` falls below the cheapest
  still-held position's `mark_price` — i.e. even the cheapest thing left on the
  desk costs more than what's left, using the last-known mark (no extra Atlas call
  needed to raise this label). Check this **after** settle, over the positions
  still `status="held"`. Note this is a distinct, coarser check from the existing
  live per-booking guard at [loop.py:569](../../../backend/app/agent/loop.py) —
  that one blocks one specific booking using the real Atlas-verified price right
  before the write; this one labels the *whole desk* at cycle end using stale
  marks. Both stay — they're not redundant, they answer different questions
  ("can this one booking go through right now" vs "is there any point continuing
  next cycle").
- **RESOLVED (S6, implemented):** exactly as decided. `budget_exhausted` is now
  emitted via a **post-settle check** — `budget_left <` the cheapest still-held
  position's last-known mark (strict `<`), over positions still `status="held"`
  — with a disclosed give-up step on the wire. Zero extra Atlas calls; the
  per-booking guard stays untouched alongside it.

### GAP 2 — give-up status consistency (real P&L is thrown away)
`_give_up` (step-budget path) returns `status="failed"` with `pnl=Decimal("0")`
and `losses_admitted=0` — it **discards the real P&L and admitted losses already
computed this cycle**. The escalated path (loop.py ~302-306) correctly **preserves**
`self._pnl(positions)` and `losses_admitted`. The give-up paths should be
consistent: a bounded give-up still closes the books on what really happened.
- **Fix:** give-up paths carry the real `pnl` / `losses_admitted` / `comparison_mode`
  computed so far, not zeros — unless there's a reason the step-budget case truly
  has none (state it if so).
- **RESOLVED (S6, implemented):** every give-up path now carries the real
  `pnl` / `losses_admitted` / `comparison_mode` computed so far — no zeros, no
  silent-close exception. A bounded give-up closes the books on what really
  happened.

### GAP 2b (load-bearing) — give-up paths discard the settle ledger → books don't tie out
Every give-up path **returns before the settle flush** (`store.settle` at loop.py
~341), so the accumulated `settle` list — loss rows (loop.py ~225), and any
completed booking's `trade` row + persisted spend — is **silently discarded**.
Combined with GAP 2 (give-up now reports real `losses_admitted`), the result and
the DB ledger would **disagree**: result says "1 loss admitted", DB has no loss
row. Worse, a give-up right after a successful live booking loses that trade's
ledger row and its persisted spend.
- **This is already live on the escalated give-up path** ([loop.py:302](../../../backend/app/agent/loop.py))
  — it reports real `losses_admitted` and never flushes settle. GAP 2 doesn't
  create the inconsistency; it propagates an existing one. So the fix covers **all**
  give-up returns: the four step-budget gates, the escalated bounded-wait
  give-up, and the post-write gate — not just one.
- **DECIDED: flush settle on every give-up.** Before any give-up `_finish`, run the
  accumulated `settle` list through the same one-transaction `store.settle` (with
  the real `spend_total` / `contingency_used_total`), exactly as the happy path
  does. Then the books tie out: `result.losses_admitted > 0` matches persisted
  ledger rows, and a post-booking mid-loop give-up keeps the trade's spend.
- Implementation detail: `budget_start` / `contingency_start` capture was
  **hoisted above the early give-up gates** — behavior-neutral, just moved the
  capture earlier. **No Atlas calls added.** A **counted disclosed step is also
  emitted when a position books** (loop.py L378-387) — a booking is real
  progress the step budget must see, and that's also what makes the post-write
  give-up gate reachable at all.
- Rationale: this product's whole thesis is a reconciliation desk
  ([01-product.md:45](../01-product.md) — "every sandbox payment reconciled against
  the ledger"). A result that claims losses/spend the DB doesn't hold is
  disqualifying in the exact dimension it's scored on. Semantically it's also
  *more* honest: a loss admitted or a booking made really happened — a give-up
  means "stop judging further positions", not "unwind what already occurred".
- **Test:** a give-up after a loss row (and after a booking, in live mode) leaves
  the DB ledger holding those rows, and `result.losses_admitted` / persisted spend
  match the ledger — no result-vs-DB disagreement on any give-up path.
- **RESOLVED (S6, implemented):** every give-up return — the four step-budget
  gates, the escalated bounded-wait give-up, and the post-write gate — now
  flushes its accumulated `settle` list through the same one-transaction
  `store.settle` before `_finish`, so `result.losses_admitted` / spend always
  tie out with the DB ledger. Persist what happened, then stop.

### GAP 3 — meter-exhaustion: give-up vs. disclosed degrade (DECISION)
S6 lists "meter exhausted" as a give-up path. Today it does **not** give up — it
**degrades**: skip-and-continue, holds the stale mark, emits a disclosed reason on
the `mark` event (`"search meter exhausted at 20 — uncertainty disclosed"`, loop.py
~418). That degrade is arguably *better* than aborting the cycle (the judgment layer
still runs on stale marks, honestly labeled).
- **DECIDED: keep as-is.** The disclosed-degrade stays the behavior — do NOT add a
  "meter exhausted → give up the cycle" path. Meter exhaustion is NOT a
  `DeskStatus` value and never will be; it only ever shows up as a `mark.stale`
  disclosure on individual positions. S6's job here is just: add the test proving
  the disclosed reason fires and no 21st search is issued (GAP 4 below) — no
  behavior change.

### GAP 4 — the tests S6 explicitly requires
Add/verify pipe tests (against the stub client) asserting:
- **Meter hard-stop:** the test was **never missing** —
  `test_reprice_fan_out_is_meter_gated` already asserted **exactly 20** `search`
  calls (the 21st never invoked, assert on the stub's call count). S6 extended
  it with the exact `"search meter exhausted at 20"` reason-text assertion on
  the disclosed stale-mark event.
- **Budget-exhausted give-up:** a desk driven past its budget emits
  `status="budget_exhausted"` + a disclosed reason (once GAP 1 is fixed).
- **Give-up reasons are disclosed:** each give-up path (step / budget / meter-per-decision)
  emits a human-readable disclosed reason on the wire, not a silent close.
- **No-retry (already covered):** keep the existing pay/create no-retry tests green.
- **RESOLVED (S6, implemented):** all four required assertions are in the suite
  and green in the re-baseline (83 passed, 3 deselected live, 0 failed). The
  meter test pre-existed and was extended, not written from scratch.

---

## Frozen constraints — do not break
- **Branch on `code`, never `message`.** No exceptions, anywhere.
- **Writes never retried.** The single retry place stays `_run_read_only`; writes
  never route through it. Don't add a second retry site.
- **The S5 frontend already branches on all 4 `DeskStatus` values** (`closed` /
  `failed` / `escalated` / `budget_exhausted`) and renders disclosed reasons. Your
  backend changes must make all 4 genuinely reachable — do NOT rename or add a
  status value without saying so (it'd silently dead-branch the frontend).
- **Fail-closed stays fail-closed.** Budget is never waived; an escalated "A" click
  waives only the authority cap. Don't loosen a guard in the name of "hardening".
- **No LLM in any deterministic step.** Give-up/meter/budget/error routing are all
  plain code. Qwen only ranks/narrates already-classified options.

## Working style (how Jaydon runs this)
- Honesty over agreeableness. Never invent a number, field, or code. Flag
  uncertainty explicitly. No opening praise.
- GAP 1 and GAP 3 are already decided above (budget-exhausted = option b;
  meter-exhausted stays a disclosed degrade, not a give-up) — build to those, no
  further check-in needed on those two.
- Plain language; explain your reasoning — Jaydon is learning while building.
- Propose a plan and get approval before large changes (Qoder Spec Mode fits).
- **Jaydon controls all git commits/sync himself. Do not auto-commit. No AI
  co-author trailer on commits.**
- A separate reviewer (Claude Code) cross-checks your output against this spec and
  the frozen contract.

## Done =
All 4 `DeskStatus` values genuinely reachable and honest; every give-up path emits
a disclosed reason carrying real P&L/losses; meter proven to hard-stop at 20 (no
21st CLI call) by test; no-retry + code-only-routing still green. Suite
re-baselined at 83 passed / 3 deselected (live) / 0 failed; new hardening tests
included. A forced failure (budget/meter/step) stops
gracefully on screen with a disclosed reason — the S6 demo checkpoint.
