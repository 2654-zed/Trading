# Briefing: On the Two Documents You Sent

**Headline: the two things you sent describe opposite games, and one contradicts the other.** Your instruction (A) was to abandon the dried-up two-token arbitrage surface and pivot to cross-sectional / factor work across many tokens at mid-to-long horizons. The MEV-engine doc (B) proposes building *exactly the two-token arbitrage engine that (A) tells me to abandon.* These are not two angles on one project — they share zero data, zero infrastructure, and zero required skill. Worth being precise about that before anyone spends money in the wrong one.

---

## 1. The MEV-engine doc (B): right mechanism, wrong game for us

The doc is **technically competent and describes a real strategy.** Atomic backrunning exists; the constant-product (x·y=k) price-impact math is exact; cross-DEX spreads do open after large swaps; flashloans and bundles work as described. None of that is hand-waving.

**But it mislocates the edge.** It treats *seeing the catalyst and being fast* as the binding constraint. On modern Ethereum (post proposer-builder separation), ordering isn't won by a microsecond footrace — it's sold in an **off-chain sealed-bid auction.** Everyone with a node sees the same pending swap; the winner is whoever extracts the most value and **bids the most of it back to the builder as a bribe.** Speed gets you *into* the auction; it doesn't win it. "Win the microsecond window" is a pre-2021 mental model of a world that was deliberately redesigned out of existence ~5 years ago.

**Our own measurements are the empirical refutation of the doc's thesis:**
- Only **8.7%** of public swaps get a verified backrun (vs. 0% null) — real but **sparse.**
- Median realized backrun PnL is **~$0; p90 ~$1k — before the bribe.** The bribe is the auction's clearing price; it competes that edge to ~zero for a marginal participant.
- **41–52% of mined transactions never appear in the public mempool.** The most valuable whale flow routes privately *specifically so it can't be backrun.* The doc's engine watches the public mempool — the adversely-selected leftover. We'd be shown only the opportunities smarter, better-connected players judged not worth hiding.
- Public actors win the visible backruns at least as often as private ones — a commodity game competed flat, not a winnable footrace.

The x·y=k simulation is **sound but commoditized** — every off-the-shelf bot and every builder's in-house searcher computes the identical number, so it confers zero edge. The real moat is capital, colocation, builder relationships, and private orderflow — none of which exist on a home machine with a public feed. (Minor tell: the doc cites Geyser, which is Solana, alongside Erigon/Reth, which are Ethereum.)

**Verdict on (B): true mechanism, not viable for our position — and your own instruction (A) already told us to drop it.** Recommend we decline it, on the evidence, not on opinion.

---

## 2. The cross-asset factor pivot (A): legitimate domain, different data, real traps

Unlike (B), this is a **real, published, institutionally-traded domain.** Honest map:
- **Carry** (perp funding, spot-futures basis) — the strongest, most accessible member: a directly-observed, mechanically-paid cashflow, not a statistical artifact. Best starting point.
- **Cross-sectional momentum** (top ~50–200 liquid tokens) — the most robust documented crypto factor, but decays sharply after costs and is regime-dependent.
- **Size / liquidity** factors — real, but mostly an *illiquidity premium*: you get paid because you *can't* trade them at size. That's the trap, not the edge.

**Critical for budgeting: this needs different data than the mempool work, and the mempool work does not transfer.** Factor investing runs on daily-to-weekly horizons across a token cross-section; the capture pipeline is a microsecond, single-block, two-token dataset. Wrong axis entirely — roughly **5% carries over** (an execution-cost prior), **95% is a new build.** Pivoting to (A) is starting a genuinely new project; the mempool spend does not amortize into it.

**The traps that specifically kill retail crypto factor backtests:**
1. **Survivorship bias — the #1 killer.** Most tokens alive in 2021 are dead or down 95–99%. Build a universe from *today's* listed tokens and backtest backward and you've silently deleted every token that went to zero — manufacturing spectacular fake returns. Needs point-in-time constituents with delisting dates. This bias alone routinely flips a "+40% CAGR" backtest to negative live.
2. **Illiquidity / capacity.** The premia live in the long tail where size can't trade. Depth-aware slippage frequently eats the entire gross premium. Expect a low capacity ceiling — often single-digit millions.
3. **Multiple testing.** 20 factors × 8 lookbacks × 5 universes → several show t-stats > 3 *by chance.* This is the exact failure mode behind our six prior out-of-sample nulls, now with more knobs.
4. **Factor decay / crowding, and BTC-beta in costume.** Anything computable from free daily data, funds computed years ago; most "factors" are a levered long-BTC bet unless orthogonalized to BTC/ETH.

**The most valuable thing we already own isn't code or data — it's the discipline:** the sealed-holdout, out-of-sample gate where a documented NO-GO is an honest deliverable. That transfers 100%.

---

## 3. Recommendation + concrete next step

**Decline (B). Pursue (A) as a small, gated experiment — not a build.** (A) suits our position because its barriers are a *clean dataset* (a money problem your capital can solve) and *statistical honesty* (a discipline we've repeatedly demonstrated) — not colocation and builder relationships we'll never have.

Equally honest on the upside: the realistic outcome of disciplined, survivorship-corrected, cost-aware testing is a **small, real, capacity-limited carry/momentum tilt — or another clean null.** Both are legitimate. More likely a modest tilt than a predictive-math jackpot; be primed for that.

**The experiment, pre-registered:**
1. **You fund one clean dataset:** point-in-time, survivorship-free, delisting-aware OHLCV + liquidity/depth for a broad universe, plus funding rates. Realistic cost **~$500–$3,000/mo** (Kaiko / Amberdata / CoinAPI tier). *That dataset is the ballgame* — a real line item, not a download.
2. **I pre-register two factors before looking at returns** — **carry first**, then liquidity-screened top-N momentum — with the universe filter + depth-aware cost model defined up front.
3. **I run them through the same sealed-holdout, out-of-sample gate** that produced our prior nulls. Survive fees + OOS → something real. Don't → we spent a dataset subscription instead of a multi-month engineering effort to learn the truth.

**One honest bridge** between old and new: the mempool feed we already pay for can become a *candidate feature* — e.g., private-vs-public flow-share shifts over days — fed *into* the factor test at a slow horizon where our lack of latency doesn't matter. Optionality on sunk cost, not a second strategy, not a promised edge.

**Bottom line:** I'm not asking you to take my verdict on faith — I'm asking to run the test you actually asked for. Fund the dataset, and the experiment tells us, at low cost and on a sealed holdout, whether the math is real. Either way you get a true answer instead of a story.
