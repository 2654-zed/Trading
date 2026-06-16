"""Capture-wide summary: dataset inventory + order-flow composition over the
archived mempool (and confirmation) data. DuckDB reads JSONL IN PLACE, so it
stays low-memory even on a multi-hundred-MB archive. Re-runnable; $0.

Reports:
  * INVENTORY  — files, rows, distinct hashes, time span, schema tiers
                 (pre/post Tier-0 hardening), clock-discipline distribution.
  * ORDER FLOW — top method selectors (labeled), top destinations (labeled),
                 priority-fee percentiles, sender concentration, and the
                 deductive third-party transferFrom control-fact count.

Usage:
    python -m engine.scripts.analyze_capture_summary
    python -m engine.scripts.analyze_capture_summary --mempool-glob "...*.jsonl"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import duckdb

# A handful of well-known mainnet selectors / addresses for readability. Not
# exhaustive — unlabeled entries are shown as the raw hex (honest, not guessed).
SELECTORS = {
    "0x": "plain ETH transfer / empty",
    "0xa9059cbb": "ERC20 transfer",
    "0x23b872dd": "ERC20 transferFrom",
    "0x095ea7b3": "ERC20 approve",
    "0xd0e30db0": "WETH deposit",
    "0x2e1a7d4d": "WETH withdraw",
    "0x3593564c": "UniversalRouter execute",
    "0x5ae401dc": "UniV3 multicall(deadline)",
    "0x04e45aaf": "UniV3 exactInputSingle",
    "0x38ed1739": "UniV2 swapExactTokensForTokens",
    "0x7ff36ab5": "UniV2 swapExactETHForTokens",
    "0xac9650d8": "multicall",
    "0x6a761202": "Safe execTransaction",
    "0xa0712d68": "mint(uint256)",
}
ADDRESSES = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": "UniV2 Router",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "UniV3 SwapRouter02",
    "0xe592427a0aece92de3edee1f18e0157c05861564": "UniV3 SwapRouter",
    "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad": "UniversalRouter",
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af": "UniversalRouter v1.2",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch v5",
    "0x111111125421ca6dc452d289314280a0f8842a65": "1inch v6",
    "0x40a50cf069e992aa4536211b23f286ef88752187": "PancakeSwap",
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5": "KyberSwap Meta",
}


def _ts(epoch):
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%m-%d %H:%M:%SZ")


def _hex2int(h):
    if not h or not isinstance(h, str):
        return None
    try:
        return int(h, 16)
    except ValueError:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mempool-glob", default="engine/data/mempool/mempool_*.jsonl")
    p.add_argument("--confs-glob",
                   default="engine/data/confirmations/confirmations_*.jsonl")
    p.add_argument("--top", type=int, default=14)
    args = p.parse_args()

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC';")
    con.create_function("hex2int", _hex2int, ["VARCHAR"], "BIGINT")
    mg = args.mempool_glob.replace("\\", "/").replace("'", "''")
    cg = args.confs_glob.replace("\\", "/").replace("'", "''")
    con.execute(f"""CREATE VIEW mempool AS SELECT * FROM read_json_auto(
        '{mg}', format='newline_delimited', union_by_name=true, ignore_errors=true);""")
    try:
        con.execute(f"""CREATE VIEW confs AS SELECT * FROM read_json_auto(
            '{cg}', format='newline_delimited', union_by_name=true, ignore_errors=true);""")
        has_confs = con.execute("SELECT count(*) FROM confs").fetchone()[0] > 0
    except duckdb.Error:
        has_confs = False

    print("=" * 70)
    print("CAPTURE SUMMARY — inventory + order-flow composition")
    print("=" * 70)

    # ---- INVENTORY -----------------------------------------------------
    inv = con.execute("""
        SELECT count(*), count(DISTINCT lower(hash)),
               min(recv_ts), max(recv_ts),
               count(*) FILTER (WHERE seq IS NULL),
               count(*) FILTER (WHERE seq IS NOT NULL)
        FROM mempool WHERE hash IS NOT NULL
    """).fetchone()
    total, uniq, t0, t1, pre, post = inv
    print(f"\nMEMPOOL  ({total:,} rows, {uniq:,} distinct hashes)")
    print(f"  span        : {_ts(t0)} -> {_ts(t1)}  "
          f"({(t1 - t0) / 3600:.1f} h wall, gaps included)")
    print(f"  schema tiers: {pre:,} pre-hardening (no seq/clock)  |  "
          f"{post:,} hardened (seq+source+clock)")
    clk = con.execute("""
        SELECT clock_synced, count(*) FROM mempool WHERE hash IS NOT NULL
        GROUP BY clock_synced ORDER BY count(*) DESC
    """).fetchall()
    print("  clock_synced: " + ", ".join(
        f"{('true' if k is True else 'false' if k is False else 'null')}={n:,}"
        for k, n in clk))
    if has_confs:
        cinv = con.execute("""
            SELECT count(*), count(DISTINCT lower(hash)),
                   count(DISTINCT block_number),
                   min(block_received_local_ts), max(block_received_local_ts)
            FROM confs WHERE hash IS NOT NULL
        """).fetchone()
        print(f"\nCONFIRMATIONS  ({cinv[0]:,} rows, {cinv[1]:,} mined hashes, "
              f"{cinv[2]:,} blocks)")
        print(f"  span        : {_ts(cinv[3])} -> {_ts(cinv[4])}")

    # ---- ORDER FLOW (mempool) ------------------------------------------
    print("\n" + "-" * 70)
    print("ORDER FLOW (mempool)")
    print("-" * 70)
    sels = con.execute(f"""
        SELECT CASE WHEN input IS NULL OR input='' OR input='0x' THEN '0x'
                    ELSE lower(substr(input, 1, 10)) END AS sel, count(*) AS n
        FROM mempool WHERE hash IS NOT NULL
        GROUP BY sel ORDER BY n DESC LIMIT {int(args.top)}
    """).fetchall()
    print(f"\n  top method selectors:")
    for sel, n in sels:
        lab = SELECTORS.get(sel, "")
        print(f"    {sel:<12} {n:>9,}  {100*n/total:4.1f}%  {lab}")

    dests = con.execute(f"""
        SELECT lower("to") AS dst, count(*) AS n FROM mempool
        WHERE hash IS NOT NULL AND "to" IS NOT NULL AND "to" <> ''
        GROUP BY dst ORDER BY n DESC LIMIT {int(args.top)}
    """).fetchall()
    print(f"\n  top destination contracts:")
    for dst, n in dests:
        lab = ADDRESSES.get(dst, "")
        print(f"    {dst}  {n:>8,}  {100*n/total:4.1f}%  {lab}")

    fee = con.execute("""
        SELECT count(*) FILTER (WHERE pf IS NOT NULL),
               quantile_cont(pf, 0.10), quantile_cont(pf, 0.50),
               quantile_cont(pf, 0.90), quantile_cont(pf, 0.99),
               max(pf)
        FROM (SELECT hex2int(max_priority_fee)/1e9 AS pf FROM mempool
              WHERE hash IS NOT NULL)
    """).fetchone()
    print(f"\n  priority fee (gwei), n={fee[0]:,} EIP-1559 txs:")
    print(f"    p10={fee[1]:.3f}  p50={fee[2]:.3f}  p90={fee[3]:.2f}  "
          f"p99={fee[4]:.1f}  max={fee[5]:.0f}")

    senders = con.execute("""
        SELECT count(DISTINCT "from"),
               sum(n) FILTER (WHERE rnk <= 10) * 1.0 / sum(n)
        FROM (SELECT "from", count(*) AS n,
                     row_number() OVER (ORDER BY count(*) DESC) AS rnk
              FROM mempool WHERE hash IS NOT NULL AND "from" IS NOT NULL
              GROUP BY "from")
    """).fetchone()
    print(f"\n  senders: {senders[0]:,} distinct; "
          f"top-10 account for {100*senders[1]:.1f}% of all txs")

    # deductive third-party transferFrom (initiator != debited owner)
    tpf = con.execute("""
        SELECT count(*) FILTER (WHERE is_tpf),
               count(DISTINCT "from") FILTER (WHERE is_tpf)
        FROM (
          SELECT "from",
                 (lower(input) LIKE '0x23b872dd%' AND length(input) >= 74
                  AND lower('0x' || substr(lower(input), 35, 40)) <> lower("from")
                 ) AS is_tpf
          FROM mempool WHERE hash IS NOT NULL
        )
    """).fetchone()
    print(f"\n  third-party transferFrom (deductive control-fact): "
          f"{tpf[0]:,} txs, {tpf[1]:,} distinct initiators")
    print("    (initiator != debited owner; includes legit routers/Permit2 —")
    print("     the right PRIMITIVE, not a conviction)")

    # EIP-2718 type census + explicit EIP-7702 count. Guarded: the field is
    # absent from pre-7702-aware capture rows, so an archive without ANY such
    # rows won't have the column at all.
    TYPE_LABELS = {0: "legacy", 1: "access-list (2930)", 2: "EIP-1559",
                   3: "blob (4844)", 4: "EIP-7702 set-code"}
    print("\n  transaction types (EIP-2718):")
    try:
        types = con.execute("""
            SELECT tx_type, count(*) AS n FROM mempool WHERE hash IS NOT NULL
            GROUP BY tx_type ORDER BY n DESC
        """).fetchall()
        for t, n in types:
            lab = ("not captured (pre-7702-aware rows)" if t is None
                   else TYPE_LABELS.get(int(t), f"type {t}"))
            tlbl = "null" if t is None else str(int(t))
            print(f"    {tlbl:<6} {n:>9,}  {100*n/total:4.1f}%  {lab}")
        e7 = con.execute("""
            SELECT count(*), count(*) FILTER (WHERE auth_count > 0)
            FROM mempool WHERE tx_type = 4
        """).fetchone()
        print(f"    --> EIP-7702 set-code txs: {e7[0]:,} "
              f"({e7[1]:,} carrying >=1 authorization)")
    except duckdb.Error:
        print("    (no tx_type column — this archive predates 7702-aware capture)")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
