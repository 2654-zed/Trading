# Data dictionary — mempool forensic export

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
