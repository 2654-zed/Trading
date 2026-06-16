"""Regime engine (sub-phase 3.3, blueprint § 5.1).

Classifies each time window into one of five market regimes:
  {trend, chaotic, low_liquidity, adversarial, exploit_risk}

Per D-031, v1 is a RULE-BASED classifier scoring each regime from
features of the window's signals, then taking argmax. Each classification
carries a confidence + a per-feature contribution log for the spot-check
(the ≥70%-intuitive-correctness acceptance gate is a human eyeball on
these logs).

Emits `RegimeLabel` to `regime.window`. The orchestrator consumes these
to route to regime-specific weight tables (per D-033 / I-17).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.event_bus import EventBus, Subscriber
from ..core.signal_schema import Signal
from .windowing import WindowAccumulator, ClosedWindow


REGIMES = ("trend", "chaotic", "low_liquidity", "adversarial", "exploit_risk")


@dataclass(frozen=True)
class RegimeLabel:
    window_start: float
    window_end: float
    regime: str
    confidence: float
    scores: dict[str, float]
    feature_log: dict[str, Any]
    signal_count: int

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "regime": self.regime,
            "confidence": self.confidence,
            "scores": dict(self.scores),
            "feature_log": dict(self.feature_log),
            "signal_count": self.signal_count,
        }


class RegimeEngine:
    INPUT_PATTERN = "signal.*"
    OUTPUT_TOPIC = "regime.window"

    # A window with <= this many signals is a candidate "low_liquidity".
    LOW_LIQUIDITY_SIGNAL_FLOOR = 3

    def __init__(self, *, window_seconds: float = 300.0,
                 name: str = "regime_engine"):
        self._window_seconds = float(window_seconds)
        self._name = name
        self._acc = WindowAccumulator(window_seconds)
        self.total_labels_emitted = 0
        self.regime_counts: Counter = Counter()

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub: Subscriber = await bus.subscribe(self.INPUT_PATTERN, name=self._name)
        while True:
            try:
                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if stop_event is not None and stop_event.is_set():
                    empty = 0
                    while empty < 3:
                        try:
                            msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                            await self._handle(bus, msg)
                            empty = 0
                        except asyncio.TimeoutError:
                            empty += 1
                    for cw in self._acc.flush():
                        await self._process_window(bus, cw)
                    return
                continue
            await self._handle(bus, msg)

    async def _handle(self, bus: EventBus, msg: Any) -> None:
        if not isinstance(msg, Signal):
            return
        for cw in self._acc.add(msg):
            await self._process_window(bus, cw)

    def classify(self, signals: list[Signal]) -> tuple[str, float, dict, dict]:
        """Pure scoring function (also used directly in unit tests).
        Returns (regime, confidence, scores, feature_log)."""
        n_total = len(signals)
        by_type: dict[str, list[float]] = {}
        by_lens: Counter = Counter()
        for s in signals:
            by_type.setdefault(s.type, []).append(s.strength)
            by_lens[s.lens] += 1

        def max_strength(type_name: str) -> float:
            return max(by_type.get(type_name, [0.0]))

        def present(type_name: str) -> bool:
            return type_name in by_type

        # Coordination strength is computed up-front: it feeds both
        # `trend` (steady coordinated state) and `exploit_risk`.
        coordination = max(max_strength("cluster_detected"),
                           max_strength("centrality_spike"))
        adversarial = max_strength("subgraph_anomaly")

        # --- per-regime raw scores in [0,1] ---
        scores: dict[str, float] = {}

        # trend: directional drift OR steady coordinated accumulation
        # (high centrality/cluster) WITHOUT adversarial or chaotic signals.
        # Coordination without an adversarial actor is a steady-state
        # trend, not an exploit — so it lands here. Damp when adversarial
        # is present so `adversarial`/`exploit_risk` can win those.
        trend_base = max(max_strength("drift_change"), 0.7 * coordination)
        scores["trend"] = trend_base * (0.5 if adversarial > 0 else 1.0)

        # chaotic: volatility + super-diffusive diffusion.
        chaotic_components = [max_strength("volatility_regime_shift")]
        # diffusion_anomaly with super_diffusive regime counts toward chaos.
        super_diff = [
            s.strength for s in signals
            if s.type == "diffusion_anomaly"
            and (s.metadata or {}).get("regime") == "super_diffusive"
        ]
        if super_diff:
            chaotic_components.append(max(super_diff))
        scores["chaotic"] = sum(chaotic_components) / len(chaotic_components)

        # low_liquidity: few signals overall.
        if n_total <= self.LOW_LIQUIDITY_SIGNAL_FLOOR:
            scores["low_liquidity"] = 1.0 - (n_total / (self.LOW_LIQUIDITY_SIGNAL_FLOOR + 1))
        else:
            scores["low_liquidity"] = 0.0

        # adversarial: poisoning / laundry subgraph anomalies present.
        scores["adversarial"] = adversarial

        # exploit_risk: coordination (cluster/centrality) AND adversarial
        # converging — the dangerous combination. Wins over `adversarial`
        # only when coordination dominates (coord > advr), so a lone
        # adversarial actor stays "adversarial" while a coordinated +
        # adversarial convergence escalates to "exploit_risk".
        if coordination > 0 and adversarial > 0:
            scores["exploit_risk"] = (coordination + adversarial) / 2.0
        else:
            scores["exploit_risk"] = 0.0

        # Pick argmax. Tie-break by REGIMES order (deterministic).
        winner = max(REGIMES, key=lambda r: (scores.get(r, 0.0),
                                             -REGIMES.index(r)))
        winner_score = scores.get(winner, 0.0)
        total = sum(scores.values())
        confidence = (winner_score / total) if total > 0 else 0.0

        feature_log = {
            "n_total": n_total,
            "by_lens": dict(by_lens),
            "coordination_strength": coordination,
            "adversarial_strength": adversarial,
            "n_super_diffusive": len(super_diff),
        }
        return winner, confidence, scores, feature_log

    async def _process_window(self, bus: EventBus, cw: ClosedWindow) -> None:
        regime, confidence, scores, feature_log = self.classify(cw.signals)
        label = RegimeLabel(
            window_start=cw.window_start,
            window_end=cw.window_end,
            regime=regime,
            confidence=confidence,
            scores=scores,
            feature_log=feature_log,
            signal_count=len(cw.signals),
        )
        await bus.publish(self.OUTPUT_TOPIC, label)
        self.total_labels_emitted += 1
        self.regime_counts[regime] += 1


class RegimeLoggerConsumer:
    def __init__(self, output_path: Path, *, name: str = "regime_logger"):
        self._output_path = Path(output_path)
        self._name = name
        self.total_received = 0

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub = await bus.subscribe(RegimeEngine.OUTPUT_TOPIC, name=self._name)
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
                                self._write(msg, handle)
                                empty = 0
                            except asyncio.TimeoutError:
                                empty += 1
                        return
                    continue
                self._write(msg, handle)

    def _write(self, msg, handle) -> None:
        if isinstance(msg, RegimeLabel):
            handle.write(json.dumps(msg.to_dict(), default=str))
            handle.write("\n")
            handle.flush()
            self.total_received += 1


__all__ = ["RegimeEngine", "RegimeLabel", "RegimeLoggerConsumer", "REGIMES"]
