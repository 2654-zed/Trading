# D-007: H1 INVALIDATED at current pool-floor settings; pivot next focus to Phase 2 spec drafting

**Date**: 2026-05-16
**Made by**: agent (per `loop/LOOP.md` Step 6 mandatory action for INVALIDATED hypothesis)
**Status**: ACTIVE

## Context

EXP-001 was terminated 2026-05-16 ~13:54 UTC after running ~3 days instead
of the planned ~24 hours (separate failure — see
`failures/FAILURE_LOG.md` 2026-05-16 entry on the `--minutes` timer bug).
The run produced one dataset with two structurally distinct windows:

- **Window A (2026-05-13 04:28 → 06:19 UTC, ~1.85h)**: 2,420 JSONL records,
  one unique opportunity key (the persistent MSUSD/USDC arb between
  Aerodrome Slipstream CL50 and Aerodrome v1 stable), margin pinned at 30
  bps, every record `layer3_stale=True`.
- **Window B (2026-05-13 06:19 UTC → 2026-05-16 13:54 UTC, ~2.5 days)**:
  ~104,000 blocks observed, **0** JSONL records, sub-second lag throughout,
  no errors, no GAP markers. Healthy pipeline producing silence.

H1 was stated in `STRATEGY_STATE.md` as: *"≥1,000 distinct arbitrage
opportunities/day exist on Base at ≥0.3% gross margin."* Falsification
criterion: *"<100 distinct opportunities/day after 7 days of observation."*

Observed: **1 distinct opportunity in ~3 days**, with strong negative
post-closure signal (no second opportunity in 104K blocks). Extrapolated
rate: ~0.33 unique opps/day. **This is ~0.033% of the H1 threshold.**

Per `loop/LOOP.md` Step 6 guidance:
> A hypothesis with a numeric threshold (e.g. H1: ≥1,000 opps/day) →
> INVALIDATED when extrapolated rate is <10% of threshold over a sample
> size that should have caught it.

Sample size check: 104K blocks at 2s/block ≈ 58 hours of zero-opp observation
on a 128-pool monitored set. The 95% confidence interval for the unique-opp
rate excludes any value ≥ ~10 opps/day with overwhelming confidence; the
H1 threshold of 1,000/day is excluded by ~100x. **The hypothesis is wrong
as stated, at the current monitored-set and margin-floor configuration.**

## Options considered

### Option 1: Mark H1 INVALIDATED, pivot next focus to Phase 2 spec drafting ← **CHOSEN**

Per LOOP Step 6: INVALIDATED triggers `STRATEGY_STATE.md` update + new
decision documenting the response. Phase 2 (cross-chain) was already
queued as the next phase, with the bridge model unblocked by D-006
(Across Protocol). The Across-supported chain triangle (Base + Arb + OP)
measures a fundamentally different opportunity surface that does not
depend on intra-Base price drift remaining open.

Pros:
- Honors LOOP discipline rather than defaulting back to "let it keep running"
- Phase 2 is already partially specified (bridge model decided)
- Cross-chain opportunities have a structurally different generating
  process (chain-relative price drift, bridge fee window) — the H1
  failure on Base does not predict similar failure on Base↔Arb↔OP
- User has consistently asked about cross-chain throughout this session

Cons:
- Phase 2 spec is not yet written — committing to drafting it now is a
  non-trivial work block
- We end Phase 1 with H1 falsified at current floors rather than
  characterizing the regime more thoroughly

### Option 2: Mark H1 WEAKENED only, lower the margin/TVL floors, re-run on Base

The H1 falsification text says "after 7 days" not "after 3 days." A
strict reading would treat this as WEAKENED-with-strong-prior, not yet
INVALIDATED.

Pros:
- More conservative epistemics — gives H1 the literal benefit of its
  written falsification criterion
- Lower floors might surface a transient-opportunity regime we missed
- Cheap to run (same infra, same code)

Cons:
- 104K blocks of silence at current floors is overwhelmingly informative
  even though it's "only" 3 days
- The data we'd get from lower floors answers a different question
  ("what's the opportunity rate when we monitor more pools / accept thinner
  spreads?") — that's not what H1 said
- Re-running consumes another day+ before any new Phase 2 work starts
- Lowering floors may produce spurious "opportunities" that wouldn't be
  profitably executable even in execution mode

### Option 3: Mark H1 WEAKENED, keep observing, defer all next-focus decisions

Pros:
- Maximum optionality
- Avoids committing to a phase pivot

Cons:
- Cargo-cult observation — we have the answer the data is willing to give
  us at current configuration
- Drains Alchemy CUs producing no new information
- Violates LOOP Step 6's whole purpose (forcing explicit belief updates)

## Decision

**Mark H1 INVALIDATED at the current pool-floor + margin-floor configuration.
Pivot next focus to drafting the Phase 2 cross-chain spec.**

Key qualifier: INVALIDATED is *as stated* — at the $500K UniV3+Slipstream,
$250K Aerodrome v1 floors, with the 30 bps margin floor, on a 128-pool
Base-only set. A re-formulated H1 with lower floors is a different
hypothesis and would require its own measurement run.

## Rationale

- LOOP Step 6 mandates INVALIDATED for <10% of threshold with adequate
  sample. We're at ~0.033% with 104K blocks. The discipline rule fires
  unambiguously.
- The post-closure 2.5-day silence is the most informative datum
  EXP-001 could have produced. Steady-state Base liquidity at our floors
  is, empirically, near-zero-opportunity. More wall time produces more
  silence, not more variety.
- D-006 already unblocked Phase 2 spec drafting on the bridge-model axis.
  The only remaining gates (UNK-003 Alchemy CU, UNK-008 JSONL size) can
  be resolved inside the spec draft itself.
- The user has consistently signaled interest in cross-chain; pivoting
  there is aligned with declared research intent, not a unilateral agent
  decision.

## Consequences expected

- `STRATEGY_STATE.md`: H1 moves from active table to a new "Invalidated
  hypotheses" section with date and link to this decision.
- H2 and H3 remain WEAKENED/structurally-untestable (depend on H1
  surfacing flagged vs unflagged samples to compare; H1 surfaced only
  one homogeneous sample). Their fate is now tied to whatever
  cross-chain run produces.
- Next focus = draft `phase_2_cross_chain_spec.md` covering: monitored
  pool sets per chain, Across bridge model integration (per D-006),
  token registry approach, schema migrations (chain field on PoolInfo
  / Opportunity / JSONL), round-trip vs one-way trade structure
  resolution, projected Alchemy CU budget against the 300M/month free
  tier (resolves UNK-003), JSONL size projection at 3-chain volume
  (resolves UNK-008).
- EXP-002 (Phase 2 run) replaces EXP-001 as the active experiment once
  the spec is approved.
- The Phase 1.6 deployment configuration is preserved as a reference
  artifact (volume, monitored_pools.json, indexed L3 DB) but is not
  redeployed without an explicit reactivation decision.

## Reversal criteria

This decision would be reversed if:

1. **Cross-chain Phase 2 also produces near-zero opportunities** at the
   chosen floors. Then the issue is our floor/methodology choice across
   the whole research program, not Base-specific liquidity efficiency.
   Action on reversal: re-formulate H1 with lower floors and re-run
   Phase 1 Base-only with the new floors before committing to Phase 2
   scope reduction.

2. **A new structural arb on Base appears within 24 hours** of this
   decision being filed (e.g. user notices something on a public arb
   tracker that our monitored set covers). Then the post-closure
   silence was a coincidence window, not the steady state. Action on
   reversal: extend EXP-001 by another 3-day window before declaring
   the matter closed.

3. **Phase 2 spec drafting reveals the bridge-model cross-chain
   methodology is structurally unsound** (e.g. all Across-supported
   chain-token combinations have negative expected margin after fees
   and bridge latency price-drift risk). Then continuing on Base with
   re-formulated floors becomes the only viable research direction.

## Consequences for the memory system

- Add H1 to a new "Invalidated hypotheses" section in `STRATEGY_STATE.md`
- Update EXP-001 entry in `STRATEGY_STATE.md` to TERMINATED status
- Promote EXP-002 to the active experiment slot in `STRATEGY_STATE.md`
- `decisions/README.md` active table — add row for D-007
- `loop/LOOP.md` history table — add the 2026-05-16 loop row showing H1=INVALIDATED
- `SYSTEM_STATE.md` health snapshot — mark deployment removed, EXP-001 terminated

## Links

- Review file driving this: `../trades/2026-05-13_exp-001-final.md`
- Bridge model unblocking Phase 2: `D-006_bridge-model-across.md`
- Deferred cross-chain parent: `D-004_defer-cross-chain.md`
- Unknowns now resolved by the same data: `D-008_resolve-unk-002-and-006.md`
- Run-extension bug (separate matter): `../failures/FAILURE_LOG.md` 2026-05-16 entry
- LOOP discipline rule that fired: `../loop/LOOP.md` Step 6 INVALIDATED row
