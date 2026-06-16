# D-045: Six-way OOS evidence scorecard + 8D-Minkowski steelman result

**Date**: 2026-06-03
**Made by**: agent (consolidation of the exploratory sweep + the financier-method backtest)
**Status**: ACTIVE

## Context

After Phase 3/4 (multi-lens engine falsified, D-042) and the H9 discovery (D-044, live OOS currently negative), a series of further angles were tested at the user's request, including the financiers' own proposed method (the "8D Minkowski predictive engine" in `Predictive Math_.pdf`). This decision consolidates the full evidence base and records the steelman backtest of the financiers' method.

## The six-way scorecard (all OOS-tested, $0, read-only)

| # | Approach | Result | Ref |
|---|---|---|---|
| 1 | Multi-lens orchestrator (H5) | NO-GO — never beats best single lens; real-outcome OOS −11 to −17pp | D-042 |
| 2 | H9 `entropy_drop` on memecoins | in-sample promising; **live OOS negative** (27% win, −2.5%/trade, −$6,444 / ~15d) | D-044, UNK-016 |
| 3 | Ecosystem froth → ETH | null (features near-zero or sign-flip OOS) | engine/data/diag_froth_eth |
| 4 | Prediction markets (Polymarket/Kalshi) fit | no overlap with monitored universe; BTC/ETH-only | engine/scripts/diag_prediction_markets |
| 5 | Perp funding → ETH | **well-powered null** (n=173; all features near-zero / sign-flip OOS) | engine/scripts/diag_funding_eth |
| 6 | **8D-Minkowski steelman → ETH** | **Minkowski interval IS corr +0.006 (zero); Euclidean control matches it; only plain momentum showed a weak, known effect** | engine/scripts/diag_minkowski_eth |

## Finding #6 detail (the financiers' method, fairly steelmanned)

The "8D Minkowski engine" reduces to a Minkowski-signature interval `M = -(d1²) + (d2²+...+d8²)` over feature deltas. Built the most legitimate backtestable version: 8-feature ETH market-state vector (momentum/vol/accel/volume/range/funding/funding-change), z-scored vs trailing baseline, their exact signature, 180d Hyperliquid data, non-overlapping 24h/48h, OOS split.

- **Minkowski interval: IS corr +0.006 / +0.003** — essentially zero *in-sample*, i.e. no fittable relationship to returns even with hindsight (so the result is robust to feature choice).
- **Euclidean control matches it** (~zero) → the Minkowski signature / "spacetime" framing adds nothing measurable over a plain distance.
- **Momentum-alone control** is the only flicker (OOS −0.32 @24h) — a trivial, known mean-reversion effect requiring none of the apparatus.
- The system's remaining inputs (mempool, latency, gas, simulation) are **live-execution-only and not backtestable** — which is itself the point: the un-backtestable half is the expensive "run a live MEV operation" commitment.

## Decision / position

**No capital is deployed against any signal that has not passed an out-of-sample test. None has.** The detection-only charter (I-1/I-3) and the withheld execution authorization (D-037) stand. The bar for capital is a passing OOS backtest with realized P&L; the burden of proof is on any proposed method (including the financiers') to provide a specific, reproducible feature set + OOS result — not architecture or vocabulary.

## Honest scope

The exploratory sweep tested SIMPLE, LINEAR, public-data versions of each idea. A non-linear / conditional / exclusive-data edge is not ruled out — but six independent simple tests returning null is strong evidence that no *easily accessible* edge exists for a team this size, consistent with market efficiency. The one live thread (H9 paper trade) continues to accumulate as the last open question.

## Links

- D-042 (multi-lens NO-GO), D-044 (H9 validated-then-live-negative), UNK-016
- `LAB_REPORT_multi-lens-engine.md`, `BRIEFING_why-arbitrage-isnt-simple.md`
- External: `Predictive Math_.pdf` (financiers' method, tested in finding #6)
- Scripts: `engine/scripts/diag_{froth_eth,prediction_markets,funding_eth,minkowski_eth}.py`
