# Decision needed: pick ONE game

We're circling three different strategies at once. They share **no data, no
infrastructure, and no skill set** — every time we discuss a new one we need to
buy different data and build different code. **Before any more spend, we need to
pick one.** Here they are, with the honest odds *for our position* (a small team,
public data feeds, no colocation, no builder relationships, no exchange-maker status).

---

### Game 1 — Factor alpha (carry + momentum, mid/long horizon)
- **What:** hold a basket of tokens for days/weeks based on signals (funding carry,
  cross-sectional momentum). No speed race.
- **Data needed:** survivorship-free historical OHLCV + funding (**~$50–150/mo**).
- **Our odds:** **Best fit.** The barriers are a clean dataset (money solves it) and
  statistical honesty (we've shown we have it). Realistic outcome: a small, real,
  capacity-limited edge — or a clean, documented "no." **The test harness is already
  built and waiting on the one data purchase.**

### Game 2 — On-chain / triangular arbitrage (DEX MEV)
- **What:** atomic backruns and 3+ pool cycles on Uniswap-style DEXs.
- **Data needed:** a live pool-reserve indexer + block-builder bundle access.
- **Our odds:** **Measured, not guessed — and it's no.** We found backruns are ~9% of
  swaps, **median ~$0 profit, mostly paid to builders**; triangular arbs are **<1% of
  transactions**. The value is won in a sealed builder auction by faster, better-funded,
  *private* players. A public-feed actor submits last and captures ~nothing.

### Game 3 — CEX order-book microstructure (Level 2 / "price pressure")
- **What:** trade off the order-book ladder (bid/ask depth, imbalance) on a centralized
  exchange — the classic TradFi-style approach.
- **Data needed:** a centralized-exchange **L2 order-book feed** (exchange websocket / Tardis).
  *None of our current data is this.*
- **Our odds:** **The most HFT-saturated game of all.** Order-book signals decay in
  milliseconds and are owned by colocated market makers with zero fees and rebates. Same
  structural latency disadvantage as Game 2 — except now we'd be paying taker fees against
  the people who *are* the market.

---

## Recommendation
**Game 1.** It's the only one whose barrier is something our money and discipline can
actually clear, rather than infrastructure (colocation, builder ties, exchange-maker
status) we will never have. Games 2 and 3 are real strategies — for HFT firms, not for us.

## The decision
**Which game do we commit to?** Each is a different data buy and a different build:
- **Game 1** → fund the ~$50–150/mo factor dataset; we run the pre-registered test this week.
- **Game 2** → fund a node + indexer + builder relationships (six figures of infra to *maybe* break even).
- **Game 3** → buy a CEX L2 feed and accept we're racing market makers.

We can do *one* of these well. Picking is now the bottleneck — more than any data question.
