"""Live booking capture — tee every raw Atlas envelope for recorded mode.

Slice 0 of the Recorded-Mode Engine plan: drives ONE real sandbox booking
(search -> verify -> [confirm-price if increased] -> create -> pay ->
poll until TICKETED) on ATRIP's blessed Flight Booking UAT reference route
(default DIRECT leg FA DUR->CPT; override via WAYPOINT_CAPTURE_ROUTE /
_DEPART / _ADULTS), while a subclass of AtlasClient tees every raw CLI
envelope to backend/data/recorded/booking_envelopes.json (JSON-lines)
BEFORE any parse decision is made. The recording feeds S9's
RecordedAtlasClient replay fixture.

The original SIN->NRT 2-adult default gave rich inventory but never left
TICKETING_PENDING (2026-08-25) and tripped PASSENGER_INFO_INVALID; the
blessed UAT route plus a widened pay timeout and a 10-min TICKETED poll
window (see the *_SECONDS consts below) target a clean TICKETED capture.

Transport tee discipline (same surface S9 will override):
- `_run_json` tees every envelope returned by ONE subprocess (writes and
  read-only retries alike — each underlying call is its own entry);
- `_run_read_only` delegates (its envelopes ride the `_run_json` tee, so
  the single allowed identical retry is captured as its own entry, never
  duplicated);
- `search` re-runs the base transport through the parent's static
  parse helpers so the search envelope is teed too (base `search` owns
  its own subprocess).

On ANY failure the failing envelope is still captured (a typed transport
error gets a synthesized code-only envelope), the code is printed, and
the script exits nonzero — the capture itself is the deliverable.

Contract discipline (error-handling.md): branch on `code`, never
`message`; `order create` / `order pay` are WRITES, never retried;
DUPLICATE_BOOKING_SUSPECTED / ORDER_CREATION_UNKNOWN get the query-only
recovery (ONE `order status` read, never re-create); codes/counts only
are printed — never passenger data (passenger-input.md).

DOUBLE GATE (mirrors tests/test_atlas_write_path.py):
1. env WAYPOINT_WRITE_PATH must read exactly "1" (explicit human intent);
2. the sandbox must report AUTHORIZED + ticketing_available=true.

Run from the backend directory:  python scripts/capture_booking.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# The script runs from backend/ — make `app` importable regardless of the
# caller's sys.path (mirrors how pytest finds the package).
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.atlas.client import (  # noqa: E402  (path fixup must come first)
    CLI_TIMEOUT_SECONDS,
    AtlasClient,
    AtlasError,
    AtlasQueryOnly,
    AtlasUnknownOrder,
)
from app.models import OrderStatus  # noqa: E402

# One-time capture output: JSON-lines, one raw envelope per line, appended
# across runs (seq is globally monotonic; the manifest in the slice doc
# discloses any composite recording).
RECORDING_PATH = BACKEND_ROOT / "data" / "recorded" / "booking_envelopes.json"

# The capture route. Default: ATRIP's blessed Flight Booking UAT reference
# route, DIRECT leg FA DUR->CPT — the route ATRIP guarantees tickets in
# sandbox, so a clean TICKETED capture is far more likely than the old
# SIN->NRT default (rich inventory, but that route's run never left
# TICKETING_PENDING).
#
# ADULTS defaults to 2 — Waypoint's demo portfolio has multi-passenger
# trips, so the recorded ticket should be a genuine 2-pax order. The old
# 2-adult PASSENGER_INFO_INVALID was a payload bug (two travelers sharing
# one identity), fixed in _build_pax_json below (per-index distinct name +
# document number). Set WAYPOINT_CAPTURE_ADULTS="1" for a single-pax
# capture.
# Overridable without editing this file:
#   WAYPOINT_CAPTURE_ROUTE="AMS-MAA"   (the blessed CONNECTION route, 6E)
#   WAYPOINT_CAPTURE_DEPART="2026-09-20"
#   WAYPOINT_CAPTURE_ADULTS="1"
_ROUTE = os.environ.get("WAYPOINT_CAPTURE_ROUTE", "DUR-CPT")
_parts = [p.strip().upper() for p in _ROUTE.split("-", 1)]
ORIGIN, DESTINATION = (_parts + ["", ""])[:2]
DEPART = date.fromisoformat(
    os.environ.get("WAYPOINT_CAPTURE_DEPART", "2026-09-20")
)
ADULTS = int(os.environ.get("WAYPOINT_CAPTURE_ADULTS", "2"))

# Widened READ timeout for status polling/recovery (capture tooling
# only — writes always keep the client's own caps). The sandbox proved
# slow-but-alive on 2026-08-25: order status answered after ~3 minutes
# once the wrapper's 60s cap was bypassed by a patient direct probe.
RECOVERY_READ_TIMEOUT_SECONDS = 240.0

# Widened WRITE timeout for the ONE pay call (capture tooling only, passed
# explicitly to client.pay). The 2026-08-25 capture's pay hit the client's
# 90s write cap and raised TIMEOUT while the payment had actually landed —
# a false alarm that forced query-only recovery. Retry policy is unchanged
# (pay is still never retried); this only lets a slow-but-alive sandbox
# answer in-band.
PAY_WRITE_TIMEOUT_SECONDS = 240.0

# How long to keep polling `order status` for the TICKETED tail. The
# sandbox has taken 5-10 min to flip TICKETING_PENDING -> TICKETED; the
# old 180s deadline gave up long before that.
TICKETED_POLL_DEADLINE_SECONDS = 600.0


class CapturingAtlasClient(AtlasClient):
    """AtlasClient that tees every raw envelope before delegating.

    Zero behavior change vs the parent: parsing, retry policy and write
    discipline all stay the inherited, live-proven code — this subclass
    only observes (subclass-at-transport, per the recorded-mode plan).
    """

    def __init__(self) -> None:
        super().__init__()
        # The driver labels each high-level step; the tee records it so
        # the replay fixture can match on verb + sequence.
        self.step = "init"
        # The sandbox intermittently answers ONLY after minutes (found
        # live 2026-08-25: pay/status calls exceed the client's 60/90s
        # caps while a patient probe eventually gets a real envelope).
        # Capture tooling may widen READ timeouts via this knob — parse
        # logic and write discipline stay the inherited code. Never
        # changes write behavior; None keeps the parent defaults.
        self.read_timeout: float | None = None
        RECORDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Globally monotonic seq: continue from whatever the file holds,
        # so appended runs never collide on sequence numbers.
        self._seq = 0
        if RECORDING_PATH.exists():
            with RECORDING_PATH.open("r", encoding="utf-8") as fh:
                self._seq = sum(1 for line in fh if line.strip())

    def _tee(self, cmd: list[str], envelope: dict) -> None:
        """Append ONE JSON-lines entry, flushed immediately — a crash in
        a later step must never lose the envelopes already captured."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "step": self.step,
            "cmd": cmd,
            "envelope": envelope,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        with RECORDING_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _run_json(
        self,
        args: list[str],
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Tee the envelope of ONE subprocess call (success OR failure
        envelope — the parent returns both; only transport/parse failures
        raise, and those get a synthesized code-only entry)."""
        try:
            envelope = super()._run_json(args, stdin=stdin, timeout=timeout)
        except AtlasError as exc:
            # Transport/parse failure: no envelope came back, so record
            # the typed code (branch-on-code discipline, nothing raw).
            self._tee(args, {"status": "error", "code": exc.code})
            raise
        self._tee(args, envelope)
        return envelope

    def _run_read_only(self, args: list[str], timeout: float | None = None) -> dict:
        """Delegates, optionally widening the READ timeout (see
        `read_timeout`): every envelope this makes (including the single
        allowed identical retry) rides the `_run_json` tee, so nothing
        is missed and nothing is duplicated here."""
        return super()._run_read_only(
            args, timeout=timeout if timeout is not None else self.read_timeout
        )

    def order_status(self, order_no: str) -> OrderStatus:
        """Inherited parse shape, with the capture's widened read timeout
        so a slow-but-alive sandbox still yields a capturable envelope."""
        envelope = self._run_read_only(
            ["order", "status", "--order-no", order_no, "--json"]
        )
        code = envelope.get("code", "")
        if code == "TICKETING_PENDING":
            return OrderStatus(code=code, order_no=order_no, ticketed=False)
        if envelope.get("status") != "success":
            raise AtlasError(code or "SERVICE_REQUEST_FAILED")
        return OrderStatus(
            code=code, order_no=order_no, ticketed=code == "TICKETED"
        )

    def search(self, origin: str, dest: str, dep: date, pax: int):
        """Base `search` owns its own subprocess, so re-run that exact
        transport here through the parent's static parse helpers and tee
        the raw envelope before the offer mapping. Same timeouts, same
        error codes — observation is the only difference."""
        args = [
            "search",
            "--origin", origin,
            "--destination", dest,
            "--depart", dep.isoformat(),
            "--adults", str(pax),
            "--json",
        ]
        try:
            proc = subprocess.run(
                [self._cli(), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=CLI_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            self._tee(args, {"status": "error", "code": "TIMEOUT"})
            raise AtlasError("TIMEOUT", "atlas-flight search timed out") from exc
        envelope = self._parse_stdout(proc.stdout)
        self._tee(args, envelope)
        return self._offers_from_envelope(envelope)


def _build_pax_json(verified_travelers: list[dict]) -> str:
    """Identical shape to the fixed production builder
    (`_build_demo_pax_json` in app/agent/loop.py): carry
    traveler_id/passenger_type from verify, never invent
    (passenger-input.md). Each passenger gets its OWN demo identity
    (given name + document number vary by index) — two travelers
    sharing one identity/doc number are rejected upstream as
    PASSENGER_INFO_INVALID (found live on SIN->NRT with 2 adults,
    2026-08-25). Sandbox demo identities only; nothing here is printed
    or logged."""
    travelers = verified_travelers or [
        {"traveler_id": "", "passenger_type": "adult"}
    ]
    passengers = [
        {
            "traveler_id": t.get("traveler_id", ""),
            # Per-index suffix keeps every demo identity distinct.
            "name": f"DEMO/WAYPOINT{chr(ord('A') + i)}",
            "passenger_type": t.get("passenger_type", "adult"),
            "gender": "M",
            "birthday": "1990-01-01",
            "nationality": "SG",
            "document": {
                "type": "PP",
                "number": f"DEMO00000{i + 1}",
                "issuing_country": "SG",
                "expires": "2030-01-01",
            },
        }
        for i, t in enumerate(travelers)
    ]
    return json.dumps({
        "passengers": passengers,
        "contact": {
            "name": "DEMO/WAYPOINT",
            "email": "demo@waypoint.test",
            "mobile": "0065-90000001",
        },
    })


def main() -> int:
    # Windows consoles often default to cp1252; force UTF-8 so printing
    # codes/arrows never crashes the capture.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

    # Route sanity (env-overridable consts parsed at import) — fail loud
    # before arming anything if WAYPOINT_CAPTURE_ROUTE was malformed.
    if len(ORIGIN) != 3 or len(DESTINATION) != 3:
        print(
            f"capture refused: bad route {ORIGIN!r}->{DESTINATION!r} "
            "(set WAYPOINT_CAPTURE_ROUTE like 'DUR-CPT')"
        )
        return 2
    print(f"route: {ORIGIN}->{DESTINATION} depart={DEPART.isoformat()} "
          f"adults={ADULTS}")

    # GATE 1 — explicit human intent (mirrors test_atlas_write_path.py).
    if os.environ.get("WAYPOINT_WRITE_PATH") != "1":
        print(
            "capture refused: write-path capture is opt-in — "
            "set WAYPOINT_WRITE_PATH=1 to arm"
        )
        return 2

    client = CapturingAtlasClient()

    # GATE 2 — re-check authorization + ticketing, fail closed.
    client.step = "auth_status"
    auth = client.auth_status()
    print(f"auth: code={auth.code} authorized={auth.authorized} "
          f"ticketing_available={auth.ticketing_available}")
    if not auth.authorized:
        print(f"capture blocked: not authorized (code={auth.code})")
        return 3
    if not auth.ticketing_available:
        print(
            "capture blocked: ticketing unavailable "
            f"(code={auth.code}, blocker={auth.ticketing_blocker or 'none'})"
        )
        return 3

    try:
        # 1. search — one search on the confirmed-live route.
        client.step = "search"
        offers = client.search(ORIGIN, DESTINATION, DEPART, ADULTS)
        current_bookable = [
            o for o in offers
            if o.bookable and o.price_status in ("current", "verified")
        ]
        candidates = current_bookable or [o for o in offers if o.bookable]
        if not candidates:
            print(f"capture blocked: no bookable offer "
                  f"(offers={len(offers)})")
            return 1
        offer = candidates[0]  # search() is cheapest-first
        print(f"search: {len(offers)} offers, picked offer "
              f"price_status={offer.price_status} price={offer.price}")

        # 2. verify — freshness re-read; the ONLY traveler_id source.
        client.step = "verify"
        verify = client.verify(offer.atlas_offer_id)
        print(f"verify: price_change={verify.price_change} "
              f"travelers={len(verify.travelers)}")

        # 3. confirm-price — CONDITIONAL, only on a verify-reported rise.
        if verify.price_change == "increased":
            client.step = "confirm_price"
            client.confirm_price(verify.booking_id)
            print("confirm-price: PRICE_CONFIRMED (verify reported increase)")

        # 4. order create — WRITE, never retried; pax payload built from
        #    THIS verify's travelers (the Bug-2 fix shape).
        client.step = "create_order"
        try:
            ref = client.create_order(
                verify.booking_id,
                _build_pax_json(verify.travelers),
                seat_policy="continue-without-seat",
            )
        except AtlasUnknownOrder as signal:
            # An order MAY exist: the ONLY legal follow-up is ONE
            # `order status` read — never re-create (error-handling.md).
            print(f"create_order: {signal.code} (never re-create)")
            if signal.order_no:
                client.step = "order_status_recovery"
                try:
                    status = client.follow_up_query_only(signal)
                    print(f"recovery order status: code={status.code}")
                except AtlasError as exc:
                    print(f"recovery order status failed: code={exc.code}")
            else:
                print("recovery impossible: no order_no in the signal")
            return 1

        # 5. order pay — WRITE, single-use, confirmation id from THAT
        #    create; never re-pay. ANY pay failure (typed code OR
        #    transport TIMEOUT — the pay may still have landed) has the
        #    ONE legal follow-up: query-only recovery via `order status`.
        client.step = "pay"
        try:
            payment = client.pay(
                ref.payment_confirmation_id, timeout=PAY_WRITE_TIMEOUT_SECONDS
            )
        except AtlasError as exc:
            print(f"pay failed: code={exc.code} — never re-pay; "
                  "query-only recovery via order status")
            client.read_timeout = RECOVERY_READ_TIMEOUT_SECONDS
            client.step = "order_status_recovery"
            try:
                status, ticketed = client.poll_until_ticketed(
                    ref.order_no, deadline=TICKETED_POLL_DEADLINE_SECONDS
                )
                print(f"recovery final: code={status.code} "
                      f"ticket_asserted={ticketed}")
                if ticketed:
                    print(f"TICKETED despite pay-transport failure — "
                          f"order_no={ref.order_no}")
                    print(f"recording: {RECORDING_PATH}")
                    return 0
            except AtlasError as exc2:
                print(f"recovery order status failed: code={exc2.code}")
            print(f"recording: {RECORDING_PATH}")
            return 1
        print(f"pay: code={payment.code} order_no={ref.order_no}")
        if payment.query_only:
            # The ONLY follow-up is ONE `order status` read — never re-pay.
            client.read_timeout = RECOVERY_READ_TIMEOUT_SECONDS
            client.step = "order_status_recovery"
            try:
                status = client.follow_up_query_only(
                    AtlasQueryOnly(
                        payment.code, payment.order_no or ref.order_no
                    )
                )
                print(f"recovery order status: code={status.code}")
            except AtlasError as exc:
                print(f"recovery order status failed: code={exc.code}")
            return 1

        # 6. poll order status — TICKETED and only TICKETED. The sandbox
        #    intermittently answers status reads only after minutes, so
        #    the capture widens its READ timeout here (reads only).
        client.read_timeout = RECOVERY_READ_TIMEOUT_SECONDS
        client.step = "order_status"
        status, ticketed = client.poll_until_ticketed(
            ref.order_no, deadline=TICKETED_POLL_DEADLINE_SECONDS
        )
        print(f"final: code={status.code} ticket_asserted={ticketed}")
        if not ticketed:
            print(f"capture failed: never ticketed (code={status.code})")
            return 1
        print(f"TICKETED — order_no={ref.order_no}")
        print(f"recording: {RECORDING_PATH}")
        return 0
    except AtlasError as exc:
        # The failing envelope was already teed by the subclass — the
        # capture is still the deliverable. Code only, never message.
        print(f"capture failed at step={client.step}: code={exc.code}")
        print(f"recording: {RECORDING_PATH}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
