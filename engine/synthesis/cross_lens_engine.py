"""Cross-lens synthesis engine (sub-phase 3.3, blueprint § 4).

Groups Signals within a time window and detects MULTI-LENS CONVERGENCE:
when ≥2 different lenses fire on the same underlying entity (address) or
the same window shows ≥2 lenses active, that's a composite pattern
stronger than any single lens alone. Emits `CompositeSignal` events.

Per D-030: v1 uses SIMPLE grouping (shared address in metadata, or
co-firing across ≥2 lenses in the window), NOT embedding-based semantic
similarity. Embeddings are deferred to a later sub-phase — simple
grouping is enough to prove the synthesis concept.

A CompositeSignal is NOT a Signal (it references multiple signals and
doesn't fit the 9-field schema). It rides the bus on `composite.window`.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..core.event_bus import EventBus, Subscriber
from ..core.signal_schema import Signal
from ..orchestrator.windowing import WindowAccumulator, ClosedWindow


def _addresses_in(sig: Signal) -> set[str]:
    """Extract candidate entity addresses from a signal's metadata.
    Different lenses use different metadata keys."""
    md = sig.metadata or {}
    out: set[str] = set()
    for key in ("address", "matched_address", "deployer_address",
                "org_id", "pool_address"):
        v = md.get(key)
        if isinstance(v, str) and v:
            out.add(v.lower())
    # cluster_detected carries a list.
    for v in (md.get("cluster_addresses") or []):
        if isinstance(v, str):
            out.add(v.lower())
    return out


@dataclass(frozen=True)
class CompositeSignal:
    """A multi-lens convergence pattern within a window."""
    window_start: float
    window_end: float
    lenses_involved: list[str]
    signal_types: list[str]
    shared_entity: Optional[str]           # the address/org they converge on
    convergence_score: float               # [0,1]
    component_signal_ids: list[str]
    component_count: int

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "lenses_involved": list(self.lenses_involved),
            "signal_types": list(self.signal_types),
            "shared_entity": self.shared_entity,
            "convergence_score": self.convergence_score,
            "component_signal_ids": list(self.component_signal_ids),
            "component_count": self.component_count,
        }


class CrossLensEngine:
    """Subscribes to `signal.*`, emits `CompositeSignal` to
    `composite.window` when ≥2 lenses converge in a window."""

    INPUT_PATTERN = "signal.*"
    OUTPUT_TOPIC = "composite.window"

    def __init__(self, *, window_seconds: float = 300.0,
                 name: str = "cross_lens_engine",
                 min_lenses: int = 2):
        self._window_seconds = float(window_seconds)
        self._name = name
        self._min_lenses = min_lenses
        self._acc = WindowAccumulator(window_seconds)
        self.total_composites_emitted = 0
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
        if not cw.signals:
            return
        # Group by shared entity.
        entity_to_sigs: dict[str, list[Signal]] = defaultdict(list)
        for sig in cw.signals:
            for addr in _addresses_in(sig):
                entity_to_sigs[addr].append(sig)

        emitted_for_window = False
        # (1) Entity-level convergence: ≥2 distinct lenses on same entity.
        for entity, sigs in entity_to_sigs.items():
            lenses = {s.lens for s in sigs}
            if len(lenses) >= self._min_lenses:
                await self._emit_composite(bus, cw, sigs, shared_entity=entity)
                emitted_for_window = True

        # (2) Window-level convergence fallback: ≥2 lenses active in the
        # window even without a shared entity (weaker convergence).
        if not emitted_for_window:
            window_lenses = {s.lens for s in cw.signals}
            if len(window_lenses) >= self._min_lenses:
                await self._emit_composite(
                    bus, cw, cw.signals, shared_entity=None,
                )

    async def _emit_composite(self, bus: EventBus, cw: ClosedWindow,
                              sigs: list[Signal], *,
                              shared_entity: Optional[str]) -> None:
        lenses = sorted({s.lens for s in sigs})
        types = sorted({s.type for s in sigs})
        # Convergence score: more lenses + higher mean strength = stronger.
        n_lenses = len(lenses)
        mean_strength = sum(s.strength for s in sigs) / len(sigs)
        # 5 canonical lenses max; normalize lens-breadth to [0,1].
        lens_breadth = min(n_lenses / 5.0, 1.0)
        # Entity-anchored convergence is stronger than window-only.
        anchor_bonus = 0.2 if shared_entity is not None else 0.0
        score = min(0.5 * lens_breadth + 0.3 * mean_strength + anchor_bonus, 1.0)
        composite = CompositeSignal(
            window_start=cw.window_start,
            window_end=cw.window_end,
            lenses_involved=lenses,
            signal_types=types,
            shared_entity=shared_entity,
            convergence_score=score,
            component_signal_ids=[s.id for s in sigs],
            component_count=len(sigs),
        )
        await bus.publish(self.OUTPUT_TOPIC, composite)
        self.total_composites_emitted += 1


class CompositeLoggerConsumer:
    """Writes CompositeSignal payloads to JSONL."""

    def __init__(self, output_path: Path, *, name: str = "composite_logger"):
        self._output_path = Path(output_path)
        self._name = name
        self.total_received = 0

    async def run(self, bus: EventBus, *,
                  stop_event: Optional[asyncio.Event] = None) -> None:
        sub = await bus.subscribe(CrossLensEngine.OUTPUT_TOPIC, name=self._name)
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
        if isinstance(msg, CompositeSignal):
            handle.write(json.dumps(msg.to_dict(), default=str))
            handle.write("\n")
            handle.flush()
            self.total_received += 1


__all__ = ["CrossLensEngine", "CompositeSignal", "CompositeLoggerConsumer"]
