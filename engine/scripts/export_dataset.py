"""Export the captured mempool data into ANALYSIS-READY tables a third party's
model/'math' can point at directly — without handing them 600 MB of raw JSONL.

Produces, in --out-dir (default handoff/):
  block_features.csv   one row per block: private-inclusion share + the
                       microstructure features of the txs we DID see.
  tx_outcomes.csv      one row per observable mempool tx: features + the
                       gap-aware outcome label (landed / replaced_drop /
                       true_ghost) + lead time.
  DATA_DICTIONARY.md   every column defined, with the honesty caveats.
  coverage_manifest.json  windows, gaps, coverage fraction, spans, clock state.

The labels reuse the SAME observability discipline as the ghost-tx detector
(only txs with continuous confirmation coverage are labeled; gap-overlapping
txs are excluded), so the export can't smuggle in artifacts the analysis
already rejects.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import duckdb

from ..bloxroute.ghost_analysis import (
    build_views, coverage, detect_gaps, build_classified,
)


def _hex2int(h):
    if not h or not isinstance(h, str):
        return None
    try:
        return int(h, 16)
    except ValueError:
        return None


def _q(p):
    return p.replace("\\", "/").replace("'", "''")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mempool-glob", default="engine/data/mempool/mempool_*.jsonl")
    ap.add_argument("--confs-glob", default="engine/data/confirmations/confirmations_*.jsonl")
    ap.add_argument("--out-dir", default="handoff")
    ap.add_argument("--tail-buffer", type=float, default=180.0)
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.create_function("hex2int", _hex2int, ["VARCHAR"], "BIGINT")
    build_views(con, args.mempool_glob, args.confs_glob)
    cov = coverage(con)
    if not cov["conf_start"] or not cov["mempool_start"]:
        print("no overlapping data to export yet — run both collectors first.",
              file=sys.stderr)
        return 1
    cov_end_obs = cov["conf_end"] - args.tail_buffer
    build_classified(con, cov["conf_start"], cov_end_obs, args.tail_buffer)
    gaps = detect_gaps(con)

    # feature-enriched, first-seen-per-hash mempool view (selector, fee, type, …)
    con.execute("""
        CREATE OR REPLACE VIEW mp_feat AS
        SELECT lower(hash) AS hash,
               any_value(lower("from")) AS sender,
               any_value(lower("to")) AS to_addr,
               any_value(nonce) AS nonce,
               min(recv_ts) AS first_seen_ts,
               any_value(lower(substr(input, 1, 10))) AS selector,
               any_value(hex2int(max_priority_fee) / 1e9) AS priority_fee_gwei,
               any_value(tx_type) AS tx_type,
               any_value(lower(input) LIKE '0x23b872dd%' AND length(input) >= 74
                   AND lower('0x' || substr(lower(input), 35, 40)) <> lower("from")
               ) AS is_third_party_transferfrom
        FROM mempool WHERE hash IS NOT NULL AND hash <> ''
        GROUP BY lower(hash);
    """)

    # ── block_features.csv ──────────────────────────────────────────────────
    block_sql = """
        WITH conf AS (
          SELECT lower(hash) AS h, block_number,
                 min(block_timestamp) AS block_timestamp,
                 min(block_received_local_ts) AS block_recv_ts,
                 any_value(builder_or_fee_recipient) AS fee_recipient
          FROM confs WHERE block_number IS NOT NULL AND hash IS NOT NULL
          GROUP BY lower(hash), block_number),
        j AS (
          SELECT c.block_number, c.block_timestamp, c.block_recv_ts, c.fee_recipient,
                 (m.hash IS NOT NULL) AS seen,
                 m.priority_fee_gwei, m.tx_type, m.sender, m.first_seen_ts,
                 m.is_third_party_transferfrom AS tpf
          FROM conf c LEFT JOIN mp_feat m ON c.h = m.hash)
        SELECT block_number,
               any_value(block_timestamp) AS block_timestamp,
               strftime(to_timestamp(any_value(block_timestamp)), '%Y-%m-%dT%H:%M:%SZ') AS block_time_iso,
               any_value(fee_recipient) AS fee_recipient,
               count(*) AS tx_count,
               count(*) FILTER (WHERE seen) AS seen_count,
               round(1 - count(*) FILTER (WHERE seen)::DOUBLE / count(*), 5) AS private_inclusion_share,
               round(median(priority_fee_gwei) FILTER (WHERE seen), 6) AS median_priofee_gwei_seen,
               round(quantile_cont(priority_fee_gwei, 0.90) FILTER (WHERE seen), 6) AS p90_priofee_gwei_seen,
               round(any_value(block_timestamp) - avg(first_seen_ts) FILTER (WHERE seen), 3) AS mean_lead_time_s_seen,
               count(*) FILTER (WHERE seen AND tx_type = 2) AS n_eip1559_seen,
               count(*) FILTER (WHERE seen AND tx_type = 0) AS n_legacy_seen,
               count(*) FILTER (WHERE seen AND tx_type = 4) AS n_eip7702_seen,
               count(*) FILTER (WHERE seen AND tx_type = 3) AS n_blob_seen,
               count(*) FILTER (WHERE seen AND tpf) AS n_third_party_transferfrom_seen,
               count(DISTINCT sender) FILTER (WHERE seen) AS n_distinct_senders_seen
        FROM j GROUP BY block_number ORDER BY block_number
    """
    con.execute(f"COPY ({block_sql}) TO '{_q(str(out / 'block_features.csv'))}' "
                "(HEADER, DELIMITER ',')")
    con.execute(f"COPY (SELECT * FROM read_csv_auto('{_q(str(out / 'block_features.csv'))}')) "
                f"TO '{_q(str(out / 'block_features.parquet'))}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    # ── tx_outcomes.csv ─────────────────────────────────────────────────────
    tx_sql = """
        SELECT c.hash, c.sender, c.nonce, f.to_addr, f.selector,
               round(f.priority_fee_gwei, 6) AS priority_fee_gwei, f.tx_type,
               f.is_third_party_transferfrom,
               round(c.first_seen_ts, 3) AS first_seen_ts,
               c.klass AS outcome,
               c.landed_block, c.landed_block_ts,
               round(c.landed_block_ts - c.first_seen_ts, 2) AS lead_time_s
        FROM classified c LEFT JOIN mp_feat f USING (hash)
        ORDER BY c.first_seen_ts
    """
    con.execute(f"COPY ({tx_sql}) TO '{_q(str(out / 'tx_outcomes.csv'))}' "
                "(HEADER, DELIMITER ',')")
    con.execute(f"COPY (SELECT * FROM read_csv_auto('{_q(str(out / 'tx_outcomes.csv'))}')) "
                f"TO '{_q(str(out / 'tx_outcomes.parquet'))}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    n_blocks_csv = con.execute(f"SELECT count(*) FROM ({block_sql})").fetchone()[0]
    n_tx_csv = con.execute(f"SELECT count(*) FROM ({tx_sql})").fetchone()[0]
    klass = dict(con.execute(
        "SELECT klass, count(*) FROM classified GROUP BY klass").fetchall())

    # ── coverage manifest ───────────────────────────────────────────────────
    clock = dict(con.execute(
        "SELECT clock_synced, count(*) FROM mempool GROUP BY clock_synced").fetchall())
    manifest = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mempool_rows": cov["mempool_hashes"], "confirmation_rows": cov["conf_rows"],
        "confirmation_unique_hashes": cov["conf_unique_hashes"],
        "mempool_span_utc": [_iso(cov["mempool_start"]), _iso(cov["mempool_end"])],
        "confirmation_span_utc": [_iso(cov["conf_start"]), _iso(cov["conf_end"])],
        "confirmation_gaps": gaps,
        "block_features_rows": n_blocks_csv, "tx_outcomes_rows": n_tx_csv,
        "tx_outcome_breakdown": klass,
        "clock_synced_distribution": {str(k): v for k, v in clock.items()},
        "honesty_caveats": [
            "SINGLE VANTAGE: one bloXroute public-mempool feed; only ~41-52% of "
            "mined txs were ever seen pending. The rest is private orderflow we "
            "cannot observe — private_inclusion_share is its measurable shadow.",
            "INTERMITTENT CAPTURE: windows are non-contiguous bursts, not a "
            "continuous sample. confirmation_gaps lists the holes; gap-overlapping "
            "txs are EXCLUDED from tx_outcomes, not mislabeled.",
            "true_ghost is an UPPER BOUND (never-landed candidate); it also "
            "absorbs private replacements we never saw. Not proof of censorship.",
            "Treat any effect as an upper bound on the public-mempool slice, NOT "
            "a property of all ETH orderflow.",
        ],
    }
    (out / "coverage_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    _write_dictionary(out)

    print(f"exported to {out.resolve()}/")
    print(f"  block_features.[csv|parquet] : {n_blocks_csv:,} blocks")
    print(f"  tx_outcomes.[csv|parquet]    : {n_tx_csv:,} txs  ({klass})")
    print(f"  + DATA_DICTIONARY.md, coverage_manifest.json")
    con.close()
    return 0


def _iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_dictionary(out: Path) -> None:
    (out / "DATA_DICTIONARY.md").write_text(_DICT, encoding="utf-8")


_DICT = """# Data dictionary — mempool forensic export

Two tables. Both are derived from a single public bloXroute mempool feed (the
"before" / intent) joined to ETH block data (the "after" / outcome). Read the
caveats in `coverage_manifest.json` before modeling — every effect is at best a
property of the **public-mempool slice (~41–52% of mined flow)**, not all of ETH.

## block_features.csv — one row per block
| column | meaning |
|---|---|
| `block_number` | ETH block height |
| `block_timestamp` / `block_time_iso` | block time (unix / ISO-UTC) |
| `fee_recipient` | block `miner` field (fee recipient; NOT the true builder/relay) |
| `tx_count` | total txs in the block |
| `seen_count` | how many we saw in the public mempool before they landed |
| **`private_inclusion_share`** | `1 − seen_count/tx_count` — the observable **shadow of private orderflow** (headline feature) |
| `median_priofee_gwei_seen` / `p90_priofee_gwei_seen` | priority-fee distribution of the txs we saw |
| `mean_lead_time_s_seen` | avg seconds between first-seen-pending and block time (how early we saw them) |
| `n_eip1559_seen` / `n_legacy_seen` / `n_eip7702_seen` / `n_blob_seen` | tx-type composition of seen txs |
| `n_third_party_transferfrom_seen` | seen txs where initiator ≠ token owner (routers/Permit2/sweepers/drains — a primitive, not a conviction) |
| `n_distinct_senders_seen` | distinct senders among seen txs |

## tx_outcomes.csv — one row per OBSERVABLE mempool tx
Only txs with continuous confirmation coverage after first-seen are included
(gap-overlapping txs excluded — no artifacts).
| column | meaning |
|---|---|
| `hash` | tx hash |
| `sender` / `nonce` / `to_addr` | tx identity |
| `selector` | first 4 bytes of calldata (method id; `0x` = plain transfer) |
| `priority_fee_gwei` | EIP-1559 priority fee (gwei); blank for legacy |
| `tx_type` | EIP-2718 type: 0 legacy, 1 access-list, 2 EIP-1559, 3 blob, 4 EIP-7702 |
| `is_third_party_transferfrom` | initiator ≠ debited owner |
| `first_seen_ts` | unix time we first saw it pending |
| **`outcome`** | `landed` / `replaced_drop` (same sender+nonce filled by another hash) / `true_ghost` (never landed, slot never resolved — UPPER bound) |
| `landed_block` / `landed_block_ts` | where/when it landed (blank if not landed) |
| `lead_time_s` | seconds from first-seen-pending to landing (blank if not landed) |

## How to point a model at this
- Predict `private_inclusion_share` (or its next-block change) from block features → a model of *when private flow concentrates*.
- Predict `outcome` / `lead_time_s` per tx from its features → a model of *what happens to a pending tx*.
- Any predictive claim must hold OUT-OF-SAMPLE on blocks/txs the model never saw (the research loop enforces exactly this).
"""


if __name__ == "__main__":
    sys.exit(main())
