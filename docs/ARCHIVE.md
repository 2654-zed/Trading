# ARCHIVE — what was tried and set aside (and why)

Kept out of [FOCUS.md](../FOCUS.md) so the focus doc stays current. This is the
graveyard + the decision record, so we don't re-litigate settled questions or
re-walk dead ends. Newest first.

## Decided against / retired
- **On-chain MEV — backrun & triangular arbitrage (Game 2).** *Measured, not
  guessed, uneconomical for our position.* Backruns: ~9% of public swaps, **median
  realized PnL ~$0** (p90 ~$1k *before* the builder bribe, which takes most),
  ~41–52% of flow private. Triangular/cyclic arbs: **<1% of transactions**, mostly
  V4 (undercounted). The value is won in a sealed builder auction by faster,
  better-funded, private players; a public-feed actor submits last. See
  [BRIEFING](../handoff/BRIEFING_mev-engine-vs-factor-pivot.md).
- **Factor alpha (carry / cross-sectional momentum).** Legitimate domain, harness
  built + tested on synthetic data; **deferred** in favor of Game 3 by team
  decision. Needs a survivorship-free dataset (~$50–150/mo) — spec at
  [DATASET_SPEC](../handoff/DATASET_SPEC_factor-test.md). Re-openable if Game 3 stalls.
- **Layer-3 behavioral-label trading edge.** Retired — L3's own retrospective
  declared the behavioral labels unreliable; that was the root cause of the
  trading nulls below.

## The six out-of-sample nulls (the evidence the public edge is dead)
All returned null/negative OOS, establishing that accessible public-price alpha is
arbitraged away: multi-lens engine, H9 (`entropy_drop`), froth→ETH, prediction-market
fit, perp-funding→ETH, and the 8D-Minkowski steelman. The recurring lesson:
**shape ≠ intent; aggregation buries signal; sign must hold IS→OOS.**

## Surviving asset from the dead trading project
The **mempool forensic pipeline** (capture + ghost-tx join + analysis) — built as a
$0-capital observational dataset. Its value is *market-structure measurement*
(private-orderflow footprint, 7702 adoption, backrun/triangular prevalence), **not a
trading edge**. Lives at `engine/bloxroute/` + `engine/data/` (data not in git).

## Why the latency games were ruled out (standing constraint)
No colocation, no builder/relay relationships, no exchange-maker status. Every
strategy whose edge is *speed* (atomic MEV, order-book HFT) is structurally
unwinnable from our setup. This is why the chosen direction (Game 3 microstructure
*study*, and the deferred factor work) are framed as **research questions answerable
on recorded data**, not live latency races. See [DECIDE](../handoff/DECIDE_pick-one-game.md).

## Source documents
The original charters, phase specs, lab reports, and financier-correspondence drafts
behind the narrative above are preserved (as written) under
**[docs/archive/](archive/README.md)** — moved out of the repo root to keep it clean.
