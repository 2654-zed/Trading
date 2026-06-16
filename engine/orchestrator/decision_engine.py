"""Decision engine (sub-phase 3.4, blueprint § 5.4).

Converts adjudicated `WindowAggregate`s into structured `Decision`
objects: action ∈ {enter, exit, hold, hedge}, confidence, risk_score,
and a rationale capturing the lens contributions that drove it.

v1 is rule-based, routing on the regime label + aggregate score +
conflict presence. The mapping is deliberately conservative for a
DETECTION-ONLY experiment (no execution): "enter" only on clean trend,
"hedge"/"exit" on danger regimes, "hold" otherwise.

A `Decision` is NOT a `Signal`; it rides the bus on `decision.window`.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.event_bus import EventBus, Subscriber
from .orchestrator import WindowAggregate


ACTIONS = ("enter", "exit", "hold", "hedge")


@dataclass(frozen=True)
class Decision:
    window_start: float
    window_end: float
    action: str                 # ∈ ACTIONS
    confidence: float           # [0,1]
    risk_score: float           # [0,1]
    regime_label: str
    aggregate_score: float
    rationale: dict             # structured: top lenses, reason, etc.
    decided_at: float           # wall-clock

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "action": self.action,
            "confidence": self.confidence,
            "risk_score": self.risk_score,
            "regime_label": self.regime_label,
            "aggregate_score": self.aggregate_score,
            "rationale": dict(self.rationale),
            "decided_at": self.decided_at,
        }


class DecisionEngine:
    INPUT_TOPIC = "aggregate.window"
    OUTPUT_TOPIC = "decision.window"

    # Score thresholds for action selection.
    HIGH_SCORE = 0.45
    # Risk base per regime.
    REGIME_RISK = {
        "exploit_risk": 0.9,
        "adversarial": 0.7,
        "chaotic": 0.55,
        "trend": 0.25,
        "low_liquidity": 0.3,
        "default": 0.4,
    }

    def __init__(self, *, name: str = "decision_engine"):
        self._name = name
        self.total_decisions = 0
        self.action_counts: dict[str, int] = {a: 0 for a in ACTIONS}

    def decide(self, agg: WindowAggregate) -> Decision:
        """Pure mapping WindowAggregate → Decision (also unit-tested)."""
        regime = agg.regime_label
        score = agg.weighted_aggregate
        high = score >= self.HIGH_SCORE

        # Risk score: regime base, boosted by conflicts.
        risk = self.REGIME_RISK.get(regime, self.REGIME_RISK["default"])
        if agg.conflict_count > 0:
            risk = min(risk + 0.1 + 0.05 * min(agg.conflict_count, 4), 1.0)

        # Action routing.
        if regime in ("adversarial", "exploit_risk"):
            action = "hedge" if high else "hold"
            reason = (f"danger regime '{regime}' (score {score:.2f}); "
                      f"{'hedge' if high else 'hold — below action threshold'}")
        elif regime == "chaotic":
            action = "hedge" if high else "hold"
            reason = f"chaotic regime; {'hedge' if high else 'hold'}"
        elif regime == "trend":
            action = "enter" if high else "hold"
            reason = (f"trend regime (score {score:.2f}); "
                      f"{'enter' if high else 'hold — weak'}")
        else:  # low_liquidity / default
            action = "hold"
            reason = f"regime '{regime}' → hold (no clean edge)"

        # Confidence: blend regime confidence with score strength.
        confidence = min(0.5 * agg.regime_confidence + 0.5 * score, 1.0)

        # Rationale: top lens contributions.
        top_lenses = sorted(
            agg.per_lens_max_strength.items(), key=lambda kv: -kv[1]
        )[:3]
        rationale = {
            "reason": reason,
            "regime": regime,
            "regime_confidence": agg.regime_confidence,
            "aggregate_score": score,
            "base_score": agg.base_score,
            "conflict_count": agg.conflict_count,
            "conflict_boost": agg.conflict_boost,
            "top_lenses": [
                {"lens": l, "max_strength": s} for l, s in top_lenses if s > 0
            ],
            # Full per-lens strength (drives the 3.4b learner's per-lens
            # outcome attribution).
            "per_lens_strength": {
                l: s for l, s in agg.per_lens_max_strength.items() if s > 0
            },
            "weights_used": dict(agg.weights_used),
            # Phase 4 run-2: entities for entity-specific outcomes.
            "entities": list(getattr(agg, "entities", []) or []),
        }

        return Decision(
            window_start=agg.window_start,
            window_end=agg.window_end,
            action=action,
            confidence=confidence,
            risk_score=risk,
            regime_label=regime,
            aggregate_score=score,
            rationale=rationale,
            decided_at=time.time(),
        )

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub: Subscriber = await bus.subscribe(self.INPUT_TOPIC, name=self._name)
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
                    return
                continue
            await self._handle(bus, msg)

    async def _handle(self, bus: EventBus, msg: Any) -> None:
        if not isinstance(msg, WindowAggregate):
            return
        decision = self.decide(msg)
        await bus.publish(self.OUTPUT_TOPIC, decision)
        self.total_decisions += 1
        self.action_counts[decision.action] = \
            self.action_counts.get(decision.action, 0) + 1


class DecisionLoggerConsumer:
    def __init__(self, output_path: Path, *, name: str = "decision_logger"):
        self._output_path = Path(output_path)
        self._name = name
        self.total_received = 0

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub = await bus.subscribe(DecisionEngine.OUTPUT_TOPIC, name=self._name)
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
        if isinstance(msg, Decision):
            handle.write(json.dumps(msg.to_dict(), default=str))
            handle.write("\n")
            handle.flush()
            self.total_received += 1


__all__ = ["DecisionEngine", "Decision", "DecisionLoggerConsumer", "ACTIONS"]
