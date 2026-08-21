"""S2 write-path unit tests — deterministic, no network, no subprocess.

The CLI envelope is stubbed at `_run_json` (the single transport seam),
mirroring test_atlas_mapping.py's `_envelope` style. Asserts the LOCKED
contract discipline:

- branch on `code`, never `message` (envelopes carry junk messages);
- read-only calls: at most ONE identical retry, only when retryable=true;
- writes (order create / order pay / seat select): NEVER retried,
  even when the envelope says retryable=true;
- ORDER_CREATION_UNKNOWN / PRICE_CHANGED / PAYMENT_STATUS_UNKNOWN ->
  typed query-only signals whose ONLY follow-up is `order status`;
- booked == TICKETED from `order status`, nothing else.
"""
from __future__ import annotations

import pytest

from app.atlas.client import (
    AtlasClient,
    AtlasError,
    AtlasQueryOnly,
    AtlasUnknownOrder,
)
from app.models import OrderRef, VerifyResult


def _envelope(
    code: str,
    data: dict | None = None,
    status: str = "success",
    retryable: bool = False,
) -> dict:
    """One CLI envelope. `message` is junk on purpose — never branched on."""
    return {
        "schema_version": "1",
        "status": status,
        "code": code,
        "message": "irrelevant — never branched on",
        "retryable": retryable,
        "data": data or {},
    }


def _verify_data(**overrides) -> dict:
    data = {
        "booking_id": "bk_fixture0001",
        "price_change": "unchanged",
        "previous_price": 512.75,
        "current_price": 512.75,
        "currency": "USD",
        "seat_supported": True,
        "baggage_supported": False,
        "travelers": [{"traveler_id": "tv_1", "passenger_type": "adult"}],
    }
    data.update(overrides)
    return data


class StubTransport:
    """Replaces AtlasClient._run_json; records every call + stdin."""

    def __init__(self, envelopes: list[dict]):
        assert envelopes, "need at least one envelope"
        self.envelopes = envelopes
        self.calls: list[tuple[list[str], str | None]] = []

    def install(self, monkeypatch) -> "StubTransport":
        def fake(client_self, args, stdin=None, timeout=None):
            self.calls.append((list(args), stdin))
            idx = min(len(self.calls) - 1, len(self.envelopes) - 1)
            return self.envelopes[idx]

        monkeypatch.setattr(AtlasClient, "_run_json", fake)
        return self


# --- verify: parsing + loud failure --------------------------------------


def test_verify_parses_price_change_and_booking_id(monkeypatch):
    stub = StubTransport([_envelope("OFFER_VERIFIED", _verify_data())])
    stub.install(monkeypatch)

    result = AtlasClient().verify("off_x1")

    assert isinstance(result, VerifyResult)
    assert result.booking_id == "bk_fixture0001"
    assert result.price_change == "unchanged"
    assert result.previous_price == pytest.approx(result.current_price)
    assert result.currency == "USD"
    assert result.seat_supported is True
    assert result.baggage_supported is False
    assert result.travelers and result.travelers[0]["traveler_id"] == "tv_1"
    # Exact contract command shape.
    assert stub.calls[0][0] == ["offer", "verify", "--offer-id", "off_x1", "--json"]


def test_verify_missing_booking_id_raises_loudly(monkeypatch):
    stub = StubTransport(
        [_envelope("OFFER_VERIFIED", _verify_data(booking_id=None))]
    )
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().verify("off_x1")
    assert excinfo.value.code == "MISSING_BOOKING_ID"


def test_verify_unknown_price_change_raises(monkeypatch):
    stub = StubTransport(
        [_envelope("OFFER_VERIFIED", _verify_data(price_change="sideways"))]
    )
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().verify("off_x1")
    assert excinfo.value.code == "BAD_ENVELOPE"


# --- read-only retry policy (one place: _run_read_only) -------------------


def test_read_only_retry_exactly_once_then_success(monkeypatch):
    stub = StubTransport([
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
        _envelope("OFFER_VERIFIED", _verify_data()),
    ])
    stub.install(monkeypatch)

    result = AtlasClient().verify("off_x1")

    assert result.booking_id == "bk_fixture0001"
    assert len(stub.calls) == 2
    # Identical retry: the second command is byte-for-byte the first.
    assert stub.calls[0] == stub.calls[1]


def test_read_only_retry_stops_after_one(monkeypatch):
    stub = StubTransport([
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
    ])
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().order_status("ord_1")
    assert excinfo.value.code == "SERVICE_TEMPORARILY_UNAVAILABLE"
    assert len(stub.calls) == 2  # never a third call


def test_read_only_no_retry_without_retryable_flag(monkeypatch):
    stub = StubTransport(
        [_envelope("ORDER_NOT_FOUND", status="error", retryable=False)]
    )
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().order_status("ord_1")
    assert excinfo.value.code == "ORDER_NOT_FOUND"
    assert len(stub.calls) == 1


# --- writes are NEVER retried ---------------------------------------------

_PAX_JSON = '{"passengers": [], "contact": {"name": "TEST/SANDBOX"}}'


def test_create_order_never_retried_even_when_retryable(monkeypatch):
    stub = StubTransport([
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
    ])
    stub.install(monkeypatch)

    with pytest.raises(AtlasError):
        AtlasClient().create_order("bk_1", _PAX_JSON)
    assert len(stub.calls) == 1  # a write: exactly one attempt, ever


def test_pay_never_retried_even_when_retryable(monkeypatch):
    stub = StubTransport([
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
    ])
    stub.install(monkeypatch)

    with pytest.raises(AtlasError):
        AtlasClient().pay("pc_1")
    assert len(stub.calls) == 1


def test_seat_select_never_retried_even_when_retryable(monkeypatch):
    stub = StubTransport([
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
    ])
    stub.install(monkeypatch)

    with pytest.raises(AtlasError):
        AtlasClient().seat_select("bk_1", "tv_1", "seg_1", "seat_1")
    assert len(stub.calls) == 1


# --- order create: success + command shape ---------------------------------


def test_create_order_returns_order_ref(monkeypatch):
    stub = StubTransport([
        _envelope("PAYMENT_CONFIRMATION_REQUIRED", {
            "payment_confirmation_id": "pc_77",
            "order_no": "ord_77",
            "order_url": "https://example.invalid/order/ord_77",
        }),
    ])
    stub.install(monkeypatch)

    ref = AtlasClient().create_order(
        "bk_1", _PAX_JSON, seat_policy="continue-without-seat"
    )

    assert isinstance(ref, OrderRef)
    assert ref.payment_confirmation_id == "pc_77"
    assert ref.order_no == "ord_77"
    args, stdin = stub.calls[0]
    # stdin one-time delivery; seat-policy BEFORE --json (cli-contract).
    assert stdin == _PAX_JSON
    assert args == [
        "order", "create", "--booking-id", "bk_1", "--passengers-stdin",
        "--seat-policy", "continue-without-seat", "--json",
    ]


def test_create_order_rejects_unknown_seat_policy(monkeypatch):
    stub = StubTransport([_envelope("PAYMENT_CONFIRMATION_REQUIRED")])
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().create_order("bk_1", _PAX_JSON, seat_policy="wing-it")
    assert excinfo.value.code == "INVALID_ARGUMENT"
    assert stub.calls == []  # never reaches the wire


def test_create_order_unknown_raises_typed_signal_and_query_only_follow_up(
    monkeypatch,
):
    """ORDER_CREATION_UNKNOWN -> AtlasUnknownOrder carrying order_no; the
    documented recovery is ONE `order status` read — no second create."""
    stub = StubTransport([
        _envelope("ORDER_CREATION_UNKNOWN", {"order_no": "ord_maybe"}, status="error"),
        _envelope("TICKETING_PENDING", {"order_no": "ord_maybe"}),
    ])
    stub.install(monkeypatch)
    client = AtlasClient()

    with pytest.raises(AtlasUnknownOrder) as excinfo:
        client.create_order("bk_1", _PAX_JSON)
    assert excinfo.value.code == "ORDER_CREATION_UNKNOWN"
    assert excinfo.value.order_no == "ord_maybe"

    # The ONLY legal follow-up: order status via the recovery helper.
    status = client.follow_up_query_only(excinfo.value)
    assert status.code == "TICKETING_PENDING"
    assert status.ticketed is False  # pending = continuing, not failure

    create_calls = [c for c in stub.calls if c[0][:2] == ["order", "create"]]
    status_calls = [c for c in stub.calls if c[0][:2] == ["order", "status"]]
    assert len(create_calls) == 1  # never a second create
    assert len(status_calls) == 1
    assert status_calls[0][0] == [
        "order", "status", "--order-no", "ord_maybe", "--json",
    ]


def test_duplicate_booking_suspected_raises_same_typed_signal(monkeypatch):
    stub = StubTransport([
        _envelope("DUPLICATE_BOOKING_SUSPECTED", {"order_no": "ord_d"}, status="error"),
    ])
    stub.install(monkeypatch)

    with pytest.raises(AtlasUnknownOrder) as excinfo:
        AtlasClient().create_order("bk_1", _PAX_JSON)
    assert excinfo.value.order_no == "ord_d"
    assert len(stub.calls) == 1


def test_price_changed_on_create_is_query_only_never_recreate(monkeypatch):
    stub = StubTransport([
        _envelope("PRICE_CHANGED", {"order_no": "ord_p"}, status="error"),
    ])
    stub.install(monkeypatch)

    with pytest.raises(AtlasQueryOnly) as excinfo:
        AtlasClient().create_order("bk_1", _PAX_JSON)
    assert excinfo.value.code == "PRICE_CHANGED"
    assert excinfo.value.order_no == "ord_p"
    assert len(stub.calls) == 1  # no second create, ever


# --- pay: outcome branching -------------------------------------------------


def test_pay_branches_ticketed(monkeypatch):
    stub = StubTransport([_envelope("TICKETED", {"order_no": "ord_9"})])
    stub.install(monkeypatch)

    result = AtlasClient().pay("pc_9")

    assert result.ticketed is True
    assert result.query_only is False
    assert result.order_no == "ord_9"
    assert stub.calls[0][0] == ["order", "pay", "--confirmation-id", "pc_9", "--json"]


def test_pay_branches_ticketing_pending(monkeypatch):
    stub = StubTransport([_envelope("TICKETING_PENDING", {"order_no": "ord_9"})])
    stub.install(monkeypatch)

    result = AtlasClient().pay("pc_9")

    assert result.ticketed is False  # pending != booked
    assert result.pending_ticketing is True
    assert result.query_only is False


def test_pay_branches_balance_check_and_unknown_as_query_only(monkeypatch):
    for code in (
        "PAYMENT_BALANCE_CHECK_REQUIRED",
        "PAYMENT_STATUS_UNKNOWN",
        "PAYMENT_PROCESSING",
    ):
        stub = StubTransport([_envelope(code, {"order_no": "ord_b"}, status="error")])
        stub.install(monkeypatch)

        result = AtlasClient().pay("pc_b")

        assert result.ticketed is False
        assert result.query_only is True  # follow-up: order status ONLY
        assert len(stub.calls) == 1  # never pay twice


def test_pay_terminal_error_raises_code_only(monkeypatch):
    stub = StubTransport(
        [_envelope("PAYMENT_DEADLINE_EXPIRED", status="error")]
    )
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().pay("pc_x")
    assert excinfo.value.code == "PAYMENT_DEADLINE_EXPIRED"


# --- order status + poll ----------------------------------------------------


def test_order_status_pending_is_continuing_not_failure(monkeypatch):
    stub = StubTransport([_envelope("TICKETING_PENDING", {"order_no": "ord_1"})])
    stub.install(monkeypatch)

    status = AtlasClient().order_status("ord_1")

    assert status.ticketed is False  # no exception, no booked flag


def test_order_status_ticketed_sets_ticket_asserted(monkeypatch):
    stub = StubTransport([_envelope("TICKETED", {"order_no": "ord_1"})])
    stub.install(monkeypatch)

    assert AtlasClient().order_status("ord_1").ticketed is True


def test_poll_until_ticketed_stops_at_ticketed(monkeypatch):
    stub = StubTransport([
        _envelope("TICKETING_PENDING", {"order_no": "ord_1"}),
        _envelope("TICKETING_PENDING", {"order_no": "ord_1"}),
        _envelope("TICKETED", {"order_no": "ord_1"}),
    ])
    stub.install(monkeypatch)
    monkeypatch.setattr("app.atlas.client.time.sleep", lambda _s: None)

    status, ticket_asserted = AtlasClient().poll_until_ticketed("ord_1")

    assert ticket_asserted is True
    assert status.code == "TICKETED"
    assert len(stub.calls) == 3


def test_poll_deadline_expires_without_ticket_assertion(monkeypatch):
    stub = StubTransport([_envelope("TICKETING_PENDING", {"order_no": "ord_1"})])
    stub.install(monkeypatch)

    status, ticket_asserted = AtlasClient().poll_until_ticketed(
        "ord_1", deadline=0.0
    )

    assert ticket_asserted is False  # never booked on a pending response
    assert status.code == "TICKETING_PENDING"
    assert len(stub.calls) == 1


# --- confirm-price (conditional step) ---------------------------------------


def test_confirm_price_accepts_price_confirmed(monkeypatch):
    stub = StubTransport([_envelope("PRICE_CONFIRMED")])
    stub.install(monkeypatch)

    AtlasClient().confirm_price("bk_1")  # no raise

    assert stub.calls[0][0] == [
        "booking", "confirm-price", "--booking-id", "bk_1", "--json",
    ]


def test_confirm_price_raises_on_failure_code(monkeypatch):
    stub = StubTransport(
        [_envelope("BOOKING_EXPIRED", status="error", retryable=False)]
    )
    stub.install(monkeypatch)

    with pytest.raises(AtlasError) as excinfo:
        AtlasClient().confirm_price("bk_1")
    assert excinfo.value.code == "BOOKING_EXPIRED"


# --- seats: degrade, never block --------------------------------------------


def test_seat_select_unavailable_degrades_to_ledger_only_signal(monkeypatch):
    stub = StubTransport([
        _envelope("SEAT_UNAVAILABLE", status="error", retryable=True),
    ])
    stub.install(monkeypatch)

    result = AtlasClient().seat_select("bk_1", "tv_1", "seg_1", "seat_1")

    assert result.available is False  # alloc degrades; main flow continues
    assert result.code == "SEAT_UNAVAILABLE"
    assert len(stub.calls) == 1  # write: no retry even when retryable=true


def test_seat_select_success(monkeypatch):
    stub = StubTransport([_envelope("SEAT_SELECTED")])
    stub.install(monkeypatch)

    assert AtlasClient().seat_select("bk_1", "tv_1", "seg_1", "seat_1").available


def test_seat_list_is_read_only_and_returns_opaque_options(monkeypatch):
    stub = StubTransport([
        _envelope("SERVICE_TEMPORARILY_UNAVAILABLE", status="error", retryable=True),
        _envelope("SEATS_LISTED", {"seats": [{"seat_id": "s_1"}]}),
    ])
    stub.install(monkeypatch)

    seats = AtlasClient().seat_list("bk_1")

    assert seats == [{"seat_id": "s_1"}]
    assert len(stub.calls) == 2  # read-only retry allowed here


# --- comparison-mode probe ----------------------------------------------------


def test_auth_status_parses_ticketing_flags(monkeypatch):
    stub = StubTransport([_envelope("AUTHORIZED", {
        "authenticated": True,
        "search_available": True,
        "ticketing_available": False,
        "ticketing_blocker": "TICKETING_ACTIVATION_REQUIRED",
    })])
    stub.install(monkeypatch)

    auth = AtlasClient().auth_status()

    assert auth.authorized is True
    assert auth.search_available is True
    assert auth.ticketing_available is False
    assert auth.ticketing_blocker == "TICKETING_ACTIVATION_REQUIRED"


def test_ticketing_live_is_cached_and_fail_closed(monkeypatch):
    stub = StubTransport([_envelope("AUTHORIZED", {
        "authenticated": True,
        "search_available": True,
        "ticketing_available": True,
    })])
    stub.install(monkeypatch)
    client = AtlasClient()

    assert client.ticketing_live() is True
    assert client.ticketing_live() is True
    assert len(stub.calls) == 1  # ONE subprocess per process/cycle


def test_ticketing_live_fail_closed_on_error(monkeypatch):
    def boom(client, args, stdin=None, timeout=None):
        raise AtlasError("TIMEOUT")

    monkeypatch.setattr(AtlasClient, "_run_json", boom)

    assert AtlasClient().ticketing_live() is False
