# D-004: Defer cross-chain (Arb + Optimism) to Phase 2 spec

**Date**: 2026-05-13
**Made by**: user accepting agent's push-back
**Status**: ACTIVE

## Context

User asked to add Arbitrum and Optimism monitoring to find "chain hop
opportunities" — implicitly meaning cross-chain arbitrage.

Spec §"What NOT to build" explicitly lists this:
> No cross-chain monitoring. Base only. Arbitrum and Optimism are Phase 2+ if they happen.

The ask conflicts with:
1. Phase 1 invariant (single chain)
2. Architecture (PoolMonitor is single-chain; no token registry; no bridge cost model)
3. Active EXP-001 (in-flight Base-only run; schema migration mid-run breaks data validity)

## Options considered

1. **Add Arb + OP monitoring inline now**
   Pros: user's stated ask.
   Cons: ~2-3 days of work; breaks in-flight run; conflates two research questions (intra-chain persistence vs cross-chain arb); skips spec-then-build gate.

2. **Light-touch: add Arb + OP pool monitoring but no cross-chain detection logic**
   Pros: less work than full cross-chain.
   Cons: doesn't answer the user's actual question. We'd have 3× the data with no insight into "chain hop" because there's no bridge model.

3. **Defer to Phase 2 spec** ← **CHOSEN**
   Pros: structured. Forces explicit decisions on bridge model, token registry, schema migration. Doesn't break in-flight run.
   Cons: user's stated immediate ask deferred.

## Decision

Don't build cross-chain in this session. Complete EXP-001 first. After
1-day data lands, write Phase 2 spec covering:

- Multi-chain pool enumeration (DefiLlama supports it; need factory addresses for UniV3 + Velodrome (OP) + Camelot/TJ (Arb))
- 3× PoolMonitor instances or refactor for multi-chain
- **Bridge friction model** (Across vs LayerZero vs Stargate) — research input needed
- Cross-chain pool indexing (canonical token registry)
- Schema migration (chain field on PoolInfo / Opportunity / JSONL / rollup)
- Alchemy CU budgeting (~108M/month projected vs 300M free tier)

## Rationale

- Honors the spec's explicit "Phase 2+ if they happen" framing
- Preserves in-flight EXP-001 data validity
- Bridge model is a research framing decision the user must make; can't be coded around
- Cross-chain is genuinely 2-3 days of work; can't be a quick patch

## Consequences expected

- 1-day Base-only run completes uninterrupted
- After completion, agent writes Phase 2 spec for user review
- Phase 2 implementation only begins after spec approval
- Total elapsed time: 1 day for current run + ~1 day for spec review + 2-3 days for build = ~5 days to cross-chain detection. User informed.

## Reversal criteria

- User decides cross-chain detection is high-enough priority to abandon EXP-001 → reverse, stop run, start fresh
- 1-day data conclusively shows Base-only opportunity rate is so high we don't need cross-chain → reverse intent (no Phase 2 needed)
- Phase 2 spec turns out to be unbuildable for some reason → reverse and pick a different next step

## Links

- Unknown: `../unknowns/UNKNOWNS.md` UNK-007 (bridge model)
- Spec: `Desktop/Trading/docs/archive/LAYER3_TRADING_EXPERIMENT.md` §"What NOT to build"
