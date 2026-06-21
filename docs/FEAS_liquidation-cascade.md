# FEAS — Liquidation-cascade predictiveness (Game 3, derivatives extension) — v2

> Decision scope: can a CEX-microstructure **fragility model** predict short-horizon
> adverse moves around perp liquidation cascades, well enough to be a tradeable
> risk-timing signal — through the sealed-holdout gate? Runs **OFFLINE on Tardis
> data we already pay for.** Dated 2026-06-20. **v2 = hardened after a 3-lens
> adversarial red-team (statistician / trader / methodologist).** Not committed.
> Related: [QA_2026-06-20](QA_2026-06-20_alpha-liquidations-tardis.md) · [FEAS_drainer-signal.md](FEAS_drainer-signal.md) · [FOCUS.md](../FOCUS.md)

## Bottom line up front (honest, post-red-team)
**This is probably a NO-GO, and the cheap way to find out is three ~1-day tests
BEFORE building anything.** The red-team found the original design would most likely
produce a *false* GREEN (via pseudo-replication, an un-controlled vol artifact, or
label↔feature circularity) — or is simply **dead on statistical power** (~6–8
independent cascades in all of Tardis history → ~2–3 in any honest holdout, vs the
gate's n_obs≥30-*independent* requirement). So we **front-load the kill-tests** and
only build the pipeline if all three survive. A documented NO-GO is the expected,
acceptable outcome.

**Update (2026-06-20) — K1 measured, and it CLEARS.** The original "dead on power"
fear assumed the mega-cascades as the sample. On the *reframed* moderate-episode
target, the key's accessible window (Feb–Jun 2026) holds **~30 independent moderate
stress episodes / ~13 major, ~7 in a holdout** (see K1 below). **K2 (vol-ablation)
now also CLEARS** — fragility adds a *modest but consistent* increment beyond realized
vol (+1.4pp R², partial corr +0.13, high-liq-burst → higher forward vol in 10/10
vol-deciles). So it's **not** dead on power and **not** a pure vol artifact. Remaining
cheap kill: **K3 (placebo lead-lag)**; then the real build (Gate A with event-clustered
inference + a concrete trading expression).
**K3-lite (2026-06-20) does NOT clear:** residualized on the vol *level*, the K2 edge is a
**volatility nowcast** (fragility marks *current* vol), not a forward cascade-**acceleration**
predictor; non-liq features (OI/funding) are flat. **Net Phase-0 read: a thin *defensive*
vol-filter at best (≈ vol-targeting), NOT the offensive cascade alpha** — a documented
**lean-NO-GO** for the alpha thesis. The only untested upside is **K3-full** (book-resilience
features, the heavy book pull); pursue only if that specific edge is worth the build.

## Why this one at all
On-chain liquidation **capture** is a saturated MEV latency race — dead for us
(Chainlink SVR internalizes ~99% of the OEV). Perp/CEX **cascades** are a *data*
edge (forecast forced-flow, no tx to win) that runs on the Tardis data we're wiring
up — the one untested direction that fits our position. It's Game 3 extended to
derivatives fragility. But "fits our position" ≠ "has an edge" — hence the gates.

## Two hard gates (either fails → NO-GO)
- **Gate A — Predictiveness, net of vol.** Does a fragility state forecast adverse
  short-horizon moves **OOS, sign held, AFTER beating a realized-vol-only benchmark**,
  on an honest **event-level** sample?
- **Gate B — Tradeability.** Expressed as a concrete rule on a real host position,
  does it improve risk-adjusted PnL **beyond a vol-only filter**, after a
  pessimistic (cascade-regime) cost model?

---

## PHASE 0 — three cheap kill-tests FIRST (no Tardis, no pipeline; ~1–3 days)
Run these on a small slice of recorded data before spending a week building. Any one failing = **NO-GO, stop.**

- **K1 · Power census (one page, no data).** Enumerate the *independent* market-wide
  cascades in the Tardis window (~2019/20→2026): Mar-20 COVID, May-21, May/Jun-22
  (LUNA+3AC = *one* regime), Nov-22 FTX, Aug-24 yen-carry, Oct-10/11-25, Nov-25 ≈
  **6–8 independent events.** Apply a time-split → **~2–3 land OOS.** If that can't
  honestly meet the gate's independent-event floor, the **event-level alpha framing
  is dead on power** — reframe to episode-level (below) or stop.
  **→ MEASURED 2026-06-20 (`engine/scripts/liq_k1_census.py`):** the key's accessible
  window (2026-02-14→06-19; $3.77B BTC liquidations, 144,819 prints) holds **~30
  independent moderate-stress episodes (>p95/hr) / ~13 major (>p99/hr), ~7 in a
  last-20% holdout** — clears the gate's ≥2/side floor. **K1 PASSES** on the reframed
  moderate-episode target. Caveats: power is *modest* not abundant; and these are
  moderate stresses — the true mega-cascades (Oct-2025 etc.) stay out of reach without
  buying historical Tardis months. Binding kills now = **K2 + K3.**
- **K2 · Vol-ablation (one regression).** On ONE known cascade, regress forward
  adverse move on **EWMA/GARCH realized vol alone.** If vol-only captures most of
  what "fragility" would, the edge is a **vol artifact** → NO-GO.
  **→ MEASURED 2026-06-20 (`engine/scripts/liq_k2_ablation.py`, 36,252 5-min bars):**
  current realized vol explains R²=0.30 of next-hour vol; +fragility (liq-burst, |OI-ROC|)
  → R²=0.31 (**incremental +1.4pp**); partial corr(liq-burst, fwd | rv)=**+0.13**;
  high-liq-burst bars show higher forward vol in **10/10 realized-vol deciles**.
  **K2 PASSES** — fragility is not just repackaged vol, but the edge is **modest**, and
  this is descriptive (autocorrelated bars; event-clustered inference deferred to Gate A).
- **K3 · Placebo-controlled, vol-residualized lead-lag.** Define the label from
  **price primitives only** (fwd adverse move > k·rolling-vol on mark/trade prices —
  *never* from a liquidation series). Test whether **non-liquidation** features
  (book-depth thinning, OI-ROC, funding) lead the gap **after partialling out
  contemporaneous vol**, AND do **not** lead matched-vol calm **placebo** timestamps
  equally. If the vol-residualized lead vanishes or the placebo leads as much → NO-GO.
  **→ K3-LITE MEASURED 2026-06-20 (`engine/scripts/liq_k3_lite.py`):** label = price-only
  vol *acceleration* (next-1h ÷ trailing-1h vol) + vol-matched permutation placebo. Result:
  liq-burst corr=−0.08, OI-ROC=−0.18, |funding|≈0 — once you residualize on the vol
  **level**, the K2 edge collapses into **vol mean-reversion** + liq-burst as a *coincident*
  vol marker; **non-liquidation features (OI-ROC, funding) show no clean forward lead**, and
  the within-regime liq-burst effect (+17σ vs placebo) is economically negligible (~1pp).
  **K3-lite does NOT clear.** Reconciles with K2 → fragility is a **volatility NOWCAST, not a
  cascade-acceleration predictor**: supports a *defensive* risk filter (≈ what vol-targeting
  already gives), undercuts the *offensive* predict-&-fade-the-cascade alpha. Only untested
  upside: **K3-full** = book-resilience features (heavy `incremental_book_L2` pull).

> K1–K3 directly attack the three most-likely false-GO paths (small-N, vol artifact,
> circularity) for the price of a few scripts. Most of the expected value of this
> whole effort is here.

## Reframed target (only if Phase 0 survives)
- **NOT** the ~7 named mega-cascades as the sample (underpowered) — those become
  **out-of-sample CASE STUDIES**.
- **Statistical sample = MANY mechanically-defined moderate stress episodes** (every
  same-side liq burst / OI-ROC spike above a *pre-registered quantile*, across the
  WHOLE window — not hand-picked around famous cascades). One **observation = one
  non-overlapping episode** (merge with a multi-hour cooldown).
- **Label = price primitives only** (forward adverse move vs rolling vol). The
  liquidation tape may be a feature **or** a label, never both (provenance firewall).
- **Primary statistic (name it in P3):** AUC / rank-corr of fragility-score-at-t vs
  realized adverse-move magnitude over [t,t+Δt] across episodes, **or** a two-sample
  mean-difference (high-F vs vol-**matched** low-F), sign pre-registered. Tail/CVaR =
  secondary confirmatory only (tail-of-a-tail is too low-N to headline).

## The expression (Gate B) — concrete, or it doesn't count
"De-risk when fragility is high" is not a strategy. Make it backtestable:
- **Host position:** a funding-carry / short-funding book *or* a fixed vol-target
  perp book.
- **Filter rule:** `F > thresh → cut size to X% / widen stop / flat`.
- **Score:** Δ-Sharpe and Δ-CVaR vs **(a) filter-off** and **(b) a realized-vol-only
  filter of identical aggressiveness.** **GO only if it beats the vol-only filter** —
  not just filter-off.
- **Report the cost of false positives:** PnL foregone across the ~95% of high-F
  states that never cascade (a filter can bleed more than it saves).

## Hardened method (P-phases, offline)
- **P0 Data:** wire Tardis (`TARDIS_DEV` in `.env`); one **primary venue/engine**
  first (don't pool venues — different liq engines + cross-venue lead-lag double-count
  events). Pull `incremental_book_L2`, `derivative_ticker` (OI/funding/mark),
  `liquidations`, `trades`. Consume via **tardis-machine normalized replay**.
- **P1 Mechanism (the sharpened K3):** placebo + vol-residualized + strict-causality
  timestamp audit. Decisive, not "features move before the gap."
- **P2 Fragility model:** combine features → score. **Edge thesis = we model book
  RESILIENCE** (depth refill-rate / time-to-refill after a sweep, quote-life,
  cancel-to-trade) that dashboards don't compute — *not* "we have OI/funding." First
  test whether **displayed depth even predicts realized impact or is a mirage** MMs
  pull; if it's a mirage, fall back to flow-toxicity/OI.
- **P3 Pre-register (freeze ONE spec):** universe, Δt, k, F-cut, feature list,
  score-combination, **cluster-reconstruction params from published margin tiers**
  (never calibrated to where cascades fired), the **vol-only benchmark**, and a
  **pessimistic cost model** (far-touch/walk-the-book + fraction of displayed depth
  assumed unavailable; pessimistic fills are the headline). Bind via `holdout_sha256`.
- **P4 Gate test — with rare-event fixes (the existing `research_loop` gate does NOT
  transfer as-is):**
  - **n_obs = independent EVENTS, not rows.** Reject any run where n_obs is
    timestamp/row count. Use block-bootstrap / event-clustered SE.
  - **Event-blocked split:** IS and OOS each need **≥2 distinct, temporally-separated
    episodes**; purge+embargo around the seam. If <2/side → **UNDERPOWERED, stop.**
  - **Vol-orthogonalization is gate-blocking:** fragility must add incremental OOS
    power on the vol-residual; GO reads "beats vol-only," not "predicts moves."
  - **Within-hypothesis trial budget:** count every (feature, k, Δt, weighting) tried
    (not just ledger hypotheses) in the multiple-testing correction, or confine all
    tuning to nested CV inside IS and freeze before the holdout is touched.
  - **Real lookahead check:** emit per-feature max-source-timestamp ≤ decision
    timestamp (not a notes-string assertion).

## GO / NO-GO
- **GO (toward paper):** Phase 0 survives **and** episode-level fragility beats the
  **vol-only benchmark** OOS with sign held on an **event-blocked** split (≥2
  independent episodes/side), surviving the within-hypothesis correction **and** the
  provenance ablation (edge persists with liquidation-derived features removed) **and**
  Gate B (the concrete filter beats the vol-only filter under pessimistic fills).
- **NO-GO:** any Phase-0 kill; or edge is vol-clustering; or it only survives with
  liquidation-derived features (circularity); or <2 independent episodes/side
  (underpowered); or the filter doesn't beat vol-only after costs.

## What NOT to build
- ❌ On-chain liquidation **capture** (MEV race — dead; SVR internalizes it).
- ❌ A label built from any **liquidation-derived** series (circularity) — price primitives only.
- ❌ Counting within-cascade **timestamps** as observations (pseudo-replication).
- ❌ Trusting vendor **heatmaps** as labels; or **calibrating cluster params** to where cascades fired (reverse-fit leak).
- ❌ Pooling **venues** into one score; ignoring **cross-margin/ADL** (ADL closes positions with *no* print and *no* book traversal → OI-delta magnitude is contaminated; restrict to isolated-margin instruments or decompose OI-delta into print-attributable vs residual).
- ❌ **Fade-the-exhaustion** as part of this study — it's a falling-knife / multi-leg problem; demote to a separate later pre-registration.
- ❌ Optimistic recorded-book fills (the book is a mirage in a cascade); claiming **push-button alpha** (the honest deliverable is a defensive filter).

## Honest risks (kept from v1, sharpened)
Reflexivity (the visible read is crowded/gamed — durable edge only in book-resilience
microstructure); rare-event small-N (**the single most likely NO-GO**); throttled liq
feed (non-random censoring worst at the peak — use OI-deltas for magnitude, model the
tape as right-censored); regime-dependence (OOS must hold its own independent stress).

## Decision routing
1. **Any Phase-0 kill** → fast, cheap NO-GO. *(Most likely path.)*
2. **Survives Phase 0 but dies at the gate** → NO-GO as alpha; the fragility/book-resilience score may still have **defensive value** (avoid being liquidated) — keep as internal risk tooling, not a strategy claim.
3. **Clears both gates** → paper-trade the concrete filter; scope live separately.

## Cost / effort / prereqs
- **Data:** already paid (Tardis Pro). **Compute:** local. **Net new $: ~0.**
- **Effort:** **Phase 0 first (~1–3 days)** — most of the decision lives here. Full
  pipeline (P0–P4) only if Phase 0 survives (~1–2 weeks + gate test).
- **Prereq:** engineer wires `TARDIS_DEV` + tardis-machine (per the Q&A doc).
- **Reuses:** `engine/research_loop` (gate — **with the rare-event fixes above**) + the Game-3 reconstruction pipeline.
