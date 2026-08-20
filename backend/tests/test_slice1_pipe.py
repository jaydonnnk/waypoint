"""Slice-1 tracer tests — PLACEHOLDERS, by design.

These only "test the mock": they catch a broken pipe (wrong shapes, dead
endpoints, a stream that never completes) but they cannot catch a logic bug,
because Slice 1 has no logic yet — the RecoveryResult is hardcoded.

Software-factory rule: a test that can't fail tests nothing. These MUST be
replaced by the real-logic tests from 03-program-design.md in Slices 3-6
(fail-closed rules, execute wall, cheapest-EXECUTABLE pick, re-verify-before-
book, ticket assertion, step budget, persistence). Do NOT keep them as green
padding once real behavior exists.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from fastapi.testclient import TestClient

from app.agent.loop import RecoveryAgent
from app.main import app


def test_recovery_result_contract():
    """The hardcoded result must mirror the Gate 3 shapes + demo numbers."""
    events: list[dict] = []

    async def run_agent():
        async def emit(event: dict) -> None:
            events.append(event)

        return await RecoveryAgent(step_budget=12).run("trip-test", emit)

    result = asyncio.run(run_agent())

    assert result.status == "recovered"
    assert result.chosen is not None and result.chosen.id == "opt-icn"
    assert result.chosen.price == Decimal("458")
    assert result.rejected_cheapest is not None
    assert result.rejected_cheapest.id == "opt-sgn"
    assert result.rejected_cheapest.price == Decimal("236")

    order = result.order
    assert order is not None
    assert order.fare_diff == Decimal("92")
    assert order.settled is True
    assert order.ticket_asserted is True
    assert order.pnr and order.ticket_number

    # The advise-gate narration must name the rejected trap (ADR 0003).
    assert result.rationale is not None and "SGN" in result.rationale

    # The emitted event sequence drives Screen 2 + Screen 3.
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert types.count("step") == result.step_count == 6
    assert "options" in types and "decision" in types and types[-1] == "result"


def test_disruption_then_recovery_endpoint():
    """POST /api/disruptions returns a trip id and recovery completes."""
    with TestClient(app) as client:
        resp = client.post("/api/disruptions")
        assert resp.status_code == 200
        trip_id = resp.json()["trip_id"]
        assert trip_id.startswith("trip-")

        final = client.get(f"/api/trips/{trip_id}/recovery")
        assert final.status_code == 200
        body = final.json()
        assert body["status"] == "recovered"
        assert body["chosen"]["id"] == "opt-icn"
        assert body["rejected_cheapest"]["id"] == "opt-sgn"


def test_stream_endpoint_emits_canned_events():
    """GET /api/trips/{id}/stream yields the full canned event sequence."""
    with TestClient(app) as client:
        trip_id = client.post("/api/disruptions").json()["trip_id"]
        seen: list[dict] = []
        with client.stream("GET", f"/api/trips/{trip_id}/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    seen.append(json.loads(line[len("data: "):]))
                    if seen[-1]["type"] == "result":
                        break

    types = [e["type"] for e in seen]
    assert types[0] == "meta" and types[-1] == "result"
    assert types.count("step") == 6
    assert "options" in types and "decision" in types
    # Screen 2's table needs the assessments with layovers + verdicts.
    options = next(e for e in seen if e["type"] == "options")
    assert len(options["assessments"]) == 3
    assert all("layovers" in a and "verdicts" in a for a in options["assessments"])
