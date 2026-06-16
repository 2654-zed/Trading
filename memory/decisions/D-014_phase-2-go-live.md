# D-014: Phase 2 go-live — EXP-002 deployment authorized

**Date**: 2026-05-16
**Made by**: user ("proceed to 2.8" instruction) + agent (sub-phase 2.8 entry per `PHASE_2_CROSS_CHAIN_SPEC.md`)
**Status**: ACTIVE

## Context

Sub-phases 2.1 through 2.7 are landed, tested (297/297 passing), and
each gate's stop-and-report has been reviewed and approved by the user.
Sub-phase 2.8 deploys the Phase 2 cross-chain detection stack to Railway
and starts EXP-002, the first measurement run after H1 was INVALIDATED
in Phase 1 (D-007).

This decision authorizes the deployment + frozen run configuration. The
LOOP at run-end (per I-10) will produce the next decision evaluating the
results.

## Sub-phase 2.8 hard preconditions check

| Precondition | Status |
|---|---|
| `--minutes` timer fix landed (FAILURE_LOG 2026-05-16) | ✅ Code in `time_budget.py` |
| `--minutes` timer fix unit-test regression | ✅ `test_time_budget.py::test_watchdog_fires_when_deadline_reached_and_cancels_peers` |
| `--minutes` timer fix LIVE regression | ✅ 2026-05-16: `--minutes 1 --chains base` exited at 67s (deadline 60 + budget 90 = 150 ceiling). 7s after deadline. |
| Across fee verification dry-run clean | ✅ D-010, 2026-05-16: 20 tuples, 5.0s, 0 aborts |
| All sub-phases 2.1–2.7 approved | ✅ User confirmation at each boundary |
| UNK-005 (per-table freshness) resolved | ✅ D-012 |
| UNK-008 (JSONL schema v2) resolved | ✅ D-011 |
| UNK-003 (Alchemy CU budget) resolved | ⏳ Resolves in-flight from live measurement during the first 30 min of EXP-002 (per spec sub-phase 2.2 acceptance) |
| D-014 (this decision) filed | ✅ |

UNK-003 is the only precondition not strictly satisfied. Per the user's
direction "proceed to 2.8", in-flight resolution from live data is
acceptable (the spec sub-phase 2.2 acceptance criterion always specified
"measured against dashboard after 30 minutes"). A follow-up D-015 will
file the resolution once the live measurement lands.

## Decision

**Deploy the Phase 2 stack to Railway, run EXP-002 for 7 days.**

Frozen run configuration:

| Element | Value | Source |
|---|---|---|
| Run duration | **7 days** (`--minutes 10080`) | Phase 2 spec § sub-phase 2.8 |
| Chains active | base, arbitrum, optimism | PHASE_2_CROSS_CHAIN_SPEC.md scope |
| Sampling cadence | Base 1×, OP 1×, Arb 1/8 | DEFAULT_SAMPLING_BY_CHAIN (D-009) |
| Bridge model | Across, verified at startup | D-006 + D-010 |
| Intra-chain margin floor | 30 bps | Phase 1 floor preserved |
| Cross-chain margin floor | 50 bps post-haircut | D-009 / D-013 |
| Pool floors | $500K CL, $250K CPAMM/Solidly | Phase 2 spec § sub-phase 2.1 |
| Notional per opp | $10,000 | Phase 1 baseline |
| Flash loan model | Aave V3 5 bps on borrowed amount | Phase 1 baseline |
| Bridge fee | static table from verifier output, frozen per I-13 | D-010 |
| Latency drift haircut | 10/15/20 bps per token volatility class | D-009 / I-14 |
| L3 freshness thresholds | per-table (contracts/deployers 1h, ..., trust_amp 36h) | D-012 |
| JSONL schema | v2 (additive over v1) | D-011 |
| Container restart policy | ON_FAILURE, maxRetries 3 | railway.json |
| Kill switch | `/app/data/KILL_SWITCH` (touch to halt) | I-8 |
| Time-budget watchdog | enabled, 5s tick | sub-phase 2.2 fix |

## Operational expectations

- **First 30 minutes**: Confirm per-chain monitors connect, multicalls succeed, lag sub-2s for Base/OP and sub-5s for Arb. Sample Alchemy dashboard CU usage rate → file D-015 resolving UNK-003.
- **First hour**: At least some intra-chain detection activity on Base (Phase 1 monitored set is unchanged at $500K/$250K floors; some MSUSD/USDC-like patterns may reappear). Cross-chain opportunities at this calibration are expected to be rare; we measure the rate empirically over the full 7 days.
- **Mid-run**: Per I-11 T-A, lag > 30s triggers intervention. Per I-11 T-B, hypothesis invalidation triggers loop + decision logging.
- **End-of-run** (or sooner if H1' invalidated): Execute the full LOOP per `loop/LOOP.md`. Produce trades/2026-05-XX_exp-002-summary.md with H1' / H2 / H3 / H4 verdicts. File the next strategy decision.

## Rationale

- Sub-phase ordering and stop-and-report discipline meant every code
  path landed under explicit review. Total: 297 tests passing, 5 spec
  acceptance criteria met per sub-phase boundary.
- D-007's invalidation of Phase 1 H1 created the empirical motivation
  for Phase 2; D-010's discovery that live Across fees are ~5-10× lower
  than D-006 baseline strengthens the case that H1' should be more
  testable than H1 was on Base.
- The 7-day run length matches the spec; the deployment has the
  watchdog fix from sub-phase 2.2 so the run will respect `--minutes
  10080` even if WS subscriptions stall mid-run.

## Consequences expected

- Railway deployment becomes ACTIVE for the layer3-trading-exp service
  (the service exists; deployment was REMOVED post-EXP-001 per D-008
  context; sub-phase 2.8 re-activates it)
- New JSONL files land in `/app/data/logs/` per UTC day; new v2 records
- New `across_fee_table.json` written at first cold start (skipped on
  subsequent restarts per I-13)
- Alchemy CU usage spikes against the free-tier 300M/month budget;
  early measurement confirms or invalidates the projection from
  PHASE_2_CROSS_CHAIN_SPEC.md sub-phase 2.2 (~22M CU/month projected)
- If lag exceeds 30s persistently within the first hour, sub-phase 2.8's
  acceptance criterion fires and we halt + diagnose before re-deploy
- At end of run (or earlier intervention), full LOOP execution per I-10

## Reversal criteria

This decision flips to REVERSED if any of:

1. **Across fee verification aborts on Railway** when it didn't on the
   local dry-run. Investigate route-specific drift; refile after fix.
2. **Per-chain lag exceeds 30s within the first hour** under steady-
   state conditions (not transient WS reconnects). Indicates Phase 2.2's
   monitoring layer can't handle the live 3-chain load — re-architect
   sampling cadence or chain selection.
3. **Alchemy CU usage rate projects > 250M/month** at 30-minute mark
   (>80% of free tier). Either reduce sampling cadence (4x cheaper
   Arbitrum sampling) or drop a chain.
4. **`across_fee_table.json` produces opportunities that wildly mismatch
   live bridge quotes** post-verification (e.g., the table's frozen
   values stop matching live within the first day). I-13 means we keep
   the frozen table for the run; the mismatch is a follow-up note for
   the next D-NNN, not an in-flight change.
5. **The watchdog regression DOESN'T hold on Railway** (--minutes 10080
   gets exceeded materially). This would mean the watchdog has a
   Linux-on-Railway-specific bug that the local Windows test missed.
   Manual `railway down` + diagnose.

Partial reversals allowed: a single chain can be removed from the run
without unwinding the whole deployment.

## Consequences for the memory system

- `decisions/README.md` active table — add D-014 row
- `STRATEGY_STATE.md` EXP-002 — flip status from "ACTIVE (pre-work)" to
  "ACTIVE (LIVE — EXP-002 running)"
- `SYSTEM_STATE.md` — flip deployment status from REMOVED to RUNNING
  once `railway up` completes; populate active monitored-set + active
  strategies
- `loop/LOOP.md` NEXT FOCUS — shift to "monitor EXP-002 first hour;
  resolve UNK-003 from live CU measurement"
- `unknowns/UNKNOWNS.md` UNK-003 — note in-flight resolution path
- `failures/FAILURE_LOG.md` — none expected; add if anomalies surface

## Links

- Spec sub-phase: `../../docs/archive/PHASE_2_CROSS_CHAIN_SPEC.md` § sub-phase 2.8
- Phase 2 umbrella: `D-009_phase-2-cross-chain-spec-approved.md`
- H1 invalidation that drove Phase 2: `D-007_h1-invalidated-at-current-floors.md`
- Cross-chain detector: `../../layer3_trading_exp/cross_chain_detector.py`
- Watchdog regression: `../failures/FAILURE_LOG.md` 2026-05-16 entry (RESOLVED)
