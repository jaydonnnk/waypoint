# BRAIN-EVAL — the DeskBrain eval harness (Slice S13)

The brain eval measures the advise gate — live Qwen vs the deterministic
prior-band fallback vs the execute wall — over 12 invented band-edge
scenarios (`backend/tests/evals/brain_cases.py`). It is **opt-in and
deliberately OUTSIDE the quality gate**, because it costs real money to run.

## Why the harness is excluded from the gate

Orkestr's live-eval rationale, quoted from `vitest.live.config.ts`:

> "A network outage, a rate limit or an expired key must not turn the
> deterministic suite red. If a live failure could fail CI, the reflex
> becomes to distrust the suite, and at that point the honest tests stop
> meaning anything."

Waypoint enforces the same separation with pytest markers instead of a
separate vitest config:

- `backend/pytest.ini`: `addopts = -m "not live and not eval"` — the
  default gate run deselects every eval test; collection cannot even see
  them run.
- `test_brain_eval.py` carries `@pytest.mark.eval` and a clean `skipif`
  when `DASHSCOPE_API_KEY` is absent — no key, no spend, no red.
- Run it explicitly: `cd backend && python -m pytest -m eval -s`.

## What the numbers mean

The harness prints one row per case:

```
case | expected band | qwen pick | fallback pick | in-band | agreement
```

plus summary rates. Every number is **reported, never asserted** — the
file contains no assertion on rates or model quality (exact Orkestr
posture: expectations are structural bands, and live results are read by
a human, not enforced by CI).

- **Expected band** — the frozenset of legal `DeskAction` kinds for that
  scenario (structure, never prose). Membership is computed and printed,
  not enforced.
- **Agreement rate** — how often live Qwen's pick kind matches the
  deterministic fallback's pick kind. High agreement means the fallback
  is a honest understudy for the demo; low agreement on band-edge cases
  is where the model earns its keep (or its scrutiny).
- **In-band rate** — how often each source's pick lands inside the
  scenario's legal band.
- **Execute-wall overrule rate** — the fraction of picks where the brain
  said `book` but the loop's deterministic cap/budget re-check
  (`loop.py:340-389`, re-checked AFTER the brain returns) refused to
  execute it and routed the pick to the human escalation beat instead.
  This is the architecture's headline number: the LLM recommends, code
  disposes. A `*` in the qwen column means the live answer degraded to
  the deterministic fallback (disclosed, never hidden).

## The demo line

> "The model is overruled by code **N%** of the time."

N = the execute-wall overrule rate from the most recent real run of
`python -m pytest -m eval -s` (qwen picks column).

**Last real run (2026-08-25, qwen-plus via DashScope, 12 solo judge
calls):**

| metric | qwen | deterministic fallback |
| --- | --- | --- |
| in-band rate | 7/12 = 58.3% | 9/12 = 75.0% |
| execute-wall overrule rate | **1/12 = 8.3%** | 3/12 = 25.0% |
| agreement (qwen vs fallback) | 5/12 = 41.7% | — |

So the demo line, filled from that run: **the model is overruled by code
8.3% of the time** — and when the deterministic fallback stands in for
the model, code overrules it 25.0% of the time (it band-books blind,
ignoring cap, budget and meter — exactly why it is the understudy, not
the desk). Reads from that table: Qwen escalated on its own at the two
over-cap spikes (c09/c11) where the fallback still said book, and the
wall caught every remaining unsafe pick either way — no pick that left
the table as `execute` was over cap or over budget.

_Note: rates are single-run snapshots of a nondeterministic model;
re-run to refresh. They are printed by the harness, never asserted._

**Accepted decision (2026-08-26):** the execute-wall overrule rate is
computed via a faithful inline reproduction of the wall's cap/budget
re-check (`loop.py:340-389`) rather than full cycles — accepted because
the wall lives inline in `DeskAgent.run` and cannot be imported; the
known cost is drift risk: if the wall's code changes, the reproduction
must be re-synced or the rate silently measures the old wall (the
line-range citation above is the sync pin).

## Inventory

- `backend/tests/evals/brain_cases.py` — 12 invented band-edge Position
  scenarios, each with an expected action band (`frozenset` of legal
  kinds). Above-band spikes (mid + long haul), exact band-top boundary,
  in-band holds, below-floor and deep losses, a stale mark, an over-cap
  book, a budget-starved book, a meter-exhausted spike, and an
  escalation-worthy stale over-cap spike.
- `backend/tests/evals/test_brain_eval.py` — the harness (one eval test,
  one printed report). The wall re-check is a faithful thin reproduction
  of `loop.py:340-389` (inline in `DeskAgent.run`, not importable).
- `backend/pytest.ini` — `eval` marker registered; default gate deselects
  `live` and `eval`.
