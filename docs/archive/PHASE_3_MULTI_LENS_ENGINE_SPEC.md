# Layer 3 Trading Research Experiment — Phase 3 Specification

**Multi-Lens Decision Engine**

**Status:** Draft for user review (translated from user's "Multi-Lens Engine — Build Blueprint v1" handoff doc, 2026-05-25)
**Scope:** Phase 3 (multi-lens signal generation + orchestrator + adjudicated decisions; execution layer behind explicit gate at sub-phase 3.4)
**Chains:** Base + Arbitrum + Optimism (inheriting Phase 2's monitored set; extensible per lens)
**Capital at risk:** $0 through sub-phase 3.3. Sub-phase 3.4 (execution) requires explicit user authorization decision; defaults to off.
**Expected duration:** 2-4 months across the 5 sub-phases for a research-grade end state. Faster if individual lenses ship thin.
**Author:** agent (draft); requires user approval before any code
**Handoff target:** A Claude Code session tasked with translating this specification into working software, following the same sub-phase discipline that worked for Phase 1 + Phase 2

---

## Why Phase 3 exists

Phase 1 (EXP-001) measured the Base-only opportunity landscape and INVALIDATED H1 at the current floors (D-007).

Phase 2 (EXP-002) extended to cross-chain. Three results came out of the LOOP at run-end (`memory/trades/2026-05-25_exp-002-summary.md`):

1. **H2 SUPPORTED-WITH-CAVEAT (D-019)** — Layer 3's behavioral corpus DOES flag detected opportunities (84.5% on cross-chain), but the flag concentrates on one rule + one pool address. The first H2-positive result across both phases.
2. **D-018 reversed UNK-002** — the MSUSD/USDC arb is bursty, not closed. Open ~6-8h, closed ~5 days, recurring.
3. **H1' WEAKENED-not-falsified** — cross-chain emissions exist but appear in narrow bursts; one ~1h window in EXP-002 produced all 12 unique cross-chain keys, no replications in ~13h subsequent detection.

Phase 1 + Phase 2 were DESCRIPTIVE — measure what's there. Phase 3 is PRESCRIPTIVE — build a system that ADJUDICATES across multiple mathematical interpretations of the same raw blockchain state, surfaces structure that no single interpretation catches, and learns which interpretation is right under which conditions.

The architectural premise (from the user's blueprint § 10, non-negotiable):
- Lenses must be independent
- All signals must be normalized
- Orchestrator cannot be a simple average
- Conflicts are first-class signals
- System must learn which lens is right over time

This is NOT "run multiple models and combine outputs." This IS "build a system where different mathematical realities compete, collaborate, and are adjudicated by an adaptive intelligence layer."

---

## MANDATORY PRE-WORK

Read the following files completely before writing any code. After reading, produce a one-paragraph summary; **do not proceed to implementation until the summary is reviewed and approved by Jason.**

1. `Multi-Lens Engine — Build Blueprint v1` (the user's source document this spec translates)
2. `PHASE_2_CROSS_CHAIN_SPEC.md` — Phase 2's sub-phase structure + invariant inheritance pattern
3. `memory/STRATEGY_STATE.md` — current hypothesis state (H1 INVALIDATED, H1' WEAKENED, H2 SUPPORTED-WITH-CAVEAT, H3 newly testable, H4 undetermined)
4. `memory/INVARIANTS.md` — all 14 invariants from Phase 1 + 2 carry into Phase 3 unchanged
5. `memory/decisions/D-019_h2-supported-with-caveat.md` — what L3's flag yield actually looks like + why the caveat
6. `memory/decisions/D-018_uk-002-reversal.md` — bursty-cadence evidence + what regime detection needs to capture
7. `memory/decisions/D-017_ws-subscription-lifecycle.md` — the architectural pattern for transport-owned resource lifecycle (carries into every new lens's network resource handling)
8. `memory/trades/2026-05-25_exp-002-summary.md` — the LOOP write-up that drove this spec
9. `memory/unknowns/UNKNOWNS.md` — UNK-002, UNK-010, UNK-011, UNK-012 are the Phase-3-relevant open questions
10. The existing `layer3_trading_exp/` codebase — every module either becomes a "data ingestion source" for the new architecture OR a lens precursor

Produce a one-paragraph summary covering:
1. What changes in the data path when "detector" becomes "lens" (one of many)
2. Which Phase 1+2 modules become raw-data adapters vs lens implementations vs orchestrator inputs
3. What new abstractions (Signal schema, event bus, lens base interface) the build adds

---

## RESEARCH HYPOTHESES (Phase 3)

Phase 1's H1 is INVALIDATED; H2 SUPPORTED-WITH-CAVEAT; H1' and H4 carry forward as observable side-effects of Phase 3 but are not the load-bearing hypotheses. Phase 3 introduces new hypotheses about the engine itself:

### H5 (Phase 3 primary): orchestrator yield over best single lens

**Statement**: Across N≥1000 adjudicated decisions, the orchestrator's decisions have measurably higher precision (true-positive rate when ground truth is available) than any single lens's signals taken in isolation, by at least 15 percentage points.

**Why we think this**: The blueprint's design rule #3 — orchestrator must outperform simple aggregation. If the orchestrator only matches the best single lens, the multi-lens architecture is overhead with no yield.

**Falsification**: Across N≥1000 decisions, orchestrator precision is ≤ best single lens's precision (within ±5pp confidence interval). Meaning the orchestrator is at best a wash.

### H6 (Phase 3 secondary): regime detection is real

**Statement**: The regime engine identifies regime transitions with ≥70% precision and ≥60% recall against ground-truth regime labels (the labels are human-supplied or derived from post-hoc analysis of price/volume signatures).

**Why we think this**: The blueprint lists 5 explicit regimes (trend / chaotic / low_liquidity / adversarial / exploit_risk). If we can't reliably distinguish them, dynamic weighting has nothing to weight against.

**Falsification**: Either precision <50% (false-regime calls dominate) OR recall <30% (engine misses most real transitions).

### H7 (Phase 3 tertiary): conflicts are first-class signals

**Statement**: Cases where two lenses produce contradicting signals on the same time window (the blueprint's example: stochastic says random, game-theory says intentional) identify opportunities that single-lens analysis misses. Concretely: conflict-flagged events have a measurably different outcome distribution (Mann-Whitney p<0.05) than non-conflict events.

**Why we think this**: The blueprint's design rule #4 + § 5.3 — "Conflicts = alpha". If conflicts don't differentiate, the conflict engine is decoration.

**Falsification**: Conflict-flagged events have the same outcome distribution as non-conflict events (p>0.05 over 7-day window).

### H8 (Phase 3 process): system learns

**Statement**: Lens weights at week 4 are measurably different from the initial weights AND the new weights perform better (on held-out data) than the initial weights. Improvement threshold: ≥10% gain in adjudicated-decision precision.

**Why we think this**: The blueprint's design rule #5. If weights never move from initial values OR move in ways that don't improve performance, the learning component is broken.

**Falsification**: After 4 weeks of feedback-loop closures, weights are within ±5% of initial AND/OR backtested performance with learned weights ≤ performance with initial weights.

### Carried-forward hypotheses from Phase 2

H1, H1', H2, H3, H4 remain as observable side-effects. Phase 3 measures them as a byproduct of the lens implementations (each lens produces signals whose density / margin / flag-rate / chain-pair distributions inform the older hypotheses). But they're not the primary research question anymore.

---

## SUCCESS CRITERIA FOR PHASE 3

Phase 3 succeeds when the following are produced, regardless of whether H5/H6/H7/H8 are supported:

1. A normalized `Signal` schema implemented + enforced via type checks across every lens
2. ≥3 independent lenses (graph + 1 stochastic-class + 1 information-theory-class) producing Signals at production volumes
3. A cross-lens synthesis engine that demonstrably groups signals into composite patterns
4. A regime engine that produces per-window regime classifications + an audit trail of why
5. A conflict engine that surfaces lens contradictions as separate signals
6. A weighting engine with a learning loop that closes against an outcome ledger
7. A decision engine that produces structured `Decision` objects per § 5.4 of the blueprint
8. A feedback loop that closes the signal → decision → outcome → weight-update cycle
9. End-of-Phase-3 LOOP execution with verdicts on H5, H6, H7, H8

Phase 3 does NOT succeed or fail based on whether the hypotheses are supported. The hypotheses are descriptive claims about whether the architecture pays off; the engine itself is the deliverable.

---

## SPEC — what Phase 3 does

### High-level architecture

```
                    RAW BLOCKCHAIN STATE
                            ↓
              (existing Phase 1+2 data ingestion: WS + multicall + L3 sync)
                            ↓
        ┌──────────────────────────────────────────┐
        │              LENS LAYER                   │
        │                                           │
        │  Stochastic │ Topology │ Graph │ Game     │
        │             │          │       │          │
        │           Information theory               │
        └──────────────────────────────────────────┘
                            ↓
                       Signal objects
                            ↓
                     EVENT BUS (pub/sub)
                            ↓
            ┌─────────────────────────────────┐
            │     CROSS-LENS SYNTHESIS         │
            │  (time-window + semantic group)  │
            └─────────────────────────────────┘
                            ↓
                   Composite Signal objects
                            ↓
            ┌─────────────────────────────────┐
            │         ORCHESTRATOR             │
            │                                  │
            │  Regime engine                   │
            │  Dynamic weighting               │
            │  Conflict engine                 │
            │  Decision engine                 │
            └─────────────────────────────────┘
                            ↓
                       Decision objects
                            ↓
            ┌─────────────────────────────────┐
            │   EXECUTION / STRATEGY ROUTER    │
            │   (sub-phase 3.4; gated)         │
            └─────────────────────────────────┘
                            ↓
                         Outcomes
                            ↓
                  FEEDBACK LOOP / LEDGER
                  (updates lens weights)
```

### Package layout (locked at spec-approval per user direction 2026-05-25)

The engine is a **separate top-level Python package**, NOT a subdirectory of `layer3_trading_exp/`:

```
C:\Users\jason\Desktop\Trading\
├── layer3_trading_exp/          ← Phase 1+2 detector (data adapter for Phase 3)
│   └── ... existing modules ...
├── engine/                       ← NEW: Phase 3 multi-lens engine
│   ├── core/
│   │   ├── signal_schema.py
│   │   └── event_bus.py
│   ├── lenses/
│   │   ├── _base.py
│   │   ├── graph/
│   │   ├── stochastic/
│   │   ├── information/
│   │   ├── topology/             # (future)
│   │   └── game/                 # (future)
│   ├── synthesis/
│   ├── orchestrator/
│   ├── execution/                # gated; sub-phase 3.4
│   ├── feedback/
│   ├── adapters/                 # raw-data adapter shims (Alchemy via Phase 1+2 today; bloxroute later)
│   ├── scripts/
│   └── tests/
├── tests/                        ← shared test infra (if any)
├── PHASE_3_MULTI_LENS_ENGINE_SPEC.md
└── memory/                       ← unchanged
```

Rationale: keeping `engine/` separate preserves a clean rebuild path. If Phase 3 ever needs to ship as its own product / repo, no surgery required. Also: `layer3_trading_exp/` stays frozen-by-discipline at Phase 2 final state (per I-2 spirit) while `engine/` evolves freely.

**Import discipline**: `engine/*` may import from `layer3_trading_exp/*` (read-only data adapter pattern). `layer3_trading_exp/*` MUST NOT import from `engine/*`.

### Integration points

**Read from existing Phase 1+2 infrastructure**:
- `pool_monitor.py` / `chain_monitor.py` — block ticks become events on the bus
- L3 sync (`sync_l3_db.py` + `Layer3Client`) — behavioral intelligence corpus for graph + game-theory lenses
- `monitored_pools.json` — frozen universe; same per-run freeze discipline
- Existing JSONL writers — generalize to log Signals + Decisions + Outcomes

**Write to**:
- Local filesystem: per-lens signal logs (JSONL), composite signal logs, decision logs, outcome ledger (CSV/SQLite)
- Optional: time-series store for lens weights over time
- No on-chain writes through sub-phase 3.3; explicit gated execution in sub-phase 3.4

**Does not touch**:
- Sub-phase 3.3 maintains all Phase 1+2 NOT-build constraints (no execution, no signing, no wallets, no LLM in runtime, no Layer 3 writes)
- Sub-phase 3.4 (execution) introduces signing path BEHIND a feature flag that is EXPLICITLY ABSENT in pre-3.4 builds

### What Phase 3 does NOT do

- Does NOT couple lenses to each other (per I-15 below). Only the orchestrator may read across lenses.
- Does NOT use LLM inference in runtime (I-4 carries forward).
- Does NOT skip the build order (per blueprint § 9 — Phase 1 foundation before Phase 2 expansion before Phase 3 intelligence before Phase 4 edge).
- Does NOT execute trades through sub-phase 3.3. Sub-phase 3.4 introduces an execution layer gated behind explicit user authorization decision (analogous to how Phase 2's execution was deferred).
- Does NOT replace Phase 1+2's data ingestion — uses it.
- Does NOT freeze the lens set forever. New lenses can be added in future sub-phases; existing lenses MUST NOT change behavior mid-run (per I-2 carried forward).

---

## INVARIANTS (carry forward from Phase 1 + 2 + Phase 3 additions)

All 14 invariants from `memory/INVARIANTS.md` apply unchanged. Six additions specific to Phase 3:

**I-15 (Phase 3): Lens independence.**
No lens may read another lens's outputs, internal state, or Signal stream. Only the orchestrator may compose across lenses. A lens may read raw blockchain state, L3 corpus, and its own historical signals. Violation: any `import` of another lens's module from within a lens module.

**I-16 (Phase 3): Signal schema normalization.**
Every lens MUST emit Signal objects conforming to the exact `signal_schema.py::Signal` shape (typed dataclass with `id`, `timestamp`, `lens`, `type`, `strength`, `confidence`, `time_horizon`, `metadata`, `vector?`). Enforcement: `validate_signal()` invoked on every event-bus publish; non-conforming signals dropped with a loud error. The runtime checks the type at publish time, not at lens-internal computation time.

**I-17 (Phase 3): Orchestrator may not be a simple average.**
The weighting engine may not collapse Signals into a scalar by uniform-average or by weighted-average alone. Either weighted-average must combine with a non-linear conflict adjustment, OR the aggregation function must be at least bilinear in signal strengths × confidences. Rationale: blueprint § 10 rule #3.

**I-18 (Phase 3): Conflicts are first-class signals.**
When the conflict engine detects contradicting signals across lenses, it emits a `ConflictSignal` that goes through the same event bus → orchestrator path as a primary signal. Conflicts must NOT be silently resolved or discarded. Storage: every conflict gets a row in the outcome ledger keyed by (timestamp, lenses_involved, contradicting_types).

**I-19 (Phase 3): System must learn.**
Every Decision must produce an Outcome record within the configured horizon. Lens weights must update from outcomes via the configured learning algorithm at the configured cadence. If learning has not run for >7 days, the orchestrator must surface a loud warning (analogous to I-6).

**I-20 (Phase 3): Feedback loop is closed.**
Every Signal that contributes to a Decision must be traceable from the Decision back through the Outcome ledger. No "fire-and-forget" decisions allowed once the feedback loop is enabled (sub-phase 3.3 onward). Decisions emitted before the loop is enabled (sub-phase 3.1 / 3.2 dry runs) are tagged `loop_disabled=true` and excluded from learning.

---

## SUB-PHASES

Following the blueprint's § 9 build order. Each sub-phase has explicit acceptance criteria. Stop at each sub-phase boundary, report status, wait for approval before proceeding.

### Sub-phase 3.1: Foundation (Signal schema + event bus + first lens)

**Goal**: Prove the abstraction. ONE lens emitting Signals through the bus to a stub consumer.

**Files to create**:
- `engine/core/signal_schema.py` — `Signal` typed dataclass + `validate_signal()` per I-16
- `engine/core/event_bus.py` — asyncio-based pub/sub; topic-routed (per blueprint § 8); deliver-once semantics; subscribers register topic prefixes
- `engine/lenses/_base.py` — abstract `Lens` class with `async def run()` + emits via `event_bus.publish("signal", signal)`
- `engine/lenses/graph/lens.py` — first concrete lens (graph centrality on the L3 contracts/deployers subgraph; reuses Phase 2's L3 sync)
- `engine/consumers/signal_logger.py` — stub orchestrator-replacement; writes received signals to JSONL for inspection
- `engine/scripts/run_engine_phase_3_1.py` — wires lens + bus + consumer for a smoke run
- `tests/engine/test_signal_schema.py` + `tests/engine/test_event_bus.py` + `tests/engine/test_graph_lens.py`

**Files NOT to create yet** (defer to later sub-phases):
- Anything in `synthesis/`, `orchestrator/`, `execution/`
- Other lens directories

**Acceptance criteria**:
- [ ] `Signal` dataclass implemented with all 9 fields (id, timestamp, lens, type, strength, confidence, time_horizon, metadata, vector)
- [ ] `validate_signal()` rejects malformed signals with a clear error
- [ ] Event bus delivers signals to ≥2 subscribers reliably (no drops in a stress test of 10K signals/sec)
- [ ] Graph lens emits at least 3 distinct signal types (e.g. `cluster_detected`, `centrality_spike`, `subgraph_anomaly`) against real Phase 2 monitored set
- [ ] 30-minute smoke run produces ≥100 Signals, all validating cleanly
- [ ] Existing 309+ test suite passes (Phase 1+2 code unchanged)

**Stop here. Report**: signal volume per lens type per hour, event bus throughput measured, signals/sec the graph lens sustains, any unexpected schema constraints surfaced. **Wait for approval before sub-phase 3.2.**

---

### Sub-phase 3.2: Expansion (2nd + 3rd lens + basic orchestrator with static weights)

**Goal**: Prove the abstraction GENERALIZES to multiple lenses with mathematically different reasoning, AND introduce the orchestrator skeleton with static weights.

**Files to create / modify**:
- `engine/lenses/stochastic/lens.py` — volatility regime shift detection (per blueprint § 3.1); reads price + liquidity streams; emits `volatility_regime_shift`, `drift_change`, `diffusion_anomaly`
- `engine/lenses/information/lens.py` — entropy + KL divergence on per-block token-flow distributions (per blueprint § 3.5); emits `entropy_drop`, `regime_surprise`, `divergence_spike`
- `engine/orchestrator/orchestrator.py` — minimal skeleton: subscribes to all `signal` topics, holds a `getLensWeights(regime)` function returning STATIC weights per the blueprint § 5.2 example, multiplies signal strengths by weights, emits aggregate metric to a logger consumer
- `engine/orchestrator/static_weights.py` — initial weight table per blueprint § 5.2: `{stochastic: 0.2, topology: 0.2, graph: 0.25, game: 0.25, information: 0.1}`
- Tests for each new lens + the orchestrator's aggregation math

**Acceptance criteria**:
- [ ] 3 lenses producing distinct signals concurrently from the same raw data stream
- [ ] Per I-15: lens modules import only from `engine/core/`, `engine/lenses/_base`, and stdlib + scientific deps. No lens-to-lens imports.
- [ ] Orchestrator subscribes via the event bus + processes all 3 lenses' signals within latency target (p95 <500ms from publish to aggregate-emit)
- [ ] Static-weights aggregation produces sensible per-window summary (e.g. "in 5-min window W, weighted-signal-score = 0.42 driven by graph 0.6 × 0.25 + stochastic 0.3 × 0.2 + ...")
- [ ] Smoke run emits ≥10K total signals across 3 lenses + **one aggregate score per elapsed window** (`expected_windows = floor(run_seconds / window_seconds)`, ±1 for partial boundary windows); all schema-valid. *(Amended 2026-05-28 per D-028: the aggregate-score count is window-size-dependent, not an absolute number. The original "≥100 aggregate scores in 6h" assumed a sub-minute window; with the 5-min default a 6h run yields ~72 windows. The real bar is: the orchestrator emits exactly one aggregate per elapsed window with zero loss. Run duration may be shortened (e.g. 1h per user direction 2026-05-27) with the signal bar scaled proportionally.)*
- [ ] No regressions in sub-phase 3.1 acceptance criteria

**Stop here. Report**: per-lens signal density distribution, observed lens-output correlation matrix (early signal-of-signals correlation hint), orchestrator wall time per window, any cross-lens type-name collisions. **Wait for approval before sub-phase 3.3.**

---

### Sub-phase 3.3: Intelligence (cross-lens synthesis + regime engine + conflict engine)

**Goal**: The orchestrator becomes adjudicating, not just aggregating. Composite patterns emerge. Conflicts surface. Regime gets detected.

**Files to create / modify**:
- `engine/synthesis/cross_lens_engine.py` — implements the blueprint § 4 synthesis logic: time-window cluster signals, group by semantic similarity (type + metadata embedding), detect multi-lens convergence, emit `CompositeSignal` events
- `engine/orchestrator/regime_engine.py` — classifies the current window into one of `{trend, chaotic, low_liquidity, adversarial, exploit_risk}` (per blueprint § 5.1). Initial implementation: rule-based classifier using features from the existing lens signals + raw price/volume data. Returns regime label + confidence + per-feature contribution log.
- `engine/orchestrator/conflict_engine.py` — detects contradicting signals across lenses per I-18 + blueprint § 5.3. Emits `ConflictSignal` events. Examples: graph says coordinated, stochastic says random → emit `hidden_coordination` flag.
- `engine/orchestrator/orchestrator.py` (modify) — weighting engine now consumes regime label and routes to a regime-specific weight table; consumes ConflictSignal to apply non-linear adjustment (per I-17)
- Optional: `engine/lenses/topology/lens.py` and `engine/lenses/game/lens.py` (the remaining 2 lenses from the blueprint § 3) for fuller coverage
- Outcome ledger schema (deferred storage; ledger active in sub-phase 3.4)
- Tests for each engine module

**Acceptance criteria**:
- [ ] Cross-lens synthesis produces ≥1 CompositeSignal per real-world coordinated event in a backtest window (validate against known historical multi-signal scenarios — e.g. the May 17 cross-chain burst)
- [ ] Regime engine classifies each window with a label + confidence; manual spot-check on a **sample of ≥100 windows** shows ≥70% intuitive correctness (proxy for H6 pre-validation). *(Amended 2026-05-28 per D-028: the 100-window sample drives the duration/window-size — e.g. 100 windows at 5-min = ~8.3h of temporal-replay data, or use a smaller window for a shorter run. The bar is sample size, not a fixed wall-clock. Requires UNK-014 resolution first: regime classification is meaningless on static data — temporal replay must be implemented before this criterion can be evaluated.)*
- [ ] Conflict engine emits ≥1 ConflictSignal per 1000 Signals in a backtest window (positive rate; calibration TBD)
- [ ] Per I-17: aggregation function is not a simple average — verify via unit test that mixing equal-strength signals from 2 lenses in conflict produces a DIFFERENT score than the same signals in agreement
- [ ] Outcome ledger schema designed + reviewed (no actual outcomes written yet)
- [ ] Smoke run of 24 hours: full pipeline produces lens signals → composites → regime labels → conflict flags → adjudicated aggregate scores. No silent failures.

**Stop here. Report**: composite signal density vs raw signal density, regime distribution over the 24h window, conflict density + categories. **Wait for approval before sub-phase 3.4.**

---

### Sub-phase 3.4: Edge (meta-learning + dynamic weighting + execution layer)

**Goal**: Close the feedback loop. The system learns. Execution becomes possible (gated).

**Files to create / modify**:
- `engine/orchestrator/decision_engine.py` — converts adjudicated signals into `Decision` objects per blueprint § 5.4 (action ∈ {enter, exit, hold, hedge}, confidence, riskScore, rationale)
- `engine/orchestrator/weighting_engine.py` — dynamic, learns from outcomes. Initial algorithm: per-regime exponential-decay-weighted average of past lens outcome contributions. Reads from outcome ledger, writes updated weights table.
- `engine/feedback/outcome_ledger.py` — SQLite/JSONL storage for decisions + outcomes. Decisions get an `outcome_at` deadline; outcomes are computed when the deadline arrives (via post-hoc analysis OR — sub-phase 3.4 gated — execution result).
- `engine/feedback/learner.py` — runs nightly: pulls outcomes since last run, updates per-regime per-lens weights, persists new weights table.
- `engine/execution/strategy_router.py` — translates `Decision` → executable order (sub-phase 3.4 with execution gate). Initially: dry-run mode logs intended order, no on-chain action. Behind a feature flag explicitly absent in pre-3.4 builds.
- Tests for each new module + the closed-loop integration test (decision → simulated outcome → weight update)
- `decisions/D-NNN_phase-3-execution-authorization.md` — required separate decision before execution gate flips on

**Acceptance criteria**:
- [ ] Decision engine produces structured Decision objects from adjudicated aggregates; manual sanity check on 100 decisions shows the `rationale` field captures the lens contributions
- [ ] Outcome ledger closes the loop in dry-run mode: every Decision gets a stub Outcome computed within the configured horizon (e.g. 1h post-decision price change as a proxy for trade outcome)
- [ ] Learner runs nightly, weights table updates measurably (vs initial), persists across restarts
- [ ] Per I-19: dashboard / log surface shows the learner ran successfully OR loud warning if >7 days since last run
- [ ] Strategy router operates in dry-run mode by default; toggling the execution feature flag requires the explicit `D-NNN_phase-3-execution-authorization` decision
- [ ] Closed-loop integration test passes (synthetic signal → decision → simulated outcome → weight update; verify weight changes)
- [ ] LOOP fires at sub-phase end (per I-10) and writes the Phase 3 end-of-experiment summary with verdicts on H5, H6, H7, H8

**Stop here. Report**: decisions emitted in last 24h, outcome attribution success rate, weight changes by lens by regime, full LOOP execution.

---

### Sub-phase 3.5 (NEW — not in blueprint, but mandated by blueprint § 7): feedback loop infrastructure consolidation

**Goal**: The blueprint § 7 mandates a feedback loop but doesn't sequence it. This sub-phase explicitly hardens the infrastructure so the loop is robust + observable, NOT a side effect.

**Files to create / modify**:
- `engine/feedback/ledger_admin.py` — query / replay / repair operations for the outcome ledger
- `engine/feedback/observability.py` — per-lens-per-regime performance dashboard (CSV / Markdown / inline plots)
- `engine/feedback/replay.py` — given a frozen window of raw blockchain state, replay through the engine with current weights vs initial weights; output the precision-difference for H8 validation

**Acceptance criteria**:
- [ ] Ledger admin: can dump ledger contents, replay a window, repair missing outcomes
- [ ] Observability: produces a weekly report showing per-lens precision + recall per regime
- [ ] Replay: enables H8 falsification testing (compare learned weights vs initial on held-out window)
- [ ] LOOP at sub-phase end with formal H5/H6/H7/H8 verdicts

---

## DECISIONS THIS SPEC LOCKS IN

If user approves this spec, the following decisions are committed (each gets a full `decisions/D-NNN_*.md` at the boundary it resolves):

1. **D-NNN_phase-3-spec-approved** — umbrella approval (analogous to D-009 for Phase 2)
2. **D-NNN_signal-schema-locked** — the exact 9-field Signal shape from blueprint § 2 (resolves H5/H6/H7/H8 testability gate)
3. **D-NNN_event-bus-implementation** — asyncio-queues + topic-routing (vs alternatives: ZeroMQ, redis pubsub, kafka). Asyncio is correct given Phase 1+2 already use it; alternatives bring deploy complexity for no clear gain at this scale.
4. **D-NNN_first-lens-graph** — confirms blueprint's recommendation. We have the most groundwork via L3 sync.
5. **D-NNN_regime-taxonomy** — the 5-regime set from blueprint § 5.1. Locked at sub-phase 3.3 start so regime engine has stable target.
6. **D-NNN_lens-weight-initialization** — initial static weights per blueprint § 5.2. First learning iteration only allowed to deviate after sub-phase 3.4 ledger exists.
7. **D-NNN_phase-3-execution-authorization** — REQUIRED before sub-phase 3.4 execution feature flag flips on. Explicit user authorization. Currently NOT granted; spec assumes off-by-default.
8. **D-NNN_outcome-attribution-method** — how `Outcome` records get computed from `Decision` records. Sub-phase 3.4 decision; options include 1h forward price-change, post-hoc realized PnL (requires execution), or proxy metrics from monitored pools.

Plus one running decision per LOOP execution at sub-phase boundaries (analogous to D-014 / D-018 / D-019).

---

## REVERSAL TRIGGERS FOR THIS SPEC

The spec is reversed (back to Phase 2-style descriptive measurement, or a different architecture) if any of:

- **No single lens (after sub-phase 3.1) produces ≥10 distinct Signal types** at sustainable rates. Either the abstraction is wrong-shaped OR our raw data isn't rich enough to feed multiple lenses.
- **Lens independence (I-15) cannot be maintained**. If 2 lenses persistently need shared state, the abstraction is wrong.
- **Orchestrator can't beat best-single-lens after sub-phase 3.4** (H5 falsified with N≥1000 decisions). Then the multi-lens architecture has no yield over a simpler best-lens approach.
- **Regime engine fails H6 falsification** (precision <50% OR recall <30%). Then dynamic weighting has nothing meaningful to weight against; collapse back to static.
- **System cannot learn** (H8 falsified). Then weights stay static; the engine is non-adaptive (functional but not "intelligent" in the blueprint's sense).
- **Alchemy CU budget exceeded (D-017 reversal trigger)** as a result of multi-lens read amplification. Then we scale back to fewer lenses OR move some lenses to non-Alchemy data sources.

Partial reversals are allowed: a single lens can be dropped without unwinding the whole spec.

---

## WHAT THIS SPEC ASSUMES

- Phase 1 + Phase 2 infrastructure is reusable as a data ingestion adapter (verified at LOOP step 1 of the user's review of this spec)
- D-017's WebSocket subscription lifecycle fix is in place and works (post-EXP-002 LOOP closeout)
- The user maintains shared L3 access (per I-3) — graph + game-theory lenses depend heavily on L3 corpus
- The shared Alchemy budget can absorb the increased read amplification from multiple lenses reading raw state in parallel. Pessimistic estimate: 3-5x current per-block read volume. If unacceptable, we either (a) cache aggressively at the data-ingestion layer or (b) reduce sampling cadence on lower-priority lenses.
- The user wants Phase 3 to focus on the engine ARCHITECTURE rather than maximally-many-lenses. Better to ship 3 deep lenses + a working orchestrator than 5 shallow lenses + a stub orchestrator.

---

## OUT OF SCOPE (NOT in Phase 3)

- **Execution-mode trading** through sub-phase 3.3 (Phase 1+2 NOT-build constraint carries forward; sub-phase 3.4 introduces it gated)
- **Predictive price modeling** — the orchestrator's job is adjudicating signals on observable structure, not predicting price direction independently
- **HFT-grade latency** — decision cadence is sub-second to 1-minute; we are not competing with co-located MEV bots
- **All 5 lenses at full mathematical depth** simultaneously — sub-phase 3.1/3.2/3.3 prioritize getting 3 lenses production-ready; topology + game-theory lenses can be added later
- **Cross-chain triangle (3-chain) opportunities** — same constraint as Phase 2 (modeled as composition of 2-chain hops)
- **Layer 3 corpus extension or reformulation** — I-3 still binds
- **Replacing Phase 1+2 data ingestion** — extending, not replacing
- **Multi-objective decision optimization** — one decision per timestep; no Pareto frontier work
- **bloxroute migration** — explicitly **deferred to Phase 4** per user direction at spec approval (2026-05-25). Phase 3 ships on the existing Alchemy WS + RPC + L3 corpus data path (the adapter layer at `engine/adapters/`). Once the engine is coded, tested, and validated, the next phase swaps the adapter implementation to read from bloxroute's mempool stream. **Important architectural note**: bloxroute is fundamentally different from Alchemy — it surfaces pending transactions (mempool) rather than mined blocks. This unlocks meaningful new capability for the game-theory lens (MEV pattern detection on pending TX) but does NOT replace block-state reads needed by other lenses. The Phase 4 spec will detail the dual-source architecture. For Phase 3, design the `engine/adapters/` interface so swapping data sources is a Phase 4 effort, not a re-architecture.

---

## NEXT STEP

User reviews this spec. If approved:

1. Agent files the umbrella `D-NNN_phase-3-spec-approved.md` decision referencing this spec
2. Agent starts sub-phase 3.1 with the mandatory pre-work summary
3. The Phase 2 outstanding items (UNK-010, UNK-011, UNK-012 resolution; H1' falsification verdict; H3 distributional test) close out in parallel since they don't block Phase 3 sub-phases

If not approved, agent edits this spec per feedback and re-presents.
