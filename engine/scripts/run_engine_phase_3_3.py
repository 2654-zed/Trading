"""Sub-phase 3.3 smoke driver — temporal replay + full adjudication.

Wires (all $0 CU, local data only):
  - 3 lenses (graph / stochastic / information) in REPLAY mode, each
    iterating a ReplayClock over the historical L3 data range
  - CrossLensEngine    → composite.window  (CompositeSignal)
  - RegimeEngine       → regime.window     (RegimeLabel)
  - ConflictEngine     → conflict.window   (ConflictSignal)
  - Orchestrator v2    → aggregate.window  (regime-aware + non-linear)
  - JSONL loggers for signals, composites, regimes, conflicts, aggregates

Per UNK-014 resolution (D-029): replay turns ~50 days of static L3 data
into a time series so regimes shift, conflicts emerge, and the
orchestrator's per-window scores actually vary.

Usage:
    python -m engine.scripts.run_engine_phase_3_3 --target-windows 200
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
from ..lenses.graph.lens import GraphLens
from ..lenses.information.lens import InformationLens
from ..lenses.stochastic.lens import StochasticLens
from ..orchestrator.orchestrator import AggregateLoggerConsumer, Orchestrator
from ..orchestrator.conflict_engine import ConflictEngine, ConflictLoggerConsumer
from ..orchestrator.regime_engine import RegimeEngine, RegimeLoggerConsumer
from ..synthesis.cross_lens_engine import CrossLensEngine, CompositeLoggerConsumer


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--target-windows", type=int, default=200,
                   help="number of replay windows across the data span.")
    p.add_argument("--orchestrator-window-seconds", type=float, default=None,
                   help="orchestrator/engine window size; default = replay slice.")
    p.add_argument(
        "--monitored-pools-path", type=Path,
        default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"),
    )
    p.add_argument(
        "--l3-db-path", type=Path,
        default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"),
    )
    p.add_argument("--out-dir", type=Path, default=Path("engine/data"))
    return p.parse_args()


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


async def run(args: argparse.Namespace) -> int:
    print(f"[run_engine_phase_3_3] starting at "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    if not args.monitored_pools_path.exists():
        print("ERROR: monitored_pools.json not found", file=sys.stderr)
        return 2
    if not args.l3_db_path.exists():
        print("ERROR: L3 SQLite not found", file=sys.stderr)
        return 2

    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)

    # Determine the data time range to bound the replay.
    addrs = _all_addresses(monitored)
    rng = l3.get_data_time_range(addrs)
    if rng is None:
        print("ERROR: no timestamped L3 data for monitored set", file=sys.stderr)
        return 2
    start_ts, end_ts = rng
    span_days = (end_ts - start_ts) / 86400
    slice_seconds = max((end_ts - start_ts) / max(args.target_windows, 1), 1.0)
    win_seconds = args.orchestrator_window_seconds or slice_seconds

    print(f"  data span      : {span_days:.1f} days "
          f"({datetime.fromtimestamp(start_ts, timezone.utc).date()} → "
          f"{datetime.fromtimestamp(end_ts, timezone.utc).date()})", flush=True)
    print(f"  target windows : {args.target_windows} "
          f"(slice {slice_seconds/3600:.1f}h)", flush=True)
    print(f"  monitored pools: {len(monitored.list_pools())}", flush=True)

    bus = EventBus(default_queue_size=50_000)

    # Lenses run in LOCKSTEP (driven below), not as independent tasks, so
    # the event-time watermark advances monotonically and windows don't
    # fragment. No replay_clock injected → the driver calls
    # scan_replay_window per window for all three in order.
    graph_lens = GraphLens(monitored_set=monitored, l3_corpus=l3)
    stoch_lens = StochasticLens(monitored_set=monitored, data_source=l3)
    info_lens = InformationLens(monitored_set=monitored, data_source=l3)
    lenses = [graph_lens, stoch_lens, info_lens]
    clock = ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                        lookback_seconds=slice_seconds)

    cross = CrossLensEngine(window_seconds=win_seconds)
    regime = RegimeEngine(window_seconds=win_seconds)
    conflict = ConflictEngine(window_seconds=win_seconds)
    orch = Orchestrator(window_seconds=win_seconds, regime_aware=True)

    out = args.out_dir
    sig_log = SignalLoggerConsumer(out / "sub_phase_3_3_signals.jsonl")
    comp_log = CompositeLoggerConsumer(out / "sub_phase_3_3_composites.jsonl")
    regime_log = RegimeLoggerConsumer(out / "sub_phase_3_3_regimes.jsonl")
    conflict_log = ConflictLoggerConsumer(out / "sub_phase_3_3_conflicts.jsonl")
    agg_log = AggregateLoggerConsumer(out / "sub_phase_3_3_aggregates.jsonl")

    stop_event = asyncio.Event()          # fires for lenses + engines
    consumer_stop = asyncio.Event()       # fires LATER, after engines flush

    consumer_tasks = [
        asyncio.create_task(sig_log.run(bus, stop_event=consumer_stop), name="sig_log"),
        asyncio.create_task(comp_log.run(bus, stop_event=consumer_stop), name="comp_log"),
        asyncio.create_task(regime_log.run(bus, stop_event=consumer_stop), name="regime_log"),
        asyncio.create_task(conflict_log.run(bus, stop_event=consumer_stop), name="conflict_log"),
        asyncio.create_task(agg_log.run(bus, stop_event=consumer_stop), name="agg_log"),
    ]
    engine_tasks = [
        asyncio.create_task(cross.run(bus, stop_event=stop_event), name="cross"),
        asyncio.create_task(regime.run(bus, stop_event=stop_event), name="regime"),
        asyncio.create_task(conflict.run(bus, stop_event=stop_event), name="conflict"),
        asyncio.create_task(orch.run(bus, stop_event=stop_event), name="orch"),
    ]
    # Let consumers + engines subscribe before lenses start emitting.
    await asyncio.sleep(0.2)

    started = time.monotonic()
    try:
        # LOCKSTEP replay: for each simulated window, run all three
        # lenses before advancing. Guarantees the event-time watermark
        # advances monotonically across the whole pipeline.
        for window in clock.windows():
            for lens in lenses:
                await lens.scan_replay_window(bus, window)
            # Yield so engines/consumers drain this window's signals
            # before the next window's signals pile in.
            await asyncio.sleep(0)
    finally:
        # Lenses done → give engines time to flush pending windows to the
        # bus, THEN stop consumers (two-phase so the engine-flushed
        # composites/conflicts/aggregates aren't stranded).
        await asyncio.sleep(0.5)
        # Phase 1: stop engines; they flush final windows to the bus.
        # Consumers are STILL RUNNING (separate stop event) so nothing
        # the engines flush gets stranded.
        stop_event.set()
        try:
            await asyncio.wait_for(
                asyncio.gather(*engine_tasks, return_exceptions=True),
                timeout=15.0)
        except asyncio.TimeoutError:
            pass
        # Give consumers a beat to drain the engine-flushed tail.
        await asyncio.sleep(0.5)
        # Phase 2: now stop consumers; they grace-drain remaining queues.
        consumer_stop.set()
        try:
            await asyncio.wait_for(
                asyncio.gather(*consumer_tasks, return_exceptions=True),
                timeout=15.0)
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
        l3.close()

    elapsed = time.monotonic() - started
    print()
    print("=" * 70)
    print("sub-phase 3.3 smoke run summary (temporal replay)")
    print("=" * 70)
    print(f"  wall_seconds            : {elapsed:.1f}")
    print(f"  graph/stoch/info scans  : {graph_lens.scan_count} / "
          f"{stoch_lens.scan_count} / {info_lens.scan_count}")
    print(f"  lens signals emitted    : graph={graph_lens.emitted_count} "
          f"stoch={stoch_lens.emitted_count} info={info_lens.emitted_count}")
    print(f"  signal_logger.received  : {sig_log.total_received}")
    print(f"  signal_logger.by_type   : {dict(sig_log.by_type)}")
    print()
    print(f"  composites emitted      : {cross.total_composites_emitted} "
          f"(logged {comp_log.total_received})")
    print(f"  regime labels emitted   : {regime.total_labels_emitted} "
          f"(logged {regime_log.total_received})")
    print(f"  regime distribution     : {dict(regime.regime_counts)}")
    print(f"  conflicts emitted       : {conflict.total_conflicts_emitted} "
          f"(logged {conflict_log.total_received})")
    print()
    print(f"  orchestrator windows    : {orch.total_windows_emitted} "
          f"(logged {agg_log.total_received})")
    print(f"  aggregate score stats   : {agg_log.aggregate_score_stats}")
    print(f"  orchestrator latency    : N/A in replay "
          f"(signal timestamps are simulated, not wall-clock; "
          f"p95<500ms target validated in 3.2 static mode)")
    bs = bus.stats()
    print()
    print(f"  bus.published           : {bs['published']}")
    print(f"  bus.validation_fails    : {bs['validation_failures']}")
    print(f"  bus.subscribers         : {len(bs['subscribers'])}")
    print()
    return 0


if __name__ == "__main__":
    args = _parse_args()
    code = asyncio.run(run(args))
    sys.exit(code)
