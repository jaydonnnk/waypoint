"""Pax builder — the G1 write-path swap (S3).

Replaces ``_build_demo_pax_json`` in ``loop.py`` with a desk-kind-aware
builder that reads stored travelers when the desk is gated, and falls back
to demo identities ONLY for ungated (legacy/recorded) desks.

FALLBACK IS KEYED ON DESK KIND, NEVER ON DATA PRESENCE (safety):
  - GATED desk (has invite_token) missing/short travelers → PaxBuild(hold=True).
    The wall HOLDS + ESCALATES; it NEVER silently books demo identities.
  - UNGATED desk (no invite_token) → demo envelope, pax_source='demo'.
    Recorded-mode and existing tests stay byte-safe.

``pax_source`` rides the booking provenance event: 'collected' | 'demo'.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.db.store import DeskStore


@dataclass(frozen=True)
class PaxBuild:
    """Result of the pax builder."""

    pax_json: str | None       # None when hold
    pax_source: Literal["collected", "demo"]
    hold: bool = False         # gated desk without a full roster → hold+escalate


def build_pax_json(
    desk_id: str,
    verified_travelers: list[dict],
    store: "DeskStore",
) -> PaxBuild:
    """Build the passenger JSON for order create.

    ``verified_travelers`` comes from the verify response (carry, never
    invent — traveler_id is the verify-returned identity).

    DESK KIND determines the fallback:
      - GATED (has invite_token) → read stored travelers, zip with verify's
        traveler_ids.  Short roster → PaxBuild(hold=True).
      - UNGATED (no invite_token) → demo envelope, byte-safe.
    """
    # Determine desk kind: gated if invite_token is non-null.
    _token, _code_hash = store.get_invite(desk_id)
    is_gated = _token is not None

    if not is_gated:
        # UNGATED desk (legacy/recorded): demo envelope, byte-safe.
        return _build_demo(verified_travelers)

    # GATED desk: real travelers ONLY — never invent an identity.
    # Empty verify travelers on a gated desk is a hold, not a fabricated
    # "traveler_id": "" (carry, never invent). The wall holds + escalates.
    if not verified_travelers:
        return PaxBuild(pax_json=None, pax_source="collected", hold=True)

    stored = store.list_travelers(desk_id)
    if not stored or len(stored) < len(verified_travelers):
        # Missing or short roster → hold + escalate.
        return PaxBuild(pax_json=None, pax_source="collected", hold=True)

    # Zip stored travelers with verify's traveler_ids (carry, never invent).
    travelers = verified_travelers
    passengers = []
    for i, t in enumerate(travelers):
        if i >= len(stored):
            # More verify travelers than stored → hold.
            return PaxBuild(pax_json=None, pax_source="collected", hold=True)
        row = stored[i]
        passengers.append({
            "traveler_id": t.get("traveler_id", ""),
            "name": f"{row['family_name']}/{row['given_name']}",
            "passenger_type": t.get("passenger_type", "adult"),
            "gender": row["gender"],
            "birthday": row["birthday"],
            "nationality": row["nationality"],
            "document": {
                "type": row.get("doc_type", "PP"),
                "number": row["doc_number"],
                "issuing_country": row["issuing_country"],
                "expires": row["doc_expiry"],
            },
        })

    # Distinct docs per pax (safety check).
    doc_numbers = [p["document"]["number"] for p in passengers]
    if len(set(doc_numbers)) != len(doc_numbers):
        # Duplicate doc numbers → hold (should never happen with the
        # store's uniqueness guard, but defense in depth).
        return PaxBuild(pax_json=None, pax_source="collected", hold=True)

    # Contact block: use the first stored traveler's contact if available.
    contact_email = stored[0].get("contact_email") or "noreply@waypoint.test"
    contact_mobile = stored[0].get("contact_mobile") or "0065-00000000"

    pax_json = json.dumps({
        "passengers": passengers,
        "contact": {
            "name": f"{stored[0]['family_name']}/{stored[0]['given_name']}",
            "email": contact_email,
            "mobile": contact_mobile,
        },
    })
    return PaxBuild(pax_json=pax_json, pax_source="collected")


def _build_demo(verified_travelers: list[dict]) -> PaxBuild:
    """Demo pax envelope for ungated desks (byte-safe, same shape as the
    original _build_demo_pax_json)."""
    travelers = verified_travelers or [
        {"traveler_id": "", "passenger_type": "adult"}
    ]
    passengers = [
        {
            "traveler_id": t.get("traveler_id", ""),
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
    pax_json = json.dumps({
        "passengers": passengers,
        "contact": {
            "name": "DEMO/WAYPOINT",
            "email": "demo@waypoint.test",
            "mobile": "0065-90000001",
        },
    })
    return PaxBuild(pax_json=pax_json, pax_source="demo")
