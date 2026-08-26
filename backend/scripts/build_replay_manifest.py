"""Build the recorded-mode replay manifest from the capture file (S9).

The recording (`backend/data/recorded/booking_envelopes.json`) is the
raw append-only capture artifact. The manifest (`manifest.json` beside
it) is the honesty register RecordedAtlasClient loads: it names every
step the replay serves (captured entries by `seq` reference, any
RECONSTRUCTED step inline and flagged), discloses the composite state,
and carries the wire disclosure for the meta event.

Script selection (data-driven, deterministic over the file):
- The replay starts at the LAST `auth_status` gate entry (the newest
  capture run supersedes earlier runs — the Slice 0 doc's "match the
  LAST complete sequence" rule), then follows that run's search →
  verify → create_order envelopes by file order.
- pay: the run's captured pay envelope is served as-is when it carries
  a pay-success code; when the transport lost the pay envelope (TIMEOUT)
  BUT a genuine TICKETED order-status envelope was captured afterwards,
  the pay step is RECONSTRUCTED (flagged) as TICKETING_PENDING — the
  order's own ticketed state proves the payment landed. A TICKETED
  envelope is NEVER reconstructed: only served when genuinely captured.
- order status: appended only when a genuine TICKETED capture exists.

Re-run this script whenever a fresh capture appends a complete sequence
(healthy-sandbox day) — the manifest then promotes that sequence
automatically.

Run from the backend directory:  python scripts/build_replay_manifest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.atlas.recorded import MANIFEST_PATH, RECORDING_PATH  # noqa: E402

# Codes `AtlasClient.pay` accepts as a pay outcome (client.py branch).
PAY_SUCCESS_CODES = (
    "TICKETED",
    "TICKETING_PENDING",
    "PAYMENT_BALANCE_CHECK_REQUIRED",
    "PAYMENT_STATUS_UNKNOWN",
    "PAYMENT_PROCESSING",
)


def _verb(cmd: list[str]) -> str:
    leading = []
    for token in cmd:
        if token.startswith("--"):
            break
        leading.append(token)
    return " ".join(leading)


def main() -> int:
    entries = []
    with RECORDING_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    if not entries:
        print("manifest refused: the recording is empty")
        return 1

    # --- the honesty facts, straight off the file.
    ticketed = [
        e for e in entries
        if e["envelope"].get("code") == "TICKETED"
        and _verb(e["cmd"]) == "order status"
    ]
    ticketed_captured = bool(ticketed)

    # --- anchor the replay on the LAST auth gate (newest run supersedes).
    auths = [e for e in entries if _verb(e["cmd"]) == "auth status"]
    if not auths:
        print("manifest refused: no auth-status gate in the recording")
        return 1
    anchor = auths[-1]
    run = [e for e in entries if e["seq"] >= anchor["seq"]]

    def take(verb: str):
        for entry in run:
            if _verb(entry["cmd"]) == verb:
                return entry
        return None

    search, verify, create = (
        take("search"), take("offer verify"), take("order create"),
    )
    pay = take("order pay")
    if not all([search, verify, create]):
        print("manifest refused: the last run lacks "
              "search/verify/create envelopes")
        return 1

    script: list[dict] = [
        {"verb": "auth status", "seq": anchor["seq"], "provenance": "captured"},
        {"verb": "search", "seq": search["seq"], "provenance": "captured"},
        {"verb": "offer verify", "seq": verify["seq"], "provenance": "captured"},
        {"verb": "order create", "seq": create["seq"], "provenance": "captured"},
    ]
    reconstructed: list[dict] = []
    order_no = (create["envelope"].get("data") or {}).get("order_no")

    if pay is not None and pay["envelope"].get("code") in PAY_SUCCESS_CODES:
        # The capture holds a real pay outcome — serve it verbatim.
        script.append(
            {"verb": "order pay", "seq": pay["seq"], "provenance": "captured"}
        )
    elif ticketed_captured:
        # Pay transport loss (TIMEOUT) with a genuine TICKETED capture
        # afterwards: the order's own ticketed state proves the payment
        # landed. Reconstruct the pay step — flagged here, disclosed on
        # the wire. NEVER a TICKETED envelope (that one is captured).
        envelope = {
            "status": "success",
            "code": "TICKETING_PENDING",
            "data": {"order_no": order_no},
        }
        script.append({
            "verb": "order pay",
            "envelope": envelope,
            "provenance": "reconstructed",
            "reason": "pay transport TIMEOUT in capture; the captured "
                      "TICKETED order-status envelope proves payment "
                      "landed — flagged, never labeled captured",
        })
        reconstructed.append({
            "verb": "order pay",
            "code": "TICKETING_PENDING",
            "reason": "see script entry; reconstruction proven by the "
                      "captured TICKETED envelope",
        })
    elif pay is not None:
        # Composite recording, no ticketing tail: serve the captured pay
        # envelope EXACTLY as captured (e.g. the TIMEOUT) — the replay
        # ends the way the capture ended, honestly.
        script.append(
            {"verb": "order pay", "seq": pay["seq"], "provenance": "captured"}
        )

    if ticketed_captured:
        script.append({
            "verb": "order status",
            "seq": ticketed[-1]["seq"],
            "provenance": "captured",
        })

    composite = not ticketed_captured
    if ticketed_captured:
        wire_disclosure = (
            "recorded Atlas replay \u2014 envelopes served from the "
            "sandbox capture (never live); includes ONE flagged "
            "reconstructed step (order pay) proven by the captured "
            "TICKETED envelope"
            if reconstructed else
            "recorded Atlas replay \u2014 envelopes served from the "
            "sandbox capture (never live); ticketing genuinely captured"
        )
    else:
        wire_disclosure = (
            "recorded Atlas replay \u2014 composite capture with NO "
            "TICKETED envelope (sandbox flapped); the cycle replays the "
            "capture exactly as recorded, never live"
        )

    manifest = {
        "recording": RECORDING_PATH.name,
        "mode_label": "recorded",
        "composite": composite,
        "ticketed_captured": ticketed_captured,
        "order_no": order_no,
        "reconstructed_steps": reconstructed,
        "script": script,
        "captured_inventory": [
            {
                "seq": e["seq"],
                "step": e["step"],
                "verb": _verb(e["cmd"]),
                "code": e["envelope"].get("code"),
                "captured_at": e.get("captured_at"),
            }
            for e in entries
        ],
        "wire_disclosure": wire_disclosure,
        "honesty_rule": (
            "never fabricate a TICKETED envelope; reconstructed steps "
            "are flagged here and disclosed on the wire; recorded is "
            "never labeled live"
        ),
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"manifest written: {MANIFEST_PATH}")
    print(f"  composite={composite} ticketed_captured={ticketed_captured} "
          f"reconstructed={len(reconstructed)} script_steps={len(script)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
