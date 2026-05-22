# Layer 3 Trading Research Experiment — Phase 1 Specification

**Status:** Draft for handoff to Claude Code implementation session
**Scope:** Phase 1 (opportunity detection only, no on-chain execution)
**Chain:** Base (Ethereum L2, Coinbase sequencer)
**Capital at risk:** $0 (Phase 1 is detection and logging only)
**Expected duration:** 30 days of continuous observation OR 30,000 logged opportunities, whichever comes first
**Author:** Jason Trinh
**Handoff target:** A fresh Claude Code session tasked with translating this specification into working software

---

## Purpose

This document specifies a research experiment to measure the opportunity landscape for arbitrage trading on Base and to characterize how Layer 3's behavioral intelligence corpus classifies those opportunities. The experiment is intentionally limited to detection and logging. No trades are executed. No capital is deployed. No flash loans are taken.

The research questions answered by Phase 1 determine whether Phase 2 (execution against filtered opportunities) happens at all. A new Claude Code session will implement Phase 2 separately if Phase 1 findings justify it.

Claude Code's job is to translate this specification into working software. Design decisions are made in this specification, not at implementation time. If anything in this spec is ambiguous, pause and ask. If anything seems wrong, pause and ask. Do not make design decisions autonomously.

---

## MANDATORY PRE-WORK

Read the following files completely before writing any code:

1. `/mnt/user-data/uploads/layer3_consumable_intelligence.md` — the Layer 3 consumable intelligence surface reference. Every query in this spec traces back to a signal documented in this file.

2. The Layer 3 production HTTP API schema at `https://spypy.up.railway.app/docs` (public OpenAPI documentation).

3. Uniswap V3 and Aerodrome pool contract interfaces — specifically the `swap`, `slot0`, and `liquidity` methods. Source: official documentation, not this spec.

4. Base chain block time and finality characteristics — relevant to opportunity freshness windows.

After reading, produce a one-paragraph summary of:
1. What the Layer 3 consumable surface provides and what it does not
2. What pool interfaces on Base the detection software will interact with (read-only)
3. What the opportunity discovery loop will do from tick to tick

**Do not proceed to implementation until this summary is reviewed and approved by Jason.**

---

## RESEARCH HYPOTHESIS AND MEASUREMENT FRAMEWORK

### Primary hypothesis

**H1:** Base has 1,000 or more arbitrage opportunities per day at margins viable for flash-loan-funded execution (gross margin ≥ 0.3% after gas, before slippage).

This is a measurement claim, not a profitability claim. The answer is determined by counting, not by trading.

### Secondary hypothesis

**H2:** Layer 3's behavioral intelligence corpus flags a meaningful, non-zero percentage of observed opportunities as elevated-risk through one or more Tier A or Tier B filters.

"Meaningful" is defined as ≥1% of observed opportunities for this phase. Lower rates indicate the corpus does not materially interact with normal arbitrage routing on Base. Higher rates indicate it does, and Phase 2 execution-side research becomes justified.

### Tertiary hypothesis

**H3:** The distribution of margins, pool types, and token involvement differs between Layer 3-flagged opportunities and Layer 3-clean opportunities in statistically observable ways.

If H3 is supported, Layer 3's intelligence is identifying a structurally different subset of the opportunity space — which is the research finding that justifies any Phase 2 work.

### Success criteria for Phase 1

Phase 1 succeeds when the following are produced, regardless of what H1/H2/H3 turn out to be:

1. A log of ≥30,000 opportunities OR 30 days of continuous observation, whichever comes first
2. Per-opportunity records including: timestamp, pools involved, tokens involved, computed margin, Layer 3 filter evaluation results with per-filter traces
3. Daily rollup statistics (opportunities per day, filter activation rates, margin distribution)
4. An end-of-run analysis answering H1, H2, H3 with statistical confidence intervals where appropriate
5. No on-chain state changes caused by the detection software (verified via address activity check)

Phase 1 does NOT succeed or fail based on whether H1/H2/H3 are supported. The hypotheses are descriptive research claims, not performance targets.

### End-of-run analysis

At end of run, Jason (not Claude Code, not the trading software) conducts the analysis. Jason writes the findings report. Jason distributes findings to Scott and Richard.

The software produces the data. The software does not produce conclusions.

---

## SPEC (get approval before implementation)

### What this feature does

The Phase 1 software is a detection-and-logging system that:

1. Continuously monitors a defined set of DEX pools on Base for price discrepancies that constitute arbitrage opportunities
2. For each opportunity detected, queries Layer 3's consumable intelligence surface to classify the contracts and tokens involved
3. Logs every opportunity with full intelligence context to a local time-series store
4. Produces daily rollup reports summarizing opportunity counts, filter activation rates, and distributional properties
5. Runs for 30 days or until 30,000 opportunities are logged, whichever comes first
6. Does not interact with any on-chain contract beyond read-only view calls to pool contracts

### Integration points

**Read from Layer 3:**
- Local SQLite copy of Layer 3's production database, synced every 15 minutes via `railway ssh` or the `/admin/sync-*` HTTP endpoints
- Freshness check via `fresh_tables` envelope in Layer 3's HTTP API responses

**Read from Base chain:**
- Pool contracts (Uniswap V3 and Aerodrome) via read-only `eth_call` through a standard RPC provider (Alchemy, Infura, QuickNode, or Base public RPC — not constrained to Layer 3's Alchemy budget)
- Block headers for timestamp validation

**Write to:**
- Local filesystem: opportunity logs (JSONL), daily rollups (CSV), analysis checkpoint snapshots

**Does not touch:**
- Any writable Layer 3 table or endpoint
- Any Ethereum address with private key control
- Any flash loan provider contract
- bloxroute, Flashbots, or any MEV submission infrastructure

### What it does NOT do

This is a research experiment in Phase 1. Scope creep must be actively prevented.

The software does NOT:
- Execute any on-chain transaction with value
- Deploy or interact with flash loan contracts
- Submit bundles to bloxroute, Flashbots, or any other MEV infrastructure
- Grant or manage token approvals
- Maintain any wallet with capital
- Run in a configuration where a software bug could cause on-chain execution
- Adapt its rules or thresholds during the run
- Use LLM inference at any point in the decision pipeline
- Produce interpretive analysis (all analysis is conducted post-run by Jason)
- Integrate with Layer 3's writable endpoints or production scheduler

Any code path that would enable on-chain execution in a future version must be gated behind a configuration flag that is explicitly absent from Phase 1 builds.

**Present this spec to Jason for approval before writing any code.**

---

## INVARIANTS (never violate these)

These are non-negotiables. Violation of any invariant is a stop-work condition requiring explicit discussion before proceeding.

### Research discipline invariants

1. **Phase 1 is detection only.** No code path produces on-chain writes. The software holds no private keys. The software has no capital.

2. **Rules are fixed for the duration of the run.** Once the software is deployed and observing, filter thresholds, pool monitoring sets, and opportunity detection logic do not change until the run ends. Corrections discovered mid-run are documented but applied only in a subsequent run.

3. **No LLM inference in the runtime pipeline.** Claude Code writes the code. The code runs deterministically. No API call to any LLM happens during detection, classification, or logging.

4. **No adaptive scaling.** The software does not scale monitoring breadth, detection sensitivity, or logging volume based on early-run observations. What starts the run ends the run.

### Layer 3 separation invariants

5. **Read-only against Layer 3.** The software never writes to any Layer 3 table or calls any Layer 3 admin endpoint. It consumes the documented consumable surface only.

6. **Respects Layer 3's Alchemy budget.** The software does not cause Layer 3 to make additional Alchemy calls. It uses its own RPC provider for Base chain queries, completely separate from Layer 3's Alchemy keys.

7. **Runs on separate infrastructure.** Phase 1 runs on a machine or container distinct from Layer 3's Railway deployment. Layer 3's uptime is not coupled to this experiment's uptime.

### Data integrity invariants

8. **Freshness awareness.** The software checks Layer 3 data freshness via the `fresh_tables` envelope (per Correction #7). If any critical table is older than 30 minutes, the software pauses new opportunity evaluation until freshness is restored. Opportunities observed during pause periods are logged with a `layer3_stale=true` flag but their filter evaluations are marked as degraded.

9. **Epistemic tier awareness.** Filter strength matches tier. Tier A signals (deductive, verifiable) can trigger hard flags. Tier B signals (inferential) can only contribute to soft flags. Tier C signals are log-only and never flag opportunities.

10. **Loud logging.** Every opportunity, every filter query, every decision, every error is logged with timestamps and full context. Silent failures are not acceptable. Unparseable responses are logged with raw payloads.

### Operational invariants

11. **Jason has kill authority.** A kill switch (documented in the Operational Constraints section) stops new opportunity evaluation on demand. It does not need to clean up anything because nothing is in-flight on-chain.

12. **Runs in a single process or well-defined process group.** No distributed state. No race conditions between workers. If scaling is needed later, it's Phase 2 work.

---

## PHASES

Phase 1 is itself divided into sub-phases for implementation sequencing. Each sub-phase has acceptance criteria. Stop at each sub-phase boundary, report status, wait for approval before proceeding.

### Phase 1.0: Infrastructure and intelligence consumption

**Goal:** Stand up the local environment, sync Layer 3's database, prove query access works end-to-end.

**Files to create:**
- `layer3_trading_exp/` repository structure
- `layer3_trading_exp/layer3_client.py` — wraps Layer 3 database queries into a clean Python interface. Every method corresponds to a query pattern documented in `layer3_consumable_intelligence.md`.
- `layer3_trading_exp/sync.py` — handles the 15-minute sync of Layer 3's production database to local
- `layer3_trading_exp/freshness.py` — checks `fresh_tables` data and enforces the 30-minute staleness threshold
- `layer3_trading_exp/config.py` — centralized configuration (chain=base, db paths, RPC URLs, sync intervals)
- `layer3_trading_exp/tests/test_layer3_client.py` — test queries against a known-state local DB copy

**Acceptance criteria:**
- [ ] `layer3_client.get_contract_classification(address)` returns the correct fields per Section 1.1 of the intelligence inventory
- [ ] `layer3_client.is_in_org_wallets(address)` returns boolean correctly for known org_001 through org_004 addresses
- [ ] `layer3_client.get_trap_events_for(address)` returns row count matching a direct SQL query
- [ ] `layer3_client.get_trust_amplification(address)` returns the CRITICAL flag correctly for 0xd4624228
- [ ] Freshness check returns `stale=True` when database is >30 minutes old
- [ ] Sync script runs successfully end-to-end from Railway production to local volume
- [ ] All tests pass (`pytest layer3_trading_exp/tests/`)

**Stop here. Report: sync duration, query latencies measured, any surprises encountered. Wait for approval before Phase 1.1.**

### Phase 1.1: Pool monitoring on Base

**Goal:** Build the pool-watching infrastructure that streams pool state from Base.

**Files to create:**
- `layer3_trading_exp/pool_monitor.py` — connects to Base RPC, subscribes to block updates, fetches pool state (slot0, liquidity, tick) for the monitored pool set
- `layer3_trading_exp/pool_set.py` — defines the initial monitored pool set on Base
- `layer3_trading_exp/tests/test_pool_monitor.py` — tests against known pool state

**Initial monitored pool set (Phase 1.1):**
- Uniswap V3 pools on Base with ≥$1M TVL at start of run (enumerated from Uniswap subgraph at deployment time, then frozen for the run)
- Aerodrome stable and volatile pools on Base with ≥$500K TVL at start of run (enumerated from Aerodrome's factory at deployment time, then frozen)

The pool set is frozen at run start. Pools added to Base after run start are not monitored. Pools drained below TVL threshold during the run remain monitored. This is deliberate — a moving monitored set introduces confounding variables in the research data.

**Acceptance criteria:**
- [ ] Pool set enumerated at run start and saved to `run_metadata/monitored_pools.json` (never modified during run)
- [ ] Monitor connects to Base RPC and receives block updates within 3 seconds of block production
- [ ] Pool state fetched correctly for all monitored pools within one block period
- [ ] Disconnect/reconnect handled with logged gap markers
- [ ] RPC rate limit respected (chosen provider's documented limit, with 80% ceiling)

**Stop here. Report: pool count enumerated, RPC provider chosen, average lag from block production to state ingestion. Wait for approval.**

### Phase 1.2: Opportunity detection logic

**Goal:** Compute arbitrage opportunities from observed pool states.

**Files to create:**
- `layer3_trading_exp/opportunity_detector.py` — computes price discrepancies across pool pairs, calculates theoretical margin
- `layer3_trading_exp/gas_estimator.py` — estimates gas cost for a hypothetical flash-loan-funded swap through the discovered path
- `layer3_trading_exp/tests/test_opportunity_detector.py` — tests with synthetic pool state where discrepancies are known

**Opportunity definition:**
An opportunity is a path `Token A → Pool X → Token B → Pool Y → Token A` where:
- The final Token A balance exceeds the starting Token A balance by a gross margin of ≥0.3% before slippage
- The gas cost for the swap sequence, estimated at current Base gas prices, is less than the absolute expected gain in USD
- Both pools are in the monitored set
- Both tokens have at least $10K notional depth in the relevant pool direction

The 0.3% threshold is a Phase 1 choice that may prove too high or too low. It's logged explicitly in run metadata and fixed for the duration of the run.

**Acceptance criteria:**
- [ ] Detector computes margin correctly for a hand-verified synthetic case
- [ ] Detector rejects opportunities below 0.3% gross margin
- [ ] Detector rejects opportunities where estimated gas cost exceeds expected gain
- [ ] Detector processes all monitored pool state updates within one block period on average
- [ ] Opportunities emitted include: timestamp, path (pools and tokens), computed margin, gas estimate, depth markers

**Stop here. Report: detection rate observed in a 1-hour dry run (opportunities per hour), distribution of margins. Wait for approval.**

### Phase 1.3: Layer 3 filter evaluation

**Goal:** For each detected opportunity, query Layer 3's intelligence surface and produce classification results.

**Files to create:**
- `layer3_trading_exp/filter_pipeline.py` — orchestrates the filter rule evaluation
- `layer3_trading_exp/filter_rules.py` — the explicit filter rules documented below
- `layer3_trading_exp/tests/test_filter_pipeline.py` — tests with synthetic opportunities where expected filter results are known

**Filter rules (evaluated in order, results recorded per rule):**

**Hard filters (would block execution in Phase 2). For Phase 1, tag the opportunity as `hard_flagged=true` and record which rule fired.**

1. **Confirmed-tier contact.** If any contract in the opportunity path (pools or token contracts) is in `contracts` table with `confidence_tier = 'confirmed'` and not `decayed_at` set, fire. (Tier A — 83.2% observable harm rate per April 19 audit.)

2. **Organizational wallet membership.** If any contract is in `org_wallets` (current 13 rows), fire. (Tier A — manually curated.)

3. **Drain-detected approval.** If any token contract in the path has a related `approval_watchlist` row with `drain_detected = 1`, fire. (Tier A — on-chain event.)

4. **Extraction event involvement.** If any contract in the path has been referenced in `extraction_events` as a trap or primary extraction vector, fire. (Tier A — documented event.)

5. **Bytecode-pattern flags.** If any token contract in the path has `has_asymmetric_transfer = 1` OR `has_conditional_revert = 1` in the `contracts` table, fire. (Tier A — deterministic bytecode analysis; directly maps to honeypot risk.)

**Soft flags (would modify size in Phase 2). For Phase 1, tag the opportunity as `soft_flagged=true` with each rule's result recorded.**

6. **Trust amplification CRITICAL.** If any pool in the path has a `trust_amplification` row with `alert_level = 'CRITICAL'`, tag with `soft_reason='trust_amp_critical'`. (Tier B — inferential.)

7. **Cross-deployer bytecode family with concentration asymmetry.** If any token contract in the path belongs to a `bytecode_family` with `is_cross_deployer = true` AND `unique_deployers / member_count < 0.01`, tag with `soft_reason='concentrated_taas_template'`. (Tier B.)

8. **Pending org candidate association.** If any deployer in the path has an address appearing in an `org_candidates` row with `status = 'pending'`, tag with `soft_reason='pending_org_candidate'`. (Tier B.)

9. **Prior trap event history.** If any contract in the path has one or more rows in `trap_events`, tag with `soft_reason='trap_event_history'` and include the count. (Tier A arithmetic, Tier B interpretation for arbitrage context.)

**Weak signals (log-only, no flag produced).**

10. **Suspected tier membership alone.** Record if any contract is `confidence_tier = 'suspected'` but do not flag. (Tier B, and the audit showed 0% PPV at 30-day horizon — not actionable.)

11. **Deployer age under 7 days.** Record if any contract in the path was deployed by a deployer with `first_seen` less than 7 days before the opportunity timestamp. Do not flag.

12. **Bytecode family membership (non-concentrated).** Record family_id and basic properties for each contract. Do not flag.

**Known-legit override.**

13. **Infrastructure registry match.** If any contract in the path is in `infrastructure_registry` with a whitelisted classification (initially only `circle_cctp_*` classifications), record this override. Note: registry absence is NOT a signal — most legitimate infrastructure is not registered. Do not use registry absence as a flag.

**Filter pipeline behavior:**

Each rule is queried for each opportunity. All rule results are recorded in the opportunity log, regardless of which rules fire. No short-circuiting — even if Rule 1 fires, Rules 2 through 13 still execute and are logged.

This is deliberate. Short-circuiting would save query cost but would prevent co-occurrence analysis (e.g. "of opportunities that fail Rule 1, what percentage also fail Rule 2").

**Acceptance criteria:**
- [ ] Each rule is implemented as a pure function with deterministic output
- [ ] Rule results are recorded individually, not collapsed into a single flag
- [ ] Query performance: full rule evaluation for one opportunity completes in ≤500ms p95
- [ ] Test suite includes known cases for each rule (synthetic positive and synthetic negative)
- [ ] If a rule's underlying table is stale per freshness check, rule result is marked `degraded=true` rather than failing silently

**Stop here. Report: filter activation rates over a 1-hour dry run. Wait for approval.**

### Phase 1.4: Logging and measurement

**Goal:** Persist per-opportunity records and produce daily rollups.

**Files to create:**
- `layer3_trading_exp/logger.py` — JSONL writer with rotation per day
- `layer3_trading_exp/daily_rollup.py` — runs at 00:00 UTC, produces the day's summary CSV
- `layer3_trading_exp/schema.py` — explicit schema definitions for opportunity log entries and daily rollups
- `layer3_trading_exp/tests/test_logger.py` — validates logging completeness and format

**Opportunity log schema (one JSONL record per opportunity):**

```
{
  "opportunity_id": "<uuid>",
  "timestamp": "<ISO-8601 UTC>",
  "block_number": <int>,
  "chain": "base",
  "path": {
    "tokens": [<address>, <address>, ...],
    "pools": [<address>, <address>, ...]
  },
  "margin_gross_bps": <int>,
  "gas_estimate_usd": <float>,
  "expected_gain_usd": <float>,
  "depth_notional_usd": <float>,
  "filter_results": {
    "rule_1_confirmed_tier": {"fired": <bool>, "matches": [<addresses>], "tier": "A"},
    "rule_2_org_wallet": {...},
    ...
    "rule_13_infra_registry_match": {...}
  },
  "layer3_freshness": {
    "contracts_last_updated": "<ISO>",
    "approval_watchlist_last_updated": "<ISO>",
    "stale_tables": [<list>],
    "degraded": <bool>
  },
  "hard_flagged": <bool>,
  "soft_flagged": <bool>,
  "flag_summary": [<rule_names_that_fired>]
}
```

**Daily rollup schema (one CSV per day):**

Columns:
- date
- total_opportunities
- hard_flagged_count
- soft_flagged_count
- unflagged_count
- hard_flag_rate
- soft_flag_rate
- per_rule_fire_counts (13 columns, one per rule)
- median_gross_margin_bps_all
- median_gross_margin_bps_unflagged
- median_gross_margin_bps_hard_flagged
- pool_type_distribution (JSON blob in cell)
- degraded_opportunities_count
- freshness_violations_count

**Acceptance criteria:**
- [ ] Every opportunity produces exactly one JSONL record
- [ ] JSONL record contains all fields per schema, no nulls in required fields
- [ ] Daily rollup runs at 00:00 UTC and writes CSV to `data/rollups/YYYY-MM-DD.csv`
- [ ] Daily rollup is idempotent (rerunning produces identical output)
- [ ] Log rotation happens at 00:00 UTC (new JSONL file per day)
- [ ] No log entry is dropped if disk writes are slower than detection rate (use bounded queue with explicit overflow logging rather than silent drop)

**Stop here. Report: log volume measurement (records per hour, bytes per hour). Wait for approval.**

### Phase 1.5: End-of-run analysis framework

**Goal:** Produce the analysis tooling Jason will use at end of run. Not the analysis itself — that's Jason's work.

**Files to create:**
- `layer3_trading_exp/analysis/h1_opportunity_rate.py` — computes observed opportunities per day with confidence intervals
- `layer3_trading_exp/analysis/h2_filter_activation.py` — computes filter activation rates per rule
- `layer3_trading_exp/analysis/h3_distributional_comparison.py` — compares margin/pool/token distributions between flagged and unflagged opportunities
- `layer3_trading_exp/analysis/generate_report.py` — produces a Markdown report template for Jason to annotate

**Acceptance criteria:**
- [ ] Each analysis script runs against the JSONL log and daily rollups
- [ ] Scripts produce plots as PNG (matplotlib or plotly, whichever is already installed; do not add new dependencies for this)
- [ ] Final report template includes all raw numbers, all plots, and explicit placeholder sections for Jason's interpretation (labeled "TO BE COMPLETED BY JASON")
- [ ] Scripts are pure-function where possible and have test coverage for core computations

**Stop here. This is the final sub-phase.**

---

## FAILURE BEHAVIOR

**Loud failure over silent wrong output. Always.**

- If a file specified in Pre-Work does not exist: STOP and report. Do not invent the interface.
- If the Layer 3 database cannot be synced: STOP and report. Do not proceed with a stale-at-start DB.
- If an invariant would be violated by the proposed approach: STOP and propose an alternative before writing code.
- If a phase acceptance criterion fails: STOP and report the exact failure. Do not attempt to silently work around it.
- If scope is unclear: STOP and ask. Do not make assumptions and proceed.
- If an on-chain write is about to be attempted: STOP immediately. This is a catastrophic invariant violation.

### Runtime failure behavior (production running software)

- **Layer 3 database sync fails:** Pause new opportunity evaluation. Continue monitoring pool state. Log every opportunity observed during pause with `layer3_unavailable=true` and no filter results.
- **Base RPC unreachable:** Pause the pool monitor. Reconnect with exponential backoff. Log gap markers for any missed blocks.
- **Disk fills:** Rotate to a new volume if configured; otherwise halt with loud alert. Do not drop log entries silently.
- **Filter query exception for one rule:** Record that rule's result as `{"error": "<message>", "fired": null}`. Continue with other rules.
- **Filter query exception for all rules:** Log the opportunity with `filter_evaluation_failed=true` and pause new evaluation until resolved.

### Kill switch

A file at `data/KILL_SWITCH` (empty, any contents) causes the detection loop to exit cleanly within one block period. No cleanup is needed because nothing is in-flight on-chain. Resume by removing the file and restarting the process.

Jason has kill authority. No one else. Claude Code does not have kill authority during the run — Claude Code is not in the loop during the run.

---

## WHAT NOT TO BUILD

Phase 1 scope is detection and logging. The following are explicitly out of scope and must not be built, even if they seem easy or obvious:

- **No execution infrastructure.** No transaction construction, no signing, no submission, no private key management, no wallet creation, no keystore handling.
- **No flash loan integration.** No Aave, Balancer, dYdX, or any other flash loan provider integration. No flash loan contract deployment. No code that calls `flashLoan()` on anything.
- **No bloxroute integration.** No bloxroute account setup, no bundle construction, no RPC routing through bloxroute endpoints. This is Phase 2 if it happens at all.
- **No Flashbots or private mempool integration.** Same reasoning as bloxroute.
- **No approval infrastructure.** The software never signs, submits, or simulates an `approve()` call.
- **No LLM inference in the runtime pipeline.** Not for filter decisions, not for opportunity ranking, not for anything. Claude Code writes the code, the code runs deterministically. This invariant is explicit.
- **No adaptive rules.** Rules are fixed at run start. Discovered improvements during the run are documented, not deployed.
- **No UI dashboards.** Logs to file. End-of-run analysis generates a Markdown report. No Flask, no Streamlit, no React, no browser-based monitoring.
- **No alerting.** Detection is for logging, not for notifying a human at runtime.
- **No cross-chain monitoring.** Base only. Arbitrum and Optimism are Phase 2+ if they happen.
- **No token discovery or opportunistic pool addition.** Monitored pool set is frozen at run start.
- **No layer on top of Layer 3's database.** The software reads the documented consumable surface. It does not add derived tables, computed columns, or "smart" wrapping logic that hides which Layer 3 signal fired.
- **No trading of any kind.** This includes "small test trades," "validation trades," "sanity checks," or any other rationalization. Zero on-chain writes with value.
- **No storage of private keys in any form.** The Phase 1 software does not generate, hold, or reference private keys. If a code path would require a private key, that code path is Phase 2.
- **No refactoring of Layer 3 itself.** Layer 3 is a separate project. This software consumes its outputs. Changes to Layer 3's schema or endpoints required to support this experiment should be proposed back to the Layer 3 maintainers, not made unilaterally.

---

## OPERATIONAL CONSTRAINTS

### Infrastructure

- Runs on a VM, container, or developer machine distinct from Layer 3's Railway deployment
- Python 3.11 or 3.13, matching Layer 3's version
- Local SQLite copy of Layer 3's production database (2.45 GB at snapshot time, grows during run)
- Dedicated RPC endpoint for Base chain (not sharing Layer 3's Alchemy credentials)
- Disk: budget 20GB for logs (opportunity log at ~2KB per record, 30K records = 60MB base, plus filter result expansions)
- Runtime: intended for 30 days continuous; should recover from transient failures without human intervention

### Access mode

Layer 3 data access is via **local SQLite copy, synced every 15 minutes** from Railway production. The HTTP API is used only for freshness checks (per Correction #7) and manual debugging — not for per-opportunity queries.

Rationale: at thousands of opportunities per hour, network round trips to Railway would dominate the latency budget. Local SQLite is sub-millisecond. The 15-minute sync window is acceptable for research purposes because trap confirmations and org_wallet additions are low-frequency events.

### Kill switch authority

Jason only. Implemented as a file check. No distributed coordination, no API endpoint.

### Data retention

All logs, rollups, and analysis artifacts are retained indefinitely. Disk space permitting. If space becomes a constraint before end of run, raise the issue; do not delete logs.

---

## FINAL DELIVERABLE

At the end of Phase 1, the software produces:

1. **Opportunity log** — JSONL files covering the full run, one file per day, one record per opportunity
2. **Daily rollups** — CSV files, one per day, with aggregate statistics
3. **End-of-run analysis outputs** — plots and computed statistics for H1, H2, H3 (raw data, not interpretation)
4. **Report template** — Markdown document with all raw findings and explicit placeholders for Jason's interpretation
5. **Run metadata snapshot** — monitored pool set, configuration values, Layer 3 DB version used, any schema or rule changes (should be none)

Jason then:
1. Reviews the raw findings
2. Conducts the interpretive analysis
3. Writes the findings document
4. Decides whether Phase 2 is justified
5. Passes findings to Scott and Richard

Claude Code does not write the findings document. Claude Code does not decide whether Phase 2 happens. The research discipline requires Jason to own the conclusions.

---

## QUESTIONS TO RESOLVE BEFORE IMPLEMENTATION

These are questions the implementation session may surface. Flag them to Jason before proceeding rather than guessing:

1. Which specific RPC provider for Base? (Alchemy separate account, QuickNode, Base public RPC, other?)
2. Where does the software run? (Developer machine, cloud VM, container?)
3. What gas price source for the gas estimator? (Base public RPC `eth_gasPrice`, chain-specific gas oracle, fixed assumption?)
4. Uniswap V3 pool enumeration: which TVL cutoff exactly, which subgraph endpoint at run time?
5. Aerodrome pool enumeration: which factory address, which TVL cutoff?
6. What's the exact 15-minute sync mechanism? (`railway ssh` + `sqlite3 .dump` + `sqlite3 .read`, or `/admin/sync-*` endpoints, or something else?)
7. If the local Layer 3 DB becomes corrupted mid-run, what's the recovery procedure?

These don't need answers before Phase 1.0 starts, but they need answers before Phase 1.1 (pool monitor) begins.

---

## Document status

This specification is the contract between the design and the implementation. Any deviation during implementation — including "small improvements" — requires explicit written approval from Jason appended to this document as an addendum. Undocumented deviations violate the research discipline and corrupt the experimental record.

Version: Draft v1.0
Date: 2026-04-21

---

## Addendum A — Pre-work resolutions (2026-04-21)

Five ambiguities surfaced during MANDATORY PRE-WORK. Jason's written resolutions:

1. **Sync mechanism.** Spec's `/admin/sync-*` endpoints do not exist. Phase 1 uses the existing `sync_railway_db.py` script, which pulls tables via the authenticated `/dump?table=…&token=…` endpoint on `run_surveillance.py`'s HTTP handler (not the FastAPI `web.app` server).

2. **Freshness envelope name.** Code uses `meta.computed_at` (dict of table → latest timestamp). Phase 1 consumers read this key; spec's `fresh_tables` reference is superseded.

3. **Org wallets acceptance criterion.** The Phase 1.0 acceptance bullet "returns boolean correctly for known org_001 through org_004 addresses" is dropped. `org_wallets` contains rows only for org_001 and org_002 per Correction #11; test coverage is restricted to those two orgs.

4. **Layer 3 API reachability.** Railway instance is confirmed up by Jason. My pre-work fetches to `/docs` and `/openapi.json` returned 404 because FastAPI is instantiated with `docs_url=None, redoc_url=None`. No change to the spec is implied; Phase 1 does not depend on the public OpenAPI page.

5. **Rule 5 (bytecode-pattern flags) tier.** Kept as a hard filter despite the §2.2 caveat about Tier B predictive interpretation and 12 known `weak_detector_only` false positives. Phase 1 logs per-rule fire outcomes, so co-occurrence with other rules will reveal whether Rule 5 over-fires in the observed distribution.

6. **Sync architecture revision (supersedes Addendum A #1).** Jason owns local-DB refresh cadence. The experiment's software does not initiate any Railway HTTP call, does not require `ADMIN_TOKEN`, and does not invoke `sync_railway_db.py`. Jason refreshes the local SQLite copy using whatever method he prefers (`railway ssh` + `sqlite3 .dump`, the `/dump` endpoint, or ad-hoc). The 30-minute freshness gate (invariant #8) continues to apply at evaluation time — stale data pauses new opportunity evaluation and flags in-flight opportunities as `layer3_stale=true`. The 15-minute cadence in §Operational Constraints → Access mode is now advisory rather than committed. The Phase 1.0 finding that seven filter-rule-critical tables (`org_wallets`, `org_candidates`, `infrastructure_registry`, `bytecode_families`, `bytecode_family_members`, `trust_amplification`, `extraction_events`) are not reachable through the current `sync_railway_db.py` remains Jason's manual-pull concern; no L3-side change is proposed by this experiment.
