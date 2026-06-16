# D-037: Execution router dry-run only — execution NOT authorized

**Date**: 2026-05-28
**Made by**: user ("approved" — execution stays gated off; detection-only charter)
**Status**: ACTIVE

## Context

Sub-phase 3.4's spec includes a `strategy_router` (Decision → order) and notes that flipping the execution feature flag "requires the explicit `D-NNN_phase-3-execution-authorization` decision." This experiment's charter is **detection + logging only**, and invariants I-1 (read-only on-chain, no signing, no private keys) + I-3 (read-only L3) prohibit transacting.

## Decision

**Build the strategy router in DRY-RUN mode and DO NOT file the execution-authorization decision. Execution is unreachable in this experiment.**

- `engine/execution/strategy_router.py`:
  - `route(decision) → IntendedOrder | None` — maps action to a dry-run intended order (`hedge`/`exit` → reduce_risk, `enter` → add_exposure, `hold` → no order). Every order is tagged `DRY_RUN: True`.
  - There is **no code path that signs or sends a transaction**. `execute()` always raises `ExecutionNotAuthorizedError`.
  - Constructing with `live_enabled=True` raises `ExecutionNotAuthorizedError` unless an authorization token `"AUTHORIZED_BY_D-NNN"` is supplied — which requires the deliberately-ABSENT `D-NNN_phase-3-execution-authorization`. Even then `_live` is hard-forced False.

**The companion decision `D-NNN_phase-3-execution-authorization` is intentionally NOT created.** Its absence is the gate. Spinning up execution would require:
1. A new explicit user decision authorizing it,
2. Revisiting I-1/I-3,
3. Private-key + signing infrastructure that does not exist in this codebase.

## Acceptance evidence

- 3.4b smoke: 123 dry-run intended orders written (77 holds dropped), `EXECUTION DISABLED`. Zero on-chain actions, zero CUs.
- Tests: `live_enabled=True` raises; `execute()` raises; `hold` → no order; `hedge` → reduce_risk dry-run order tagged DRY_RUN.

## Reversal triggers

- The ONLY way execution turns on: a future explicit `D-NNN_phase-3-execution-authorization` decision authored by the user, after a deliberate review of I-1/I-3 and the addition of signing infrastructure. Until then, this stays a research instrument.

## Links

- I-1 (read-only on-chain, no keys), I-3 (read-only L3) — preserved
- D-035 (decisions the router consumes)
- Project charter: detection + logging only (memory: project_l3_trading_experiment)
- Code: `engine/execution/strategy_router.py`
- Tests: `engine/tests/test_learner_and_execution.py`
