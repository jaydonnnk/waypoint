"""AtlasClient — the Gate 3 wrapper around the installed atlas-flight skill.

Slice 2 implements ONLY the read path: `search`. No verify / order / pay
(Slices 3-5, gated on the skill fork + UAT ticketing).

Transport: the skill exposes a clean library entrypoint
(`atlas_cli.cli.build_search_service`), but that package requires
Python >= 3.12 while this backend runs 3.11 — so we use the brief's
sanctioned fallback: subprocess `atlas-flight ... --json` and parse the
stdout envelope. The subprocess reuses the INSTALLED tool's stored
OS-keyring auth + sandbox env config; auth is never re-implemented and
no secret ever appears in code, args, or logs (we branch on envelope
`code`, never on `message`, and only surface codes/counts).

The function signature and return type are transport-independent — the
Gate 3 contract in 03-program-design.md is what stays stable.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime
from decimal import Decimal

from app.models import Offer, Segment

CLI_TIMEOUT_SECONDS = 60.0

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
    """Gate 3 Atlas facade. Slice 2: search only (read path)."""

    def __init__(self, cli_path: str | None = None):
        # Injectable for tests; defaults to `atlas-flight` on PATH.
        self._cli_path = cli_path

    def _cli(self) -> str:
        path = self._cli_path or shutil.which("atlas-flight")
        if not path:
            raise AtlasError("CLI_NOT_FOUND", "atlas-flight CLI not on PATH")
        return path

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
