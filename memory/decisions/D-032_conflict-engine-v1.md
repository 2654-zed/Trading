# D-032: Conflict engine v1 (I-18 compliance)

**Date**: 2026-05-28
**Made by**: agent (per D-020 pre-staged + user "approved on all")
**Status**: ACTIVE

## Context

Sub-phase 3.3 / blueprint § 5.3 / invariant I-18: when lenses produce contradicting signals, that contradiction is itself alpha ("conflicts = alpha"). It must be surfaced as a first-class `ConflictSignal` through the bus → orchestrator path, never silently resolved. `engine/orchestrator/conflict_engine.py`.

## Decision

**Detect conflicts via semantic-tag opposition.** Each `(lens, signal_type)` maps to a semantic tag (the stance it implies). When two OPPOSED tags from DIFFERENT lenses co-occur on the same entity/window → emit a `ConflictSignal`.

Tag map (`_semantic_tag`):
- graph/cluster_detected, centrality_spike → `coordinated`; subgraph_anomaly → `adversarial`
- stochastic/volatility_regime_shift → `volatile`; drift_change → `trending`; diffusion_anomaly → `chaotic` (super_diffusive) or `stable` (mean_reverting)
- information/entropy_drop → `ordering`; regime_surprise, divergence_spike → `shifting`

Opposed pairs → conflict type (`CONFLICT_PAIRS`):
- {coordinated, stable} → `hidden_coordination` (coordination without volatility = concealed)
- {ordering, chaotic} → `order_chaos_conflict`
- {adversarial, stable} → `concealed_adversary`
- {coordinated, shifting} → `coordinated_regime_shift`

Requires the contradiction to span ≥2 distinct lenses (a single lens contradicting itself isn't cross-lens conflict). `ConflictSignal` rides the bus on `conflict.window`; NOT a `Signal`.

Per I-18, conflicts get a dedicated outcome-ledger table keyed by (window_start, lenses_involved, contradicting_types) — schema in `engine/feedback/outcome_ledger.py` (design-only in 3.3, active in 3.4).

## Acceptance evidence

200-window replay: **194 ConflictSignals emitted, 194 logged**. Rate = 194 / 7,938 signals = **24.4 per 1,000** — far above the spec's "≥1 per 1,000" positive-rate bar. Unit tests verify hidden_coordination fires on graph-coordination + stochastic-stable cross-lens co-occurrence, and does NOT fire when both opposed tags come from a single lens.

## Reversal triggers

- Conflict rate too high (24/1000 may be noisy) — tighten by requiring entity-level (not window-level) co-occurrence, or add a strength floor. Calibration deferred (spec says "calibration TBD").
- H7 (conflicts differentiate outcomes) falsifies in 3.4/3.5 → reconsider the tag map or retire low-yield conflict types.

## Links

- I-18 (conflicts first-class) — this engine implements it
- H7 (conflicts are alpha) — to be tested in 3.4/3.5 against outcomes
- D-033 (orchestrator applies non-linear conflict boost per I-17)
- Code: `engine/orchestrator/conflict_engine.py`, `engine/feedback/outcome_ledger.py`
- Tests: `engine/tests/test_replay_and_engines.py`
