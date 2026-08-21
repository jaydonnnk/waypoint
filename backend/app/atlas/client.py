"""AtlasClient — the Gate 3 wrapper around the installed atlas-flight skill.

Slice 2 (S2) adds the WRITE path: verify / confirm-price / order create /
order pay / order status / seat list+select, plus the auth-status probe
that drives comparison mode. search() (S1 read path) stays untouched.

Transport: the skill exposes a clean library entrypoint
(`atlas_cli.cli.build_search_service`), but that package requires
Python >= 3.12 while this backend runs 3.11 — so we use the brief's
sanctioned fallback: subprocess `atlas-flight ... --json` and parse the
stdout envelope. The subprocess reuses the INSTALLED tool's stored
OS-keyring auth + sandbox env config; auth is never re-implemented and
no secret ever appears in code, args, or logs (we branch on envelope
`code`, never on `message`, and only surface codes/counts).

Contract discipline (error-handling.md / booking-workflow.md):
- branch on envelope `code`, NEVER `message`;
- read-only calls: at most ONE identical retry, only when the envelope
  says retryable=true — implemented in exactly one place (_run_read_only);
- `order create` / `order pay` / `seat select` are WRITES: never retried,
  even if retryable=true appears;
- PRICE_CHANGED / ORDER_CREATION_UNKNOWN / DUPLICATE_BOOKING_SUSPECTED /
  PAYMENT_STATUS_UNKNOWN raise a typed query-only signal whose ONLY legal
  follow-up is `order status` — never re-create, never re-pay;
- a position is "booked" only after `order status` == TICKETED.

The public signatures are transport-independent — the Gate 3 contract in
03-program-design.md is what stays stable.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from datetime import date, datetime
from decimal import Decimal

from app.models import (
    AuthStatus,
    Offer,
    OrderRef,
    OrderStatus,
    PaymentResult,
    SeatSelection,
    Segment,
    VerifyResult,
)

CLI_TIMEOUT_SECONDS = 60.0
WRITE_TIMEOUT_SECONDS = 90.0  # writes may run longer (order create / pay)

# The ONLY seat policies the CLI contract allows on `order create`
# (cli-contract.md), placed before --json. Never on `seat select`.
SEAT_POLICIES = ("continue-without-seat", "cancel-order", "accept-similar-seat")

# Confirmed live (Slice 2 probe, sandbox SIN->NRT): upstream datetimes are
# compact `YYYYMMDDHHMM` (12 digits); the API docs' `YYYYMMSS` was a typo.
# The parser stays tolerant of the other plausible shapes so a format
# change upstream degrades to a clear error instead of a silent misparse.
_DATETIME_FORMATS = (
    "%Y%m%d%H%M",
    "%Y%m%d%H%M%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y%m%d",
)


class AtlasError(RuntimeError):
    """Terminal Atlas failure. `code` is the CLI envelope code."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class AtlasNoResults(AtlasError):
    """Search succeeded but found nothing. `reason` is the upstream one
    (route_not_supported / no_flight / sold_out) or None."""

    def __init__(self, reason: str | None):
        super().__init__("SEARCH_NO_RESULTS", reason or "no results")
        self.reason = reason


class AtlasQueryOnly(AtlasError):
    """Typed side-effect-uncertainty signal (PRICE_CHANGED,
    PAYMENT_STATUS_UNKNOWN, PAYMENT_PROCESSING). The caller's ONLY legal
    follow-up is `order status` with `order_no` — never re-create,
    never re-pay (booking-workflow §5 / error-handling)."""

    def __init__(self, code: str, order_no: str | None = None):
        super().__init__(code)
        self.order_no = order_no


class AtlasUnknownOrder(AtlasQueryOnly):
    """ORDER_CREATION_UNKNOWN / DUPLICATE_BOOKING_SUSPECTED: an order may
    exist but its outcome is unconfirmed. Never create another order;
    query `order status` with the returned order_no when present."""


def parse_atlas_time(raw: object) -> datetime:
    """Parse an upstream depTime/arrTime robustly.

    Drives total_minutes + layover hours, so it must never silently
    misparse: unknown shapes raise ValueError with the offending repr.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return datetime.fromtimestamp(raw / 1000.0)  # epoch ms
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"unparseable Atlas datetime: {raw!r}")
    text = raw.strip()
    if text.isdigit() and len(text) >= 13:  # epoch ms as a string
        return datetime.fromtimestamp(int(text) / 1000.0)
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable Atlas datetime: {raw!r}")


def _same_ticket(raw_segments: list[dict]) -> bool:
    """Ticket-structure hint from the public normalized contract.

    The CLI's normalized offers intentionally drop the upstream
    `separateBookings` flag, so we infer from carrier continuity: one
    marketing carrier across every segment = single ticket; mixed
    carriers = treat as self-transfer (the conservative hint). This is a
    SECONDARY hint only — never decisive (ADR 0002).
    """
    carriers = {seg.get("carrier") for seg in raw_segments if seg.get("carrier")}
    return len(carriers) <= 1


def map_offer(raw: dict) -> Offer:
    """Map one CLI NormalizedOffer (JSON dict) -> our Gate 3 Offer.

    Preserves EVERY segment — dropping a connecting airport is a bug
    (the whole product hangs off layover airports). Raises KeyError /
    ValueError on malformed input; callers skip-and-continue.
    """
    raw_segments = raw["segments"]
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("offer has no segments")

    segments = [
        Segment(
            dep_airport=seg["departure_airport"],
            arr_airport=seg["arrival_airport"],
            dep_time=parse_atlas_time(seg["departure_time"]),
            arr_time=parse_atlas_time(seg["arrival_time"]),
            flight_number=seg["flight_number"],
            direction=seg.get("direction", "outbound"),
        )
        for seg in raw_segments
    ]

    total_minutes = int(
        (segments[-1].arr_time - segments[0].dep_time).total_seconds() // 60
    )

    price_status = raw.get("price_status", "reference")
    if price_status not in ("reference", "current", "verified"):
        price_status = "reference"  # never crash on an unknown literal

    offer_id = raw["offer_id"]
    return Offer(
        id=f"opt-{offer_id}",
        atlas_offer_id=offer_id,
        price=Decimal(str(raw["total_price"])),
        currency=raw.get("currency", "USD"),
        total_minutes=total_minutes,
        segments=segments,
        price_status=price_status,
        bookable=bool(raw.get("bookable", False)),
        same_ticket=_same_ticket(raw_segments),
    )


class AtlasClient:
    """Gate 3 Atlas facade. S1: search (read). S2: + write path
    (verify / confirm-price / create / pay / status / seats) + the
    comparison-mode probe. Writes are fail-closed and never retried."""

    def __init__(self, cli_path: str | None = None):
        # Injectable for tests; defaults to `atlas-flight` on PATH.
        self._cli_path = cli_path
        # Comparison-mode switch cache: ONE auth-status subprocess per
        # process/cycle (ticketing_live); None = not probed yet.
        self._ticketing_cache: bool | None = None

    def _cli(self) -> str:
        path = self._cli_path or shutil.which("atlas-flight")
        if not path:
            raise AtlasError("CLI_NOT_FOUND", "atlas-flight CLI not on PATH")
        return path

    def _run_json(
        self,
        args: list[str],
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Shared subprocess + envelope discipline: run ONE CLI command,
        parse ONE JSON envelope. `stdin` is piped once and closed
        (one-time passenger delivery). No retry logic here — retry
        policy lives in exactly one place (_run_read_only)."""
        cmd = [self._cli(), *args]
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout if timeout is not None else CLI_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AtlasError("TIMEOUT", f"atlas-flight {' '.join(args[:2])} timed out") from exc
        return self._parse_stdout(proc.stdout)

    def _run_read_only(self, args: list[str], timeout: float | None = None) -> dict:
        """THE retry policy (single place): a read-only call gets at most
        ONE identical retry, and only when the envelope says
        retryable=true. `retryable=true` never authorizes a different
        command. WRITES NEVER GO THROUGH HERE."""
        envelope = self._run_json(args, timeout=timeout)
        if envelope.get("status") != "success" and envelope.get("retryable") is True:
            envelope = self._run_json(args, timeout=timeout)  # identical, once
        return envelope

    def search(self, origin: str, dest: str, dep: date, pax: int) -> list[Offer]:
        """One-way search. Returns ALL offers, faithful (price_status /
        bookable carried through; candidate filtering is the loop's job).
        Sorted cheapest-first."""
        cmd = [
            self._cli(),
            "search",
            "--origin", origin,
            "--destination", dest,
            "--depart", dep.isoformat(),
            "--adults", str(pax),
            "--json",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=CLI_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise AtlasError("TIMEOUT", "atlas-flight search timed out") from exc
        return self._offers_from_envelope(self._parse_stdout(proc.stdout))

    @staticmethod
    def _parse_stdout(stdout: str) -> dict:
        text = stdout.lstrip("\ufeff").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AtlasError(
                "BAD_ENVELOPE", "atlas-flight returned no JSON envelope"
            ) from exc

    @staticmethod
    def _offers_from_envelope(envelope: dict) -> list[Offer]:
        # Branch on `code`, never `message` (cli-contract). Only codes and
        # counts leave this function — never payload details, never secrets.
        code = envelope.get("code", "")
        if code == "FLIGHT_SEARCHED":
            data = envelope.get("data") or {}
            offers: list[Offer] = []
            for raw_offer in data.get("offers", []):
                try:
                    offers.append(map_offer(raw_offer))
                except (KeyError, ValueError, TypeError):
                    continue  # skip a malformed offer; never crash the stream
            offers.sort(key=lambda offer: offer.price)
            return offers
        if code == "SEARCH_NO_RESULTS":
            data = envelope.get("data") or {}
            reason = data.get("reason")
            raise AtlasNoResults(reason if isinstance(reason, str) else None)
        raise AtlasError(code or "SERVICE_REQUEST_FAILED")

    # ------------------------------------------------------------------
    # Comparison-mode probe (S2): the desk executes writes ONLY when this
    # says True. Comparison mode = decisions logged + marked, NO write
    # commands, labeled "comparison mode".
    # ------------------------------------------------------------------

    def auth_status(self) -> AuthStatus:
        """`auth status --json` snapshot. Branches on `code` only."""
        envelope = self._run_json(["auth", "status", "--json"])
        code = envelope.get("code", "")
        data = envelope.get("data") or {}
        blocker = data.get("ticketing_blocker")
        return AuthStatus(
            code=code,
            authorized=(code == "AUTHORIZED") and bool(data.get("authenticated")),
            search_available=bool(data.get("search_available")),
            ticketing_available=bool(data.get("ticketing_available")),
            ticketing_blocker=blocker if isinstance(blocker, str) else None,
        )

    def ticketing_live(self) -> bool:
        """Cached write-path gate: ONE auth-status subprocess per
        process/cycle. Fail-closed — any error keeps the desk in
        comparison mode (nothing ordered)."""
        if self._ticketing_cache is None:
            try:
                self._ticketing_cache = self.auth_status().ticketing_available
            except (AtlasError, OSError):
                self._ticketing_cache = False
        return self._ticketing_cache

    # ------------------------------------------------------------------
    # Write path (S2). The contract enumerates FAILURE codes; a step
    # succeeded when the envelope's `status` is "success" (failures are
    # branched on `code`, never `message`).
    # ------------------------------------------------------------------

    def verify(self, offer_id: str) -> VerifyResult:
        """`offer verify --offer-id` — freshness re-read before every
        write. Read-only retry rule. Returns price_change + booking_id
        (the ONLY booking_id source for confirm-price / order create)."""
        envelope = self._run_read_only(
            ["offer", "verify", "--offer-id", offer_id, "--json"]
        )
        code = envelope.get("code", "")
        if envelope.get("status") != "success":
            raise AtlasError(code or "SERVICE_REQUEST_FAILED")
        data = envelope.get("data") or {}
        booking_id = data.get("booking_id")
        if not isinstance(booking_id, str) or not booking_id:
            # Fail loudly with a clear code: downstream steps cannot run.
            raise AtlasError("MISSING_BOOKING_ID", "verify envelope lacks booking_id")
        price_change = data.get("price_change")
        if price_change not in ("unchanged", "decreased", "increased"):
            raise AtlasError("BAD_ENVELOPE", "verify envelope lacks price_change")
        try:
            return VerifyResult(
                offer_id=offer_id,
                booking_id=booking_id,
                price_change=price_change,
                previous_price=Decimal(str(data["previous_price"])),
                current_price=Decimal(str(data["current_price"])),
                currency=str(data["currency"]),
                seat_supported=bool(data.get("seat_supported")),
                baggage_supported=bool(data.get("baggage_supported")),
                travelers=list(data.get("travelers") or []),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise AtlasError("BAD_ENVELOPE", "verify envelope missing price fields") from exc

    def confirm_price(self, booking_id: str) -> None:
        """`booking confirm-price --booking-id` — caller invokes this ONLY
        when verify reported `increased`. Read-only retry rule.
        PRICE_CONFIRMED continues the same booking."""
        envelope = self._run_read_only(
            ["booking", "confirm-price", "--booking-id", booking_id, "--json"]
        )
        code = envelope.get("code", "")
        if envelope.get("status") == "success" or code == "PRICE_CONFIRMED":
            return
        raise AtlasError(code or "SERVICE_REQUEST_FAILED")

    def create_order(
        self,
        booking_id: str,
        pax_json: str,
        seat_policy: str | None = None,
    ) -> OrderRef:
        """`order create --booking-id ... --passengers-stdin` — WRITE,
        NEVER retried (even if retryable=true appears). Success code is
        PAYMENT_CONFIRMATION_REQUIRED (booking-workflow §3) carrying the
        single-use payment_confirmation_id + order_no. Passengers arrive
        once via stdin, never in args."""
        args = ["order", "create", "--booking-id", booking_id, "--passengers-stdin"]
        if seat_policy is not None:
            if seat_policy not in SEAT_POLICIES:
                raise AtlasError("INVALID_ARGUMENT", "unknown seat policy")
            args += ["--seat-policy", seat_policy]
        args.append("--json")
        envelope = self._run_json(args, stdin=pax_json, timeout=WRITE_TIMEOUT_SECONDS)
        code = envelope.get("code", "")
        data = envelope.get("data") or {}
        order_no = data.get("order_no")
        order_no = order_no if isinstance(order_no, str) and order_no else None
        if code == "PAYMENT_CONFIRMATION_REQUIRED":
            confirmation_id = data.get("payment_confirmation_id")
            if not isinstance(confirmation_id, str) or not confirmation_id or not order_no:
                raise AtlasError(
                    "BAD_ENVELOPE", "order create lacks confirmation id / order_no"
                )
            return OrderRef(payment_confirmation_id=confirmation_id, order_no=order_no)
        if code in ("ORDER_CREATION_UNKNOWN", "DUPLICATE_BOOKING_SUSPECTED"):
            # Typed signal: the ONLY follow-up is `order status`.
            raise AtlasUnknownOrder(code, order_no)
        if code == "PRICE_CHANGED":
            # Never create another order — typed query-only signal.
            raise AtlasQueryOnly(code, order_no)
        raise AtlasError(code or "SERVICE_REQUEST_FAILED")

    def pay(self, payment_confirmation_id: str) -> PaymentResult:
        """`order pay --confirmation-id` — WRITE, single-use, NEVER
        retried. The confirmation ID is the one from THAT create_order
        response. Branches on result codes; never pays twice."""
        envelope = self._run_json(
            ["order", "pay", "--confirmation-id", payment_confirmation_id, "--json"],
            timeout=WRITE_TIMEOUT_SECONDS,
        )
        code = envelope.get("code", "")
        data = envelope.get("data") or {}
        order_no = data.get("order_no")
        order_no = order_no if isinstance(order_no, str) and order_no else None
        if code in (
            "TICKETED",
            "TICKETING_PENDING",
            "PAYMENT_BALANCE_CHECK_REQUIRED",
            "PAYMENT_STATUS_UNKNOWN",
            "PAYMENT_PROCESSING",
        ):
            return PaymentResult(
                code=code,
                order_no=order_no,
                ticketed=code == "TICKETED",
                pending_ticketing=code == "TICKETING_PENDING",
                # Query-only: the ONLY follow-up is `order status`
                # (never re-pay; balance-check never pays again either).
                query_only=code
                in ("PAYMENT_STATUS_UNKNOWN", "PAYMENT_PROCESSING",
                    "PAYMENT_BALANCE_CHECK_REQUIRED"),
            )
        raise AtlasError(code or "SERVICE_REQUEST_FAILED")

    def order_status(self, order_no: str) -> OrderStatus:
        """`order status --order-no` — read-only retry rule.
        TICKETING_PENDING is continuing, NOT failure. The outcome
        assertion of the write path: TICKETED and only TICKETED means
        the position is booked."""
        envelope = self._run_read_only(
            ["order", "status", "--order-no", order_no, "--json"]
        )
        code = envelope.get("code", "")
        if code == "TICKETING_PENDING":
            return OrderStatus(code=code, order_no=order_no, ticketed=False)
        if envelope.get("status") != "success":
            raise AtlasError(code or "SERVICE_REQUEST_FAILED")
        return OrderStatus(code=code, order_no=order_no, ticketed=code == "TICKETED")

    def poll_until_ticketed(
        self,
        order_no: str,
        deadline: float = 90.0,
        base_delay: float = 2.0,
    ) -> tuple[OrderStatus, bool]:
        """Poll `order status` with exponential backoff (base -> 2x, cap
        8s, small jitter) until TICKETED or the deadline. Returns the
        final status + `ticket_asserted` — True ONLY on a real TICKETED
        envelope, never on an accepted/200 response."""
        start = time.monotonic()
        delay = base_delay
        status = self.order_status(order_no)
        while not status.ticketed and (time.monotonic() - start) < deadline:
            time.sleep(delay + random.uniform(0.0, 0.25 * delay))
            status = self.order_status(order_no)
            delay = min(delay * 2.0, 8.0)
        return status, status.ticketed

    def follow_up_query_only(self, signal: AtlasQueryOnly) -> OrderStatus:
        """The documented recovery for a query-only signal: ONE
        `order status` read. Never re-create, never re-pay."""
        if not signal.order_no:
            raise AtlasError(signal.code, "query-only signal without order_no")
        return self.order_status(signal.order_no)

    def seat_list(self, booking_id: str) -> list[dict]:
        """`booking seat list --booking-id` — read-only retry rule.
        Returns the opaque seat options (IDs preserved exactly); an empty
        list means the alloc degrades to ledger-only."""
        envelope = self._run_read_only(
            ["booking", "seat", "list", "--booking-id", booking_id, "--json"]
        )
        if envelope.get("status") != "success":
            raise AtlasError(envelope.get("code") or "SERVICE_REQUEST_FAILED")
        data = envelope.get("data") or {}
        seats = data.get("seats", data.get("options", []))
        return seats if isinstance(seats, list) else []

    def seat_select(
        self,
        booking_id: str,
        traveler_id: str,
        segment_id: str,
        seat_id: str,
    ) -> SeatSelection:
        """`booking seat select` — WRITE (booking-stage, booking_id-bound,
        pre-order), NEVER retried. SEAT_UNAVAILABLE is a typed degrade:
        continue the main flow, the alloc lands ledger-only."""
        envelope = self._run_json(
            [
                "booking", "seat", "select",
                "--booking-id", booking_id,
                "--traveler-id", traveler_id,
                "--segment-id", segment_id,
                "--seat-id", seat_id,
                "--json",
            ],
            timeout=WRITE_TIMEOUT_SECONDS,
        )
        code = envelope.get("code", "")
        if envelope.get("status") == "success":
            return SeatSelection(available=True, code=code or "SEAT_SELECTED")
        if code == "SEAT_UNAVAILABLE":
            return SeatSelection(available=False, code=code)
        raise AtlasError(code or "SERVICE_REQUEST_FAILED")
