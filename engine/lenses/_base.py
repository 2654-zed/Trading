"""Lens abstract base class.

Per I-15 (lens independence): the base class deliberately does NOT
expose any introspection of other lenses. The only outbound interface
is `bus.publish("signal.{lens_name}.{signal_type}", signal)`.

Each lens overrides `async def run(self, bus, *, stop_event=None)`,
which loops emitting signals until cancelled (or until `stop_event`
is set, for testable shutdown).

Lens implementations:
  - MUST set `lens_label` to one of the canonical VALID_LENSES values
    (validated at construction)
  - MUST emit Signals via `self.emit(...)` so the topic format is
    consistent (`signal.{lens_label}.{signal_type}`)
  - SHOULD use `self.emit_at(...)` when they have a logical timestamp
    different from `time.time()` (e.g. block timestamp from chain head)
  - MAY read raw blockchain state, L3 corpus, on-chain RPC, etc. via
    injected adapter interfaces (no lens-to-lens or lens-to-orchestrator
    reads)
"""

from __future__ import annotations

import abc
import asyncio
import time
from typing import Any, Optional

from ..core.event_bus import EventBus
from ..core.signal_schema import (
    VALID_LENSES,
    Signal,
    new_signal_id,
    validate_signal,
)


class Lens(abc.ABC):
    """Abstract base for all engine lenses.

    Concrete lenses subclass this + override `run`. The base class
    provides the standard emit helper that builds + validates Signals
    and publishes to the canonical topic shape.
    """

    # Subclasses must override (or pass via __init__).
    lens_label: str = ""

    def __init__(self, lens_label: Optional[str] = None):
        if lens_label is not None:
            # Class-level default can be overridden per-instance for tests.
            self.lens_label = lens_label
        if not self.lens_label:
            raise ValueError(
                f"{type(self).__name__} must set lens_label to one of "
                f"{sorted(VALID_LENSES)}"
            )
        if self.lens_label not in VALID_LENSES:
            raise ValueError(
                f"{type(self).__name__}.lens_label = {self.lens_label!r} is "
                f"not in VALID_LENSES {sorted(VALID_LENSES)}"
            )
        # Counters for observability + acceptance tests.
        self.emitted_count = 0
        self.emit_failures = 0
        # Temporal-replay hook (sub-phase 3.3): when set, emit() stamps
        # Signals with this simulated timestamp instead of time.time().
        # Static-mode lenses leave it None.
        self._forced_timestamp: Optional[float] = None

    async def emit(
        self,
        bus: EventBus,
        signal_type: str,
        *,
        strength: float,
        confidence: float,
        time_horizon: str = "short",
        metadata: Optional[dict[str, Any]] = None,
        vector: Optional[list[float]] = None,
        timestamp: Optional[float] = None,
    ) -> Signal:
        """Build + validate + publish a Signal.

        Helper used by concrete lens implementations to keep the topic
        format consistent and the validation surface centralized.
        Returns the published Signal so the caller can inspect it
        (useful for tests + per-lens logging).

        Per I-16: validation happens here AND at publish time (the
        EventBus.publish path also validates). Two validations are
        cheap; missing one is catastrophic.
        """
        ts = timestamp
        if ts is None:
            ts = self._forced_timestamp
        if ts is None:
            ts = time.time()
        sig = Signal(
            id=new_signal_id(),
            timestamp=ts,
            lens=self.lens_label,
            type=signal_type,
            strength=float(strength),
            confidence=float(confidence),
            time_horizon=time_horizon,
            metadata=dict(metadata or {}),
            vector=vector,
        )
        try:
            validate_signal(sig)
        except Exception:
            self.emit_failures += 1
            raise
        topic = f"signal.{self.lens_label}.{signal_type}"
        await bus.publish(topic, sig)
        self.emitted_count += 1
        return sig

    async def scan_replay_window(self, bus: EventBus, window) -> None:
        """Run a single replay scan for the given ReplayWindow.

        Used by the lockstep replay driver (sub-phase 3.3): all lenses
        scan the SAME window before any advances to the next, so the
        orchestrator's event-time watermark advances monotonically and
        windows don't fragment from concurrent lenses running at
        different wall-clock speeds.

        Relies on the subclass exposing `_scan_once(bus, *, as_of_ts=...)`
        and a `_scan_count` counter.
        """
        self._forced_timestamp = window.as_of
        try:
            await self._scan_once(bus, as_of_ts=window.as_of)  # type: ignore[attr-defined]
        finally:
            self._forced_timestamp = None
        self._scan_count = getattr(self, "_scan_count", 0) + 1

    @abc.abstractmethod
    async def run(
        self,
        bus: EventBus,
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Run the lens. Loops until cancelled OR `stop_event` is set.

        Implementations should:
          - Read raw blockchain state / L3 corpus via injected adapters
          - Compute lens-specific signals
          - Emit via `await self.emit(bus, signal_type, ...)`
          - Check `stop_event.is_set()` at natural loop boundaries OR
            rely on asyncio.CancelledError for shutdown

        Concrete lenses must NOT swallow CancelledError. The outer
        orchestrator uses cancellation as the standard shutdown signal.
        """
        raise NotImplementedError


__all__ = ["Lens"]
