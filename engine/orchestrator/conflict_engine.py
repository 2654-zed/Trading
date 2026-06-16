"""Conflict engine (sub-phase 3.3, blueprint § 5.3, invariant I-18).

Detects CONTRADICTING signals across lenses in the same window/entity and
emits `ConflictSignal` events. Per I-18, conflicts are first-class — they
ride the same bus → orchestrator path as primary signals and must NOT be
silently resolved or discarded. The blueprint's design rule #4:
"conflicts = alpha".

Per D-032, v1 detects conflicts via SEMANTIC TAG opposition. Each
(lens, signal_type) maps to a semantic tag describing the state it
implies. When two OPPOSED tags (from DIFFERENT lenses) co-occur on the
same entity in a window, that's a conflict.

A ConflictSignal is NOT a Signal; it rides the bus on `conflict.window`.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..core.event_bus import EventBus, Subscriber
from ..core.signal_schema import Signal
from .windowing import WindowAccumulator, ClosedWindow


# (lens, type) -> semantic tag. A signal asserts a "stance" on some
# dimension; opposed stances co-occurring = a conflict.
def _semantic_tag(sig: Signal) -> Optional[str]:
    key = (sig.lens, sig.type)
    if key == ("graph", "cluster_detected"):
        return "coordinated"
    if key == ("graph", "centrality_spike"):
        return "coordinated"
    if key == ("graph", "subgraph_anomaly"):
        return "adversarial"
    if key == ("stochastic", "volatility_regime_shift"):
        return "volatile"
    if key == ("stochastic", "drift_change"):
        return "trending"
    if key == ("stochastic", "diffusion_anomaly"):
        regime = (sig.metadata or {}).get("regime")
        if regime == "super_diffusive":
            return "chaotic"
        return "stable"   # mean_reverting
    if key == ("information", "entropy_drop"):
        return "ordering"
    if key in (("information", "regime_surprise"),
               ("information", "divergence_spike")):
        return "shifting"
    return None


# Opposed tag pairs → conflict type name. "Conflicts = alpha": these are
# the cases single-lens analysis would miss.
CONFLICT_PAIRS: dict[frozenset, str] = {
    frozenset({"coordinated", "stable"}): "hidden_coordination",
    frozenset({"ordering", "chaotic"}): "order_chaos_conflict",
    frozenset({"adversarial", "stable"}): "concealed_adversary",
    frozenset({"coordinated", "shifting"}): "coordinated_regime_shift",
}


def _addresses_in(sig: Signal) -> set[str]:
    md = sig.metadata or {}
    out: set[str] = set()
    for key in ("address", "matched_address", "deployer_address",
                "org_id", "pool_address"):
        v = md.get(key)
        if isinstance(v, str) and v:
            out.add(v.lower())
    for v in (md.get("cluster_addresses") or []):
        if isinstance(v, str):
            out.add(v.lower())
    return out


@dataclass(frozen=True)
class ConflictSignal:
    window_start: float
    window_end: float
    conflict_type: str
    lenses_involved: list[str]
    contradicting_types: list[str]
    shared_entity: Optional[str]
    strength: float
    component_signal_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "conflict_type": self.conflict_type,
            "lenses_involved": list(self.lenses_involved),
            "contradicting_types": list(self.contradicting_types),
            "shared_entity": self.shared_entity,
            "strength": self.strength,
            "component_signal_ids": list(self.component_signal_ids),
        }


class ConflictEngine:
    INPUT_PATTERN = "signal.*"
    OUTPUT_TOPIC = "conflict.window"

    def __init__(self, *, window_seconds: float = 300.0,
                 name: str = "conflict_engine"):
        self._window_seconds = float(window_seconds)
        self._name = name
        self._acc = WindowAccumulator(window_seconds)
        self.total_conflicts_emitted = 0
        self.total_signals_seen = 0

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
        self.total_signals_seen += 1
        for cw in self._acc.add(msg):
            await self._process_window(bus, cw)

    async def _process_window(self, bus: EventBus, cw: ClosedWindow) -> None:
        if len(cw.signals) < 2:
            return
        # Group by shared entity; also consider the whole window as a group.
        entity_to_sigs: dict[Optional[str], list[Signal]] = defaultdict(list)
        for sig in cw.signals:
            addrs = _addresses_in(sig)
            if addrs:
                for a in addrs:
                    entity_to_sigs[a].append(sig)
            else:
                entity_to_sigs[None].append(sig)

        seen_conflicts: set[tuple] = set()
        for entity, sigs in entity_to_sigs.items():
            # Build tag -> (lens, type, strength, id) within this group.
            tag_members: dict[str, list[tuple]] = defaultdict(list)
            for s in sigs:
                tag = _semantic_tag(s)
                if tag:
                    tag_members[tag].append((s.lens, s.type, s.strength, s.id))
            tags_present = set(tag_members.keys())
            for pair, conflict_name in CONFLICT_PAIRS.items():
                if pair <= tags_present:
                    members = []
                    for t in pair:
                        members.extend(tag_members[t])
                    lenses = sorted({m[0] for m in members})
                    # Require the contradiction to span ≥2 DIFFERENT lenses.
                    if len(lenses) < 2:
                        continue
                    dedup_key = (entity, conflict_name)
                    if dedup_key in seen_conflicts:
                        continue
                    seen_conflicts.add(dedup_key)
                    await self._emit_conflict(
                        bus, cw, conflict_name, members, entity,
                    )

    async def _emit_conflict(self, bus: EventBus, cw: ClosedWindow,
                             conflict_name: str, members: list[tuple],
                             entity: Optional[str]) -> None:
        lenses = sorted({m[0] for m in members})
        types = sorted({m[1] for m in members})
        # Strength = mean of the contradicting signals' strengths.
        strength = sum(m[2] for m in members) / len(members)
        ids = [m[3] for m in members]
        conflict = ConflictSignal(
            window_start=cw.window_start,
            window_end=cw.window_end,
            conflict_type=conflict_name,
            lenses_involved=lenses,
            contradicting_types=types,
            shared_entity=entity,
            strength=strength,
            component_signal_ids=ids,
        )
        await bus.publish(self.OUTPUT_TOPIC, conflict)
        self.total_conflicts_emitted += 1


class ConflictLoggerConsumer:
    def __init__(self, output_path: Path, *, name: str = "conflict_logger"):
        self._output_path = Path(output_path)
        self._name = name
        self.total_received = 0

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub = await bus.subscribe(ConflictEngine.OUTPUT_TOPIC, name=self._name)
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
        if isinstance(msg, ConflictSignal):
            handle.write(json.dumps(msg.to_dict(), default=str))
            handle.write("\n")
            handle.flush()
            self.total_received += 1


__all__ = [
    "ConflictEngine", "ConflictSignal", "ConflictLoggerConsumer",
    "CONFLICT_PAIRS",
]
