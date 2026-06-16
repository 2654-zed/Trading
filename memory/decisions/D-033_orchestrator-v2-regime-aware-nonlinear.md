# D-033: Orchestrator v2 — regime-aware + non-linear (I-17 compliance)

**Date**: 2026-05-28
**Made by**: agent (per D-020 pre-staged + user "approved on all")
**Status**: ACTIVE

## Context

Sub-phase 3.3 evolves the 3.2 static-weights skeleton (D-027) into an adjudicating engine that consumes regime labels + conflicts and satisfies I-17 (the aggregation may not be a simple/weighted average alone).

## Decision

`engine/orchestrator/orchestrator.py` v2:

### Inputs (three bus topics)
- `signal.*` — lens signals (per-lens max strength + max confidence per window)
- `regime.window` — RegimeLabel → selects the weight table
- `conflict.window` — ConflictSignal → applies the non-linear boost

### Regime-aware weight routing
`regime_weights.py` defines a table per regime (trend/chaotic/low_liquidity/adversarial/exploit_risk/default). The orchestrator routes each window to its regime's table. E.g. adversarial emphasizes graph (0.40); chaotic emphasizes stochastic (0.40). Falls back to default when no label / `regime_aware=False`.

### Aggregation (satisfies I-17 two independent ways)
```
base  = Σ_lens  weight[lens] · max_strength[lens] · max_confidence[lens]   # BILINEAR
score = base · (1 + CONFLICT_BOOST · mean_conflict_strength)                # NON-LINEAR
```
- Bilinear in strength × confidence → satisfies I-17's "at least bilinear" clause.
- Conflict boost (conflicts = alpha, so they raise the score) → satisfies I-17's "weighted-average combined with a non-linear conflict adjustment" clause.
- `CONFLICT_BOOST = 0.5`; score capped at 1.0.

### Cross-topic join
Windows are emitted with a **2-window event-time lag** so the RegimeLabel + ConflictSignals for a window (emitted by sibling engines on the same watermark) arrive before the orchestrator aggregates it. `WindowAggregate` extended with `regime_label`, `regime_confidence`, `base_score`, `conflict_count`, `conflict_boost`.

## Acceptance evidence

200-window replay: **200 aggregates emitted, 200 logged**, scores vary (min 0.045, max 0.832, 166 distinct). Unit tests prove:
- aggregation is bilinear (strength×confidence), not w·strength alone
- the same signals WITH a conflict produce a strictly higher score than WITHOUT (non-linear) — and identical signals in conflict vs agreement produce different scores (direct I-17 check)
- regime routing selects the correct weight table (adversarial → graph 0.40)

Latency: N/A in replay (simulated timestamps); the p95<500ms target was validated in 3.2 static mode (16ms).

## Reversal triggers

- CONFLICT_BOOST = 0.5 is a guess; recalibrate once outcomes exist (3.4/3.5) and H7 is tested.
- 2-window lag drops the final 1-2 windows' regime/conflict join under tight shutdown — acceptable for smoke; revisit if it matters for live (3.4+).

## Links

- I-17 (no simple average) — satisfied here
- D-027 (orchestrator v1 skeleton this supersedes)
- D-031 (regime labels), D-032 (conflict signals) — the inputs
- D-029 (temporal replay — supplies time-varying input)
- Code: `engine/orchestrator/orchestrator.py`, `engine/orchestrator/regime_weights.py`
- Tests: `engine/tests/test_orchestrator.py`, `engine/tests/test_replay_and_engines.py`
