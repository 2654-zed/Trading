# D-042: Phase 4 run-2 (entity-specific outcomes) — H5 NOT-SUPPORTED, Phase 5 NO-GO FINAL

**Date**: 2026-05-28
**Made by**: agent (Phase 4 run-2, user "run 2")
**Status**: ACTIVE — supersedes the "provisional" caveat in D-041

## Context

D-041 resolved UNK-015 with one un-exhausted refinement: outcomes were whole-basket turbulence (88-token average), which dilutes entity-concentrated signal. Run-2 exhausts it — outcomes are now the forward turbulence of ONLY the tokens each window's signals actually fired on (entity-specific attribution: orchestrator captures signal-metadata addresses → ledger → attributor). 460/460 windows attributed entity-specifically (0 fallback), 94% price coverage, $0 CU.

## Result (entity-specific real outcomes)

| | In-sample | Out-of-sample |
|---|---|---|
| **H5** orch vs best single lens | orch \|corr\| 0.126 vs **information 0.292** → **−16.6pp** | orch 0.042 vs **information 0.152** → **−11.0pp** |

Per-lens correlations (entity-specific forward turbulence):
| lens | in-sample | OOS |
|---|---|---|
| **information** | **0.292** | **0.152** |
| stochastic | 0.163 | −0.053 |
| graph | 0.035 | — |
| **orchestrator** | 0.126 | 0.042 |

H7 weak-SUPPORTED (0.126 vs 0.114). H8 OOS +0.3pp (negligible).

## Conclusion: H5 NOT-SUPPORTED, robustly. NO-GO is FINAL.

The refinement made the picture **clearer, not murkier**: entity-specific outcomes *strengthened* the predictive signal (information lens 0.19→0.29), yet the orchestrator's gap to the best lens *widened* (−11.7→−16.6pp in-sample). Across **six tests now** the orchestrator never beats the best single lens out-of-sample or on any real outcome:

| Test | orch − best lens |
|---|---|
| proxy in-sample | +7.0pp (only positive; in-sample) |
| proxy OOS | −5.1pp |
| real whole-basket IS | −11.7pp |
| real whole-basket OOS | −8.4pp |
| real entity IS | **−16.6pp** |
| real entity OOS | **−11.0pp** |

**UNK-015 is fully resolved (candidate c, genuine):** the multi-lens synthesis adds no edge — it actively *dilutes* the one lens (information) that carries signal, because the graph lens dominates signal volume (7,632/7,938) while being nearly non-predictive (corr 0.035). Weighting/synthesis drags the strong information signal toward the noisy graph signal.

**Phase 5 (execution) = NO-GO, final.** The H5 ≥15pp gate is failed by 11–17pp in the wrong direction across every honest test. Execution authorization (D-037) stays withheld. The engine is a validated research instrument that does NOT demonstrate a tradable edge.

## Actionable insight (the silver lining)

There IS modest real predictive signal — but it lives in the **information lens alone** (entropy/KL on token-flow distributions), corr ~0.29 with entity-specific forward turbulence. The productive follow-on is NOT a multi-lens orchestrator; it is a **single information-lens detector**, tested on its own, possibly with a better-targeted outcome/horizon. The multi-lens architecture should be set aside.

## Cost / charter

$0 CU across all of Phase 4 (DefiLlama free data, D-040). Read-only (I-1/I-3 intact). No execution. The entire "does this have an edge?" question was answered for ~$0 before any execution capital was risked — the intended, money-saving result.

## Reversal triggers

- A single information-lens detector demonstrates ≥15pp edge over baseline on real outcomes → new decision, possibly re-opening execution consideration (single-lens, not multi-lens).

## Links

- D-041 (run-1, provisional — this finalizes it), UNK-015 (now fully resolved), D-037 (execution gate, stays closed)
- D-040 (free data), D-039 (proxy OOS), D-034 (proxy)
- PHASE_5_EXECUTION_TRANSITION_SPEC.md §1 (gate failed)
- Artifacts: `engine/data/phase4_run2.log`, `phase4_verdicts.json`
- Code: entity capture in `engine/orchestrator/orchestrator.py`, `engine/feedback/realized_outcome.py` (entity_mode)
