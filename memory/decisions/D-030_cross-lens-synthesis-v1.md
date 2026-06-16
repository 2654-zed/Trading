# D-030: Cross-lens synthesis engine v1

**Date**: 2026-05-28
**Made by**: user ("approved on all" — confirmed simple grouping over embeddings)
**Status**: ACTIVE

## Context

Sub-phase 3.3 / blueprint § 4: the orchestrator must detect when multiple lenses *converge* on the same pattern (a composite stronger than any single lens). `engine/synthesis/cross_lens_engine.py`.

## Decision

**v1 uses SIMPLE grouping, not embedding-based semantic similarity** (per the pre-work amendment the user approved):

- `CrossLensEngine` subscribes to `signal.*`, buffers signals via the shared `WindowAccumulator` (event-time windows), and on each closed window:
  1. **Entity-level convergence**: group signals by shared address (extracted from metadata keys: `address`, `matched_address`, `deployer_address`, `org_id`, `pool_address`, `cluster_addresses`). If ≥2 *distinct lenses* fired on the same entity → emit a `CompositeSignal`.
  2. **Window-level fallback**: if no entity-level composite fired but ≥2 distinct lenses are active in the window → emit a window-level composite (weaker, `shared_entity=None`).
- `CompositeSignal` is NOT a `Signal` (it references multiple signals). It rides the bus on `composite.window`.
- `convergence_score = 0.5·lens_breadth + 0.3·mean_strength + 0.2·entity_anchor_bonus`, capped at 1.0.

Embeddings deferred: they need a model + vector infra that's overkill for proving synthesis. Revisit in a later sub-phase if simple grouping proves too coarse.

## Acceptance evidence

200-window replay: **238 CompositeSignals emitted, 238 logged** (100% capture after the lockstep + two-phase-shutdown fixes). Spec bar "≥1 CompositeSignal per coordinated event" — far exceeded. Unit tests verify entity-level convergence fires on 2-lens overlap and does NOT fire on single-lens windows.

## Reversal triggers

- Composite density too high/noisy → raise `min_lenses` or add a strength floor.
- Simple address-grouping misses semantically-related-but-different-address convergence → introduce embeddings in a D-NNN.

## Links

- D-029 (temporal replay — supplies the time-varying signal stream)
- D-021/D-022 (schema + bus)
- Code: `engine/synthesis/cross_lens_engine.py`, `engine/orchestrator/windowing.py`
- Tests: `engine/tests/test_replay_and_engines.py`
