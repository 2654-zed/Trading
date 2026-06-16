# D-028: Spec amendment — window-count acceptance bars are window-size-aware

**Date**: 2026-05-28
**Made by**: user ("b" — chose to formally amend the spec's window-count bars to be window-size-aware, over treating 3.2 as passed on capability proof alone)
**Status**: ACTIVE

## Context

Sub-phase 3.2's acceptance criterion read: *"Smoke run of 6 hours emits ≥10K total signals across 3 lenses + ≥100 aggregate scores."* When the user shortened the smoke to 1 hour (2026-05-27 direction), the signal bar scaled cleanly (10K/6 ≈ 1,700) but the **aggregate-score bar did not** — and on inspection, the original ≥100 bar was itself unreachable even at 6 hours:

- The orchestrator emits **one aggregate per elapsed window**.
- With the 5-min default window, a 6h run yields `floor(6×3600 / 300) = 72` windows — not 100.
- A 1h run yields `floor(3600 / 300) = 12` windows.

So "≥100 aggregate scores" silently assumed a sub-minute window size. The count is a function of `floor(run_seconds / window_seconds)`, not an absolute target.

The sub-phase 3.3 criterion *"manual spot-check on 100 windows"* has the same dependency: 100 windows at 5-min = ~8.3h of data.

## The empirical evidence

1-hour 3.2 smoke (2026-05-28): 6,240 signals, **13 aggregate windows** at 5-min — all 13 received by the logger, 0 lost. A fast 2-sec-window re-run (40 scans) produced **20 windows**, mechanically clearing the user-scaled ≥17 bar. The orchestrator's window-emission machinery is demonstrably correct; only the metric's framing was wrong.

## Decision

**Amend the window-count acceptance bars to be window-size-aware rather than absolute.**

### Sub-phase 3.2 (amended)

Old: "≥100 aggregate scores in 6h"
New: "one aggregate score per elapsed window (`expected_windows = floor(run_seconds / window_seconds)`, ±1 for partial boundary windows), zero loss." Run duration may be shortened with the signal bar scaled proportionally.

The real capability being tested: **the orchestrator emits exactly one aggregate per elapsed window with zero loss**, not a magic absolute count.

### Sub-phase 3.3 (amended)

Old: "manual spot-check on 100 windows"
New: "manual spot-check on a sample of ≥100 windows" — the 100-window *sample* drives the required run duration / window size, not a fixed wall-clock. Also annotated with the UNK-014 precondition (regime classification is meaningless on static data; temporal replay must land first).

## Why this over option (a) "treat as passed on capability proof"

Option (a) would have left a latent inconsistency in the spec that would re-bite at every future window-count criterion. Amending the spec makes the bars correct and self-consistent, so 3.3/3.4 don't inherit the same arithmetic trap. The capability was already proven (20 windows in the re-run); this decision fixes the *measurement*, not the system.

## Sub-phase 3.2 final status

With the amended bar, sub-phase 3.2 **PASSES all acceptance criteria**:
- 3 lenses concurrent ✓
- I-15 no lens-to-lens imports ✓
- p95 latency 16.2ms < 500ms ✓
- sensible per-window aggregate (0.5013 = graph 1.0×0.25 + stoch 1.0×0.20 + info 0.51×0.10) ✓
- ≥1,700 signals → 6,240 ✓
- one aggregate per elapsed window, zero loss (13/13 at 5-min, 20/20 at 2-sec) ✓
- no 3.1 regressions (406/406 tests) ✓

## Reversal triggers

- If a future sub-phase genuinely needs an absolute minimum window count (e.g. for statistical power of a spot-check), state it as a sample-size requirement that drives duration/window-size, not a bare count against a fixed wall-clock.

## Links

- D-020 (Phase 3 spec approval) — the spec being amended
- D-027 (orchestrator v1) — the component whose window-emission this measures
- UNK-014 (static-replay limitation) — the 3.3 precondition annotated in the amended 3.3 criterion
- Spec file: `PHASE_3_MULTI_LENS_ENGINE_SPEC.md` lines ~306 (3.2) + ~328 (3.3)
