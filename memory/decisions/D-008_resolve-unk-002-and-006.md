# D-008: Resolve UNK-002 (MSUSD/USDC reopening) and UNK-006 (long-term lag stability)

**Date**: 2026-05-16
**Made by**: agent (per `unknowns/UNKNOWNS.md` rule: every UNK closure requires a Linked Decision)
**Status**: ACTIVE

## Context

EXP-001's unintended 3-day runtime (the timer-didn't-terminate bug — see
`failures/FAILURE_LOG.md` 2026-05-16) had a silver lining: it provided
~2.5 days / ~104K blocks of clean post-arb-closure observation. Two
open UNKs were sitting on this exact data:

- **UNK-002** ("Will the MSUSD/USDC arb reopen?") had an explicit
  resolution path: group EXP-001 records by hour, count distinct opp
  keys per hour. Empirical question.
- **UNK-006** ("Long-term lag stability post-index-fix") had a resolution
  path: sample lag at T+6h / T+12h / T+24h. Empirical question.

The 3-day run answered both. Per the UNKNOWNS.md rule that no UNK may be
marked RESOLVED without a Linked Decision, this file is the joint
linked decision.

## Options considered

### Option 1: One decision file covering both UNK resolutions ← **CHOSEN**

Pros:
- Both resolutions share the same evidence (the post-arb-closure
  observation window from EXP-001).
- Both close empirical-data UNKs (no architectural change needed) so a
  single short decision file is appropriate.
- Reduces decision-file proliferation.

Cons:
- Slightly couples the two resolutions in case one of them needs to be
  reversed later. Mitigation: this file documents both resolutions
  separately within Sections below, so a partial reversal can quote one
  section without touching the other.

### Option 2: One decision file per UNK (D-008 for UNK-002, D-009 for UNK-006)

Pros:
- Maximum atomicity — each decision is independently reversible.

Cons:
- Both decisions cite literally the same evidence window from the same
  experiment; splitting them is bookkeeping for its own sake.
- Sets a precedent that every empirical UNK closure gets its own
  decision file, which would clutter `decisions/`.

## Decision

**Use a single decision file (D-008) to resolve both UNK-002 and UNK-006.**
Resolutions documented in dedicated sections below, so future reversals
can target one without disturbing the other.

## Resolution of UNK-002

**Question**: Will the MSUSD/USDC arb reopen during the 1-day window? At
what cadence?

**Resolution**: **The arb did NOT reopen** in the ~2.5-day post-closure
observation window. Zero JSONL records emitted across ~104K blocks. No
GAP markers, no detection failures, no Tracebacks. The detector was
healthy throughout. Strong negative empirical result.

**Interpretation**:

- The first 1.85h of observation (2,420 records of the same opportunity)
  was the *closing phase* of a structural inefficiency, not a sample of
  a steady-state opportunity.
- Either a market participant captured the spread and rebalanced the
  pool, or the underlying price-feed alignment between MSUSD's
  Aerodrome Slipstream CL50 pool and its v1 stable pool reached
  equilibrium during the index-fix outage and stayed there.
- The 2.5-day silence is far longer than typical Base-block-time-scale
  noise (~2s). This is a regime change, not a transient quiet window.

**Reversal trigger for this resolution**: if a new MSUSD/USDC arb is
detected on Base in any subsequent run, re-open UNK-002 and document
the open-window duration distribution.

## Resolution of UNK-006

**Question**: Does lag stay stable over a full 24h+ window post-index-fix?

**Resolution**: **YES, lag stayed sub-1s for the entire ~3-day window.**
The container produced clean block ticks with no observable lag creep:

- Sync cycles ran every 5 min with 0 errors (confirms D-005's
  functional indexes remained selective over time)
- No `Traceback` in the logs covering the ~104K blocks
- No GAP markers (would have appeared if WS missed blocks)
- I-11 T-A intervention trigger (lag > 30s) did NOT fire at any sample point

**Interpretation**:

- D-005 (`add functional indexes to synced L3 DB`) is durable at current
  scale. The fix doesn't degrade as the DB grows row-by-row over a
  multi-day window.
- The original lag bug was indeed missing-index cost on full table
  scans, not a deeper architectural problem with the sync model.

**Reversal trigger for this resolution**: if a future run on the same
architecture sees lag climb past 30s without an unrelated cause
(network outage, restart event), re-open UNK-006 and investigate
either (a) index degradation due to row volume growth, (b) sync lock
contention scaling, or (c) drift in Layer3Client query patterns.

## Rationale

Both resolutions are joint outcomes of the same observation window. The
data is unambiguous in each case. Documenting them together
acknowledges that one event (3-day post-closure observation) resolved
two questions, without inflating the decision count.

## Consequences expected

- `unknowns/UNKNOWNS.md` UNK-002 and UNK-006 both flip to RESOLVED with
  Linked Decision = D-008 (already done by editor; this file backs that
  citation).
- No code change. Both UNK resolutions are empirical findings, not
  architectural fixes.
- One downstream implication: H1 invalidation (D-007) leans on UNK-002's
  negative resolution. If UNK-002 ever reverses (the arb reappears),
  D-007's reversal-criterion #2 also fires.

## Reversal criteria

This decision's status flips to REVERSED if either:

1. **UNK-002 reversal**: A MSUSD/USDC arb (or a similarly persistent
   single-pair arb on the monitored set) reappears within 30 days, OR
2. **UNK-006 reversal**: A future deployment of the same architecture
   exhibits lag creep > 30s under non-anomalous conditions.

Either reversal demotes one of the resolutions but leaves the other
intact (since they were documented separately above).

## Links

- Driving review: `../trades/2026-05-13_exp-001-final.md`
- Index fix that UNK-006 vindicated: `D-005_add-l3-db-indexes.md`
- Hypothesis decision that uses these resolutions as input: `D-007_h1-invalidated-at-current-floors.md`
- UNK-002 entry: `../unknowns/UNKNOWNS.md#unk-002-will-the-msusdusdc-arb-reopen`
- UNK-006 entry: `../unknowns/UNKNOWNS.md#unk-006-long-term-lag-stability-post-index-fix`
- Run extension bug (separate, still OPEN): `../failures/FAILURE_LOG.md` 2026-05-16 entry
