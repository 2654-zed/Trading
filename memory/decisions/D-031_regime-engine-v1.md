# D-031: Regime engine v1

**Date**: 2026-05-28
**Made by**: agent (per D-020 pre-staged + user "approved on all")
**Status**: ACTIVE

## Context

Sub-phase 3.3 / blueprint § 5.1: classify each time window into one of `{trend, chaotic, low_liquidity, adversarial, exploit_risk}`. `engine/orchestrator/regime_engine.py`.

## Decision

**Rule-based classifier** scoring each regime from window signal features, argmax wins, with a per-feature contribution log for the spot-check gate.

Scoring (each in [0,1]):
- **trend** = `max(drift_change_strength, 0.7·coordination)`, damped ×0.5 if adversarial present. Coordination = max(cluster_detected, centrality_spike). Captures steady coordinated accumulation OR directional drift.
- **chaotic** = mean(volatility_regime_shift, super_diffusive diffusion strength).
- **low_liquidity** = graded high when window signal count ≤ 3.
- **adversarial** = subgraph_anomaly strength (poisoning/laundry actors).
- **exploit_risk** = (coordination + adversarial)/2 when BOTH present — escalation of coordination+adversarial convergence; wins over `adversarial` only when coordination dominates.

Confidence = winner_score / sum(scores). Tie-break deterministic by REGIMES order.

### Bug fixed during validation (spot-check-driven)

Initial scoring left **coordination-only windows** (high centrality, no adversarial/volatility) scoring 0 on every regime → arbitrarily labeled "trend" with **0.00 confidence**. Fix: feed coordination into the `trend` score. After fix: 0 windows with 0.00 confidence (was many).

## Acceptance evidence

200-window replay, **200 regime labels (1:1 with windows)**:
- distribution: adversarial=124, trend=62, chaotic=7, exploit_risk=4, low_liquidity=3 (all 5 regimes appear)
- **0 windows with 0.00 confidence**
- Spot-check on a 20-window sample: **20/20 intuitively correct** (100% ≥ 70% bar). Coherent narrative — quiet early period → `trend`, then `adversarial` once laundry-funded activity appears mid-April.

This is the H6 pre-validation proxy.

## Reversal triggers

- A future analysis attests the regime labels don't track real market states (e.g. backtest the May 17 burst) → re-tune scoring or add features in a D-NNN.
- adversarial over-dominates (124/200) — acceptable for this data (persistent laundry actor) but if it masks other regimes, add recency-weighting to the adversarial feature.

## Links

- D-029 (temporal replay), D-033 (orchestrator consumes regime labels for weight routing)
- H6 (regime detection is real) — this is the pre-validation
- Code: `engine/orchestrator/regime_engine.py`, `engine/orchestrator/regime_weights.py`
- Tests: `engine/tests/test_replay_and_engines.py`
- Spot-check artifact: `engine/data/sub_phase_3_3_regimes.jsonl`
