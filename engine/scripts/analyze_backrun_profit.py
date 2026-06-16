"""Realized-profit distribution of verified backruns (the question that
decides whether a backrun engine is worth building).

Samples public-swap → backrun pairs, fetches receipts read-only, keeps the
VERIFIED backruns (i+1 arbs the swap's pool), and computes each one's realized
gross profit, gas, and net-of-gas (excludes the unmeasurable builder bribe —
so net_visible is an UPPER bound on searcher take-home but the right measure of
total arb value on the table).

Usage:
    python -m engine.scripts.analyze_backrun_profit --n 300 --eth-price 2500
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.block_ingest import DEFAULT_RPCS, RpcError, rpc_call
from ..bloxroute import backrun_verify as bv
from ..bloxroute.backrun_profit import realized_profit


def _fetch_receipts(hashes, rpcs, timeout=10.0, sleep=0.04):
    out, i, fails = {}, 0, 0
    for k, h in enumerate(hashes):
        rcpt = None
        for _ in range(len(rpcs)):
            try:
                rcpt = rpc_call(rpcs[i], "eth_getTransactionReceipt", [h], timeout)
                break
            except RpcError:
                i = (i + 1) % len(rpcs)
        if rcpt is None:
            fails += 1
        out[h] = rcpt
        if (k + 1) % 100 == 0:
            print(f"  fetched {k+1}/{len(hashes)} ({fails} failed)", flush=True)
        time.sleep(sleep)
    return out, fails


def _pctile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--eth-price", type=float, default=2500.0)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    wins = bv.available_windows()
    if not wins:
        print("no overlapping windows.", file=sys.stderr)
        return 1
    pairs = bv.sample_backrun_pairs(wins, n=args.n, seed=args.seed)
    if not pairs:
        print("no backrun pairs.", file=sys.stderr)
        return 1
    hashes = sorted({h for s, nx, _ in pairs for h in (s, nx)})
    print(f"{len(pairs)} pairs; fetching {len(hashes)} receipts "
          f"(read-only, eth=${args.eth_price:.0f})...", flush=True)
    rcpts, fails = _fetch_receipts(hashes, DEFAULT_RPCS, timeout=args.timeout)

    verified = []
    for s, nx, seen in pairs:
        sw, bk = rcpts.get(s), rcpts.get(nx)
        if not sw or not bk:
            continue
        if bv.shared_specific(bv.extract_touched_contracts(sw),
                              bv.extract_touched_contracts(bk)):
            p = realized_profit(bk, args.eth_price)
            p["next_seen"] = seen
            p["hash"] = nx
            verified.append(p)

    gross = [p["net_token_usd"] for p in verified]
    gas = [p["gas_usd"] for p in verified]
    net = [p["pnl_usd"] for p in verified]
    n_profit = sum(1 for x in net if x > 0)

    def d(label, xs):
        if not xs:
            print(f"  {label}: (none)")
            return
        print(f"  {label}: median ${st.median(xs):,.0f}  p90 ${_pctile(xs,0.9):,.0f}  "
              f"max ${max(xs):,.0f}  sum ${sum(xs):,.0f}")

    print("\n" + "=" * 66)
    print(f"REALIZED BACKRUN PROFIT  (n={len(verified)} verified backruns)")
    print("=" * 66)
    print(f"  receipts ok: {len(hashes)-fails}/{len(hashes)}")
    if verified:
        print(f"  PnL-positive (ex builder bribe): "
              f"{n_profit}/{len(verified)} = {100*n_profit/len(verified):.0f}%")
        d("net token value (cross-token)", gross)
        d("gas cost                     ", gas)
        d("PnL ex-bribe (net of gas)    ", net)
        toks = {}
        for p in verified:
            toks[p["profit_token"]] = toks.get(p["profit_token"], 0) + 1
        print(f"  profit token mix: {toks}")
        print("  top verified backruns by gross (look these up to sanity-check):")
        for t in sorted(verified, key=lambda x: x["gross_usd"], reverse=True)[:5]:
            print(f"    ${t['gross_usd']:>12,.0f}  {t['hash']}  "
                  f"{'priv' if not t['next_seen'] else 'pub'}")
    print("\n  INTERPRETATION")
    print("  * 'gross arb value' = total $ on the table per backrun. If it's a")
    print("    few dollars, the game is marginal even before competition.")
    print("  * 'net of gas' EXCLUDES the builder bribe (invisible in logs). It")
    print("    is an UPPER BOUND on what a searcher keeps; the real take-home is")
    print("    lower because most of the gross is bid back to the builder.")
    print("  * For US (no bundle infra, submitting publicly, last in the queue):")
    print("    we would capture a FRACTION of 'net of gas' at best. If net of gas")
    print("    is small, a public backrun engine is not worth building.")
    print(f"  * ETH=${args.eth_price:.0f} assumed; for WETH-settled arbs the")
    print("    profitable/not SIGN is price-independent, only the $ size scales.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
