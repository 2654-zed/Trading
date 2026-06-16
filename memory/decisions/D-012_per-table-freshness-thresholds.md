# D-012: Per-table L3 freshness thresholds; resolves UNK-005

**Date**: 2026-05-16
**Made by**: agent (per `PHASE_2_CROSS_CHAIN_SPEC.md` sub-phase 2.6 boundary)
**Status**: ACTIVE

## Context

Phase 1's freshness check used a single 30-minute threshold across every
filter-critical L3 table (per `INVARIANTS.md` I-8 as written then). The
issue (UNK-005): L3 tables have wildly different natural update cadences.
`trust_amplification` is recomputed roughly every few days; `trap_events`
runs as a daily batch; `bytecode_families` refreshes every few hours;
`contracts` and `deployers` update on minute-scale infrastructure events.

Under one 30-minute threshold, *every* filter evaluation in EXP-001
marked `degraded=True` because `trust_amplification` was ~4 days old at
evaluation time. 100% of the 2,420 logged records were degraded. H2
("Layer 3 flags ≥1% of opportunities") was structurally untestable — the
metric was vacuous by construction.

Sub-phase 2.6 implements per-table thresholds calibrated to each table's
natural cadence so H2 can produce a non-vacuous reading in Phase 2.

## Options considered

1. **Per-table thresholds calibrated to L3 cadences** ← **CHOSEN**
   Pros: H2 becomes testable. Each rule's freshness reading reflects the
   table it actually queries.
   Cons: Adds a configuration surface (5 numbers) that needs maintenance
   if L3's table cadences shift.

2. **Single global threshold raised to 48h**
   Pros: Simpler.
   Cons: Loses the operational signal that `contracts`/`deployers` going
   stale by even an hour is a sync-pipeline problem.

3. **Drop the freshness check entirely**
   Pros: Even simpler.
   Cons: Violates I-8 (loud failure / data integrity). Silent stale-data
   would corrupt H2/H3 without surfacing.

## Decision

Adopt per-table thresholds, calibrated to L3's observed cadences:

| Table | Threshold | Source |
|---|---|---|
| `contracts` | 1 hour | Minute-cadence updates from L3's infrastructure pipeline; 1h is ~60x the typical update interval |
| `deployers` | 1 hour | Derived from contracts; same cadence |
| `bytecode_families` | 6 hours | Family classifier runs every few hours |
| `trap_events` | 24 hours | Daily trap-detection batch |
| `trust_amplification` | 36 hours | Slowest L3 table; recomputed at multi-day cadence |
| (any other) | 6 hours | Default fallback for tables not enumerated above |

Implementation lands in `freshness.py::PER_TABLE_THRESHOLDS_SECONDS`
and is wired through `FilterPipeline.evaluate` to populate
`degraded_per_table: dict[str, bool]` on `FilterEvaluation`. JSONL v2
records carry the same `degraded_per_table` field (added to
`V2_ADDITIONAL_FIELDS` in `schema.py`). Each `l3_freshness[<table>]`
entry now also carries `threshold_seconds` so analysis can verify
which threshold fired.

The legacy aggregate `degraded` bool is preserved as a back-compat field
but its semantics shift: it now means "ANY filter-critical table is
stale under its per-table threshold" (rather than "any table older than
30 min"). This shift is documented at code level + here.

## Rationale

- **Phase 1 JSONL replay confirms the calibration.** 2,420 EXP-001
  records replayed through Phase 2 per-table logic showed:
  - 100% had `trust_amplification` stale (it was ~4 days old at the
    time of EXP-001 — well beyond the 36h threshold)
  - **0% had ALL 5 tables stale** (Phase 2 "fully degraded")
  - 0% of contracts / deployers / bytecode_families / trap_events
    were stale under their per-table thresholds
  - Spec acceptance was "<10% fully degraded"; we got 0%.
- This means H2 in Phase 2 can distinguish "this opportunity was
  evaluated against fresh `contracts` + `deployers` + `trap_events` +
  `bytecode_families`" (good — 4/5 rules' inputs are trustworthy) from
  "this opportunity is fully degraded" (rare). Phase 1's collapsed bit
  couldn't make that distinction.
- The thresholds are conservative (each ~10-100x the typical update
  interval) so transient L3 sync hiccups don't spuriously flag rules
  as degraded.

## Consequences expected

- H2 in Phase 2 produces non-vacuous results when at least some
  rules' tables are fresh
- Rules that depend ONLY on `trust_amplification` (rule 8 — CRITICAL
  alert lookups) still mark their results as degraded until L3's
  `trust_amplification` cadence improves OR the threshold is widened
- Analysis scripts gain a new dimension: per-rule degraded rate (a
  rule whose dependency table is always stale doesn't contribute to
  H2 statistics)
- `degraded` (legacy aggregate) remains True in the Phase 1 dataset
  on replay because `trust_amplification` is stale under its 36h
  threshold. That matches reality — the table genuinely IS stale.
  But `degraded_per_table` correctly attributes the staleness to the
  one table responsible.

## Reversal criteria

- **L3 changes a table's update cadence** materially (e.g.
  `trust_amplification` moves to hourly refresh) — re-tune the
  corresponding threshold. Change is a one-line constant edit; no
  schema migration.
- **Phase 2 H2 calibration shows that a 36h `trust_amplification`
  threshold leaves H2 still effectively vacuous** (rule 8 fires almost
  never with fresh table) — extend to 72h or document the limitation.
- **A new L3 table is added to the filter-critical set** — add to
  `PER_TABLE_THRESHOLDS_SECONDS` with a documented cadence rationale.
- **`degraded_per_table` consumers misinterpret the legacy `degraded`
  aggregate** — clarify in analysis scripts (already documented in
  `FilterEvaluation`'s docstring).

## Consequences for the memory system

- `unknowns/UNKNOWNS.md` UNK-005 status → RESOLVED, Linked Decision = D-012
- `decisions/README.md` active table — add D-012 row
- `STRATEGY_STATE.md` — H2 moves out of "WEAKENED — structurally untestable"
  state into "WEAKENED — testable but partial coverage" (still WEAKENED
  because the slowest L3 table genuinely IS stale relative to the rule
  that depends on it; per-table thresholds don't paper over that)
- `INVARIANTS.md` I-8 — the wording "30 minutes" is now outdated.
  Update to reference `freshness.PER_TABLE_THRESHOLDS_SECONDS` as the
  source of truth.
- `failures/FAILURE_LOG.md` — no entries (sub-phase landed cleanly)

## Links

- Spec sub-phase: `../../docs/archive/PHASE_2_CROSS_CHAIN_SPEC.md` § sub-phase 2.6
- Linked UNK: `../unknowns/UNKNOWNS.md` UNK-005
- Phase 1 replay: `../../run_artifacts/exp_001/2026-05-13.jsonl`
  (2,420 records — 100% Phase 1 degraded, 0% Phase 2 fully degraded)
- Schema migration that preserves the new field: D-011
- Implementation:
  - `../../layer3_trading_exp/freshness.py::PER_TABLE_THRESHOLDS_SECONDS`
  - `../../layer3_trading_exp/filter_pipeline.py::FilterEvaluation.degraded_per_table`
  - `../../layer3_trading_exp/schema.py::V2_ADDITIONAL_FIELDS` (`degraded_per_table`)
