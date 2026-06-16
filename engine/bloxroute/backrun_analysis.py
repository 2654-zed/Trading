"""Backrun-slot CONTESTABILITY measurement (answers: "did you try a backrun
simulator?").

We cannot simulate backrun PnL from this data — that needs historical pool
reserves + decoded swap amounts + a price feed, none of which we hold. What
we CAN measure, from exactly the two tables we have (public mempool sightings
+ per-block tx_index), is the question that decides whether a backrun
simulator is even worth writing:

  When a swap we saw in the PUBLIC mempool lands at index i in a block, who
  occupies the BACKRUN SLOT (index i+1)? A tx we also saw (a PUBLIC actor we
  could out-bid) — or a tx we NEVER saw (a PRIVATE bundle that already won the
  auction before our public-feed script could submit)?

If the backrun slot after public swaps is disproportionately UNSEEN-in-our-feed
versus a matched baseline, that quantifies how much of the flow around swaps a
public-feed actor is blind to.

HONESTY CORRECTION (adversarial review re-ran this on real data): the measured
effect is REAL and robust (+24.9pp, survives block-position stratification, OOS
sign holds) but means LESS than "backruns are captured privately":
  * NON-DIRECTIONAL — the slot BEFORE a swap is as unseen as the slot after
    (i-1≈44%, i+1≈42%, flat through i+3). A genuine "private bundle won the
    backrun slot" effect would be directional + localized to i+1. This is
    symmetric ⇒ "swaps sit in unseen-dense block zones," NOT "the backrun was
    won privately."
  * THE 2.4x IS THE BASELINE BEING LOW, not the backrun slot being high: the
    slot's 42% ≈ the 44% overall background unseen rate; post-non-swap-public
    slots are anomalously public (17%), inflating the ratio.
  * "UNSEEN" ≠ "PRIVATE BUNDLE": a single vantage misses 41–52% of all txs;
    this conflates true private routing with propagation/sampling misses.
So this is a COVERAGE / contestability caution (a public-feed sim would
overstate fills), NOT proof the backrun is won privately or PnL of any kind.
"""

from __future__ import annotations

import glob
import math
import os
import re
from typing import Optional

import duckdb

MEMPOOL_DIR = "engine/data/mempool"
CONF_DIR = "engine/data/confirmations"
_KEY_RE = re.compile(r"_(\d{8}_\d{2})\.jsonl$")

# Known mainnet DEX routers / aggregators (lowercase). Identifying targets by
# ROUTER ADDRESS is higher-precision than by selector. This set is a LOWER
# BOUND on swaps — anything routed through an un-listed contract is missed, so
# the measured target count understates true swap volume (decoder coverage,
# not on-chain reality, sets it).
DEX_ROUTERS = {
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": "UniversalRouter",
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af": "UniversalRouter v1.2",
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": "UniV2 Router",
    "0xe592427a0aece92de3edee1f18e0157c05861564": "UniV3 SwapRouter",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "UniV3 SwapRouter02",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch v5",
    "0x111111125421ca6dc452d289314280a0f8842a65": "1inch v6",
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5": "KyberSwap Meta",
    "0x40a50cf069e992aa4536211b23f286ef88752187": "PancakeSwap",
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": "0x Exchange Proxy",
}


def available_windows() -> list[str]:
    def keys(dir_, prefix):
        out = set()
        for fp in glob.glob(f"{dir_}/{prefix}*.jsonl"):
            m = _KEY_RE.search(fp.replace("\\", "/"))
            if m:
                out.add(m.group(1))
        return out
    return sorted(keys(MEMPOOL_DIR, "mempool_") & keys(CONF_DIR, "confirmations_"))


def _files(window_keys):
    mp = [f"{MEMPOOL_DIR}/mempool_{k}.jsonl" for k in window_keys]
    cf = [f"{CONF_DIR}/confirmations_{k}.jsonl" for k in window_keys]
    return ([p for p in mp if os.path.exists(p)],
            [p for p in cf if os.path.exists(p)])


def _sql_list(paths):
    return "[" + ",".join("'" + p.replace("\\", "/") + "'" for p in paths) + "]"


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def two_proportion_z(x1, n1, x2, n2):
    """Two-sided z-test that proportion1 != proportion2. Returns (z, p)."""
    if n1 == 0 or n2 == 0:
        return None, None
    p1, p2 = x1 / n1, x2 / n2
    pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return None, None
    z = (p1 - p2) / se
    return z, 2 * (1 - _phi(abs(z)))


def _slot_table(con, window_keys) -> bool:
    """Build the `slots` view: one row per confirmed tx with its private flag,
    whether it's a public router-swap, and the private flag of the NEXT slot
    in the same block. Returns False if no data."""
    mp, cf = _files(window_keys)
    if not mp or not cf:
        return False
    router_sql = "(" + ",".join("'" + a + "'" for a in DEX_ROUTERS) + ")"
    con.execute("SET TimeZone='UTC';")
    con.execute(f"""
        CREATE OR REPLACE VIEW seen AS
        SELECT lower(hash) AS h, any_value(lower("to")) AS to_addr
        FROM read_json_auto({_sql_list(mp)}, format='newline_delimited',
                            union_by_name=true, ignore_errors=true)
        WHERE hash IS NOT NULL GROUP BY lower(hash);
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW conf_d AS
        SELECT block_number, tx_index, any_value(lower(hash)) AS h
        FROM read_json_auto({_sql_list(cf)}, format='newline_delimited',
                            union_by_name=true, ignore_errors=true)
        WHERE hash IS NOT NULL AND block_number IS NOT NULL AND tx_index IS NOT NULL
        GROUP BY block_number, tx_index;
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW labeled AS
        SELECT c.block_number, c.tx_index, c.h,
               (s.h IS NOT NULL) AS seen,
               (s.h IS NOT NULL AND s.to_addr IN {router_sql}) AS is_public_swap
        FROM conf_d c LEFT JOIN seen s ON c.h = s.h;
    """)
    con.execute("""
        CREATE OR REPLACE VIEW slots AS
        SELECT *,
               LEAD(seen) OVER (PARTITION BY block_number ORDER BY tx_index) AS next_seen,
               LEAD(tx_index) OVER (PARTITION BY block_number ORDER BY tx_index) AS next_idx
        FROM labeled;
    """)
    return True


def _measure(con) -> dict:
    """Core stat over whatever windows are loaded in `slots`."""
    overall = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE NOT seen) FROM labeled").fetchone()
    n_all, n_priv_all = overall[0] or 0, overall[1] or 0
    # backrun slot: tx right after a PUBLIC SWAP that has a next slot
    tgt = con.execute("""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE NOT next_seen) AS n_private_next
        FROM slots WHERE is_public_swap AND next_idx IS NOT NULL
    """).fetchone()
    n_targets, n_priv_backrun = tgt[0] or 0, tgt[1] or 0
    # matched baseline: tx right after a NON-swap PUBLIC tx
    base = con.execute("""
        SELECT count(*) AS n,
               count(*) FILTER (WHERE NOT next_seen) AS n_private_next
        FROM slots WHERE seen AND NOT is_public_swap AND next_idx IS NOT NULL
    """).fetchone()
    n_base, n_priv_base = base[0] or 0, base[1] or 0

    backrun_priv_rate = n_priv_backrun / n_targets if n_targets else None
    base_priv_rate = n_priv_base / n_base if n_base else None
    overall_priv_rate = n_priv_all / n_all if n_all else None
    z, p = two_proportion_z(n_priv_backrun, n_targets, n_priv_base, n_base)
    # effect = excess private-capture of the backrun slot vs matched baseline
    effect = (backrun_priv_rate - base_priv_rate
              if backrun_priv_rate is not None and base_priv_rate is not None else None)
    sign = ("0" if effect is None or abs(effect) < 1e-6
            else "+" if effect > 0 else "-")
    return {
        "n_public_swaps_with_backrun_slot": n_targets,
        "backrun_slot_private_rate": backrun_priv_rate,
        "baseline_slot_private_rate": base_priv_rate,
        "overall_private_rate": overall_priv_rate,
        "excess_private_capture": effect,   # >0 ⇒ backrun slot MORE private than baseline
        "edge_sign": sign,
        "z": z, "p_value": p,
        "n_total_confirmed": n_all,
    }


def analyze_backrun_contestability(window_keys: list[str]) -> dict:
    """Full measurement over the given windows, with a chronological
    early/late regime split for robustness."""
    con = duckdb.connect()
    try:
        if not _slot_table(con, window_keys):
            return {"error": "no data for windows", "n_public_swaps_with_backrun_slot": 0}
        main = _measure(con)
        # regime split: earlier vs later half of block numbers
        rng = con.execute(
            "SELECT min(block_number), max(block_number) FROM labeled").fetchone()
        per_regime = []
        if rng[0] is not None and rng[1] is not None and rng[1] > rng[0]:
            mid = (rng[0] + rng[1]) // 2
            for label, lo, hi in (("early", rng[0], mid), ("late", mid + 1, rng[1])):
                con.execute(f"""
                    CREATE OR REPLACE VIEW slots_r AS
                    SELECT * FROM slots WHERE block_number BETWEEN {lo} AND {hi};
                """)
                t = con.execute("""
                    SELECT count(*) FILTER (WHERE is_public_swap AND next_idx IS NOT NULL),
                           count(*) FILTER (WHERE is_public_swap AND next_idx IS NOT NULL AND NOT next_seen),
                           count(*) FILTER (WHERE seen AND NOT is_public_swap AND next_idx IS NOT NULL),
                           count(*) FILTER (WHERE seen AND NOT is_public_swap AND next_idx IS NOT NULL AND NOT next_seen)
                    FROM slots_r
                """).fetchone()
                nt, npt, nb, npb = (t[0] or 0, t[1] or 0, t[2] or 0, t[3] or 0)
                br = npt / nt if nt else None
                ba = npb / nb if nb else None
                eff = (br - ba) if (br is not None and ba is not None) else None
                per_regime.append({
                    "regime": label, "n_targets": nt,
                    "backrun_slot_private_rate": br, "baseline_slot_private_rate": ba,
                    "effect": eff,
                    "edge_sign": ("0" if eff is None or abs(eff) < 1e-6
                                  else "+" if eff > 0 else "-")})
        main["per_regime"] = per_regime
        main["notes"] = ("no_lookahead; contestability not PnL; swap set is a "
                         "router-address lower bound; single public vantage")
        return main
    finally:
        con.close()


__all__ = ["DEX_ROUTERS", "available_windows", "two_proportion_z",
           "analyze_backrun_contestability"]
