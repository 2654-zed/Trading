# D-035: Decision engine v1 + outcome ledger activation (3.4a)

**Date**: 2026-05-28
**Made by**: agent (per D-020 pre-staged + user "approved, split 3.4 into a and b")
**Status**: ACTIVE

## Context

Sub-phase 3.4a converts adjudicated `WindowAggregate`s into structured `Decision` objects and activates the outcome ledger (live writes) so the feedback loop can close. Per the experiment charter (detection-only) + I-1/I-3, there is NO execution.

## Decisions locked

### Decision engine (`engine/orchestrator/decision_engine.py`)
- `Decision` = {action ∈ {enter, exit, hold, hedge}, confidence, risk_score, regime_label, aggregate_score, rationale, decided_at}. Rides the bus on `decision.window`.
- Rule-based v1 routing (conservative for a detection-only experiment):
  - `adversarial`/`exploit_risk`/`chaotic` + score ≥ 0.45 → `hedge`, else `hold`
  - `trend` + score ≥ 0.45 → `enter`, else `hold`
  - `low_liquidity`/default → `hold`
- `risk_score` = per-regime base (exploit_risk 0.9 … trend 0.25) + conflict boost.
- `confidence` = 0.5·regime_confidence + 0.5·score.
- `rationale` captures the reason string + top-3 lens contributions + regime/score/conflict context (the spec's "rationale captures the lens contributions" gate).

### Outcome ledger activation (`engine/feedback/outcome_ledger.py` → `LedgerWriter`)
- Live SQLite at `engine/data/outcome_ledger.db` — a SEPARATE writable DB; the L3 corpus is never written (I-3).
- Writes decisions, conflicts (per I-18, keyed by window/lenses/contradicting_types), and outcomes.

### Outcome attribution (`engine/feedback/outcome_attributor.py`, per D-034)
- Post-replay pass: for each decision at window end T, compute the forward-looking proxy over [T, T+2·window] (data the decision couldn't see).

## Acceptance evidence (3.4a subset)

200-window replay:
- **200 decisions** emitted + logged + in ledger. Action mix: hold=77, hedge=123, enter=0, exit=0 (no clean-trend window cleared the 0.45 enter bar — conservative, as designed).
- **194 conflicts** persisted to the ledger (I-18).
- **200 outcomes attributed** (forward proxy): ground_truth quiet=177, escalated=23; outcome_value 0.06–0.56, mean 0.112.
- 0 validation failures. EXECUTION: none.
- 444/444 tests pass (15 new in `test_decision_and_ledger.py`).

## What's deferred to 3.4b

- Learner + dynamic per-regime per-lens weights (I-19)
- Dry-run strategy router (+ explicit "execution NOT authorized" record)
- Closed-loop integration test + full Phase-3 LOOP with H5–H8 proxy-caveated verdicts

## Reversal triggers

- enter=0 may indicate the HIGH_SCORE=0.45 bar is mis-tuned for trend windows; revisit once the learner adjusts weights (3.4b) — trend scores may rise.
- Action taxonomy proves too coarse → extend in a D-NNN.

## Links

- D-034 (outcome proxy), D-033 (orchestrator aggregates feed decisions)
- D-020 (Phase 3 spec), I-18 (conflict storage), I-1/I-3 (no execution)
- Code: `engine/orchestrator/decision_engine.py`, `engine/feedback/outcome_ledger.py` (LedgerWriter), `engine/feedback/outcome_attributor.py`, adapter windowed accessors
- Tests: `engine/tests/test_decision_and_ledger.py`
- Smoke: `engine/scripts/run_engine_phase_3_4a.py`
