"""Triangular- (and cyclic-) arb prevalence in the captured blocks.

A triangular arb is ONE transaction that swaps through a cycle of >=3 pools and
comes back to the starting token with a profit (e.g. WETH->A->B->WETH). On-chain
its signature is: multiple DEX Swap events across DISTINCT pools in a single tx,
plus a net-positive settlement-token delta for the searcher (a profitable closed
cycle — as opposed to a user multi-hop, which ends in a DIFFERENT token and nets
~zero/negative).

We count cycle length from Uniswap V2 + V3 Swap events (unambiguous topics).
LIMITATION: Uniswap V4 routes every swap through a singleton PoolManager, so V4
hops can't be counted by emitter address (they need PoolId parsing). V4-routed
triangular arbs are therefore UNDERCOUNTED — every number here is a LOWER BOUND,
especially for recent activity. V4 involvement is flagged separately so the
undercount is visible, not hidden.
"""

from __future__ import annotations

import duckdb

from .backrun_profit import realized_profit
from .backrun_verify import _build_labeled, available_windows  # reuse views

# Unambiguous Swap event topic0 hashes
V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
V4_POOLMANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"


def swap_pools(receipt) -> set[str]:
    """Distinct V2/V3 pool addresses that emitted a Swap event in this tx.
    len() == the (V2/V3-countable) cycle length / hop count."""
    pools = set()
    if not receipt:
        return pools
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if topics and (topics[0] or "").lower() in (V2_SWAP, V3_SWAP):
            a = (log.get("address") or "").lower()
            if a:
                pools.add(a)
    return pools


def v4_touched(receipt) -> bool:
    if not receipt:
        return False
    return any((lg.get("address") or "").lower() == V4_POOLMANAGER
               for lg in (receipt.get("logs") or []))


def classify_tx(receipt, eth_price_usd: float = 2500.0,
                min_profit_usd: float = 1.0) -> dict:
    """Classify a confirmed tx by cyclic-swap structure + profitability."""
    pools = swap_pools(receipt)
    hops = len(pools)
    v4 = v4_touched(receipt)
    profit = realized_profit(receipt, eth_price_usd)["net_token_usd"]
    profitable = profit is not None and profit > min_profit_usd
    if profitable and hops >= 3:
        kind = "triangular_plus_arb"          # >=3 distinct V2/V3 pools, profit
    elif profitable and hops == 2:
        kind = "two_pool_arb"
    elif profitable and v4:
        kind = "v4_arb_hops_uncounted"        # profitable cyclic swap via V4
    elif hops >= 2 or v4:
        kind = "multihop_not_arb"             # user multi-hop / unprofitable
    elif hops == 1:
        kind = "single_swap"
    else:
        kind = "non_swap"
    return {"hops": hops, "v4": v4, "profit_usd": profit,
            "profitable": profitable, "kind": kind}


def sample_confirmed_txs(window_keys, n: int = 1500, seed: int = 21):
    """Random sample of (hash, seen) over ALL confirmed txs in the windows —
    `seen` = was it in our public mempool (public) or not (private/unseen)."""
    con = duckdb.connect()
    try:
        if not _build_labeled(con, window_keys):
            return []
        rows = con.execute(
            f"SELECT h, seen FROM labeled "
            f"ORDER BY hash(h || '{int(seed)}') LIMIT {int(n)}").fetchall()
        return [(h, bool(s)) for h, s in rows]
    finally:
        con.close()


__all__ = ["V2_SWAP", "V3_SWAP", "V4_POOLMANAGER", "swap_pools", "v4_touched",
           "classify_tx", "sample_confirmed_txs", "available_windows"]
