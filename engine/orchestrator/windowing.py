"""Shared event-time windowing primitive (sub-phase 3.3).

The synthesis, regime, and conflict engines all need to buffer Signals
into fixed-width time windows and process each window once it's closed.
Rather than reimplement that four times, `WindowAccumulator` provides it:

  - Buckets signals by `floor(signal.timestamp / window_seconds)`.
  - Tracks an event-time watermark (max signal.timestamp seen).
  - Closes a window once the watermark advances past window_end + grace.
  - `flush()` closes all remaining windows (for shutdown).

Event-time (not wall-clock) so it works in temporal-replay mode where
simulated time races ahead of wall-clock. Same windowing math as the
Orchestrator so all engines' windows align.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClosedWindow:
    window_start: float
    window_end: float
    signals: list = field(default_factory=list)


class WindowAccumulator:
    def __init__(self, window_seconds: float, *, grace_fraction: float = 0.5,
                 max_pending_windows: int = 64):
        self.window_seconds = float(window_seconds)
        self.grace = self.window_seconds * grace_fraction
        self._buckets: dict[float, list] = {}
        self._max_seen_ts: float = 0.0
        self._max_pending = max_pending_windows
        self.dropped_outside_window = 0

    def window_start_for(self, ts: float) -> float:
        return math.floor(ts / self.window_seconds) * self.window_seconds

    def add(self, signal: Any) -> list[ClosedWindow]:
        """Add a signal; return any windows that closed as a result."""
        ts = getattr(signal, "timestamp", None)
        if ts is None:
            return []
        if ts > self._max_seen_ts:
            self._max_seen_ts = ts
        ws = self.window_start_for(ts)
        if ws not in self._buckets and len(self._buckets) >= self._max_pending:
            # Too many open windows: drop signals for brand-new windows.
            self.dropped_outside_window += 1
            return []
        self._buckets.setdefault(ws, []).append(signal)
        return self._close_expired()

    def _close_expired(self) -> list[ClosedWindow]:
        closed: list[ClosedWindow] = []
        for ws in sorted(self._buckets.keys()):
            if self._max_seen_ts >= ws + self.window_seconds + self.grace:
                closed.append(ClosedWindow(
                    window_start=ws,
                    window_end=ws + self.window_seconds,
                    signals=self._buckets.pop(ws),
                ))
        return closed

    def flush(self) -> list[ClosedWindow]:
        """Close all remaining open windows (shutdown)."""
        closed = [
            ClosedWindow(ws, ws + self.window_seconds, self._buckets[ws])
            for ws in sorted(self._buckets.keys())
        ]
        self._buckets.clear()
        return closed


__all__ = ["WindowAccumulator", "ClosedWindow"]
