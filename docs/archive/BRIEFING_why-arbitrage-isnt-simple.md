# Why "Just Find Price Discrepancies and Act on Them" Isn't Simple

**Audience:** financiers / partners evaluating the trading strategy
**Date:** 2026-06-02
**One-line:** Detecting a price discrepancy is trivial; profiting from one is a different problem that this project has *already empirically tested and found unviable* at our scale.

---

## The intuition — and why it's the most expensive idea in trading

"Two venues show different prices for the same asset — just buy low, sell high." It sounds obvious. That obviousness is exactly the problem: if it were that simple, everyone would do it, the buying/selling would erase the gap, and there would be nothing left. The persistence of any visible discrepancy is evidence it **can't be profitably captured** — not that it can.

Below is why, in four parts, followed by the decisive fact: **we already ran this experiment with the fund's money, and it returned "no."**

---

## 1. A visible discrepancy is a GROSS number, not profit

To capture a price gap you pay a stack of costs, and the gap must clear **all** of them:

- **Swap fees** — each DEX pool charges a fee per trade (commonly ~30 bps). You trade twice (buy on A, sell on B) ≈ **60 bps** gone.
- **Slippage / price impact** — the act of trading moves the price against you. Your buy pushes the cheap side up; your sell pushes the expensive side down. The gap *shrinks as you trade it*.
- **Gas** — every transaction costs gas; a reverted transaction costs gas for nothing.
- **Bridge fee + latency** (cross-chain) — moving capital between chains costs a fee and takes seconds-to-minutes, during which the price moves.

### Worked example

WETH shows **$2,500 on DEX A** and **$2,505 on DEX B** — a 0.2% (20 bps) gap that looks like free money on a $10,000 trade:

| Cost component | ~bps |
|---|---:|
| Buy fee (DEX A) | 30 |
| Sell fee (DEX B) | 30 |
| Slippage (both legs) | 10–30 |
| Gas | a few |
| **Total cost** | **~70–90 bps** |

The gap was **20 bps**. Acting on it loses **~50–70 bps**. To *profit*, you need gaps **bigger than ~1%** that also **persist** long enough to act on — and those are vanishingly rare, for the reasons below.

---

## 2. You're not the only one looking — you're last in line

On-chain arbitrage is one of the most saturated, infrastructure-heavy activities in crypto. The actors who actually capture discrepancies are **MEV searchers**:

- co-located, custom-built bots,
- funded by flash loans (no capital required),
- executing as **atomic bundles** (the whole arb succeeds or reverts as one transaction),
- submitted through **private orderflow** (Flashbots, bloXroute) within the **same block** the gap appears.

By the time a discrepancy is visible to a normal participant, it has already been taken — milliseconds to one block earlier. **You see the stale price; the real price already moved.**

### The winner's curse (adverse selection)

This produces the trap that sinks most arbitrage efforts: the discrepancies *still available* when you arrive are disproportionately the ones the professionals **declined** — because they are:

- **fake** (a stale quote or lagging oracle),
- **un-exitable** (the "cheap" token has no liquidity to sell back into),
- or a **deliberate trap**.

**If you get filled, it is often precisely because someone faster and smarter looked at the same trade and passed.**

---

## 3. Cross-chain makes it strictly worse

If the proposal means cross-*chain* (e.g. a price difference between Base and Arbitrum), the difficulty increases:

- You must **pre-position capital on both chains**, or bridge mid-trade.
- **Bridges take seconds to minutes** — an eternity in on-chain time. The gap that existed when you started has moved or inverted by the time your capital lands.

This is not hypothetical: this project's cross-chain detector, monitoring Base / Arbitrum / Optimism continuously, caught **only one ~1-hour opportunity burst in 49 days.**

---

## 4. The decisive fact: we already tested this — with the fund's capital

This is not theory. **Phase 1 of this project did exactly what is being proposed** — it built a detector to find cross-chain / cross-DEX price discrepancies and measured how many were *exploitable after realistic costs*. The result, from our own data:

- **Hypothesis H1 — "there are enough exploitable opportunities per day to trade" — was INVALIDATED.** At realistic cost floors, exploitable discrepancies were **near zero**.
- The single "persistent arbitrage" we found was one bursty pool, open ~6–8 hours roughly every ~5 days — not a strategy.

**The entire reason this project moved on** — from naive arbitrage to a multi-lens analytical engine, and then to a *predictive* signal — is that "just find discrepancies and act on them" was already measured and **did not survive contact with reality.** Re-running it would re-spend money to re-learn the same answer.

---

## The real distinction: detection is easy, EDGE is hard

Anyone can *detect* that two prices differ. Having an **edge** — a reason your trade is net-profitable after costs and competition — requires one of exactly two things:

1. **A speed / orderflow moat** — co-location, private orderflow, atomic execution. This is the MEV arms race. It costs millions to compete and is dominated by specialized firms; a fund our size enters it as prey, not predator. (Note: even buying a feed like bloXroute only rents a sliver of this stack — and it doesn't cover Arbitrum/Optimism, where our limited signal appeared.)

2. **An informational / predictive edge** — the ability to anticipate a move *before* the discrepancy appears, rather than racing to capture it after. This is the **only** category open to a participant without a speed moat.

This project's current candidate signal (internally "H9") is an attempt at category 2 — predicting forward price moves from on-chain behavioral data. It showed promise on historical data, but its live, out-of-sample paper-trading record is **currently negative** (down over the last two weeks, no winning tail trades yet). That is the honest, real-time reason not to deploy capital today — not caution for its own sake, but because the one edge we have is not, at this moment, demonstrating itself.

---

## One-paragraph summary

> Finding a price discrepancy is trivial — anyone can see two prices differ. Profiting from one is a different problem, because the gap must beat swap fees, slippage, gas, and bridge costs (~70+ bps round-trip), **and** you compete against co-located MEV bots that take any real gap within the same block. What's left for a normal participant is the gaps the professionals declined — which are fake or traps (the winner's curse). We don't merely believe this: **Phase 1 of this project tested exactly this strategy and measured near-zero exploitable opportunities, which is why we pivoted to a predictive signal.** Pure arbitrage requires a speed/orderflow moat that costs millions and still loses to incumbents. The only viable path for a fund our size is a predictive informational edge — which is precisely what we are paper-trading and validating right now, and which we will fund with real capital only once it proves itself out-of-sample.

---

*Supporting evidence in this repository: Phase 1/2 H1-invalidation (`memory/decisions/` D-007), the cross-chain burst-rarity finding (UNK-012), the multi-lens negative result (`LAB_REPORT_multi-lens-engine.md`, D-042), and the live H9 paper-trade record (`engine/data/paper_trade_cron.log`, UNK-016).*
