# D-040: Free historical price data is sufficient for Phase 4 outcomes ($0 CU)

**Date**: 2026-05-28
**Made by**: agent (sub-phase 4.1 diagnostic, user "proceed")
**Status**: ACTIVE

## Context

Phase 4 (PHASE_4_REAL_OUTCOMES_SPEC.md) replaces the D-034 forward-flow proxy with REAL realized price/return outcomes, to give the first trustworthy H5–H8 verdict (the proxy showed no out-of-sample edge — D-039, UNK-015). The open question: can FREE historical price data cover our monitored set, or must we spend the scarce remaining Alchemy CU? The monitored set is mostly **Base memecoin pools** (BALD, BRETT, TOSHI, doginme, CLANKER…) paired with WETH/USDC — exactly where free coverage is usually weakest.

## Diagnostic (sub-phase 4.1, $0 CU)

Probed DefiLlama's free historical-price API (`coins.llama.fi/prices/historical/{ts}/{chain:addr}`, no API key) against all 88 unique monitored-set tokens at three points across the ~49-day replay window, with chain fallback (base → arbitrum → optimism):

| Timepoint | Coverage |
|---|---|
| start+10% | 82/88 (93%) |
| mid | 82/88 (93%) |
| end−10% | 82/88 (93%) |
| **union** | **84/88 (95%)** |

Only 4 tokens never priced — AIRDROP, TICKER, PRESALE, BEBE (dead/scam-named, negligible liquidity; their pools wouldn't yield meaningful outcomes regardless).

Forward realized-return computation verified end-to-end (price at T vs T+12h horizon): WETH +0.07%, BRETT −1.38%, TOSHI +1.42% — real, differentiated outcomes, exactly what the `RealizedOutcomeAttributor` needs.

## Decision

**Phase 4 outcome attribution will use DefiLlama free historical prices. $0 CU. The Alchemy CU reserve is NOT needed for Phase 4** and stays reserved for a possible high-fidelity confirmatory run only if the first-pass real-outcome H5 comes back borderline (per PHASE_4 §2).

- Realized outcome = forward return over [T, T+horizon] of the pool's volatile token (USD price; USDC≈$1 leg is the numéraire).
- Network access from the run environment confirmed working (read-only GET to a public API; no key, no on-chain calls — I-1/I-3 preserved).

## Consequences

- Sub-phase 4.1's data-source question is resolved GO. Phase 4 can be implemented at ~$0, contingent on user approval of the Phase 4 spec.
- Strengthens the financier message: "proving the edge costs ~$0."
- Caveat carried forward: DefiLlama point-in-time prices may smooth intra-window volatility; if the realized-return outcome proves too coarse to separate the orchestrator from the best lens (echoing the proxy problem), the scoped CU fallback (exact `eth_getLogs` Swap logs for signal-driving pools) is the remedy.

## Links

- PHASE_4_REAL_OUTCOMES_SPEC.md §2 (data strategy), §3 (sub-phase 4.1)
- D-034 (proxy being replaced), D-039 (why real outcomes are needed), UNK-015 (the question Phase 4 resolves)
- I-1/I-3 preserved (read-only; ledger is the only writable artifact)
