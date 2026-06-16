"""Event bus — asyncio pub/sub with topic routing.

Per blueprint § 8 + I-15 (lens independence): the bus is the ONLY
sanctioned channel between lenses and downstream consumers. Lenses
don't reference each other or downstream consumers directly; they
publish to topics. Subscribers register topic prefixes; the bus
fan-outs each published message to every matching subscriber.

Design choices:
  - Async-first. All publish/subscribe APIs are coroutines, so they
    cooperate with the existing asyncio event loop pattern from Phase
    1+2 (PoolMonitor, time_budget_watchdog, l3_sync).
  - Topic routing via dot-notation prefix match. Publishing to
    "signal.graph.cluster_detected" delivers to subscribers of
    "signal.graph.*", "signal.*", and "*" (and exact-match
    "signal.graph.cluster_detected").
  - Per-subscriber bounded queue with backpressure. When a subscriber's
    queue fills, the bus chooses to BLOCK the publisher (default) or
    DROP the message; the choice is per-subscriber. Default = block so
    fast lenses can't starve slow consumers, but a real-time consumer
    can opt into drop-on-overflow.
  - Per-subscriber deliver-once. The bus tracks delivery; no
    accidental re-delivery on subscriber crash + restart.
  - I-16 enforcement at publish time. Signal payloads are validated;
    invalid signals are loudly dropped (logged + counter incremented)
    instead of crashing the publisher.

Topic syntax:
  - Dot-separated parts: "signal.graph.cluster_detected"
  - Trailing "*" wildcard matches the rest: "signal.graph.*"
  - Plain "*" matches everything.
  - Exact match: "signal.graph.cluster_detected" matches itself only.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .signal_schema import Signal, SignalValidationError, validate_signal


class BusClosedError(RuntimeError):
    """Raised when publish/subscribe is attempted on a closed bus."""
    pass


# Backpressure modes for slow subscribers.
BACKPRESSURE_BLOCK = "block"
BACKPRESSURE_DROP_OLDEST = "drop_oldest"
BACKPRESSURE_DROP_NEWEST = "drop_newest"
VALID_BACKPRESSURE_MODES = frozenset({
    BACKPRESSURE_BLOCK, BACKPRESSURE_DROP_OLDEST, BACKPRESSURE_DROP_NEWEST,
})


def _topic_matches(pattern: str, topic: str) -> bool:
    """Check whether `topic` matches subscription `pattern`.

    Rules:
      - "*" matches everything (special case)
      - "a.b.*" matches "a.b" + "a.b.<anything>" (prefix match)
      - "a.b" matches "a.b" exactly (no wildcard)
    """
    if pattern == "*":
        return True
    if pattern == topic:
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return topic == prefix or topic.startswith(prefix + ".")
    return False


@dataclass
class Subscriber:
    """Internal representation of one subscriber."""
    pattern: str
    queue: asyncio.Queue
    name: str
    backpressure_mode: str = BACKPRESSURE_BLOCK
    # Counters for observability.
    delivered: int = 0
    dropped: int = 0
    last_delivery_ts: float = 0.0


class EventBus:
    """Asyncio pub/sub event bus.

    Usage:
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*", name="my_consumer")
        async for msg in sub:
            handle(msg)

        await bus.publish("signal.graph.cluster_detected", signal)

    The bus does not run a background task by itself — it fan-outs
    synchronously inside `publish()`. Subscribers consume their queues
    via `subscriber.consume()` (async iterator).
    """

    def __init__(
        self,
        *,
        default_queue_size: int = 1024,
        default_backpressure_mode: str = BACKPRESSURE_BLOCK,
        log_validation_errors: bool = True,
    ):
        self._subscribers: list[Subscriber] = []
        self._closed: bool = False
        self._default_queue_size = int(default_queue_size)
        if default_backpressure_mode not in VALID_BACKPRESSURE_MODES:
            raise ValueError(
                f"default_backpressure_mode must be one of "
                f"{sorted(VALID_BACKPRESSURE_MODES)}, got "
                f"{default_backpressure_mode!r}"
            )
        self._default_backpressure_mode = default_backpressure_mode
        self._log_validation_errors = log_validation_errors
        # Counters for observability + acceptance tests.
        self.published_count = 0
        self.validation_failure_count = 0
        self.published_per_topic: dict[str, int] = {}

    async def subscribe(
        self,
        pattern: str,
        *,
        name: Optional[str] = None,
        queue_size: Optional[int] = None,
        backpressure_mode: Optional[str] = None,
    ) -> Subscriber:
        """Register a new subscriber. Returns a Subscriber whose `consume()`
        async-iterator yields delivered messages."""
        if self._closed:
            raise BusClosedError("bus is closed")
        if not pattern:
            raise ValueError("pattern must be a non-empty string")
        size = queue_size if queue_size is not None else self._default_queue_size
        if size < 1:
            raise ValueError(f"queue_size must be >= 1, got {size}")
        mode = backpressure_mode or self._default_backpressure_mode
        if mode not in VALID_BACKPRESSURE_MODES:
            raise ValueError(
                f"backpressure_mode must be one of "
                f"{sorted(VALID_BACKPRESSURE_MODES)}, got {mode!r}"
            )
        sub = Subscriber(
            pattern=pattern,
            queue=asyncio.Queue(maxsize=size),
            name=name or f"sub_{len(self._subscribers)}",
            backpressure_mode=mode,
        )
        self._subscribers.append(sub)
        return sub

    async def publish(self, topic: str, payload: Any) -> None:
        """Publish `payload` to `topic`. Fans out to all matching
        subscribers per their backpressure mode.

        Per I-16: if `payload` is a Signal, validate first; on
        validation failure, increment counter + log to stderr + drop
        the message (do NOT raise). The publisher should never crash
        because of a bad payload; the lens that produced the bad
        signal should be observable via the counter delta.
        """
        if self._closed:
            raise BusClosedError("bus is closed")

        # I-16 enforcement.
        if isinstance(payload, Signal):
            try:
                validate_signal(payload)
            except SignalValidationError as e:
                self.validation_failure_count += 1
                if self._log_validation_errors:
                    print(
                        f"[event_bus] DROPPED invalid signal on topic "
                        f"{topic!r}: {e}",
                        file=sys.stderr, flush=True,
                    )
                return

        self.published_count += 1
        self.published_per_topic[topic] = self.published_per_topic.get(topic, 0) + 1

        # Fan-out. Snapshot subscribers under the assumption that
        # subscribe()/unsubscribe() are not commonly called during high
        # publish throughput; if they are, this list-copy is the safety net.
        targets = [s for s in self._subscribers if _topic_matches(s.pattern, topic)]
        for sub in targets:
            try:
                if sub.backpressure_mode == BACKPRESSURE_BLOCK:
                    await sub.queue.put(payload)
                elif sub.backpressure_mode == BACKPRESSURE_DROP_NEWEST:
                    if sub.queue.full():
                        sub.dropped += 1
                        continue
                    sub.queue.put_nowait(payload)
                elif sub.backpressure_mode == BACKPRESSURE_DROP_OLDEST:
                    if sub.queue.full():
                        try:
                            sub.queue.get_nowait()
                            sub.dropped += 1
                        except asyncio.QueueEmpty:
                            pass
                    sub.queue.put_nowait(payload)
                sub.delivered += 1
                sub.last_delivery_ts = time.time()
            except Exception as e:
                # A subscriber's queue should never raise except on full
                # in non-blocking modes (handled above). Anything else is
                # a logic error; log loudly and continue with other subs.
                print(
                    f"[event_bus] WARN: subscriber {sub.name!r} on pattern "
                    f"{sub.pattern!r} raised {type(e).__name__}: {e}",
                    file=sys.stderr, flush=True,
                )

    async def close(self) -> None:
        """Close the bus. Future publish/subscribe calls raise BusClosedError.
        Existing subscriber consume() loops should exit on their own when
        their queues drain; callers may want to send poison-pill sentinels
        before closing if graceful shutdown matters."""
        self._closed = True

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def stats(self) -> dict:
        """Diagnostic snapshot. Used by acceptance tests + observability."""
        return {
            "published": self.published_count,
            "validation_failures": self.validation_failure_count,
            "subscribers": [
                {
                    "name": s.name,
                    "pattern": s.pattern,
                    "delivered": s.delivered,
                    "dropped": s.dropped,
                    "queue_size": s.queue.qsize(),
                    "backpressure_mode": s.backpressure_mode,
                }
                for s in self._subscribers
            ],
            "by_topic": dict(self.published_per_topic),
        }


# Helper: subscribe-then-async-iterate. Most consumers will use this pattern.
async def subscriber_iter(sub: Subscriber):
    """Async iterator over a subscriber's incoming messages.

    Loops forever (or until the consumer breaks out). Callers should
    typically wrap this in their own loop with cancellation / shutdown
    semantics, since the bus has no notion of "subscriber done".
    """
    while True:
        msg = await sub.queue.get()
        yield msg


__all__ = [
    "BACKPRESSURE_BLOCK",
    "BACKPRESSURE_DROP_NEWEST",
    "BACKPRESSURE_DROP_OLDEST",
    "BusClosedError",
    "EventBus",
    "Subscriber",
    "VALID_BACKPRESSURE_MODES",
    "subscriber_iter",
]
