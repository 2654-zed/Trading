"""Triangular-arb prevalence in the captured blocks (read-only, sampled).

Samples confirmed txs, pulls receipts, classifies each by cyclic-swap
structure, and reports how common triangular (>=3-pool cyclic) arbs are, their
cycle length, who wins them (public vs private), and rough profit. V4 cycles
are undercounted (flagged) — treat results as a lower bound.

Usage:
    python -m engine.scripts.analyze_triangular_arb --n 1500 --eth-price 2500
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
import time
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.block_ingest import DEFAULT_RPCS, RpcError, rpc_call
from ..bloxroute import triangular_analysis as ta


def _fetch(hashes, rpcs, timeout=10.0, sleep=0.04):
    out, i, fails = {}, 0, 0
    for k, h in enumerate(hashes):
        r = None
        for _ in range(len(rpcs)):
            try:
                r = rpc_call(rpcs[i], "eth_getTransactionReceipt", [h], timeout)
                break
            except RpcError:
                i = (i + 1) % len(rpcs)
        if r is None:
            fails += 1
        out[h] = r
        if (k + 1) % 200 == 0:
            print(f"  fetched {k+1}/{len(hashes)} ({fails} failed)", flush=True)
        time.sleep(sleep)
    return out, fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--eth-price", type=float, default=2500.0)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    wins = ta.available_windows()
    if not wins:
        print("no overlapping windows.", file=sys.stderr)
        return 1
    sample = ta.sample_confirmed_txs(wins, n=args.n, seed=args.seed)
    if not sample:
        print("no confirmed txs sampled.", file=sys.stderr)
        return 1
    print(f"sampling {len(sample)} confirmed txs from {len(wins)} windows; "
          f"fetching receipts (read-only)...", flush=True)
    rcpts, fails = _fetch([h for h, _ in sample], DEFAULT_RPCS, timeout=args.timeout)

    kinds = Counter()
    arb_hops, tri_profit, tri_seen, v4_any = [], [], Counter(), 0
    n_ok = 0
    ARB = {"two_pool_arb", "triangular_plus_arb", "v4_arb_hops_uncounted"}
    for h, seen in sample:
        r = rcpts.get(h)
        if r is None:
            continue
        n_ok += 1
        c = ta.classify_tx(r, args.eth_price)
        kinds[c["kind"]] += 1
        if c["v4"]:
            v4_any += 1
        if c["kind"] == "triangular_plus_arb":
            arb_hops.append(c["hops"])
            tri_profit.append(c["profit_usd"])
            tri_seen["private" if not seen else "public"] += 1

    def pct(x, d=None):
        d = d if d is not None else n_ok
        return f"{100*x/d:.2f}%" if d else "n/a"

    n_tri = kinds["triangular_plus_arb"]
    n_arb = sum(kinds[k] for k in ARB)
    print("\n" + "=" * 66)
    print(f"TRIANGULAR-ARB PREVALENCE  (n={n_ok} confirmed txs analysed)")
    print("=" * 66)
    print("  tx composition:")
    for k in ("non_swap", "single_swap", "multihop_not_arb", "two_pool_arb",
              "triangular_plus_arb", "v4_arb_hops_uncounted"):
        print(f"    {k:<24} {kinds[k]:>6}  {pct(kinds[k])}")
    print(f"\n  ANY profitable cyclic arb (2-pool + 3+ + v4): {n_arb}  ({pct(n_arb)})")
    print(f"  TRIANGULAR (>=3 distinct V2/V3 pools, profit): {n_tri}  ({pct(n_tri)})")
    if arb_hops:
        print(f"  triangular cycle length (hops): {dict(Counter(arb_hops))}")
        print(f"  triangular winner: {dict(tri_seen)}  (public=we-saw-it, private=unseen)")
        if tri_profit:
            print(f"  triangular profit $ (untracked-token-limited): "
                  f"median ${st.median(tri_profit):,.0f}  max ${max(tri_profit):,.0f}")
    print(f"\n  V4 coverage caveat: {v4_any} of {n_ok} sampled txs "
          f"({pct(v4_any)}) touched the V4 PoolManager — their cycle length is")
    print("    NOT counted here, so triangular prevalence is a LOWER BOUND.")
    print("\n  INTERPRETATION")
    print("  * This counts how OFTEN triangular arbs happen + who wins them.")
    print("  * Cross-reference with the backrun finding: these are the same")
    print("    atomic-arb game — sparse, mostly bribed to builders, and the")
    print("    'private' winners are the ones a public-feed actor can't beat.")
    print("  * NOT a profitability verdict for US: prevalence != capturable by")
    print("    a laptop with a public feed and no bundle infra.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
