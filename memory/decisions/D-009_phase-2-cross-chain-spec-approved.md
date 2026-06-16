# D-009: Phase 2 cross-chain spec approved — implementation cleared to start

**Date**: 2026-05-16
**Made by**: user (one-word approval "approved" in response to drafted spec)
**Status**: ACTIVE

## Context

After D-007 invalidated H1 at current Base-only floors, LOOP Step 7 set
NEXT FOCUS to "draft the Phase 2 cross-chain spec." Spec was drafted at
`PHASE_2_CROSS_CHAIN_SPEC.md` covering: re-formulated H1′ + new H4
hypothesis, 8 sub-phases (2.1–2.8), 3 new invariants (I-12 / I-13 / I-14),
acceptance criteria per sub-phase, reversal triggers, and gating on the
open `--minutes` timer bug. User reviewed and approved.

This decision is the umbrella record that the spec is approved and that
implementation may proceed per the spec's sub-phase discipline.

## Options considered

### Option 1: Approve the spec as drafted and proceed with sub-phase 2.1 ← **CHOSEN**

Pros:
- Spec covers every blocking decision (token registry, trade structure,
  Across fee model, CU budget, JSONL schema, freshness thresholds)
- Acceptance criteria gate each sub-phase
- Reversal triggers explicit so the work can be halted with a clear signal
- 7 derivative decisions are pre-staged in the spec (each files at the
  sub-phase boundary that resolves it)

Cons:
- Phase 2 is materially larger than any single Phase 1 sub-phase
- The `--minutes` timer bug is a hard precondition for Phase 2.8 but
  doesn't block sub-phases 2.1–2.7

### Option 2: Approve in principle, ask agent to break sub-phase 2.5 (schema migration) into smaller commits before proceeding

Pros:
- Schema migrations are the highest-risk sub-phase (touches every record)
- Smaller commits = easier rollback

Cons:
- Spec already specifies the migration is additive-only and that all
  Phase 1 tests must pass after — that's the same safety property
- Premature sub-division before the agent has even read the existing
  schema code

### Option 3: Reject and ask for a thinner spec covering only sub-phases 2.1–2.3

Pros:
- Smaller commitment

Cons:
- Sub-phases 2.4–2.8 each resolve a blocking unknown (UNK-003, UNK-005,
  UNK-008) — splitting the spec would defer their resolution
- The spec already has a sub-phase gating mechanism; "thinner spec" is
  redundant with "stop at each sub-phase boundary"

## Decision

**Approve `PHASE_2_CROSS_CHAIN_SPEC.md` as drafted. Implementation cleared
to proceed under the spec's sub-phase discipline.**

The first concrete unit of work is the mandatory pre-work summary
specified in the spec's MANDATORY PRE-WORK section. No code until the
summary is reviewed and approved.

## Rationale

- User did the review (saw the spec in full, said "approved" with no
  amendments requested). That's the gate.
- The spec already encodes its own discipline — sub-phase boundaries,
  acceptance criteria, reversal triggers, "stop and wait for approval"
  at each boundary. Approval doesn't release the agent from those gates.
- The 7 pre-staged derivative decisions (token registry, margin floor,
  freshness thresholds, CU budget, JSONL schema, Across fee model,
  Phase 2 umbrella) get filed at their natural sub-phase boundaries.
  This decision is just the umbrella.

## Consequences expected

- **NEXT FOCUS shifts**: from "draft the Phase 2 cross-chain spec" to
  "complete the mandatory pre-work summary and present it for review."
- **EXP-002 stays ACTIVE in spec-execution status** in STRATEGY_STATE.md
  (was ACTIVE in spec-drafting; now ACTIVE in pre-work).
- **The `--minutes` timer bug** (open in FAILURE_LOG.md 2026-05-16) is
  upgraded from "deferred to Phase 2 prep" to "scheduled for fix before
  Phase 2.8 deployment." Failure log entry updated accordingly.
- **Sub-phase boundaries gate further decisions.** Each future sub-phase
  approval will require its own user "go" before code lands.
- **Phase 1 artifacts preserved** as the reference baseline. The
  `run_artifacts/exp_001/` JSONL + analysis report are referenced in the
  Phase 2 spec for replay testing during sub-phase 2.5 (schema migration
  back-compat verification).

## Reversal criteria

This decision flips to REVERSED if:

1. **Mandatory pre-work reveals the existing codebase architecture
   doesn't actually generalize cleanly to 3 chains.** E.g. process-global
   state in `pool_monitor.py` that resists multi-instantiation. The
   pre-work summary surfaces this; if found, the spec needs an addendum
   or sub-phase 2.2 needs significant rework.
2. **Across API behavior on first verification run is materially
   different from the spec assumptions.** If verification fails or
   fees drift >20 bps on most canonical tuples, D-006 itself needs
   revisiting (per the spec's reversal triggers section).
3. **Any sub-phase acceptance criterion fails 3 consecutive attempts.**
   Per I-11 T-C, repeat failures force a halt + redesign rather than
   continuing.

Partial reversals are allowed: a single sub-phase can be reverted
without unwinding the whole spec. The spec's "Stop here" boundaries
make this safe.

## Consequences for the memory system

- `decisions/README.md` active-decisions table — add row for D-009
- `STRATEGY_STATE.md` EXP-002 — update status to "ACTIVE (pre-work in
  progress)"
- `failures/FAILURE_LOG.md` 2026-05-16 entry — upgrade status from "fix
  deferred to Phase 2 prep work" to "fix scheduled before Phase 2.8;
  blocks deployment"
- `loop/LOOP.md` NEXT FOCUS — update to reflect pre-work as next gate
- `SYSTEM_STATE.md` — no change yet (deployment still REMOVED; no new
  runtime state until sub-phase 2.8)

## Links

- The approved spec: `../../docs/archive/PHASE_2_CROSS_CHAIN_SPEC.md`
- Driving hypothesis decision: `D-007_h1-invalidated-at-current-floors.md`
- Bridge model premise: `D-006_bridge-model-across.md`
- Open failure that gates 2.8: `../failures/FAILURE_LOG.md` 2026-05-16
- Pre-staged sub-phase derivative decisions (file at boundaries):
  - D-NNN_token-registry-curated-only (with 2.3)
  - D-NNN_phase-2-margin-floor (with 2.3)
  - D-NNN_across-static-fee-model (with 2.4)
  - D-NNN_jsonl-schema-v2 (with 2.5) — resolves UNK-008
  - D-NNN_per-table-freshness-thresholds (with 2.6) — resolves UNK-005
  - D-NNN_alchemy-cu-budget-phase-2 (with 2.2) — resolves UNK-003
  - D-NNN_phase-2-go-live (with 2.8 deployment)
