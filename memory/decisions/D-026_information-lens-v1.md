# D-026: InformationLens v1 — Shannon entropy + KL divergence over L3 categorical distributions

**Date**: 2026-05-27
**Made by**: agent (per D-020 pre-staged + D-024 data reinterpretation)
**Status**: ACTIVE

## Context

Sub-phase 3.2 introduces the InformationLens as the third concrete lens. Per D-024, the lens reads two parallel categorical streams from L3 (rather than per-block token-flow distributions that don't exist at $0 CU):

1. `liquidity_events.event_type` distribution per token address (add_liquidity / remove_liquidity)
2. `org_transfer_events.from_role` distribution per recipient address (gas_station / laundry / unknown / null)

Math (Shannon entropy + KL divergence) is identical to the original blueprint intent — only the underlying categorical sources differ.

## The locked design

### Three signal types

| Type | Trigger | Strength | Confidence | Time horizon |
|---|---|---|---|---|
| `entropy_drop` | `H(baseline) - H(current)` ≥ 0.3 bits (diversity collapse) | `min(delta / H_baseline, 1.0)` | 0.85 | medium |
| `regime_surprise` | `KL(current \|\| baseline)` in `[0.2, 1.0)` bits (moderate distribution shift) | `min(kl / 1.0, 1.0)` linear | 0.80 | medium |
| `divergence_spike` | `KL(current \|\| baseline)` ≥ 1.0 bits (acute shift) | `min(log2(1+kl) / log2(8), 1.0)` log-scaled | 0.90 | short |

Both signal types are emitted for each (source × address) pair where the distribution thresholds cross. `source` ∈ {`liquidity_events`, `org_transfer_roles`} is captured in metadata so downstream consumers (synthesis in 3.3) can distinguish the angle.

### Window structure

- `WINDOW_DAYS_CURRENT = 7`: the "now" window is the trailing 7 days from the latest event in L3 for that address (anchored to the data, not wall-clock-now, because the L3 SQLite copy is updated by sync — may be behind live chain).
- `WINDOW_DAYS_BASELINE = 7`: the reference window is the 7 days immediately before the current window.
- `MIN_TOTAL_CURRENT = MIN_TOTAL_BASELINE = 10`: scans skip addresses with insufficient samples — entropy on tiny distributions is noise-dominated.

### Information-theoretic primitives

`shannon_entropy(counts)` — Shannon entropy in bits. Returns 0 for empty / single-category inputs.

`kl_divergence(p_counts, q_counts, smoothing=1.0)` — KL(P || Q) with **additive (Laplace) smoothing** so zero-probability bins don't blow up to infinity. Returns 0 for empty input.

Both helpers are exported from `engine.lenses.information.lens` for direct testing.

### Adapter contract additions

`InformationDataSource` Protocol:
- `prefetch_for_scan(addresses)` — bulk-fetch hook
- `get_liquidity_event_distributions(address, days_current, days_baseline)` → `{current, baseline, current_total, baseline_total}` dict
- `get_role_distributions(...)` — same shape, different source

Implemented by `Phase2L3CorpusAdapter`: one full-table scan per source, anchored to `MAX(timestamp)` filtered to the address set so windows track the data rather than wall-clock.

### Resilience contract

- Per-address try/except around both source fetches; one bad address can't crash the scan.
- Smoothing in `kl_divergence` prevents log(0) / log(infinity) divergence.

## Acceptance evidence (sub-phase 3.2 boundary)

- 16 unit tests in `test_information_lens.py` cover:
  - entropy + KL primitives (uniform / single-category / empty / identical / disjoint cases)
  - all three signal types fire under intended conditions
  - none fire when distributions are similar (no false positives in unit tests)
  - role-distribution source path (not just liq path)
  - sample-count minimum is respected
  - L3 query failures don't crash the lens
  - I-15 import isolation

1-hour smoke (2026-05-28, 120 scans, $0 CU):
- **120 `entropy_drop`** (1/scan, mean strength 0.513) + **120 `regime_surprise`** (1/scan, mean strength 0.450) = 240 signals total
- 0 `divergence_spike` against this static snapshot (no address crossed the KL≥1.0 acute-shift bar)
- 0 validation failures
- 16 unit tests pass in `test_information_lens.py`
- Caveat (UNK-014): static data → identical signals per scan; the full divergence_spike path is unit-tested and will exercise once temporal replay lands in 3.3

## What ships in 3.2 — what doesn't

**Ships now**: the three signal types, the entropy/KL math, both data sources (liquidity events + transfer roles), full test coverage.

**Does NOT ship** (deferred):
- Per-block token-flow distributions (no $0-CU source; Phase 4 may enable via bloxroute swap events)
- Higher-order information theory (mutual information across pools, transfer entropy time-lag analysis)
- Distribution-drift attribution (which category caused the entropy drop) beyond what's already in metadata

## Caveats per D-024

`liquidity_events` produces ~60 events/day across our token set; a per-pool 7-day window may have only 5-50 samples. The MIN_TOTAL_* thresholds reject the lowest-sample cases but moderate-sample cases still have noisy entropy estimates. Phase 4's potentially-faster data source can narrow the windows to hours instead of days; for 3.2 we accept the noise floor.

## Reversal triggers

- Entropy estimates prove too noisy at 7-day window scale → adjust WINDOW_DAYS_* in a D-NNN
- The two sources (liq + roles) turn out to fire on the same underlying events → drop one to avoid double-counting
- KL_REGIME_THRESHOLD = 0.2 bits is empirically wrong → re-calibrate with a D-NNN

## Links

- D-020, D-021, D-022, D-023, D-024 (parent + sibling decisions)
- I-15 / I-16 invariants
- Code: `engine/lenses/information/lens.py`, adapter extensions in `engine/adapters/l3_corpus_phase2.py`
- Tests: `engine/tests/test_information_lens.py`
