"""Waypoint demo pre-warm — seed a desk and run its cycle BEFORE the demo.

The 0:00–0:10 cold open replays a REAL prior cycle from the SSE buffer:
a late connect replays from event 0, so opening the desk URL after this
script replays a complete real cycle instantly. Nothing is faked
client-side; comparison mode stays labeled.

OPERATIONAL (read before the demo):
- Run this AFTER the backend is up (uvicorn serving the FastAPI app),
  and do NOT restart the backend between the pre-warm and the demo —
  the replay buffer is IN-MEMORY and dies with the process.
- The scenario flag (WAYPOINT_INJECT_SCENARIO) is read by the BACKEND
  process, so the backend must have been started from the same shell
  (same for DASHSCOPE_API_KEY — otherwise the auditor quietly runs its
  deterministic fallback line). "0" disarms the scripted loss+spike
  scenario; anything else (including unset) keeps it armed.

Stdlib-adjacent HTTP via httpx (already a backend dependency — no new
deps). Base URL overridable via WAYPOINT_BASE_URL.
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

# Windows consoles often default to cp1252, which cannot encode the
# auditor line's route arrows (JFK→LIS) or minus signs (−$22.00). Force
# UTF-8 output with a replacement fallback so printing never crashes the
# pre-warm. Guarded: some streams (redirected pipes, exotic runtimes)
# have no reconfigure() — those fall through unchanged.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError, ValueError):
    pass

BASE_URL = os.environ.get("WAYPOINT_BASE_URL", "http://localhost:8000")
FRONTEND_URL = os.environ.get("WAYPOINT_FRONTEND_URL", "http://localhost:3000")
POLL_DEADLINE_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 2.0
# Bounded wait for the post-close SSE replay verification (the buffer is
# in-memory and the cycle is already settled, so replay should be instant).
STREAM_VERIFY_TIMEOUT_SECONDS = 15.0


def main() -> int:
    scenario_hint = (
        "disarmed (clean baseline)"
        if os.environ.get("WAYPOINT_INJECT_SCENARIO") == "0"
        else "armed (scripted loss + fare-spike injection)"
    )
    print(f"pre-warm: backend {BASE_URL}")
    print(f"pre-warm: scenario in THIS shell: {scenario_hint}")
    with httpx.Client(timeout=30.0) as client:
        seed = client.post(f"{BASE_URL}/api/desk/seed")
        seed.raise_for_status()
        desk_id = seed.json()["desk_id"]
        print(f"pre-warm: seeded desk {desk_id} — running its cycle...")

        # Poll /close until the cycle settles. Expected statuses ONLY:
        # 200 = settled, 504 = still running. ANYTHING else fails fast
        # with the real status instead of spinning to the deadline.
        deadline = time.monotonic() + POLL_DEADLINE_SECONDS
        report: dict | None = None
        while time.monotonic() < deadline:
            resp = client.get(f"{BASE_URL}/api/desk/{desk_id}/close")
            if resp.status_code == 200:
                report = resp.json()
                break
            if resp.status_code == 504:
                time.sleep(POLL_INTERVAL_SECONDS)  # still running → poll on
                continue
            if resp.status_code == 500:
                print("pre-warm: FAILED — the desk cycle crashed (HTTP 500)")
                return 1
            print(
                f"pre-warm: FAILED — unexpected HTTP {resp.status_code} "
                f"from /close (expected 200 or 504)"
            )
            return 1
        if report is None:
            print("pre-warm: FAILED — cycle did not finish before the demo deadline")
            return 1

        # Contract sanity: the CloseReport wrapper, its auditor line, and
        # the zero-breach invariant — all BEFORE printing success.
        if "result" not in report:
            print("pre-warm: FAILED — close report missing the DeskResult wrapper")
            return 1
        if not report.get("auditor_line"):
            print("pre-warm: FAILED — close report missing the auditor line")
            return 1
        if report.get("policy_breaches") != 0:
            print(
                "pre-warm: FAILED — expected 0 policy breaches, got "
                f"{report.get('policy_breaches')!r}"
            )
            return 1

        # Verify the thing actually being pre-warmed: the SSE replay
        # buffer. A late connect must replay the WHOLE settled cycle from
        # event 0 (the generator closes once done, so this stream ends).
        print(f"pre-warm: verifying the SSE replay buffer for {desk_id}...")
        event_count = 0
        saw_result = False
        try:
            with client.stream(
                "GET",
                f"{BASE_URL}/api/desk/{desk_id}/stream",
                timeout=STREAM_VERIFY_TIMEOUT_SECONDS,
            ) as stream_resp:
                if stream_resp.status_code != 200:
                    print(
                        "pre-warm: FAILED — /stream returned HTTP "
                        f"{stream_resp.status_code}"
                    )
                    return 1
                for line in stream_resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event_count += 1
                    event = json.loads(line[len("data: "):])
                    if event.get("type") == "result":
                        saw_result = True
        except httpx.HTTPError as exc:
            print(f"pre-warm: FAILED — replay verification errored: {exc!r}")
            return 1
        if event_count == 0:
            print("pre-warm: FAILED — the replay buffer is EMPTY")
            return 1
        if not saw_result:
            print(
                "pre-warm: FAILED — the replay buffer has no terminal "
                "result event"
            )
            return 1
        print(
            f"pre-warm: replay verified — {event_count} buffered events, "
            "terminal result present (cold open replays from event 0)"
        )

    result = report["result"]
    print(f"pre-warm: cycle done — status={result['status']}, "
          f"pnl={result['pnl']}, comparison_mode={result['comparison_mode']}")
    print(f"pre-warm: policy breaches (code-computed): {report['policy_breaches']}")
    print(f"pre-warm: auditor [{report['auditor_source']}]: {report['auditor_line']}")
    print()
    print("Demo URLs (replay is instant — the full real cycle is buffered):")
    print(f"  desk:  {FRONTEND_URL}/desk/{desk_id}")
    print(f"  close: {FRONTEND_URL}/close/{desk_id}")
    print()
    print("Do NOT restart the backend now — the replay buffer is in-memory.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
