# D-029: Temporal replay mechanism (resolves UNK-014)

**Date**: 2026-05-28
**Made by**: user ("approved on all" — confirmed the ReplayClock + simulated-timestamp design)
**Status**: ACTIVE

## Context

UNK-014: sub-phase 3.2 lenses scanned the *current full state* of static L3 data every scan, so signals were identical across scans and the orchestrator's per-window scores had zero variance — making regime + conflict detection (sub-phase 3.3) meaningless. Resolving this was the precondition for 3.3.

## Decision

**Replay historical L3 data as a time series via a `ReplayClock`.**

- `engine/core/replay_clock.py` — `ReplayClock(start, end, slice_seconds)` yields a deterministic sequence of `ReplayWindow(start, end)` tuples covering the data span. `as_of = window.end`.
- Lenses query data **"as of"** the window cursor. The adapter (`Phase2L3CorpusAdapter`) was refactored to **cache raw timestamped rows once** (`prefetch_for_scan`) and derive all aggregates from them, filtered by an optional `as_of_ts`. `as_of_ts=None` → all data (static mode, preserves 3.1/3.2); `as_of_ts` set → rows at-or-before that simulated time.
- Signals are stamped with the **simulated** timestamp (via `Lens._forced_timestamp`, consumed by `emit()`), not wall-clock.
- The orchestrator + all engines window by **event-time** (max simulated timestamp seen), not wall-clock — so windows close correctly even though simulated time races ahead of wall-clock.

### Lockstep execution (critical correctness fix)

Initial design ran the three lenses as independent concurrent tasks, each with its own clock. This **fragmented windows**: the slow lens (graph, ~7,600 signals) lagged the fast lens (info, ~220 signals) in wall-clock, so the event-time watermark (driven by the fast lens) closed windows before the slow lens contributed — producing duplicate/partial regime labels (75 for ~30 windows) and starving cross-lens convergence (2 composites).

Fix: the smoke driver runs lenses in **lockstep** — for each simulated window, all three lenses scan before any advances. `Lens.scan_replay_window(bus, window)` (base class) supports this. The watermark advances monotonically; windows align across the whole pipeline. After lockstep: regime labels 1:1 with windows, composites jumped from 2 → 238.

## Acceptance evidence

200-window replay over the 49.2-day data span ($0 CU):
- All 3 lenses scan 200 windows in lockstep
- Aggregate scores **vary**: min 0.045, max 0.832, **166 distinct of 200** (UNK-014's zero-variance defect is gone)
- 7,938 signals, all 9 signal types fire (static mode only ever fired 5-6)
- 0 validation failures

## Reversal triggers

- Phase 4 live data (bloxroute/Alchemy) replaces replay as the primary driver; replay stays as the backtest harness. File a D-NNN when live data lands.
- If lockstep proves too slow at finer window granularity, parallelize with a barrier instead of full sequential.

## Links

- UNK-014 (the limitation this resolves)
- D-024 (zero-CU baseline — replay keeps it $0)
- D-021/D-022 (schema + bus, unchanged)
- Code: `engine/core/replay_clock.py`, `engine/adapters/l3_corpus_phase2.py` (raw-row caching + as_of), `engine/lenses/_base.py` (`_forced_timestamp`, `scan_replay_window`), all 3 lenses (replay run loop)
- Tests: `engine/tests/test_replay_and_engines.py` (ReplayClock, WindowAccumulator)
