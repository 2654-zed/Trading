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
| Status | **RUNNING** — EXP-002 launched 2026-05-17 ~17:20 UTC via `railway up` (per D-014) |
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

## Current health snapshot (2026-05-17, EXP-002 LIVE)

- Deployment status: **RUNNING (resumed)**. Railway service `layer3-trading-exp`. EXP-002 first deploy stalled at T+7h via FAILURE_LOG 2026-05-18 (all WS subscriptions silently degraded). Redeployed 2026-05-18 ~23:10 UTC with the **WS-stall detector** patch (PoolMonitor.run wraps newHeads iteration with silence + same-block-repeating detectors that raise `WSStallError` → existing reconnect loop fires). New deadline ~2026-05-25 23:10 UTC.
- Pre-stall data preserved: `run_artifacts/exp_002_t24h/2026-05-17.jsonl` — 13,967 records (12,729 intra-chain Base + **1,238 cross-chain on Base↔Arbitrum**, 12 unique cross-chain opp keys). First empirical evidence that cross-chain detection produces non-trivial signal.
- Active experiment: EXP-002 (resumed), 7-day run (`--minutes 10080`).
- Monitored pool set: **159 pools across 3 chains** (Base 123, Arbitrum 34, Optimism 2). Optimism only has UniV3 pools — Velodrome factory addresses still need verification; deferred to a follow-up sub-phase.
- Cross-chain detector: **12 scan routes** active across the Base↔Arb↔OP triangle.
- Across fee table: loaded from `/app/data/run_metadata/across_fee_table.json` (verified 2026-05-17; USDC ~1.39 bps live vs 10 bps D-006 baseline — auto-updated, no aborts).
- Per-chain lag at sample (~30 min post-deploy):
  - Base: ~0.4-0.8s (well under 2s spec)
  - Arbitrum (sampled 1/8): ~0.5-1.2s (well under 5s spec)
  - Optimism: ~0.2-0.3s (well under 2s spec)
- Sync cycle: ~19-20s for ~80K rows, 0 errors.
- Live opp count (first ~30 min): Base intra-chain `+1 intra +0 cross` per block — the persistent MSUSD/USDC arb pattern appears to have **reopened** (D-008 UNK-002 resolution reversal technically fired; formal reversal at run-end LOOP). Cross-chain opp count so far: 0 (consistent with the cross-chain shape requiring inter-chain price drift > 50 bps net, which is rare for stable pairs).
- **--minutes 10080 timer-watchdog ARMED**: deadline ~2026-05-24 17:20 UTC. Live regression confirmed 2026-05-16 (FAILURE_LOG entry RESOLVED).

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
