# D-024: Phase 3 zero-CU baseline + lens data-source reinterpretation

**Date**: 2026-05-27
**Made by**: user ("do 1 and record the $0 CU usage for when our monthly CU resets and we can reiterate when CU comes back")
**Status**: ACTIVE

## Context

After D-017 (newHeads-subscription-lifecycle fix) + the second CU spike that triggered the live-detector kill, the user wanted Phase 3 to ship at **$0 CU cost** — no Alchemy spend, no on-chain RPC, no live WS subscriptions. The engine is purely offline / replay-mode until Phase 4 introduces bloxroute (or restored Alchemy budget) as a live data source.

Sub-phase 3.2 (per D-020 / PHASE_3_MULTI_LENS_ENGINE_SPEC.md) requires 2 new lenses:
- **Stochastic lens** — spec says "reads price + liquidity streams" → volatility regime shift detection
- **Information lens** — spec says "entropy + KL divergence on per-block token-flow distributions"

Diagnostic (per UNK-013 lesson — diagnose data sources before code) revealed:
- Phase 2's live WS feed → not usable ($CU)
- Phase 2's JSONL emissions → too sparse (10-200 records/day, opportunity-event only)
- L3 `transaction_events` (18M rows) → 0 hits on our 217 monitored addresses
- **L3 `org_transfer_events`** → 244K events with timestamp + value_eth across 50 of our addresses
- **L3 `liquidity_events`** → 2,963 events across 2,962 distinct blocks on our token set

Both `org_transfer_events` and `liquidity_events` overlap meaningfully with our monitored set. Neither is a classical "price feed."

## What this decision locks

### 1. Lens data-source reinterpretation (sub-phase 3.2 only)

| Lens | Spec-intent data | Actually-used data | Math preserved? |
|---|---|---|---|
| **Stochastic** | Per-block price + liquidity | Per-window transfer-flow volume from `org_transfer_events.value_eth + timestamp`, bucketed by minute | Yes — volatility/drift/diffusion decomposition is generalizable to any stochastic time series, not just price |
| **Information** | Per-block token-flow distributions | (a) `liquidity_events.event_type` distribution per pool per window (add/remove), (b) `org_transfer_events.from_role` distribution per pool (gas_station / laundry / unknown / null) | Yes — entropy / KL divergence operates on any categorical distribution |

The signal types keep their spec-defined names (`volatility_regime_shift`, `drift_change`, `diffusion_anomaly`, `entropy_drop`, `regime_surprise`, `divergence_spike`). The semantic meaning shifts from "price" to "flow." Downstream stages (orchestrator, synthesis) don't care about the underlying physical meaning — they care about the schema contract (D-021) and the math.

### 2. Zero-CU baseline (record + monitor)

Sub-phase 3.2 ships at **$0 Alchemy CU spend**. Recording the baseline so future re-iteration is unambiguous:

- **Alchemy CUs consumed by Phase 3 sub-phase 3.2**: 0 (no live RPC, no WS, no eth_getLogs)
- **Data path**: 100% local file I/O (L3 SQLite + Phase 2 JSONL + monitored_pools.json)
- **Network bytes out**: 0 (no external APIs)
- **Live data freshness**: bounded by L3 SQLite sync cadence (D-005) — typically <1h lag from live chains for the surveillance corpus

### 3. Phase 4 reiteration trigger

**When monthly Alchemy CUs reset OR bloxroute is wired up, REVISIT the stochastic lens AND consider augmenting (not replacing) it with a real price feed.**

Specifically:
- **Trigger**: Phase 4 begins (per D-020, bloxroute migration is the Phase 4 boundary) OR user-driven "CU budget restored" event
- **Action**: file D-NNN_stochastic-lens-phase-4-augmentation documenting:
  1. The price-feed source (bloxroute pending TX stream, restored Alchemy WS, or another)
  2. Whether the new feed REPLACES the org_transfer_events grounding or RUNS ALONGSIDE it (running alongside is preferred — it lets cross-lens conflict detection notice when price-volatility and flow-volatility disagree)
  3. Updated signal-type taxonomy if the new feed enables genuinely new signal types
  4. Updated thresholds (price-derived volatility scales differently than flow-derived)
- **Don't do this in Phase 3.2/3.3/3.4** — bloxroute is deferred per D-020 and the existing lenses produce enough signal for the orchestrator + synthesis work that defines Phases 3.3 + 3.4

### 4. Information lens data-volume caveat

`liquidity_events` produces ~60 events/day across our token set. A 1-hour window may have only 2-3 events. Entropy computed on tiny samples is noise-dominated. The lens will:
- Compute entropy over a **rolling 7-day window per pool** (gets us ~400 events) instead of per-hour
- Emit `entropy_drop` / `divergence_spike` signals when the current window's entropy deviates meaningfully from a baseline window
- This is appropriate for a slow-moving signal; Phase 4's potentially-faster data source can shorten the window

## Acceptance evidence (to be backfilled at sub-phase 3.2 close-out)

Pre-staged; D-024 active before code. Evidence comes from:
- Sub-phase 3.2 smoke run (1 hour, per user direction)
- Signal density per lens
- Verification that `engine/scripts/run_engine_phase_3_2.py` (to be written) opens **only** local files (no `requests`, `httpx`, `web3`, `aiohttp` imports — to be lint-checked)

## Reversal triggers

- A future analysis attests that flow-derived volatility is qualitatively different from price-derived volatility in a way that makes the stochastic-lens signals meaningless → keep the lens but rename signal types in a D-NNN to reflect the flow grounding (e.g. `flow_volatility_shift` instead of `volatility_regime_shift`)
- Information lens entropy computations turn out to be too noisy at the 7-day window scale → narrow to 1-day or extend to 30-day in a D-NNN
- bloxroute migration happens earlier than Phase 4 (user-pulled forward) → fire the Phase 4 reiteration trigger immediately at that point

## Links

- D-020 (Phase 3 spec approval; bloxroute deferred to Phase 4)
- D-021 (signal schema locked) — the contract these lenses emit against; reinterpretation does NOT change the schema, only the grounding
- D-022 (event bus implementation) — the transport
- D-023 (GraphLens v1 / UNK-013 resolution) — precedent for adapting lens grounding to actually-available L3 data
- UNK-013 (L3 ↔ monitored-set disjointness on classification keys) — the prior lesson driving the "diagnose data first" discipline
- I-3 (read-only against Layer 3) — preserved by this decision
