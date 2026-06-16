"""Standalone investigation of the information lens (post-D-042 silver
lining). Runs ONLY the info lens over the historical window, attaches
REAL forward price returns to each emitted signal, and breaks predictive
power down by signal type, data source, horizon, and direction.

Answers:
  - Which signal type (entropy_drop / regime_surprise / divergence_spike)
    carries the edge?
  - Which data source (liquidity_events vs org_transfer_roles)?
  - Does it predict MAGNITUDE (|return|) only, or DIRECTION (signed)?
  - How does it vary with forward horizon?

$0 CU (DefiLlama), read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import defaultdict
from pathlib import Path

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


def _pearson(xs, ys):
    n = len(xs)
    if n < 5:
        return None
    mx, my = sum(xs)/n, sum(ys)/n
    cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys))
    vx = sum((x-mx)**2 for x in xs); vy = sum((y-my)**2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov/math.sqrt(vx*vy)


def _tokens(monitored):
    return list({v['address'].lower() for p in monitored.list_pools()
                 for v in (p.get('token0'), p.get('token1'))
                 if isinstance(v, dict) and v.get('address')})


async def run(args) -> int:
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    toks = _tokens(monitored)
    rng = l3.get_data_time_range(toks)
    start_ts, end_ts = rng
    span = end_ts - start_ts
    slice_seconds = max(span / max(args.target_windows, 1), 1.0)
    horizons = [12*3600, 24*3600, 48*3600]

    px = DefiLlamaPriceHistory(period="4h")
    px.prefetch(toks, start_ts, end_ts + max(horizons))
    print(f"price coverage: {100*px.coverage(toks):.0f}%", flush=True)

    # Run ONLY the info lens, capture every signal.
    bus = EventBus(default_queue_size=100_000)
    sub = await bus.subscribe("signal.information.*")
    lens = InformationLens(
        monitored_set=monitored, data_source=l3,
        replay_clock=ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                                 lookback_seconds=slice_seconds))
    await lens.run(bus)

    sigs = []
    while not sub.queue.empty():
        sigs.append(sub.queue.get_nowait())
    print(f"info-lens signals emitted: {len(sigs)}", flush=True)

    # Attach forward returns. Group by (type, source). Track ts for an
    # out-of-sample (train 70% / holdout 30%) split.
    split_ts = start_ts + 0.7 * span
    by_group = defaultdict(lambda: {h: {"str": [], "signed": [], "abs": [],
                                        "ts": []} for h in horizons})
    type_counts = defaultdict(int)
    for s in sigs:
        md = s.metadata or {}
        addr = (md.get("address") or "").lower()
        source = md.get("source", "?")
        type_counts[(s.type, source)] += 1
        if not addr:
            continue
        T = s.timestamp
        p0 = px.price_at(addr, T)
        if not p0 or p0 <= 0:
            continue
        for h in horizons:
            p1 = px.price_at(addr, T + h)
            if not p1:
                continue
            ret = (p1 - p0) / p0
            g = by_group[(s.type, source)][h]
            g["str"].append(s.strength)
            g["signed"].append(ret)
            g["abs"].append(abs(ret))
            g["ts"].append(T)

    print()
    print("=" * 78)
    print("INFORMATION LENS — standalone predictive breakdown")
    print("=" * 78)
    print(f"{'type':18s} {'source':20s} {'n':>5s} {'h':>4s} "
          f"{'corr|str,|ret||':>14s} {'corr(str,ret)':>13s} {'mean|ret|':>9s}")
    for (typ, source), hd in sorted(by_group.items()):
        for h in horizons:
            g = hd[h]
            n = len(g["str"])
            if n < 5:
                continue
            c_abs = _pearson(g["str"], g["abs"])
            c_sgn = _pearson(g["str"], g["signed"])
            mabs = sum(g["abs"])/n
            print(f"{typ:18s} {source:20s} {n:>5d} {h//3600:>3d}h "
                  f"{(f'{c_abs:+.3f}' if c_abs is not None else '  n/a'):>14s} "
                  f"{(f'{c_sgn:+.3f}' if c_sgn is not None else ' n/a'):>13s} "
                  f"{mabs:>8.2%}")
    print()
    print("signal type×source counts:", dict(type_counts))

    # ---- OUT-OF-SAMPLE split (the make-or-break test) ----
    print()
    print("=" * 78)
    print(f"OUT-OF-SAMPLE (train < {split_ts:.0f} ≤ holdout) — signed-return corr")
    print("=" * 78)
    print(f"{'type':18s} {'source':20s} {'h':>4s} "
          f"{'n_tr':>5s} {'corr_train':>11s} {'n_ho':>5s} {'corr_holdout':>13s}")
    for (typ, source), hd in sorted(by_group.items()):
        for h in horizons:
            g = hd[h]
            tr_s = [s for s, t in zip(g["str"], g["ts"]) if t < split_ts]
            tr_r = [r for r, t in zip(g["signed"], g["ts"]) if t < split_ts]
            ho_s = [s for s, t in zip(g["str"], g["ts"]) if t >= split_ts]
            ho_r = [r for r, t in zip(g["signed"], g["ts"]) if t >= split_ts]
            if len(tr_s) < 5 and len(ho_s) < 5:
                continue
            c_tr = _pearson(tr_s, tr_r)
            c_ho = _pearson(ho_s, ho_r)
            print(f"{typ:18s} {source:20s} {h//3600:>3d}h "
                  f"{len(tr_s):>5d} "
                  f"{(f'{c_tr:+.3f}' if c_tr is not None else '  n/a'):>11s} "
                  f"{len(ho_s):>5d} "
                  f"{(f'{c_ho:+.3f}' if c_ho is not None else '  n/a'):>13s}")
    l3.close()
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-windows", type=int, default=200)
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
