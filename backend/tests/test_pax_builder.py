"""S3 — Pax builder tests.

Each test FAILS against pre-S3 code: app/pax.py does not exist pre-S3.

test_carries_verify_traveler_ids: traveler_id from verify, name/doc from
  stored rows, pax_source=collected.
test_ungated_desk_demo_fallback: legacy/recorded desk (no invite_token)
  → demo envelope, pax_source=demo (byte-safe).
test_gated_desk_missing_travelers_holds: gated desk short a roster
  → PaxBuild.hold=True, wall holds+escalates, NEVER demo identities.
test_distinct_docs_per_pax: no two pax share a doc number.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.store import DeskStore
from app.pax import PaxBuild, build_pax_json


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_pax.db'}",
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
def store():
    return DeskStore()


def _seed_desk(store: DeskStore, *, gated: bool, team_size: int = 2) -> str:
    """Seed a desk, optionally gated."""
    from app import fixture

    mandate, positions, budgets = fixture.seeded_portfolio(team_size=team_size)
    if gated:
        return store.seed_desk(
            mandate, positions, budgets,
            lifecycle="awaiting_travelers",
            invite_token="test-token-pax",
            code_hash="salt$fakehash",
        )
    else:
        return store.seed_desk(mandate, positions, budgets)


def _add_traveler(store: DeskStore, desk_id: str, slot: int, doc_number: str = "E1234567") -> None:
    """Add a verified traveler to the desk."""
    from app.bot.mrz import MrzFields

    fields = MrzFields(
        family_name="TAN",
        given_name="WEILING",
        gender="M",
        birthday="1990-01-01",
        nationality_iso2="SG",
        doc_number=doc_number,
        issuing_country="SG",
        doc_expiry="2030-01-01",
    )
    store.add_traveler(desk_id, slot, fields)


class TestPaxBuilder:
    def test_carries_verify_traveler_ids(self, tmp_db, store):
        """Gated desk with full roster: traveler_id from verify, name/doc
        from stored rows, pax_source='collected'."""
        desk_id = _seed_desk(store, gated=True, team_size=2)
        _add_traveler(store, desk_id, slot=1, doc_number="E1234567")
        _add_traveler(store, desk_id, slot=2, doc_number="E7654321")

        verified_travelers = [
            {"traveler_id": "atlas-t1", "passenger_type": "adult"},
            {"traveler_id": "atlas-t2", "passenger_type": "adult"},
        ]

        result = build_pax_json(desk_id, verified_travelers, store)

        assert not result.hold
        assert result.pax_source == "collected"
        assert result.pax_json is not None

        payload = json.loads(result.pax_json)
        passengers = payload["passengers"]
        assert len(passengers) == 2
        # traveler_id carried from verify, never invented.
        assert passengers[0]["traveler_id"] == "atlas-t1"
        assert passengers[1]["traveler_id"] == "atlas-t2"
        # Name/doc from stored rows.
        assert passengers[0]["name"] == "TAN/WEILING"
        assert passengers[0]["document"]["number"] == "E1234567"
        assert passengers[1]["document"]["number"] == "E7654321"

    def test_ungated_desk_demo_fallback(self, tmp_db, store):
        """Ungated desk (no invite_token) → demo envelope, pax_source='demo'."""
        desk_id = _seed_desk(store, gated=False)

        verified_travelers = [
            {"traveler_id": "atlas-t1", "passenger_type": "adult"},
        ]

        result = build_pax_json(desk_id, verified_travelers, store)

        assert not result.hold
        assert result.pax_source == "demo"
        assert result.pax_json is not None

        payload = json.loads(result.pax_json)
        passengers = payload["passengers"]
        assert passengers[0]["name"].startswith("DEMO/WAYPOINT")
        # Byte-safe: demo doc numbers.
        assert passengers[0]["document"]["number"].startswith("DEMO")

    def test_gated_desk_missing_travelers_holds(self, tmp_db, store):
        """Gated desk with missing/short roster → PaxBuild.hold=True.
        NEVER demo identities for a gated desk."""
        desk_id = _seed_desk(store, gated=True, team_size=2)
        # Only add 1 of 2 travelers.
        _add_traveler(store, desk_id, slot=1, doc_number="E1234567")

        verified_travelers = [
            {"traveler_id": "atlas-t1", "passenger_type": "adult"},
            {"traveler_id": "atlas-t2", "passenger_type": "adult"},
        ]

        result = build_pax_json(desk_id, verified_travelers, store)

        assert result.hold is True
        assert result.pax_json is None
        assert result.pax_source == "collected"

    def test_distinct_docs_per_pax(self, tmp_db, store):
        """No two pax share a doc number — the store rejects duplicates."""
        desk_id = _seed_desk(store, gated=True, team_size=2)
        _add_traveler(store, desk_id, slot=1, doc_number="E1234567")

        # Attempt to add a second traveler with the same doc number → ValueError.
        with pytest.raises(ValueError, match="duplicate doc_number"):
            _add_traveler(store, desk_id, slot=2, doc_number="E1234567")

    def test_builder_holds_on_duplicate_docs(self, tmp_db, store):
        """The builder's OWN distinct-doc guard holds even if two stored
        rows somehow share a doc number (defense in depth, independent of
        the store's insert-time uniqueness)."""
        desk_id = _seed_desk(store, gated=True, team_size=2)

        # Fake store returning duplicate doc numbers to exercise the
        # builder's own guard branch directly.
        class _DupStore:
            def get_invite(self, d):
                return ("test-token", "salt$hash")  # gated

            def list_travelers(self, d):
                return [
                    {"slot": 1, "family_name": "TAN", "given_name": "A",
                     "gender": "M", "birthday": "1990-01-01", "nationality": "SG",
                     "doc_type": "PP", "doc_number": "SAME1", "issuing_country": "SG",
                     "doc_expiry": "2030-01-01", "contact_email": None,
                     "contact_mobile": None},
                    {"slot": 2, "family_name": "LIM", "given_name": "B",
                     "gender": "F", "birthday": "1991-01-01", "nationality": "SG",
                     "doc_type": "PP", "doc_number": "SAME1", "issuing_country": "SG",
                     "doc_expiry": "2030-01-01", "contact_email": None,
                     "contact_mobile": None},
                ]

        result = build_pax_json(
            "any-desk",
            [{"traveler_id": "t1"}, {"traveler_id": "t2"}],
            _DupStore(),
        )
        assert result.hold is True
        assert result.pax_json is None

    def test_gated_empty_verify_holds_never_invents(self, tmp_db, store):
        """Gated desk with EMPTY verify travelers → hold, never a fabricated
        'traveler_id': '' (carry, never invent)."""
        desk_id = _seed_desk(store, gated=True, team_size=1)
        _add_traveler(store, desk_id, slot=1, doc_number="E1234567")

        result = build_pax_json(desk_id, [], store)
        assert result.hold is True
        assert result.pax_json is None


class TestPurgeTravelers:
    """The retention promise: purge_travelers removes all rows for a desk."""

    def test_purge_removes_all(self, tmp_db, store):
        desk_id = _seed_desk(store, gated=True, team_size=2)
        _add_traveler(store, desk_id, slot=1, doc_number="E1111111")
        _add_traveler(store, desk_id, slot=2, doc_number="E2222222")
        assert len(store.list_travelers(desk_id)) == 2

        store.purge_travelers(desk_id)
        assert store.list_travelers(desk_id) == []
        assert store.verified_count(desk_id) == 0
