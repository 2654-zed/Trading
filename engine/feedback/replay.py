"""Replay harness + H8 falsification (sub-phase 3.5).

The H8 hypothesis ("system learns") demands an OUT-OF-SAMPLE test:
train weights on one slice of history, then check whether the learned
weights predict outcomes better than the initial/static weights on a
HELD-OUT slice. Same-sample improvement (3.4b) is suggestive but
circular; this is the rigorous version.

Design:
  1. Split the data span into train [start, split) and holdout [split, end].
  2. Run the pipeline over train → learn per-regime weights.
  3. Run the pipeline over holdout TWICE: once with static weights, once
     with the learned weights. Attribute forward outcomes for each.
  4. Compare score↔outcome correlation on the holdout. H8 SUPPORTED if
     learned > static out-of-sample; FALSIFIED if learned ≤ static.

This also sharpens UNK-015 (H5): the holdout comparison reports the
orchestrator's correlation vs the best single lens's, out-of-sample.

Reuses the real engine components (no recompute shortcuts) so the test
exercises the actual pipeline.
"""

from __future__ import annotations

import asyncio
import math
import time
from pathlib import Path
from typing import Optional

from ..core.event_bus import EventBus
from ..core.replay_clock import ReplayClock
from .outcome_attributor import OutcomeAttributor
from .outcome_ledger import LedgerWriter
from ..lenses.graph.lens import GraphLens
from ..lenses.information.lens import InformationLens
from ..lenses.stochastic.lens import StochasticLens
from ..orchestrator.conflict_engine import ConflictEngine, ConflictSignal
from ..orchestrator.decision_engine import Decision, DecisionEngine
from ..orchestrator.orchestrator import Orchestrator
from ..orchestrator.regime_engine import RegimeEngine
from ..synthesis.cross_lens_engine import CrossLensEngine


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def score_outcome_correlation(joined_rows: list[dict]) -> Optional[float]:
    if len(joined_rows) < 3:
        return None
    s = [float(r["weighted_aggregate"] or 0.0) for r in joined_rows]
    o = [float(r["outcome_value"] or 0.0) for r in joined_rows]
    return pearson(s, o)


async def replay_to_ledger(
    l3, monitored, ledger_path, *,
    start_ts: float, end_ts: float, slice_seconds: float,
    weighting_engine=None, attributor=None,
) -> LedgerWriter:
    """Canonical replay pass over [start, end): lenses (lockstep) →
    synthesis/regime/conflict → orchestrator → decisions → ledger, then
    outcome attribution. Returns the LedgerWriter (open).

    `attributor`: an object with `attribute_all(writer)`. Defaults to the
    D-034 forward-flow proxy (`OutcomeAttributor`). Phase 4 injects a
    `RealizedOutcomeAttributor` (real price returns) — same interface."""
    if Path(ledger_path).exists():
        Path(ledger_path).unlink()
    writer = LedgerWriter(ledger_path)
    bus = EventBus(default_queue_size=50_000)
    win = slice_seconds
    lenses = [
        GraphLens(monitored_set=monitored, l3_corpus=l3),
        StochasticLens(monitored_set=monitored, data_source=l3),
        InformationLens(monitored_set=monitored, data_source=l3),
    ]
    cross = CrossLensEngine(window_seconds=win)
    regime = RegimeEngine(window_seconds=win)
    conflict = ConflictEngine(window_seconds=win)
    orch = Orchestrator(window_seconds=win, regime_aware=True,
                        weighting_engine=weighting_engine)
    decider = DecisionEngine()
    clock = ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                        lookback_seconds=slice_seconds)

    stop_event = asyncio.Event()
    consumer_stop = asyncio.Event()

    async def _tap(topic, kind, writefn):
        sub = await bus.subscribe(topic, name=f"tap_{kind}")
        while True:
            try:
                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if consumer_stop.is_set():
                    empty = 0
                    while empty < 3:
                        try:
                            msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                            writefn(msg); empty = 0
                        except asyncio.TimeoutError:
                            empty += 1
                    return
                continue
            writefn(msg)

    def _w_decision(msg):
        if isinstance(msg, Decision):
            writer.write_decision(msg, time.time())

    def _w_conflict(msg):
        if isinstance(msg, ConflictSignal):
            writer.write_conflict(msg, time.time())

    consumer_tasks = [
        asyncio.create_task(_tap(DecisionEngine.OUTPUT_TOPIC, "dec", _w_decision)),
        asyncio.create_task(_tap(ConflictEngine.OUTPUT_TOPIC, "conf", _w_conflict)),
    ]
    engine_tasks = [
        asyncio.create_task(cross.run(bus, stop_event=stop_event)),
        asyncio.create_task(regime.run(bus, stop_event=stop_event)),
        asyncio.create_task(conflict.run(bus, stop_event=stop_event)),
        asyncio.create_task(orch.run(bus, stop_event=stop_event)),
        asyncio.create_task(decider.run(bus, stop_event=stop_event)),
    ]
    await asyncio.sleep(0.2)
    try:
        for window in clock.windows():
            for lens in lenses:
                await lens.scan_replay_window(bus, window)
            await asyncio.sleep(0)
    finally:
        await asyncio.sleep(0.5)
        stop_event.set()
        try:
            await asyncio.wait_for(asyncio.gather(*engine_tasks, return_exceptions=True), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.5)
        consumer_stop.set()
        try:
            await asyncio.wait_for(asyncio.gather(*consumer_tasks, return_exceptions=True), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        for t in engine_tasks + consumer_tasks:
            if not t.done():
                t.cancel()
        for t in engine_tasks + consumer_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    if attributor is None:
        attributor = OutcomeAttributor(l3, horizon_seconds=2 * win)
    attributor.attribute_all(writer)
    return writer


async def h8_holdout_test(
    l3, monitored, *, start_ts: float, end_ts: float,
    target_windows: int, out_dir: Path, train_frac: float = 0.7,
    blend_alpha: float = 0.6, attributor=None,
) -> dict:
    """Out-of-sample H8 test. Returns a verdict dict.

    `attributor` is passed through to every replay pass (Phase 4 injects
    the realized-return attributor; default = proxy)."""
    from .learner import Learner
    from ..orchestrator.weighting_engine import WeightingEngine
    from .phase3_analysis import analyze_h5

    span = end_ts - start_ts
    split = start_ts + span * train_frac
    slice_seconds = max(span / max(target_windows, 1), 1.0)

    # 1. Train pass → learn weights.
    train_writer = await replay_to_ledger(
        l3, monitored, out_dir / "h8_train.db",
        start_ts=start_ts, end_ts=split, slice_seconds=slice_seconds,
        attributor=attributor)
    learner = Learner(blend_alpha=blend_alpha)
    learned = learner.learn(train_writer)
    wpath = out_dir / "h8_learned_weights.json"
    learner.persist(learned, wpath)
    train_writer.close()
    we = WeightingEngine(wpath)

    # 2. Holdout pass — static weights.
    hold_static = await replay_to_ledger(
        l3, monitored, out_dir / "h8_holdout_static.db",
        start_ts=split, end_ts=end_ts, slice_seconds=slice_seconds,
        weighting_engine=None, attributor=attributor)
    static_joined = hold_static.outcomes_joined()
    static_corr = score_outcome_correlation(static_joined)
    static_h5 = analyze_h5(static_joined)
    hold_static.close()

    # 3. Holdout pass — learned weights.
    hold_learned = await replay_to_ledger(
        l3, monitored, out_dir / "h8_holdout_learned.db",
        start_ts=split, end_ts=end_ts, slice_seconds=slice_seconds,
        weighting_engine=we, attributor=attributor)
    learned_joined = hold_learned.outcomes_joined()
    learned_corr = score_outcome_correlation(learned_joined)
    hold_learned.close()

    sc = abs(static_corr) if static_corr is not None else 0.0
    lc = abs(learned_corr) if learned_corr is not None else 0.0
    improved = lc > sc
    return {
        "train_windows": train_writer.count("decisions") if False else None,
        "holdout_n": len(static_joined),
        "static_corr": static_corr,
        "learned_corr": learned_corr,
        "static_abs": sc,
        "learned_abs": lc,
        "improvement_pp": round((lc - sc) * 100, 1),
        "h8_verdict": ("SUPPORTED-OOS" if improved else "NOT-SUPPORTED-OOS"),
        "holdout_h5": static_h5,  # H5 on the held-out set (sharpens UNK-015)
    }


__all__ = [
    "replay_to_ledger", "h8_holdout_test",
    "score_outcome_correlation", "pearson",
]
