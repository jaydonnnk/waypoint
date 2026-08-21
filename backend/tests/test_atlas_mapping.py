"""Slice-2 mapping tests — deterministic, no live calls.

The core guarantee: a NormalizedOffer -> Offer mapping that drops a
connecting airport is a bug (the whole product hangs off layover
airports — see ADR 0002). Feeds fixture JSON shaped exactly like the
CLI's public normalized offers (verified against the live Slice-2 probe).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.atlas.client import (
    AtlasClient,
    AtlasError,
    AtlasNoResults,
    map_offer,
    parse_atlas_time,
)
from app.data.loaders import load_iata_city, load_iata_country


def _raw_offer(segments: list[dict], **overrides) -> dict:
    """Shape of one CLI `data.offers[]` entry (normalized public form)."""
    raw = {
        "offer_id": "off_fixture0001",
        "currency": "USD",
        "total_price": 512.75,
        "transaction_fee_total": 2.0,
        "passenger_prices": [],
        "segments": segments,
        "ancillary_supported": [],
        "bookable": False,
        "price_status": "reference",
        "refresh_time": None,
        "expire_time": None,
    }
    raw.update(overrides)
    return raw


# Three legs => two connecting airports (ICN and NRT-side layover at KIX).
# Times are the CONFIRMED upstream format (compact YYYYMMDDHHMM) and cross
# midnight, so layover/total math must use full dates, not clock times.
THREE_SEG = [
    {
        "departure_airport": "SIN",
        "arrival_airport": "ICN",
        "departure_time": "202609042300",
        "arrival_time": "202609050645",
        "carrier": "KE",
        "operating_carrier": None,
        "flight_number": "KE642",
        "duration_minutes": 405,
        "cabin_class": None,
        "direction": "outbound",
    },
    {
        "departure_airport": "ICN",
        "arrival_airport": "KIX",
        "departure_time": "202609050915",
        "arrival_time": "202609051055",
        "carrier": "KE",
        "operating_carrier": None,
        "flight_number": "KE723",
        "duration_minutes": 100,
        "cabin_class": None,
        "direction": "outbound",
    },
    {
        "departure_airport": "KIX",
        "arrival_airport": "NRT",
        "departure_time": "202609051525",
        "arrival_time": "202609051645",
        "carrier": "KE",
        "operating_carrier": None,
        "flight_number": "KE711",
        "duration_minutes": 80,
        "cabin_class": None,
        "direction": "outbound",
    },
]


def test_offer_mapping_preserves_all_layover_airports():
    """3 segments in -> 3 segments out, BOTH connecting airports kept,
    with correct countries, cities, and layover hours."""
    offer = map_offer(_raw_offer(THREE_SEG))

    # Every segment survives the mapping.
    assert [s.flight_number for s in offer.segments] == ["KE642", "KE723", "KE711"]
    assert [(s.dep_airport, s.arr_airport) for s in offer.segments] == [
        ("SIN", "ICN"),
        ("ICN", "KIX"),
        ("KIX", "NRT"),
    ]

    # Layover hours use full datetimes (06:45 -> 09:15 = 2.5h;
    # 10:55 -> 15:25 = 4.5h) — clock-only math would break here.
    layovers = offer.layovers(load_iata_country(), load_iata_city())
    assert [(lo.airport, lo.hours) for lo in layovers] == [
        ("ICN", 2.5),
        ("KIX", 4.5),
    ]
    # Country + city ride on the wire now (no frontend hardcoding).
    assert [(lo.country, lo.city) for lo in layovers] == [
        ("KR", "Seoul"),
        ("JP", "Osaka"),
    ]
    # All-same-carrier itinerary reads as one ticket (secondary hint only).
    assert offer.same_ticket is True
    assert all(lo.same_ticket for lo in layovers)

    # Price/status/ids carried faithfully.
    assert offer.id == "opt-off_fixture0001"
    assert offer.atlas_offer_id == "off_fixture0001"
    assert offer.price == Decimal("512.75")
    assert offer.currency == "USD"
    assert offer.price_status == "reference"
    assert offer.bookable is False

    # total_minutes = first departure -> last arrival (17h45m).
    assert offer.total_minutes == (17 * 60) + 45


def test_mapping_marks_mixed_carriers_as_self_transfer():
    segs = [
        dict(THREE_SEG[0]),  # KE
        dict(THREE_SEG[1], carrier="VJ", flight_number="VJ888"),
        dict(THREE_SEG[2]),  # KE
    ]
    assert map_offer(_raw_offer(segs)).same_ticket is False


def test_mapping_promotes_bookable_current_flags():
    offer = map_offer(
        _raw_offer(THREE_SEG, bookable=True, price_status="current")
    )
    assert offer.bookable is True
    assert offer.price_status == "current"


def test_mapping_sanitizes_unknown_price_status():
    offer = map_offer(_raw_offer(THREE_SEG, price_status="weird"))
    assert offer.price_status == "reference"


def test_mapping_rejects_empty_segments():
    with pytest.raises(ValueError):
        map_offer(_raw_offer([]))


# --- datetime parser: confirmed format + tolerance + hard failure -------

def test_parse_confirmed_compact_format():
    assert parse_atlas_time("202609042300") == datetime(2026, 9, 4, 23, 0)
    assert parse_atlas_time("202609050645") == datetime(2026, 9, 5, 6, 45)


def test_parse_tolerates_other_plausible_formats():
    assert parse_atlas_time("2026-09-04 23:00:00") == datetime(2026, 9, 4, 23, 0)
    assert parse_atlas_time("2026-09-04T23:00") == datetime(2026, 9, 4, 23, 0)
    assert parse_atlas_time(1788627600000) == datetime.fromtimestamp(1788627600)


def test_parse_raises_on_garbage():
    for bad in ("", None, "04/09/2026 11pm", "2026-13-99"):
        with pytest.raises(ValueError):
            parse_atlas_time(bad)


# --- envelope handling: branch on code, graceful reasons ----------------

def _envelope(code: str, data: dict | None = None) -> dict:
    return {
        "schema_version": "1",
        "status": "success" if code == "FLIGHT_SEARCHED" else "error",
        "code": code,
        "message": "irrelevant — never branched on",
        "data": data or {},
    }


def test_envelope_no_results_carries_reason():
    with pytest.raises(AtlasNoResults) as excinfo:
        AtlasClient._offers_from_envelope(
            _envelope("SEARCH_NO_RESULTS", {"reason": "no_flight"})
        )
    assert excinfo.value.reason == "no_flight"


def test_envelope_error_raises_with_code_only():
    with pytest.raises(AtlasError) as excinfo:
        AtlasClient._offers_from_envelope(_envelope("AUTHORIZATION_REQUIRED"))
    assert excinfo.value.code == "AUTHORIZATION_REQUIRED"


def test_envelope_skips_malformed_offers_and_sorts_by_price():
    cheap = _raw_offer(THREE_SEG, offer_id="off_cheap", total_price=236.0)
    pricey = _raw_offer(THREE_SEG, offer_id="off_pricey", total_price=458.0)
    broken = {"offer_id": "off_broken"}  # missing segments -> skipped
    envelope = _envelope(
        "FLIGHT_SEARCHED", {"offers": [pricey, broken, cheap]}
    )
    offers = AtlasClient._offers_from_envelope(envelope)
    assert [o.atlas_offer_id for o in offers] == ["off_cheap", "off_pricey"]
