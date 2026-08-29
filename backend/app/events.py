"""The typed domain-event sink (Gate 3 §events). In-process pub/sub: the
agent loop publishes named desk moments, the Waybot subscribes.

This is the ONE place every announced moment is enumerated. Single-process
today (the desk registry is in-memory), so a subscriber callback has no
polling lag. Multi-process later swaps this for an SSE/webhook consumer
without touching the loop.

Delivery is FIRE-AND-FORGET with per-subscriber try/except (symmetric
isolation): a subscriber raising cannot break the cycle, and a cycle
fault cannot break another subscriber. S1 ships the sink itself; the loop
does not publish yet (that lands with S3/S5).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

DeskEventType = Literal[
    "travelers_complete",
    "pending_approval",
    "ticketed",
    "disruption",
    "close_summary",
    # provenance / honesty-register events
    "pinned_resume",
    "fallback_used",
]


@dataclass(frozen=True)
class DeskEvent:
    """One announced desk moment. `payload` is typed per-type by construction
    (never raw PII — masked upstream before it reaches the sink)."""

    type: DeskEventType
    desk_id: str
    payload: dict


DeskEventHandler = Callable[[DeskEvent], Awaitable[None]]


class EventSink:
    """In-process fan-out of DeskEvents to registered async subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[DeskEventHandler] = []

    def subscribe(self, handler: DeskEventHandler) -> None:
        """Register an async handler. Idempotent per handler object."""
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: DeskEventHandler) -> None:
        """Drop a handler (e.g. a torn-down bot). No-op if not registered,
        so a double-unsubscribe never raises into the caller."""
        try:
            self._subscribers.remove(handler)
        except ValueError:
            pass

    def publish(self, event: DeskEvent) -> None:
        """Fan the event out to every subscriber, fire-and-forget.

        Each handler is scheduled as its own task and wrapped so a raise
        is logged and swallowed — one bad subscriber never breaks the
        publisher or the other subscribers (symmetric isolation). Safe to
        call from sync code (schedules onto the running loop); if no loop
        is running the delivery is skipped with a warning rather than
        raising into the caller."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "EventSink.publish(%s) with no running loop — dropped",
                event.type,
            )
            return
        for handler in list(self._subscribers):
            # Scheduling itself is wrapped too, so even a create_task fault
            # (e.g. a loop mid-shutdown) cannot break the publisher or the
            # other subscribers — symmetric isolation holds end to end.
            try:
                loop.create_task(self._deliver(handler, event))
            except Exception:  # noqa: BLE001 — isolate a scheduling fault
                logger.exception(
                    "EventSink failed to schedule %s (isolated)", event.type
                )

    @staticmethod
    async def _deliver(handler: DeskEventHandler, event: DeskEvent) -> None:
        try:
            await handler(event)
        except Exception:  # noqa: BLE001 — isolate a faulty subscriber
            logger.exception(
                "EventSink subscriber raised on %s (isolated)", event.type
            )


# The process-wide sink singleton (S5). It lived in `app/main.py` through
# S4, but the agent loop now publishes to it too (pending_approval /
# pinned_resume) and `routes.py` builds the AGENT — main.py imports routes,
# so the singleton has to live BELOW both of them. `app.main.SINK` stays a
# valid name (main re-imports it), and there is still exactly one sink.
SINK = EventSink()
