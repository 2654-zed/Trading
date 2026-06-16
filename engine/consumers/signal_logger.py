"""Signal logger — JSONL sink for raw Signals.

Sub-phase 3.1 stub. Subscribes to `signal.*` on the bus and writes
each received Signal to a JSONL file. Provides a per-type emission
count for acceptance-test assertions.

This is NOT the long-term consumer — sub-phase 3.2 introduces the
synthesis engine, and sub-phases 3.3+ add the orchestrator. But the
signal logger pattern is the right shape for an OBSERVABILITY consumer
that just records what's flowing through the bus.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

from ..core.event_bus import EventBus, Subscriber
from ..core.signal_schema import Signal


class SignalLoggerConsumer:
    """Subscribe to `signal.*` and write JSONL.

    Run via `await consumer.run(bus)`. Cancellable via standard asyncio
    task cancellation; on shutdown, flushes pending writes via the
    context-manager exit.

    Stats are computed in-process for acceptance-test assertions:
      - total_received: int
      - by_lens: Counter[lens]
      - by_type: Counter[(lens, type)]
    """

    def __init__(
        self,
        output_path: Path,
        *,
        pattern: str = "signal.*",
        name: str = "signal_logger",
        verbose: bool = False,
    ):
        self._output_path = Path(output_path)
        self._pattern = pattern
        self._name = name
        self._verbose = verbose
        self._handle = None
        self.total_received = 0
        self.by_lens: Counter[str] = Counter()
        self.by_type: Counter[tuple[str, str]] = Counter()

    async def run(
        self,
        bus: EventBus,
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        sub: Subscriber = await bus.subscribe(self._pattern, name=self._name)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._output_path.open("a", encoding="utf-8") as handle:
                self._handle = handle
                while True:
                    try:
                        msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if stop_event is not None and stop_event.is_set():
                            # Grace-period drain for late-arriving
                            # signals (rare for this sink, since lenses
                            # exit before stop_event fires).
                            empty_streak = 0
                            while empty_streak < 3:
                                try:
                                    msg = await asyncio.wait_for(
                                        sub.queue.get(), timeout=0.5,
                                    )
                                    self._on_message(msg, handle)
                                    empty_streak = 0
                                except asyncio.TimeoutError:
                                    empty_streak += 1
                            return
                        continue
                    self._on_message(msg, handle)
        finally:
            self._handle = None

    def _on_message(self, msg, handle) -> None:
        if not isinstance(msg, Signal):
            # Skip non-Signal payloads (the bus is general).
            if self._verbose:
                print(
                    f"[signal_logger] skipping non-Signal payload: "
                    f"{type(msg).__name__}",
                    file=sys.stderr, flush=True,
                )
            return
        line = json.dumps(msg.to_dict(), default=str)
        handle.write(line)
        handle.write("\n")
        handle.flush()
        self.total_received += 1
        self.by_lens[msg.lens] += 1
        self.by_type[(msg.lens, msg.type)] += 1
        if self._verbose:
            print(
                f"[signal_logger] +1 {msg.lens}/{msg.type}  "
                f"strength={msg.strength:.2f}  confidence={msg.confidence:.2f}",
                file=sys.stderr, flush=True,
            )


__all__ = ["SignalLoggerConsumer"]
