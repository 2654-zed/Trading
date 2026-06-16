# SYSTEM_STATE.md

**Last verified:** 2026-05-16
**Source:** Local repo + Railway deployment state at termination

## Execution mode

**DETECTION-ONLY.** No on-chain writes, no trades, no flash loans, no signing.
Phase 1 spec explicitly forbids execution. This system OBSERVES and LOGS.
Use of the word "trade" anywhere in this codebase refers to a hypothetical
arbitrage path, never an actual transaction.

## Active deployment

| Field | Value |
|---|---|
| Status | **HALTED 2026-05-24** — `railway down --yes` executed after Alchemy CU consumption spiked +500M (May 22-23) then +400M (May 23-24) on the shared Alchemy account. D-015's reversal trigger has fired. See `failures/FAILURE_LOG.md` 2026-05-24 entry. EXP-002 originally launched 2026-05-17 ~17:20 UTC via `railway up` (per D-014); ran intermittently through multiple WS-stall + reconnect cascades before this CU-driven halt. Root cause of the spike not yet attributed (stellar-embrace post-outage catch-up vs our detector vs stacked containers). |
| Host | Railway, project `blockchain` (ID `d05222fb-abc2-4698-be1a-151289d1b5e1`) |
| Service | `layer3-trading-exp` (ID `befe4436-aa4f-44e4-9203-180f883ccea0`) |
| Sibling service | `stellar-embrace` (Layer 3 production, same project) |
| L3 access path | `http://stellar-embrace.railway.internal:8080/dump` over Railway private network |
| Run duration | **7 days** (`--minutes 10080`); deadline ~2026-05-24 17:20 UTC |
| Persistent volume | `/app/data` (50 GB) — preserves logs, run_metadata, synced L3 DB, Across fee table |
| Entry point | `entrypoint.sh` → enumerate_pools (multi-chain) → verify_across_fees (skipped if table exists) → detect_dry_run |
| Kill switch | `touch /app/data/KILL_SWITCH` halts within 1 block per chain |
| Time-budget watchdog | ENABLED (5s tick, NOT gated on `on_block`) — fixes FAILURE_LOG 2026-05-16 |

## Active monitored set

| Field | Value |
|---|---|
| Chain | Base ONLY (Arb/OP are Phase 2+) |
| Pool count | 128 |
| Pool floors | UniV3 + Slipstream: $500K, Aerodrome v1: $250K |
| Protocols | UniV3 (36) + Aerodrome Slipstream (46) + Aerodrome v1 volatile+stable (46) |
| Frozen at block | Re-enumerated during current deploy; see `data/run_metadata/monitored_pools.json` on volume |
| Pool source | DefiLlama `/pools` endpoint (NOT factory event scan — see PHASE_1_1_ADDENDUM.md) |

## Active strategies

**Single strategy: 2-hop intra-chain arbitrage detection.**

- Input: per-block pool state from monitored set
- Algorithm: for each token-pair with ≥2 pools, simulate `A → pool_X → B → pool_Y → A` round-trip at $10K notional
- Filter: gross margin ≥ 30 bps (0.3%), expected USD gain > estimated execution cost
- Cost model: Aave V3 0.05% flash loan + Base gas (DEFAULT_BASE_FEE_GWEI=0.01)
- Output: `Opportunity` records logged to JSONL

## Data sources

| Source | Purpose | Connection |
|---|---|---|
| Alchemy Base WS | `newHeads` subscription | `BASE_WSS_URL` env |
| Alchemy Base HTTP | Multicall3 `eth_call` per block | `BASE_RPC_URL` env |
| DefiLlama `/pools` | Pool enumeration at run start | Public HTTP (no auth) |
| L3 `/dump?since_ts=&cursor_col=` | Incremental sync of 11 tables | `L3_DUMP_BASE_URL` + `LAYER3_ADMIN_TOKEN` |

**L3 tables synced** (to `/app/data/run_metadata/l3_sync.db`, ~370 MB):
`contracts`, `deployers`, `trap_events`, `bytecode_families`, `trust_amplification`,
`bytecode_family_members`, `approval_watchlist`, `extraction_events`,
`org_wallets`, `org_candidates`, `infrastructure_registry`.

Sync cadence: every 300 seconds. Indexed on Layer3Client WHERE-clause
columns (plain + LOWER() functional indexes — see decisions/D-005).

## Pipeline components

| Component | File | Role |
|---|---|---|
| Pool state monitor | `pool_monitor.py` | WS subscription + multicall3 batched fetch |
| Opportunity detector | `opportunity_detector.py` | 2-hop arb scanning across all pool pairs |
| Quote modules | `quote/cl_quote.py`, `cpamm_quote.py`, `stableswap_quote.py` | AMM swap math per protocol |
| Filter pipeline | `filter_pipeline.py` + `filter_rules.py` | 13 L3 rules, no short-circuiting |
| Layer3 client | `layer3_client.py` | Read-only SQLite wrapper, `check_same_thread=False` |
| JSONL logger | `logger.py` | Async writer, daily rotation at 00:00 UTC, bounded queue |
| Daily rollup | `daily_rollup.py` | CSV summary per UTC day, idempotent |
| L3 sync | `scripts/sync_l3_db.py` | Incremental pull via `/dump` |
| Analysis | `analysis/` | H1/H2/H3 + markdown report (Phase 1.5) |

## Risk constraints

See `INVARIANTS.md` for non-negotiable rules.

## Current health snapshot (2026-05-24, EXP-002 HALTED on CU spike)

- Deployment status: **HALTED — REMOVED via `railway down --yes` on 2026-05-24**. Root cause identified 2026-05-25 (newHeads subscription leak in `_AlchemyTransport`; fix landed in commits `9ca2565` + `a810799`; architectural pattern in `D-017_ws-subscription-lifecycle.md`). Detector remains HALTED pending the next deploy with the fix — operator gate. See `failures/FAILURE_LOG.md` 2026-05-24 entry for the full RCA + lessons-learned write-up.
- Account utilization at halt: **1.59B / 2.5B CU = ~64% with 7 days left in May**. Forecast at halt time was ~2.4B if another spike fired.
- Data preserved (volume persists across redeploys):
  - `run_artifacts/exp_002_t24h/2026-05-17.jsonl`: 13,967 records (Run 1 first 7h — **1,238 cross-chain on Base↔Arbitrum, 12 unique cross-chain opp keys**)
  - `run_artifacts/exp_002_t21h/2026-05-23.jsonl`: 12,153 records of the reopened MSUSD/USDC arb (~7.9h continuous open window before close at 14:56 UTC May 23)
  - `run_artifacts/exp_002_d2/2026-05-24.jsonl`: 2 records of transient WETH-mid-WETH intra-chain arbs (146 + 93 bps margins) on Base block 46,419,096
  - Plus all rollup CSVs through 2026-05-23
- Monitored pool set at halt: 159 pools across 3 chains (Base 123, Arbitrum 34, Optimism 2 UniV3-only). Across fee table verified and frozen on volume.
- **Required before any redeploy**: spike attribution via Alchemy dashboard per-app filter (split layer3-trading-exp vs stellar-embrace). If our detector is implicated, code review the 2.8.4 changes for non-multicall RPC patterns. If stellar-embrace is the source (out of scope per I-3), user decides whether to address there.
- **Hypothesis status at halt**:
  - H1 (intra ≥1000/day): INVALIDATED long since (D-007); recent data shows ~3 unique intra-chain opps/day → unchanged
  - H1' (cross ≥50/day): WEAKENED; Run 1's 12 cross-chain in 7h is the ONLY cross-chain signal across all of EXP-002. ~7 days of additional wall-clock since produced zero. Approaching the <10/day falsification floor.
  - H2 (L3 flags ≥1%): testable but unflagged-only data so far
  - H4 (Pareto): n too low to evaluate

## Tests

204/204 passing as of 2026-05-13.
Run: `cd C:/Users/jason/Desktop/Trading && python -m pytest layer3_trading_exp/tests/`

## What this system is NOT (per spec §"What NOT to build")

- Not an execution system
- Not a wallet
- Not a flash loan client
- Not a private-mempool integrator (no bloxroute, no Flashbots)
- Not multi-chain (Phase 2+)
- Not adaptive (rules fixed at run start)
- Not a UI / dashboard / alerting system
- Does not refactor Layer 3
