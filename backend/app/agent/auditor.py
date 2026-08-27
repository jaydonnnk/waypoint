"""RiskAuditor — the close-time second-pass risk officer (S7).

Multi-agent flavor for the weekly close: a SECOND Qwen call reads the
desk's own ALREADY-CLASSIFIED blotter (trip labels, routes, cost vs
mark, ledger kinds/notes) and challenges ONE trade in ONE sentence.

Two gates (ADR 0003/0004 — non-negotiable): the auditor NEVER computes
policy or money. The policy-breach count arrives pre-computed by
deterministic code as a FACT it may quote but never recompute, and the
prompt forbids inventing any number. The auditor is labeled honestly —
a second-pass heuristic challenge, never an oracle or authority.

Transport mirrors DeskBrain's discipline 1:1: ONE call via plain httpx
against DashScope's OpenAI-compatible endpoint (no SDK), bounded by
`asyncio.wait_for`, NO retry, injectable `transport=` seam so tests
never touch the network. ANY failure (no key, transport error, timeout,
garbage / multi-sentence / over-length output) degrades to the
deterministic one-line challenge. `read` never raises and never blocks
or crashes the close.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Awaitable, Callable

import httpx

from app.agent.brain import DASHSCOPE_URL, DEFAULT_MODEL
from app.models import Mandate, Position

# Tighter than the brain's 15s — the close must never wait long on narration.
AUDITOR_TIMEOUT_SECONDS = 6.0

# Output contract: ONE sentence, length-capped; anything else degrades.
LINE_MAX_CHARS = 240

# Honest label — the auditor is a heuristic challenge, never an authority.
AUDITOR_LABEL = (
    "second-pass risk officer (heuristic challenge, not an authority)"
)

# Wire values for CloseReport.auditor_source (frozen contract — exactly these).
SOURCE_AGENT = "agent"
SOURCE_FALLBACK = "deterministic-fallback"

# The transport seam: async (messages list) -> raw model content string.
Transport = Callable[[list[dict]], Awaitable[str]]


def fallback_challenge(
    positions: list[Position], policy_breaches: int | None = None
) -> str:
    """Deterministic one-line challenge — PURE code, zero LLM.

    Names the position with the worst mark-vs-cost delta and asks whether
    the hold was deliberate; quotes the code-computed breach count when
    one is handed in. Never invents a number: every figure is read
    straight off the blotter objects it is given.
    """
    if positions:
        worst = min(positions, key=lambda p: p.mark_price - p.cost_basis)
        delta = worst.mark_price - worst.cost_basis
        move = f"\u2212${abs(delta):.2f}" if delta < 0 else f"+${delta:.2f}"
        line = (
            f"Held {worst.origin}\u2192{worst.dest} through a {move} "
            f"mark-vs-cost move \u2014 was the timing deliberate?"
        )
    else:
        line = (
            "Nothing on the blotter to challenge \u2014 the desk closed "
            "without a position worth second-guessing."
        )
    if policy_breaches is not None:
        line += f" Code-computed policy breaches this cycle: {policy_breaches}."
    return line


# Mirror of routes.HUMAN_WAIVER_MARKER (kept literal here: routes imports
# THIS module, so importing routes back would be circular). Same discipline
# as count_policy_breaches — branch on structured row fields, never prose.
_WAIVER_MARKER = "human waiver"


def _money(amount: Decimal, currency: str) -> str:
    """Display-only money formatting of a blotter figure — never computed
    into anything. Whole dollars drop the ".00"; non-USD keeps its code."""
    text = f"{amount:,.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    return f"${text}" if currency == "USD" else f"{currency} {text}"


def plain_challenge(
    mandate: Mandate,
    positions: list[Position],
    ledger_tail: list[dict],
    policy_breaches: int,
) -> str:
    """Plain-English second opinion — PURE code, zero LLM (task #8).

    Built from the SAME structured facts the auditor read; the free-text
    `auditor_line` is never parsed, summarized, or quoted here, and every
    figure is read straight off the blotter objects handed in. Branches on
    structured facts only:
    1. OVER-CAP BOOK: a `trade` row over the authority cap without the
       waiver marker → name its position's route, the amount, the cap.
    2. HOLD: the worst mark-vs-cost position (same pick as
       `fallback_challenge`) → route + the blotter delta, sign-aware.
    3. NOTHING TO CHALLENGE: empty blotter → the clean-run line.
    Works identically for the agent and the deterministic-fallback paths —
    it comes from code facts, not from whichever line won.
    """
    if policy_breaches > 0:
        for row in ledger_tail:
            if row.get("kind") != "trade":
                continue
            amount = row.get("amount")
            if amount is None or amount <= mandate.authority_cap:
                continue
            if _WAIVER_MARKER in (row.get("note") or ""):
                continue
            pos = next(
                (p for p in positions if p.id == row.get("position_id")),
                None,
            )
            if pos is not None:
                return (
                    f"Your reviewer double-checked the {pos.origin}\u2192"
                    f"{pos.dest} booking: at {_money(amount, mandate.currency)} "
                    "it\u2019s over your "
                    f"{_money(mandate.authority_cap, mandate.currency)} "
                    "auto-approve limit, so nothing was booked without "
                    "your OK."
                )
            break  # breaching row with no resolvable position → hold path
    if positions:
        worst = min(positions, key=lambda p: p.mark_price - p.cost_basis)
        delta = worst.mark_price - worst.cost_basis
        direction = "less" if delta < 0 else "more"
        return (
            f"Your reviewer flagged the {worst.origin}\u2192{worst.dest} "
            f"trip we held: it now prices {_money(abs(delta), mandate.currency)} "
            f"{direction} than we paid \u2014 worth a look before the next cycle."
        )
    return (
        "Your reviewer checked every decision on this run and found "
        "nothing to challenge."
    )


class RiskAuditor:
    """Close-time narration ONLY. Reads the settled blotter; computes
    nothing — every number and the breach count are code-owned facts."""

    def __init__(
        self,
        transport: Transport | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = AUDITOR_TIMEOUT_SECONDS,
    ):
        # Injectable for tests (monkeypatchable seam; tests never hit the
        # network). Default transport posts to DashScope via httpx.
        self._transport = transport
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------
    # The one close-time read: (line, source). NEVER raises.
    # ------------------------------------------------------------------

    async def read(
        self,
        mandate: Mandate,
        positions: list[Position],
        ledger_tail: list[dict],
        policy_breaches: int,
    ) -> tuple[str, str]:
        """Return (line, source) where source is exactly "agent" or
        "deterministic-fallback". ANY failure degrades — the close never
        waits on, or crashes because of, the auditor."""
        fallback = (
            fallback_challenge(positions, policy_breaches),
            SOURCE_FALLBACK,
        )
        # LLM unavailable (no key and no injected transport) → fallback.
        if self._transport is None and not os.environ.get("DASHSCOPE_API_KEY"):
            return fallback
        try:
            prompt = self._build_prompt(
                mandate, positions, ledger_tail, policy_breaches
            )
            raw = await asyncio.wait_for(
                self._complete(prompt), timeout=self._timeout
            )
            line = self._validate(raw)
            if line is None:
                return fallback
            return line, SOURCE_AGENT
        except Exception:  # noqa: BLE001 — degrade, never crash the close
            return fallback

    # ------------------------------------------------------------------
    # Internals: prompt, transport, defensive validation.
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        mandate: Mandate,
        positions: list[Position],
        ledger_tail: list[dict],
        policy_breaches: int,
    ) -> str:
        pos_lines = [
            f"- {pos.id}: {pos.origin}\u2192{pos.dest} "
            f"\u201c{pos.trip_label}\u201d, cost {pos.cost_basis}, mark "
            f"{pos.mark_price} ({pos.mark_price - pos.cost_basis:+} vs cost)"
            for pos in positions
        ]
        ledger_lines = [
            f"- {row.get('kind')}: {row.get('note') or '(no note)'}"
            for row in ledger_tail
        ] or ["- (empty)"]
        return (
            "You are the risk officer of Waypoint, a travel-fare desk \u2014 "
            f"{AUDITOR_LABEL}. You are reading the desk's own "
            "ALREADY-CLASSIFIED blotter after the cycle settled. Narration "
            "and judgment ONLY: deterministic code computed every number "
            "below and owns ALL policy and money math.\n"
            "Policy-breach count, computed by code \u2014 you may QUOTE it, "
            f"NEVER recompute it: {policy_breaches}.\n"
            "NEVER invent, alter, or recalculate any number, amount, or "
            "count; use only the figures given here.\n"
            f"Mandate {mandate.id}, currency {mandate.currency}, "
            f"authority cap {mandate.authority_cap} (a code-owned fact).\n"
            "Positions (cost vs mark as classified by the desk):\n"
            + "\n".join(pos_lines)
            + "\nLedger tail (kind \u2014 note):\n"
            + "\n".join(ledger_lines)
            + "\nRespond with EXACTLY ONE sentence, at most "
            f"{LINE_MAX_CHARS} characters, challenging exactly ONE trade "
            "or hold decision \u2014 no preamble, no markdown, no second "
            "sentence."
        )

    async def _complete(self, prompt: str) -> str:
        if self._transport is not None:
            return await self._transport(
                [{"role": "user", "content": prompt}]
            )
        # Default transport: plain httpx against DashScope. The key is
        # read from the environment here — it never appears in code,
        # logs, or the prompt.
        api_key = os.environ["DASHSCOPE_API_KEY"]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                DASHSCOPE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _validate(raw: object) -> str | None:
        """ONE non-empty sentence within the length cap, else None (the
        caller degrades to the deterministic fallback). Defensive: a
        non-str reply, embedded newlines, a second sentence, or an
        over-length line are all garbage."""
        if not isinstance(raw, str):
            return None
        line = raw.strip()
        if not line or "\n" in line:
            return None
        if len(line) > LINE_MAX_CHARS:
            return None
        # Single sentence: allow ONE terminator at the very end only. A
        # "." flanked by digits is a decimal point ("−$22.00"), not a
        # sentence break — blotter money rides in the line legitimately.
        core = line[:-1] if line[-1] in ".!?" else line
        for i, ch in enumerate(core):
            if ch not in ".!?":
                continue
            if (
                ch == "."
                and 0 < i < len(core) - 1
                and core[i - 1].isdigit()
                and core[i + 1].isdigit()
            ):
                continue
            return None
        return line
