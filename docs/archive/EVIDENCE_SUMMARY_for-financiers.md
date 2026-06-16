# Trading Strategy — Evidence Summary & Capital Position

**Prepared for:** financing partners
**Date:** 2026-06-03
**Status:** detection / research only — no capital deployed, no trades executed

---

## Summary

Over the course of this research we have tested **six independent approaches** to finding a tradeable price edge — including the most recent "predictive math" method proposed by the team. **Every one was tested out-of-sample, on real market data, at zero incremental cost, and none produced a robust, replicable edge.**

This is not a statement of pessimism — it is the result of disciplined testing. The responsible position follows directly from it: **we do not commit capital to a signal until it passes an out-of-sample test. To date, none has.**

---

## How each result was produced (why they are trustworthy)

Every approach was held to the same standard:

- **Out-of-sample.** Each signal is fit/observed on one slice of history and tested on a *separate, later* slice it never saw. A result only counts if it holds the **same sign** in-sample and out-of-sample. This is the single most important guard against the universal failure mode — a pattern that looks great on the data it was found in and evaporates on new data.
- **Real outcomes.** Realized forward price returns, not proxies.
- **Costs included** where applicable (fees, slippage, gas).
- **Zero incremental spend.** All tests used free public data; read-only; no capital at risk.

---

## The scorecard

| # | Approach tested | Out-of-sample result |
|---|---|---|
| 1 | **Multi-lens analytical engine** | Never beat its own best single component; underperformed it by 11–17 points out-of-sample. |
| 2 | **Behavioral signal on tokens** (lead candidate) | Promising on historical data; **live forward paper-trading is currently negative** (27% win rate, −2.5%/trade, no winning tail trades). |
| 3 | **Ecosystem activity → ETH** | No predictive correlation; features were near-zero or reversed sign out-of-sample. |
| 4 | **Prediction markets (Polymarket / Kalshi)** | No overlap with our asset universe; coverage is BTC/ETH-only, dominated by politics/sports. |
| 5 | **Perpetual funding rates → ETH** | Well-powered null (173 independent windows); no funding feature predicted forward returns. |
| 6 | **Proposed "predictive-state" metric** | Built the backtestable core and tested it on 180 days of ETH: **zero predictive correlation (+0.006 in-sample)**; a plain distance metric matched it, meaning the advanced framing added nothing measurable. The only signal present was ordinary momentum — a long-known, already-competed effect. |

---

## On the most recent proposed method (#6)

We took the proposed pseudocode seriously and did exactly what was asked: extracted its testable core, implemented it, and backtested it out-of-sample on a large, multi-regime ETH sample.

- The metric's **in-sample** correlation with forward returns was **+0.006** — effectively zero. A metric that cannot correlate even *in-sample* (with hindsight) has no relationship to fit; the result does not depend on implementation choices.
- A trivial control (a plain distance, with none of the advanced structure) performed identically — so the specialized framing contributes nothing measurable.
- The remainder of the proposed system relies on **live-execution inputs** (mempool state, latency, gas competition) that exist only at the moment of trading and **cannot be backtested at all.** Validating that half is not a backtest — it requires standing up a live, capitalized execution operation, which is the precise risk we are declining to take on faith.

We remain open to any method that can demonstrate the one thing that matters.

---

## Position and what would change it

**We will deploy capital when — and only when — a strategy demonstrates a positive, out-of-sample, after-cost result.** That bar is not unusual; it is the minimum any serious allocator applies to their own money.

The burden of proof rests with the method, not the tester. A genuine edge can produce an out-of-sample backtest with realized P&L in an afternoon. Six approaches have now been put through exactly that test — including the team's own — and the answer has consistently been *no robust edge.*

One live experiment (the behavioral token signal, #2) continues to accumulate forward evidence at zero capital risk. If it turns positive and survives costs, it becomes a candidate. Until something clears the bar, committing capital would be betting against our own repeated, rigorous findings.

---

*This summary is backed by reproducible test scripts and a full technical record. Methodology and raw results available on request.*
