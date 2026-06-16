# D-039: Feedback-loop hardening + out-of-sample H5/H8 verdicts (3.5)

**Date**: 2026-05-28
**Made by**: agent (sub-phase 3.5 — final Phase 3 sub-phase)
**Status**: ACTIVE

## Context

Sub-phase 3.5 hardens the feedback infrastructure (ledger admin, observability) and runs the RIGOROUS out-of-sample H8 falsification (deferred from 3.4b): train weights on 70% of history, test learned-vs-static on the held-out 30%. This is the honest test the in-sample numbers couldn't give.

## What was built

- `engine/feedback/ledger_admin.py` — dump / counts / decisions-by-regime / find-missing-outcomes / **repair-missing-outcomes** (idempotent backfill).
- `engine/feedback/observability.py` — per-lens-per-regime **precision/recall** report (Markdown), the I-19/I-20 visibility surface.
- `engine/feedback/replay.py` — canonical `replay_to_ledger()` + `h8_holdout_test()` (out-of-sample train/holdout harness) + correlation analytics.

## The honest finding (this is the important part)

In-sample (3.4b) the orchestrator beat the best single lens by +7.0pp. **Out-of-sample, it does NOT:**

| Metric (holdout 30%, 60 windows) | Value |
|---|---|
| H8: static \|corr\| | 0.016 |
| H8: learned \|corr\| | 0.042 → **+2.6pp**, verdict SUPPORTED-OOS (but both near-zero) |
| H5: orchestrator \|corr\| | 0.016 |
| H5: best single lens (stochastic) \|corr\| | 0.067 |
| H5 margin | **−5.1pp** → **NOT-SUPPORTED out-of-sample** |

**Out-of-sample, on the forward-flow proxy, the multi-lens orchestrator does NOT beat the best single lens — a single lens predicts the proxy outcome better.** The in-sample +7pp was optimistic and did not generalize.

Observability corroborates: NO lens is a strong escalation predictor on the proxy. In `trend` (62 windows) 0 escalated (regime correctly tags quiet); in `adversarial` (123 windows) only 20 escalated, all lens precisions 0.16–0.19; `exploit_risk` graph precision 0.50 but n=4.

## What this does and does NOT mean

- **Does NOT mean the architecture is broken.** Every component works end-to-end: lenses, bus, synthesis, regime, conflict, orchestrator, decisions, ledger, learner, dry-run router — all validated, 470 tests pass, $0 CU.
- **Does mean the orchestrator's *edge over a single lens is unproven* on the forward-flow proxy.** Per UNK-015's three candidates — (a) proxy weakness, (b) lens dominance/correlation, (c) genuinely no edge — the OOS result cannot distinguish (a) from (c). Graph dominates signal volume (7,632/7,938) which supports (b) as a contributor.
- The proxy near-zero correlations (0.016–0.067) suggest the forward-flow proxy may simply be too weak a target — strengthening the case that **real outcomes (Phase 4) are required** to fairly judge H5.

## Verdict dispositions (carried to the Phase 3 final summary)

- **H5** (orchestrator > best lens ≥15pp): **NOT-SUPPORTED (proxy, OOS)**. −5.1pp out-of-sample. → UNK-015 stays OPEN; only Phase 4 real outcomes can disambiguate proxy-weakness vs genuine.
- **H6** (regime real): **SUPPORTED-PROXY**. Regimes carry outcome-relevant structure (trend→quiet, adversarial→the escalation-prone windows).
- **H7** (conflicts = alpha): **SUPPORTED-IN-SAMPLE-ONLY**. The 3.4b effect (+0.107 rank) was in-sample; not separately confirmed OOS. Downgraded pending OOS confirmation.
- **H8** (system learns): **WEAKLY-SUPPORTED-OOS**. Learned > static out-of-sample (+2.6pp) but both near-zero — the loop learns, but the proxy gives it almost nothing to learn from.

## Reversal triggers

- Phase 4 real outcomes re-run H5/H8: if the orchestrator beats best-lens by ≥15pp on realized P&L, H5 flips to SUPPORTED and the proxy is exonerated. If still negative, the multi-lens architecture's value is genuinely in doubt → simplify.

## Links

- UNK-015 (H5 margin) — this is the rigorous evidence; stays OPEN for Phase 4
- D-034 (proxy), D-036 (learner), D-038 (3.4b in-sample LOOP)
- Code: `engine/feedback/{ledger_admin,observability,replay}.py`
- Tests: `engine/tests/test_feedback_hardening.py`
- Artifacts: `engine/data/sub_phase_3_5_{h8.json,performance.md}`
