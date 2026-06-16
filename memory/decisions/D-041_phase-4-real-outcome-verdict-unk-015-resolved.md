# D-041: Phase 4 real-outcome verdict — H5 NOT-SUPPORTED, UNK-015 resolved, Phase 5 NO-GO (provisional)

**Date**: 2026-05-28
**Made by**: agent (Phase 4 build + real-outcome re-test, user-driven)
**Status**: ACTIVE

## Context

Phase 4 replaced the D-034 forward-flow proxy with REAL realized price outcomes (forward price turbulence from free DefiLlama data, D-040) and re-ran the full H5–H8 battery — the test that was supposed to settle whether the multi-lens engine has a real edge (UNK-015). $0 CU, 94% price coverage, execution off.

## The result (real outcomes, 200-window replay)

| Hypothesis | In-sample | Out-of-sample | Verdict |
|---|---|---|---|
| **H5** orch > best single lens ≥15pp | orch \|corr\| 0.072 vs **information 0.190** → **−11.7pp** | orch 0.000 vs **stochastic 0.084** → **−8.4pp** | **NOT-SUPPORTED** |
| **H7** conflicts = alpha | conflict mean 0.102 vs 0.089 | — | SUPPORTED-PROXY (weak) |
| **H8** system learns | — | learned 0.023 vs static 0.000 (+2.2pp) | WEAKLY-SUPPORTED-OOS |

Per-lens forward-turbulence correlations are ALL weak (≤0.19) and unstable across the train/holdout split (information best in-sample at 0.19 → 0.039 OOS; stochastic best OOS at 0.084). **The orchestrator is consistently among the worst predictors, never the best.**

## The honest conclusion

**Across BOTH the proxy (D-039: OOS −5.1pp) and now REAL price outcomes (in-sample −11.7pp, OOS −8.4pp), the multi-lens orchestrator does NOT beat the best single lens. H5 is NOT-SUPPORTED.** The architecture, as built, does not demonstrate an edge over a single-lens detector — on four independent tests now (proxy IS/OOS, real IS/OOS).

## UNK-015 RESOLVED

The question was: is the missing edge (a) a proxy artifact, (b) lens dominance, or (c) genuine? **Resolution: NOT a proxy artifact (a) — real outcomes gave the same negative answer.** The evidence points to **(c) the multi-lens synthesis adds no demonstrable predictive edge over the best single lens** for this signal set and these outcomes. A residual caveat remains (below), but the burden of proof was on the architecture and it has not been met.

## Residual caveat (honest limits of this verdict)

- **All predictors are weak** (≤0.19 corr). The L3-interaction-derived signals are poor predictors of forward *price turbulence* generally — not just the orchestrator. It is possible these signals predict something *other* than price turbulence (e.g. specific exploit events), and that a **better-targeted outcome** (entity-specific forward returns on the pools a window's signals actually fired on, rather than whole-basket turbulence) would tell a different story.
- The whole-basket turbulence outcome is coarse (199/200 windows "quiet" on the binary; continuous range 0.044–0.287). This dilutes concentrated signal.

So the verdict is **NOT-SUPPORTED with one un-exhausted refinement** (entity-specific outcomes). It is provisional only in that narrow sense.

## Phase 5 recommendation: NO-GO (provisional)

Per PHASE_5 §1, execution requires H5 SUPPORTED ≥15pp on real outcomes. **That gate is NOT met — it is failed by ~8–12pp in the wrong direction.** Phase 5 (execution) must NOT proceed. The engine stays a research/detection instrument. Execution authorization (D-037's absent decision) remains correctly withheld.

Two honest paths for the user:
1. **Accept NO-GO.** The engine is a validated research instrument; we proved for ~$0 that it lacks a demonstrable trading edge BEFORE any execution capital was risked. This is the money-saving outcome the LOOP discipline exists to produce.
2. **Authorize ONE refinement iteration**: entity-specific outcome attribution (forward returns of the specific pools each window's signals fired on) + re-test H5. If that *also* fails, NO-GO is final.

## Reversal triggers

- Entity-specific outcome re-test flips H5 ≥+15pp → re-open Phase 5 consideration.
- A future lens set / different outcome horizon demonstrates edge → new decision.

## Links

- UNK-015 (resolved by this decision), D-039 (proxy OOS evidence), D-040 (free data), D-034 (proxy), D-037 (execution gate)
- PHASE_4_REAL_OUTCOMES_SPEC.md, PHASE_5_EXECUTION_TRANSITION_SPEC.md §1
- Artifacts: `engine/data/phase4_verdicts.json`, `phase4_performance.md`, `phase4_run.log`
- Code: `engine/adapters/price_history.py`, `engine/feedback/realized_outcome.py`, `engine/scripts/run_engine_phase_4.py`
