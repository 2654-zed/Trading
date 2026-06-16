# D-020: Phase 3 multi-lens engine spec approved — implementation cleared

**Date**: 2026-05-25
**Made by**: user ("approve as drafted, make a separate engine package. We will be transitioning to bloxroute as the board we observe instead of alchemy. But we will do this once the engine is coded and created")
**Status**: ACTIVE

## Context

After D-019 (H2 SUPPORTED-WITH-CAVEAT) closed out the EXP-002 LOOP, the
user shared a "Multi-Lens Engine — Build Blueprint v1" handoff doc
describing a Phase 3 architecture: 5 mathematical lenses, normalized
signal schema, cross-lens synthesis, orchestrator with regime/weighting/
conflict/decision engines, feedback loop.

Agent drafted `PHASE_3_MULTI_LENS_ENGINE_SPEC.md` translating the
blueprint into the sub-phase discipline that worked for Phase 1 + 2.
User reviewed and approved with two adjustments:

1. Engine ships as a **separate top-level package** (`engine/` at repo
   root, not under `layer3_trading_exp/`)
2. **bloxroute migration is deferred to Phase 4** — Phase 3 ships on
   the existing Alchemy + L3 data path; the adapter layer is designed
   so the swap is a Phase 4 effort

This decision is the umbrella record locking in spec approval and
clearing implementation to begin under the spec's sub-phase discipline.

## Options considered

### Option 1: Approve the spec as drafted with the two adjustments noted above ← **CHOSEN**

Pros: Phase 1/2 sub-phase discipline pattern proven. Spec covers every
load-bearing aspect — invariants, hypotheses, acceptance criteria,
reversal triggers, pre-staged decisions. The separate-package layout
preserves a clean rebuild path and frees `engine/` to evolve without
mutating frozen `layer3_trading_exp/`.

Cons: 5 sub-phases (3.1-3.5) is a 2-4 month commitment. Scope is larger
than Phase 2 by a meaningful factor.

### Option 2: Approve only sub-phases 3.1-3.2 (foundation + expansion); defer 3.3-3.5 until first results

Pros: Lower commitment up front.

Cons: The build order is interdependent. Splitting decisions mid-spec
creates the same coordination overhead Phase 2 wanted to avoid.

### Option 3: Approve in principle, ask for a thinner spec covering only sub-phase 3.1

Pros: Smallest commitment.

Cons: The acceptance criteria of sub-phase 3.1 ("prove the abstraction
generalizes") only make sense in the context of 3.2 and beyond. The
spec discipline IS the gating mechanism; no extra layer of "thin spec"
needed.

## Decision

**Approve `PHASE_3_MULTI_LENS_ENGINE_SPEC.md` as drafted (with the
spec-edit additions above).** Implementation cleared to proceed under
the spec's sub-phase discipline. First concrete unit of work is the
mandatory pre-work summary per the spec's MANDATORY PRE-WORK section.
No code until the summary is reviewed and approved.

## Rationale

- User reviewed the drafted spec (full text seen including 5 sub-phases,
  6 new invariants, 4 new hypotheses, decisions pre-staged) and approved.
- The two adjustments (separate package; bloxroute deferred) are
  documented in this decision + reflected in the spec edits.
- Phase 1+2 sub-phase discipline produced reliable outcomes — every
  sub-phase boundary had a clear stop-and-report. Same pattern applied.
- D-014 (Phase 2 go-live) is the structural precedent for this decision.

## Key adjustments locked in

| Adjustment | Source | Spec change |
|---|---|---|
| Engine ships as separate top-level package | User direction 2026-05-25 | Spec § "Package layout" added under SPEC section |
| bloxroute migration deferred to Phase 4 | User direction 2026-05-25 | Spec § "OUT OF SCOPE" entry expanded with architectural rationale |
| First lens = graph | Blueprint § 9 + spec recommendation | Sub-phase 3.1 acceptance criteria |
| 5 regimes per blueprint § 5.1 | Blueprint | Pre-staged decision in spec § "DECISIONS THIS SPEC LOCKS IN" |
| Static initial weights per blueprint § 5.2 | Blueprint | Pre-staged decision; weights only mutate after sub-phase 3.4 ledger exists |
| Execution gated behind separate decision | I-1 / Phase 1+2 carry forward | Sub-phase 3.4 acceptance criteria require `D-NNN_phase-3-execution-authorization` filed BEFORE the feature flag flips on |

## Consequences expected

- **NEXT FOCUS shifts**: from "Phase 2 closeout + post-CU-spike halt" to
  "complete the mandatory pre-work summary and present for review."
- **STRATEGY_STATE.md gets a new active experiment**: EXP-003 (Phase 3
  multi-lens engine build) — status: ACTIVE (pre-work in progress).
- **Phase 2 outstanding items** (UNK-010, UNK-011, UNK-012, H1'
  falsification, H3 distributional test) continue in parallel — they
  don't block Phase 3 sub-phases.
- **Code path**: `engine/` directory will be created at repo root in
  sub-phase 3.1. `layer3_trading_exp/` stays frozen at Phase 2 final
  state per the spec's import-discipline rule.
- **Cost**: Phase 3 multi-lens parallel reads will amplify Alchemy CU
  consumption (3-5x estimated). D-017 + D-015 reversal triggers carry
  forward; if hit again, sub-phase scope reduces.
- **Sub-phase boundaries gate further decisions.** Each future sub-phase
  approval will require its own user "go" before code lands.

## Reversal criteria

This decision flips to REVERSED if:

1. **Mandatory pre-work reveals the existing Phase 1+2 architecture
   doesn't actually generalize cleanly to a Lens base interface.** E.g.
   process-global state in `pool_monitor.py` resists multi-lens
   instantiation. The pre-work summary surfaces this; if found, the
   spec needs an addendum or sub-phase 3.1 needs significant rework.
2. **Sub-phase 3.1 ships but the Signal schema is found to be too rigid
   for a real second lens.** Then we re-spec the schema before sub-phase
   3.2 begins.
3. **H5/H6/H7/H8 falsify aggressively in sub-phase 3.4 dry-run.** Then
   the multi-lens architecture has no yield and we collapse back to a
   simpler best-single-lens approach.
4. **Alchemy CU budget exceeds D-017's reversal trigger** as a result
   of multi-lens read amplification. Then we scale back to fewer lenses
   OR accelerate Phase 4 (bloxroute migration) to relieve the read load.
5. **Any sub-phase acceptance criterion fails 3 consecutive attempts**
   (per I-11 T-C). Repeat failures force a halt + redesign.

Partial reversals are allowed: a single sub-phase can be reverted
without unwinding the whole spec. The spec's "Stop here" boundaries
make this safe.

## Consequences for the memory system

- `decisions/README.md` active-decisions table — add row for D-020
- `STRATEGY_STATE.md` — add EXP-003 as active experiment (status:
  ACTIVE — pre-work in progress)
- `failures/FAILURE_LOG.md` 2026-05-24 entry — Phase 3 spec doesn't
  unblock the open root-cause attribution for the CU spike; that
  remains a precondition before any Phase 3 deploy that hits Alchemy
- `loop/LOOP.md` NEXT FOCUS — update to reflect Phase 3 pre-work as
  next gate
- `SYSTEM_STATE.md` — no change yet (detector still HALTED; no Phase 3
  runtime state until sub-phase 3.4 execution gate is opened)

## Links

- Approved spec: `../../docs/archive/PHASE_3_MULTI_LENS_ENGINE_SPEC.md`
- Driving LOOP write-up: `../trades/2026-05-25_exp-002-summary.md`
- H2 finding that motivated lens-pivot: `D-019_h2-supported-with-caveat.md`
- Phase 2 umbrella precedent: `D-009_phase-2-cross-chain-spec-approved.md`
- Phase 2 go-live precedent: `D-014_phase-2-go-live.md`
- WS subscription lifecycle (post-D-017): code at `layer3_trading_exp/scripts/detect_dry_run.py::_AlchemyTransport`
- Pre-staged sub-phase derivative decisions (file at boundaries):
  - D-NNN_signal-schema-locked (with sub-phase 3.1)
  - D-NNN_event-bus-implementation (with sub-phase 3.1)
  - D-NNN_first-lens-graph (with sub-phase 3.1)
  - D-NNN_regime-taxonomy (with sub-phase 3.3)
  - D-NNN_lens-weight-initialization (with sub-phase 3.2)
  - D-NNN_phase-3-execution-authorization (with sub-phase 3.4; default OFF)
  - D-NNN_outcome-attribution-method (with sub-phase 3.4)
