# Layer 3 Trading Research Experiment — Phase 2 Specification

**Status:** Draft for user review (per `memory/loop/LOOP.md` NEXT FOCUS, set 2026-05-16 14:30 UTC)
**Scope:** Phase 2 (cross-chain detection only, no on-chain execution)
**Chains:** Base + Arbitrum + Optimism (the Across Protocol L2 triangle)
**Capital at risk:** $0 (Phase 2 remains detection and logging only)
**Expected duration:** 7 days of continuous observation OR 50,000 logged opportunities, whichever comes first
**Author:** agent (draft); requires user approval before any code
**Handoff target:** A fresh Claude Code session tasked with translating this specification into working software

---

## Why Phase 2 exists

Phase 1's H1 ("≥1,000 distinct arbitrage opportunities/day on Base at ≥0.3% gross margin") was **INVALIDATED** at current pool floors per `memory/decisions/D-007_h1-invalidated-at-current-floors.md`. EXP-001 produced 1 unique opportunity in ~3 days (~0.033% of threshold); ~104K post-closure blocks contained zero opportunities. The steady-state intra-Base opportunity rate at $500K/$250K floors is empirically near-zero.

Phase 2 measures a structurally different opportunity surface: **cross-chain arbitrage** between Base, Arbitrum, and Optimism via the Across Protocol bridge (per `memory/decisions/D-006_bridge-model-across.md`). The opportunity-generating process is fundamentally distinct from intra-chain Phase 1:

| Dimension | Phase 1 (intra-Base) | Phase 2 (Base ↔ Arb ↔ OP) |
|---|---|---|
| Source of edge | Cross-pool price drift within one chain | Chain-relative price drift across L2s + bridge-window risk premium |
| Latency budget | ~2s (Base block time) | ~30s (Across L2→L2 fill time, per D-006) |
| Cost floor | Aave V3 flash 0.05% + gas | Aave V3 flash 0.05% + 2× bridge fee (~20 bps) + 2× swap gas |
| Margin floor | 0.30% gross | 0.50% gross (raised; see "Margin floor" below) |
| Falsification | <100 opps/day after 7 days | <50 cross-chain opps/day after 7 days |

The H1 failure on Base does not predict similar failure on cross-chain — the two regimes have different drivers.

---

## MANDATORY PRE-WORK

Read the following files completely before writing any code. After reading, produce a one-paragraph summary; **do not proceed to implementation until the summary is reviewed and approved by Jason.**

1. `LAYER3_TRADING_EXPERIMENT.md` — the Phase 1 spec. Phase 2 inherits its discipline (detection only, no LLM, frozen rules, kill authority, etc.).
2. `PHASE_1_1_ADDENDUM.md` — the pool-enumeration pivot. Phase 2 extends this approach to 3 chains.
3. `memory/STRATEGY_STATE.md` — current hypotheses including H1 INVALIDATED + H2/H3 status.
4. `memory/INVARIANTS.md` — all 11 invariants carry into Phase 2.
5. `memory/decisions/D-006_bridge-model-across.md` — the bridge model premise.
6. `memory/decisions/D-007_h1-invalidated-at-current-floors.md` — what Phase 2 is responding to and the reversal criteria that govern it.
7. `memory/unknowns/UNKNOWNS.md` — UNK-003, UNK-005, UNK-008 are gated on this spec resolving them.
8. `memory/failures/FAILURE_LOG.md` 2026-05-16 entry — the `--minutes` timer bug. Phase 2 deployment cannot ship until this is fixed.
9. Across Protocol docs: https://docs.across.to/ — specifically the suggested-fees API (`/api/suggested-fees`) and the L2→L2 fill-time distribution.
10. The existing `layer3_trading_exp/` codebase — every module that takes a `PoolInfo` or `Opportunity` will need a schema migration.

Produce a one-paragraph summary covering:
1. What changes in the opportunity definition when chains are heterogeneous
2. Which Phase 1 modules need a schema-level change vs. an additive change
3. What the steady-state runtime looks like across 3 WS subscriptions + 3 HTTP RPC paths

---

## RESEARCH HYPOTHESES

### Re-formulated primary hypothesis (replaces H1)

**H1′ (Phase 2):** ≥50 distinct cross-chain arbitrage opportunities per day exist on the Base–Arbitrum–Optimism triangle at ≥0.50% gross margin (after the Across point-estimate fee of 10 bps × 2 bridge legs).

- "Distinct" = unique `(src_chain, dst_chain, borrowed_token, dst_pool)` tuple
- 50/day is set at **5%** of Phase 1's H1 threshold; cross-chain opportunities are structurally rarer (require multi-chain drift + bridge window) so the bar is appropriately lower
- Falsification: <10 distinct opportunities per day after 7 days

### Carried-forward hypotheses

**H2** (Layer 3 flags ≥1% of detected opportunities) — carries forward unchanged. Phase 2 must resolve UNK-005 (per-table freshness thresholds) so H2 is not structurally vacuous as it was in Phase 1.

**H3** (flagged vs. unflagged opportunities have distributionally different properties) — carries forward; depends on H2 producing a non-empty flagged group.

### New secondary hypothesis

**H4 (Phase 2):** Cross-chain opportunities concentrate on a small subset of the chain × token matrix (Pareto-style: ≥80% of opportunities involve ≤20% of the (chain-pair, token) combinations).

- Why: bridge liquidity, token decimals, and L2-DEX TVL distributions are not uniform across the matrix. If H4 holds, future runs can prune the matrix without losing signal.
- Falsification: top-20% combinations account for <40% of opportunities (i.e., no concentration).

### Success criteria for Phase 2

Phase 2 succeeds when the following are produced, regardless of what H1′/H2/H3/H4 resolve to:

1. A log of ≥50,000 records (any combination of intra-chain and cross-chain) OR 7 days of continuous observation
2. Per-opportunity records with chain attribution, bridge-leg details, and per-rule L3 evaluations
3. Daily rollup statistics per chain and per chain pair
4. End-of-run H1′/H2/H3/H4 analysis with confidence intervals
5. No on-chain state changes caused by detection software (verified)
6. Phase 1's `--minutes` timer bug fixed and verified

Phase 2 does NOT succeed or fail based on whether hypotheses are supported.

---

## SPEC — what Phase 2 does

### High-level architecture

```
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│ Base monitor  │  │ Arb monitor   │  │ OP monitor    │
│ (WS + multi)  │  │ (WS + multi)  │  │ (WS + multi)  │
└───────┬───────┘  └───────┬───────┘  └───────┬───────┘
        │                  │                  │
        └────────┬─────────┴──────────────────┘
                 v
        ┌──────────────────────────┐
        │ Multi-chain detector     │
        │ - intra-chain (per chain)│
        │ - cross-chain (chain×ch) │
        │ - applies bridge cost +  │
        │   latency-drift penalty  │
        └──────────┬───────────────┘
                   v
        ┌──────────────────────────┐
        │ Filter pipeline (L3)     │
        │ - 13 rules + per-chain   │
        │   chain-aware lookups    │
        │ - per-table freshness    │
        │   thresholds (UNK-005)   │
        └──────────┬───────────────┘
                   v
        ┌──────────────────────────┐
        │ JSONL logger             │
        │ - daily rotation         │
        │ - chain field + bridge   │
        │   leg field added        │
        └──────────────────────────┘
```

### Integration points

**Read from Base, Arbitrum, Optimism chains:**
- Pool contracts via read-only `eth_call`/multicall3 (one Alchemy endpoint per chain)
- Block headers via `newHeads` WS subscription (one per chain)

**Read from Layer 3:**
- Same `/dump` incremental-sync model as Phase 1
- L3 indexes contracts across multiple chains; Phase 2 reads must filter by `chain` column where present, otherwise treat as chain-agnostic (TODO sub-decision below)

**Read from Across Protocol (static; see below):**
- A precomputed static fee table per (src_chain, dst_chain, token) — NOT live API calls in runtime
- One-shot API verification at deployment time to confirm the static fees are within sensitivity range

**Write to:**
- Local filesystem: opportunity logs (JSONL), daily rollups (CSV), run metadata
- No on-chain writes anywhere on any chain

### What Phase 2 does NOT do

Carrying forward Phase 1's "what NOT to build" with cross-chain additions:

- Does NOT execute any on-chain transaction with value on any chain
- Does NOT fetch live Across fee quotes per opportunity (static table; see Across integration below)
- Does NOT call any Across SDK or relayer endpoint that writes
- Does NOT use any bridge other than Across (single-bridge model per D-006)
- Does NOT support tokens outside the curated canonical list (USDC, WETH, USDT, DAI, cbBTC) in this Phase
- Does NOT do auto-discovery of cross-chain token mappings (deferred to a hypothetical Phase 2.X follow-on)
- Does NOT model atomic cross-chain trades (no such primitive exists at this latency)
- Does NOT adapt rules, thresholds, or pool sets during the run
- Does NOT use LLM inference in the runtime pipeline
- Does NOT integrate with bloxroute, Flashbots, or any MEV submission infrastructure

---

## INVARIANTS (carry forward from Phase 1 + Phase 2 additions)

All 11 invariants from `memory/INVARIANTS.md` apply unchanged. Three additions specific to Phase 2:

**I-12 (Phase 2): Same-token-different-chain mapping is canonical only.**
A token is "the same token across chains" iff it appears in the curated canonical mapping shipped in `token_registry.json`. We do NOT infer same-token from symbol matching or wrapped/unwrapped variants at runtime. Phase 2 cannot detect opportunities involving any token outside the canonical mapping.

**I-13 (Phase 2): Bridge fees are static for the duration of the run.**
The static fee table is loaded at run start and frozen. Mid-run fee changes are not observed. (Per I-2 / I-5: rules are fixed for the duration of the run.) A one-shot verification compares the static table to a live Across API quote at deployment time; if any (src, dst, token) is outside ±5 bps of the static value, abort the run and update the table.

**I-14 (Phase 2): Cross-chain opportunities must clear the latency-drift haircut.**
For any cross-chain opportunity, the destination-pool price observed at detection time is haircut by an estimated price-drift over the bridge latency window (default 30s). If the haircut puts the opportunity below the margin floor, it is NOT logged as an opportunity. Per-record JSONL still records the unhaircut margin for analysis.

---

## SUB-PHASES

Each sub-phase has acceptance criteria. Stop at each sub-phase boundary, report status, wait for approval before proceeding.

### Phase 2.1: Pool enumeration across 3 chains

**Goal:** Extend the DefiLlama-based enumeration (D-001 + Phase 1.1 addendum) to produce a 3-chain pool set with chain attribution.

**Files to create / modify:**
- `layer3_trading_exp/pool_set.py` — extend `PoolInfo` with `chain: Literal["base", "arbitrum", "optimism"]`
- `layer3_trading_exp/scripts/enumerate_pools.py` — extend to accept `--chain` flag, default to all three
- `layer3_trading_exp/quote/` — add adapters for any Arb/OP-specific AMM forks not already supported (see protocol coverage table below)

**Pool set per chain (initial floors):**

Floors are **absolute USD**, not TVL-proportional. Same-absolute-dollar makes cross-chain rates directly comparable; proportional floors would smuggle TVL ratios into the comparison.

| Chain | UniV3 + concentrated-liquidity floor | Classic AMM / Solidly floor | Protocols included |
|---|---|---|---|
| Base | $500K | $250K | UniV3, Aerodrome Slipstream (CL), Aerodrome v1 volatile + stable |
| Arbitrum | $500K | $250K | UniV3, Camelot V3, Camelot V2 (CPAMM), SushiSwap (CPAMM) |
| Optimism | $500K | $250K | UniV3, Velodrome V2 (Slipstream-equivalent), Velodrome V1 stable + volatile |

Projected pool count (DefiLlama-based estimate): Base ~128 (unchanged from Phase 1), Arb ~180, OP ~95. **Total ≈ 400 pools.** Multicall3 batching keeps this feasible (verified in Phase 1 at 128 pools with sub-second lag).

**Acceptance criteria:**
- [ ] `enumerate_pools.py --chain all` writes `monitored_pools.json` with `chain` field on every record
- [ ] Per-chain counts within ±20% of estimates above (sanity check, not hard gate)
- [ ] All AMM types in the table above have working quote adapters (CL, CPAMM, Solidly stable)
- [ ] No on-chain writes during enumeration (verified by code review)
- [ ] Existing 204+ tests pass; new tests added for the per-chain enumeration path

**Stop here. Report: per-chain pool count, enumeration wall time, any Arb/OP protocols requiring new quote adapter work. Wait for approval.**

---

### Phase 2.2: Multi-chain pool monitoring

**Goal:** Run three concurrent pool monitors (Base, Arb, OP) inside one process, each with its own WS subscription and multicall3 HTTP path.

**Files to create / modify:**
- `layer3_trading_exp/pool_monitor.py` — make `PoolMonitor` instantiable per chain; lift any process-global state
- `layer3_trading_exp/scripts/detect_dry_run.py` — orchestrate 3 `PoolMonitor` instances under one asyncio event loop
- `layer3_trading_exp/config.py` — add `BASE_*`, `ARB_*`, `OP_*` env triples for WS + HTTP URLs

**Sampling cadence (resolves part of UNK-003):**

Arbitrum has ~250ms blocks; sampling every Arb block would push CU usage past the free-tier rate cap (25 RPS) without batching. Decision:

| Chain | Block time | Sampling cadence | Rationale |
|---|---|---|---|
| Base | ~2s | Every block | Phase 1 baseline; unchanged |
| Arbitrum | ~250ms | Every 8 blocks (~2s) | Match Base cadence; one multicall per 2s wall-time keeps CU within budget |
| Optimism | ~2s | Every block | Same as Base |

Sampling is implemented by counting block-ticks per chain and acting only on tick % N == 0.

**Acceptance criteria:**
- [ ] Three `PoolMonitor` instances run concurrently for 10 minutes with no asyncio deadlocks
- [ ] Per-chain lag (`block X lag=Y`) stays sub-2s for Base + OP and sub-5s for Arb (sampled cadence)
- [ ] WS disconnect on any one chain is logged with a GAP marker and does not crash the other two monitors
- [ ] Alchemy CU usage measured against dashboard after 30 minutes; projected monthly extrapolated and documented in **UNK-003 resolution decision**

**Stop here. Report: lag distributions per chain, CU consumption rate, any RPC errors observed. Wait for approval.**

---

### Phase 2.3: Cross-chain opportunity detection

**Goal:** Extend the opportunity detector to find arb paths that span two chains via Across.

**Files to create / modify:**
- `layer3_trading_exp/opportunity_detector.py` — add cross-chain path scanning
- `layer3_trading_exp/bridge_model.py` (new) — static Across fee table, latency-drift haircut, route validation
- `layer3_trading_exp/token_registry.py` (new) — curated canonical mapping per I-12
- `layer3_trading_exp/tests/test_cross_chain_detector.py` (new) — synthetic price spreads across chains

**Opportunity definition (cross-chain, round-trip flash-loan-funded):**

A cross-chain opportunity is a path:

```
borrow USDC on Base (flash from Aave V3)
  → bridge USDC Base→Arb via Across (pay bridge_fee_1, wait 30s)
  → swap USDC→WETH on pool_X (Arb)
  → swap WETH→USDC on pool_Y (Arb)
  → bridge USDC Arb→Base via Across (pay bridge_fee_2, wait 30s)
  → repay flash on Base
```

Equivalent path variants exist for any (src_chain, dst_chain) in {Base, Arb, OP}².

The detection condition:
```
gross_margin
  - aave_flash_fee (0.05%)
  - 2 × bridge_fee (default 10 bps each, configurable per token via static table)
  - 2 × dst_chain_swap_gas_usd
  - latency_drift_haircut (default: 0.1% of notional at 30s latency)
  >= 0.50%  (Phase 2 margin floor)
```

Per I-14: if `latency_drift_haircut` pushes the path below floor, it is NOT logged as an opportunity. The unhaircut margin is still computed for the analysis-only `gross_margin_raw` field.

**Round-trip is primary; one-way is a secondary analysis (per D-006 sub-decision resolution):**

Phase 2 detects and logs **round-trip** cross-chain opportunities only. One-way (rebalance) opportunities are derivable from the same data in post-run analysis (use only the first leg of any logged round-trip with `bridge_fee_2 = 0`). Live detection of one-way opportunities would require modeling inventory and is out of scope.

**Acceptance criteria:**
- [ ] Synthetic test cases: detector finds a known cross-chain opportunity in test data and rejects a known sub-floor case
- [ ] Per-block detection wall time stays under 50ms for the 400-pool monitored set across 3 chains
- [ ] Token registry enforces I-12: paths involving any non-canonical token are skipped
- [ ] Bridge model enforces I-14: paths below floor after haircut are not logged
- [ ] Round-trip path generation is exhaustive over the curated token list × (src, dst) pairs (no missing combinations in unit tests)

**Stop here. Report: detector wall-time histogram, count of opportunity paths considered per block, count rejected by bridge model. Wait for approval.**

---

### Phase 2.4: Across fee model (static table)

**Goal:** Ship a frozen fee table + a one-shot deployment-time verification step.

**Files to create / modify:**
- `layer3_trading_exp/run_metadata/across_fee_table.json` — static fees per (src, dst, token)
- `layer3_trading_exp/scripts/verify_across_fees.py` — one-shot script that queries Across `/api/suggested-fees` for every (src, dst, token) tuple in the table, fails if any drift is >5 bps from the static value
- `entrypoint.sh` — call `verify_across_fees.py` before starting the detector; abort run on verification failure

**Static fee table (D-006 baseline):**

| Token | Default fee | Source |
|---|---|---|
| USDC (any L2 ↔ any L2) | 10 bps | D-006 point estimate |
| WETH | 8 bps | Across historical; verify on deploy |
| USDT | 12 bps | Across historical; verify on deploy |
| DAI | 10 bps | D-006 point estimate |
| cbBTC | 15 bps | Across historical; verify on deploy |

These are STARTING VALUES. The deployment-time verification step rewrites any value that drifts >5 bps. If any drift is >20 bps (outside D-006's sensitivity range), the deployment aborts and the user is asked whether to update the D-006 sensitivity range or abandon the (src, dst, token) tuple.

**Acceptance criteria:**
- [ ] `verify_across_fees.py` runs in under 60s against live Across API
- [ ] All 5 canonical tokens × 6 directional chain pairs (30 tuples) verified at run start
- [ ] On verification failure, deployment aborts with a loud log line; no detector starts
- [ ] Static table is immutable once `entrypoint.sh` proceeds past verification (per I-13)

**Stop here. Report: verification wall time, any tuples that drifted >5 bps from defaults, any that required user intervention. Wait for approval.**

---

### Phase 2.5: Schema migration

**Goal:** Add chain attribution and bridge-leg detail throughout the data path without breaking Phase 1 analysis scripts.

**Files to modify:**
- `layer3_trading_exp/types.py` (or wherever `PoolInfo`, `Opportunity` live)
- `layer3_trading_exp/logger.py` — JSONL schema bump from v1 to v2
- `analysis/` scripts — handle both v1 and v2 schemas (read-side back-compat)

**Schema additions:**

`PoolInfo` gets:
- `chain: Literal["base", "arbitrum", "optimism"]`

`Opportunity` gets:
- `path_chains: list[str]` — chain per pool hop; intra-chain opps have `["base", "base"]`, cross-chain `["base", "arbitrum", "arbitrum", "base"]` etc.
- `bridge_legs: list[BridgeLeg] | []` — empty for intra-chain; one `BridgeLeg` per hop with `src_chain`, `dst_chain`, `token`, `fee_bps`, `latency_s`
- `latency_drift_haircut_bps: float | 0.0` — what the I-14 haircut took off
- `gross_margin_raw_bps: float` — pre-haircut margin (for analysis sensitivity)
- `cost_breakdown.bridge_fees_bps: float` — separated from `cost_breakdown.swap_fees_bps`

JSONL schema v2:
- Adds `schema_version: 2` at the top of every record
- Adds `chains: list[str]` summary array
- Adds `bridge_legs` array (empty for intra-chain records, preserving back-compat)
- All v1 fields remain unchanged

**JSONL size projection (resolves UNK-008):**

Phase 1 baseline: ~2.5 KB/record. Phase 2 additions (chain fields, bridge legs, schema version): ~+0.8 KB/record → **~3.3 KB/record**.

| Projection | Records / day | JSONL / day | 7-day total |
|---|---|---|---|
| Conservative (10× Phase 1 unique rate, ~3 per day cross-chain) | ~30 | ~100 KB | ~700 KB |
| Moderate (~100/day) | ~100 | ~330 KB | ~2.3 MB |
| If H1' clears threshold (~50/day baseline, 5× during arb windows) | ~250 | ~825 KB | ~5.8 MB |
| Tail risk (persistent multi-chain arb similar to MSUSD/USDC) | ~30,000 | ~100 MB | ~700 MB |

All projections fit comfortably on the existing 50 GB Railway volume. **Recommend keeping the `per_address` debug block** (UNK-008 decision: don't drop it). If observed record volume exceeds 200 MB/day projection at T+2h sample, drop the block via a runtime config flag.

**Acceptance criteria:**
- [ ] All 204+ Phase 1 tests pass after schema migration (v1 analysis scripts handle v2 records)
- [ ] Phase 1's logged JSONL (`run_artifacts/exp_001/2026-05-13.jsonl`) replays through the new analysis pipeline without errors
- [ ] New v2 records validate against the v2 schema (formal `jsonschema` validation in tests)
- [ ] Migration is additive only — no v1 field removed

**Stop here. Report: test pass count, replay verification result, schema diff. Wait for approval.**

---

### Phase 2.6: L3 freshness + sync for cross-chain (resolves UNK-005)

**Goal:** Per-table freshness thresholds so H2 is not structurally vacuous in Phase 2.

**Files to modify:**
- `layer3_trading_exp/freshness.py` — replace single 30-min threshold with per-table thresholds
- `layer3_trading_exp/filter_pipeline.py` — degraded flag is now per-table (rule may be fully fresh even if another table is stale)

**Per-table thresholds (UNK-005 resolution):**

| Table | Threshold | Source / rationale |
|---|---|---|
| `contracts` | 1 hour | Updates on new contract deployments (cadence ~minutes) |
| `deployers` | 1 hour | Derived from contracts; same cadence |
| `bytecode_families` | 6 hours | Family classifier runs every few hours per L3 ops |
| `trap_events` | 24 hours | Trap detection is batched daily per L3 ops |
| `trust_amplification` | 36 hours | Slowest L3 table; CRITICAL flags are stable over multi-day windows |
| All other tables | 6 hours | Default |

The decision creating these thresholds (`D-NNN_per-table-freshness-thresholds`) must ship with this sub-phase and resolves UNK-005.

**Acceptance criteria:**
- [ ] `freshness.py` returns per-table `stale` booleans
- [ ] `filter_pipeline.evaluate()` populates `degraded_per_table: dict[str, bool]` instead of a single `degraded` boolean
- [ ] JSONL v2 schema includes `degraded_per_table`
- [ ] Replay of Phase 1 JSONL through Phase 2 freshness logic shows `<10%` of records fully degraded (was 100% in Phase 1)

**Stop here. Report: degraded-rate distribution on the replay; commit the per-table-thresholds decision. Wait for approval.**

---

### Phase 2.7: Analysis updates (H1′ + H4)

**Goal:** Extend `analysis/` so the daily and end-of-run reports speak the cross-chain language.

**Files to create / modify:**
- `analysis/h1_prime.py` (new) — H1′ extrapolation with chain attribution
- `analysis/h4_pareto.py` (new) — Pareto analysis over (chain-pair, token) combinations
- `analysis/run_analysis.py` — wire in H1′ and H4; keep H2 and H3 untouched in core math (they're chain-agnostic)

**Acceptance criteria:**
- [ ] Running analysis against a synthetic 7-day cross-chain JSONL produces H1′ verdict, H4 verdict, H2/H3 verdicts
- [ ] Confidence intervals computed and reported for H1′ daily rate
- [ ] Phase 1 EXP-001 JSONL passed through the same pipeline still produces sensible (Base-only) H1 numbers per legacy path
- [ ] Markdown report template extended with cross-chain summary section and per-chain-pair breakdown

**Stop here. Report: synthetic-data verdicts, replay verdicts. Wait for approval.**

---

### Phase 2.8: Deployment + EXP-002 dry-run

**Goal:** Land the Railway deployment and execute a 7-day measurement run.

**Prerequisites that must be cleared BEFORE this sub-phase starts:**
- ✅ `--minutes` timer-didn't-terminate bug fixed (the open 2026-05-16 failure entry). Until this is verified, no time-bounded run is allowed.
- ✅ Across fee verification clean in dry-run
- ✅ All sub-phases 2.1–2.7 approved
- ✅ UNK-003 resolved (Alchemy CU budget decision filed)
- ✅ UNK-005 resolved (per-table thresholds decision filed)
- ✅ UNK-008 resolved (JSONL size decision filed)
- ✅ A new `D-NNN_phase-2-cross-chain.md` decision filed referencing this spec as approved

**Files to modify:**
- `Dockerfile` — same as Phase 1 baseline; bump CMD to `["--minutes", "10080"]` (7 days)
- `entrypoint.sh` — add Across fee verification + per-chain pool enumeration if missing
- `railway.json` — keep `restartPolicyType: ON_FAILURE`, set `maxRetries: 3`
- `--minutes` timer fix (per the open failure)

**Acceptance criteria:**
- [ ] Dry-run on local for 1 hour with all 3 chains active produces non-empty JSONL with no errors
- [ ] `--minutes 60` terminates the process within 90 seconds of the deadline (regression test for the open failure)
- [ ] Railway deployment passes the Across verification step on cold start
- [ ] First hour of live run: per-chain lag within spec, sync cycles error-free, no GAP markers
- [ ] LOOP fires after the 7-day run completes (or sooner if a hypothesis is invalidated mid-run)

**Stop here. Report: live deployment health snapshot, full LOOP after run end.**

---

## DECISIONS THIS SPEC LOCKS IN

If the user approves this spec, the following decisions are committed:

1. **D-NNN_phase-2-cross-chain.md** — the umbrella approval; references this file
2. **D-NNN_token-registry-curated-only.md** — curated canonical mapping (I-12)
3. **D-NNN_phase-2-margin-floor.md** — 0.50% gross margin floor for cross-chain
4. **D-NNN_per-table-freshness-thresholds.md** — resolves UNK-005
5. **D-NNN_alchemy-cu-budget-phase-2.md** — sampling cadences chosen above; resolves UNK-003
6. **D-NNN_jsonl-schema-v2.md** — additive migration with `per_address` retained; resolves UNK-008
7. **D-NNN_across-static-fee-model.md** — static table + deployment-time verification; locks in I-13

Each gets a full decision file at the boundary it resolves (most file together with Phase 2.6 / 2.4 / 2.2).

## REVERSAL TRIGGERS FOR THIS SPEC

The spec is reversed (back to Base-only with reformulated H1 or a different cross-chain bridge) if any of:

- **Across fee verification fails repeatedly at deployment.** If 3 consecutive deployment attempts find >20 bps drift on any canonical tuple, D-006 itself needs revisiting (bridge model premise wrong).
- **Phase 2.2 measurement shows projected monthly CU > 250M.** That's >80% of the free tier — too tight to commit. Revisit sampling cadences or chain selection.
- **Phase 2.5 schema migration breaks Phase 1 replay.** Means the migration is not additive; redo design.
- **Phase 2.8 dry-run produces zero opportunities in first 24 hours.** Different signal than Phase 1's zero-after-arb-closure — would indicate detection bug, not market regime. Fix before going live.
- **The `--minutes` timer fix doesn't hold under regression test.** Cannot run any time-bounded measurement until this is solved.

## WHAT THIS SPEC ASSUMES

- The Phase 1 codebase architecture (asyncio event loop, multicall3 batching, JSONL writer, L3 sync via `/dump`) is sound and extends to 3 chains. Phase 1 ran for ~3 days with sub-1s lag — strong prior.
- Across Protocol's API remains stable in shape and response format during the spec-drafting → run window
- Alchemy's free-tier rate caps (25 RPS) and CU caps (300M/month) remain unchanged
- L3's `/dump` endpoint continues to expose `chain` columns on tables where they exist; if not, contract-address-based lookups remain chain-agnostic
- The user retains kill authority + the ability to `railway down --yes` mid-run

## OUT OF SCOPE (NOT in Phase 2)

- Any execution-mode work (Phase 3+)
- Non-Across bridges (single-bridge per D-006)
- Non-canonical tokens (per I-12)
- 3-hop intra-chain paths (Phase 1 was 2-hop; Phase 2 stays 2-hop intra-chain, adds 2-hop-with-bridges cross-chain)
- Triangle arbitrage across three chains in one path (Base→Arb→OP→Base) — modeled as composition of two 2-chain hops only; full triangles deferred
- Layer 3 corpus extension or reformulation
- Any UI / dashboard / alerting beyond JSONL + CSV outputs

---

## NEXT STEP

User reviews this spec. If approved:

1. Agent files the umbrella `D-NNN_phase-2-cross-chain.md` decision referencing this spec
2. Agent files the `--minutes` timer-fix follow-up (closes the open 2026-05-16 failure)
3. Agent starts sub-phase 2.1 with the mandatory pre-work summary

If not approved, agent edits this spec per feedback and re-presents.
