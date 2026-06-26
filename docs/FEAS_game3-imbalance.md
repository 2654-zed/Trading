# FEAS — Game 3: order-book imbalance → short-horizon move (the original question)

> The **core Game-3 hypothesis** — does order-book imbalance ("price pressure")
> predict the short-horizon price move — run through a day-blocked IS/OOS test on
> Tardis `book_snapshot_25`. Dated 2026-06-20. Repro: `engine/scripts/game3_imbalance.py`.
> Related: [FOCUS.md](../FOCUS.md) · [FEAS_liquidation-cascade.md](FEAS_liquidation-cascade.md) · [DATA.md](DATA.md)

## Verdict: NO-GO for our position — the signal is REAL but lives inside the fee
Order-book imbalance **genuinely predicts direction and the sign holds out-of-sample** —
this is not a null result. But the edge is **~10–25× smaller than a taker's fees**, so it
is **untradeable from our seat** (taker, public data, no queue priority). It is a
**maker-only** edge.

## What was measured
- **Feature:** `OBI(t) = (Σ top-10 bid amount − Σ top-10 ask amount) / (Σ bid + Σ ask)`.
- **Target:** signed forward **mid-price return** at horizons 1 / 5 / 30 / 60 / 300 s (bps).
- **Split:** day-blocked — IS = earliest 4 days, OOS = latest 2 (`book_snapshot_25`, BTCUSDT
  binance-futures, 6 days incl. stress + calm).
- **Cost model:** BTC perp spread ≈ **0.01 bps** (negligible) + **taker fee ~4 bps/side →
  ~8 bps round-trip** (the real wall).

| horizon | IS corr | OOS corr | hit% | edge/side (extreme-decile) | **net of ~8 bps taker cost** |
|---|---|---|---|---|---|
| 1s | +0.23 | +0.25 | 58–59% | ~0.3–0.4 bps | **−7.6** |
| 5s | +0.18 | +0.17 | 60% | ~0.5–0.6 bps | **−7.4** |
| 30s | +0.09 | +0.08 | 54–56% | ~0.7 bps | **−7.3** |
| 60s | +0.07 | +0.06 | 53% | ~0.6–0.8 bps | **−7.3** |
| 300s | +0.03 | +0.02 | 51% | ~0.6–0.7 bps | **−7.4** |

The signal is real (corr decays smoothly with horizon, sign-stable IS→OOS, 58–60% hit at
short horizons — textbook imbalance behavior). It is also **far below cost at every horizon.**

## Why it's maker-only (and we're a taker)
A **maker** harvests this by *posting* a limit order on the heavy side — earning the
spread / fee rebate and capturing the predicted drift — which needs **queue priority
(colocation/low latency)** and **maker economics**. A **taker** must *cross* the spread and
pay the taker fee both ways (~8 bps), which annihilates a ~0.5 bps edge. We are explicitly
a taker on public data with no colocation/queue priority → **not our game.**

## Caveats (kept honest)
First cut: 6 days (3 stress + 3 calm), taker-cost frame, and the "edge/side" is the
**optimistic extreme-decile** spread (trading every signal is smaller). The verdict is
**robust to the fee assumption** — even an optimistic VIP taker fee (~1.5–2 bps/side →
3–4 bps round-trip) still dwarfs the ~0.3–0.8 bps edge. A maker/queue-priority frame is a
different, infrastructure-heavy game outside our position.

## The pattern this completes
Same structural result as the rest of the effort — **every edge we find is *positional***:
- on-chain MEV / backruns → won in the builder latency auction (not ours);
- approval drains → detectable but can't win the block (not ours);
- liquidation cascades → a vol nowcast, no tradeable acceleration lead;
- **order-book imbalance → real & OOS-stable, but inside taker fees / maker-only (not ours).**

The durable micro-edges in crypto are **positional, not informational**: they require a
privileged seat (maker status, colocation, builder access) we don't have. Our position can
**see** them but can't **capture** them.

## Decision
Game 3's core question is **answered: NO-GO for our position.** The honest next move is to
**stop the offensive microstructure/latency thesis entirely** (every branch — MEV, drains,
liquidations, imbalance — lands the same way) and, if anything is pursued, **pivot to where
our position is NOT structurally disadvantaged**: the deferred **factor / carry** work
(mid-horizon, survivorship-controlled, not latency- or fee-bound) — see
[ARCHIVE.md](ARCHIVE.md) and `engine/research_loop/factor_*`. That's the one direction left
whose edge isn't gated by a seat we don't occupy.
