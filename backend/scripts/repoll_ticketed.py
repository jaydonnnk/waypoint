"""Slice S9 Step 0 — ONE bounded, query-only re-poll for the TICKETED tail.

The Slice 0 capture left order TESTA20260825233427052 at TICKETING_PENDING
(pay transport TIMEOUT, payment clearly landed, then 17 pending polls and a
sandbox flap). This script asks the SAME read-only question a few times
over at most ~10 minutes: `order status --order-no ... --json`.

Discipline (error-handling.md):
- READ-ONLY. The command is `order status` and nothing else — NEVER
  re-create, NEVER re-pay, never any other verb.
- Every envelope (success or typed failure) is teed into the recording
  file with the step label "order_status_s9_poll", continuing the
  globally monotonic seq — exactly the capture format S9's replay loads.
- Branch on `code`, never `message`; print codes only.
- Stops early the moment a TICKETED envelope arrives.

HONESTY RULE: this script only APPENDS envelopes the sandbox actually
returned. It never fabricates anything; if the sandbox keeps flapping,
the recording simply gains the flap envelopes and S9 proceeds with the
composite manifest.

Run from the backend directory:  python scripts/repoll_ticketed.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

RECORDING_PATH = BACKEND_ROOT / "data" / "recorded" / "booking_envelopes.json"
# The order to re-poll. Override via env for a fresh capture's pending
# order; the historical default is the Slice 0 order this script was
# first written for.
ORDER_NO = os.environ.get("WAYPOINT_REPOLL_ORDER", "TESTA20260825233427052")

# Overall budget for the whole re-poll session (~10 minutes, per the plan).
DEADLINE_SECONDS = 600.0
# The sandbox intermittently answers status reads only after minutes
# (found live 2026-08-25), so the read gets a patient cap — it is the
# ONLY command this script ever runs.
READ_TIMEOUT_SECONDS = 180.0
# Pause between polls so a few attempts fit inside the budget.
POLL_INTERVAL_SECONDS = 45.0


def _next_seq() -> int:
    if not RECORDING_PATH.exists():
        return 1
    with RECORDING_PATH.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip()) + 1


def _tee(cmd: list[str], envelope: dict, seq: int) -> None:
    entry = {
        "seq": seq,
        "step": "order_status_s9_poll",
        "cmd": cmd,
        "envelope": envelope,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    with RECORDING_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass
    cli = shutil.which("atlas-flight")
    if not cli:
        print("re-poll blocked: atlas-flight CLI not on PATH")
        return 2
    cmd = [cli, "order", "status", "--order-no", ORDER_NO, "--json"]
    start = _now()
    attempt = 0
    while _now() - start < DEADLINE_SECONDS:
        attempt += 1
        seq = _next_seq()
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=READ_TIMEOUT_SECONDS,
            )
            envelope = json.loads(proc.stdout.lstrip("\ufeff").strip())
        except subprocess.TimeoutExpired:
            envelope = {"status": "error", "code": "TIMEOUT"}
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            envelope = {"status": "error", "code": "BAD_ENVELOPE"}
        _tee(cmd[1:], envelope, seq)
        code = envelope.get("code", "")
        print(f"poll {attempt}: seq={seq} code={code}")
        if code == "TICKETED":
            print("TICKETED envelope captured — recording complete")
            return 0
        if _now() - start + POLL_INTERVAL_SECONDS >= DEADLINE_SECONDS:
            break
        import time
        time.sleep(POLL_INTERVAL_SECONDS)
    print(f"re-poll budget exhausted after {attempt} attempts — "
          "no TICKETED envelope; S9 proceeds with the composite recording")
    return 1


def _now() -> float:
    import time
    return time.monotonic()


if __name__ == "__main__":
    sys.exit(main())
