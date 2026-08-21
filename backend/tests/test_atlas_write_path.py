"""Live write-path proof — OPT-IN behind TWO gates, mirrors
test_atlas_sandbox_live.py:

1. pytest marker `live` (excluded from default runs by pytest.ini);
2. env flag WAYPOINT_WRITE_PATH=1 (explicit human intent).

Even with both gates open, the test SKIPS CLEANLY while the sandbox
reports ticketing_available=false (blocker TICKETING_ACTIVATION_REQUIRED
as of the 2026-08-22 probe). NEVER fabricates tickets: `ticket_asserted`
is True only on a real TICKETED `order status` envelope, and create/pay
are counted to prove each runs exactly once. Codes/counts only — no
personal data is printed (passenger-input.md).
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pytest

from app.atlas.client import AtlasClient, AtlasError

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("WAYPOINT_WRITE_PATH") != "1",
        reason="write-path proof is opt-in: set WAYPOINT_WRITE_PATH=1",
    ),
]

# The ONE UAT route for the live proof (6E AMS->MAA).
ORIGIN, DESTINATION = "AMS", "MAA"


def _build_pax_json(verify_result) -> str:
    """One-time stdin payload from the verify-returned traveler IDs
    (carry, never invent — passenger-input.md). Sandbox test identities
    only; nothing here is printed or logged."""
    travelers = verify_result.travelers or [
        {"traveler_id": "", "passenger_type": "adult"}
    ]
    passengers = [
        {
            "traveler_id": t.get("traveler_id", ""),
            "name": "WAYPOINT/UAT",
            "passenger_type": t.get("passenger_type", "adult"),
            "gender": t.get("gender", "M"),
            "birthday": t.get("birthday", "1990-01-01"),
            "nationality": "NL",
            "document": {
                "type": "PP",
                "number": "UAT000000",
                "issuing_country": "NL",
                "expires": "2031-01-01",
            },
        }
        for t in travelers
    ]
    return json.dumps({
        "passengers": passengers,
        "contact": {"name": "WAYPOINT/UAT"},
    })


def test_live_write_path_tickets():
    """search -> best bookable/current offer -> verify -> [confirm-price
    ONLY if verify reports increased] -> create_order (once) -> pay (once,
    confirmation ID from THAT create) -> poll order status until TICKETED.
    """
    client = AtlasClient()

    # Gate 0: re-check authorization + ticketing. Skip cleanly (with the
    # normalized blocker code, nothing else) while ticketing is blocked.
    auth = client.auth_status()
    if not auth.authorized:
        pytest.skip(f"Atlas not authorized: {auth.code}")
    if not auth.ticketing_available:
        pytest.skip(
            "write path blocked — ticketing unavailable"
            f" (code={auth.code}, blocker={auth.ticketing_blocker or 'none'})"
        )

    # One search on the UAT route (~3 weeks out keeps it bookable).
    depart = date.today() + timedelta(days=21)
    offers = client.search(ORIGIN, DESTINATION, depart, 1)
    current_bookable = [
        o for o in offers
        if o.bookable and o.price_status in ("current", "verified")
    ]
    candidates = current_bookable or [o for o in offers if o.bookable]
    if not candidates:
        pytest.skip(
            f"no bookable offer on {ORIGIN}->{DESTINATION}"
            f" (offers={len(offers)})"
        )
    offer = candidates[0]  # search() is cheapest-first

    verify = client.verify(offer.atlas_offer_id)
    if verify.price_change == "increased":
        client.confirm_price(verify.booking_id)  # conditional step ONLY

    # Count the writes to prove each happens exactly once.
    counts = {"create": 0, "pay": 0}
    real_create, real_pay = client.create_order, client.pay

    def counted_create(*args, **kwargs):
        counts["create"] += 1
        return real_create(*args, **kwargs)

    def counted_pay(*args, **kwargs):
        counts["pay"] += 1
        return real_pay(*args, **kwargs)

    client.create_order, client.pay = counted_create, counted_pay

    # Seats never block the write path: ledger-only degrade is fine here.
    ref = client.create_order(
        verify.booking_id,
        _build_pax_json(verify),
        seat_policy="continue-without-seat",
    )
    result = client.pay(ref.payment_confirmation_id)  # ID from THAT create

    # The outcome assertion: TICKETED from `order status`, nothing else.
    status, ticket_asserted = client.poll_until_ticketed(
        ref.order_no, deadline=90.0
    )

    assert counts == {"create": 1, "pay": 1}, "writes must run exactly once"
    assert result.order_no in (ref.order_no, None)
    assert ticket_asserted is True, f"never ticketed: code={status.code}"
    assert status.code == "TICKETED"


def test_live_seat_select_pre_order():
    """Seats are booking-stage, pre-order: seat_list(booking_id) after
    verify, then seat_select BEFORE `order create` (which carries
    --seat-policy continue-without-seat). Degrade discipline: an EMPTY
    list or SEAT_UNAVAILABLE never blocks the main flow — the alloc just
    lands ledger-only (the loop's disclosed degrade), and codes/counts are
    the only things surfaced. Same double gate + skip checks as the
    write-path proof; skips cleanly today (ticketing blocked)."""
    client = AtlasClient()

    # Gate 0: identical auth/ticketing skip checks as the write proof.
    auth = client.auth_status()
    if not auth.authorized:
        pytest.skip(f"Atlas not authorized: {auth.code}")
    if not auth.ticketing_available:
        pytest.skip(
            "seat proof blocked — ticketing unavailable"
            f" (code={auth.code}, blocker={auth.ticketing_blocker or 'none'})"
        )

    # One search on the UAT route (~3 weeks out keeps it bookable).
    depart = date.today() + timedelta(days=21)
    offers = client.search(ORIGIN, DESTINATION, depart, 1)
    current_bookable = [
        o for o in offers
        if o.bookable and o.price_status in ("current", "verified")
    ]
    candidates = current_bookable or [o for o in offers if o.bookable]
    if not candidates:
        pytest.skip(
            f"no bookable offer on {ORIGIN}->{DESTINATION}"
            f" (offers={len(offers)})"
        )
    offer = candidates[0]

    verify = client.verify(offer.atlas_offer_id)
    if verify.price_change == "increased":
        client.confirm_price(verify.booking_id)  # conditional step ONLY

    # --- seat list BEFORE any order (booking-stage, booking_id-bound).
    degraded = False
    try:
        seats = client.seat_list(verify.booking_id)
    except AtlasError as exc:
        if exc.code == "SEAT_UNAVAILABLE":
            seats, degraded = [], True  # typed degrade, code-branched
        else:
            raise
    if degraded or not seats:
        # The degrade is the alloc: ledger-only, main flow never blocked.
        # (The loop records the disclosed "ledger-only" note.) Nothing
        # further to prove here — the order proof lives above.
        assert seats == []
        return

    # Seats exist: select exactly ONE id from the LATEST list response,
    # bound to the first verify-returned traveler (carry, never invent).
    seat = seats[0]
    traveler_id = (verify.travelers or [{}])[0].get("traveler_id", "")
    segment_id = str(seat.get("segment_id", ""))
    seat_id = str(seat.get("seat_id", ""))
    if not (traveler_id and segment_id and seat_id):
        pytest.skip("seat option lacks traveler/segment/seat ids")

    selection = client.seat_select(
        verify.booking_id, traveler_id, segment_id, seat_id
    )
    if not selection.available:
        # SEAT_UNAVAILABLE on select → same ledger-only degrade; continue.
        assert selection.code == "SEAT_UNAVAILABLE"
        return

    # Seat selected BEFORE the order: create carries the seat policy.
    counts = {"create": 0, "pay": 0}
    real_create, real_pay = client.create_order, client.pay

    def counted_create(*args, **kwargs):
        counts["create"] += 1
        return real_create(*args, **kwargs)

    def counted_pay(*args, **kwargs):
        counts["pay"] += 1
        return real_pay(*args, **kwargs)

    client.create_order, client.pay = counted_create, counted_pay

    ref = client.create_order(
        verify.booking_id,
        _build_pax_json(verify),
        seat_policy="continue-without-seat",
    )
    result = client.pay(ref.payment_confirmation_id)  # ID from THAT create
    status, ticket_asserted = client.poll_until_ticketed(
        ref.order_no, deadline=90.0
    )

    assert counts == {"create": 1, "pay": 1}, "writes must run exactly once"
    assert result.order_no in (ref.order_no, None)
    assert ticket_asserted is True, f"never ticketed: code={status.code}"
    assert status.code == "TICKETED"
