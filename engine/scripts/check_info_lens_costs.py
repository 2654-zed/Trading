"""Gate #2 (costs) for H9 — does the +0.50 edge survive memecoin slippage?
(UNK-016, final gate.)

Strategy under test (the gate-3 regime-robust one): on a strong
`entropy_drop`-on-roles signal, go LONG the token, hold 48h, exit.

Per-trade economics for position size X (USD) in the token's best
(max-TVL) monitored pool:
    gross           = token 48h forward return
    fee_frac        = pool fee_bps / 10000          (paid each leg)
    slip_frac(X)    = X / (TVL/2 + X)               (constant-product, each leg)
    net_multiplier  = (1+gross) · (1-fee)^2 · (1-slip)^2
    net_return      = net_multiplier - 1 - gas_usd / X

Sweeps position size to find the sweet spot (small = gas-dominated,
large = slippage-dominated). Reports mean gross vs net, win rate, and
the strong-vs-weak cohort spread. $0 CU (TVL-at-enumeration depth proxy;
exact depth would need <100K CU but this sizes the answer first).
"""

from __future__ import annotations

import argparse
import asyncio
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


def _tokens(monitored):
    return list({v['address'].lower() for p in monitored.list_pools()
                 for v in (p.get('token0'), p.get('token1'))
                 if isinstance(v, dict) and v.get('address')})


def _token_best_pool(monitored):
    """token_lower -> (fee_bps, tvl_usd) of its highest-TVL monitored pool."""
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


def net_return(gross, fee_bps, tvl, X, gas_usd=0.10):
    """Realistic round-trip: ENTRY slippage on position X, EXIT slippage on
    the POST-move position value X·(1+gross) — so a token that pumped is
    dumped as a bigger bag (the dominant cost on winners). TVL held constant
    (conservative: pumps often raise TVL, which would reduce exit slip)."""
    fee = fee_bps / 10000.0
    reserve = max(tvl / 2.0, 1.0)
    slip_in = X / (reserve + X)
    x_exit = X * (1 + gross) if gross > -1 else 0.0
    slip_out = x_exit / (reserve + x_exit) if x_exit > 0 else 1.0
    mult = (1 + gross) * (1 - fee) ** 2 * (1 - slip_in) * (1 - slip_out)
    return mult - 1 - gas_usd / max(X, 1.0)


async def run(args) -> int:
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    toks = _tokens(monitored)
    best_pool = _token_best_pool(monitored)
    start_ts, end_ts = l3.get_data_time_range(toks)
    span = end_ts - start_ts
    slice_seconds = max(span / max(args.target_windows, 1), 1.0)
    H = 48 * 3600

    px = DefiLlamaPriceHistory(period="4h")
    px.prefetch(toks, start_ts, end_ts + H)

    bus = EventBus(default_queue_size=100_000)
    sub = await bus.subscribe("signal.information.entropy_drop")
    lens = InformationLens(
        monitored_set=monitored, data_source=l3,
        replay_clock=ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                                 lookback_seconds=slice_seconds))
    await lens.run(bus)
    sigs = [s for s in _drain(sub)
            if (s.metadata or {}).get("source") == "org_transfer_roles"]

    # Build trades: (strength, gross_48h, fee_bps, tvl)
    trades = []
    for s in sigs:
        addr = (s.metadata or {}).get("address", "").lower()
        T = s.timestamp
        p0 = px.price_at(addr, T); p1 = px.price_at(addr, T + H)
        if not p0 or not p1 or p0 <= 0:
            continue
        gross = (p1 - p0) / p0
        fee, tvl = best_pool.get(addr, (30.0, 0.0))
        if tvl <= 0:
            continue
        trades.append((s.strength, gross, fee, tvl))

    print(f"tradeable entropy_drop/roles signals (48h, priced, pooled): {len(trades)}")
    if not trades:
        l3.close(); return 1

    # Cohort spread: strong (top half by strength) vs weak.
    trades.sort(key=lambda t: t[0])
    mid = len(trades) // 2
    weak, strong = trades[:mid], trades[mid:]
    mg = lambda ts: sum(t[1] for t in ts) / len(ts)
    print(f"  mean GROSS 48h return — weak half: {mg(weak):+.1%}   "
          f"strong half: {mg(strong):+.1%}   (spread {mg(strong)-mg(weak):+.1%})")
    # TVL distribution of strong-cohort pools.
    tvls = sorted(t[3] for t in strong)
    print(f"  strong-cohort pool TVL: min ${tvls[0]:,.0f}  "
          f"median ${tvls[len(tvls)//2]:,.0f}  max ${tvls[-1]:,.0f}")

    print()
    print("=" * 74)
    print("H9 GATE 2 — net per-trade expectancy after costs (strong cohort, 48h long)")
    print("=" * 74)
    print(f"{'position $':>12s} {'mean_gross':>10s} {'mean_net':>9s} "
          f"{'win%':>6s} {'median_net':>10s} {'verdict':>10s}")
    for X in (1_000, 5_000, 10_000, 25_000, 50_000, 100_000):
        nets = [net_return(g, fee, tvl, X) for (_, g, fee, tvl) in strong]
        mean_net = sum(nets) / len(nets)
        nets_sorted = sorted(nets)
        med = nets_sorted[len(nets_sorted) // 2]
        win = sum(1 for n in nets if n > 0) / len(nets)
        verdict = "EDGE" if mean_net > 0 else "dead"
        print(f"{X:>12,d} {mg(strong):>+9.1%} {mean_net:>+8.1%} "
              f"{win:>5.0%} {med:>+9.1%} {verdict:>10s}")

    # Concentration diagnosis: is the positive mean real edge or a few
    # lottery winners? Use $10k net returns for the strong cohort.
    nets10 = sorted((net_return(g, fee, tvl, 10_000)
                     for (_, g, fee, tvl) in strong), reverse=True)
    n = len(nets10)
    total = sum(nets10)
    top1 = nets10[0]
    top3 = sum(nets10[:3])
    winners = sum(1 for x in nets10 if x > 0)
    mean_no_top3 = (total - top3) / (n - 3) if n > 3 else 0.0
    print()
    print("  CONCENTRATION DIAGNOSIS ($10k position, strong cohort):")
    print(f"    n trades            : {n}")
    print(f"    winners / losers    : {winners} / {n - winners} "
          f"({winners/n:.0%} win)")
    print(f"    top winner net      : {top1:+.0%}")
    print(f"    top-3 share of total: {top3/total:.0%}" if total else "n/a")
    print(f"    mean WITH top-3     : {total/n:+.1%}")
    print(f"    mean WITHOUT top-3  : {mean_no_top3:+.1%}  "
          f"<-- if negative, the 'edge' is a few lottery winners")
    print(f"    return percentiles  : p10={nets10[int(0.9*n)]:+.0%}  "
          f"p50={nets10[n//2]:+.0%}  p90={nets10[int(0.1*n)]:+.0%}")

    # Also: edge restricted to LIQUID pools only (TVL ≥ $1M).
    liquid = [t for t in strong if t[3] >= 1_000_000]
    print(f"\n  strong-cohort restricted to TVL≥$1M pools (n={len(liquid)}):")
    if liquid:
        for X in (10_000, 50_000, 100_000):
            nets = [net_return(g, fee, tvl, X) for (_, g, fee, tvl) in liquid]
            mn = sum(nets)/len(nets)
            win = sum(1 for n in nets if n > 0)/len(nets)
            print(f"    ${X:>7,d}: mean_net {mn:+.1%}  win {win:.0%}  "
                  f"{'EDGE' if mn>0 else 'dead'}")
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
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
