"""Verify whether the tx in the backrun slot is a REAL backrun of the swap.

The contestability measurement only showed an unseen tx OCCUPIES the slot after
a swap. This goes one level deeper, read-only: for a sample of (swap, next-tx)
pairs, pull both receipts and check whether they touch the SAME specific
contract (the pool the swap moved) — i.e. whether next-tx actually arbitraged
that swap, vs sitting there for unrelated reasons.

A real backrun trades the SAME pool the victim swap hit, so its logs share that
pool's address. We exclude ubiquitous tokens/routers (WETH, USDC, …, the
routers themselves) so the signal is a shared POOL/specific contract, not the
fact that everything touches WETH. A random-pair control calibrates the null.

Outputs the number that actually matters: "X% of backrun-slot txs are verified
backruns of the swap", split by whether the backrunner was seen (public) or
unseen (private).
"""

from __future__ import annotations

import glob
import os
import re
from typing import Optional

import duckdb

from .backrun_analysis import DEX_ROUTERS

MEMPOOL_DIR = "engine/data/mempool"
CONF_DIR = "engine/data/confirmations"
_KEY_RE = re.compile(r"_(\d{8}_\d{2})\.jsonl$")

# Tokens/contracts so common that sharing them is NOT evidence of a backrun.
# A real backrun shares the POOL address (not in this set), which survives.
UBIQUITOUS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
    "0x000000000022d473030f116ddee9f6b43ac78ba3",  # Permit2
    # Uniswap V4 PoolManager singleton — EVERY V4 swap touches it, so sharing
    # it is NOT evidence of the same pool (V4 pools are keyed by PoolId in the
    # event data, not by emitter address). Excluding it makes the verified
    # rate a conservative LOWER bound (V4-only backruns become undetectable by
    # this address-overlap method; PoolId parsing is the future refinement).
    "0x000000000004444c5dc75cb358380d2e3de08a90",  # UniV4 PoolManager
} | set(DEX_ROUTERS)


def extract_touched_contracts(receipt: Optional[dict]) -> set[str]:
    """Lowercased set of contract addresses that emitted a log in this tx —
    the pools + tokens it actually interacted with on-chain."""
    if not receipt:
        return set()
    out = set()
    for log in receipt.get("logs") or []:
        a = log.get("address")
        if isinstance(a, str) and a:
            out.add(a.lower())
    return out


def shared_specific(a: set[str], b: set[str]) -> set[str]:
    """Contracts BOTH txs touched, excluding ubiquitous tokens/routers — i.e.
    the shared POOL(s). Non-empty ⇒ they traded the same specific liquidity."""
    return (a & b) - UBIQUITOUS


def _keys(dir_, prefix):
    out = set()
    for fp in glob.glob(f"{dir_}/{prefix}*.jsonl"):
        m = _KEY_RE.search(fp.replace("\\", "/"))
        if m:
            out.add(m.group(1))
    return out


def available_windows() -> list[str]:
    return sorted(_keys(MEMPOOL_DIR, "mempool_") & _keys(CONF_DIR, "confirmations_"))


def _files(window_keys):
    mp = [f"{MEMPOOL_DIR}/mempool_{k}.jsonl" for k in window_keys]
    cf = [f"{CONF_DIR}/confirmations_{k}.jsonl" for k in window_keys]
    return ([p for p in mp if os.path.exists(p)],
            [p for p in cf if os.path.exists(p)])


def _sql_list(paths):
    return "[" + ",".join("'" + p.replace("\\", "/") + "'" for p in paths) + "]"


def _build_labeled(con, window_keys) -> bool:
    mp, cf = _files(window_keys)
    if not mp or not cf:
        return False
    routers = "(" + ",".join("'" + a + "'" for a in DEX_ROUTERS) + ")"
    con.execute(f"""CREATE OR REPLACE VIEW seen AS
        SELECT lower(hash) AS h, any_value(lower("to")) AS to_addr
        FROM read_json_auto({_sql_list(mp)}, format='newline_delimited',
                            union_by_name=true, ignore_errors=true)
        WHERE hash IS NOT NULL GROUP BY lower(hash);""")
    con.execute(f"""CREATE OR REPLACE VIEW conf_d AS
        SELECT block_number, tx_index, any_value(lower(hash)) AS h
        FROM read_json_auto({_sql_list(cf)}, format='newline_delimited',
                            union_by_name=true, ignore_errors=true)
        WHERE hash IS NOT NULL AND block_number IS NOT NULL AND tx_index IS NOT NULL
        GROUP BY block_number, tx_index;""")
    con.execute(f"""CREATE OR REPLACE VIEW labeled AS
        SELECT c.block_number, c.tx_index, c.h, (s.h IS NOT NULL) AS seen,
               (s.h IS NOT NULL AND s.to_addr IN {routers}) AS is_public_swap
        FROM conf_d c LEFT JOIN seen s ON c.h = s.h;""")
    return True


def sample_backrun_pairs(window_keys, n: int = 150, seed: int = 7):
    """Reservoir sample of (swap_hash, next_hash, next_seen) over public swaps
    that have a following slot."""
    con = duckdb.connect()
    try:
        if not _build_labeled(con, window_keys):
            return []
        rows = con.execute(f"""
            SELECT swap_hash, next_hash, next_seen FROM (
              SELECT h AS swap_hash, is_public_swap,
                     LEAD(h) OVER (PARTITION BY block_number ORDER BY tx_index) AS next_hash,
                     LEAD(seen) OVER (PARTITION BY block_number ORDER BY tx_index) AS next_seen
              FROM labeled)
            WHERE is_public_swap AND next_hash IS NOT NULL
            ORDER BY hash(swap_hash || '{int(seed)}')
            LIMIT {int(n)}
        """).fetchall()
        return [(a, b, bool(c)) for a, b, c in rows]
    finally:
        con.close()


def sample_control_pairs(window_keys, n: int = 150, seed: int = 99):
    """Null: random swap hash paired with a random UNRELATED confirmed tx
    (different block). Shared-pool rate here should be ~0."""
    con = duckdb.connect()
    try:
        if not _build_labeled(con, window_keys):
            return []
        swaps = [r[0] for r in con.execute(
            f"SELECT h FROM labeled WHERE is_public_swap "
            f"ORDER BY hash(h || '{int(seed)}') LIMIT {int(n)}").fetchall()]
        others = [r[0] for r in con.execute(
            f"SELECT h FROM labeled ORDER BY hash(h || '{int(seed)+1}') "
            f"LIMIT {int(n)}").fetchall()]
        return list(zip(swaps, others))
    finally:
        con.close()


def summarize(pairs_overlap: list[dict]) -> dict:
    """pairs_overlap: list of {next_seen, shared (bool)}. Returns verified
    backrun rates overall and split by seen/unseen backrunner."""
    def rate(items):
        n = len(items)
        return (sum(1 for x in items if x["shared"]) / n) if n else None, n
    overall, n = rate(pairs_overlap)
    seen = [x for x in pairs_overlap if x.get("next_seen")]
    unseen = [x for x in pairs_overlap if x.get("next_seen") is False]
    r_seen, n_seen = rate(seen)
    r_unseen, n_unseen = rate(unseen)
    return {
        "n_pairs": n, "verified_backrun_rate": overall,
        "n_public_backrunner": n_seen, "verified_rate_public": r_seen,
        "n_private_backrunner": n_unseen, "verified_rate_private": r_unseen,
    }


__all__ = ["UBIQUITOUS", "extract_touched_contracts", "shared_specific",
           "available_windows", "sample_backrun_pairs", "sample_control_pairs",
           "summarize"]
