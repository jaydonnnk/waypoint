"""DeskBrain — the advise gate (judgment ONLY; it executes nothing).

Two gates (ADR 0003/0004): the LLM (Qwen via DashScope) owns ONLY the
book/hold/escalate picks + the absorb/re-quote judgment. Deterministic
code (the loop's execute wall) owns ledger math, cap/budget checks, and
order/pay execution, and re-checks EVERY pick AFTER the brain returns.

Transport: ONE batched Qwen call per cycle — every position rides in a
single prompt — via plain httpx against DashScope's OpenAI-compatible
endpoint (no new SDK dependency). The async call is wrapped in
`asyncio.wait_for(15s)`. The transport is injectable (`transport=`) so
tests never touch the network.

Fallback discipline: ANY failure (no key, transport error, timeout,
non-JSON, missing/duplicate position ids, unknown kind) degrades to the
deterministic prior-band rule emitting the IDENTICAL `DeskAction` shape —
the fallback is the fallback, never the on-camera path while the API
works. The brain never touches the AtlasClient or the DeskStore.
"""
from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from typing import Awaitable, Callable, Literal

import httpx

from app.models import DeskAction, Position

# DashScope OpenAI-compatible endpoint (no SDK dependency).
DASHSCOPE_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
DEFAULT_MODEL = "qwen-plus"
JUDGE_TIMEOUT_SECONDS = 15.0

# Disclosed in every fallback rationale (honesty register).
FALLBACK_NOTE = "deterministic fallback (prior-band rule) — Qwen unavailable"

# The transport seam: async (messages list) -> raw model content string.
Transport = Callable[[list[dict]], Awaitable[str]]

# Curated route classification for the demo routes (no ML — disclosed
# approximation, ADR 0002 precedent). Unknown pairs default to mid_haul.
ROUTE_TYPES: dict[tuple[str, str], str] = {
    ("SIN", "NRT"): "long_haul",
    ("DAC", "LHR"): "long_haul",
    ("JFK", "LIS"): "long_haul",
    ("BKK", "ICN"): "mid_haul",
    ("SYD", "SIN"): "long_haul",
    ("GRU", "MIA"): "long_haul",
}


def route_type_for(origin: str, dest: str) -> str:
    """Curated route-type lookup; unknown pairs fall back to mid_haul."""
    return ROUTE_TYPES.get((origin, dest), "mid_haul")


def move_pct(position: Position) -> float:
    """Mark-vs-cost_basis relative move (float fraction, signed)."""
    if position.cost_basis == 0:
        return 0.0
    return float(
        (position.mark_price - position.cost_basis) / position.cost_basis
    )


class DeskBrain:
    """Advise-gate judgment. Sees everything; executes nothing."""

    def __init__(
        self,
        transport: Transport | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = JUDGE_TIMEOUT_SECONDS,
    ):
        # Injectable for tests (monkeypatchable seam; tests never hit the
        # network). Default transport posts to DashScope via httpx.
        self._transport = transport
        self._model = model
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Advise gate: ONE batched call per cycle (all positions, one prompt).
    # ------------------------------------------------------------------

    async def judge(
        self,
        positions: list[Position],
        priors: dict[str, dict],
        meter_left: int,
        budget_left: Decimal,
        contingency_left: Decimal,
    ) -> list[DeskAction]:
        """Recommend book/hold/escalate per position. NEVER raises — any
        failure degrades to the deterministic prior-band fallback with the
        identical DeskAction shape."""
        fallback = self.fallback_actions(positions, priors)
        if not positions:
            return []
        # LLM unavailable (no key and no injected transport) → fallback.
        if self._transport is None and not os.environ.get("DASHSCOPE_API_KEY"):
            return fallback
        try:
            prompt = self._build_prompt(
                positions, priors, meter_left, budget_left, contingency_left
            )
            raw = await asyncio.wait_for(
                self._complete(prompt), timeout=self._timeout
            )
            actions = self._validate(
                json.loads(self._strip_to_json(raw)), positions
            )
            if actions is None:
                return fallback
            return actions
        except Exception:  # noqa: BLE001 — degrade, never crash the cycle
            return fallback

    # ------------------------------------------------------------------
    # Deterministic prior-band rule — the FALLBACK ONLY.
    # ------------------------------------------------------------------

    def fallback_actions(
        self, positions: list[Position], priors: dict[str, dict]
    ) -> list[DeskAction]:
        """Prior-band rule: mark ran past the curated band top → book
        (lock the fare); anything else (inside or below the band) → hold.
        Every rationale carries the fallback disclosure."""
        actions: list[DeskAction] = []
        for pos in positions:
            band = priors[route_type_for(pos.origin, pos.dest)]["band_pct"]
            move = move_pct(pos)
            if move >= band[1]:
                kind: Literal["book", "hold", "escalate"] = "book"
                reason = (
                    f"mark {move:+.1%} vs cost basis ran past the curated "
                    f"band top {band[1]:.0%} — lock the fare"
                )
            else:
                kind = "hold"
                reason = (
                    f"mark {move:+.1%} vs cost basis stays inside/below the "
                    f"curated band ({band[0]:.0%}–{band[1]:.0%}) — timing "
                    "not triggered"
                )
            actions.append(
                DeskAction(
                    position_id=pos.id,
                    kind=kind,
                    rationale=f"{reason}. {FALLBACK_NOTE}",
                )
            )
        return actions

    # ------------------------------------------------------------------
    # Absorb vs re-quote — PURE deterministic code (judgment boundary only;
    # the ledger math stays in the loop).
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_price_change(
        delta: Decimal, contingency_left: Decimal
    ) -> Literal["absorb", "requote"]:
        """Absorb while the increase fits the contingency remainder
        (boundary inclusive); otherwise re-quote. Never a second order."""
        return "absorb" if delta <= contingency_left else "requote"

    # ------------------------------------------------------------------
    # Admitted-loss detection — the brain supplies the note/threshold text;
    # the LOOP logs the loss via the ledger (kind="loss").
    # ------------------------------------------------------------------

    @staticmethod
    def admitted_loss(
        position: Position, priors: dict[str, dict]
    ) -> tuple[Decimal, str] | None:
        """Held position whose mark moved against the desk past the
        disclosed curated band floor → (amount, note). None otherwise."""
        if position.status != "held":
            return None
        band = priors[route_type_for(position.origin, position.dest)][
            "band_pct"
        ]
        move = move_pct(position)
        if move > -band[0]:
            return None
        amount = position.mark_price - position.cost_basis  # negative
        note = (
            f"held too long, −${abs(amount):.2f}, threshold adjusted "
            f"(curated band floor −{band[0]:.0%}, disclosed — no ML)"
        )
        return amount, note

    # ------------------------------------------------------------------
    # Internals: prompt, transport, defensive parse.
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(
        positions: list[Position],
        priors: dict[str, dict],
        meter_left: int,
        budget_left: Decimal,
        contingency_left: Decimal,
    ) -> str:
        lines = []
        for pos in positions:
            rtype = route_type_for(pos.origin, pos.dest)
            band = priors[rtype]["band_pct"]
            lines.append(
                f"- {pos.id}: {pos.origin}→{pos.dest} on "
                f"{pos.depart_date.isoformat()}, pax {pos.pax}, "
                f"cost_basis {pos.cost_basis}, mark {pos.mark_price} "
                f"({move_pct(pos):+.1%} vs basis), "
                f"stale_mark={pos.mark_stale}, prior band "
                f"{band[0]:.0%}–{band[1]:.0%} ({priors[rtype]['label']})"
            )
        return (
            "You are the desk brain of Waypoint, a travel-fare desk. "
            "ADVISE GATE ONLY: you recommend; deterministic code executes "
            "and re-checks every pick after you — never invent position "
            "ids, amounts, or actions outside book/hold/escalate.\n"
            "Weigh, for EVERY position together: its mark vs the curated "
            "prior band (disclosed approximation, no ML), the search meter "
            "remaining, the remaining budget, and the remaining "
            "contingency. A stale mark means uncertainty — lean hold.\n"
            f"Search meter remaining: {meter_left}/20. "
            f"Budget remaining: {budget_left}. "
            f"Contingency remaining: {contingency_left}.\n"
            "Positions:\n"
            + "\n".join(lines)
            + "\nRespond with ONLY a JSON array — no prose, no markdown — "
            "one object per position, exactly: "
            '[{"position_id": "...", "kind": "book"|"hold"|"escalate", '
            '"rationale": "one sentence weighing priors, meter, budget and '
            'contingency"}]'
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
    def _strip_to_json(raw: str) -> str:
        """Tolerate a model that wraps the array in markdown fences or prose
        (MJ3): extract the outermost [...] so valid Qwen output is not
        silently dropped to the deterministic fallback. If no array is found,
        return the text unchanged (json.loads then fails -> fallback, exactly
        as before)."""
        text = raw.strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]
        return text

    @staticmethod
    def _validate(
        parsed: object, positions: list[Position]
    ) -> list[DeskAction] | None:
        """Defensive parse: strict JSON list covering EVERY position exactly
        once with a legal kind; anything else → None (caller falls back)."""
        if not isinstance(parsed, list):
            return None
        expected = {p.id for p in positions}
        seen: set[str] = set()
        actions: list[DeskAction] = []
        for item in parsed:
            if not isinstance(item, dict):
                return None
            pid = item.get("position_id")
            kind = item.get("kind")
            rationale = item.get("rationale")
            if (
                not isinstance(pid, str)
                or pid not in expected
                or pid in seen
                or kind not in ("book", "hold", "escalate")
                or not isinstance(rationale, str)
                or not rationale.strip()
            ):
                return None
            seen.add(pid)
            actions.append(
                DeskAction(position_id=pid, kind=kind, rationale=rationale)
            )
        if seen != expected:
            return None
        return actions
