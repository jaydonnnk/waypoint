"""Brain eval — live Qwen vs the deterministic fallback vs the execute wall.

S13 posture (modeled on Orkestr's evals/, adapted to pytest — NOT copied):

* OPT-IN AND OUTSIDE THE GATE. pytest.ini deselects `eval` by default
  (`addopts = -m "not live and not eval"`), and this module skips itself
  cleanly when DASHSCOPE_API_KEY is absent. Orkestr's rationale, quoted
  from vitest.live.config.ts: a live failure must never turn the
  deterministic suite red — if it could, "the honest tests stop meaning
  anything". This harness costs real money per run, so it never rides CI.
* EXPECTATIONS ARE STRUCTURAL BANDS, NOT PROSE (cases.ts:24-62 posture):
  each case in brain_cases.py carries a frozenset of legal DeskAction
  kinds; the harness computes membership and prints it.
* REPORTED, NEVER ASSERTED. The table + the agreement rate and the
  execute-wall overrule rate are printed for a human to read. There is
  NO assertion on rates or on model quality anywhere in this file — a
  bad Qwen day is a data point, not a red gate.

Each case is judged SOLO (one position per `judge` call) so the table
maps one row to one paid model call; the production loop batches all
positions into one call, which is covered by the deterministic suite.

The execute-wall re-check is reproduced faithfully below because the
checks live INLINE inside DeskAgent.run (app/agent/loop.py:340-389) and
are not importable as a function. If the loop ever factors them out,
import that instead and delete the reproduction.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest

from app.agent.brain import FALLBACK_NOTE, DeskBrain
from app.fixture import VOLATILITY_PRIORS
from app.models import DeskAction, Position

from tests.evals.brain_cases import BRAINS_CASES, BrainCase

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("DASHSCOPE_API_KEY"),
        reason=(
            "DASHSCOPE_API_KEY not set — the brain eval is opt-in and "
            "costs real money (pytest -m eval -s)"
        ),
    ),
]


# ----------------------------------------------------------------------
# Execute-wall re-check — faithful thin reproduction of the cap/budget
# gate in app/agent/loop.py:340-389 (inline in DeskAgent.run; not an
# importable function). Same comparisons, same order, same verdicts:
# hold passes untouched; any book/escalate pick is priced at the mark,
# and over-cap OR over-budget OR an escalate pick routes to the human
# escalation beat instead of executing.
# ----------------------------------------------------------------------
def wall_verdict(
    pick_kind: str, position: Position,
    authority_cap: Decimal, budget_left: Decimal,
) -> str:
    """The wall's answer to one brain pick: 'execute' | 'escalate' | 'hold'.

    Mirrors loop.py:330 ('hold' → continue) and loop.py:341-347 (amount =
    mark_price; over_cap / over_budget; escalate-or-wall → escalation beat).
    Budget is NEVER waived (loop.py:382-388); the cap waiver needs the one
    human click, which this harness does not simulate — a walled pick is
    reported as 'escalate', i.e. the model's pick did not execute as-is.
    """
    if pick_kind == "hold":
        return "hold"
    amount = position.mark_price
    over_cap = amount > authority_cap
    over_budget = amount > budget_left
    if pick_kind == "escalate" or over_cap or over_budget:
        return "escalate"
    return "execute"


def overruled(pick_kind: str, verdict: str) -> bool:
    """The wall overrules the brain only when a 'book' pick fails the
    cap/budget re-check and is routed to the human beat instead of
    executing. A hold honored as hold and an escalate honored as escalate
    are the wall doing EXACTLY what the brain asked — not an overrule."""
    return pick_kind == "book" and verdict != "execute"


def _fmt_band(band: frozenset) -> str:
    order = ("book", "hold", "escalate")
    return "{" + ",".join(k for k in order if k in band) + "}"


def _fmt_pick(action: DeskAction) -> str:
    """Mark a degraded pick: if the rationale carries FALLBACK_NOTE the
    'live' column actually shows the deterministic fallback (transparency
    over a pretend number)."""
    star = "*" if FALLBACK_NOTE in action.rationale else " "
    return f"{action.kind}{star}"


async def _run_eval() -> dict:
    brain = DeskBrain()  # default transport = live DashScope (real money)
    rows: list[dict] = []
    for case in BRAINS_CASES:
        qwen_actions = await brain.judge(
            [case.position], VOLATILITY_PRIORS,
            case.meter_left, case.budget_left, case.contingency_left,
        )
        fallback_actions = brain.fallback_actions(
            [case.position], VOLATILITY_PRIORS
        )
        # Harness integrity (contract shape, NOT model quality): judge
        # never raises and always answers every position exactly once.
        assert len(qwen_actions) == 1
        assert qwen_actions[0].position_id == case.position.id
        assert qwen_actions[0].kind in ("book", "hold", "escalate")
        assert len(fallback_actions) == 1

        qwen_pick, fb_pick = qwen_actions[0], fallback_actions[0]
        qwen_verdict = wall_verdict(
            qwen_pick.kind, case.position,
            case.authority_cap, case.budget_left,
        )
        fb_verdict = wall_verdict(
            fb_pick.kind, case.position,
            case.authority_cap, case.budget_left,
        )
        rows.append({
            "case": case,
            "qwen": qwen_pick.kind,
            "qwen_fmt": _fmt_pick(qwen_pick),
            "fallback": fb_pick.kind,
            "qwen_in_band": qwen_pick.kind in case.band,
            "fb_in_band": fb_pick.kind in case.band,
            "agree": qwen_pick.kind == fb_pick.kind,
            "qwen_verdict": qwen_verdict,
            "fb_verdict": fb_verdict,
            "qwen_overruled": overruled(qwen_pick.kind, qwen_verdict),
            "fb_overruled": overruled(fb_pick.kind, fb_verdict),
        })
    return {"rows": rows}


def _print_report(rows: list[dict]) -> None:
    header = (
        f"{'case':<20} {'expected band':<18} {'qwen':>7} {'fallback':>8} "
        f"{'qwen-in-band':>12} {'fb-in-band':>10} {'agree':>6} "
        f"{'wall(qwen)':>11} {'wall(fb)':>9}"
    )
    print("\n=== BRAIN EVAL — live Qwen vs deterministic fallback ===")
    print("(scenarios invented; bands structural; numbers reported, "
          "never asserted)")
    print(header)
    print("-" * len(header))
    for row in rows:
        case: BrainCase = row["case"]
        print(
            f"{case.id:<20} {_fmt_band(case.band):<18} "
            f"{row['qwen_fmt']:>7} {row['fallback']:>8} "
            f"{str(row['qwen_in_band']):>12} {str(row['fb_in_band']):>10} "
            f"{str(row['agree']):>6} "
            f"{row['qwen_verdict']:>11} {row['fb_verdict']:>9}"
        )
    n = len(rows)
    agree = sum(r["agree"] for r in rows)
    qwen_in = sum(r["qwen_in_band"] for r in rows)
    fb_in = sum(r["fb_in_band"] for r in rows)
    qwen_ovr = sum(r["qwen_overruled"] for r in rows)
    fb_ovr = sum(r["fb_overruled"] for r in rows)
    print("-" * len(header))
    print(f"agreement rate (qwen vs fallback): {agree}/{n} = "
          f"{agree / n:.1%}")
    print(f"qwen in-band rate:  {qwen_in}/{n} = {qwen_in / n:.1%}")
    print(f"fallback in-band rate: {fb_in}/{n} = {fb_in / n:.1%}")
    print(f"execute-wall overrule rate (qwen picks): {qwen_ovr}/{n} = "
          f"{qwen_ovr / n:.1%}")
    print(f"execute-wall overrule rate (fallback picks): {fb_ovr}/{n} = "
          f"{fb_ovr / n:.1%}")
    print("* = the 'live' answer degraded to the deterministic fallback "
          "(FALLBACK_NOTE in rationale)")
    print("wall verdict: execute = pick ran as-is; escalate = cap/budget "
          "re-check routed the pick to the human beat")


def test_brain_eval_qwen_vs_fallback_vs_wall():
    """The whole eval is ONE test: one report, read by a human. Reported,
    never asserted — a live Qwen regression is a number on this table,
    never a red deterministic gate."""
    report = asyncio.run(_run_eval())
    _print_report(report["rows"])
