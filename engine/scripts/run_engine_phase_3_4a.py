"""Sub-phase 3.4a smoke driver — decisions + live ledger + outcomes.

Extends the 3.3 replay pipeline with:
  - DecisionEngine    → decision.window  (Decision)
  - LedgerWriter      → live SQLite ledger (decisions + conflicts, I-18)
  - OutcomeAttributor → forward-looking $0-CU outcome proxy (post-replay)

Per D-034: outcomes are measured over [T, T+horizon] from data the
decision could not see (no leakage). Per the experiment charter +
I-1/I-3, there is NO execution — this is detection + attribution only.

Usage:
    python -m engine.scripts.run_engine_phase_3_4a --target-windows 200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..adapters.l3_corpus_phase2 import Phase2L3CorpusAdapter
from ..adapters.monitored_set_phase2 import Phase2MonitoredSetAdapter
from ..consumers.signal_logger import SignalLoggerConsumer
from ..core.event_bus import EventBus
from ..core.replay_clock import ReplayClock
from ..feedback.outcome_attributor import OutcomeAttributor
from ..feedback.outcome_ledger import LedgerWriter
from ..lenses.graph.lens import GraphLens
from ..lenses.information.lens import InformationLens
from ..lenses.stochastic.lens import StochasticLens
from ..orchestrator.conflict_engine import ConflictEngine
from ..orchestrator.decision_engine import Decision, DecisionEngine, DecisionLoggerConsumer
from ..orchestrator.orchestrator import AggregateLoggerConsumer, Orchestrator
from ..orchestrator.regime_engine import RegimeEngine
from ..synthesis.cross_lens_engine import CrossLensEngine


def _all_addresses(monitored) -> list[str]:
    addrs = set()
    for p in monitored.list_pools():
        if p.get("address"):
            addrs.add(p["address"].lower())
        for k in ("token0", "token1"):
            v = p.get(k)
            if isinstance(v, dict) and v.get("address"):
                addrs.add(v["address"].lower())
    return list(addrs)


class _LedgerConflictTap:
    """Subscribes to conflict.window and persists each conflict to the
    ledger (I-18: conflicts must be recorded)."""
    def __init__(self, writer: LedgerWriter):
        self._writer = writer
        self.written = 0

    async def run(self, bus, *, stop_event=None):
        from ..orchestrator.conflict_engine import ConflictEngine, ConflictSignal
        sub = await bus.subscribe(ConflictEngine.OUTPUT_TOPIC, name="ledger_conflict_tap")
        while True:
            try:
                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if stop_event is not None and stop_event.is_set():
                    empty = 0
                    while empty < 3:
                        try:
                            msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                            if isinstance(msg, ConflictSignal):
                                self._writer.write_conflict(msg, time.time()); self.written += 1
                            empty = 0
                        except asyncio.TimeoutError:
                            empty += 1
                    return
                continue
            if isinstance(msg, ConflictSignal):
                self._writer.write_conflict(msg, time.time()); self.written += 1


class _LedgerDecisionTap:
    def __init__(self, writer: LedgerWriter):
        self._writer = writer
        self.written = 0

    async def run(self, bus, *, stop_event=None):
        sub = await bus.subscribe(DecisionEngine.OUTPUT_TOPIC, name="ledger_decision_tap")
        while True:
            try:
                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if stop_event is not None and stop_event.is_set():
                    empty = 0
                    while empty < 3:
                        try:
                            msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                            if isinstance(msg, Decision):
                                self._writer.write_decision(msg, time.time()); self.written += 1
                            empty = 0
                        except asyncio.TimeoutError:
                            empty += 1
                    return
                continue
            if isinstance(msg, Decision):
                self._writer.write_decision(msg, time.time()); self.written += 1


async def run(args) -> int:
    print(f"[run_engine_phase_3_4a] starting "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    addrs = _all_addresses(monitored)
    rng = l3.get_data_time_range(addrs)
    if rng is None:
        print("ERROR: no L3 data", file=sys.stderr); return 2
    start_ts, end_ts = rng
    slice_seconds = max((end_ts - start_ts) / max(args.target_windows, 1), 1.0)
    win = slice_seconds
    print(f"  span {(end_ts-start_ts)/86400:.1f}d, {args.target_windows} windows "
          f"(slice {slice_seconds/3600:.1f}h)", flush=True)

    ledger_path = args.out_dir / "outcome_ledger.db"
    if ledger_path.exists():
        ledger_path.unlink()
    writer = LedgerWriter(ledger_path)

    bus = EventBus(default_queue_size=50_000)
    lenses = [
        GraphLens(monitored_set=monitored, l3_corpus=l3),
        StochasticLens(monitored_set=monitored, data_source=l3),
        InformationLens(monitored_set=monitored, data_source=l3),
    ]
    clock = ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                        lookback_seconds=slice_seconds)

    cross = CrossLensEngine(window_seconds=win)
    regime = RegimeEngine(window_seconds=win)
    conflict = ConflictEngine(window_seconds=win)
    orch = Orchestrator(window_seconds=win, regime_aware=True)
    decider = DecisionEngine()

    out = args.out_dir
    sig_log = SignalLoggerConsumer(out / "sub_phase_3_4a_signals.jsonl")
    agg_log = AggregateLoggerConsumer(out / "sub_phase_3_4a_aggregates.jsonl")
    dec_log = DecisionLoggerConsumer(out / "sub_phase_3_4a_decisions.jsonl")
    conf_tap = _LedgerConflictTap(writer)
    dec_tap = _LedgerDecisionTap(writer)

    stop_event = asyncio.Event()
    consumer_stop = asyncio.Event()

    consumer_tasks = [
        asyncio.create_task(sig_log.run(bus, stop_event=consumer_stop)),
        asyncio.create_task(agg_log.run(bus, stop_event=consumer_stop)),
        asyncio.create_task(dec_log.run(bus, stop_event=consumer_stop)),
        asyncio.create_task(conf_tap.run(bus, stop_event=consumer_stop)),
        asyncio.create_task(dec_tap.run(bus, stop_event=consumer_stop)),
    ]
    engine_tasks = [
        asyncio.create_task(cross.run(bus, stop_event=stop_event)),
        asyncio.create_task(regime.run(bus, stop_event=stop_event)),
        asyncio.create_task(conflict.run(bus, stop_event=stop_event)),
        asyncio.create_task(orch.run(bus, stop_event=stop_event)),
        asyncio.create_task(decider.run(bus, stop_event=stop_event)),
    ]
    await asyncio.sleep(0.2)

    started = time.monotonic()
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

    # Post-replay outcome attribution (forward-looking; D-034).
    attributor = OutcomeAttributor(l3, horizon_seconds=2 * win)
    n_outcomes = attributor.attribute_all(writer)

    elapsed = time.monotonic() - started
    print()
    print("=" * 70)
    print("sub-phase 3.4a smoke summary (decisions + ledger + outcomes)")
    print("=" * 70)
    print(f"  wall_seconds          : {elapsed:.1f}")
    print(f"  signals               : {sig_log.total_received}")
    print(f"  aggregates            : {agg_log.total_received}")
    print(f"  decisions emitted      : {decider.total_decisions} "
          f"(logged {dec_log.total_received})")
    print(f"  decision action mix   : {decider.action_counts}")
    print(f"  ledger.decisions      : {writer.count('decisions')}")
    print(f"  ledger.conflicts      : {writer.count('conflicts')}")
    print(f"  ledger.outcomes       : {writer.count('outcomes')} "
          f"(attributed {n_outcomes})")
    # Outcome distribution.
    joined = writer.outcomes_joined()
    from collections import Counter
    gt = Counter(r["ground_truth"] for r in joined)
    print(f"  outcome ground_truth  : {dict(gt)}")
    if joined:
        vals = [r["outcome_value"] for r in joined]
        print(f"  outcome_value range   : {min(vals):.3f}..{max(vals):.3f} "
              f"(mean {sum(vals)/len(vals):.3f})")
    bs = bus.stats()
    print(f"  bus.validation_fails  : {bs['validation_failures']}")
    print("  EXECUTION             : NONE (detection-only; no router run in 3.4a)")
    writer.close()
    l3.close()
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-windows", type=int, default=200)
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    p.add_argument("--out-dir", type=Path, default=Path("engine/data"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
