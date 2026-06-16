"""Sub-phase 3.1 smoke driver.

Wires:
  - Phase 2 monitored_pools.json adapter → MonitoredSetSource
  - Phase 2 Layer3Client adapter → Layer3CorpusSource
  - GraphLens (the first concrete lens)
  - SignalLoggerConsumer (stub orchestrator-replacement: writes JSONL)
  - EventBus

Run for a fixed duration (default: 30 min per the spec's acceptance
criterion). On shutdown, prints stats:
  - Total signals emitted by lens
  - Signals received by consumer
  - Per-(lens, type) breakdown
  - Bus diagnostic stats

Usage:
    python -m engine.scripts.run_engine_phase_3_1 --minutes 30
    python -m engine.scripts.run_engine_phase_3_1 --minutes 1 --scan-interval 5
    python -m engine.scripts.run_engine_phase_3_1 --scans 3 --scan-interval 1
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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--minutes", type=float, default=30.0,
                   help="wall-clock run duration (default 30, per spec).")
    g.add_argument("--scans", type=int,
                   help="exact scan count (overrides --minutes).")
    p.add_argument("--scan-interval", type=float, default=30.0,
                   help="GraphLens scan interval in seconds (default 30).")
    p.add_argument(
        "--monitored-pools-path", type=Path,
        default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"),
        help="Phase 2 monitored_pools.json (default: local Phase 2 path).",
    )
    p.add_argument(
        "--l3-db-path", type=Path,
        default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"),
        help="Path to local L3 SQLite copy.",
    )
    p.add_argument(
        "--output-jsonl", type=Path,
        default=Path("engine/data/sub_phase_3_1_signals.jsonl"),
        help="Where SignalLoggerConsumer writes (appended).",
    )
    p.add_argument("--verbose", action="store_true",
                   help="Per-signal stderr trace.")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    print(f"[run_engine_phase_3_1] starting at "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    print(f"  monitored_pools : {args.monitored_pools_path}", flush=True)
    print(f"  l3_db          : {args.l3_db_path}", flush=True)
    print(f"  output_jsonl   : {args.output_jsonl}", flush=True)
    print(f"  scan_interval  : {args.scan_interval}s", flush=True)
    if args.scans is not None:
        print(f"  scans          : {args.scans}", flush=True)
    else:
        print(f"  duration       : {args.minutes} min", flush=True)

    # Validate inputs early.
    if not args.monitored_pools_path.exists():
        print(f"ERROR: monitored_pools.json not found at "
              f"{args.monitored_pools_path}", file=sys.stderr)
        return 2
    if not args.l3_db_path.exists():
        print(f"ERROR: L3 SQLite not found at {args.l3_db_path}",
              file=sys.stderr)
        return 2

    # Wire up.
    bus = EventBus(default_queue_size=4096)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    lens = GraphLens(
        monitored_set=monitored, l3_corpus=l3,
        scan_interval_seconds=args.scan_interval,
        max_scans=args.scans,
    )
    consumer = SignalLoggerConsumer(args.output_jsonl, verbose=args.verbose)

    pool_count = len(monitored.list_pools())
    print(f"  monitored pool count: {pool_count}", flush=True)

    # Start consumer + lens. The consumer subscribes to `signal.*`.
    stop_event = asyncio.Event()
    consumer_task = asyncio.create_task(
        consumer.run(bus, stop_event=stop_event), name="consumer",
    )
    lens_task = asyncio.create_task(lens.run(bus, stop_event=stop_event),
                                    name="graph_lens")

    started_at = time.monotonic()
    deadline: Optional[float] = (
        started_at + args.minutes * 60 if args.scans is None else None
    )

    try:
        # Drive until lens finishes (scans-based mode) OR deadline hits.
        while True:
            if lens_task.done():
                # Lens hit its max_scans; signal consumer to drain + exit.
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    finally:
        # Trigger graceful shutdown.
        stop_event.set()
        # Give the consumer a moment to drain.
        await asyncio.sleep(1.5)
        for task in (lens_task, consumer_task):
            if not task.done():
                task.cancel()
        for task in (lens_task, consumer_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        l3.close()

    # Stats.
    elapsed = time.monotonic() - started_at
    print()
    print("=" * 60)
    print("sub-phase 3.1 smoke run summary")
    print("=" * 60)
    print(f"  wall_seconds          : {elapsed:.1f}")
    print(f"  lens scans            : {lens.scan_count}")
    print(f"  lens.emitted_count    : {lens.emitted_count}")
    print(f"  lens.emit_failures    : {lens.emit_failures}")
    print(f"  consumer.received     : {consumer.total_received}")
    print(f"  consumer.by_lens      : {dict(consumer.by_lens)}")
    print(f"  consumer.by_type      : {dict(consumer.by_type)}")
    bs = bus.stats()
    print(f"  bus.published         : {bs['published']}")
    print(f"  bus.validation_fails  : {bs['validation_failures']}")
    print(f"  bus.subscribers       : {len(bs['subscribers'])}")
    for s in bs["subscribers"]:
        print(f"    - {s['name']:25s} pattern={s['pattern']:15s}  "
              f"delivered={s['delivered']}  dropped={s['dropped']}")
    print()
    if lens.emitted_count == 0:
        print("WARNING: lens emitted 0 signals — check that monitored pools "
              "actually overlap with L3 corpus entries.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    args = _parse_args()
    code = asyncio.run(run(args))
    sys.exit(code)
