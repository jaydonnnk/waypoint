"""Atlas mode switch (S9) — live sandbox vs recorded replay.

ONE env var selects the Atlas rail: `WAYPOINT_ATLAS_MODE`. Strict parse,
case-normalized: ONLY the exact value "recorded" selects the replay
client; unset, empty, typos, and anything else stay LIVE (today's
behavior, unchanged).

Fail-to-live is the safe default here — not because live is safer than
replay, but because money safety NEVER rests on this switch: it rests on
the two existing write gates (the human arm-switch WAYPOINT_LIVE_BOOKING
and ticketing_live(), loop.py `_comparison_mode`), both fail-closed.
A typo in WAYPOINT_ATLAS_MODE can therefore never order anything it
should not; it can only leave the desk on the rail it had before (ADR
0005). The recorded container sets "recorded" EXPLICITLY.
"""
from __future__ import annotations

import os

ATLAS_MODE_ENV = "WAYPOINT_ATLAS_MODE"

# The only two modes this codebase knows. Anything unparsable reads as
# LIVE (documented above — never as recorded, never as a crash).
MODE_LIVE = "live"
MODE_RECORDED = "recorded"


def read_atlas_mode() -> str:
    """Strict parse of WAYPOINT_ATLAS_MODE: case-normalized, and ONLY the
    exact value "recorded" selects replay. Unset / unknown / typo → live.
    No strip-and-guess: a padded " recorded " is a typo and reads live."""
    raw = os.environ.get(ATLAS_MODE_ENV)
    if raw is None:
        return MODE_LIVE
    if raw.lower() == MODE_RECORDED:
        return MODE_RECORDED
    return MODE_LIVE
