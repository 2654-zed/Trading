"""ReplayClock — temporal replay over historical L3 data (sub-phase 3.3).

Resolves UNK-014: the sub-phase 3.2 lenses scanned the *current full
state* of static L3 data, so every scan emitted identical signals and
the orchestrator's per-window scores had zero variance — making regime
detection + conflict detection meaningless.

The ReplayClock turns ~50 days of historical L3 data into a time series.
Each lens (in replay mode) iterates the clock's windows, queries data
"as of" the simulated cursor, and stamps emitted Signals with the
SIMULATED timestamp. The orchestrator windows by simulated time
(event-time), so signals from all lenses for the same simulated window
align regardless of wall-clock skew between the independent lens tasks.

Design (per D-029):
  - Each lens owns its own ReplayClock instance (same start/end/slice).
    No shared mutable state → no races between the concurrent lens tasks.
  - The clock yields a deterministic sequence of (window_start, window_end)
    tuples covering [start_ts, end_ts] in slice_seconds steps.
  - Signals are stamped at window_end (the "as of" point).
  - $0 CU: all data is the local L3 SQLite copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class ReplayWindow:
    """One simulated time window. `as_of` is the point Signals emitted
    for this window should be timestamped at (== end)."""
    start: float
    end: float

    @property
    def as_of(self) -> float:
        return self.end

    @property
    def duration(self) -> float:
        return self.end - self.start


class ReplayClock:
    """Yields a deterministic sequence of simulated time windows.

    Usage (in a lens run loop):
        clock = ReplayClock(start_ts, end_ts, slice_seconds=3600)
        for window in clock.windows():
            await self._scan_window(bus, window)
    """

    def __init__(
        self,
        start_ts: float,
        end_ts: float,
        *,
        slice_seconds: float = 3600.0,
        lookback_seconds: Optional[float] = None,
    ):
        if end_ts <= start_ts:
            raise ValueError(
                f"end_ts ({end_ts}) must be > start_ts ({start_ts})"
            )
        if slice_seconds <= 0:
            raise ValueError(f"slice_seconds must be > 0, got {slice_seconds}")
        self.start_ts = float(start_ts)
        self.end_ts = float(end_ts)
        self.slice_seconds = float(slice_seconds)
        # How far back a lens should look when computing "as of" stats.
        # Defaults to one slice. A lens may use this to size its
        # current-window analysis (e.g. flow buckets in [as_of - lookback,
        # as_of]).
        self.lookback_seconds = (
            float(lookback_seconds)
            if lookback_seconds is not None
            else self.slice_seconds
        )
        self._current_index = 0

    @property
    def total_windows(self) -> int:
        span = self.end_ts - self.start_ts
        # Number of full slices, plus a partial trailing window.
        import math
        return max(1, math.ceil(span / self.slice_seconds))

    @property
    def current_index(self) -> int:
        return self._current_index

    def windows(self) -> Iterator[ReplayWindow]:
        """Generator yielding ReplayWindow tuples covering the range.

        The final window is clamped to end_ts (may be shorter than
        slice_seconds). Advances `current_index` as a side effect for
        progress tracking.
        """
        cursor = self.start_ts
        self._current_index = 0
        while cursor < self.end_ts:
            window_end = min(cursor + self.slice_seconds, self.end_ts)
            yield ReplayWindow(start=cursor, end=window_end)
            self._current_index += 1
            cursor = window_end

    @classmethod
    def from_data_range(
        cls,
        start_ts: float,
        end_ts: float,
        *,
        target_windows: int = 200,
        lookback_seconds: Optional[float] = None,
    ) -> "ReplayClock":
        """Build a clock that divides [start, end] into ~target_windows
        slices. Useful when you want a fixed window count regardless of
        the absolute data span.
        """
        span = end_ts - start_ts
        slice_seconds = max(span / max(target_windows, 1), 1.0)
        return cls(
            start_ts, end_ts,
            slice_seconds=slice_seconds,
            lookback_seconds=lookback_seconds,
        )


__all__ = ["ReplayClock", "ReplayWindow"]
