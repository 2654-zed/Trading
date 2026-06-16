# D-034: Outcome attribution proxy ($0-CU forward-looking backtest)

**Date**: 2026-05-28
**Made by**: user ("approved" — forward flow + adversarial-L3-event proxy, 2-window horizon)
**Status**: ACTIVE

## Context

Sub-phase 3.4 closes the feedback loop: decisions need OUTCOMES to learn from. The spec's proxy is *"1h post-decision price change."* But per D-024 we have **no price feed** at $0 CU (the whole reason the stochastic lens reads flow, not price). Same structural wall as UNK-013 (classification overlap) and UNK-014 (temporal dynamics), now at the outcome layer.

## Decision

**Outcome = a forward-looking, $0-CU proxy measured from data the decision could not see.** Legitimate backtesting: predict on past, score on future.

- A decision is made at the window ending `T` using only data `≤ T` (the replay as-of cursor enforces this).
- Its outcome is measured over `[T, T + horizon]` where **horizon = 2 replay windows** — data strictly after the decision's as-of, so there's no leakage.
- Proxy metric (continuous, bounded [0,1], computable from cached L3 raw rows):
  ```
  flow_ratio   = forward_flow[T, T+h] / max(baseline_flow[T-h, T], eps)
  flow_term    = 0.5 * (tanh(flow_ratio - 1) clamped to [0,1])
  advr_term    = 0.5 * min(forward_poisoning_events / POISON_NORM, 1.0)
  outcome_value = flow_term + advr_term          # in [0, 1]
  ground_truth  = "escalated" if outcome_value >= ESCALATION_THRESHOLD else "quiet"
  ```
  - `forward_flow` / `forward_poisoning` come from `org_transfer_events` + `poisoning_events` in the forward window.
  - Interpretation: a risk-oriented decision (adversarial / exploit_risk / hedge / exit) is "correct" if the entity's activity **escalated** afterward; an opportunity/quiet decision is "correct" if it stayed quiet. The sign of correctness is resolved per-decision-action in the learner (3.4b).

## What this proxy can and cannot validate

- **CAN** test H5–H8 in a *relative* sense: does the orchestrator's score correlate with forward escalation better than any single lens? Do conflict-flagged windows show different forward outcomes? Does learning improve the correlation?
- **CANNOT** measure realized P&L (no execution, no price). All H5–H8 verdicts from 3.4/3.5 are explicitly **proxy-validated**, pending real price-outcome validation in Phase 4.

## Why not the alternatives

- Price feed → costs CUs (killed) / needs bloxroute (Phase 4).
- Same-window self-consistency → circular (leakage). The forward-window design avoids this.

## Reversal triggers

- Phase 4 price feed lands → replace the flow/event proxy with realized-return outcomes; re-run the learner; re-test H5–H8 for real. File a D-NNN.
- The proxy proves degenerate (e.g. outcome_value near-constant) → re-design the metric before trusting any H-verdict.

## Links

- D-024 (no price feed → flow grounding) — the parent constraint
- D-029 (temporal replay — supplies the forward windows)
- D-036 (learner consumes these outcomes), D-038 (LOOP H5–H8 verdicts use them)
- Spec: sub-phase 3.4 acceptance ("every Decision gets a stub Outcome within the horizon")
- I-3 (read-only L3) — outcomes are read-only measurements; the ledger is a SEPARATE writable SQLite, never L3.
