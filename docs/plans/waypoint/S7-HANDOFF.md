# Waypoint — Slice 7 Handoff for Qoder (Risk officer + demo choreography)

You are building **Slice 7** in Qoder. S1–S6 are done and merged/committed
(S6 on branch `qoder/slice-6-hardening`, pushed). Backend suite: 83 passed.

S7 is the **weekly-close multi-agent beat + demo choreography** — the last code
slice. S8 is rehearsal/recording (Jaydon's, no code). This is the 2:50–3:00 beat:
the risk officer reads the desk's own blotter and challenges one trade, the close
reports zero policy breaches, and the cold open is pre-warmed to replay instantly.

## Read these first
- `docs/plans/waypoint/00-status.md` — locked decisions, Atlas state, build workflow.
- `docs/plans/waypoint/04-slices.md` → **S7** (line 70) — done-criteria.
- `docs/plans/waypoint/03-program-design.md` — auditor DI (`DeskAgent.__init__(... auditor ...)`
  line ~68), `Auditor.read(blotter) -> one-line challenge` (line ~110), demo beats (~135).
- `docs/adr/0003`, `docs/adr/0004` — the LLM-never-computes-policy/money discipline.
- `docs/plans/waypoint/01-product.md:45` — success metric: **"zero authority-cap breaches"**.

## S7 done-criteria (04-slices.md, verbatim)
> `GET /api/desk/{desk_id}/close` returns P&L, zero-policy-breach count, and the
> risk-officer's one-line challenge of one trade; loss + spike injections are
> one-flag; cold-open replay pre-warmed.
> Demo checkpoint: beat 2:50–3:00 lands; the auditor line reads as a second agent,
> labeled honestly.

---

## Current state (grounded — build on this, don't guess)
- **`auditor.py` does not exist yet** — new file.
- **`DeskAgent` has no `auditor` param today** — its `__init__` is
  `(step_budget, atlas, brain, store, escalation_slot, escalation_clear,
  meter_report, escalation_wait, pace)`, all defaulted. The 03 spec shows an
  `auditor` on this DI list — **but see DECISION 4 below: this may be the wrong
  layer.** Don't wire it here until that's settled.
- **Injection is ALWAYS-ON and hardcoded** in `fixture.py::seeded_portfolio()`:
  position 2 (DAC→LHR) is the spike (mark 1790 > cap 1500); positions 3 (JFK→LIS,
  588<610) and 6 (GRU→MIA, 655<690) carry mark-below-cost unrealized losses. There
  is **no flag** — see DECISION 1.
- **`/close` returns a bare `DeskResult`** (`status`/`pnl`/`losses_admitted`/
  `step_count`/`comparison_mode`). It does NOT carry a breach count or an auditor
  line yet — see DECISION 3.
- **The S5 frontend close screen already consumes `DeskResult`** directly
  (`frontend/app/close/[deskId]/page.tsx`, branches on `result.status`; `lib/api.ts`
  `getDeskClose()` returns the 5-variant `CloseOutcome` union — `result` /
  `still_running` / `crashed` / `not_found` / `unreachable`, only `result` carries
  a `DeskResult`). **The screen already reserves your slot** —
  `page.tsx:238-239`: `{/* Room for one more line later (S7 risk-officer verdict). */}`.
  **S7 must extend the frontend close screen** to render the breach count + auditor
  line into that reserved spot — real S7 work even though 04-slices lists only
  backend files. Flag it in your plan.

---

## The work

### 1. The risk officer (`agent/auditor.py`, new)
A second agent that **reads the desk's own blotter** (the settled ledger +
positions for this cycle) and emits **one honest line challenging one trade** —
e.g. *"held JFK→LIS through a −$22 mark while booking a +$17 leg — was the timing
deliberate?"*. This is the multi-agent flavor for the close.
- **Split (ADR 0003/0004 — non-negotiable):** the challenge is **narration/judgment
  over already-classified data** — legitimate LLM use. The **policy-breach count is
  deterministic CODE**, never the LLM. The auditor may *say* "zero policy breaches"
  only because code computed it and handed it over. The LLM never computes a
  verdict, a count, or any money/policy math.
- **Degrade like the brain:** if the LLM call fails, fall back to a deterministic
  one-line challenge (pick the position with the worst mark-vs-cost and state it).
  Never crash the close; never block on the auditor.
- **Label honestly:** the line must read as a *second-pass heuristic challenge*, not
  an independent authority — the rubric rewards "reads as a second agent, labeled
  honestly", and overclaiming an oracle is the trap.

### 2. Zero-policy-breach count (deterministic code)
Count, in code, the authority-cap breaches this cycle — by construction of the
execute wall this is **0** (no booking clears above the cap without a human click).
Surface that count on the close. This is the [01-product.md:45](../01-product.md)
success metric made visible: "zero authority-cap breaches."

### 3. Extend the close response (`routes.py` + `models.py` + frontend)
`/close` must additionally carry the breach count + the auditor line. See DECISION 3
for the shape. Whatever you choose, the S5 frontend close screen renders both in the
2:50–3:00 card, honestly labeled.

### 4. One-flag injection (`fixture.py`) — see DECISION 1.

### 5. Cold-open replay pre-warmed
The 0:00–0:10 cold open replays a **real prior cycle** from the SSE buffer (the
buffer+replay plumbing already exists from S1/S5 — a late connect replays from
event 0). "Pre-warmed" = a desk is seeded and its cycle run *before* the demo, so
opening the desk URL replays a complete real cycle instantly. Provide the mechanism
(a small seed-and-run script, or a startup pre-warm) — nothing faked client-side
(this is the S5 "live from real events" cold-open decision; the toast reads "book
decision logged", never "BOOKED"). Keep it comparison-mode honest.

---

## DECISIONS NEEDED — surface these in your plan, do NOT pre-decide

**DECISION 1 — injection: one-flag arm/disarm, or keep always-on?**
Today the loss+spike scenario is hardcoded always-on. 04-slices says "one-flag."
Options: (a) a single flag (env or seed param) that arms the whole scenario,
**default ON** so the demo runs the scripted path but a clean baseline desk is also
possible; (b) keep always-on, treat "one-flag" as satisfied by the single
deterministic seed. My lean: (a) — one flag, default ON — it's low-cost and lets S8
rehearsal show both the scenario and a clean run. Confirm.

**DECISION 2 — auditor: real second Qwen agent, or deterministic heuristic?**
(a) A genuine second Qwen call that narrates the challenge (adds multi-agent
"Agent Technology" rubric credit; must degrade to a deterministic line on failure;
adds one LLM call + a failure mode to the final beat). (b) A pure deterministic
heuristic line (zero LLM risk, but no multi-agent credit). My lean: (a) with a
hard deterministic fallback and honest labeling — the multi-agent close is the
rubric point, and the fallback removes the demo-day risk. Confirm — it's a
risk/reward call on the final beat.

**DECISION 4 — auditor placement: on `DeskAgent`, or at the routes/`/close` layer?**
The 03 spec puts `auditor` on `DeskAgent`'s DI list, but the auditor only ever runs
**once, at close time**, reading the already-settled blotter — it never touches the
live cycle loop. Wiring it into `DeskAgent.__init__` when nothing in `run()` calls
it is dead plumbing. The lower-risk placement: instantiate the auditor in
`routes.py` and call it directly inside the `/close` handler, reading from `STORE`
the same way the handler already reads desk state — no `DeskAgent` change at all.
My lean: **routes-layer, not `DeskAgent`.** Confirm before touching `loop.py`'s
constructor.

**DECISION 3 — close response shape.**
(a) Add optional fields to `DeskResult` (`policy_breaches: int`, `auditor_line: str`)
— additive, but `DeskResult` is also the `result` SSE event payload, so the fields
ride the wire event too (populated only at close). (b) A separate `CloseReport`
wrapper returned only by `/close` = `{ result: DeskResult, policy_breaches, auditor_line }`
— cleaner separation, but the frontend `getClose` type + close screen change more.
My lean: (b) — the auditor/breach data is close-specific and doesn't belong on every
`result` event. Propose your pick.

---

## Demo-day risk (S8 checklist item, flag now)
`DASHSCOPE_API_KEY` is read directly via `os.environ["DASHSCOPE_API_KEY"]`
(`brain.py:251`) — **no `.env` file is loaded anywhere in `app/`** (no `dotenv`
call exists). If the key isn't exported into the actual backend process's shell
env on demo day, `brain.py` already silently degrades to its deterministic
fallback ([brain.py:105](../../../backend/app/agent/brain.py) checks for the key's
absence up front) — and under DECISION 2 (real Qwen auditor), the **auditor would
do the same silently**: demo runs, looks fine, but the "second agent" beat is
quietly running its fallback line the whole time, not the LLM. Not a code bug —
an operational one. Put "confirm `DASHSCOPE_API_KEY` is exported in the exact shell
that launches the backend" on the S8 rehearsal checklist.

Separately: Model Studio's free quota (~1M tokens/model, 90 days from
activation, Singapore region only) can be exhausted mid-quota with an
**HTTP 403 `AllocationQuota.FreeTierOnly`** — a real exception, not a silent
degrade. That's a different failure than "key missing" (which `brain.py:105`
already catches gracefully). Make sure the auditor's (and brain's) Qwen-call
try/except also treats a 403 as a normal fallback trigger, not an unhandled
crash — and add "check remaining free quota isn't near zero" to the S8
rehearsal checklist alongside the key-export check. (Verified: `brain.py:120`
already uses a bare `except Exception`, so a 403 there is already caught —
just make sure the new auditor's Qwen call copies that same bare-except
pattern, not a narrower one.)

## DECIDED mid-build — the human-waiver marker gap (breach count)
`count_policy_breaches` (routes.py) excludes over-cap `trade` rows that carry
`HUMAN_WAIVER_MARKER` ("human waiver") — a human "A" click legitimately waived the
cap, so it is NOT a breach. But the write path (`_write_position` in loop.py) does
**not** emit that marker when `cap_waived=True`, so the exclusion branch is dead.

**Decision: option A — disclose honestly, do NOT complete it this round.**
- loop.py stays zero-change this round (don't touch the write/give-up/settle path
  right after S6 hardening). The routes.py comment discloses the gap honestly: the
  marker is forward-compat only; in comparison mode (demo default) nothing books
  over cap, so the breach count is **structurally 0** and genuinely scanned off the
  blotter, never hardcoded. Real waiver-write is deferred to the live-booking slice.
- **Rejected B** (write the marker as a note prefix): can't be end-to-end tested
  without live booking (never achieved), and — the real reason — making a
  security-critical breach count depend on a **substring in a free-text note** is
  the exact "branch on message text, never on a structured code" anti-pattern this
  codebase bans everywhere else (all of client.py). Completing it hardens that
  fragility.
- **Rejected C** (delete the branch): loses the documented live-mode intent, churn
  you'd reverse.

**FOR THE FUTURE LIVE-BOOKING SLICE (record, don't do now):** the correct live-mode
fix is a **structured waiver field on the ledger row** — a real column/flag on the
ledger entry (e.g. `waived: bool` or a distinct entry kind) — and
`count_policy_breaches` branches on THAT field, never on note text. Do NOT
"complete" the note-prefix approach; that just entrenches the substring anti-pattern.
Bundle this with whichever slice makes `WAYPOINT_LIVE_BOOKING` + real ticketing
actually book, since the marker only ever matters once real over-cap trades exist.

## Frozen constraints — do not break
- **LLM never computes policy or money.** Breach count, cap checks, P&L, budget math
  are all code. The auditor LLM only narrates/challenges already-classified data.
- **Books still tie out** (S6 invariant): don't regress the give-up settle-flush or
  the `budget_exhausted` label. All 83 tests stay green.
- **All 4 `DeskStatus` values stay genuinely reachable**; the frontend already
  branches on them. Don't add a status value silently.
- **Nothing faked client-side.** Cold open replays real buffered events; comparison
  mode stays labeled; no "BOOKED"/TICKETED state the backend didn't emit.
- **No auto-commit. No AI co-author trailer.** Jaydon commits.

## Working style
- Honesty over agreeableness; never invent a number, field, or code; flag
  uncertainty; no opening praise.
- **Surface DECISIONS 1–3 in your plan and wait for Jaydon's call before building
  them.**
- Plain language, explain reasoning. Propose a plan (Spec Mode) before large changes.
- A separate reviewer (Claude Code) cross-checks your output against this spec.

## Done =
`/close` carries P&L + zero-breach count + one honest auditor challenge; the S5
close screen renders all three, the auditor line labeled as a second-pass agent;
injection is one-flag; the cold open replays a real pre-warmed cycle instantly.
Existing 83 tests green + new tests (auditor line present & degrades; breach count
is code-derived and zero on the scripted run; close-shape). Beat 2:50–3:00 lands
on screen, comparison-mode honest.
