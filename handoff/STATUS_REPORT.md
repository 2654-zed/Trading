# Status Report — Mempool Forensic Dataset + Testing Apparatus

**Prepared for:** the financing/research partners
**What this is:** a real, labeled dataset to point your predictive math at — plus
the rigorous pipeline that will tell us, honestly, whether anything it finds
holds up out-of-sample. The goal of this handoff is to **stop trading theory for
theory** and start testing hypotheses against something concrete.

---

## 1. The short version

We built three things: a **live capture** of the Ethereum mempool (the "before"
/ intent side), a **block-confirmation ingest** (the "after" / outcome side), and
a **rigorous hypothesis-testing loop** that refuses to pass anything that doesn't
survive out-of-sample. This report ships with the captured data in
analysis-ready form.

What we have **not** found is a naive, tradeable edge in the *public* mempool —
and we now understand the structural reason (Section 4), which is itself the
most useful finding. What we **have** produced is a proprietary, outcome-labeled
dataset and a measurement of the one thing this data uniquely sees: the
**footprint of private orderflow**. That is the surface your math should attack.

---

## 2. What's in this package (`handoff/`)

| File | Rows | What it is |
|---|---|---|
| `block_features.csv` | **7,871 blocks** | one row per block: private-inclusion share + the microstructure features of the txs we saw pending |
| `tx_outcomes.csv` | **1,476,089 txs** | one row per observable pending tx: features + the resolved outcome (landed / replaced / ghost) + lead time |
| `DATA_DICTIONARY.md` | — | every column defined, with caveats |
| `coverage_manifest.json` | — | spans, gaps, coverage fraction, clock state |

Capture so far: **1,835,148 mempool rows · 2,428,351 confirmations**, spanning
**2026-06-09 → 2026-06-12** (~3.4 days, intermittent — see caveats). Outcome
breakdown of the labeled set: **89.0% landed · 7.1% true-ghost · 3.9% replaced.**

This is not a price feed. It is event/microstructure data: *who broadcast what,
when, with what fee, and what happened to it.* That is exactly the kind of
substrate a structural-edge model needs and almost nobody has cleanly labeled.

---

## 3. What the data already shows (so your math doesn't re-derive the obvious)

- **The mempool is mostly payments.** ~80% of captured flow is plain ETH / ERC-20
  transfers into stablecoins (USDT, USDC); median priority tip ≈ **0.005 gwei**.
  Most participants are not competing for ordering.
- **The visible competitive arena is small and cheap.** After removing organic
  volume, genuine bidding tops out around ~11–30 distinct racers on ~20–26
  contested targets, clearing at ~2 gwei. Real but thin.
- **EIP-7702 (account-abstraction) is live but small** — ~1% of the type-aware
  captures, all carrying authorizations.
- **The headline structural fact (Section 4).**

## 4. The structural finding that should reframe the whole effort

**~41–52% of mined transactions never appear in the public mempool at all.** The
majority of what actually lands in blocks bypasses the public feed entirely —
that is the observable shadow of **private orderflow** (builder-direct bundles,
private RPC, MEV-Boost). The `private_inclusion_share` column in
`block_features.csv` measures it per block.

The implication is direct and important: **public, accessible alpha here is
arbitraged away, and the live edge has moved to a venue a public-mempool vantage
cannot see.** This is consistent with six prior independent out-of-sample tests
on our side, all null. It is not a failure of effort — it is the market being
efficient and the *interesting* flow being private. **So the productive question
is not "what public signal predicts price" (that well is dry) but "what does the
private-orderflow footprint predict, and can we model it from its shadow?"**

## 5. Where your math comes in

Two concrete modeling targets are already laid out in the data:

1. **Predict `private_inclusion_share`** (or its next-block change) from block
   features → a model of *when and where private flow concentrates*.
2. **Predict `outcome` / `lead_time_s` per tx** from its features → a model of
   *what happens to a pending transaction* (does it land, get replaced, or
   vanish; how fast).

Point the math at these. Anything it claims, we test in Section 6 — and a
claim only counts if it holds on blocks/txs the model never saw.

## 6. How we test what the math produces (the apparatus)

We built a deterministic testing loop (`engine/research_loop/`) so hypotheses
are vetted, not vibes:
- A **sealed, burn-on-fail holdout** — a hypothesis is scored once on data it
  never touched. **Sign must hold in-sample → out-of-sample**, or it's rejected.
- A **multiple-testing ledger** — the more hypotheses we try, the stricter the
  significance bar, so we can't p-hack our way to a false positive across many
  attempts.
- **The LLM components can only reject; only the hard metrics can confirm.**

This means we can hand back, for any hypothesis, a credible verdict —
`CONFIRMED` or a documented, reproducible `REJECTED` with the reason. A clean
documented NO-GO is a first-class result, not a failure.

## 7. Honesty caveats (please read before modeling)

1. **Single vantage.** One public feed; ~41–52% of mined flow is invisible.
   Every effect is at best a property of the *public slice*, not all of ETH.
2. **Intermittent capture.** Coverage is non-contiguous bursts (10 gaps,
   ~12.7k missing blocks); gap-overlapping txs are **excluded**, not mislabeled.
   A handful of window-edge blocks show `seen_count=0` / `private_share=1.0`;
   filter those.
3. **`true_ghost` is an upper bound** — never-landed candidates that also absorb
   private replacements we never saw. Not proof of censorship.
4. **`fee_recipient` is the block `miner`**, not the true builder/relay (which
   needs out-of-protocol relay APIs).

## 8. The ask

Point your math at `block_features.csv` and `tx_outcomes.csv`. Generate
hypotheses about the private-orderflow footprint or tx-outcome prediction. Hand
them back as concrete, testable claims, and we'll run each through the loop and
return a verdict with evidence. That turns this from "trade theory for theory"
into a real, falsifiable research collaboration — which is the only honest path
to anything that would survive contact with live capital.
