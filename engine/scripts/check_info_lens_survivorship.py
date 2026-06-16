"""Survivorship check for H9 (D-043 / UNK-016 gate #1).

The entropy_drop-on-roles signal showed +0.48 OOS signed-return corr — but
only over tokens that KEPT price coverage. If the signal fires before
tokens DIE (flow concentration = exit liquidity / rug), those deaths are
silently dropped and the positive (directional) edge could be an illusion.

This re-computes the H9 correlation two ways:
  - SURVIVOR-ONLY: current method (clamps to last price; excludes deaths).
  - DEATH-AWARE: a token whose price series ENDS before the forward target
    AND before the global window end (i.e. it disappeared, not just a data
    boundary) is assigned a catastrophic return (default −100%). Tokens
    unpriceable at signal time are likewise treated as already-dead.

If the signed (directional) edge survives DEATH-AWARE, H9's long-thesis is
real. If it collapses/flips, the directional edge was survivorship. The
magnitude (|return|) edge is expected to be more robust either way.

$0 CU, read-only.
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
    DEATH_RETURN = args.death_return
    # A token counts as DEAD (not just data-boundary) if its last priced
    # point is more than `death_margin` before the global window end.
    death_margin = 2 * 86400

    px = DefiLlamaPriceHistory(period="4h")
    px.prefetch(toks, start_ts, end_ts + max(horizons))
    print(f"price coverage: {100*px.coverage(toks):.0f}%", flush=True)

    # token -> last priced ts (series end); None if never priced.
    last_ts = {t: (px._series[t][-1][0] if px._series.get(t) else None)
               for t in toks}

    bus = EventBus(default_queue_size=100_000)
    sub = await bus.subscribe("signal.information.entropy_drop")
    lens = InformationLens(
        monitored_set=monitored, data_source=l3,
        replay_clock=ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                                 lookback_seconds=slice_seconds))
    await lens.run(bus)
    sigs = [s for s in _drain(sub) if (s.metadata or {}).get("source") == "org_transfer_roles"]
    print(f"entropy_drop/org_transfer_roles signals: {len(sigs)}", flush=True)

    # Build paired data: per signal per horizon, survivor-only + death-aware.
    rows = []  # (T, strength, h, ret_surv_or_None, ret_death, dead_flag)
    deaths = 0
    unpriceable_at_signal = 0
    for s in sigs:
        addr = (s.metadata or {}).get("address", "").lower()
        T = s.timestamp
        p0 = px.price_at(addr, T)
        lt = last_ts.get(addr)
        for h in horizons:
            target = T + h
            if not p0 or p0 <= 0:
                # unpriceable at signal time → already-dead in death-aware.
                rows.append((T, s.strength, h, None, DEATH_RETURN, True))
                if h == horizons[0]:
                    unpriceable_at_signal += 1
                continue
            if lt is not None and target <= lt:
                p1 = px.price_at(addr, target)
                ret = (p1 - p0)/p0 if p1 else None
                rows.append((T, s.strength, h, ret, ret if ret is not None else 0.0, False))
            elif lt is not None and lt < (end_ts - death_margin):
                # series ended before target AND well before window end → DIED.
                rows.append((T, s.strength, h, None, DEATH_RETURN, True))
                if h == horizons[0]:
                    deaths += 1
            else:
                # target beyond our data, token not confirmed dead → unknown.
                rows.append((T, s.strength, h, None, None, False))

    print(f"signals on tokens that DIED mid-window: {deaths}", flush=True)
    print(f"signals unpriceable at signal time:    {unpriceable_at_signal}", flush=True)

    print()
    print("=" * 78)
    print("H9 SURVIVORSHIP TEST — signed-return corr(strength, return)")
    print("=" * 78)
    print(f"{'split':10s} {'h':>4s} {'mode':14s} {'n':>5s} {'corr':>8s} {'mean_ret':>9s}")
    for label, lo, hi in [("in-sample", start_ts, split_ts),
                          ("holdout", split_ts, end_ts + 1)]:
        for h in horizons:
            for mode in ("survivor", "death-aware"):
                ss, rr = [], []
                for (T, strg, hh, r_surv, r_death, dead) in rows:
                    if hh != h or not (lo <= T < hi):
                        continue
                    r = r_surv if mode == "survivor" else r_death
                    if r is None:
                        continue
                    ss.append(strg); rr.append(r)
                c = _pearson(ss, rr)
                mr = (sum(rr)/len(rr)) if rr else 0.0
                print(f"{label:10s} {h//3600:>3d}h {mode:14s} {len(ss):>5d} "
                      f"{(f'{c:+.3f}' if c is not None else '  n/a'):>8s} "
                      f"{mr:>+8.1%}")
    l3.close()
    return 0


def _drain(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-windows", type=int, default=200)
    p.add_argument("--death-return", type=float, default=-1.0,
                   help="return assigned to tokens that died (default -1.0)")
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
