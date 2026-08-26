"""RecordedAtlasClient — the recorded-replay Atlas rail (S9, ADR 0005).

Subclass-at-transport, per the Recorded-Mode Engine plan: this class
overrides ONLY the transport layer (`_run_json`, `_run_read_only`,
`search`), so every write-path method (`verify`, `confirm_price`,
`create_order`, `pay`, `order_status`, `auth_status`, …) runs through
the IDENTICAL inherited parse logic from client.py — the same-parser
guarantee, with zero edits to the file proven live. Two small control
overrides serve replay hygiene and are documented on the methods:
`reset_ticketing_cache` (per-cycle cursor reset) and
`poll_until_ticketed` (no clock/random/sleep — the recording IS the
timeline).

The recording: `backend/data/recorded/booking_envelopes.json` — raw
JSON-lines envelopes captured live from the sandbox (Slice 0). The
companion `manifest.json` is the honesty register: it names every step
the replay serves, flags any RECONSTRUCTED step (a step the capture
lost but the order's own later state proves — never a TICKETED
envelope, which is only ever served when genuinely captured), and
carries the wire disclosure the loop puts on the meta event.

Matching: normalized command verb + sequence index within the manifest
script. An unmatched call raises a typed AtlasError("NO_RECORDING") —
fail closed: nothing is served, never wrong data.

HONESTY RULE (absolute, ADR 0005): recorded is NEVER labeled live. This
client exposes `mode_label = "recorded"`; the loop's wire label becomes
"recorded ticketing (replay)" whenever it would otherwise say "live
ticketing". No subprocess, no clock, no random, no sleep anywhere here.
"""
from __future__ import annotations

import json
from collections import deque
from datetime import date
from pathlib import Path

from app.atlas.client import AtlasClient, AtlasError
from app.models import OrderStatus

# The capture artifacts live with the backend data (Slice 0). Recorded
# mode fails closed if either is missing — a deployment that claims the
# recording must carry the recording.
RECORDING_PATH = (
    Path(__file__).resolve().parents[2]
    / "data" / "recorded" / "booking_envelopes.json"
)
MANIFEST_PATH = RECORDING_PATH.parent / "manifest.json"


def _verb(args: list[str]) -> str:
    """Normalized command verb: the leading non-flag tokens, space-joined.
    ["order", "status", "--order-no", ...] -> "order status";
    ["search", "--origin", ...] -> "search". Flags (and everything after
    the first flag) never participate — matching is verb + sequence,
    never argument values."""
    leading = []
    for token in args:
        if token.startswith("--"):
            break
        leading.append(token)
    return " ".join(leading)


class RecordedAtlasClient(AtlasClient):
    """Replay client: identical public contract, recorded transport.

    Serves the envelopes the manifest script lists — captured entries by
    `seq` reference into the JSON-lines recording, reconstructed entries
    inline (flagged in the manifest, disclosed on the wire). Anything
    the script does not cover fails closed with NO_RECORDING.
    """

    # The loop probes this attribute (getattr precedent: the
    # reset_ticketing_cache probe) to switch the wire label — recorded
    # NEVER wears the live label.
    mode_label = "recorded"

    def __init__(
        self,
        recording_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ):
        super().__init__()
        recording_file = Path(recording_path or RECORDING_PATH)
        manifest_file = Path(manifest_path or MANIFEST_PATH)
        self.manifest = self._load_manifest(manifest_file)
        captured = self._load_recording(recording_file)
        # verb -> pristine scripted envelope list (script order = replay
        # order). The live per-cycle queues are rewound from this snapshot
        # on every reset_ticketing_cache call.
        pristine: dict[str, list[dict]] = {}
        for entry in self.manifest.get("script", []):
            verb = entry["verb"]
            if "seq" in entry:
                envelope = captured.get(entry["seq"])
                if envelope is None:
                    # Manifest references an envelope the recording does
                    # not hold — fail closed at construction, loudly.
                    raise AtlasError(
                        "NO_RECORDING",
                        f"manifest script seq {entry['seq']} missing "
                        "from the recording",
                    )
            else:
                # Reconstructed step — inline in the manifest, flagged
                # there and disclosed on the wire (never a TICKETED
                # envelope; the honesty rule is enforced in the manifest,
                # which only ever marks CAPTURED ticketing as such).
                envelope = entry["envelope"]
            pristine.setdefault(verb, []).append(envelope)
        self._pristine = pristine
        self._rewind()
        # The wire disclosure the loop reads for the meta event: states
        # replay, composite status and any reconstructed steps.
        self.gate_disclosure = self.manifest.get(
            "wire_disclosure",
            "recorded Atlas replay \u2014 envelopes served from the "
            "capture, never live",
        )

    # ------------------------------------------------------------------
    # Loading.
    # ------------------------------------------------------------------

    @staticmethod
    def _load_recording(path: Path) -> dict[int, dict]:
        """JSON-lines capture -> {seq: envelope}. Blank lines and
        non-entry lines are skipped; a malformed line fails closed."""
        envelopes: dict[int, dict] = {}
        if not path.exists():
            raise AtlasError("NO_RECORDING", f"recording missing: {path.name}")
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    envelopes[int(entry["seq"])] = entry["envelope"]
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    raise AtlasError(
                        "NO_RECORDING", "recording line malformed"
                    ) from exc
        return envelopes

    @staticmethod
    def _load_manifest(path: Path) -> dict:
        if not path.exists():
            raise AtlasError("NO_RECORDING", f"manifest missing: {path.name}")
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise AtlasError("NO_RECORDING", "manifest malformed") from exc

    # ------------------------------------------------------------------
    # Transport overrides — the ONLY behavioral surface that differs.
    # ------------------------------------------------------------------

    def _take(self, verb: str) -> dict:
        queue = self._script.get(verb)
        if not queue:
            # Fail closed: an unscripted call gets NOTHING — never a
            # guess, never a synthesized answer (typed code, branch on
            # code upstream; the raw verb stays server-side in tests).
            raise AtlasError("NO_RECORDING", f"no recorded envelope for {verb}")
        return queue.popleft()

    def _run_json(
        self,
        args: list[str],
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Recorded transport: serve the next scripted envelope for the
        verb. `stdin` (one-time passenger delivery) is consumed and
        ignored — nothing is ever sent anywhere. No subprocess, no
        timeout, no retry."""
        del stdin, timeout
        return self._take(_verb(args))

    def _run_read_only(self, args: list[str], timeout: float | None = None) -> dict:
        """ONE envelope per call. The inherited identical-once retry is
        LIVE-transport discipline (retryable=true authorizes re-running a
        command); replaying a recorded envelope is idempotent by
        construction, so the script's next entry IS the answer."""
        del timeout
        return self._take(_verb(args))

    def search(self, origin: str, dest: str, dep: date, pax: int) -> list:
        """Recorded search: the envelope rides the SAME inherited parser
        (`_offers_from_envelope` -> `map_offer`) the live client uses —
        identical Offer list, identical skip-and-continue on malformed
        offers, identical cheapest-first sort."""
        del origin, dest, dep, pax  # matching is verb + sequence only
        return self._offers_from_envelope(self._take("search"))

    # ------------------------------------------------------------------
    # Control overrides — replay hygiene, documented one by one.
    # ------------------------------------------------------------------

    def poll_until_ticketed(
        self,
        order_no: str,
        deadline: float = 90.0,
        base_delay: float = 2.0,
    ) -> tuple[OrderStatus, bool]:
        """Recorded poll: the recording IS the timeline — no clock, no
        sleep, no jitter (the inherited backoff is live-transport
        pacing). Queries the replayed `order status` sequence until
        TICKETED; the script is finite, so exhaustion raises NO_RECORDING
        (fail closed) instead of looping. `ticket_asserted` stays the
        inherited meaning: True ONLY on a real TICKETED envelope."""
        del deadline, base_delay
        status = self.order_status(order_no)
        while not status.ticketed:
            status = self.order_status(order_no)
        return status, True

    def reset_ticketing_cache(self) -> None:
        """Per-cycle reset (fix-7 hook): besides the inherited probe
        cache, EVERY replay cursor resets — each desk cycle replays the
        recording from its first scripted envelope. This is what makes
        consecutive cycles deterministic and byte-identical."""
        super().reset_ticketing_cache()
        self._rewind()

    def _rewind(self) -> None:
        """(Re)build the live verb queues from the pristine script — a
        fresh cursor for every desk cycle."""
        self._script: dict[str, deque[dict]] = {
            verb: deque(envelopes) for verb, envelopes in self._pristine.items()
        }
