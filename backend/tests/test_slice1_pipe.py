"""Pipe tests — Slice 2.

The SEARCH is now real, so these tests inject a deterministic stub client
(RecoveryAgent DI) and assert the PIPE for real: event contract, cheapest-
candidate selection, layover geography on the wire, and clean give-up on
no-results / search failure. Live-sandbox coverage lives in
test_atlas_sandbox_live.py (opt-in, `-m live`).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import fixture
from app.agent.loop import RecoveryAgent
from app.api import routes
from app.atlas.client import AtlasError, AtlasNoResults
from app.main import app


class StubAtlas:
    """Deterministic stand-in for AtlasClient.search (same signature)."""

    def __init__(self, offers=None, exc: Exception | None = None):
        self._offers = offers if offers is not None else fixture.demo_offers()
        self._exc = exc
        self.calls: list[tuple] = []

    def search(self, origin, dest, dep, pax):
        self.calls.append((origin, dest, dep, pax))
        if self._exc is not None:
            raise self._exc
        return self._offers


@pytest.fixture()
def stub_agent(monkeypatch):
    """Route the HTTP endpoints through a stubbed agent (no live calls)."""
    stub = StubAtlas()
    monkeypatch.setattr(
        routes, "AGENT", RecoveryAgent(step_budget=12, atlas=stub)
    )
    return stub


def _run_agent(atlas) -> tuple[object, list[dict]]:
    events: list[dict] = []

    async def go():
        async def emit(event: dict) -> None:
            events.append(event)

        return await RecoveryAgent(step_budget=12, atlas=atlas).run(
            "trip-test", emit
        )

    return asyncio.run(go()), events


def test_recovery_pipe_with_stubbed_search():
    """Real search stubbed: the pipe completes in the HONEST intermediate
    state — a top candidate, no fabricated rejection, no canned booking."""
    stub = StubAtlas()
    result, events = _run_agent(stub)

    # The demo broken leg is what gets searched.
    assert stub.calls == [("SIN", "NRT", date(2026, 9, 4), 1)]

    # Pending, not recovered: rules (Slice 3) and booking (Slice 5) haven't
    # run, so nothing is rejected and no ticket is asserted (Guard #3).
    assert result.status == "pending"
    assert result.chosen is not None and result.chosen.id == "opt-sgn"
    assert result.chosen.price == Decimal("236")
    assert result.rejected_cheapest is None, "no rejection without the rules engine"
    assert result.order is None, "never assert a booking that didn't happen"
    assert result.rationale is None, "narration is Slice 4's real Qwen output"

    # Screen 3 reads country/city from the wire now (no hardcoded map).
    assert result.layovers, "result must carry layovers for Screen 3"
    sgn = next(lo for lo in result.layovers if lo.airport == "SGN")
    assert sgn.country == "VN" and sgn.city == "Ho Chi Minh City"

    # The emitted event sequence drives Screen 2 + Screen 3.
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert types.count("step") == result.step_count == 6
    assert "options" in types and "decision" in types and types[-1] == "result"

    options = next(e for e in events if e["type"] == "options")
    assert len(options["assessments"]) == 3
    for a in options["assessments"]:
        assert a["layovers"] and a["verdicts"]
        assert a["layovers"][0]["country"] != "??"  # real CSV map, not DEMO_IATA
    decision = next(e for e in events if e["type"] == "decision")
    assert decision["chosen_offer_id"] == "opt-sgn"
    assert decision["rationale"], "decision carries an honest placeholder note"


def test_no_results_gives_up_cleanly():
    """SEARCH_NO_RESULTS (route_not_supported/no_flight/sold_out) must end
    in a clean state — never a crash."""
    result, events = _run_agent(StubAtlas(exc=AtlasNoResults("sold_out")))
    assert result.status == "no_legal_option"
    assert result.chosen is None and result.order is None
    types = [e["type"] for e in events]
    assert types[-1] == "result" and "sold_out" in events[-2]["text"]


def test_search_failure_fails_cleanly():
    result, events = _run_agent(StubAtlas(exc=AtlasError("AUTHORIZATION_REQUIRED")))
    assert result.status == "failed"
    assert [e["type"] for e in events][-1] == "result"


def test_disruption_then_recovery_endpoint(stub_agent):
    """POST /api/disruptions returns a trip id and recovery completes."""
    with TestClient(app) as client:
        resp = client.post("/api/disruptions")
        assert resp.status_code == 200
        trip_id = resp.json()["trip_id"]
        assert trip_id.startswith("trip-")

        final = client.get(f"/api/trips/{trip_id}/recovery")
        assert final.status_code == 200
        body = final.json()
        assert body["status"] == "pending"
        assert body["chosen"]["id"] == "opt-sgn"
        assert body["order"] is None and body["rejected_cheapest"] is None
        assert body["layovers"], "Screen 3 needs layovers on the wire"


def test_stream_endpoint_emits_full_sequence(stub_agent):
    """GET /api/trips/{id}/stream yields the full event sequence."""
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
    assert "options" in types and "decision" in types
    options = next(e for e in seen if e["type"] == "options")
    assert len(options["assessments"]) == 3
    assert all("layovers" in a and "verdicts" in a for a in options["assessments"])
