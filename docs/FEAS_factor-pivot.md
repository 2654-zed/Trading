# FEAS — Factor / carry pivot (mid-horizon market-neutral crypto)

> The one direction whose edge is **NOT gated by our seat** (taker, public data,
> laptop): mid-horizon (weekly/monthly) cross-sectional/time-series factor investing.
> Scope for a sealed-holdout test *before* any data purchase or capital. Dated 2026-06-20.
> Grounded in a 3-agent evidence/data/cost workflow (sources at bottom).
> Related: [FOCUS.md](../FOCUS.md) · [FEAS_game3-imbalance.md](FEAS_game3-imbalance.md) · [ARCHIVE.md](ARCHIVE.md) · `engine/research_loop/factor_*`

## The frame flips
Every prior branch (MEV, drains, liquidations, imbalance) failed on **position** — the
edge was real but needed a seat we don't have. **Factor investing inverts that:** at a
weekly/monthly horizon a ~5–6 bps taker fee is trivial against 200–500 bps cross-sectional
moves; no maker queue / colocation / builder access needed; public funding+OHLCV+mcap
suffice. **So the gate is no longer "can we capture it" — it's "is there anything left to
capture after crowding/decay."** That is now the whole question.

## Bottom line up front (sober)
**Worth testing, but go in expecting ~0.5 net Sharpe, not a money printer — and expect
the cheap kills to retire half the candidate factors.** Specifics from the evidence:
- **Carry (funding) ≈ dead as a core trade.** Sharpe 6.45 (2020–25) → 4.06 (2024) →
  **negative in 2025**; BTC basis 25% → ~4.5%. ETF cash-and-carry crowded it out. *Keep
  only* as an opportunistic **cross-venue/cross-perp funding-dispersion** sleeve (residual
  Binance↔Hyperliquid gaps ~11% avg).
- **Cross-sectional momentum: weak net of cost.** Survivorship-controlled survey finds
  CMOM ~2.1%/wk post-2020 *gross*, but Han/Kang/Ryu show it's **weak after costs + short-leg
  liquidation risk**; the robust survivor is **time-series TREND** (vol-targeted, liquid).
- **Size: dead** post-2020 (−0.9%/wk, insignificant) — mostly illiquidity + survivorship.
- **Low-vol/beta: contradictory** — treat as a **construction overlay**, not alpha.
- **Value: shrinking but alive** (−2.3%/wk post-2020), fragile/definition-dependent.
- **Illiquidity: the most consistently priced** — but self-defeating at scale; **a small
  book's moat** (capacity ceiling is the point).

→ **The test-worthy shortlist:** (1) **time-series trend** (vol-targeted, top-20–50 perps);
(2) a **small illiquidity / short-horizon reversal** tilt; (3) **funding-dispersion** as an
opportunistic sleeve. *Avoid* naive wide-universe decile momentum, crowded BTC carry, and size.

**Red-team verdict (it read this scope AND the `engine/research_loop/factor_*` code):
NEEDS-WORK, leaning NO-GO-on-EV.** The existing harness is **tuned to a false GO** — flat
30 bps costs (not the depth-aware model claimed), breadth/overlap-inflated `n_obs`,
multiple-testing that doesn't bind, and synthetic shorts that capture the **−90% death return
of tokens you could never have shorted.** And on the honest ~0.5 Sharpe — after the "realized
≈ half" haircut, the momentum-crash left tail, and single-venue counterparty risk — **the EV
case is weak *before any backtest*.** → **Run the $0 synthetic kill (K0) first**; only buy data
if the planted edge survives honest costs + n_obs + a shortable-only short leg. Corrections below.

## ⚠️ Red-team corrections — the harness is currently tuned to a FALSE GO; fix these first
A quant red-team read this scope **and** the implemented `engine/research_loop/factor_*` code:
the harness does **not** do what the prose advertises, and every approximation errs *optimistic*.
A GO from the current code is **not trustworthy**. Blocking fixes:
1. **`n_obs` inflates significance 2–5×.** "rebalance × breadth" is wrong — crypto names are
   ~0.6–0.85 correlated (→1 in stress), so effective breadth ≈ **1–2, not 30**; and the code's
   `n_obs=len(rets)` on a *daily*-formed book is overlap-inflated. **Fix:** `n_obs` = **non-
   overlapping rebalance periods** (~48 for 4yr monthly); block-bootstrap / Newey–West t;
   report the **Deflated Sharpe** (Bailey–LdP); raise the gate `min_obs` floor to ≥~40 *periods*.
2. **Multiple-testing doesn't bind.** The ledger counts *executed rows*, not forking paths; the
   real grid (universe × lookback × cadence × quantile × neutralization × cost × bands) is
   **~10²–10³ paths**. Running-Bonferroni is order-gameable. **Fix:** enumerate the full grid,
   **seed ledger N = grid size before the first test**, use **BH/FDR on the full p-vector batch**,
   **freeze** cost/quantile/lookback in the sealed contract (currently free kwargs), **NO-GO if
   PBO (prob. of backtest overfit) > 50%**.
3. **The cost model is a flat 30 bps — not depth-aware, and carry books no funding.** Identical
   30 bps charged to BTC and the #45 name; `carry_factor` sorts on funding but never books the
   funding cashflow → the illiquidity/reversal + funding sleeves look tradeable when honest costs
   eat 100–300 bps on the tail. **Fix:** per-name **√-impact slippage = f(notional/ADV, spread,
   vol)**, **book the funding cashflow**, model **real turnover + no-trade bands as a signal
   transform**, **asymmetric 2–4× exit slippage** in top-decile-vol months. Re-run K2 with this.
4. **Gate B must be a quantitative EV hurdle, and the bar is > 0.5.** ~0.5 net Sharpe + crash
   left tail + single-venue counterparty + the "realized ≈ half" haircut → **realized ~0.25 with
   a fund-ending tail** = weak/negative EV *before any test*. The "3–4 sleeves → 0.8" rescue is
   **assumed, not shown** (trend/illiquidity/funding fail *together* in a de-risk event). **Fix:**
   require backtested net Sharpe **≥ 0.8**, **sleeve corr < 0.3 in the worst-decile-vol months**,
   **multi-venue**, capacity worth the operational cost — else default = **don't trade**.
5. **The survivorship-free LONG-SHORT is counterfactual on the SHORT side.** You could not have
   shorted most delisted micro-caps (no borrow/perp, withdrawal halts, stale delist mark) — yet
   the synthetic harness lets shorts capture the **−90% death return**, so **K1 can be passed by
   an un-capturable short.** Also CoinGecko `inactive` is a *today* flag (look-ahead) → use
   **point-in-time delist dates**; `mcap = price × supply` is noise for dead tokens. **Fix:**
   restrict the **short leg to actually-shortable names at formation**, define universe by
   **delist dates**, make Tardis derivatives the **primary investable universe** (truly
   survivorship-free) with CoinGecko mcap a *ranking covariate with missingness flags only*, and
   re-run K1 in **shortable-only** mode.

## K0 — the $0 kill to run BEFORE buying any data (~1 day, code already on disk)
Run the existing factor harness on its **synthetic** panel with three honest patches:
**(a)** `n_obs` = non-overlapping monthly periods; **(b)** replace flat 30 bps with size/ADV-scaled
slippage + funding cashflow; **(c)** **forbid the short leg from capturing the −0.90 death return**
(restrict shorts to still-listed liquid names). If the *planted* factors no longer clear the
**full-grid FDR** threshold under these patches, then real data — noisier and more crowded — has
**no chance**, and you've reached NO-GO for **$0**. This is the factor analog of the synthetic kill
that ended the liquidation thread — **run it first.**

## Two gates (either fails → NO-GO)
- **Gate A — Survivable edge.** Does a candidate factor clear a **survivorship-free,
  cost-aware, OOS** test with the **sign holding** and a **net Sharpe materially > 0** (and,
  to be worth it, ≳ 0.5)?
- **Gate B — Worth it after crowding.** Is the *recent-period* (post-2024) edge still alive
  (not decayed to ~0), and is ~0.5 Sharpe worth the capital + the ongoing decay-monitoring
  burden vs. just not trading?

## The data prerequisite (the only purchase)
- **Funding + OI leg: already covered.** Tardis Pro `derivative_ticker` gives per-perp
  funding + OI history and **preserves dead exchanges/symbols (FTX etc.) → survivorship-free
  on the derivatives side.** No new spend.
- **The gap = universe + market cap + delisted spot price.** Tardis is *not* a universe/mcap
  panel. Cheapest fix: **CoinGecko API Pro/Analyst (~$129/mo)** — `/coins/list?status=inactive`
  enumerates **delisted IDs (point-in-time universe)**; OHLC/market_chart return history for
  inactive IDs. **$0 pre-check first:** verify daily **market cap for known-dead tokens** is
  actually populated (CoinGecko delisted mcap can be patchy) on a 1-month trial **before**
  committing — if it fails, fall back to CoinMetrics (richer, enterprise-priced) or compute
  `mcap = delisted price × circulating supply` yourself.

## PHASE 0 — three cheap kills FIRST (mostly $0; days, not weeks)
The factor analog of the liquidation K-tests — run before building the full gate. Any one failing for a given factor retires it.
- **K1 · Survivorship kill (the decisive one).** Build the factor on **survivors only**,
  then **add the dead/delisted tokens**, and recompute. If the Sharpe **collapses** when dead
  tokens are included, the factor was survivorship-inflation, not alpha → drop it. *(This is
  why the universe purchase matters; it IS the test.)*
- **K2 · Cost-ablation.** Apply a realistic **turnover × taker-fee + depth-aware slippage +
  funding** model. Monthly rebalance, ~5–8 bps/side, slippage scaled to a *small* book on
  *liquid* names. If the gross premium doesn't survive → drop. *(Turnover control is the #1
  lever; monthly ≫ weekly after costs.)*
- **K3 · Decay check.** Compare the factor's **post-2024 IC/Sharpe** to full-sample. If it's
  decayed to ~0 or negative (as carry has) → drop or demote to opportunistic.

## Method (only factors surviving Phase 0)
- **P0 Panel.** Join Tardis funding/OI (survivorship-free) + CoinGecko universe/mcap/price
  (incl. delisted) into a point-in-time daily panel. **Symbol↔asset map by (exchange, symbol,
  date-range)** — Tardis reuses raw symbol strings across underlyings over time.
- **P1 Construct.** Top-N **liquid** perps (e.g. top 20–50 by mcap/volume); factor scores
  (trend, illiquidity/reversal, funding-dispersion); **monthly** rebalance; vol-target /
  beta-neutralize the book; no-trade bands to cap turnover.
- **P2 Pre-register (freeze one spec).** Universe rule, factor defs, rebalance cadence,
  horizon, the **depth-aware cost model**, and the **multiple-testing budget across the factor
  set** (every factor/parameterization tried counts). Bind via `holdout_sha256`.
- **P3 Gate test.** Sealed holdout via `engine/research_loop`, with the **rare-event lessons
  applied**: **n_obs = independent rebalance periods × breadth, NOT raw daily rows**;
  **period-blocked OOS** (purge/embargo around the seam); **sign-must-hold IS→OOS**;
  multiple-testing correction across the factor family. → **GO (paper)** or **documented NO-GO**.

## GO / NO-GO
- **GO (toward paper):** a factor (or a small **low-correlation combo**) survives K1–K3 **and**
  clears the period-blocked OOS gate with **sign held**, a net Sharpe **≳ 0.5 after the full
  cost model**, and a **non-decayed post-2024** contribution.
- **NO-GO:** edge is survivorship/cost/decay artifact, or sign flips OOS, or net Sharpe is
  trivial (< ~0.3) for the risk/operational burden, or only the crowded/dead factors "work."

## What NOT to build
- ❌ Naive **wide-universe decile momentum** (illiquid micro-caps, short-squeeze tails) — use
  **time-series trend on liquid names** instead.
- ❌ The crowded **BTC cash-and-carry** (dead) — only the cross-venue dispersion remnant.
- ❌ Leaning on **survivorship-inflated pre-2021** backtests or **headline gross Sharpes** (2–2.5
  figures are gross/in-sample; realized ≈ half).
- ❌ **Over-mining the factor zoo** (only 13/49 anomalies stayed significant) — pre-register a
  small set, count every trial in the correction.
- ❌ A **small-cap tilt for capacity we don't need** — the depth-aware slippage there is the killer.

## Honest risks
- **Crowding/decay is the deepest threat** — factors die *as* they're found; carry is the
  fresh corpse. Defense = signal novelty + active decay-monitoring + capacity-niche.
- **Momentum-crash tail:** in reversals the short (loser) leg rebounds violently *and* funding
  flips negative — vol-scaling mitigates, doesn't eliminate.
- **~0.5 Sharpe ceiling** for crowded factors — honest base case; combining 3–4 low-corr
  sleeves + turnover control is the path toward ~0.8, leverage scales return *not* Sharpe.
- **Capacity** is low (few $M–low-tens of $M) — fine for a small fund (it's the moat), but
  don't size into the illiquid tail.
- **Counterparty/ADL/outage** — single-exchange concentration is a fund-level risk for a small op.

## Decision routing
0. **K0 synthetic honest-kill fails** → **NO-GO for $0**, before any purchase or harness rework. *(The red-team's most-likely outcome — run it first.)*
1. **CoinGecko delisted-mcap pre-check fails** → either fall back to CoinMetrics (cost step-up)
   or self-compute mcap; if neither is viable cheaply → **NO-GO on data grounds**.
2. **K1–K3 retire all shortlisted factors** → documented NO-GO (the crowding won).
3. **A factor/combo survives Phase 0** → run the full gate (P0–P3); GO only on ≳0.5 net Sharpe,
   sign-held, non-decayed. Otherwise NO-GO.

## Cost / effort
- **Data:** Tardis owned + **~$129/mo CoinGecko** (after a $0/1-month-trial delisted-mcap
  pre-check). **Compute:** local. **Reuses:** `engine/research_loop` (gate) + the existing
  `factor_data.py` / `factor_measurements.py` (survivorship-aware panel contract + carry/xs-mom,
  already built+tested on synthetic — now point them at real survivorship-free data).
- **Effort:** **Phase 0 first (~3–5 days)** — most of the decision lives there; the full gate
  only if factors survive. **Expect Phase 0 to be where most candidates die.**
