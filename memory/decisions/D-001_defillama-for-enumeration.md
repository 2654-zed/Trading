# D-001: Use DefiLlama instead of factory event scan for Phase 1.1 pool enumeration

**Date**: 2026-05-06
**Made by**: agent (with user approval)
**Status**: ACTIVE

## Context

Spec §Phase 1.1 instructed: enumerate UniV3 + Aerodrome pools by scanning
each factory's `PoolCreated` events from deploy block to head, then filter
by TVL.

First attempt against Base UniV3 surfaced **1.86M `PoolCreated` events**
(mostly mid-2024 meme-coin frenzy). Initial scan got to 63% in ~1 hour
before being killed; even with checkpointing + multicall3 batching,
projected wall time was 60-120 minutes for scan alone. Then the post-decode
TVL filter would have required ~3.7M `balanceOf` RPC calls (~100 hours
without multicall batching).

## Options considered

1. **Spec'd path: factory event scan + per-pool `balanceOf` TVL filter**
   Pros: matches spec literally, no third-party deps.
   Cons: 60-120h wall time; massive Alchemy CU usage; brittle to restarts;
   most events are dead meme pools that get filtered out anyway.

2. **CoinGecko prices + on-chain balances**
   Pros: still on-chain TVL, just outsource pricing.
   Cons: free-tier CoinGecko rate limits; 1.86M events × per-token price
   lookups = hours of rate-limited queries; not viable.

3. **Uniswap subgraph (The Graph)**
   Pros: pools with TVL already indexed; one GraphQL query.
   Cons: Aerodrome's subgraph quality unclear; adds GraphQL client; the
   subgraph is also a third-party dep, just a different one.

4. **DefiLlama `/pools` endpoint** ← **CHOSEN**
   Pros: one HTTP GET returns all DEX pools above any TVL floor across
   all chains. DefiLlama already tracks Aerodrome variants and UniV3.
   We hit `getPool(...)` on-chain per entry to verify and resolve pool
   address. ~33 seconds total wall time.
   Cons: third-party data source; their TVL is their model, not ours
   (we lose ground-truth on TVL math).

## Decision

Use DefiLlama's `/pools` endpoint as the pool-candidate source. Verify
each candidate's on-chain pool address via the appropriate factory's
`getPool(...)`. Use DefiLlama's reported `tvlUsd` as the floor metric.

## Rationale

- Reduces enumeration from 60+ hours to ~30 seconds.
- DefiLlama's data is already the canonical source for TVL across DeFi.
- We still verify on-chain pool addresses, so DefiLlama's role is bounded
  to "candidate discovery"; we never trust it for state.
- The spec's "separate Alchemy budget" intent is preserved (less Alchemy
  usage, not more).

## Consequences expected

- ✅ Phase 1.1 acceptance run completes in seconds, not hours
- ✅ Alchemy CU usage drops dramatically
- ⚠️ TVL filter is DefiLlama's calculation, not ours — minor data quality risk
- ⚠️ DefiLlama outage during enumeration would block a fresh run start (mitigation: don't re-enumerate during a run; cache the pool set)

## Reversal criteria

- DefiLlama systematically miscounts or misses pools we'd otherwise catch
- DefiLlama's terms change to require API key with rate limits we can't accommodate
- We need ground-truth TVL math (e.g. for Phase 2 execution sizing) and DefiLlama's numbers aren't accurate enough

## Links

- Documented in `Desktop/Trading/docs/archive/PHASE_1_1_ADDENDUM.md`
- Resolved (then-extant) UNK on enumeration approach
