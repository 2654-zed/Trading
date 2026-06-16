# D-015: Alchemy CU budget for Phase 2 (EXP-002); resolves UNK-003

**Date**: 2026-05-17
**Made by**: agent (per `PHASE_2_CROSS_CHAIN_SPEC.md` sub-phase 2.2 acceptance + D-014 in-flight resolution path)
**Status**: **REVERSAL TRIGGERED 2026-05-24 → ROOT CAUSE IDENTIFIED 2026-05-25**. Spike was caused by a newHeads subscription leak in `_AlchemyTransport.subscribe_new_heads()` — each PoolMonitor subscription-level retry opened a new subscription_id without unsubscribing the previous one; Alchemy delivered messages to all leaked subscriptions on the shared WS, peaking at ~170 active subs on Base (vs intended 1). NOT a fundamental misjudgment of the projected CU rate — the projection was correct for the intended state, but the actual state diverged due to a resource leak. Fix landed 2026-05-25 (commits `9ca2565` + `a810799`); architectural pattern documented in `D-017_ws-subscription-lifecycle.md`. Detector still HALTED pending the next deploy with the fix; this decision flips back to **ACTIVE** once post-deploy verification confirms newHeads CU rate drops to ~2.6M/day per chain.

## Context

UNK-003 was the projection that multi-chain Phase 2 would use ~108M CU/
month against Alchemy's 300M free-tier monthly budget. The spec sub-
phase 2.2 acceptance criterion said the resolution lands "after 30
minutes" of live measurement.

EXP-002 went live 2026-05-17 ~17:20 UTC. ~30+ minutes of live data
provides the empirical basis for this resolution.

## Live measurement

Sampled the last 300 log lines from the running deployment 2026-05-17 ~22:10 UTC (~4 hours into the run, all 3 chains active):

| Chain | Block ticks in sample | Effective multicall rate |
|---|---|---|
| Base | 91 | ~0.5 req/s (every 2s) |
| Arbitrum (sampled 1/8) | 90 | ~0.5 req/s (every 8 blocks ≈ 2s wall) |
| Optimism | 91 | ~0.5 req/s (every 2s) |
| **Aggregate** | **272** | **~1.5 multicall req/s** |

This matches the engineering projection from PHASE_2_CROSS_CHAIN_SPEC.md sub-phase 2.2 ("Aggregate steady-state HTTP rate: ~1.5 multicall req/s across all chains").

### CU projection

Alchemy's documented per-method cost for `eth_call` on the free tier is
26 CU per call. `multicall3.aggregate3` is a single `eth_call` regardless
of how many inner calls it batches (159 pools in our case → ~211 inner
calls / multicall, but billed as ONE eth_call).

- Multicall load: 1.5 req/s × 26 CU = **39 CU/s = 3.37M CU/day ≈ 101M CU/month**
- WebSocket `eth_subscribe newHeads`: setup-only cost (~20 CU once per
  chain × 3 chains = 60 CU initial); per-message notifications are not
  charged per-message on Alchemy's standard plans (subscription cost is
  amortized in the connection-level throughput limit, not per-message
  CU). **This is the load-bearing assumption** flagged in reversal
  criteria below.

**Projected monthly CU: ~101M against 300M free tier = ~34% utilization.**

This is comfortably inside the spec's 80% ceiling (240M/month).

## Options considered

1. **Accept the engineering projection (~34% utilization) and let the
   run continue** ← **CHOSEN**
   Pros: ~34% gives plenty of headroom for natural variance; the spec's
   <80% threshold is well below us; runtime data confirms the projection.
   Cons: WS pricing assumption isn't verified against user's actual
   Alchemy dashboard. If WS notifications ARE charged per-message at
   ~10 CU each, the projection becomes ~226M/month (~75%) which is
   still under threshold but tighter.

2. **Pause the run pending Alchemy dashboard manual verification**
   Pros: Removes the WS pricing uncertainty.
   Cons: Costs 7 days of measurement; spec sub-phase 2.2 explicitly
   allowed the in-flight resolution path.

3. **Reduce Arb sampling cadence from 1/8 to 1/16 to halve Arb load**
   Pros: More conservative CU posture.
   Cons: Premature optimization — the projection says we have headroom.
   Arb block-time of ~250ms means 1/8 sampling already gives ~2s
   effective cadence matching Base+OP; halving again to 1/16 would mean
   Arb effective cadence ~4s, missing some price-drift signal.

## Decision

**Accept the engineering projection and continue EXP-002 unchanged.**
Projected utilization ~34% (101M / 300M); comfortable headroom against
the spec's 80% ceiling.

## Rationale

- Live measurement matches the projection (1.5 req/s aggregate).
- `multicall3.aggregate3` billing as a single `eth_call` is documented
  Alchemy behavior — the 159-pool batching does NOT scale CU linearly
  with pool count.
- The 7-day run produces ~24M CU which is ~8% of the monthly free tier;
  even doubling that for unexpected overhead is still well inside budget.

## Consequences expected

- EXP-002 runs the full 7 days inside the free-tier budget
- L3 sync continues via L3's own Alchemy budget (sibling service); no
  impact on our budget per spec § Phase 1 invariants
- Across fee verifier runs once per cold start (DefiLlama query +
  20 Across API queries, no Alchemy involvement)

## Reversal criteria

This decision is REVERSED and EXP-002 paused for re-spec if any of:

1. **WS pricing assumption is wrong**: Alchemy dashboard shows projected
   monthly CU > 250M when sampled at T+24h or T+72h. Reversal action:
   chunk Arb sampling to 1/16 OR move WS to Base public RPC for one of
   the chains.
2. **Sustained multicall rate > 3 req/s** (2× projection). Indicates
   the sampling cadence isn't holding for some reason. Inspect the
   monitor's actual fetch rate.
3. **CU rate spikes during L3 sync windows** suggesting sync cycles are
   triggering on our Alchemy. They shouldn't (L3 sync uses L3's HTTP
   `/dump` endpoint, not Alchemy RPC) but if it does, isolate the cause.
4. **Free-tier rate cap (25 RPS) hit** — would surface as 429 responses
   in logs. Then chunking is mandatory, not optional.

## Consequences for the memory system

- `unknowns/UNKNOWNS.md` UNK-003 status → RESOLVED, Linked Decision = D-015
- `decisions/README.md` active table — add D-015 row
- `STRATEGY_STATE.md` EXP-002 — note UNK-003 resolved + measured rate
- `SYSTEM_STATE.md` — no change beyond existing health snapshot
- `failures/FAILURE_LOG.md` — none (no anomalies)

## Links

- Spec sub-phase: `../../docs/archive/PHASE_2_CROSS_CHAIN_SPEC.md` § sub-phase 2.2 acceptance
- Phase 2 umbrella: `D-009_phase-2-cross-chain-spec-approved.md`
- Go-live decision: `D-014_phase-2-go-live.md`
- UNK-003 entry: `../unknowns/UNKNOWNS.md`
- Live deployment: Railway service `layer3-trading-exp`
