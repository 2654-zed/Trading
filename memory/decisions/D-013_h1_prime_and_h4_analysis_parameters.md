# D-013: H1' + H4 analysis parameters locked

**Date**: 2026-05-16
**Made by**: agent (per `PHASE_2_CROSS_CHAIN_SPEC.md` sub-phase 2.7 boundary)
**Status**: ACTIVE

## Context

Sub-phase 2.7 lands two new analysis modules — `analysis/h1_prime.py` and
`analysis/h4_pareto.py` — and extends `generate_report.py` to render
their sections. Both modules need concrete threshold values to decide
"supported / falsified" verdicts on Phase 2 data. This decision records
those values so the run-time analysis output and Jason's interpretation
share the same contract.

## Options considered

For each parameter, the options reduce to "the value documented in
`STRATEGY_STATE.md` for H1' and H4" vs. "an alternative numeric value".
The strategy spec drove the values; this decision just ratifies them
into code so the analysis module isn't free to drift.

## Decision

The following analysis constants are locked:

| Parameter | Value | Source |
|---|---|---|
| **H1' threshold (support)** | ≥ 50 unique cross-chain opps/day | STRATEGY_STATE.md H1' |
| **H1' falsification** | < 10 unique opps/day after 7 days | STRATEGY_STATE.md H1' |
| **Cross-chain margin floor** | 50 bps (0.50%) post-haircut | PHASE_2_CROSS_CHAIN_SPEC.md sub-phase 2.3 |
| **H1' counting modes** | emissions / unique / episodes | mirror Phase 1's H1 module |
| **Bootstrap CI** | 95%, seed 42, 1000 resamples | reused from H1 for symmetry |
| **H4 top-fraction** | 20% of buckets | STRATEGY_STATE.md H4 |
| **H4 support threshold** | top-20% covers ≥ 80% of events | STRATEGY_STATE.md H4 |
| **H4 falsification** | top-20% covers < 40% (with ≥ 5 buckets observed) | STRATEGY_STATE.md H4 |
| **H4 bucket key** | (src_chain, dst_chain, borrow_token_symbol) | inferred from H4's "chain × token matrix" wording |
| **H4 event unit** | unique (day, bucket) pair | per-day-dedup mirror of H1's `unique` mode |

## Rationale

- Every value comes directly from the strategy spec; no novel choice
  introduced in code beyond the bucket-key shape, which is the smallest
  observable for "which cross-chain route did this opportunity use."
- Mirroring H1's three counting modes (emissions / unique / episodes)
  in H1' keeps analysis-side reasoning consistent across hypotheses.
- The bootstrap seed and resample count are reused for byte-deterministic
  Markdown output across reruns.
- The H4 falsification floor (40%) leaves a meaningful gap between
  "uniform-ish distribution (~20% coverage)" and "strong concentration
  (≥80% coverage)" — a top-20%-covers-40% result would be ambiguous, so
  the falsification threshold is set ABOVE that ambiguous zone.

## Consequences expected

- Phase 2 dry-run output produces H1' + H4 verdicts directly readable
  from the rendered Markdown report
- Phase 1 JSONL replay through the new analysis still produces a Phase 1-
  shaped report (no H1'/H4 sections) because `h1_prime.compute(records)`
  returns zero-stats for v1 records — verified: EXP-001's 2,420 records
  produce 0 cross-chain opportunities, 0 buckets, report renders as
  Phase 1
- Jason's interpretation prompts in the Markdown sections explicitly
  reference D-006's fee model (for H1') and the bridge-liquidity profile
  (for H4) so the qualitative review touches the right pieces

## Reversal criteria

- **H1' threshold is wrong as scaled** — if Phase 2 produces ~30 cross-
  chain opps/day across 7 days, falsification at <10 may be too
  permissive. Reverse by re-spec'ing H1' threshold + falsification in
  STRATEGY_STATE.md + this decision.
- **H4 bucket key is too coarse** — if H4 is supported only because the
  USDC bucket dominates everything, refine to (src, dst, borrow_token,
  mid_token) for follow-up analysis. Codepath supports the refinement
  trivially.
- **Bootstrap CI breaks on Phase 2 data** — e.g. heavy-tailed
  distributions where the bootstrap mean is unstable. Mitigation: switch
  to percentile-of-day-counts rather than mean-of-day-counts. Doesn't
  reverse this decision, just the CI methodology.

## Consequences for the memory system

- `decisions/README.md` active table — add D-013 row
- No UNK changes
- `STRATEGY_STATE.md` — no change (already documents H1' / H4)
- `INVARIANTS.md` — no change

## Links

- Spec sub-phase: `../../docs/archive/PHASE_2_CROSS_CHAIN_SPEC.md` § sub-phase 2.7
- H1' module: `../../layer3_trading_exp/analysis/h1_prime.py`
- H4 module: `../../layer3_trading_exp/analysis/h4_pareto.py`
- Report extension: `../../layer3_trading_exp/analysis/generate_report.py`
- Phase 1 replay confirmation: 2,420 EXP-001 records → 0 cross-chain
  opps under H1' (correct); report renders as Phase 1 shape
