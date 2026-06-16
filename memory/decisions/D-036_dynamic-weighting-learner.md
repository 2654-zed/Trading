# D-036: Dynamic weighting learner v1 (I-19)

**Date**: 2026-05-28
**Made by**: agent (per D-020 pre-staged + user "approved, split 3.4")
**Status**: ACTIVE

## Context

Sub-phase 3.4b makes the orchestrator's weighting DYNAMIC — the system learns which lenses earn their weight under which regime (I-19 "system must learn"). `engine/feedback/learner.py` + `engine/orchestrator/weighting_engine.py`.

## Decision

### Learner (`learner.py`)
Pulls attributed outcomes from the ledger and recomputes per-regime per-lens weights:
```
credit_{R,L} = Σ_t decay_t · strength_{L,t} · outcome_t        (over regime-R decisions)
learned_{R,L} = α · normalize(credit_{R,L}) + (1−α) · prior_{R,L}     (α = 0.6)
```
- `decay_t = 0.5^(age / half_life)` (half_life = 50 decisions) — recency-weighted.
- Lenses with no positive credit keep a damped prior; tables re-normalized to sum 1.0 and `validate_weights`-checked.
- Idempotent: each run recomputes from the full ledger. Persists to `learned_weights.json` with a `last_run_ts`.
- I-19 observability: `is_stale()` / `staleness_seconds()` — warn if > 7 days since last run.

### Weighting engine (`weighting_engine.py`)
Serves per-regime weights to the orchestrator: learned table when present AND fresh, else the static regime prior. If the learned table is stale (>7d) it falls back to static AND flags stale (don't trust stale learning). Orchestrator gained a `weighting_engine` param; when set, `_aggregate` routes regime → learned weights.

## Acceptance evidence (3.4b smoke, 200-window replay)

- Learner ran over 200 outcomes; per-regime decision counts {adversarial 123, trend 62, chaotic 6, exploit_risk 4, low_liquidity 3}.
- **Weights moved measurably** — L1 delta from static prior per regime: trend 0.84, low_liquidity 0.90, default 0.54, exploit_risk 0.48, adversarial 0.42, chaotic 0.38.
- **Persist across restarts**: written to JSON, reloaded by WeightingEngine (`using_learned=True, stale=False`).
- **Closed-loop effect (the payoff)**: re-running the pipeline with learned weights changed the decision action mix from STATIC {hold 77, hedge 123, enter 0} → LEARNED {enter 40, hold 27, hedge 133}. The learner raised trend-regime lens weights enough that 40 windows cleared the `enter` threshold — the enter=0 baseline flagged at the 3.4a checkpoint moved, demonstrably driven by learning.
- Closed-loop integration test passes (`test_closed_loop_signal_to_decision_to_outcome_to_weight_update`).

## Caveats

- Weights learn against the D-034 PROXY outcome (forward flow/adversarial-event escalation), not realized P&L. Learned weights are only as meaningful as the proxy.
- The `enter=40` shift shows the loop *works*, not that those entries would be *profitable* — that needs Phase 4 price outcomes.

## Reversal triggers

- α=0.6 / half_life=50 are guesses; re-tune once Phase 4 real outcomes exist.
- If the proxy is shown degenerate (D-034 reversal), the learned weights are suspect — revert to static until a better outcome signal exists.

## Links

- I-19 (system learns) — implemented here
- D-034 (outcome proxy the learner consumes), D-035 (decisions feeding the ledger)
- D-033 (orchestrator the weights feed), D-038 (LOOP H8 verdict)
- Code: `engine/feedback/learner.py`, `engine/orchestrator/weighting_engine.py`, orchestrator `weighting_engine` param
- Tests: `engine/tests/test_learner_and_execution.py`
