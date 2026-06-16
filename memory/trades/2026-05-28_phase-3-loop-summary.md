# Phase 3 end-of-experiment LOOP summary (sub-phase 3.4b)

**Date**: 2026-05-28
**Trigger**: completion of sub-phase 3.4 (the multi-lens engine's first closed feedback loop) — per I-10 the LOOP must fire at a sub-phase boundary.
**Scope**: the full Phase 3 engine, validated via $0-CU temporal replay over 49.2 days of historical L3 data (200 windows). All verdicts are **PROXY-validated** (outcome = forward flow/adversarial-event escalation per D-034, NOT realized P&L).

---

## Step 1 — Opportunity Review (engine output review)

Replay produced, per 200-window pass:
- **7,938 signals** across 3 lenses / 9 types (graph 7,632 · information 220 · stochastic 86)
- **238 composite signals** (multi-lens convergence, all entity-anchored)
- **200 regime labels** (adversarial 124 · trend 62 · chaotic 7 · exploit_risk 4 · low_liquidity 3)
- **194 conflicts** (coordinated_regime_shift 127 · hidden_coordination 33 · concealed_adversary 33 · order_chaos_conflict 1)
- **200 orchestrator aggregates** (166 distinct scores; min 0.045, max 0.832)
- **200 decisions** → ledger; **200 outcomes** attributed (escalated 23 · quiet 177)
- **0 validation failures**, **$0 CU**, **execution disabled**

## Step 2 — Strategy Check

The multi-lens architecture now runs end-to-end: lenses → bus → synthesis/regime/conflict → orchestrator (regime-aware, non-linear) → decisions → ledger → outcomes → learner → updated weights. The dynamic-weighting loop is closed (dry-run). This matches the blueprint's intended shape. The strategy remains **research/detection only** — D-037 keeps execution unreachable.

## Step 3 — Risk Check

- I-1/I-3 preserved (read-only; L3 never written; outcomes are a separate writable SQLite).
- Execution hard-disabled (D-037): no signing path exists.
- $0 CU maintained across all Phase 3 sub-phases (D-024 baseline holds).
- Principal risk surface = the **proxy outcome** (D-034). Every learned weight + H-verdict inherits the proxy's limitation. This is the load-bearing caveat for the whole phase.

## Step 4 — Unknown Extraction

- **UNK-013** RESOLVED (D-023): L3↔monitored-set overlap via interaction tables.
- **UNK-014** RESOLVED (D-029): temporal replay gives time-varying signal.
- **NEW UNK-015** (filed): the H5 margin is only +7.0pp (below the formal ≥15pp bar). Is that because (a) the proxy outcome is too weak to separate orchestrator from best-lens, (b) the lenses are too correlated, or (c) the orchestrator genuinely adds little? Resolvable only with Phase 4 real outcomes + the 3.5 replay harness.

## Step 5 — Decision Logging

Filed this sub-phase: D-034 (outcome proxy), D-035 (decision engine + ledger), D-036 (learner/I-19), D-037 (dry-run router / execution-not-authorized), D-038 (this LOOP). UNK-015 opened.

## Step 6 — Hypothesis Scoring (Phase 3 H5–H8)

All verdicts **PROXY-validated**; real-P&L validation deferred to Phase 4.

- **H5 (orchestrator > best single lens by ≥15pp)** — **WEAK-SUPPORT-PROXY**. The orchestrator's score↔outcome correlation (|0.242|) DID beat the best single lens (graph, |0.172|), but by only **+7.0pp** — short of the formal ≥15pp bar. Directionally correct, magnitude unmet. Not falsified (orchestrator is not ≤ best lens), but not cleanly supported either. → UNK-015.
- **H6 (regime detection is real)** — **SUPPORTED-PROXY**. Rule-based regime engine classifies every window with confidence; spot-check 20/20 windows intuitively correct (≥70% bar cleared), coherent temporal narrative (quiet→trend→adversarial). (D-031.)
- **H7 (conflicts are alpha)** — **SUPPORTED-PROXY**. Conflict-flagged windows show higher mean forward outcome (0.129 vs 0.083; rank effect +0.107) — conflicts associate with subsequent escalation, consistent with "conflicts = alpha." Effect is modest; Mann-Whitney-proxy positive. (D-032.)
- **H8 (system learns)** — **SUPPORTED-PROXY**. Learner moves per-regime weights measurably (L1 deltas 0.38–0.90), persists across restarts, and the learned weights demonstrably change decisions (enter 0→40). Rigorous learned-vs-initial precision comparison on a held-out window is the explicit job of sub-phase 3.5. (D-036.)

## Step 7 — Next Focus Selection

Sub-phase **3.5** (feedback-loop hardening): ledger admin/replay/repair, per-lens-per-regime observability dashboard, and the **H8 falsification replay** (learned vs initial weights on a held-out window) — which also sharpens the H5 question (UNK-015). The deeper unlock — turning every PROXY verdict into a real one — is **Phase 4** (bloxroute/price feed), where realized outcomes replace the flow proxy.

---

## Bottom line (as of 3.4b — superseded by the 3.5 update below)

Phase 3 built a working multi-lens decision engine with a closed (dry-run) learning loop at $0 CU. On the forward-activity proxy: regime detection and conflict-as-alpha hold up, the system demonstrably learns, and the orchestrator edges out the best single lens — but not yet by the margin H5 demands. Whether that margin is real or a proxy artifact is the central open question carried into 3.5 + Phase 4.

---

## 3.5 FINAL VERDICT UPDATE (out-of-sample, D-039) — Phase 3 close

The rigorous out-of-sample test (train 70% → holdout 30%) overturned the optimistic in-sample read:

| Hypothesis | In-sample (3.4b) | Out-of-sample (3.5) | Final |
|---|---|---|---|
| **H5** orch > best lens ≥15pp | +7.0pp (weak) | **−5.1pp** | **NOT-SUPPORTED (proxy)** — orchestrator does NOT beat best single lens out-of-sample |
| **H6** regime real | 20/20 spot-check | regimes carry outcome structure | **SUPPORTED-PROXY** |
| **H7** conflicts = alpha | +0.107 rank (in-sample) | not separately confirmed OOS | **SUPPORTED-IN-SAMPLE-ONLY** (downgraded) |
| **H8** system learns | weights move + change decisions | learned > static +2.6pp OOS (both ~0) | **WEAKLY-SUPPORTED-OOS** |

**Honest Phase 3 conclusion**: the *engine architecture is complete and works* (470 tests, $0 CU, closed dry-run loop), but the *core value claim — that multi-lens synthesis beats the best single lens — is UNPROVEN, and out-of-sample it looks negative on the forward-flow proxy.* All proxy correlations are near-zero (0.016–0.067), which strongly implicates proxy weakness (the forward-flow target may simply not be predictable from these signals). The decisive test requires **real outcomes (Phase 4)** — until then, the orchestrator's edge is not demonstrated. This is exactly the kind of result the LOOP discipline exists to surface rather than paper over.

**What carries forward**: UNK-015 stays OPEN (proxy-weakness vs genuine-no-edge, only Phase 4 disambiguates). Phase 4 (bloxroute/price feed → real outcomes) is the gate to a real H5 verdict. Execution stays OFF (D-037) regardless until a real edge is proven.
