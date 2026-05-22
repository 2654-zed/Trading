# D-006: Bridge model for Phase 2 cross-chain arb = Across Protocol

**Date**: 2026-05-13
**Made by**: user (in response to UNK-007 deadline check during cross-chain status query)
**Status**: ACTIVE

## Context

Phase 2 cross-chain arbitrage detection was blocked on UNK-007 — the
question of which bridge model to assume for "what does a cross-chain
arbitrage opportunity even mean." Three candidates were on the table
(see `../unknowns/UNKNOWNS.md` UNK-007 prior text):

- **Across Protocol** — optimistic relayer-filled bridge, ~30s typical L2-to-L2 latency, dynamic 0.05-0.2% fee (LP + relayer)
- **LayerZero / Stargate** — message-passing-based, ~1-5 min latency, 0.1-0.3% fee
- **Canonical L2 bridges** — 7-day finality on optimistic rollups; structurally unusable for arb

The bridge choice fundamentally defines the "opportunity" — under canonical
bridges, no opportunities exist; under instant-bridge assumption, every
cross-chain spread is one. Without picking a model, the Phase 2 spec
couldn't be written.

User asked status of cross-chain implementation; the memory system surfaced
this as the only unknown they could unblock without waiting for EXP-001
to finish. User picked Across.

## Options considered

(Same as UNK-007 candidates.)

1. **Across Protocol** ← **CHOSEN**
   Pros: fastest realistic latency (~30s), real LP-backed liquidity, supports
   Base + Arbitrum + Optimism (our exact triangle), public rate quoting
   available, used by real production cross-chain dApps.
   Cons: dynamic fees mean we model a range, not a point estimate.

2. **LayerZero / Stargate**
   Pros: broader token coverage, more established ecosystem.
   Cons: slower (1-5 min adds price-drift risk), higher fees.

3. **Canonical bridges**
   Pros: most secure, no third-party trust.
   Cons: 7-day finality kills any arb research; not viable.

## Decision

Use **Across Protocol** as the Phase 2 bridge model with these initial
parameters:

| Parameter | Value | Source / rationale |
|---|---|---|
| Latency per bridge hop | 30 seconds | Across published median for L2↔L2 fills |
| Fee per bridge hop | 10 bps (0.10%) as point estimate | Middle of published 0.05-0.2% range |
| Fee sensitivity range | 5-20 bps | For sensitivity analysis in H1/H3 reporting |
| Supported tokens (initial) | USDC, WETH, USDT, DAI, cbBTC | Across's well-covered set; matches our most common pool tokens |
| Supported chains | Base, Arbitrum, Optimism | Exact match for our Phase 2 expansion |
| Trade structure modeled | Sequential bridge-then-trade (not atomic) | No flash loan provider supports atomic cross-chain; sequential is what executors actually face |
| Round-trip vs one-way | **DEFERRED to Phase 2 spec** | Both have research value: round-trip = pure arb, one-way = rebalance trade. Phase 2 spec must pick one or both. |

## Rationale

- Across is the bridge that real cross-chain arb traders actually use on
  the Base/Arb/OP triangle. Modeling against it produces results that map
  to real execution-mode opportunities.
- 30-second latency is the load-bearing constraint — it sets the maximum
  acceptable price drift between detection and bridge finality. Any
  opportunity with spread < 30s of expected price-drift is unrealistic.
- 10 bps as point estimate keeps margin math clean for an initial pass;
  sensitivity analysis covers the rest of the realistic range.
- The chains Across supports happen to be exactly the chains we want to
  monitor — no token-availability research needed.

## Consequences expected

- **Unblocks UNK-007** (this decision is its Linked Decision)
- **Enables Phase 2 spec drafting** — once EXP-001 completes and analysis lands, agent can write the Phase 2 spec without further user input on bridge model
- **Tightens the cross-chain opportunity definition**: any cross-chain "opportunity" must clear `gross_spread - 2 × bridge_fee - swap_fee_per_leg - bridge_latency_risk > 0.3%` (research floor, same as Phase 1)
- **Still does NOT trigger build** — Phase 2 spec must be drafted, reviewed, and approved before any code; same workflow as Phase 1 sub-phases

## Reversal criteria

- Across has a major outage / incident during the Phase 2 build window that calls its 30s latency model into question
- Real execution-mode data shows Across fills taking 2+ minutes typically (model is wrong)
- A different bridge produces materially different opportunity counts that we want to compare against (would lead to multi-bridge model rather than reversal)
- User changes mind based on new information about Across (e.g. fee model changes structurally)

## Consequences for the memory system

- `unknowns/UNKNOWNS.md` UNK-007 must be marked RESOLVED with Linked Decision = D-006
- `STRATEGY_STATE.md` EXP-002 entry should note this unblock (but stay DEFERRED — EXP-001 still in flight)
- Phase 2 spec, when drafted, will reference this decision as its bridge-model premise

## Links

- Unknown resolved: `../unknowns/UNKNOWNS.md` UNK-007
- Strategy entry: `../STRATEGY_STATE.md` EXP-002 (Phase 2 cross-chain)
- Deferred parent decision: D-004 (defer cross-chain to Phase 2)
- Across docs: https://docs.across.to/
