# STRATEGY_STATE.md

**Why this system exists.** Updated 2026-05-16.

## Objective

Measure two things on Base **and on the Base ↔ Arbitrum ↔ Optimism cross-chain
surface**, end-to-end, with quantitative answers:

1. **The arbitrage opportunity landscape** — how many opportunities exist per
   day above a 0.3% gross-margin floor, what their distributional properties
   are, and how much of monitored TVL is structurally arbitrage-resistant.
   *(Base-only formulation of this objective tested and INVALIDATED at the
   $500K/$250K pool floors — see Invalidated hypotheses below. Cross-chain
   formulation pending Phase 2 spec.)*
2. **Layer 3's intelligence yield** — what fraction of detected opportunities
   does Layer 3's behavioral corpus flag, and do the flag distributions
   correlate with structural characteristics of the underlying pools.

The output is a research report, not a trading product. Phase 2 (execution)
is conditional on what this measurement reveals.

## Active hypotheses

| ID | Statement | Why we think this | What would falsify |
|---|---|---|---|
| H2 | Layer 3's intelligence flags ≥1% of detected opportunities | Layer 3 has 90K+ contracts indexed; if any of the pool/token contracts in arb paths overlap with L3's `confirmed`/`suspected` tiers or `org_wallets`/`drain_detected` rows, we'd expect ≥1% intersection. | 0% flag rate after 7 days — would mean either (a) detected opportunities are exclusively on legitimate infrastructure, or (b) L3 corpus has blind spots for the pools we monitor. **Currently WEAKENED — UNK-005 RESOLVED (D-012) so H2 is now structurally testable for 4 of 5 filter-critical tables (`contracts`, `deployers`, `bytecode_families`, `trap_events` all fresh under per-table thresholds). Only `trust_amplification`-dependent rules remain partially degraded due to L3's multi-day cadence on that table.** |
| H3 | Flagged opportunities have measurably different margin / pool-type / token distributions than unflagged ones | If L3's signals correlate with risky pool structures, flagged arbs should over-represent (a) low-fee pools used for routing exploits, (b) tokens with asymmetric transfer behavior, (c) pools deployed by `org_001`-adjacent deployers. | Mann-Whitney U p > 0.05 across all numeric fields AND χ² p > 0.05 across categoricals — would mean flagged vs unflagged distributions are indistinguishable, undermining H2's research value. **Currently WEAKENED — no flagged group exists in EXP-001 data; depends on Phase 2 producing variety.** |

## Invalidated hypotheses

| ID | Statement | Date invalidated | Linked decision | Evidence |
|---|---|---|---|---|
| H1 | ≥1,000 distinct arbitrage opportunities/day exist on Base at ≥0.3% gross margin | 2026-05-16 | **D-007** | EXP-001 produced 1 unique opportunity in ~3 days (~0.033% of threshold). ~104K blocks of post-closure observation contained zero opportunities. INVALIDATED *at current pool floors* ($500K UniV3+Slipstream, $250K Aerodrome v1). A re-formulated H1 with lower floors would be a different hypothesis. |

## Active experiments

### EXP-001: 1-day Base-only run — TERMINATED 2026-05-16

- **Started**: 2026-05-13 ~04:28 UTC (initial deploy), restarted ~05:33 UTC (index fix)
- **Terminated**: 2026-05-16 ~13:54 UTC via `railway down --yes` (planned terminate via `--minutes 1440` timer failed — see `failures/FAILURE_LOG.md` 2026-05-16)
- **Outcome**: H1 INVALIDATED at current floors (see Invalidated hypotheses + D-007). UNK-002 + UNK-006 RESOLVED (see D-008).
- **Data**: 2,420 JSONL records preserved in `run_artifacts/exp_001/`. Phase 1.5 analysis report at `run_artifacts/exp_001_analysis/analysis/report_2026-05-13_2026-05-13.md`.
- **Deployment**: Railway service removed.

### EXP-002: Phase 2 cross-chain (Arb + Optimism) — **LIVE** (deployed 2026-05-17 17:20 UTC, 7-day run)

- **Status**: **RUNNING** — deployed per D-014; deadline 2026-05-24 ~17:20 UTC.
- **Monitored set**: 159 pools (Base 123, Arb 34, OP 2). OP is UniV3-only — Velodrome factory addresses still unverified, deferred. Per-chain pool counts within ±20% spec estimates except OP (estimated ~95, actual 2 → cross-chain coverage on the WETH-USDC pair only, still sufficient for H1' on the canonical token).
- **Cross-chain scan routes**: 12 across the Base↔Arb↔OP triangle.
- **First-30-min health**: Base lag 0.4-0.8s, Arb (sampled 1/8) lag 0.5-1.2s, OP lag 0.2-0.3s. Sync cycle 19-20s, 0 errors. All within spec.
- **Spec**: `PHASE_2_CROSS_CHAIN_SPEC.md` (8 sub-phases, 3 new invariants I-12/I-13/I-14, reformulated H1′ + new H4, 7 pre-staged derivative decisions)
- **Decisions in place**:
  - ✅ **Bridge model**: Across Protocol (D-006). Latency 30s, fee point-estimate 10 bps/hop, sensitivity range 5-20 bps. Supports Base + Arb + OP triangle.
  - ✅ **Phase 1 conclusion**: H1 INVALIDATED at current Base-only floors (D-007), motivating the cross-chain pivot.
  - ✅ **Spec approval**: D-009.
  - ✅ **Bridge fee model + first verification**: D-010. First live verifier run (2026-05-16) found USDC/WETH/USDT bridge fees ~5-10x LOWER than D-006 point estimates (USDC live 1.4 bps vs 10 bps baseline; WETH ~1.2 bps vs 8). H1' is now more likely to be supported because the cross-chain cost basis is ~half what D-006 modeled. 20 tuples verified in 5s; 14 updated, 6 kept, 0 aborts.
- **Resolved during spec drafting (locked in spec, derivative decisions file at sub-phase boundaries)**:
  - ✅ Token registry: curated canonical list (USDC, WETH, USDT, DAI, cbBTC) per I-12
  - ✅ Schema migration: additive v1→v2 with `chain`, `path_chains`, `bridge_legs`, `schema_version: 2`
  - ✅ Round-trip vs one-way: round-trip primary, one-way derivable in post-run analysis
  - ✅ Margin floor: 0.50% gross (raised from Phase 1's 0.30% to absorb bridge cost)
  - ✅ Across fees: static table + one-shot deployment-time verification (I-13)
  - ✅ Latency-drift haircut: 0.1% of notional at 30s default (I-14)
- **To resolve at sub-phase boundaries**:
  - ✅ UNK-003 (Alchemy CU budget) — RESOLVED 2026-05-17 via D-015; live measurement ~1.5 req/s aggregate, projected ~34% of 300M/month free tier
  - ✅ UNK-005 (per-table freshness thresholds) — RESOLVED 2026-05-16 via D-012; Phase 1 replay: 100% → 0% fully degraded
  - ✅ UNK-008 (JSONL size at 3-chain volume) — RESOLVED 2026-05-16 via D-011; `per_address` debug block retained; size growth ~30%, well within 50 GB volume
- **Hard precondition for sub-phase 2.8 deployment**: `--minutes` timer-didn't-terminate failure (FAILURE_LOG 2026-05-16) must be fixed and pass regression test
- **Next gate**: Agent produces mandatory pre-work summary per spec; user reviews and approves before sub-phase 2.1 begins.

## Success criteria for current measurement phase

Since EXP-001 terminated and EXP-002 is in spec-drafting, the active
success criteria are for the spec, not for a running experiment:

- [ ] `phase_2_cross_chain_spec.md` drafted with bridge integration, token
      registry approach, schema migrations, trade structure choice, CU
      budget projection, JSONL size projection
- [ ] UNK-003 and UNK-008 marked RESOLVED with Linked Decisions during
      spec drafting
- [ ] UNK-005 resolution path (per-table freshness thresholds) integrated
      into the Phase 2 measurement plan so H2 is not structurally vacuous
- [ ] Spec ready for user review

## What we are NOT optimizing for

- **Not** real-time response (this is research observation)
- **Not** opportunity capture — we observe, we don't compete
- **Not** maximum throughput — staying inside Alchemy free tier matters more
- **Not** Layer 3 reformulation — we consume what L3 publishes

## Decision points the strategy depends on

If any of these flip, the strategy needs to re-spec:

| Trigger | Action |
|---|---|
| Phase 2 cross-chain run also produces near-zero opportunities | Reverse D-007; re-formulate H1 with lower floors and re-test Base-only (D-007 reversal criterion #1) |
| MSUSD/USDC arb (or similar persistent single-pair arb) reappears on Base within 30 days | Reverse D-008's UNK-002 resolution; reopen UNK-002 and document open-window distribution |
| Lag > 30s observed in any future deployment of same architecture under normal conditions | Reverse D-008's UNK-006 resolution; reopen UNK-006 and investigate index degradation / lock contention / drift |
| H2 stays vacuous (degraded=100%) in Phase 2 too | UNK-005 becomes a hard blocker; per-table freshness thresholds must be implemented before any H2 verdict |
| Filter eval p95 > 500ms persistently | Investigate sync lock contention; consider sync moving to separate process |
| Alchemy CU usage > 200M / month | Reduce monitoring frequency OR move sync off Alchemy |
| L3 admin token rotates | Update `LAYER3_ADMIN_TOKEN` env on Railway and restart |
