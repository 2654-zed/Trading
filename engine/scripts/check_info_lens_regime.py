"""Gate #3 (beta-vs-alpha) for H9 — market-neutral test (UNK-016).

A true second time-window isn't available (L3 role labels end 2026-05-18
and the signal is L3-bound; Alchemy CU can't reconstruct role labels).
So instead of a separate bear window, we remove market beta directly:

  excess_return = token_forward_return − market_basket_forward_return

where the basket = equal-weighted mean forward return of all priced
monitored tokens over the same [T, T+h]. If corr(entropy_drop strength,
excess_return) holds, the signal predicts OUTPERFORMANCE regardless of
market direction — real alpha. If it collapses toward 0, the raw +0.48
was beta (strong signals fired on high-beta tokens during a rally).

Secondary: split signals by contemporaneous market direction (up/down
phase) and report corr within each.

$0 CU.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
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
    start_ts, end_ts = l3.get_data_time_range(toks)
    span = end_ts - start_ts
    slice_seconds = max(span / max(args.target_windows, 1), 1.0)
    split_ts = start_ts + 0.7 * span
    horizons = [24*3600, 48*3600]

    px = DefiLlamaPriceHistory(period="4h")
    px.prefetch(toks, start_ts, end_ts + max(horizons))

    def fwd_ret(tok, T, h):
        p0 = px.price_at(tok, T); p1 = px.price_at(tok, T + h)
        if p0 and p1 and p0 > 0:
            return (p1 - p0) / p0
        return None

    def market_ret(T, h):
        rs = [fwd_ret(t, T, h) for t in toks]
        rs = [r for r in rs if r is not None]
        return (sum(rs) / len(rs)) if rs else None

    bus = EventBus(default_queue_size=100_000)
    sub = await bus.subscribe("signal.information.entropy_drop")
    lens = InformationLens(
        monitored_set=monitored, data_source=l3,
        replay_clock=ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                                 lookback_seconds=slice_seconds))
    await lens.run(bus)
    sigs = [s for s in _drain(sub)
            if (s.metadata or {}).get("source") == "org_transfer_roles"]
    print(f"entropy_drop/org_transfer_roles signals: {len(sigs)}", flush=True)

    # Collect per signal/horizon: strength, raw return, market return, excess.
    data = {h: [] for h in horizons}  # (T, strength, raw, mkt, excess)
    for s in sigs:
        addr = (s.metadata or {}).get("address", "").lower()
        T = s.timestamp
        for h in horizons:
            r = fwd_ret(addr, T, h)
            m = market_ret(T, h)
            if r is None or m is None:
                continue
            data[h].append((T, s.strength, r, m, r - m))

    def report(label, lo, hi):
        for h in horizons:
            rows = [d for d in data[h] if lo <= d[0] < hi]
            if len(rows) < 5:
                print(f"  {label:9s} {h//3600:>3d}h  (n={len(rows)} too few)")
                continue
            strg = [d[1] for d in rows]
            raw = [d[2] for d in rows]
            exc = [d[4] for d in rows]
            c_raw = _pearson(strg, raw)
            c_exc = _pearson(strg, exc)
            mkt_mean = sum(d[3] for d in rows) / len(rows)
            print(f"  {label:9s} {h//3600:>3d}h  n={len(rows):>3d}  "
                  f"corr(str,RAW)={_f(c_raw)}  corr(str,EXCESS)={_f(c_exc)}  "
                  f"mkt_mean={mkt_mean:+.1%}")

    print()
    print("=" * 74)
    print("H9 GATE 3 — beta-vs-alpha (market-neutral excess return)")
    print("=" * 74)
    report("in-sample", start_ts, split_ts)
    report("holdout", split_ts, end_ts + 1)
    report("ALL", start_ts, end_ts + 1)

    # Secondary: up-phase vs down-phase (by contemporaneous market dir).
    print()
    print("up-phase vs down-phase (by contemporaneous market direction):")
    for h in horizons:
        up = [(d[1], d[2]) for d in data[h] if d[3] > 0]
        dn = [(d[1], d[2]) for d in data[h] if d[3] <= 0]
        c_up = _pearson([a for a, _ in up], [b for _, b in up]) if len(up) >= 5 else None
        c_dn = _pearson([a for a, _ in dn], [b for _, b in dn]) if len(dn) >= 5 else None
        print(f"  {h//3600:>3d}h  up: n={len(up):>3d} corr(str,raw)={_f(c_up)}   "
              f"down: n={len(dn):>3d} corr(str,raw)={_f(c_dn)}")
    l3.close()
    return 0


def _f(c):
    return f"{c:+.3f}" if c is not None else "  n/a"


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


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
