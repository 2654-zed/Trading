# Phase 5 — Proxy → Actual Execution (transition spec, DRAFT)

**Status**: DRAFT FOR REVIEW — not approved, not started.
**Author**: agent, 2026-05-28, at user request ("create a later phase where we move from proxy to actual execution").
**Charter impact**: MAXIMAL. This is the phase that would change the project from a *detection + logging research experiment* into a *system that moves real capital on-chain*. It must not be entered lightly, accidentally, or out of sequence.

---

## 0. Read this first — why this phase is different

Every prior phase has been **read-only**. The governing invariants are:
- **I-1**: read-only on-chain, no signing, no private keys.
- **I-3**: read-only against Layer 3.
- Project charter (memory: `project_l3_trading_experiment`): "detection + logging only."

Phase 5 proposes to *reverse* I-1. That is a one-way door: once a key can sign, the failure modes change from "wrong number in a log" to "irreversible loss of funds." This spec is deliberately conservative and gate-heavy. **Nothing in Phase 5 is built until the hard prerequisites below are met AND the user files the explicit authorization decision that D-037 left deliberately absent.**

---

## 1. Hard prerequisites (ALL must hold before Phase 5 starts)

Phase 5 is **blocked** until every one of these is true. Each is a gate, not a guideline.

1. **Phase 4 complete** — a real data + real outcome pipeline exists (bloxroute / restored Alchemy price feed per D-020/D-024). The engine consumes live or faithfully-replayed *price/return* outcomes, not the D-034 forward-flow proxy.
2. **H5 SUPPORTED on real outcomes** — the orchestrator beats the best single lens by the formal **≥15pp** margin on **realized** outcomes, out-of-sample. As of Phase 3 close this is **NOT met**: out-of-sample H5 was **−5.1pp on the proxy** (D-039, UNK-015). Until a real-outcome re-test flips H5 positive, there is no demonstrated edge to execute on. **Executing without a proven edge is just paying fees to lose money.**
3. **UNK-015 RESOLVED** — we know whether the multi-lens edge is real (not a proxy artifact). Resolution decision filed.
4. **Backtested profitability after costs** — a Phase 4 backtest over ≥90 days shows positive expectancy *net of* gas, slippage, bridge fees, and MEV/adverse-selection haircut (reuse the Phase 2 cost model: D-010 static fees, D-016 latency haircut).
5. **Explicit user authorization decision filed** — `decisions/D-NNN_phase-5-execution-authorization.md`, authored by the user, that:
   - explicitly amends I-1 (and scopes the amendment),
   - states the maximum capital at risk,
   - acknowledges irreversibility + total-loss risk,
   - names the kill-switch owner.
   This is the gate D-037 references as "deliberately absent."

If any prerequisite fails, Phase 5 does not begin. No partial starts.

---

## 2. Invariant changes (must be ratified in the authorization decision)

| Invariant | Today | Phase 5 proposal |
|---|---|---|
| **I-1** read-only on-chain, no keys | absolute | **AMENDED**: a single, scoped, hardware-isolated signing key may submit orders, bounded by §5 risk limits. Read-only everywhere else. |
| **I-3** read-only Layer 3 | absolute | **UNCHANGED** — L3 stays read-only forever. |
| **New I-21** capital-at-risk ceiling | — | total at-risk notional may never exceed the figure in the authorization decision; breaching it halts execution. |
| **New I-22** kill-switch supremacy | — | a single manual kill switch disables signing within one block-time; it overrides all engine logic. |
| **New I-23** every order is logged before + after | — | intended order → ledger (pre-trade), fill/revert → ledger (post-trade); no silent orders (extends I-18's spirit). |

---

## 3. Staged rollout (each stage gated; no skipping)

Execution is introduced in graduated stages with explicit promotion criteria. You cannot advance a stage without meeting its bar.

### Stage 5.0 — Paper trading (no key, no funds)
- The dry-run `strategy_router` (D-037) already produces `IntendedOrder`s. Stage 5.0 adds a **paper-fill simulator**: given live order book / pool state at decision time + the cost model, compute a simulated fill + realized P&L, written to the ledger as a real (not proxy) outcome.
- **Promotion bar**: ≥30 days paper trading shows positive net expectancy AND H5 holds on paper-realized outcomes.
- Still $0 at risk. Still no key.

### Stage 5.1 — Micro-capital live (tiny, capped)
- First real signing key. Hard caps: per-trade notional ≤ the "micro" figure in the auth decision (e.g. low hundreds of USD), total at-risk ≤ a small multiple, max N open positions.
- **Promotion bar**: ≥30 days live, realized P&L within X% of paper prediction (model trustworthy), zero risk-limit breaches, zero key-handling incidents.

### Stage 5.2 — Scaled live
- Raise caps incrementally, each raise its own decision. Never raise after a drawdown breach without a written post-mortem.

---

## 4. Components to build (only after §1 gates pass)

- `engine/execution/paper_fill.py` — Stage 5.0 fill simulator (order book / pool-state aware) → realized outcomes to the ledger. Reuses Phase 2 quote + cost modules.
- `engine/execution/signer.py` — Stage 5.1+. Isolated key handling. Loads from a hardware wallet / KMS / env-injected secret that is NEVER committed, NEVER logged (extends I-9 token discipline to private keys). Single narrow `sign_and_send(order)` surface, disabled unless the auth token + live flag + risk-check all pass.
- `engine/execution/risk_manager.py` — pre-trade gate: enforces I-21 (capital ceiling), per-trade cap, max open positions, per-day loss stop, slippage bound. Rejects any order that breaches. Runs BEFORE the signer, always.
- `engine/execution/kill_switch.py` — I-22. A file/flag/endpoint that, when tripped, hard-disables `signer.sign_and_send` within one block-time and cancels open intents. Checked on every order.
- `engine/execution/reconciler.py` — post-trade: match submitted orders to on-chain fills/reverts, write realized outcomes to the ledger (I-23), detect stuck/failed/partially-filled orders, alert.
- Strategy router upgrade: the existing dry-run router gains a live path that is **default-off** and routes through risk_manager → signer only when all gates pass.
- Tests: every risk limit, the kill switch (must disable within target latency), the reconciler against simulated fills/reverts, and a full Stage-5.0 paper-trading integration test.

---

## 5. Risk controls (non-negotiable)

1. **Capital ceiling (I-21)** — total at-risk notional hard-capped at the authorization figure. Breach → halt.
2. **Per-trade cap** — no single order exceeds its stage cap.
3. **Daily loss stop** — cumulative realized loss in a UTC day beyond a threshold → halt signing for the day.
4. **Drawdown stop** — peak-to-trough beyond a threshold → halt + mandatory review.
5. **Kill switch (I-22)** — manual, supreme, sub-block-time. The owner is named in the auth decision.
6. **Slippage / staleness guard** — reject if quote is older than N seconds or expected slippage exceeds bound (reuse D-016 latency-haircut logic).
7. **Key hygiene (extends I-9)** — private key never in chat, code, commits, logs, or memory files. Hardware/KMS isolation. Compromise assumption: a leaked key = total loss, so treat it like one.
8. **Dead-man default** — if the engine, risk_manager, reconciler, or kill-switch heartbeat is unhealthy, signing is disabled by default (fail closed, never fail open).

---

## 6. Success / failure criteria for Phase 5

- **Success**: ≥90 days of scaled live operation with realized net-positive expectancy matching backtest within tolerance, zero risk-limit breaches, zero key incidents.
- **Failure / halt-and-review**: any drawdown-stop trip, any limit breach, realized expectancy materially below backtest, or any key-handling incident → stop, post-mortem, decision before resuming.
- **Abort**: if Phase 4 never flips H5 positive on real outcomes, Phase 5 is abandoned — the engine stays a research / detection instrument. That is an acceptable, honest outcome.

---

## 7. What this spec deliberately does NOT do

- Does NOT authorize execution. That requires the separate user decision in §1.5.
- Does NOT pick a venue, chain, or capital figure — those belong in the authorization decision after Phase 4 informs them.
- Does NOT build any signing code now. This is a plan; the build is gated.

---

## 8. Sequencing summary

```
Phase 3 (DONE)   multi-lens engine, proxy outcomes, dry-run.  Edge UNPROVEN (D-039).
      │
Phase 4 (NEXT)   bloxroute/price feed → REAL outcomes. Re-test H5/H6/H7/H8 for real.
      │          GATE: H5 SUPPORTED ≥15pp on realized outcomes + net-positive backtest.
      │
Phase 5 (THIS)   execution — ONLY if Phase 4 proves edge AND user files the
                 execution-authorization decision (amends I-1).
                 5.0 paper → 5.1 micro-capital → 5.2 scaled. Risk controls throughout.
```

The single most important line in this document: **if Phase 4 does not prove a real edge, Phase 5 does not happen.** Out-of-sample, the engine currently shows no edge over a single lens on the proxy (D-039). Execution is earned by evidence, not assumed.
