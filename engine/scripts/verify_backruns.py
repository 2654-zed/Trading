"""Verify what fraction of backrun-slot txs are REAL backruns of the swap.

Read-only. Samples (swap, next-tx) pairs from the captured data, pulls both
receipts from a FREE public RPC, and checks whether next-tx touched the same
specific pool the swap moved. Reports the verified-backrun rate, split by
whether the backrunner was seen (public) or unseen (private), with a random
-pair control for the null.

Usage:
    python -m engine.scripts.verify_backruns --n 150
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.block_ingest import DEFAULT_RPCS, RpcError, rpc_call
from ..bloxroute import backrun_verify as bv


def _fetch_receipts(hashes, rpcs, timeout=10.0, sleep=0.05):
    """Sequential, endpoint-rotating receipt fetch. Returns hash -> set(touched
    contracts). Missing/failed receipts map to an empty set (no shared pool)."""
    touched = {}
    i = 0
    fails = 0
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
        touched[h] = bv.extract_touched_contracts(rcpt)
        if (k + 1) % 50 == 0:
            print(f"  fetched {k+1}/{len(hashes)} receipts "
                  f"({fails} failed)", flush=True)
        time.sleep(sleep)
    return touched, fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150, help="pairs to sample")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    wins = bv.available_windows()
    if not wins:
        print("no overlapping capture windows.", file=sys.stderr)
        return 1
    print(f"sampling {args.n} backrun pairs + {args.n} control pairs "
          f"from {len(wins)} windows...", flush=True)
    pairs = bv.sample_backrun_pairs(wins, n=args.n, seed=args.seed)
    control = bv.sample_control_pairs(wins, n=args.n, seed=args.seed + 1)
    if not pairs:
        print("no public-swap backrun pairs found.", file=sys.stderr)
        return 1

    hashes = set()
    for s, nx, _ in pairs:
        hashes.add(s); hashes.add(nx)
    for a, b in control:
        hashes.add(a); hashes.add(b)
    hashes = sorted(hashes)
    print(f"fetching {len(hashes)} unique receipts (read-only, free RPC)...",
          flush=True)
    touched, fails = _fetch_receipts(hashes, DEFAULT_RPCS, timeout=args.timeout)

    overlap = []
    examples = []
    for s, nx, seen in pairs:
        shared = bv.shared_specific(touched.get(s, set()), touched.get(nx, set()))
        overlap.append({"next_seen": seen, "shared": bool(shared)})
        if shared and len(examples) < 5:
            examples.append((s, nx, seen, sorted(shared)[:2]))
    ctrl_shared = sum(1 for a, b in control
                      if bv.shared_specific(touched.get(a, set()),
                                            touched.get(b, set())))
    ctrl_rate = ctrl_shared / len(control) if control else None

    s = bv.summarize(overlap)

    def pct(x):
        return f"{100*x:.1f}%" if isinstance(x, (int, float)) else "n/a"

    print("\n" + "=" * 66)
    print("VERIFIED BACKRUN RATE  (does the i+1 tx arb the swap's pool?)")
    print("=" * 66)
    print(f"  receipts fetched ok           : {len(hashes)-fails}/{len(hashes)}")
    print(f"  backrun pairs analysed        : {s['n_pairs']}")
    print(f"  VERIFIED backrun rate (overall): {pct(s['verified_backrun_rate'])}")
    print(f"    public backrunner (seen)    : {pct(s['verified_rate_public'])}  "
          f"(n={s['n_public_backrunner']})")
    print(f"    private backrunner (unseen) : {pct(s['verified_rate_private'])}  "
          f"(n={s['n_private_backrunner']})")
    print(f"  CONTROL (random unrelated pair): {pct(ctrl_rate)}  "
          f"(n={len(control)})  <- the null; should be ~0")
    if examples:
        print("  sample verified backruns (swap -> next, shared pool):")
        for sw, nx, seen, pools in examples:
            print(f"    {sw[:12]} -> {nx[:12]} [{'pub' if seen else 'priv'}]  pool {pools}")
    print("\n  INTERPRETATION")
    print("  * verified rate >> control  => the i+1 tx really does arb the swap's")
    print("    pool (real backruns), not coincidental adjacency.")
    print("  * if private(unseen) rate > public(seen) rate => the real backruns")
    print("    are disproportionately won by flow we can't see/compete with.")
    print("  * STILL not PnL: this confirms backruns HAPPEN and who does them,")
    print("    not that they were profitable or how much. Single vantage; the")
    print("    'unseen' label is a coverage flag, sample is router-set-limited.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
