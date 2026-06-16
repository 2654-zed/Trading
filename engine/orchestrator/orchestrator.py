"""Orchestrator (sub-phase 3.3 v2 — regime-aware + non-linear).

Evolves the 3.2 static-weights skeleton into an adjudicating engine:

  - Consumes `signal.*` (lens signals), `regime.window` (RegimeLabel),
    and `conflict.window` (ConflictSignal).
  - Routes to a regime-specific weight table (regime_weights.py).
  - Aggregation is BILINEAR in strength × confidence (per I-17) and
    additionally applies a NON-LINEAR conflict boost (conflicts = alpha):
        base   = Σ_lens  weight[lens] · max_strength[lens] · max_conf[lens]
        score  = base · (1 + CONFLICT_BOOST · mean_conflict_strength)
    This satisfies I-17 two independent ways (bilinear AND conflict-adjusted).

  - Join across the three topics uses a 2-window event-time lag: a
    window is emitted once the watermark has advanced two full windows
    past it, by which point its RegimeLabel + ConflictSignals (emitted by
    the sibling engines on the same watermark) have arrived.

Latency tracking + the WindowAggregate / AggregateLoggerConsumer surface
are preserved from 3.2.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
import re
from collections import defaultdict
from dataclasses import dataclass, field

_ADDR_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def _extract_addresses(metadata) -> set:
    """Recursively pull 0x-addresses from a signal's metadata (strings,
    lists, dicts). Used for entity-specific outcome attribution."""
    out: set = set()
    def walk(v):
        if isinstance(v, str):
            for m in _ADDR_RE.findall(v):
                out.add(m.lower())
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(metadata)
    return out
from pathlib import Path
from typing import Any, Optional

from ..core.event_bus import EventBus, Subscriber
from ..core.signal_schema import Signal
from .static_weights import get_lens_weights, validate_weights
from .regime_weights import get_regime_weights
from .regime_engine import RegimeLabel, RegimeEngine
from .conflict_engine import ConflictSignal, ConflictEngine


@dataclass(frozen=True)
class WindowAggregate:
    window_start: float
    window_end: float
    per_lens_max_strength: dict[str, float]
    per_lens_signal_count: dict[str, int]
    weights_used: dict[str, float]
    weighted_aggregate: float
    contributing_signal_ids: list[str]
    regime_label: str = "default"
    regime_confidence: float = 0.0
    base_score: float = 0.0
    conflict_count: int = 0
    conflict_boost: float = 0.0
    # Phase 4 run-2: addresses the window's signals fired on (for
    # entity-specific outcome attribution). Pool + token addrs mixed.
    entities: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "per_lens_max_strength": dict(self.per_lens_max_strength),
            "per_lens_signal_count": dict(self.per_lens_signal_count),
            "weights_used": dict(self.weights_used),
            "weighted_aggregate": self.weighted_aggregate,
            "contributing_signal_ids": list(self.contributing_signal_ids),
            "regime_label": self.regime_label,
            "regime_confidence": self.regime_confidence,
            "base_score": self.base_score,
            "conflict_count": self.conflict_count,
            "conflict_boost": self.conflict_boost,
            "entities": list(self.entities),
        }


class Orchestrator:
    AGGREGATE_TOPIC = "aggregate.window"
    SIGNAL_PATTERN = "signal.*"

    # Conflict boost coefficient: each unit of mean conflict strength adds
    # this fraction to the base score (non-linear adjustment per I-17).
    CONFLICT_BOOST = 0.5
    # Emit a window once the watermark is this many windows past it (lets
    # the regime/conflict for the window arrive first).
    EMIT_LAG_WINDOWS = 2

    def __init__(
        self,
        *,
        window_seconds: float = 300.0,
        regime_aware: bool = True,
        weights: Optional[dict[str, float]] = None,
        weighting_engine=None,
        name: str = "orchestrator",
        max_pending_windows: int = 64,
    ):
        self._window_seconds = float(window_seconds)
        self._regime_aware = regime_aware
        # Optional dynamic weighting (sub-phase 3.4b): when provided, the
        # orchestrator routes regime → learned weights via this engine
        # instead of the static regime_weights table.
        self._weighting_engine = weighting_engine
        # Fixed fallback weights (used when regime_aware is False OR no
        # regime label is available for a window).
        self._weights = dict(weights) if weights else get_lens_weights()
        validate_weights(self._weights)
        self._name = name
        # Per-window accumulation.
        # ws -> {lens: {"max_strength", "max_conf", "count", "ids"}}
        self._windows: dict[float, dict[str, dict]] = defaultdict(
            lambda: defaultdict(
                lambda: {"max_strength": 0.0, "max_conf": 0.0,
                         "count": 0, "ids": [], "entities": set()}
            )
        )
        self._regimes: dict[float, RegimeLabel] = {}
        self._conflicts: dict[float, list[ConflictSignal]] = defaultdict(list)
        self._latencies: list[float] = []
        self._max_seen_ts: float = 0.0
        self.total_signals_received = 0
        self.total_windows_emitted = 0
        self.signals_dropped_outside_window = 0
        self._max_pending = max_pending_windows

    @property
    def weights(self) -> dict[str, float]:
        return dict(self._weights)

    def latency_stats(self) -> dict:
        if not self._latencies:
            return {"count": 0, "p50_ms": None, "p95_ms": None,
                    "p99_ms": None, "max_ms": None}
        s = sorted(self._latencies)
        n = len(s)
        def pct(p):
            return s[min(int(n * p), n - 1)] * 1000.0
        return {"count": n, "p50_ms": pct(0.50), "p95_ms": pct(0.95),
                "p99_ms": pct(0.99), "max_ms": max(s) * 1000.0}

    def _window_start_for(self, ts: float) -> float:
        return math.floor(ts / self._window_seconds) * self._window_seconds

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sig_sub: Subscriber = await bus.subscribe(self.SIGNAL_PATTERN, name=self._name)
        regime_sub: Subscriber = await bus.subscribe(
            RegimeEngine.OUTPUT_TOPIC, name=f"{self._name}_regime")
        conflict_sub: Subscriber = await bus.subscribe(
            ConflictEngine.OUTPUT_TOPIC, name=f"{self._name}_conflict")
        try:
            while True:
                drained_any = False
                # Drain all three queues opportunistically.
                for sub in (regime_sub, conflict_sub, sig_sub):
                    try:
                        while True:
                            msg = sub.queue.get_nowait()
                            self._route(msg, time.time())
                            drained_any = True
                    except asyncio.QueueEmpty:
                        pass
                await self._emit_ready(bus)
                if not drained_any:
                    if stop_event is not None and stop_event.is_set():
                        await self._flush_all(bus)
                        return
                    await asyncio.sleep(0.05)
        finally:
            try:
                await self._flush_all(bus)
            except Exception:
                pass

    def _route(self, msg: Any, arrival_ts: float) -> None:
        if isinstance(msg, Signal):
            self._on_signal(msg, arrival_ts)
        elif isinstance(msg, RegimeLabel):
            self._regimes[msg.window_start] = msg
            if msg.window_end > self._max_seen_ts:
                self._max_seen_ts = msg.window_end
        elif isinstance(msg, ConflictSignal):
            self._conflicts[msg.window_start].append(msg)
            if msg.window_end > self._max_seen_ts:
                self._max_seen_ts = msg.window_end

    def _on_signal(self, msg: Any, arrival_ts: float) -> None:
        if not isinstance(msg, Signal):
            return
        self.total_signals_received += 1
        self._latencies.append(max(0.0, arrival_ts - msg.timestamp))
        if msg.timestamp > self._max_seen_ts:
            self._max_seen_ts = msg.timestamp
        ws = self._window_start_for(msg.timestamp)
        if ws not in self._windows and len(self._windows) >= self._max_pending:
            self.signals_dropped_outside_window += 1
            return
        b = self._windows[ws][msg.lens]
        if msg.strength > b["max_strength"]:
            b["max_strength"] = float(msg.strength)
        if msg.confidence > b["max_conf"]:
            b["max_conf"] = float(msg.confidence)
        b["count"] += 1
        b["ids"].append(msg.id)
        if msg.metadata:
            b["entities"].update(_extract_addresses(msg.metadata))

    async def _emit_ready(self, bus: EventBus) -> None:
        """Emit windows the watermark has advanced EMIT_LAG_WINDOWS past."""
        if not self._windows:
            return
        threshold = self._max_seen_ts - self.EMIT_LAG_WINDOWS * self._window_seconds
        for ws in sorted(self._windows.keys()):
            if ws + self._window_seconds <= threshold:
                await self._emit_window(bus, ws)

    async def _flush_all(self, bus: EventBus) -> None:
        for ws in sorted(self._windows.keys()):
            await self._emit_window(bus, ws)

    def _aggregate(self, ws: float):
        """Compute the WindowAggregate for window ws from accumulated state.
        Pure-ish (reads self caches). Returns WindowAggregate."""
        per_lens = self._windows.get(ws, {})
        per_lens_max: dict[str, float] = {}
        per_lens_conf: dict[str, float] = {}
        per_lens_count: dict[str, int] = {}
        ids: list[str] = []
        entities: set = set()
        for lens, b in per_lens.items():
            per_lens_max[lens] = float(b["max_strength"])
            per_lens_conf[lens] = float(b["max_conf"])
            per_lens_count[lens] = int(b["count"])
            ids.extend(b["ids"])
            entities.update(b.get("entities", ()))

        # Regime → weights.
        regime_label = "default"
        regime_conf = 0.0
        rl = self._regimes.get(ws)
        if rl is not None:
            regime_label = rl.regime
            regime_conf = rl.confidence
        if self._regime_aware:
            if self._weighting_engine is not None:
                weights = self._weighting_engine.get_weights(regime_label)
            else:
                weights = get_regime_weights(regime_label)
        else:
            weights = dict(self._weights)

        # Bilinear base: Σ weight · strength · confidence (I-17).
        base = 0.0
        for lens, w in weights.items():
            base += w * per_lens_max.get(lens, 0.0) * per_lens_conf.get(lens, 0.0)

        # Non-linear conflict boost (conflicts = alpha).
        conflicts = self._conflicts.get(ws, [])
        conflict_boost = 0.0
        if conflicts:
            mean_cs = sum(c.strength for c in conflicts) / len(conflicts)
            conflict_boost = self.CONFLICT_BOOST * mean_cs
        score = min(base * (1.0 + conflict_boost), 1.0)

        return WindowAggregate(
            window_start=ws,
            window_end=ws + self._window_seconds,
            per_lens_max_strength=per_lens_max,
            per_lens_signal_count=per_lens_count,
            weights_used=weights,
            weighted_aggregate=score,
            contributing_signal_ids=ids,
            regime_label=regime_label,
            regime_confidence=regime_conf,
            base_score=base,
            conflict_count=len(conflicts),
            conflict_boost=conflict_boost,
            entities=sorted(entities),
        )

    async def _emit_window(self, bus: EventBus, ws: float) -> None:
        if ws not in self._windows:
            return
        agg = self._aggregate(ws)
        # Clean up state for this window.
        self._windows.pop(ws, None)
        self._regimes.pop(ws, None)
        self._conflicts.pop(ws, None)
        await bus.publish(self.AGGREGATE_TOPIC, agg)
        self.total_windows_emitted += 1


class AggregateLoggerConsumer:
    def __init__(self, output_path: Path, *, name: str = "agg_logger"):
        self._output_path = Path(output_path)
        self._name = name
        self.total_received = 0
        self._aggregate_scores: list[float] = []

    @property
    def aggregate_score_stats(self) -> dict:
        if not self._aggregate_scores:
            return {"count": 0, "min": None, "mean": None, "max": None,
                    "distinct": 0}
        return {
            "count": len(self._aggregate_scores),
            "min": min(self._aggregate_scores),
            "mean": statistics.fmean(self._aggregate_scores),
            "max": max(self._aggregate_scores),
            "distinct": len({round(s, 6) for s in self._aggregate_scores}),
        }

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC, name=self._name)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._output_path.open("a", encoding="utf-8") as handle:
            while True:
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if stop_event is not None and stop_event.is_set():
                        empty = 0
                        while empty < 3:
                            try:
                                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                                self._process(msg, handle)
                                empty = 0
                            except asyncio.TimeoutError:
                                empty += 1
                        return
                    continue
                self._process(msg, handle)

    def _process(self, msg, handle) -> None:
        if isinstance(msg, WindowAggregate):
            handle.write(json.dumps(msg.to_dict(), default=str))
            handle.write("\n")
            handle.flush()
            self.total_received += 1
            self._aggregate_scores.append(msg.weighted_aggregate)


__all__ = ["Orchestrator", "WindowAggregate", "AggregateLoggerConsumer"]
