# D-003: Cut detection run from 7 days to 1 day

**Date**: 2026-05-13
**Made by**: user (mid-session decision)
**Status**: ACTIVE

## Context

Original Phase 1.6 spec configured the live detection run for 7 days
(`--minutes 10080`). The first ~2 hours of data showed:

- Single persistent opportunity (MSUSD/USDC arb) emitting every block
- Zero variety beyond that one opp
- 100% degraded evaluations (freshness threshold mismatch)
- Filter pipeline operating correctly but on a degenerate signal

User asked to cut to 1 day in parallel with two other asks (cross-chain
expansion + same-opp confirmation). 7 days of the same persistent arb
data does not produce more research value than 1 day; storage + wall
time were the only differentiators.

## Options considered

1. **Keep 7-day run**
   Pros: more wall time → more chances for transient opps to land.
   Cons: 7 days of duplicate MSUSD/USDC emissions = ~30 GB JSONL with same single arb.

2. **Cut to 1 day** ← **CHOSEN**
   Pros: faster turnaround, smaller storage, sufficient for variety check.
   Cons: smaller sample size for H1's per-day rate confidence interval.

3. **Cut to specific UTC-day boundaries** (00:00–23:59 UTC)
   Pros: clean daily-rollup alignment.
   Cons: requires waiting for next UTC midnight to start, delays research. Not worth it.

## Decision

Set Dockerfile CMD to `--minutes 1440`. Run terminates ~24h after container
start. Daily rollup will produce a partial-day CSV (run spans two UTC days)
which is fine for analysis.

## Rationale

- The persistent-arb data is sufficient signal; 7 days would compound
  rather than diversify.
- Cross-chain (Phase 2) is a more valuable next investment than more
  Base-only data.
- Smaller dataset = faster analysis cycle = sooner Phase 2 spec writing.

## Consequences expected

- ✅ Run completes ~T+24h after the post-index-fix restart
- ✅ JSONL log: ~750 MB instead of ~5 GB
- ⚠️ H1 bootstrap CI will be wider (1 day vs 7 days of samples)
- ⚠️ Daily rollup spans 2 UTC days — the analysis script must handle this

## Reversal criteria

- 1-day data shows zero unique opportunities beyond MSUSD/USDC → extend to capture more transient events
- User wants the H1 CI tighter than 1-day-sample can provide → restart with longer window

## Links

- Memory file note: `C:\Users\jason\.claude\projects\C--Users-jason\memory\project_l3_trading_experiment.md` (1-DAY SCOPE REVISION section)
- Dockerfile: `CMD ["--minutes", "1440"]`
