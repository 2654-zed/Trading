"""Sub-phase 3.2 smoke driver.

Wires:
  - Phase 2 monitored_pools.json adapter
  - Phase 2 L3 SQLite adapter (extended with stochastic + information
    data feeds per D-024)
  - GraphLens (from sub-phase 3.1) — still active
  - StochasticLens (new) — flow-volatility/drift/diffusion
  - InformationLens (new) — entropy + KL divergence
  - Orchestrator (skeleton) — static weights, windowed aggregate
  - SignalLoggerConsumer — writes JSONL of all signals
  - AggregateLoggerConsumer — writes JSONL of orchestrator aggregates

Per D-024: zero Alchemy CU consumption. Data path is 100% local files
(L3 SQLite + monitored_pools.json).

Default smoke duration: 1 hour (per user direction 2026-05-27). The
acceptance bar scales proportionally to a 6-hour run: ≥1,700 signals
+ ≥17 aggregate windows emitted.

Usage:
    python -m engine.scripts.run_engine_phase_3_2 --minutes 60
    python -m engine.scripts.run_engine_phase_3_2 --scans 3 --scan-interval 1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..adapters.l3_corpus_phase2 import Phase2L3CorpusAdapter
from ..adapters.monitored_set_phase2 import Phase2MonitoredSetAdapter
from ..consumers.signal_logger import SignalLoggerConsumer
from ..core.event_bus import EventBus
from ..lenses.graph.lens import GraphLens
from ..lenses.information.lens import InformationLens
from ..lenses.stochastic.lens import StochasticLens
from ..orchestrator.orchestrator import (
    AggregateLoggerConsumer,
    Orchestrator,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--minutes", type=float, default=60.0,
                   help="wall-clock run duration (default 60, per user 3.2 direction).")
    g.add_argument("--scans", type=int,
                   help="exact per-lens scan count (overrides --minutes).")
    p.add_argument("--scan-interval", type=float, default=30.0)
    p.add_argument("--window-seconds", type=float, default=300.0,
                   help="orchestrator window size (default 5 min).")
    p.add_argument(
        "--monitored-pools-path", type=Path,
        default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"),
    )
    p.add_argument(
        "--l3-db-path", type=Path,
        default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"),
    )
    p.add_argument(
        "--signals-jsonl", type=Path,
        default=Path("engine/data/sub_phase_3_2_signals.jsonl"),
    )
    p.add_argument(
        "--aggregates-jsonl", type=Path,
        default=Path("engine/data/sub_phase_3_2_aggregates.jsonl"),
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    print(f"[run_engine_phase_3_2] starting at "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    print(f"  monitored_pools : {args.monitored_pools_path}", flush=True)
    print(f"  l3_db          : {args.l3_db_path}", flush=True)
    print(f"  signals_jsonl  : {args.signals_jsonl}", flush=True)
    print(f"  aggregates     : {args.aggregates_jsonl}", flush=True)
    print(f"  scan_interval  : {args.scan_interval}s", flush=True)
    print(f"  window_seconds : {args.window_seconds}s", flush=True)
    if args.scans is not None:
        print(f"  scans per-lens : {args.scans}", flush=True)
    else:
        print(f"  duration       : {args.minutes} min", flush=True)

    if not args.monitored_pools_path.exists():
        print(f"ERROR: monitored_pools.json not found", file=sys.stderr)
        return 2
    if not args.l3_db_path.exists():
        print(f"ERROR: L3 SQLite not found", file=sys.stderr)
        return 2

    bus = EventBus(default_queue_size=8192)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)

    # Three lenses, sharing the same monitored set + L3 adapter.
    graph_lens = GraphLens(
        monitored_set=monitored, l3_corpus=l3,
        scan_interval_seconds=args.scan_interval, max_scans=args.scans,
    )
    stoch_lens = StochasticLens(
        monitored_set=monitored, data_source=l3,
        scan_interval_seconds=args.scan_interval, max_scans=args.scans,
    )
    info_lens = InformationLens(
        monitored_set=monitored, data_source=l3,
        scan_interval_seconds=args.scan_interval, max_scans=args.scans,
    )

    orchestrator = Orchestrator(window_seconds=args.window_seconds)
    signal_logger = SignalLoggerConsumer(args.signals_jsonl,
                                         verbose=args.verbose)
    agg_logger = AggregateLoggerConsumer(args.aggregates_jsonl)

    pool_count = len(monitored.list_pools())
    print(f"  monitored pool count: {pool_count}", flush=True)

    stop_event = asyncio.Event()

    consumer_tasks = [
        asyncio.create_task(signal_logger.run(bus, stop_event=stop_event),
                            name="signal_logger"),
        asyncio.create_task(agg_logger.run(bus, stop_event=stop_event),
                            name="agg_logger"),
        asyncio.create_task(orchestrator.run(bus, stop_event=stop_event),
                            name="orchestrator"),
    ]
    lens_tasks = [
        asyncio.create_task(graph_lens.run(bus, stop_event=stop_event),
                            name="graph_lens"),
        asyncio.create_task(stoch_lens.run(bus, stop_event=stop_event),
                            name="stoch_lens"),
        asyncio.create_task(info_lens.run(bus, stop_event=stop_event),
                            name="info_lens"),
    ]

    started_at = time.monotonic()
    deadline: Optional[float] = (
        started_at + args.minutes * 60 if args.scans is None else None
    )

    try:
        while True:
            if all(t.done() for t in lens_tasks):
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        # Ordered shutdown:
        # 1. Signal stop. Lenses exit immediately at their next check.
        # 2. Wait for lens tasks to fully complete so no more signals
        #    enter the bus.
        # 3. Wait for orchestrator to finish flushing pending windows
        #    (it publishes flushed windows on shutdown).
        # 4. Wait for consumers to drain their queues.
        # 5. Cancel anything still alive.
        stop_event.set()
        # 2: lens tasks first.
        try:
            await asyncio.wait_for(
                asyncio.gather(*lens_tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            pass
        # 3: orchestrator + consumers (they all return when stop_event
        # is set AND their queues are drained, per the drain-on-shutdown
        # pattern in each consumer's run loop).
        try:
            await asyncio.wait_for(
                asyncio.gather(*consumer_tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            pass
        # 5: anything still alive gets cancelled.
        for t in lens_tasks + consumer_tasks:
            if not t.done():
                t.cancel()
        for t in lens_tasks + consumer_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        l3.close()

    elapsed = time.monotonic() - started_at
    print()
    print("=" * 70)
    print("sub-phase 3.2 smoke run summary")
    print("=" * 70)
    print(f"  wall_seconds              : {elapsed:.1f}")
    print(f"  graph_lens.scans          : {graph_lens.scan_count}")
    print(f"  graph_lens.emitted        : {graph_lens.emitted_count}")
    print(f"  stoch_lens.scans          : {stoch_lens.scan_count}")
    print(f"  stoch_lens.emitted        : {stoch_lens.emitted_count}")
    print(f"  info_lens.scans           : {info_lens.scan_count}")
    print(f"  info_lens.emitted         : {info_lens.emitted_count}")
    print()
    print(f"  signal_logger.received    : {signal_logger.total_received}")
    print(f"  signal_logger.by_lens     : {dict(signal_logger.by_lens)}")
    print(f"  signal_logger.by_type     : {dict(signal_logger.by_type)}")
    print()
    print(f"  orchestrator.signals_seen : {orchestrator.total_signals_received}")
    print(f"  orchestrator.windows_emit : {orchestrator.total_windows_emitted}")
    print(f"  orchestrator.dropped_oow  : "
          f"{orchestrator.signals_dropped_outside_window}")
    print(f"  orchestrator.latency      : {orchestrator.latency_stats()}")
    print()
    print(f"  agg_logger.received       : {agg_logger.total_received}")
    print(f"  agg_logger.score_stats    : {agg_logger.aggregate_score_stats}")
    bs = bus.stats()
    print()
    print(f"  bus.published             : {bs['published']}")
    print(f"  bus.validation_fails      : {bs['validation_failures']}")
    print(f"  bus.subscribers           : {len(bs['subscribers'])}")
    for s in bs["subscribers"]:
        print(f"    - {s['name']:25s} pattern={s['pattern']:20s}  "
              f"delivered={s['delivered']}  dropped={s['dropped']}")
    print()
    return 0


if __name__ == "__main__":
    args = _parse_args()
    code = asyncio.run(run(args))
    sys.exit(code)
