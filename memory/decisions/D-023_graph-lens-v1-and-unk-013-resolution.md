# D-023: GraphLens v1 — re-grounded on L3 interaction tables (resolves UNK-013)

**Date**: 2026-05-27
**Made by**: user (picked Path 1 from UNK-013's resolution menu: "yes" → implement; "yes and re-run the 30 min smoke" → verify)
**Status**: ACTIVE

## Context

Sub-phase 3.1 required a first concrete lens to prove the lens abstraction yields net-new signal. Per the spec, GraphLens was the chosen first lens, with three signal types: `cluster_detected`, `centrality_spike`, `subgraph_anomaly`.

The initial implementation backed all three signals on **L3 classification tables** (`contracts`, `org_wallets`, `deployers`). Unit tests passed against synthetic fixtures. But the 30-min smoke against the live L3 SQLite **emitted zero signals** — see UNK-013.

Diagnostic showed the L3 corpus and the Phase 2 monitored set are **disjoint by construction** on classification keys:
- 0/129 monitored pool addresses in `contracts`
- 0/129 monitored pool-deployer addresses in `contracts`
- 0/88 token addresses in `contracts`
- 0/88 token addresses in `org_wallets`

L3 indexes "suspicious infrastructure to warn about"; Phase 2 monitors "top-TVL legitimate liquidity." These were architected as complements, not overlapping sets.

UNK-013 surfaced **four candidate resolution paths**. User picked **Path 1**: re-ground GraphLens on L3's **interaction** tables (which DO overlap with our monitored set).

## What changed

### Schema-level (no breaking changes to Signal contract)

`Layer3CorpusSource` Protocol gained three new methods:
- `get_org_interactions(address)` — aggregates `org_transfer_events` (244K hits on our monitored set across 50 distinct addresses)
- `get_poisoning_membership(address)` — aggregates `poisoning_events` (1 of our addresses appears as a poisoner)
- `prefetch_for_scan(addresses)` — performance hook (see below)

Legacy probes (`get_contract_classification`, `get_org_wallet_membership`, `get_deployer_deploy_count`) are kept for completeness but rarely fire against top-TVL pools.

### Three signal types — re-grounded

| Signal | Old grounding | New grounding |
|---|---|---|
| `cluster_detected` | ≥3 pools share a deployer (per `contracts.deployer_address`) | ≥3 monitored addresses share an `org_id` (per `org_transfer_events.org_id`) |
| `centrality_spike` | deployer has >50 contracts deployed | address receives >100 transfers from L3-orgs (in-degree centrality on the funding graph). Strength = log10(n) / 6.0, capped at 1.0 |
| `subgraph_anomaly` | address in `org_wallets` | (a) address in `poisoning_events` (poisoner or target), OR (b) address received transfers from a high-risk `from_role` ∈ {`laundry`, `unknown`}, OR (c) legacy org_wallets path (rarely fires) |

### Performance hook: `prefetch_for_scan`

First real-data run was painfully slow (~10 min per scan) because `org_transfer_events.to_address` has **no index**, and the lens was issuing 217 separate full-table scans per scan against a 4M-row table.

Fix: added `prefetch_for_scan(addresses)` to the Protocol. The concrete adapter does **one bulk `IN(...)` query per table per scan**, builds per-address aggregate dicts in memory, and serves subsequent per-address calls from cache. Per-scan latency dropped from ~10 min to ~3 sec.

Per-address methods are still in the Protocol and still work (falling back to single-address SQL) for ad-hoc consumers outside the lens hot path.

## Acceptance evidence

| Gate | Target | Actual |
|---|---|---|
| ≥100 Signals in 30-min smoke | ≥100 | **2,940** (29× over) |
| All Signals validate cleanly | 0 failures | **0** |
| ≥3 distinct signal types | ≥3 | **3** (cluster_detected, centrality_spike, subgraph_anomaly) |
| 60 scans / 30 min wall clock | 60 | **60 in 1801.6 s** |
| Reliable delivery, no drops | 0 drops | **0** |
| Phase 1+2 test suite still passes | 310/310 | **310/310** |
| Engine test suite | all pass | **54/54** |

Per-scan breakdown over 60 scans:
- `cluster_detected`: 1/scan (60 total) — caught the `org_001` cluster of 44 monitored addresses on Base
- `centrality_spike`: 39/scan (2,340 total) — 39 monitored addresses receive >100 L3-org transfers
- `subgraph_anomaly`: 9/scan (540 total) — mostly `from_role='laundry'` and `from_role='unknown'` matches

Smoke run cost: **$0, 0 Alchemy CUs, 0 network bytes** — engine is local-only until Phase 4.

## What this proves

1. **The lens abstraction is sound.** GraphLens emits signal that the existing Phase 2 filter pipeline does NOT compute (no other code looks at `org_transfer_events` interaction-graph centrality).
2. **The Signal schema holds up under real-data volume.** 2,940 signals through the bus, zero validation failures.
3. **The "lens reads via adapters" pattern is testable AND fast.** Unit tests use synthetic fixtures; the adapter does the SQL-bulk-fetch optimization without leaking SQL details up to the lens.

## What this does NOT yet prove

- **Signal QUALITY** — whether these specific 2,940 signals would yield trading-relevant alpha. That's a synthesis / orchestrator question (sub-phase 3.3+).
- **Multi-lens dynamics** — only one lens lit up. Sub-phase 3.2 adds the next lens; sub-phase 3.3 introduces conflict + weighting + regime routing.
- **Feedback loop** — outcome attribution and weight updates land in sub-phase 3.5.

## Reversal triggers

- A future analysis attests that `cluster_detected` on org_id is a false-positive signal (e.g. org_001 is just gas-station infrastructure for the entire DeFi ecosystem) → re-tighten the clustering rule (e.g. require ≥N distinct from_roles, not just shared org_id) in a D-NNN.
- The `from_role='laundry'` signal turns out to be over-broad → narrow `HIGH_RISK_FROM_ROLES` in a D-NNN with attestation evidence.
- A future lens needs an L3 interaction table not currently exposed by the adapter → extend `Layer3CorpusSource` Protocol with the additional method in a D-NNN.

## Links

- D-020 (Phase 3 spec approval) — parent decision
- D-021 (signal schema locked) — sibling, locked the contract this lens emits
- D-022 (event bus implementation) — sibling, the transport this lens publishes to
- UNK-013 (zero L3↔monitored-set overlap) — this decision resolves it (Path 1 chosen)
- I-15 (lens independence) — enforced by `test_lens_does_not_import_other_lenses`
- I-16 (Signal validation at every publish) — verified by smoke run's `validation_failures = 0`
- Code: `engine/lenses/graph/lens.py`, `engine/adapters/l3_corpus_phase2.py`, tests: `engine/tests/test_graph_lens.py`
- Smoke artifacts: `engine/data/sub_phase_3_1_signals.jsonl` (2,940 lines), `engine/data/smoke_run_3_1.log`
