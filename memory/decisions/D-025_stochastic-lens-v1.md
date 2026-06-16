# D-025: StochasticLens v1 — flow-volume time-series stochastics

**Date**: 2026-05-27
**Made by**: agent (per D-020 pre-staged + D-024 data reinterpretation)
**Status**: ACTIVE

## Context

Sub-phase 3.2 of the multi-lens engine introduces the StochasticLens as the second concrete lens. Per D-024, the lens reads transfer-flow volume time series from `org_transfer_events.value_eth + timestamp` instead of price (no $0-CU price feed exists). Math machinery (volatility / drift / diffusion decomposition) is identical to the original blueprint intent — only the underlying time series differs.

## The locked design

### Three signal types

| Type | What triggers it | Strength | Confidence | Time horizon |
|---|---|---|---|---|
| `volatility_regime_shift` | `current_std / baseline_std` ≥ 2.0 or ≤ 0.5 | `min(\|log10(ratio)\|, 1.0)` (saturates to 1.0 if ratio=0) | 0.85 | medium |
| `drift_change` | `\|cur_mean - base_mean\| / baseline_std` ≥ 2.0 (z-score) | `min(z / 5.0, 1.0)` | 0.80 | medium |
| `diffusion_anomaly` | Variance ratio between two halves of current window ≤ 0.5 or ≥ 2.0 (Lo-MacKinlay variance ratio test) | `min(\|log10(vr)\|, 1.0)` | 0.75 | short |

### Window structure

- `bucket_seconds = 60` (default): transfer-flow time series is bucketed in 1-minute bins. Each bucket = (start_ts, total_value_eth, transfer_count).
- `MIN_BUCKETS = 30`: scans skip addresses with fewer than 30 distinct minute-buckets — std/mean on tiny samples is noise.
- `CURRENT_BUCKETS = 30`: newest 30 buckets used as the "current" window estimate.
- `BASELINE_BUCKETS = 90`: next-oldest 90 buckets used as the "baseline" reference. If insufficient data for full split, falls back to half-and-half.

### Adapter contract additions

`StochasticDataSource` Protocol (defined in `engine/lenses/stochastic/lens.py`):
- `prefetch_for_scan(addresses)` — bulk-fetch hook (per D-023 perf pattern)
- `get_flow_buckets(address, *, bucket_seconds=60)` — returns the bucketed time series

Implemented by `Phase2L3CorpusAdapter` (extended in this sub-phase): a single full-table `IN(...)` query against `org_transfer_events`, then per-address bucketing in Python. The cache key is `frozenset(addresses)`; per-scan cache hits skip the SQL.

### Resilience contract

- `math.log(0)` is guarded in both `volatility_regime_shift` and `diffusion_anomaly` — strength saturates to 1.0 instead of raising.
- Per-address analysis is wrapped in try/except so one bad address can't crash the whole lens scan. Failures increment `emit_failures`.
- L3 query failures (caught by `except Exception` around `get_flow_buckets`) skip the address; the scan continues.

## What the math means for our actually-available data

Per the 3.2 diagnostic (2026-05-27):
- 50/217 monitored addresses have any `org_transfer_events` activity at all
- Top address: 151,291 transfers over ~50 days → ~3K/day → buckets per day ~50 → ~1,500 buckets total → way above MIN_BUCKETS
- Addresses with ~1,193+ transfers (25-30 addresses): bucketing depends on distribution; some pass MIN_BUCKETS, some don't
- Most addresses with <200 transfers won't have enough buckets

Expected signal density per scan: 1-10 `volatility_regime_shift` + 0-5 `drift_change` + 0-5 `diffusion_anomaly`. Empirical (3-scan smoke 2026-05-27): 1 `volatility_regime_shift` per scan, no drift/diffusion. The static-data scenario means signals repeat identically across scans (the time series doesn't advance until new L3 data arrives via sync).

## What ships in 3.2 — what doesn't

**Ships now**: the three signal types above, the threshold-tuned math, the perf hook, full unit-test coverage (10 tests in `test_stochastic_lens.py`).

**Does NOT ship in 3.2** (deferred to later sub-phases):
- Streaming/live mode (lens runs in batch-scan mode against historical L3 data)
- Multi-resolution buckets (e.g. 1s + 1m + 5m parallel analysis)
- Volatility-of-volatility (higher-order stochastics)
- Price-based grounding (deferred to Phase 4 per D-024 — augmentation, not replacement)

## Acceptance evidence (sub-phase 3.2 boundary)

1-hour smoke (2026-05-28, 120 scans, $0 CU):
- **120 `volatility_regime_shift` signals** (1/scan), mean strength 1.000
- 0 `drift_change`, 0 `diffusion_anomaly` against this static L3 snapshot (the flow series for the qualifying addresses showed volatility shifts but not drift/diffusion threshold crossings — expected; all three code paths are unit-tested)
- 0 validation failures, 0 emit failures
- 10 unit tests pass in `test_stochastic_lens.py`
- Caveat (UNK-014): static L3 data means identical signals every scan; drift/diffusion may fire once temporal replay lands in 3.3

## Reversal triggers

- A future analysis attests that flow-volume volatility is fundamentally different from price volatility in a way that renders the signal misleading → keep the lens but rename signal types in a D-NNN to reflect the flow grounding (e.g. `flow_volatility_shift`)
- Bucketing at 60s turns out to be too coarse or fine → adjust BUCKET_SECONDS in a D-NNN with empirical evidence
- The MIN_BUCKETS = 30 cutoff filters too many addresses → lower threshold + accept noisier strength estimates

## Links

- D-020 (Phase 3 spec approval)
- D-021 (signal schema locked) — the contract this lens emits against
- D-022 (event bus implementation) — the transport
- D-023 (GraphLens v1) — adapter-bulk-prefetch pattern reused here
- D-024 (zero-CU baseline + data reinterpretation) — the umbrella decision this lens implements
- I-15 / I-16 invariants — enforced (test coverage in `test_stochastic_lens.py`)
- Code: `engine/lenses/stochastic/lens.py`, adapter extensions in `engine/adapters/l3_corpus_phase2.py`
