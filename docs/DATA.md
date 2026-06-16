# DATA — what exists, where it lives, how it's described

**Rule: data BYTES do not go in git** (too large, and the mempool config holds the
bloXroute auth header). The repo holds **descriptions + schemas + a link** to where
the bytes actually live (shared drive / cloud bucket). Update the "Location" links
when you place the files.

## Datasets
| Dataset | Venue | What it is | Location | Schema doc |
|---|---|---|---|---|
| **CEX L2 (Tardis Pro)** | centralized exchanges | historical L2 order books + trades + funding | Tardis account / your bucket | (Tardis docs) |
| **CEX L2 (live)** | centralized exchanges | live L2 recordings from `l2_collector` | engineer's machine → `<link>` | [handoff/L2_DATA_SCHEMA.md](../handoff/L2_DATA_SCHEMA.md) |
| **Mempool forensic** | Ethereum (on-chain) | pending-tx "before" + block "after" | local `engine/data/` (not in git) → `<link>` | [handoff/STATUS_REPORT.md](../handoff/STATUS_REPORT.md) |
| **Handoff tables** | derived (on-chain) | `block_features`, `tx_outcomes` (parquet/csv) | `<link>` (≈100 MB+) | [handoff/DATA_DICTIONARY.md](../handoff/DATA_DICTIONARY.md) |

> The mempool data belongs to Game 2 (on-chain, deprioritized). The active effort
> (Game 3) runs on the **CEX L2** rows. Both are described above so nothing is lost.

## "Latest data" folder
Point this at the canonical, current copy (a shared drive/bucket folder), e.g.:
`<paste the shared-folder link here>` — keep the newest export there and note the
date. The collector + exports regenerate it; this doc just says where to find it.

## Relating venues (answer to Q2)
Tardis (CEX) and bloXroute (on-chain DEX) **share no common key** — no tx-level join.
They relate only at the **(same asset, wall-clock time)** level:
- **Primary pairing:** Tardis = *history*, `l2_collector` = *live* — same data type (CEX
  order books). The reconstruction + imbalance analysis runs on both identically.
- **Optional bridge (Phase 2):** CEX↔DEX price discovery / lead-lag, via a common
  time-bucketed `(asset, time)` panel. Limited by the ~12s on-chain block resolution
  vs. CEX milliseconds; statistical, not record-level. Only pursue if Game 3 shows promise.

## How to use the L2 recordings
`l2_collector` records the **raw** stream losslessly; reconstruct the book + compute
the imbalance ("price-pressure") feature **offline** — see [handoff/L2_DATA_SCHEMA.md](../handoff/L2_DATA_SCHEMA.md).
Binance.US `depth20` rows are usable as-is; Coinbase/Kraken are snapshot+diff (reconstruct first).
