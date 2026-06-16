# Reading the Data — Crypto vs. TradFi (for the new engineer)

You're coming from equities/TradFi, where there's one mental model: a **central limit
order book (CLOB)** — resting bids and asks stacked at price levels, and a tape of
market orders hitting them. Your instinct ("if price is 100, how many are bidding 99 /
asking 101, and can I see the volume before the price moves?") is exactly the right
question *in that world*. The catch: **crypto has two completely different worlds, and
they need different data and different code. The dataset we sent is from the second one.**

---

## The one distinction that explains everything: CEX vs. DEX

**1. Centralized exchanges (CEX) — Binance, Coinbase, OKX, Bybit.**
These work just like a stock exchange. There **is** a real limit order book: resting bids
at 99 (size X), asks at 101 (size Y), the full depth ladder, plus a trade tape. Your TradFi
instincts transfer almost 1:1. You get this data from **the exchange's own Level-2 websocket
API** (or a vendor like Kaiko/Tardis that records it). 

**2. Decentralized exchanges (DEX / AMM) — Uniswap, Aerodrome, Curve, etc.**
These are *not* order books at all. There are **no people resting orders at 99 and 101.**
Instead, each market is a **liquidity pool** governed by a math formula (the constant-product
"bonding curve," `x · y = k`). The price is just a function of the pool's two reserves. When
someone trades, they push along the curve and the price moves continuously. **The mempool
data we sent you is from this world.**

So your question, answered for each:

| Your TradFi question | CEX answer | DEX / AMM answer |
|---|---|---|
| "How many are bidding 99 / asking 101?" | Yes — read the L2 order book; sizes are right there. | **Nobody is.** There's no order book. Liquidity is a curve. The closest analog: **Uniswap V3 "ticks"** — LPs park liquidity in price *ranges*, so "how much sits between 99 and 101" = the liquidity in those ticks. You read it from **on-chain pool state**, not an order book. |
| "What's the bid/ask spread?" | A posted spread between best bid and best ask. | No posted spread. The effective cost = **pool fee tier (e.g. 0.05%/0.30%) + slippage** (how far your own trade pushes the curve). Bigger trade → worse price. |
| "Can I see volume *before* the price moves?" | You see resting orders + incoming market orders on the tape. | **Yes — that's the mempool.** Pending swaps sit briefly in the mempool *before* they execute and move the pool. That "about to happen" stream is the DeFi analog of order flow — and it's what our capture records. **Two big caveats below.** |

---

## The two caveats on "seeing volume before it moves" in DeFi

This is the part that matters most, and it's where the data we sent both helps and limits you:

1. **The mempool is a queue of incoming *market orders*, not resting *limit orders*.** It's a
   waiting room of "I'm about to swap X for Y" intents — not a book of passive bids/asks at
   price levels. You can see the catalyst that's about to move price; you cannot see a ladder
   of resting demand at 99/101 (that concept doesn't exist on an AMM — the nearest thing is V3
   tick liquidity, which is pool *state*, read on-chain, not in the mempool).

2. **~41–52% of the volume that actually moves the price is invisible to us.** The biggest,
   most informed trades route **privately** (straight to block builders, never hitting the
   public mempool) *specifically so no one can see them coming and trade ahead*. We measured
   this directly. So the public mempool shows you the **leftover** flow — the half that didn't
   bother to hide. Building on "we can see the volume before it moves" is only ~half true, and
   it's the less-informed half.

---

## Level 2 / depth-of-book and "price pressure"

You're right that in an order-book market, **L2 depth** (bid/ask sizes at each price level)
is *the* tool for reading price pressure. Here's the crypto translation, by venue type:

- **CEX (Binance, Coinbase) and CLOB DEXs (Hyperliquid, dYdX):** real L2 books exist exactly
  as you'd expect — the full ladder of bid/ask levels and sizes, updating live. **This is
  available.** Source: the exchange's own **L2 depth websocket** (free, real-time) or a vendor
  for history (**Tardis.dev** records full historical L2/L3 order books; Kaiko/Amberdata too).
  If you want order-book-imbalance / depth-based price-pressure signals, *this is your data and
  your TradFi instincts apply directly.*
- **AMM DEX, Uniswap V2:** there is **no order book and no L2.** Liquidity is a single
  continuous `x·y=k` curve; "depth" is just the pool's two reserves; price impact is a
  deterministic formula of trade size vs. reserves. No levels, no bid/ask sizes.
- **AMM DEX, Uniswap V3/V4:** the closest analog to L2 — concentrated liquidity sits in discrete
  price **ranges (ticks)**, so you *can* reconstruct a depth profile ("how much liquidity is
  between price X and Y"). **But** you read it from **on-chain pool state** (the contract's tick
  bitmap + liquidity-per-tick), not an order-book feed — and it's *resting LP liquidity*, not
  directional bids/asks. It tells you how far a trade pushes price (slippage), not how many
  buyers vs. sellers are queued.

**Key conceptual point: "price pressure" means something different on an AMM.** In an order
book it's bid/ask *imbalance* plus market orders eating levels. On an AMM there are no resting
directional orders to be imbalanced — price is mechanical from reserves. The AMM-native pressure
signals are: **(a) pool depth** (the V3/V4 tick profile, read on-chain) and **(b) pending swap
flow** (the mempool — net buy vs. sell volume about to land, which is what we capture, ~half of
it visible). There is no "bid size at level 2" to read on an AMM.

**Our handoff data contains none of this** — no bids, asks, levels, or reserves; only (partial)
pending-swap flow. To get true L2/depth you need either a **CEX order-book feed** or an
**on-chain pool-state (tick) indexer** — neither is in the dataset, and the latter is a separate
build.

## What the dataset we sent IS (and is NOT)

It is **forensic market-structure data** — a record of *who did what, when, and what happened
to it* — built from the public mempool ("before") joined to mined blocks ("after"):

- `block_features.csv/parquet` — per block: the **private-inclusion share** (how much of that
  block bypassed the public mempool), fee distribution, etc.
- `tx_outcomes.csv/parquet` — per pending tx: did it **land / get replaced / vanish**, how fast.

It is **NOT**, and cannot be turned into:
- ❌ a price feed or bid/ask quotes,
- ❌ an order book / depth ladder,
- ❌ pool reserves or live prices,
- ❌ anything you can compute an arbitrage from.

It answers questions like *"how contested is this, how much is private, is this game even
capturable from a public vantage?"* — **not** *"what's the price of ETH across these pools
right now?"* If you try to find trade signals *inside* it, you're using a thermometer to
measure weight.

---

## On triangular arbitrage

Triangular arb = cycle through three pairs (e.g. `USDT → BTC → ETH → USDT`, or on a DEX
`WETH → USDC → DAI → WETH`) and profit if the loop comes back to more than you started,
after fees. It's a real, classic strategy — but two honest points:

1. **It's the same atomic-arbitrage game, with more legs.** Three legs = ~3× the fees + 3×
   the slippage + more gas, so the price inconsistency has to be *bigger* to be profitable —
   which makes those opportunities **rarer and more contested**, not easier. On a CEX it's won
   by colocated HFT firms with zero fees; on a DEX it's the MEV/builder-auction game where the
   fastest, best-connected, *private* players win. We already measured the DEX version: the
   opportunities are sparse, the profit is mostly paid to block builders as a bribe, and a
   public-feed actor submits last and captures ~none.

2. **A triangular engine needs data we don't have and the mempool dataset doesn't contain.**
   To find a profitable cycle in real time you need the **live reserves/prices of every pool in
   every candidate triangle, continuously** — a live pool-state indexer (custom Erigon/Reth
   subscriptions). That's a separate, substantial build. Our forensic dataset has zero prices
   or reserves in it, so it can't drive a triangular engine.

---

## Bottom line / how to spend your energy

**First, pick your world**, because everything else follows:
- **CEX path** → your TradFi instincts mostly work. You need the **exchange's L2 order-book
  API** (or a vendor recording it). Triangular arb here is a latency/fee race against HFT.
- **DEX path** → there's no order book; you need a **live pool-reserve indexer** to compute
  prices/arbs, plus builder/bundle access to execute. This is the MEV game we've shown is not
  economical from a public feed without serious infrastructure.

Either way, **the dataset we sent is not the input to an arbitrage engine** — it's the
evidence base for *how contested and how private* this market is, which is why the recommended
direction is the slower, mid/long-horizon factor work (carry, momentum) that doesn't require
winning a microsecond race. Happy to walk through any of this live.
