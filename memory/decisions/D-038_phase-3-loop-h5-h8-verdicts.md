# D-038: Phase 3 LOOP + H5–H8 proxy verdicts (sub-phase 3.4b close)

**Date**: 2026-05-28
**Made by**: agent (LOOP execution per I-10 at the sub-phase 3.4 boundary)
**Status**: ACTIVE

## Context

Sub-phase 3.4 closed the multi-lens engine's first feedback loop. Per I-10 + the spec ("LOOP fires at sub-phase end ... verdicts on H5, H6, H7, H8"), the full 7-step LOOP ran against the 200-window $0-CU replay. Full writeup: `memory/trades/2026-05-28_phase-3-loop-summary.md`.

## Verdicts (all PROXY-validated per D-034)

| Hypothesis | Verdict | Evidence |
|---|---|---|
| **H5** orchestrator > best single lens by ≥15pp | **WEAK-SUPPORT-PROXY** | orch |corr|=0.242 vs best lens (graph) |corr|=0.172 → +7.0pp. Directionally beats best lens but below the 15pp bar. → UNK-015 |
| **H6** regime detection is real | **SUPPORTED-PROXY** | spot-check 20/20 windows intuitive (≥70%), coherent narrative (D-031) |
| **H7** conflicts are alpha | **SUPPORTED-PROXY** | conflict windows mean outcome 0.129 vs 0.083 no-conflict, rank effect +0.107 (D-032) |
| **H8** system learns | **SUPPORTED-PROXY** | weights move measurably (L1 0.38–0.90) + persist + change decisions (enter 0→40) (D-036) |

## The load-bearing caveat

Every verdict rests on the D-034 forward-activity proxy (flow + adversarial-L3-event escalation), NOT realized P&L. Real validation requires Phase 4's price feed. The verdicts say "the machinery behaves as designed on a reasonable proxy," not "this makes money."

## Consequences

- Phase 3's engine is functionally complete (3.1–3.4). Sub-phase 3.5 hardens the feedback infra + runs the H8 falsification replay (learned vs initial on a held-out window), which also sharpens H5 (UNK-015).
- No execution authorized (D-037). The phase ends as a research instrument.

## Links

- LOOP writeup: `memory/trades/2026-05-28_phase-3-loop-summary.md`
- D-034 (proxy), D-035 (decisions), D-036 (learner), D-037 (no execution)
- D-031 (H6), D-032 (H7), D-023/D-029 (UNK-013/014 resolutions underpinning the replay)
- UNK-015 (H5 margin question) — opened by this LOOP
