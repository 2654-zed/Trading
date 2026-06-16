# Lab Report — Multi-Lens Decision Engine & the Information-Lens Edge

**Project:** Layer 3 Trading Research Experiment (`Desktop/Trading/`)
**Phases covered:** 3 (multi-lens engine) · 4 (real-outcome validation) · H9 investigation
**Period:** 2026-05-25 → 2026-05-28
**Charter:** detection + logging only; read-only on-chain (I-1) and read-only against Layer 3 (I-3); no execution
**Status:** Phase 3 & 4 complete. Multi-lens thesis falsified. One signal (H9) validated on a single window; second-window replication pending L3 restart.
**Cost:** $0 incremental (free DefiLlama price data); 77M Alchemy CU available and deliberately unspent.

---

## 1. Abstract

We built a five-lens "multi-lens decision engine" that ingests Layer 3 surveillance data, emits typed signals from mathematically distinct lenses (graph, stochastic, information, + planned topology/game), synthesizes them through a regime-aware orchestrator with a closed learning loop, and produces trading decisions. We tested its central hypothesis — that multi-lens synthesis beats the best single lens (H5, ≥15 percentage-point edge) — first against a proxy outcome and then against **real forward price returns** from free historical data.

**Primary result (negative):** The multi-lens orchestrator **never beat its best single component** across six independent tests (proxy and real, in-sample and out-of-sample). On real outcomes it *underperformed* the best lens by 11–17 pp. The synthesis actively **dilutes** signal: the graph lens dominates emission volume (96%) while being non-predictive (corr 0.035), dragging the orchestrator below the one lens that works.

**Secondary result (positive):** Decomposing the engine surfaced one signal — **`entropy_drop` on transfer-role distributions** (Shannon-entropy collapse in *who* transacts with a token) — that is the first signal in the project to survive out-of-sample, predicting forward returns with correlation ≈ +0.48 (n=74 holdout). It passed three validation gates (survivorship, beta-vs-alpha, costs) as a **positive-EV but convex (low-win-rate, fat-tailed)** edge on liquid pools, on a single 49-day window.

**Conclusion:** The architecture's premise (synthesis > components) is false for this signal set; the productive artifact is one narrow signal, not the engine. Execution remains gated NO-GO pending second-window replication.

---

## 2. Background & Motivation

Phase 1–2 (prior work) established a cross-chain arbitrage detector consuming Layer 3 (L3) — a production behavioral-surveillance system labeling smart-contract infrastructure on Base/Arbitrum/Optimism. Phase 2 closed with H2 "SUPPORTED-WITH-CAVEAT": L3 flags concentrated on a single pool, leaving open whether L3 signal yields tradable information.

Phase 3 proposed a generalization: rather than one filter pipeline, run **N independent "lenses,"** each applying a different branch of mathematics to the same data, and let an **orchestrator** synthesize them — the thesis being that combining diverse weak signals yields an edge no single lens has.

---

## 3. Hypotheses

| ID | Statement | Disposition |
|----|-----------|-------------|
| **H5** (primary) | Orchestrator decisions beat the best single lens's by ≥15 pp (precision/correlation), out-of-sample. | **NOT-SUPPORTED** (D-041, D-042) |
| **H6** | Regime detection (trend/chaotic/adversarial/…) carries outcome-relevant structure. | SUPPORTED-PROXY (D-031) |
| **H7** | Cross-lens *conflicts* are alpha (different outcome distribution). | SUPPORTED in-sample; not confirmed OOS |
| **H8** | The system learns (dynamic weights improve over static, out-of-sample). | WEAKLY-SUPPORTED-OOS (both near zero) |
| **H9** (emergent) | `entropy_drop` in a token's transfer-role distribution predicts forward return with a tradable edge after costs. | **VALIDATED w/ caveats** (D-043, D-044) |

---

## 4. Methods

### 4.1 Engine architecture (Phase 3)
- **Signal schema** (D-021): frozen 9-field contract (`id, timestamp, lens, type, strength, confidence, time_horizon, metadata, vector`), validated at every event-bus publish (invariant I-16). 5 canonical lenses.
- **Event bus** (D-022): asyncio, dot-topic routing with wildcards, three backpressure modes; validated 10K signals/sec, no drops.
- **Lenses built:** graph (org-cluster / centrality / poisoning subgraph), stochastic (flow-volatility regime shifts), information (Shannon entropy + KL divergence on categorical distributions). Topology + game deferred.
- **Orchestrator** (D-033): regime-aware weighting; non-linear conflict adjustment (I-17, "no simple average"); conflicts emitted as first-class `ConflictSignal`s (I-18).
- **Feedback loop** (D-035/036): decision engine → outcome ledger (separate writable SQLite, never L3) → learner (per-regime per-lens exponential-decay weights, I-19) → dynamic weighting. Execution router built **dry-run only**, hard-disabled (D-037).

### 4.2 Temporal replay (D-029, resolves UNK-014)
Static L3 data produced identical signals every scan (zero variance → no testable dynamics). A `ReplayClock` advances a simulated cursor through ~49 days of historical L3 data (2026-03-29 → 05-18); lenses query "as of" each window and stamp signals with simulated time; the orchestrator windows by event-time. 200 windows ≈ 5.9 h each.

### 4.3 Outcome attribution
- **Proxy (D-034):** forward "turbulence" of L3 activity (transfer flow + adversarial events) over [T, T+2 windows]. Used because no $0-CU price feed existed at the time.
- **Real (D-040):** forward price **return** from DefiLlama's free historical API (no key, $0 CU). 94% coverage of the 88 monitored-set tokens, including Base memecoins. Two variants: whole-basket turbulence, and entity-specific (return of the tokens a window's signals actually fired on).

### 4.4 Hypothesis evaluation
- **In-sample + out-of-sample (70/30 train-holdout) split** on the replay window.
- **H5:** Pearson correlation of orchestrator score vs outcome, compared to each lens's strength vs outcome. Margin in pp.
- **H9 gates:** (1) survivorship — death-aware returns; (2) beta-vs-alpha — market-relative excess returns; (3) costs — net per-trade EV after fees + constant-product slippage, position-size swept.

### 4.5 Reproducibility (scripts, all `python -m engine.scripts.<name>`)
`run_engine_phase_3_4b` (proxy loop) · `run_engine_phase_4` (real outcomes, `--entity-mode`) · `analyze_info_lens` (per-type/source/horizon breakdown + OOS) · `check_info_lens_survivorship` · `check_info_lens_regime` (beta-vs-alpha) · `check_info_lens_costs` (net EV). 479 unit/integration tests pass.

---

## 5. Results

### 5.1 Phase 3 — multi-lens engine on proxy outcomes
Engine runs end-to-end at p95 16 ms/window (target <500 ms; **speed never a constraint**). 200-window replay: ~7,900 signals, 200 decisions, 194 conflicts, closed dry-run learning loop. The learner moved weights measurably and changed decisions (e.g. `enter` count 0→40 under learned weights). But H5 on the proxy: **in-sample +7 pp, out-of-sample −5.1 pp** — the in-sample edge did not generalize.

### 5.2 Phase 4 — real price outcomes (the decisive test)

| Test | Orchestrator − best single lens |
|------|--------------------------------|
| proxy, in-sample | +7.0 pp |
| proxy, OOS | −5.1 pp |
| real (whole-basket), in-sample | −11.7 pp |
| real (whole-basket), OOS | −8.4 pp |
| real (entity-specific), in-sample | **−16.6 pp** |
| real (entity-specific), OOS | **−11.0 pp** |

Per-lens correlation with real forward outcomes (entity-specific): **information 0.29** (best), stochastic 0.16, **graph 0.035** (noise), **orchestrator 0.13**. Refining outcomes from whole-basket → entity-specific *strengthened* the signal yet *widened* the orchestrator's deficit — confirming the failure is structural, not a metric artifact. **UNK-015 resolved:** the multi-lens edge is genuinely absent; synthesis dilutes the predictive lens because the non-predictive graph lens dominates emission volume.

**→ Phase 5 (execution) gate FAILED. NO-GO. (D-042)**

### 5.3 H9 — the information-lens edge

Decomposing the info lens by signal type × data source × horizon, with OOS split:

| Signal × source | 24h holdout signed corr (n=74) | 48h holdout |
|---|---|---|
| **`entropy_drop` / transfer-roles** | **+0.480** | **+0.506** |
| `regime_surprise` / * | sign-flips OOS (−0.56) — noise | — |
| `divergence_spike`, `entropy_drop`/liquidity | too rare to validate | — |

`entropy_drop` on transfer-roles is the first OOS-surviving signal in the project. Economic reading: when the **diversity of who transacts** with a token collapses (entropy drop = flow concentration), a price move follows.

**Validation gates (D-044):**

| Gate | Method | Result |
|------|--------|--------|
| **1 — Survivorship** | death-aware returns (dead tokens → catastrophic) | **PASS** — 0 of 86 signals fired on a token that died; corr unchanged. (Signal self-selects liquid tokens: needs ≥10 transfer events to fire.) |
| **3 — Beta vs alpha** | market-relative excess returns + up/down-phase split | **PASS** — excess corr +0.49–0.50 @48h (≈ raw); market mean only ~+1% (not a bull artifact). 48h regime-robust; 24h inverts in down-markets (use 48h). |
| **2 — Costs** | net per-trade EV after fees + CP slippage (exit sized on post-move bag), size swept | **PASS at size** — strong cohort gross +44.9% vs weak +1.2%; net **+37.3% @$10k, +16.1% @$50k, −2.1% @$100k**; survives outlier removal (mean ex-top-3 **+18.8%**). |

**Profile caveat:** 42% win rate, **negative median (−2%)**, fat right tail (p90 +190%, top winner +299%). Positive EV is convex/lottery-shaped, not a steady edge. Pools are liquid (median TVL $2.28M), so slippage only dominates above ~$100k/trade.

---

## 6. Discussion

**The recurring lesson — aggregation buries signal.** At every level, predictive signal lived in one narrow place and combining washed it out: the multi-lens orchestrator buried the information lens under graph noise; *within* the information lens, the real edge (`entropy_drop`/roles) was buried under the more-frequent `regime_surprise` noise (53% of emissions, sign-flips OOS). The discipline that paid off was **decomposition, not synthesis.**

**Why multi-lens failed (H5).** Beating the *best* single lens requires **complementary** information across lenses — synergy. We found none: the lenses are either redundant or independently weak, and the dominant-volume lens (graph) is non-predictive, so any volume-influenced weighting regresses toward noise. The learner couldn't rescue it within the weak outcome signal available.

**Why H9 is interesting but not yet bankable.** It is market-neutral (alpha, not beta), survivorship-clean, and cost-surviving on liquid pools — genuinely rare for a $0 retrospective study. But it rests on **one 49-day window (n=43 strong trades)** with a convex return profile, so the *sign* is robust while the *magnitude* (+37% point estimate) has a wide confidence interval.

---

## 7. Limitations

1. **Single window (the dominant limitation).** All results are one 49-day period (2026-03-29 → 05-18). H9's convexity + bull-leaning window mean a second, regime-different window is required to trust magnitude.
2. **Signal is L3-bound.** `from_role` labels are L3-proprietary; **Alchemy CU cannot reconstruct them** — so a second window requires resuming the L3 surveillance system, not buying data.
3. **Price granularity.** DefiLlama 4h nearest-point; fine for 24–48h returns, imprecise intraday.
4. **Cost model.** Constant-product slippage with TVL-at-enumeration depth (conservative on exit); not exact per-block pool state. Refinable with <100K CU but shown unnecessary at current sizes.
5. **Chains.** Pools lack per-chain labels; tokens resolved base→arbitrum→optimism. bloxroute (a future live-feed option) does not cover Arbitrum/Optimism, where prior signal concentrated.
6. **No execution / no realized P&L.** All "outcomes" are price movements, not filled trades.

---

## 8. Conclusions

1. **The multi-lens decision engine does not work as theorized.** It never beats its best single lens; synthesis dilutes rather than amplifies. The architecture is set aside.
2. **The engineering is sound and complete** — schema, bus, replay, orchestrator, learner, dry-run execution, 479 tests — and produced a rigorous, reproducible *negative* result for ~$0.
3. **One signal survived: H9 (`entropy_drop` on transfer-role diversity)** — a positive-EV, market-neutral, cost-surviving convex edge on liquid tokens, validated on one window.
4. **Execution stays NO-GO.** The formal multi-lens gate failed; H9 is promising but single-window. Read-only charter (I-1/I-3) intact; the execution-authorization decision remains deliberately un-filed (D-037).

---

## 9. Future Work — second-window replication (gated, D-045-pending)

The single test that confirms or kills H9:
1. **Restart L3 surveillance** (`stellar-embrace` on Railway) — stopped 2026-05-26 → 06-01; resumes after 06-01. Consumes L3's own Alchemy budget (separate from the 77M trading CU).
2. **Accumulate ≥14 days** (H9 uses 7d current + 7d baseline windows) — realistically ~3 weeks for a usable sample.
3. **Re-dump** the production DB → local `surveillance.db` (manual: `railway ssh` + `sqlite3 .dump`).
4. **Re-run the H9 gate battery** on the fresh out-of-sample window.

**Earliest meaningful replication: ~late June 2026.** The 2026-05-18 → 06-01 gap is likely permanent (L3 is a forward-only WS monitor). If H9 holds on a second window, it becomes a candidate for a focused **single-signal** study (still behind an I-1 amendment + execution-authorization). If it breaks, it was period-specific.

---

## Appendix A — Decision & Unknown index

**Decisions:** D-020 (Phase 3 spec) · D-021 signal schema · D-022 event bus · D-023 graph lens / UNK-013 · D-024 lens data grounding · D-029 temporal replay / UNK-014 · D-031 regime · D-032 conflict · D-033 orchestrator · D-034 proxy outcome · D-035 decision engine · D-036 learner · D-037 execution dry-run (gate withheld) · D-038 Phase-3 LOOP · D-039 3.5 OOS verdicts · D-040 free price data · D-041 Phase-4 verdict (provisional) · D-042 entity-specific NO-GO final / UNK-015 · D-043 H9 discovery · D-044 H9 validated.

**Unknowns:** UNK-013 (L3↔monitored overlap, resolved) · UNK-014 (temporal dynamics, resolved) · UNK-015 (multi-lens edge real?, resolved: no) · UNK-016 (H9 gates, resolved: validated w/ caveats).

## Appendix B — Key artifacts
`engine/data/`: `phase4_verdicts.json`, `info_lens_oos.log`, `info_lens_survivorship.log`, `info_lens_regime.log`, `info_lens_costs3.log`. Specs: `PHASE_3_MULTI_LENS_ENGINE_SPEC.md`, `PHASE_4_REAL_OUTCOMES_SPEC.md`, `PHASE_5_EXECUTION_TRANSITION_SPEC.md`.

---

*Prepared 2026-05-28. All figures reproducible from the cited scripts against the frozen L3 copy (data through 2026-05-18) + DefiLlama free API. Read-only throughout; $0 incremental spend.*
