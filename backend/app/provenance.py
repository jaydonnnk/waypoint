"""Per-rail provenance (S12, ADR 0006) — PURE builder for the meta `rails`.

The desk runs on rails of mixed provenance: Atlas (live sandbox / recorded
replay / comparison-only), Qwen (live judgment / deterministic fallback),
priors (always curated — no ML) and the ledger (always real — code-computed).
ONE global "live" label would be a lie the moment any two rails disagree —
it would be true of the rail the reader is looking at and false of the rail
they are about to trust. So provenance is per-rail, always, and each row
states what it is: {rail, state, label, detail}.

PURE: takes the rail inputs, returns rows. No component decides its own
label, nothing here reads the env or touches the wire.

FAIL-TO-LEAST-LIVE (absolute): a missing client, a missing mode signal, or
a brain that has not judged reads as the LEAST-live label that rail has —
never as a live claim. A caller cannot claim Atlas by omission: `build_rails()`
called bare yields comparison-only / fallback / curated / real.

Order is fixed and meaningful: the two rails that CAN be live (Atlas, Qwen)
come first, the two that never can (priors, ledger) immediately after, so a
reader never sees a live label without the non-live rows in the same glance.
"""
from __future__ import annotations

from typing import Any

from app.agent.brain import SOURCE_AGENT

# Rail states — closed vocabulary, branched on downstream (frontend tone
# classes), never parsed. Atlas: live/recorded/comparison/unknown; Qwen:
# live/fallback; priors and ledger never vary.
STATE_LIVE = "live"
STATE_RECORDED = "recorded"
STATE_COMPARISON = "comparison"
STATE_UNKNOWN = "unknown"
STATE_FALLBACK = "fallback"
STATE_CURATED = "curated"
STATE_REAL = "real"


def _atlas_rail(
    atlas: object | None, comparison: bool, live_ticketing: bool
) -> dict:
    """The booking rail. comparison takes priority (no write commands run
    regardless of where envelopes come from — the loop's wire-label block
    uses the same ordering); recorded is probed via the S9 `mode_label`
    attribute; a live claim needs BOTH the explicit live-ticketing signal
    AND a client present — never inferred from silence."""
    if comparison:
        return {
            "rail": "Atlas",
            "state": STATE_COMPARISON,
            "label": "comparison-only",
            "detail": (
                "ticketing is blocked by a fail-closed gate \u2014 decisions "
                "are logged and marked, no write commands run"
            ),
        }
    if getattr(atlas, "mode_label", None) == "recorded":
        return _recorded_rail(atlas)
    if live_ticketing and atlas is not None:
        return {
            "rail": "Atlas",
            "state": STATE_LIVE,
            "label": "live sandbox",
            "detail": (
                "fares and orders come from a real Atlas sandbox call made "
                "just now \u2014 sandbox money only, nothing is production "
                "inventory"
            ),
        }
    # Missing client / missing mode signal — the least-live label, never a
    # live claim.
    return {
        "rail": "Atlas",
        "state": STATE_UNKNOWN,
        "label": "not verified",
        "detail": (
            "the booking rail did not identify itself, so it is treated as "
            "not live \u2014 nothing here came from a verified sandbox"
        ),
    }


def _recorded_rail(atlas: object) -> dict:
    """Recorded replay — the manifest's honesty rides in the detail:
    composite capture and whether a TICKETED envelope was ever genuinely
    captured. A missing/unreadable manifest degrades to the least-live
    wording, never to a live claim."""
    manifest = getattr(atlas, "manifest", None)
    if not isinstance(manifest, dict):
        return {
            "rail": "Atlas",
            "state": STATE_RECORDED,
            "label": "recorded replay",
            "detail": (
                "a recorded Atlas capture is being replayed, but its capture "
                "register is unavailable \u2014 treat the replay as "
                "unverified; nothing was requested just now"
            ),
        }
    parts = [
        "a real Atlas sandbox capture, replayed exactly as recorded \u2014 "
        "nothing was requested just now"
    ]
    if manifest.get("composite"):
        parts.append("the capture is composite")
    if manifest.get("ticketed_captured"):
        parts.append(
            "a TICKETED envelope was genuinely captured at recording time"
        )
    else:
        parts.append(
            "no TICKETED envelope was ever captured, so nothing here is "
            "confirmed ticketed"
        )
    return {
        "rail": "Atlas",
        "state": STATE_RECORDED,
        "label": "recorded replay",
        "detail": "; ".join(parts),
    }


def _qwen_rail(brain: object | None) -> dict:
    """The judgment rail. Reads `DeskBrain.last_source` (set at every
    judge() exit, auditor.py wire values). No judgment on record reads as
    the fallback — the least-live label, never a live claim."""
    if getattr(brain, "last_source", None) == SOURCE_AGENT:
        return {
            "rail": "Qwen",
            "state": STATE_LIVE,
            "label": "live model",
            "detail": (
                "a live Qwen judgment made the book/hold calls; "
                "deterministic code re-checks every pick afterwards"
            ),
        }
    return {
        "rail": "Qwen",
        "state": STATE_FALLBACK,
        "label": "deterministic fallback",
        "detail": (
            "no Qwen judgment on record \u2014 the picks come from the "
            "deterministic prior-band rule in code, no model called"
        ),
    }


def _priors_rail() -> dict:
    return {
        "rail": "Priors",
        "state": STATE_CURATED,
        "label": "curated \u2014 no ML",
        "detail": (
            "volatility bands are hand-curated approximations compiled into "
            "this build \u2014 disclosed, no model trained or called"
        ),
    }


def _ledger_rail() -> dict:
    return {
        "rail": "Ledger",
        "state": STATE_REAL,
        "label": "real \u2014 code-computed",
        "detail": (
            "every amount is computed by deterministic code and settled in "
            "one ledger transaction \u2014 no model touches money"
        ),
    }


def build_rails(
    *,
    atlas: object | None = None,
    brain: object | None = None,
    comparison: bool = True,
    live_ticketing: bool = False,
) -> list[dict[str, Any]]:
    """Four provenance rows, fixed order: Atlas, Qwen (the two that CAN be
    live), then priors, ledger (the two that never can). Defaults are
    fail-closed: comparison=True and live_ticketing=False, so a bare call
    can only ever read as the least-live set."""
    return [
        _atlas_rail(atlas, comparison, live_ticketing),
        _qwen_rail(brain),
        _priors_rail(),
        _ledger_rail(),
    ]
