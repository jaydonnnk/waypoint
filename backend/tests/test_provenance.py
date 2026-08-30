"""S12 per-rail provenance — build_rails matrix + honesty contracts.

In-gate (non-live). The pure matrix (live/recorded/comparison × qwen
live/fallback), the fail-to-least-live rule ("cannot claim Atlas by
omission"), the absolute honesty rule (recorded NEVER emits an Atlas rail
labelled live — asserted against BOTH a stub and the real
RecordedAtlasClient with the real composite manifest), and the loop
wiring (a full recorded cycle's meta event carries the four rails).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.brain import SOURCE_AGENT, SOURCE_FALLBACK, DeskBrain
from app.agent.loop import DeskAgent
from app.atlas.recorded import RecordedAtlasClient
from app.db import database
from app.db.store import DeskStore
from app.fixture import VOLATILITY_PRIORS
from app.models import Budget, Mandate, Position
from app.provenance import build_rails

SEED_AT = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)


# --- stubs ------------------------------------------------------------------


class _StubAtlas:
    """Bare live-sandbox stand-in: no mode_label attribute at all."""


class _StubRecorded:
    mode_label = "recorded"

    def __init__(self, manifest: dict | None):
        if manifest is not None:
            self.manifest = manifest


class _StubBrain:
    def __init__(self, last_source: str | None):
        self.last_source = last_source


def rail_by_name(rails: list[dict], name: str) -> dict:
    matches = [r for r in rails if r["rail"] == name]
    assert len(matches) == 1, f"expected exactly one {name} rail"
    return matches[0]


# --- the pure matrix: 3 atlas modes × 2 qwen sources ------------------------


@pytest.mark.parametrize(
    "brain_source", [SOURCE_AGENT, SOURCE_FALLBACK, None]
)
def test_matrix_live_sandbox(brain_source):
    """Armed gates + a client with no recorded label → live sandbox."""
    rails = build_rails(
        atlas=_StubAtlas(),
        brain=_StubBrain(brain_source),
        comparison=False,
        live_ticketing=True,
    )
    assert [r["rail"] for r in rails] == ["Atlas", "Qwen", "Priors", "Ledger"]
    atlas = rail_by_name(rails, "Atlas")
    assert atlas["state"] == "live"
    assert atlas["label"] == "live sandbox"
    qwen = rail_by_name(rails, "Qwen")
    if brain_source == SOURCE_AGENT:
        assert qwen["state"] == "live"
        assert qwen["label"] == "live model"
    else:
        # fallback AND no-judgment-yet both read as the fallback rail
        assert qwen["state"] == "fallback"
        assert qwen["label"] == "deterministic fallback"


@pytest.mark.parametrize(
    "brain_source", [SOURCE_AGENT, SOURCE_FALLBACK, None]
)
def test_matrix_recorded_replay(brain_source):
    rails = build_rails(
        atlas=_StubRecorded(
            {"composite": True, "ticketed_captured": False}
        ),
        brain=_StubBrain(brain_source),
        comparison=False,
        live_ticketing=True,  # the recorded probe wins over the live signal
    )
    atlas = rail_by_name(rails, "Atlas")
    assert atlas["state"] == "recorded"
    assert atlas["label"] == "recorded replay"


@pytest.mark.parametrize(
    "brain_source", [SOURCE_AGENT, SOURCE_FALLBACK, None]
)
def test_matrix_comparison_only(brain_source):
    """comparison takes priority — no write commands run regardless of
    where envelopes come from (same ordering as the loop's wire label)."""
    rails = build_rails(
        atlas=_StubRecorded(
            {"composite": True, "ticketed_captured": False}
        ),
        brain=_StubBrain(brain_source),
        comparison=True,
        live_ticketing=False,
    )
    atlas = rail_by_name(rails, "Atlas")
    assert atlas["state"] == "comparison"
    assert atlas["label"] == "comparison-only"


def test_priors_and_ledger_never_vary():
    for comparison in (True, False):
        rails = build_rails(
            atlas=_StubAtlas(),
            brain=_StubBrain(SOURCE_AGENT),
            comparison=comparison,
            live_ticketing=not comparison,
        )
        priors = rail_by_name(rails, "Priors")
        assert priors["state"] == "curated"
        assert "no ML" in priors["label"]
        ledger = rail_by_name(rails, "Ledger")
        assert ledger["state"] == "real"
        assert "code-computed" in ledger["label"]


# --- fail-to-least-live: cannot claim Atlas by omission ---------------------


def test_bare_call_is_the_least_live_set():
    rails = build_rails()
    assert rail_by_name(rails, "Atlas")["state"] == "comparison"
    assert rail_by_name(rails, "Qwen")["state"] == "fallback"


def test_missing_client_never_claims_live():
    """live_ticketing=True but no client → unknown, never a live claim."""
    rails = build_rails(atlas=None, comparison=False, live_ticketing=True)
    atlas = rail_by_name(rails, "Atlas")
    assert atlas["state"] == "unknown"
    assert "live" not in atlas["label"]


def test_missing_mode_signal_never_claims_live():
    """A client that neither identifies as recorded nor rides an explicit
    live signal → unknown."""
    rails = build_rails(atlas=_StubAtlas(), comparison=False)
    assert rail_by_name(rails, "Atlas")["state"] == "unknown"


def test_brain_without_judgment_reads_fallback():
    rails = build_rails(brain=None)
    qwen = rail_by_name(rails, "Qwen")
    assert qwen["state"] == "fallback"
    # A real DeskBrain that has never judged reads the same.
    rails = build_rails(brain=DeskBrain())
    assert rail_by_name(rails, "Qwen")["state"] == "fallback"


# --- recorded NEVER wears a live label ---------------------------------------


@pytest.mark.parametrize(
    "brain_source", [SOURCE_AGENT, SOURCE_FALLBACK, None]
)
@pytest.mark.parametrize("comparison", [True, False])
def test_recorded_atlas_rail_is_never_labelled_live(brain_source, comparison):
    rails = build_rails(
        atlas=_StubRecorded(
            {"composite": True, "ticketed_captured": False}
        ),
        brain=_StubBrain(brain_source),
        comparison=comparison,
        live_ticketing=not comparison,
    )
    atlas = rail_by_name(rails, "Atlas")
    assert "live" not in atlas["label"].lower()
    assert atlas["state"] != "live"


def test_real_recorded_client_never_labels_atlas_live():
    """The REAL RecordedAtlasClient + REAL manifest: the Atlas rail says
    recorded replay and its detail surfaces the genuine-ticketed honesty."""
    client = RecordedAtlasClient()
    for comparison in (True, False):
        rails = build_rails(
            atlas=client,
            comparison=comparison,
            live_ticketing=not comparison,
        )
        atlas = rail_by_name(rails, "Atlas")
        if comparison:
            assert atlas["state"] == "comparison"
            continue
        assert atlas["state"] == "recorded"
        assert "live" not in atlas["label"].lower()
        # Genuine TICKETED honesty from the manifest, verbatim.
        assert "genuinely captured" in atlas["detail"]
        assert "composite" not in atlas["detail"]


def test_recorded_without_manifest_degrades_least_live():
    rails = build_rails(
        atlas=_StubRecorded(None),
        comparison=False,
        live_ticketing=True,
    )
    atlas = rail_by_name(rails, "Atlas")
    assert atlas["state"] == "recorded"
    assert "unverified" in atlas["detail"]
    assert "live" not in atlas["label"].lower()


def test_ticketed_captured_manifest_says_so():
    rails = build_rails(
        atlas=_StubRecorded(
            {"composite": False, "ticketed_captured": True}
        ),
        comparison=False,
        live_ticketing=True,
    )
    atlas = rail_by_name(rails, "Atlas")
    assert "genuinely captured" in atlas["detail"]
    assert "composite" not in atlas["detail"]


# --- DeskBrain.last_source (the Qwen rail's source of truth) -----------------


def _make_position() -> Position:
    return Position(
        id="desk-t-pos-1", trip_label="test leg", origin="AAA", dest="BBB",
        depart_date=SEED_AT.date(), pax=1, status="held",
        cost_basis=Decimal("100.00"), mark_price=Decimal("120.00"),
        mark_at=SEED_AT, mark_stale=False,
    )


def _judge(brain: DeskBrain) -> None:
    asyncio.run(brain.judge(
        [_make_position()], VOLATILITY_PRIORS,
        meter_left=20, budget_left=Decimal("12000"),
        contingency_left=Decimal("600"),
    ))


def test_last_source_agent_on_valid_live_judgment():
    async def transport(messages):
        return json.dumps([
            {"position_id": "desk-t-pos-1", "kind": "hold",
             "rationale": "stub live judgment"}
        ])

    brain = DeskBrain(transport=transport)
    assert brain.last_source is None  # no judgment yet → least-live read
    _judge(brain)
    assert brain.last_source == SOURCE_AGENT


def test_last_source_fallback_on_transport_failure():
    async def transport(messages):
        raise RuntimeError("LLM unavailable (stub)")

    brain = DeskBrain(transport=transport)
    _judge(brain)
    assert brain.last_source == SOURCE_FALLBACK


def test_last_source_fallback_on_hostile_shape():
    """Valid call but _validate rejects the shape → fallback source."""
    async def transport(messages):
        return json.dumps([
            {"position_id": "desk-t-INVENTED", "kind": "book",
             "rationale": "invented id"}
        ])

    brain = DeskBrain(transport=transport)
    _judge(brain)
    assert brain.last_source == SOURCE_FALLBACK


def test_last_source_fallback_without_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    brain = DeskBrain()
    _judge(brain)
    assert brain.last_source == SOURCE_FALLBACK


# --- loop wiring: the meta event carries the four rails ----------------------


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_provenance.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False),
    )
    database.Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def recorded_env(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "1")
    monkeypatch.setenv("WAYPOINT_ATLAS_MODE", "recorded")


def _seed_single_position_desk() -> str:
    desk_id = f"desk-{uuid4().hex[:8]}"
    mandate = Mandate(
        id=desk_id, holder="provenance", created_at=SEED_AT,
        budget_total=Decimal("12000.00"), authority_cap=Decimal("1500.00"),
        contingency_pct=0.05, currency="USD",
    )
    positions = [Position(
        id=f"{desk_id}-pos-1", trip_label="SIN\u2192NRT replay leg",
        origin="SIN", dest="NRT", depart_date=datetime(2026, 9, 4).date(),
        pax=2, status="held", cost_basis=Decimal("270.00"),
        mark_price=Decimal("350.00"), mark_at=SEED_AT, mark_stale=False,
    )]
    budgets = [Budget(
        desk_id=desk_id, period="2026-W38",
        allocated=Decimal("12000.00"), contingency=Decimal("600.00"),
    )]
    DeskStore().seed_desk(mandate, positions, budgets)
    return desk_id


def _run_cycle(agent, desk_id):
    events: list[dict] = []

    async def emit(event):
        events.append(event)

    result = asyncio.run(agent.run(desk_id, emit))
    return result, events


def test_meta_event_carries_honest_rails_in_recorded_mode(
    tmp_db, recorded_env
):
    """Full recorded cycle: the meta event carries all four rails, the
    Atlas rail says recorded replay (never live) with the genuine-ticketed
    honesty, and the fallback brain (no key) reads fallback. mode and
    disclosures stay as S9 pinned them."""
    atlas = RecordedAtlasClient()
    agent = DeskAgent(step_budget=12, atlas=atlas, store=DeskStore(), pace=0)
    _, events = _run_cycle(agent, _seed_single_position_desk())

    meta = next(e for e in events if e["type"] == "meta")
    # S9's wire contract, byte-identical alongside the new field.
    assert meta["mode"] == "recorded ticketing (replay)"
    assert any("recorded Atlas replay" in d for d in meta["disclosures"])

    rails = meta["rails"]
    assert [r["rail"] for r in rails] == ["Atlas", "Qwen", "Priors", "Ledger"]
    atlas_rail = rail_by_name(rails, "Atlas")
    assert atlas_rail["state"] == "recorded"
    assert "live" not in atlas_rail["label"].lower()
    assert "genuinely captured" in atlas_rail["detail"]
    assert "composite" not in atlas_rail["detail"]
    # meta rides BEFORE the first judgment → the Qwen rail reads the
    # least-live label (fallback), exactly the fail-to-least-live rule.
    qwen_rail = rail_by_name(rails, "Qwen")
    assert qwen_rail["state"] == "fallback"
    assert rail_by_name(rails, "Priors")["state"] == "curated"
    assert rail_by_name(rails, "Ledger")["state"] == "real"


def test_meta_event_rails_comparison_when_disarmed(
    tmp_db, recorded_env, monkeypatch
):
    """Human switch disarmed → the Atlas rail says comparison-only even
    though the recorded client is wired (priority mirrors the wire label)."""
    monkeypatch.setenv("WAYPOINT_LIVE_BOOKING", "0")
    atlas = RecordedAtlasClient()
    agent = DeskAgent(step_budget=12, atlas=atlas, store=DeskStore(), pace=0)
    _, events = _run_cycle(agent, _seed_single_position_desk())

    meta = next(e for e in events if e["type"] == "meta")
    assert meta["mode"].startswith("comparison mode")
    atlas_rail = rail_by_name(meta["rails"], "Atlas")
    assert atlas_rail["state"] == "comparison"
    assert atlas_rail["label"] == "comparison-only"


def test_second_cycle_rails_read_fallback_not_previous_agent_source(
    tmp_db, monkeypatch
):
    """Provenance staleness fix — meta emits BEFORE judge(), and
    DeskBrain.last_source persists across cycles, so run() resets it
    after the desk reload. After a FIRST cycle whose judgment genuinely
    came from the (stubbed-live) agent, the SECOND cycle's meta rails
    must still read the fallback label — never a live claim inherited
    from the previous cycle."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("WAYPOINT_LIVE_BOOKING", raising=False)

    class _SearchlessAtlas:
        def search(self, origin, dest, dep, pax):
            return []

    desk_id = _seed_single_position_desk()

    async def transport(messages):
        return json.dumps([
            {"position_id": f"{desk_id}-pos-1", "kind": "hold",
             "rationale": "stub live judgment"}
        ])

    brain = DeskBrain(transport=transport)
    agent = DeskAgent(
        step_budget=12, atlas=_SearchlessAtlas(), brain=brain,
        store=DeskStore(), pace=0,
    )

    _run_cycle(agent, desk_id)
    # Precondition: the first cycle's judgment really set the agent source.
    assert brain.last_source == SOURCE_AGENT

    _, events = _run_cycle(agent, desk_id)
    meta = next(e for e in events if e["type"] == "meta")
    qwen_rail = rail_by_name(meta["rails"], "Qwen")
    assert qwen_rail["state"] == "fallback"  # no stale live claim
    # Non-vacuous: the second judgment ran live again (reset, not freeze).
    assert brain.last_source == SOURCE_AGENT
