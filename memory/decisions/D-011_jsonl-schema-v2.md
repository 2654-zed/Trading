# D-011: JSONL schema v1 → v2 migration; resolves UNK-008

**Date**: 2026-05-16
**Made by**: agent (per `PHASE_2_CROSS_CHAIN_SPEC.md` sub-phase 2.5 boundary)
**Status**: ACTIVE

## Context

Sub-phase 2.5 lands the JSONL log schema migration from v1 (Phase 1) to v2
(Phase 2). v2 captures cross-chain attribution (`bridge_legs`,
`path_chains`, `chains`, per-record `chain` becomes dynamic) and a
structured `cost_breakdown` separating bridge fees from swap + gas + flash
costs. UNK-008 (per-record JSONL size optimization tradeoff) had been
gating this work; the spec sub-phase 2.5 promised resolution at this
boundary.

## Options considered

1. **Additive migration: v2 adds fields, v1 records still validate** ← **CHOSEN**
   Pros: Phase 1 analysis scripts read v2 records unchanged. Existing
   Phase 1 JSONL artifact (`run_artifacts/exp_001/2026-05-13.jsonl`,
   2,420 records, 5.7 MB) replays through the v2-aware analysis pipeline
   with identical numbers. No analysis-side rework. UNK-008's size
   tradeoff resolved with `per_address` block retained.
   Cons: Slight schema growth (~30% per record: 2.5 KB → 3.3 KB) even
   for intra-chain records. Acceptable per JSONL size projection.

2. **Hard cutover: v2 schema only, drop v1 fields not in v2**
   Pros: Cleaner; one schema version going forward.
   Cons: Breaks Phase 1 analysis scripts. Phase 1 JSONL artifact becomes
   un-replayable without conversion. Violates the spec's "Migration is
   additive only" acceptance criterion.

3. **Drop `per_address` debug block to shrink record size**
   Pros: ~30% size reduction per record.
   Cons: Loses per-rule diagnostic detail. Per spec UNK-008 + size
   projections (largest tail-risk case ≈ 100 MB/day at 3-chain volume),
   storage isn't tight. Drop only if runtime sample at T+2h exceeds the
   200 MB/day threshold.

## Decision

**Adopt additive v1→v2 migration. Keep `per_address` debug block.**

Mechanism:

| Element | Behavior |
|---|---|
| `schema_version: 2` | Top-level field in every new record. v1 records (no field) report as 1 via `record_schema_version(record)` |
| `chains: list[str]` | Unique sorted chains touched by the opp |
| `path_chains: list[str]` | Per-pool-hop chain (matches `path.pools` order) |
| `bridge_legs: list[dict]` | Empty for intra-chain; populated with `BridgeLeg.to_dict()` for cross-chain |
| `latency_drift_haircut_bps: float` | 0.0 for intra-chain |
| `gross_margin_raw_bps: int` | Pre-haircut margin (== `margin_gross_bps` for intra-chain) |
| `borrow_chain: str` | Where the flash loan executes |
| `cost_breakdown: dict` | `{gas_usd, flash_loan_fee_usd, bridge_fees_bps, swap_fees_bps_total, latency_drift_haircut_bps}` |
| v1 fields (all 16 required) | UNCHANGED. `chain` becomes dynamic but `validate_record` still accepts it |
| `per_address` debug block (inside filter_results) | KEPT (per UNK-008 sub-decision) |

## Rationale

- Phase 1's research artifact (the 2,420-record JSONL from EXP-001) is
  preserved as a reference baseline per D-007. The additive migration
  means Phase 1 replay through v2 analysis produces identical numbers
  (verified: dedup → 1 unique opp, matching D-008).
- The size growth (~30%) keeps even the worst-case tail risk scenario
  (~700 MB / 7-day run at 30K records/day) within the 50 GB Railway
  volume. No size pressure.
- The structured `cost_breakdown` makes H1' / H3 analysis cleaner — bridge
  fees and latency haircuts are first-class fields, not derived from
  multiple flat fields.
- `record_schema_version()` and `is_cross_chain_record()` helpers give
  analysis modules a clean entry point for branching on schema variant,
  without re-implementing legacy detection.

## Consequences expected

- Phase 1 JSONL replays through Phase 2 analysis without rework
- New Phase 2 runs produce v2 records readable by Phase 1 analysis (extra
  fields are ignored, not errored)
- The cross-chain detector (`CrossChainDetector`, sub-phase 2.3) is now
  wired into `detect_dry_run.py` (sub-phase 2.5 integration step) and
  its output flows through the same `FilterPipeline` + `OpportunityLogger`
  as intra-chain detection
- UNK-008 (JSONL size optimization) is RESOLVED with `per_address`
  retained; revisit if a runtime sample shows record volume exceeds the
  projected envelope

## Reversal criteria

- **Storage projection wrong**: if record volume exceeds 200 MB/day at
  T+2h of any Phase 2 run, the `per_address` block gets dropped via a
  runtime flag (no schema migration; just a field omission). Reversal is
  a one-line change to `schema.py::build_log_record`.
- **Cross-chain field shape changes**: e.g. `bridge_legs` needs nested
  hops for 3-chain triangle support — at that point we'd version-bump to
  v3 (still additive over v2). v2 → v3 follows the same back-compat
  pattern documented here.
- **Analysis scripts can't handle the mixed-schema-version JSONL**:
  if analysis breaks on a mixed run (some v1 records, some v2), pause
  Phase 2 detector deployment until analysis is patched.

## Consequences for the memory system

- `unknowns/UNKNOWNS.md` UNK-008 status flips to RESOLVED — Linked
  Decision = D-011
- `decisions/README.md` active table — add D-011 row
- `STRATEGY_STATE.md` EXP-002 — note schema migration shipped; analysis
  back-compat verified against Phase 1 artifact
- `INVARIANTS.md` — no change (schema is fixed at run start per I-5;
  v1 → v2 is a code-level rev, not a runtime mutation)
- `failures/FAILURE_LOG.md` — no entries (migration landed cleanly)

## Links

- Spec sub-phase: `../../PHASE_2_CROSS_CHAIN_SPEC.md` § sub-phase 2.5
- Replay artifact verified: `../../run_artifacts/exp_001/2026-05-13.jsonl`
- Schema module: `../../layer3_trading_exp/schema.py`
- Linked UNK: `../unknowns/UNKNOWNS.md` UNK-008
- Detector wiring: `../../layer3_trading_exp/scripts/detect_dry_run.py`
