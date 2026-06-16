"""H9 live paper-trading driver.

Runs the validated H9 strategy forward against the CURRENT L3 data (a
fresh `surveillance.db` dump) + free DefiLlama prices, accumulating a
live out-of-sample track record across runs. NO capital, ~$0 Alchemy
(reads L3 SQLite + free price API).

Each invocation:
  1. loads persisted paper state (open positions + closed trades + dedup),
  2. runs the information lens over the available L3 window to extract
     `entropy_drop`/org_transfer_roles signals,
  3. opens paper positions for NEW strong signals (idempotent — re-runs
     over overlapping data don't double-open),
  4. marks open positions to market at the latest data timestamp (closes
     matured ones at real forward prices; deaths after a grace window),
  5. saves state + prints the running track record.

Intended cadence: re-dump surveillance.db daily (railway ssh + sqlite3
.dump) then run this. The track record is genuine forward OOS evidence —
the second window that confirms or kills H9 (UNK-016 / D-044).

Usage:
    python -m engine.scripts.paper_trade_h9
    python -m engine.scripts.paper_trade_h9 --position-usd 10000 --strength 0.5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _atomic_write_json(path: Path, data: dict) -> None:
    """Crash-safe state write: rotate a .bak of the last good state, write
    to a temp file, fsync, then atomically rename over the target. A power
    loss mid-save leaves either the prior state or the .bak intact — never
    a truncated/corrupt JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on the same filesystem


def _load_state(path: Path):
    """Load state, falling back to the .bak if the primary is missing or
    corrupt (e.g. a crash during a pre-atomic-fix write)."""
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
    return None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..adapters.l3_corpus_phase2 import Phase2L3CorpusAdapter
from ..adapters.monitored_set_phase2 import Phase2MonitoredSetAdapter
from ..adapters.price_history import DefiLlamaPriceHistory
from ..core.event_bus import EventBus
from ..core.replay_clock import ReplayClock
from ..lenses.information.lens import InformationLens
from ..paper.h9_paper_trader import H9PaperTrader, DEFAULT_HORIZON_SECONDS


def _tokens(monitored):
    return list({v['address'].lower() for p in monitored.list_pools()
                 for v in (p.get('token0'), p.get('token1'))
                 if isinstance(v, dict) and v.get('address')})


def _token_pool_info(monitored):
    best: dict[str, tuple[float, float]] = {}
    for p in monitored.list_pools():
        tvl = float(p.get("tvl_usd_at_enumeration") or 0.0)
        fee = float(p.get("fee_bps") or 30)
        for k in ("token0", "token1"):
            v = p.get(k)
            if isinstance(v, dict) and v.get("address"):
                a = v["address"].lower()
                if a not in best or tvl > best[a][1]:
                    best[a] = (fee, tvl)
    return best


async def run(args) -> int:
    print(f"[paper_trade_h9] {datetime.now(timezone.utc).isoformat()}", flush=True)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    toks = _tokens(monitored)
    pool_info = _token_pool_info(monitored)
    rng = l3.get_data_time_range(toks)
    if rng is None:
        print("ERROR: no L3 data", file=sys.stderr); return 2
    start_ts, end_ts = rng
    print(f"  L3 data window: {datetime.fromtimestamp(start_ts, timezone.utc).date()} "
          f".. {datetime.fromtimestamp(end_ts, timezone.utc).date()} "
          f"({(end_ts-start_ts)/86400:.1f}d)", flush=True)

    # Prices: cover the data window + horizon so matured positions can exit.
    px = DefiLlamaPriceHistory(period="4h")
    px.prefetch(toks, start_ts, end_ts + DEFAULT_HORIZON_SECONDS + 2 * 86400)
    print(f"  price coverage: {100*px.coverage(toks):.0f}%", flush=True)

    # Extract entropy_drop / org_transfer_roles signals over the window.
    slice_seconds = max((end_ts - start_ts) / max(args.target_windows, 1), 1.0)
    bus = EventBus(default_queue_size=100_000)
    sub = await bus.subscribe("signal.information.entropy_drop")
    lens = InformationLens(
        monitored_set=monitored, data_source=l3,
        replay_clock=ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                                 lookback_seconds=slice_seconds))
    await lens.run(bus)
    signals = []
    while not sub.queue.empty():
        s = sub.queue.get_nowait()
        if (s.metadata or {}).get("source") == "org_transfer_roles":
            signals.append(s)
    signals.sort(key=lambda s: s.timestamp)
    # OOS DISCIPLINE: only OPEN positions for signals at/after the
    # out-of-sample cutoff. Everything up to 2026-05-18 is the in-sample
    # window the signal was DISCOVERED on — trading it would inflate the
    # record with non-evidence. The live track record must be forward-only.
    pre = [s for s in signals if s.timestamp < args.oos_start_ts]
    signals = [s for s in signals if s.timestamp >= args.oos_start_ts]
    cutoff_date = datetime.fromtimestamp(args.oos_start_ts, timezone.utc).date()
    print(f"  entropy_drop/roles signals: {len(pre)} in-sample (<{cutoff_date}, "
          f"EXCLUDED) + {len(signals)} out-of-sample (≥{cutoff_date}, traded)",
          flush=True)
    if not signals:
        print("  → no out-of-sample signals yet. Re-dump L3 with post-cutoff "
              "data to begin accumulating the forward record.", flush=True)

    # Paper trader: load prior state, process signals, mark to market.
    trader = H9PaperTrader(px, pool_info,
                           strength_threshold=args.strength,
                           position_usd=args.position_usd)
    state_path = args.state_path
    prior = _load_state(state_path)
    if prior is not None:
        trader.load_state(prior)
        print(f"  loaded state: {len(trader.seen)} seen, "
              f"{len(trader.open)} open, {len(trader.closed)} closed", flush=True)

    opened = 0
    for s in signals:
        if trader.process_signal(
            token=(s.metadata or {}).get("address", ""),
            signal_ts=s.timestamp, strength=s.strength,
            signal_type=s.type, source=(s.metadata or {}).get("source", ""),
        ):
            opened += 1
    closed = trader.mark_to_market(end_ts)

    _atomic_write_json(state_path, trader.to_dict())

    tr = trader.track_record()
    print()
    print("=" * 64)
    print("H9 PAPER-TRADE — live out-of-sample track record")
    print("=" * 64)
    print(f"  opened this run : {opened}")
    print(f"  closed this run : {closed}")
    print(f"  positions open  : {tr.get('open', 0)}")
    print(f"  trades closed   : {tr.get('closed', 0)}")
    if tr.get("closed", 0) > 0:
        print(f"  win rate        : {tr['win_rate']:.0%}")
        print(f"  mean net/trade  : {tr['mean_net']:+.1%}")
        print(f"  median net/trade: {tr['median_net']:+.1%}")
        print(f"  best / worst    : {tr['best_net']:+.0%} / {tr['worst_net']:+.0%}")
        print(f"  deaths          : {tr['deaths']}")
        print(f"  cumulative P&L  : ${tr['cum_pnl_usd']:+,.0f} "
              f"(@ ${tr['position_usd']:,.0f}/trade)")
    print(f"  state saved     : {state_path}")
    print()
    print("  NOTE: paper only — no capital, no on-chain action. Track record is")
    print("  forward OOS evidence for H9. Re-dump L3 daily + re-run to accumulate.")
    l3.close()
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--strength", type=float, default=0.5,
                   help="entropy_drop strength threshold for the strong cohort")
    p.add_argument("--position-usd", type=float, default=10_000.0)
    p.add_argument("--target-windows", type=int, default=200)
    p.add_argument("--oos-start-ts", type=float, default=1779075697.0,
                   help="epoch cutoff; signals before it are in-sample and "
                        "NOT traded (default ≈2026-05-18, the in-sample end)")
    p.add_argument("--state-path", type=Path,
                   default=Path("engine/data/h9_paper_state.json"))
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
