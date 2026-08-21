"""Live sandbox smoke test — OPT-IN, excluded from default runs.

Run explicitly with:  pytest -m live
Needs: `atlas-flight` on PATH, keyring auth AUTHORIZED, env = sandbox.
Read path only — one search, no verify/order/pay.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.atlas.client import AtlasClient

pytestmark = pytest.mark.live


def test_live_sandbox_search_returns_offers():
    """The demo broken leg's reroute search returns >= 1 real offer,
    with every segment carrying parseable times and real IATA codes."""
    offers = AtlasClient().search("SIN", "NRT", date(2026, 9, 4), 1)

    assert len(offers) >= 1
    for offer in offers:
        assert offer.segments, "an offer without segments is a mapping bug"
        assert offer.atlas_offer_id
        assert offer.currency
        assert offer.total_minutes > 0
        # price_status/bookable carried faithfully (sandbox may return
        # reference-only offers while ticketing activation is pending).
        assert offer.price_status in ("reference", "current", "verified")
        for seg in offer.segments:
            assert len(seg.dep_airport) == 3 and len(seg.arr_airport) == 3
            assert seg.arr_time >= seg.dep_time
