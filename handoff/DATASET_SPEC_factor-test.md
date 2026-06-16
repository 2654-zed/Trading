# Dataset Spec — Cross-Asset Factor Test

**Purpose:** the single, precise data purchase that lets us test the mid/long-horizon
factor pivot (carry + cross-sectional momentum) honestly. This is the line item that
unblocks everything — the testing apparatus is already built and passing; it just needs
clean data pointed at it. **Don't buy a big enterprise feed. Buy exactly this.**

---

## 1. The non-negotiable requirement (read first)
**Survivorship-free / point-in-time, with delisting dates.** The dataset MUST include
tokens that later died or delisted, and mark *when*. ~Most tokens alive in 2021 are now
dead or down 95–99%. If the dataset only contains tokens listed *today*, every backtest
silently deletes the losers and manufactures fake returns — this single bias routinely
turns a "+40% CAGR" backtest negative in live trading. **A vendor that can't document
this is disqualified for our purpose, regardless of price.**

## 2. The exact data contract (what the harness already requires)
The test harness (`engine/research_loop/factor_data.py`) validates a long-format panel
with these columns. **Acceptance = it loads, `validate_panel` passes, and dead tokens are
present (`is_listed=False` exists).**

| column | meaning | needed for |
|---|---|---|
| `date` | daily (UTC) | all |
| `token` | stable token id (handle symbol reuse) | all |
| `ret` | period return (or `close` to derive it) | all |
| `funding_rate` | perp funding for the period | **carry** |
| `dollar_volume` | daily $ volume | liquidity screen + cost model |
| `is_listed` | point-in-time: actively traded that day (False after delist) | **survivorship** |

- **Universe:** broad but liquid — top ~100–300 tokens by liquidity, point-in-time
  (membership as it *was* on each date, not today's list).
- **Granularity:** daily is sufficient for v1 (hourly optional later).
- **History:** ≥3 years, ideally spanning a full cycle (2021 mania → 2022 crash → recovery)
  so the holdout is a genuinely different regime.
- **Funding:** daily/8h is fine for carry.
- **Depth/liquidity:** daily `dollar_volume` is the minimum; true order-book depth is a
  nice-to-have for later capacity work, not required for v1.

## 3. Minimum-viable purchase (cheapest that satisfies the above)
Two legs: survivorship-free OHLCV+volume (momentum) + funding rates (carry).

> **Recommended: CoinAPI Flat Files (OHLCV+volume, survivorship-free) + CoinGlass (funding).**
> Estimated **~$50–150/mo.**

- **CoinAPI** is the only vendor pairing *documented* survivorship-free + delisting-date
  metadata (`data_trade_start`/`data_trade_end`, `/v1/symbols/history`) with semi-transparent
  usage-based pricing and ~14-yr history. Daily bars for a wide universe are tiny in data
  volume → low tens of $/mo. Use `/v1/symbols/history` to build the point-in-time universe.
- **CoinGlass** ($29–79/mo) covers funding cheaply (`futures/fundingRate/ohlc-history`).

**Cheaper / alternative options:**
- **Rock-bottom (~$29/mo, momentum daily):** the FREE `crypto2` R package (documented
  CRSP-style survivorship handling, CMC source) + CoinGlass for funding. Caveat: R-only,
  scrape-fragile, daily-only, no liquidity — fine for a first pass, not publication-grade.
- **Single-vendor (~$350–700/mo):** Tardis.dev (Academic/Solo) — documented survivorship-free
  OHLCV *and* tick funding *and* order-book depth from one vendor. Removes the two-source
  seam and adds real depth; worth it only if liquidity/capacity becomes a screen.

**Skip for v1:** Kaiko, Amberdata, Coin Metrics (Pro), CCData, Crypto Lake — all quote-only
*and* undocumented on survivorship (two strikes). CoinMarketCap has great survivorship-free
OHLCV but **no funding**, so it still needs CoinGlass and beats nothing CoinAPI offers here.

## 4. Vendor comparison (researched; "UNKNOWN" = confirm via sales call)
| Vendor | Survivorship-free + delist | Funding | Depth/liq | History | Pricing |
|---|---|---|---|---|---|
| **CoinAPI** | **YES (documented)** | YES (~2021+) | YES | ~14 yr | semi-public, usage-based (~low tens $/mo for daily) |
| **Tardis.dev** | **YES (documented)** | YES (tick) | YES | 2019+ | published tiers ~$350–2,200/mo |
| **CoinMarketCap** | **YES (documented)** | NO | NO | 2010+ | public $29–699 |
| **crypto2 (R, free)** | **YES (documented)** | NO | NO | ~2013+ | FREE |
| **CoinGlass** | n/a (funding tool) | **YES** | partial | all-time daily | public $29–699 |
| Coin Metrics | UNKNOWN | YES (2018+) | YES | varies | quote-only |
| Kaiko | UNKNOWN | YES | YES | 2015+ | quote-only (~$10k–55k/yr) |
| Amberdata | UNKNOWN | YES (2018+) | YES | 2018+ | quote-only |
| CCData | UNKNOWN | YES | YES | some 2010+ | quote-only |

## 5. Confirm via quote/trial before committing (CoinAPI primary)
1. **Flat Files actual $** for our daily-bar universe (per-unit documented, total is quote/trial-gated).
2. **Delisted coverage completeness** — `data_trade_end` for *all* dead tokens, not just majors; gap-free pre-2018?
3. **Funding history start + exchange footprint** vs. the venues we'd trade (docs ~2021+; earlier unknown).
4. **CoinGlass funding exchange list** + whether its symbols reconcile to CoinAPI instrument IDs (seam risk).

## 6. What you get for the spend
Fund this (~$50–150/mo), and the already-built harness runs the pre-registered test:
**carry first, then liquidity-screened momentum**, through the sealed-holdout / sign-must-hold /
multiple-testing gate. Outcome is one of two honest answers — a small, real, capacity-limited
tilt, or a clean documented null — at the cost of a data subscription instead of a multi-month
build. The dataset is the only missing piece; everything downstream is done and tested.
