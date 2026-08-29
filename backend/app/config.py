"""Shared tolerant env parsing (M-new2 consolidation).

Both the API routes (WAYPOINT_CODE_TTL / WAYPOINT_CODE_ATTEMPT_CAP) and
the bot handlers (WAYBOT_MAX_PHOTO_BYTES) used to carry their own copies
of a tolerant int env reader. This module is the ONE copy.

The `minimum` guard is what makes it security-safe: a NEGATIVE override
falls back to the default instead of silently disabling the guard it
configures (e.g. WAYPOINT_CODE_ATTEMPT_CAP=-1 must not mean "unlimited
guesses"). A malformed value likewise falls back to the default rather
than crashing app import (config-typo DoS)."""
from __future__ import annotations

import os


def int_env(name: str, default: int, minimum: int) -> int:
    """Tolerant int env read: returns `default` when the value is unset,
    unparsable, OR below `minimum` — so a typo/negative override can never
    crash import or disable the guard it tunes."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return default
    return value
