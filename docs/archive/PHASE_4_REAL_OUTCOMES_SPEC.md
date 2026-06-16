# Phase 4 — Real Outcomes (the "prove the edge" phase, DRAFT)

**Status**: DRAFT FOR REVIEW — not approved, not started.
**Author**: agent, 2026-05-28, at user request.
**Cost posture**: **~$0 new spend.** Free historical data first; the small remaining Alchemy CU is a *scoped fallback*, not a line item. No bloxroute subscription (that's a Phase 5 / live-execution concern).

---

## 0. Why Phase 4 exists

Phase 3 built a working multi-lens engine but proved nothing about whether it has an *edge*. Every H5–H8 verdict rests on the D-034 **forward-flow proxy**, and the rigorous out-of-sample test was damning: **H5 = −5.1pp** (the orchestrator did NOT beat the best single lens out-of-sample; D-039, UNK-015). All proxy correlations were near-zero (0.016–0.067), which strongly suggests the *proxy itself is too weak a target* — but we cannot prove that without a real one.

**Phase 4's single job: replace the proxy with REAL realized price/return outcomes, then re-test H5–H8 and resolve UNK-015.** The result is the go/no-go gate for Phase 5 (execution). If the edge is real, we proceed; if not, the engine stays an honest research instrument and we have saved the financiers from funding a phantom edge.

This is the most important measurement in the project. It is also nearly free.

---

## 1. What changes vs Phase 3

Exactly one thing changes in the pipeline: **the outcome**. Everything upstream (lenses, bus, synthesis, regime, conflict, orchestrator, decisions, learner, replay harness) is reused unchanged.

| | Phase 3 (proxy) | Phase 4 (real) |
|---|---|---|
| Outcome source | forward org-transfer flow + adversarial events (D-034) | **realized forward price return / volatility** of the relevant pool(s) |
| Data | L3 SQLite (already local) | **historical DEX price/swap data from free sources** |
| Attributor | `OutcomeAttributor` | new `RealizedOutcomeAttributor` (same interface) |
| Verdicts | PROXY-validated | **REAL** (the actual H5–H8 test) |

The `OutcomeAttributor` was deliberately built behind a narrow interface (`compute_outcome(window_end) → (value, ground_truth, detail)`); Phase 4 swaps in a realized-return implementation and the entire replay/learner/analysis/H8-OOS machinery works unchanged.

---

## 2. Data strategy (free-first, CU-reserve fallback)

### Primary (free)
Our monitored pools are dominated by major pairs (WETH/USDC, USDC/USDT) with abundant free price history:
- **DEX subgraphs** (Uniswap v3, Aerodrome/Velodrome) — per-pool swap + price history, free.
- **Dune** free tier — DEX price series.
- **DefiLlama / CoinGecko** free — token price history for the major tokens (coarser, but fine for a first-pass realized-return).

### Fallback (scoped CU)
Only if free sources show gaps on the **signal-driving pools** (the Arbitrum WETH/USDC Slipstream pool from UNK-011, the org_001 cluster pools), OR if the first-pass H5 comes back **borderline** and a high-fidelity confirmatory run is warranted:
- Pull exact historical `Swap` logs via `eth_getLogs` for **those specific pools only**, over **the specific windows with gaps**, batched, under a **hard CU budget set up front** (given the D-017 leak history).
- This is surgical, not a bulk pull. Most of Phase 4 should need zero CU.

### Note on chains
bloxroute's coverage (Base/ETH/BSC/Solana; **not** Arbitrum/Optimism) is irrelevant to Phase 4 — the **backtest is venue-agnostic**. We can source historical Arbitrum/Optimism price data from free sources or the CU reserve regardless of who could execute there later. The bloxroute/chain question belongs to the Phase 5 gate.

---

## 3. Sub-phases

### Sub-phase 4.1 — Historical price data adapter + diagnostic (diagnose-first, per UNK-013 lesson)
- `engine/adapters/price_history.py` — a `PriceHistorySource` that returns realized return / realized volatility for a pool (or the monitored-set aggregate) over a window, backed by a free source. Cached like the L3 adapter.
- **Diagnostic FIRST** (the hard lesson from UNK-013/UNK-014): before building anything on top, verify the free source actually covers our 129 pools — especially the signal-driving ones — at usable time granularity over the replay window (the ~49-day L3 span). Report coverage % and gaps. **If coverage is inadequate AND the CU fallback can't cheaply fill it, stop and report before proceeding.**
- **Acceptance**: ≥1 free source gives realized returns for the signal-driving pools across the replay window; coverage report produced; CU spent = 0 (or a documented, budgeted fallback amount).

### Sub-phase 4.2 — Realized outcome attribution
- `engine/feedback/realized_outcome.py` — `RealizedOutcomeAttributor` implementing the same interface as `OutcomeAttributor`, computing forward realized return (and/or forward realized volatility) over [T, T+horizon]. Outcome semantics resolved per decision action (risk decisions "correct" on adverse forward move; opportunity decisions "correct" on favorable forward move).
- Re-attribute the existing ledgers (or re-run replay) with real outcomes.
- **Acceptance**: every decision gets a real outcome; outcome distribution is non-degenerate (not near-constant like the proxy); tests.

### Sub-phase 4.3 — Re-test H5–H8 on real outcomes + UNK-015 resolution + Phase 5 gate
- Re-run the in-sample analysis (`phase3_analysis.py`) and the **OOS H8/H5 replay** (`replay.py h8_holdout_test`) with real outcomes — the same harness, real numbers.
- Formal verdicts: H5 (≥15pp OOS?), H6, H7 (confirm OOS this time), H8.
- **Resolve UNK-015** with a linked decision: is the multi-lens edge real, or was the proxy the problem?
- Phase 4 LOOP (7-step) + a **Phase 5 go/no-go gate decision**:
  - **GO** → H5 SUPPORTED ≥15pp OOS on real outcomes + net-positive after costs → Phase 5 prerequisites (PHASE_5 §1) start to clear.
  - **NO-GO** → edge not demonstrated → engine stays research-only; Phase 5 abandoned (an acceptable, honest outcome).
- **Acceptance**: real H5–H8 verdicts filed; UNK-015 resolved; explicit Phase 5 go/no-go recorded.

---

## 4. Success / failure criteria for Phase 4

- **Success (the deliverable, regardless of which way it points)**: a trustworthy, real-outcome H5–H8 verdict + UNK-015 resolution + Phase 5 go/no-go. A clean NO-GO is just as valuable as a GO — it prevents wasted execution spend.
- **Failure**: free data + scoped CU together cannot produce realized outcomes of sufficient quality to trust the H5 verdict. Then we either (a) accept a defined CU budget for better data, or (b) declare the edge untestable at current resources and stop.

---

## 5. Invariants (unchanged)

Phase 4 is still **read-only** — I-1 (no keys), I-3 (read-only L3) hold. The only writable artifact remains the separate outcome ledger. No execution. The CU reserve is read-only `eth_getLogs` calls, hard-budgeted.

---

## 6. What this phase does NOT do

- Does NOT execute or sign anything (that's gated to Phase 5).
- Does NOT buy a bloxroute subscription or any live feed.
- Does NOT add lenses or change the engine — only the outcome source.
- Does NOT assume the edge exists — it tests for it, and a negative result is a valid, reportable outcome.

---

## 7. Sequencing

```
Phase 3 (DONE)   engine complete; edge UNPROVEN on proxy (OOS H5 −5.1pp, D-039).
      │
Phase 4 (THIS)   swap proxy → REAL outcomes from FREE historical data (~$0).
      │          Re-test H5–H8 + OOS replay. Resolve UNK-015. Phase 5 go/no-go.
      │          GATE: real H5 ≥15pp OOS + net-positive backtest → GO.
      │
Phase 5          execution — ONLY on a Phase 4 GO + user execution-authorization
                 decision (amends I-1). Paper → micro → scaled.
```

**Bottom line for financiers**: Phase 4 answers "does this thing actually have an edge?" for roughly $0, before anyone is asked to fund execution infrastructure. It is the cheapest, highest-leverage step in the entire project, and it is explicitly designed so that a "no" is a clean, money-saving result.
