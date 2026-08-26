"""S9 recorded-mode unit tests — the mode switch, replay-through-real-parser,
fail-closed behavior, and the AtlasClient/RecordedAtlasClient contract guard.

In-gate (non-live): NOTHING here spawns a subprocess or touches the
sandbox. The replay client serves the Slice 0 capture
(`backend/data/recorded/booking_envelopes.json`) through the IDENTICAL
inherited parse logic from client.py — the same-parser guarantee is the
property under test.
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import date

import pytest

from app.atlas.client import AtlasClient, AtlasError
from app.atlas.config import ATLAS_MODE_ENV, MODE_LIVE, MODE_RECORDED, read_atlas_mode
from app.atlas.recorded import RECORDING_PATH, RecordedAtlasClient


def _captured_entries() -> list[dict]:
    """The raw capture file, parsed (one JSON object per line)."""
    entries = []
    with RECORDING_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


# ==========================================================================
# read_atlas_mode — strict parse, case-normalized, fail-to-live.
# ==========================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        ("recorded", MODE_RECORDED),
        ("RECORDED", MODE_RECORDED),      # case-normalized
        ("Recorded", MODE_RECORDED),
        ("live", MODE_LIVE),
        ("", MODE_LIVE),                  # empty stays live
        ("recoded", MODE_LIVE),           # typo -> live (never guess)
        ("record", MODE_LIVE),
        (" recorded ", MODE_LIVE),        # padded = typo -> live
        ("1", MODE_LIVE),
        ("true", MODE_LIVE),
    ],
)
def test_read_atlas_mode_strict_parse(monkeypatch, value, expected):
    monkeypatch.setenv(ATLAS_MODE_ENV, value)
    assert read_atlas_mode() == expected


def test_read_atlas_mode_unset_is_live(monkeypatch):
    monkeypatch.delenv(ATLAS_MODE_ENV, raising=False)
    assert read_atlas_mode() == MODE_LIVE


# ==========================================================================
# Replay through the REAL parser — identical Offer list, identical writes.
# ==========================================================================


def test_recorded_search_yields_identical_offers_to_live_parser():
    """Deliverable 8 core: the recorded search envelope rides the SAME
    inherited parser (`_offers_from_envelope` -> `map_offer`) the live
    client uses — the Offer list is byte-identical to a live parse of the
    same envelope (cheapest-first sort included)."""
    search_entry = next(
        e for e in _captured_entries()
        if e["step"] == "search" and e["envelope"].get("code") == "FLIGHT_SEARCHED"
        and e["seq"] >= 5  # the replay run (last auth gate onward)
    )
    expected = AtlasClient()._offers_from_envelope(search_entry["envelope"])

    recorded = RecordedAtlasClient()
    got = recorded.search("SIN", "NRT", date(2026, 9, 4), 2)

    assert got == expected
    assert len(got) == 8  # the capture holds eight SIN->NRT offers
    # Cheapest-first, exactly as the live parser sorts.
    assert [o.price for o in got] == sorted(o.price for o in got)
    assert got[0].price == min(o.price for o in got)


def test_recorded_write_path_parses_captured_envelopes():
    """verify / create_order / pay run through the inherited write-path
    parsers unchanged: the captured envelopes produce the same typed
    results a live run parsed, and the captured pay TIMEOUT raises the
    typed error — branch on code, never message."""
    recorded = RecordedAtlasClient()

    verified = recorded.verify("off_c5c2aff9ea4849967b775b98")
    assert verified.booking_id == "book_db2483544646f3d094e082cb"
    assert verified.price_change == "unchanged"
    assert len(verified.travelers) == 2  # carried, never invented

    ref = recorded.create_order(verified.booking_id, "[]", "continue-without-seat")
    assert ref.order_no == "TESTA20260825233427052"
    assert ref.payment_confirmation_id

    # The capture's pay envelope IS the transport TIMEOUT — replay ends
    # the way the capture ended, honestly (typed code on the wire).
    with pytest.raises(AtlasError) as exc_info:
        recorded.pay(ref.payment_confirmation_id)
    assert exc_info.value.code == "TIMEOUT"


def test_ticketing_live_replays_the_captured_auth_gate():
    """ticketing_live() is inherited verbatim: it replays the captured
    AUTHORIZED auth-status envelope (a genuine captured fact) and caches
    exactly like the live client — one envelope per cycle."""
    recorded = RecordedAtlasClient()
    assert recorded.ticketing_live() is True
    # Cached: a second read consumes NOTHING new (cursor unchanged).
    assert recorded.ticketing_live() is True
    recorded.reset_ticketing_cache()
    # Per-cycle rewind: the probe replays from the first scripted envelope.
    assert recorded.ticketing_live() is True


# ==========================================================================
# Fail-closed discipline — unmatched calls get NOTHING.
# ==========================================================================


def test_unscripted_call_fails_closed_with_no_recording():
    """The composite script ends at the captured pay TIMEOUT: a second
    search (the script holds one) and any unscripted verb raise typed
    NO_RECORDING — never a guess, never a synthesized envelope."""
    recorded = RecordedAtlasClient()
    recorded.search("SIN", "NRT", date(2026, 9, 4), 2)  # the one scripted search
    with pytest.raises(AtlasError) as exc_info:
        recorded.search("SIN", "NRT", date(2026, 9, 4), 2)
    assert exc_info.value.code == "NO_RECORDING"
    # confirm-price is unscripted (verify reported `unchanged` live).
    with pytest.raises(AtlasError) as exc_info:
        recorded.confirm_price("book_db2483544646f3d094e082cb")
    assert exc_info.value.code == "NO_RECORDING"


def test_poll_until_ticketed_is_clock_free_and_fails_closed():
    """The recording IS the timeline: poll_until_ticketed never touches
    the clock (sleep/monotonic tripwired), and with no `order status`
    step scripted (composite capture) it fails closed on NO_RECORDING —
    it NEVER loops forever and NEVER fabricates TICKETED."""
    def tripwire(*args, **kwargs):
        raise AssertionError("replay must never touch the clock")

    recorded = RecordedAtlasClient()
    monkeypatch_sleep = time.sleep
    monkeypatch_monotonic = time.monotonic
    time.sleep = tripwire
    time.monotonic = tripwire
    try:
        with pytest.raises(AtlasError) as exc_info:
            recorded.poll_until_ticketed("TESTA20260825233427052")
        assert exc_info.value.code == "NO_RECORDING"
    finally:
        time.sleep = monkeypatch_sleep
        time.monotonic = monkeypatch_monotonic


def test_missing_or_malformed_artifacts_fail_closed(tmp_path):
    """A deployment that claims the recording must carry it: missing or
    malformed recording/manifest refuse construction with NO_RECORDING."""
    with pytest.raises(AtlasError) as exc_info:
        RecordedAtlasClient(recording_path=tmp_path / "absent.json")
    assert exc_info.value.code == "NO_RECORDING"

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(AtlasError) as exc_info:
        RecordedAtlasClient(manifest_path=broken)
    assert exc_info.value.code == "NO_RECORDING"

    bad_line = tmp_path / "bad_line.json"
    bad_line.write_text("not-a-json-line\n", encoding="utf-8")
    with pytest.raises(AtlasError) as exc_info:
        RecordedAtlasClient(recording_path=bad_line)
    assert exc_info.value.code == "NO_RECORDING"


def test_recorded_client_never_spawns_a_subprocess(monkeypatch):
    """Transport tripwire: a full scripted pass (auth probe -> search ->
    verify -> create -> pay) spawns ZERO subprocesses."""
    spawned: list = []

    def trap(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("recorded mode must never spawn a process")

    monkeypatch.setattr(subprocess, "run", trap)
    monkeypatch.setattr(subprocess, "Popen", trap)

    recorded = RecordedAtlasClient()
    assert recorded.ticketing_live() is True
    recorded.search("SIN", "NRT", date(2026, 9, 4), 2)
    verified = recorded.verify("off_c5c2aff9ea4849967b775b98")
    ref = recorded.create_order(verified.booking_id, "[]", "continue-without-seat")
    with pytest.raises(AtlasError):
        recorded.pay(ref.payment_confirmation_id)
    assert spawned == []


# ==========================================================================
# Honesty register — manifest + wire label.
# ==========================================================================


def test_mode_label_and_wire_disclosure_are_honest():
    """Recorded NEVER wears the live label, and the wire disclosure
    states the composite capture truth (no fabricated TICKETED)."""
    recorded = RecordedAtlasClient()
    assert recorded.mode_label == "recorded"
    assert "recorded Atlas replay" in recorded.gate_disclosure
    assert recorded.manifest["ticketed_captured"] is False
    assert recorded.manifest["composite"] is True
    # Every scripted step is provenance-captured in the composite case.
    assert recorded.manifest["reconstructed_steps"] == []
    assert all(
        entry["provenance"] == "captured" for entry in recorded.manifest["script"]
    )


# ==========================================================================
# Contract drift — the public callable surface MUST stay identical.
# ==========================================================================


def _public_callables(cls) -> set[str]:
    return {
        name for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }


def test_recorded_client_public_surface_matches_live_client():
    """Deliverable 7: RecordedAtlasClient overrides ONLY the transport —
    the public callable contract of AtlasClient is inherited unchanged.
    A future public method on either side trips this guard."""
    assert _public_callables(AtlasClient) == _public_callables(RecordedAtlasClient)
