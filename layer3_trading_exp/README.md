# Layer 3 Trading Research Experiment — Phase 1

Detection-and-logging arbitrage research on Base, consuming Layer 3's behavioral
intelligence corpus. Specification: [`../docs/archive/LAYER3_TRADING_EXPERIMENT.md`](../docs/archive/LAYER3_TRADING_EXPERIMENT.md).

**Phase 1 scope:** no on-chain execution, no capital, no private keys, no LLM in
runtime pipeline, no adaptive rules. Detection and logging only. See spec §Invariants.

## Phase status

- **1.0 — Infrastructure and intelligence consumption:** in progress (awaiting sub-phase acceptance review)
- **1.1 — Pool monitoring on Base:** **complete** — 78 pools enumerated, frozen at block 45,658,483. See [`../docs/archive/PHASE_1_1_ADDENDUM.md`](../docs/archive/PHASE_1_1_ADDENDUM.md) for the architecture pivot from event-scan + CoinGecko to DefiLlama + `getPool`.
- **1.2 — Opportunity detection:** not started
- **1.3 — Layer 3 filter pipeline:** not started
- **1.4 — Logging and measurement:** not started
- **1.5 — End-of-run analysis framework:** not started

## Modules

### Phase 1.0 (intelligence consumption)

| File | Purpose |
|---|---|
| `config.py` | Frozen run configuration. |
| `layer3_client.py` | Read-only wrapper over the local Layer 3 SQLite copy. Methods mirror query patterns in `layer3_consumable_intelligence.md` §1.1–§1.10. |
| `freshness.py` | 30-minute staleness check against Layer 3's table→timestamp mapping. |
| `sync.py` | Local-DB status helper. Per Addendum A #6 the experiment does not sync from Railway; Jason refreshes the local copy manually. |

### Phase 1.1 (pool monitoring)

| File | Purpose |
|---|---|
| `pool_set.py` | Frozen monitored pool set for Base. `DefiLlamaTvlSource` queries DefiLlama's `/pools` endpoint; `enumerate_uniswap_v3_pools`, `enumerate_aerodrome_pools`, and `enumerate_aerodrome_slipstream_pools` derive on-chain pool addresses via `factory.getPool(...)` and write `monitored_pools.json`. |
| `pool_monitor.py` | Per-block multicall3 monitor for the frozen pool set. State fetched once per block via single `aggregate3` call. |
| `scripts/enumerate_live.py` | Phase 1.1 driver: connects to Base, runs the three enumerate functions, freezes the result. |
| `scripts/monitor_lag_test.py` | Latency tracker: measures p50/max block-update lag for the monitor. |
| `data/run_metadata/monitored_pools.json` | The frozen pool set. 78 pools at acceptance run. |

## Phase 1.1 architecture

The original spec called for a factory event scan + per-pool USD TVL computation
via CoinGecko prices and on-chain `balanceOf`. That approach proved untenable
(1.86M+ events to scan, free-tier CoinGecko rate limits, ~100h projected wall
time). The new path queries DefiLlama for pools above the TVL floor, derives
on-chain addresses via `factory.getPool(...)`, and enriches with on-chain token
metadata. Wall time dropped from ~100h (worst case) to **~42 seconds**.

Full rationale, trade-offs, and contingency plan: [`../docs/archive/PHASE_1_1_ADDENDUM.md`](../docs/archive/PHASE_1_1_ADDENDUM.md).

## Acceptance run result (2026-05-06)

```
head_block: 45,658,483
total pools: 78
  uniswap_v3:           21 pools  ($207,888,936 TVL)
  aerodrome_volatile:   26 pools  ($ 88,464,299 TVL)
  aerodrome_stable:      4 pools  ($ 25,976,862 TVL)
  aerodrome_slipstream: 27 pools  ($146,636,050 TVL)
total monitored TVL: $468,966,147
wall time:           42 seconds
```

## Requirements

- Python 3.13 (matches Layer 3 production).
- Layer 3 local SQLite copy at `C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db`. Jason refreshes it manually.
- Base RPC endpoint via `BASE_RPC_URL` env var (Alchemy or equivalent).
- Network access to `yields.llama.fi` for the one-shot DefiLlama TVL fetch at enumeration time.

## Running tests

```bash
cd C:\Users\jason\Desktop\Trading
python -m pytest layer3_trading_exp/tests/ -v
```

## Re-running Phase 1.1

```bash
# Spec invariant #2: refuses to overwrite a frozen monitored_pools.json.
# To start a new frozen run, delete the existing file first.
rm layer3_trading_exp/data/run_metadata/monitored_pools.json
python -m layer3_trading_exp.scripts.enumerate_live
```
