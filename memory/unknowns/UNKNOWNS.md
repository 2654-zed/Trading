# UNKNOWNS.md

Each unknown is research-blocking, operationally significant, or
both. Add new entries via the format below. **Closing an UNKNOWN requires
a Linked Decision** — passive resolution ("we just learned the answer")
is not allowed. Every resolution must be an explicit decision.

## Rules (enforced by LOOP.md Step 4 and Step 7)

1. **UNKNOWN cannot be marked RESOLVED without a Linked Decision**. The
   Linked Decision field must reference a `decisions/D-NNN_*.md` file
   created at the time of resolution. If you "know the answer" but
   haven't logged a decision yet → it stays OPEN.
2. **Every UNKNOWN must have a Deadline or Trigger**. Open-ended unknowns
   that no one revisits become invisible. Each entry below names either
   a calendar deadline or a system-state trigger that forces revisit.
3. **The agent must revisit UNKNOWNs when deadlines/triggers fire**.
   LOOP.md Step 4 is responsible for this sweep.

## Format

```
## UNK-NNN: <short name>
* **Description**: what we don't know, in one sentence
* **Why it matters**: which decision / metric / behavior depends on this
* **Impact**: concrete consequence of leaving this open
* **Status**: OPEN / INVESTIGATING / RESOLVED — date
* **Owner**: who's tracking
* **Resolution Path**: the concrete probe / experiment / check that would close this
* **Linked Decision**: D-NNN reference (REQUIRED before marking RESOLVED; "none yet" while OPEN)
* **Deadline / Trigger**: calendar date OR system-state condition that forces revisit
* **Resolution** (when closed): how the linked decision answered the unknown
```

---

## UNK-001: Base block number anomaly during redeploy

* **Description**: When the container restarted after the index fix, the WS subscription delivered block `46,001,672`. The pre-restart container had been processing block `45,930,992` with 30 min lag. That's a 70,680-block gap, equivalent to ~39 hours of Base block production, but only ~30 min of wall time elapsed between containers.
* **Why it matters**: Suggests either (a) Alchemy WS skipped buffered blocks and jumped to live head, or (b) my mental model of Base block rate is wrong, or (c) something else is going on.
* **Impact**: We may be silently missing block ranges. Any "blocks observed" count in analysis output is suspect across container restarts.
* **Status**: OPEN — 2026-05-13
* **Owner**: next session
* **Resolution Path**: At next container restart, run `curl https://api.basescan.org/api?module=block&action=...` (or query Alchemy `eth_blockNumber`) within 60s of restart and compare to first logged head. If they match → Alchemy jumps to live head, gap is by design. If logged head < live head → we're missing the in-between window.
* **Linked Decision**: none yet — required before RESOLVED
* **Deadline / Trigger**: Next container restart (any cause)

---

## UNK-002: MSUSD/USDC arb cadence — what triggers reopens?  [REVERTED-TO-OPEN]

* **Description**: D-008 (2026-05-16) closed this UNK with "the arb did NOT reopen in ~2.5 days of post-closure observation." EXP-002 produced TWO subsequent reopenings of the same pool pair (2026-05-17 ~6.3h open; 2026-05-23 7.9h open) — see D-018 for the full evidence + open-window distribution. D-008's resolution criterion #1 explicitly fired. UNK is REVERTED to OPEN with a sharpened question: **what is the open-window cadence and what triggers reopens?**
* **Why it matters**: The arb is bursty: open for 6-8h at a time, closed for 4-5+ days between bursts. This is RESEARCH-RELEVANT — Phase 2 runs that happen to land entirely within closed phases will see zero MSUSD/USDC emissions and undercount intra-chain opportunity rate. Runs that catch open phases will see ~12K emissions of 1 unique key. We can't compute a steady-state rate without characterizing the open/close cycle.
* **Impact**: H1 measurement remains INVALIDATED (D-007) regardless — even continuously-open MSUSD/USDC is 1 unique key, far from 1000/day. But the bursty pattern matters for explaining why some run windows show high intra-chain emission counts and others don't.
* **Status**: REVERTED-TO-OPEN — 2026-05-25 (was RESOLVED 2026-05-16 via D-008; reversal triggered)
* **Owner**: next multi-week run (post-D-017 redeploy)
* **Resolution Path**: Run the detector continuously for 30+ days post-D-017 fix. Observe each open/close transition. Characterize the inter-burst gap distribution + open-window duration distribution. Compute conditional reopening probability per day. Resolves with a new D-NNN "MSUSD/USDC cadence characterized — bursty pattern at λ=... per week".
* **Linked Decision**: **D-018** (`decisions/D-018_uk-002-reversal.md`) — REVERSES D-008's resolution. D-018 itself is the most recent linked decision; further resolution will produce D-NNN.
* **Deadline / Trigger**: Trigger — next Phase 2 long-run completes (≥30 days post-D-017). Until then, UNK is informational, not blocking.
* **Observed open windows** (cumulative across EXP-001 + EXP-002):
  - EXP-001: ~1.85h open (2026-05-13 04:28-06:19 UTC), then closed
  - EXP-002 Run 1: ~6.3h open (2026-05-17 16:40-22:59 UTC), then closed (probably; WS stalled during the close, so close-time is imprecise)
  - EXP-002 Run 4: **7.9h** open (2026-05-23 07:05-14:56 UTC), then closed (close time precise — JSONL writes stopped cleanly)
- **Inter-burst gaps observed**: 4.4 days (EXP-001 → Run 1), 5.4 days (Run 1 → Run 4). Initial cadence estimate: open ~6-8h every ~5 days.

---

## UNK-003: Alchemy CU usage for multi-chain expansion

* **Description**: Phase 2 cross-chain would run pool monitoring on Base + Arbitrum + Optimism. Projected CU usage: ~108M / month (3× current Base-only ~36M). Untested against actual usage.
* **Why it matters**: Free tier is 300M CU/month. If we exceed it, billing kicks in. Need to verify before committing to Phase 2 architecture.
* **Impact**: Unexpected billing OR rate limiting mid-run.
* **Status**: RESOLVED — 2026-05-17
* **Owner**: EXP-002 live measurement (agent + Phase 2.8 deploy)
* **Resolution Path**: (a) check Alchemy dashboard for current monthly CU usage, (b) model per-chain CU per minute from observed Base-only call rates × 3, (c) compare projected total to 300M/month and to free-tier rate caps (25 RPS).
* **Linked Decision**: **D-015** (`decisions/D-015_alchemy-cu-budget-phase-2.md`)
* **Deadline / Trigger**: Before Phase 2 spec finalization (i.e. blocking the eventual D-NNN_phase-2-cross-chain decision)
* **Resolution**: **EXP-002 live measurement at T+30min: aggregate multicall rate ~1.5 req/s across 3 chains (matches engineering projection).** With `multicall3.aggregate3` billed as a single eth_call at 26 CU per call, projected monthly CU is **~101M / 300M = ~34% utilization** — comfortably inside the 80% spec ceiling. The 159-pool batching does NOT scale CU linearly with pool count because multicall3 batches into one eth_call. WS subscription cost is amortized via connection-level throughput (NOT per-message CU) on Alchemy's standard plans — this is flagged as the load-bearing assumption in D-015's reversal criteria. Reversal at T+24h dashboard check if observed CU > 250M projected monthly OR rate-cap (25 RPS) hit.

---

## UNK-004: L3 sync delta size — why so consistent?

* **Description**: Every 5-min sync cycle pulls ~78-80K row deltas. Suspiciously consistent across cycles. Either (a) L3 has a roughly constant deployment-event rate of ~16K rows/min across 3 chains, or (b) something in our sync cursor logic is pulling extra data each cycle.
* **Why it matters**: Sync cost (bandwidth, sync duration, lock contention) scales with delta size. If we can reduce it, lag stability improves.
* **Impact**: Sync cycles take 3-20s each. During those windows, DB lock contention can affect filter eval p95.
* **Status**: OPEN — 2026-05-13
* **Owner**: next session can probe
* **Resolution Path**: Add per-table row-count logging to `_sync_table` (one-line patch). Run for 3 cycles. Compare to `last_cursor` advancement: if cursor advances by expected delta but rows-seen exceeds it, we're double-counting. Possible D-NNN: "split sync into per-table cursors with explicit `last_cursor < ts` filtering instead of `last_cursor <= ts`".
* **Linked Decision**: none yet — required before RESOLVED
* **Deadline / Trigger**: TRIGGER — sync cycle duration exceeds 60s for any single cycle (currently ~20s, threshold = 3x current). Until then, this is research-curiosity, not operationally blocking.

---

## UNK-005: L3 freshness threshold vs actual cadence

* **Description**: `freshness.py` uses a 30-min threshold per spec invariant #8, but the 5 filter-critical L3 tables actually update on multi-hour cadences (one daily). Every filter evaluation in this run is `degraded=True`.
* **Why it matters**: H2 statistics exclude degraded evaluations. With 100% degraded, H2 is structurally vacuous.
* **Impact**: H2 verdict for this run will be "no signal," undermining one of the three primary hypotheses.
* **Status**: RESOLVED — 2026-05-16
* **Owner**: Phase 2 sub-phase 2.6 implementer (agent)
* **Resolution Path**: Implement per-table thresholds in `freshness.py` (recommended: `contracts`/`deployers` 1h, `bytecode_families` 6h, `trap_events` 24h, `trust_amplification` 36h). Re-run analysis against existing JSONL to see if `degraded_pct` drops. Decision required: are these thresholds correct given L3's actual cadence?
* **Linked Decision**: **D-012** (`decisions/D-012_per-table-freshness-thresholds.md`)
* **Deadline / Trigger**: Before EXP-001 analysis is written up (otherwise H2 verdict is forced to "vacuous"). Hard deadline = ~2026-05-14 evening.
* **Resolution**: Per-table thresholds implemented exactly as recommended (`contracts`/`deployers` 1h, `bytecode_families` 6h, `trap_events` 24h, `trust_amplification` 36h, default 6h). `FilterEvaluation` now carries `degraded_per_table: dict[str, bool]`; JSONL v2 schema includes the same field. **Phase 1 JSONL replay confirms calibration: 100% of EXP-001's 2,420 records were "fully degraded" under Phase 1's single 30-min threshold; under Phase 2 per-table thresholds, 0% are fully degraded.** Only `trust_amplification` remains 100% stale on Phase 1 data (it was ~4 days old at evaluation time — that's a legitimate L3 cadence issue, not a threshold artifact). Spec acceptance was "<10% fully degraded"; achieved 0%.

---

## UNK-006: Long-term lag stability post-index-fix

* **Description**: Immediately after the index fix, lag dropped from 1,836s to <1s. Whether this holds over 24 hours of continuous operation is unverified.
* **Why it matters**: If lag re-creeps during sustained operation (e.g., DB grows, indexes become less selective, sync cycles slow down), we're back to the original problem.
* **Impact**: 1-day run could degrade silently if no one watches lag.
* **Status**: RESOLVED — 2026-05-16
* **Owner**: 1-day analysis (EXP-001 final review)
* **Resolution Path**: Sample `block X,XXX  lag=Y` lines from `railway logs --deployment` at T+6h, T+12h, T+24h. If lag stays <30s across all three samples → resolved (lag is stable). If lag climbs past 30s at any sample → INVARIANT I-11 fires, intervene.
* **Linked Decision**: **D-008** (`decisions/D-008_resolve-unk-002-and-006.md`)
* **Deadline / Trigger**: TRIGGER — lag > 30s observed at any sample point (this fires I-11). HARD DEADLINE — end of EXP-001 (T+24h from index-fix restart).
* **Resolution**: **Lag stayed sub-1s across the full ~3-day window post-index-fix (~104K blocks).** The container kept up with Base block production with no observed creep; sync cycles every 5 min ran without errors; no GAP markers. Per D-008: the index fix (D-005) is durable at current scale. Indexes remain selective even as the synced DB accumulates rows over time. Lag intervention triggers (I-11 T-A) did not fire at any sample point.

---

## UNK-007: Bridge model for Phase 2 cross-chain

* **Description**: "Cross-chain arbitrage" is undefined without specifying the bridge that connects chains. Candidates: Across (~30s, ~0.05-0.2% fee), LayerZero / Stargate (~1-5 min, ~0.1-0.3% fee), canonical bridges (7-day finality — unusable).
* **Why it matters**: The "opportunity" definition fundamentally depends on bridge friction. With canonical bridges, no opportunities exist. With instant-bridge assumption, every spread is an opportunity.
* **Impact**: Phase 2 spec cannot be written.
* **Status**: RESOLVED — 2026-05-13
* **Owner**: user decision (research framing choice, not implementation)
* **Resolution Path**: Pick a primary bridge + document fee/latency parameters + sensitivity analysis (what changes if we model Across vs Stargate?). Recommend Across (~30s, ~0.05-0.2%). User must affirm or pick differently.
* **Linked Decision**: **D-006** (`decisions/D-006_bridge-model-across.md`) — Across Protocol selected with 30s latency / 10 bps fee point estimate / 5-20 bps sensitivity range / Base+Arb+OP supported chains.
* **Deadline / Trigger**: Before Phase 2 spec implementation begins (i.e. blocks D-NNN_phase-2-cross-chain). Not date-bound — driven by Phase 2 start.
* **Resolution**: User picked Across on 2026-05-13 when asked about cross-chain status. Decision logged in D-006 with parameter specifics. Phase 2 spec can now be drafted (after EXP-001 completes) without further user input on bridge model — though Phase 2 spec must still pick round-trip vs one-way trade structure (deferred sub-decision).

---

## UNK-008: Per-record JSONL size optimization tradeoff

* **Description**: Each JSONL record is ~2.5 KB; ~30% of that is the `per_address` debug block inside filter_results. Dropping it would shrink files by ~1.5x at the cost of losing per-rule diagnostic detail.
* **Why it matters**: 7-day run was ~5 GB; 1-day run will be ~700 MB. Storage isn't tight at current scale, but if Phase 2 multi-chain multiplies record volume by 3-5x, optimization matters.
* **Impact**: We make a storage decision in Phase 2 spec without baseline measurement.
* **Status**: RESOLVED — 2026-05-16
* **Owner**: Phase 2 sub-phase 2.5 implementer (agent)
* **Resolution Path**: Compute JSONL size with vs without `per_address` block against existing log file. Estimate Phase 2 storage. Decide on schema tradeoff in a D-NNN.
* **Linked Decision**: **D-011** (`decisions/D-011_jsonl-schema-v2.md`)
* **Deadline / Trigger**: TRIGGER — Phase 2 spec drafting begins (i.e. blocks D-NNN_phase-2-cross-chain). Not standalone-urgent; deferrable.
* **Resolution**: **Keep the `per_address` debug block.** v2 record size grew ~30% (2.5 KB → 3.3 KB) from the v1 → v2 additive migration, but even the worst-case projection (~700 MB / 7-day run at 30K records/day, multi-chain) fits inside the 50 GB Railway volume with headroom. The `per_address` block is operationally valuable for H3 distributional analysis. Reversal trigger documented in D-011: drop the block via a runtime flag only if a Phase 2 run's T+2h sample projects beyond 200 MB/day record volume.

---

## UNK-009: Detection correctness without live opportunities to verify

* **Description**: After the persistent arb closed, the detector has been emitting zero opportunities. We cannot distinguish "no opportunities exist" from "detector is broken in some new way" without ground truth.
* **Why it matters**: A silent detector failure during the 1-day window would invalidate H1 results without any signal.
* **Impact**: Loss of confidence in detection output.
* **Status**: OPEN — partially resolvable via instrumentation
* **Owner**: next session
* **Resolution Path**: At end of 1-day run, spot-check 3-5 monitored pool pairs by computing margin from raw pool state independently vs the detector's logged output for the same block. If they agree on the same blocks the detector reported zero opps → detector OK. Optionally: add a synthetic test pair with mocked divergent prices to a tests/ verification harness.
* **Linked Decision**: none yet — required before RESOLVED. Linked decision will be a D-NNN_detector-correctness-attested either affirming the detector is correct (with the spot-check methodology recorded) or describing a fix.
* **Deadline / Trigger**: HARD DEADLINE — before EXP-001 analysis is published. If we can't attest detection correctness, H1 numbers can't be claimed.

---

## UNK-010: L3 `bytecode_families` + `trust_amplification` 100% stale across EXP-002 window

* **Description**: Across all 26,122 EXP-002 records (per D-012 per-table thresholds), `bytecode_families` (6h threshold) and `trust_amplification` (36h threshold) report stale 100% of the time. The other 3 filter-critical tables (`contracts`, `deployers`, `trap_events`) report stale <0.1% of the time — they're fresh.
* **Why it matters**: Filter rules 4 (`asymmetric_transfer`) + 8 (`org_001_proximity`) depend on `bytecode_families` and `trust_amplification` respectively. With these tables always stale, those rules always report degraded, contributing nothing to H2's flag rate. H2 SUPPORTED-WITH-CAVEAT (D-019) is driven entirely by rule_2 + rule_3 on the 3 fresh tables.
* **Impact**: 2 of 13 filter rules effectively offline. H2's coverage is partial — we don't know what rule 4/8 would flag if their inputs were fresh.
* **Status**: OPEN — surfaced 2026-05-25 via EXP-002 LOOP analysis
* **Owner**: agent surfaces; user decides whether to investigate L3-side
* **Resolution Path**: Per I-3 (read-only on L3) we can't fix L3's update cadence ourselves. Options: (a) query L3 directly to confirm whether the tables are genuinely behind their natural cadence or whether L3 has stopped writing to them; (b) widen D-012's thresholds for these two tables (e.g. 72h instead of 36h for trust_amp) — but only if L3 confirms a slower-than-expected cadence is the new normal; (c) accept partial filter coverage and document it as a known limitation.
* **Linked Decision**: none yet — required before RESOLVED. Will be a D-NNN_l3-table-cadence-investigation referencing whichever option (a/b/c) is taken.
* **Deadline / Trigger**: Before any H2/H3 numbers are published externally. Sub-week deadline.

---

## UNK-011: rule_2 flagged the canonical Arbitrum WETH/USDC Slipstream pool — true positive or false positive?

* **Description**: 1,047 of 1,238 cross-chain emissions (84.5%) fired rule_2 `org_wallet_membership` (Tier A) on pool `0xc6962004f452be9203591991d15f6b388e09e8d0` — the canonical WETH/USDC Aerodrome Slipstream pool on Arbitrum at fee tier ~5 bps. Is this:
  - **True positive**: L3 has legitimately detected something risky about this pool's deployer / operator (e.g. an exchange or MEV operator with confirmed bad behavior in L3's corpus)?
  - **False positive**: L3's `org_wallets` table over-broadly captures major-protocol infrastructure (this pool's deployer might be Aerodrome's official deployer, which got swept up in some L3 classification rule)?
* **Why it matters**: H2 SUPPORTED-WITH-CAVEAT (D-019) hinges on this being a meaningful signal. If true positive → cross-chain arb research has a real L3-yield finding (these specific pools should be avoided in execution-mode trading). If false positive → the 84.5% flag rate is an artifact of L3's classification noise, and H2's spirit-test fails.
* **Impact**: Phase 3 (execution-mode) go/no-go decision rests in part on this. Also affects H3's interpretation — comparing flagged vs unflagged distributions only means something if "flagged" is a meaningful category.
* **Status**: OPEN — surfaced 2026-05-25 via EXP-002 LOOP analysis
* **Owner**: agent surfaces; user decides whether to query L3 directly
* **Resolution Path**: Manual query against L3's `org_wallets` table for the address `0xc6962004f452be9203591991d15f6b388e09e8d0` to surface: (a) which org_NNN it's classified under, (b) what evidence drove the classification, (c) whether the classification appears intentional (specific bad behavior documented) or incidental (deployer-of-many-things swept up). Per I-3 this is read-only L3 query.
* **Linked Decision**: none yet — required before RESOLVED. Will be a D-NNN_l3-flagged-pool-attestation either affirming the L3 classification or documenting a false-positive workaround.
* **Deadline / Trigger**: Before H3 distributional comparison is published. Same deadline as UNK-010.

---

## UNK-012: Cross-chain emissions clustered entirely in one ~1-hour window — is this rare-burst pattern, time-of-day, or coincidence?

* **Description**: All 1,238 EXP-002 cross-chain emissions (12 unique keys) fired in a single ~1-hour UTC window on 2026-05-17 23:XX. The remaining ~13 hours of effective EXP-002 detection produced zero cross-chain emissions. Possible causes:
  - **Rare-burst pattern**: cross-chain arbs exist but are very infrequent (similar to MSUSD/USDC's bursty cadence — UNK-002)
  - **Time-of-day effect**: certain market conditions (lower CEX activity, U.S. evening, Asian morning) favor cross-chain price drift
  - **Coincidence cluster**: a one-time market event we happened to capture; no repeatable pattern
* **Why it matters**: H1' (cross-chain ≥50/day) verdict depends critically on this. If rare-burst at λ≈1/week, H1' falsifies on a multi-month window. If time-of-day-clustered, we could schedule observation to catch the daily window. If coincidence, H1' has no testable structure.
* **Impact**: Determines whether Phase 2 closes out or extends into a Phase 2.X with longer wall-clock.
* **Status**: OPEN — surfaced 2026-05-25 via EXP-002 LOOP analysis
* **Owner**: next long-run (post-D-017 redeploy)
* **Resolution Path**: Run continuously for ≥30 days post-D-017. Bucket cross-chain emissions by UTC hour-of-day. If concentrated, time-of-day hypothesis. If spread uniformly with ~1 burst/week, rare-burst hypothesis at observable rate. If zero further bursts in 30 days, coincidence/H1' falsification.
* **Linked Decision**: none yet — required before RESOLVED. Will be a D-NNN tied to the next LOOP execution.
* **Deadline / Trigger**: Trigger — next long-run completes. Deadline = end of next Phase 2 measurement window.

---

## UNK-013: Phase 2 monitored set ↔ L3 corpus have zero address overlap by construction

* **Description**: Sub-phase 3.1's 30-min smoke (60 scans of GraphLens against the real `monitored_pools.json` + L3 SQLite) emitted **0 signals**. Diagnosis: the 129 Phase 2 monitored pools (top-TVL AMM contracts on Base/Arb/Optimism) have **zero** L3 corpus overlap on *any* tested join key:
  * `contracts.contract_address` — 0/129 pool-address hits
  * `contracts.deployer_address` — 0/129 pool-deployer hits (pool factories don't appear)
  * `contracts.contract_address` queried with the 88 unique `token0`/`token1` addresses — 0/88 hits (canonical tokens like WETH/USDC aren't tracked in L3's surveillance corpus)
  * `org_wallets.address` queried with token addresses — 0/88 hits
* **Why it matters**: GraphLens v1's three signal types (`cluster_detected`, `centrality_spike`, `subgraph_anomaly`) all assume L3 has classifications for addresses in our monitored set. The unit tests passed against synthetic fixtures that I designed to fit the lens — but real data shows the universes are **disjoint by construction**:
  * L3 indexes *suspicious infrastructure* (drainers, vanity-bait, asymmetric-transfer tokens, trap-emitting contracts) — the things L3 wants to *warn about*
  * Phase 2 monitors *top-TVL legitimate liquidity* — the things L3 explicitly excludes from surveillance
  * Even more structurally: `cluster_detected` ("≥3 pools share a deployer") **cannot fire** for AMM pools at all, because each Uniswap V3 pool is deployed by its factory, not by a shared EOA. The synthetic fixture was structurally unrealistic.
* **Impact**: The sub-phase 3.1 spec's acceptance criterion *"30-min smoke run produces ≥100 Signals, all validating cleanly"* cannot be met with the current data plumbing. We have to either (a) change what we monitor, (b) change what we ask L3, (c) change the lens entirely, or (d) accept the smoke as an "infrastructure passes; real-signal validation deferred" partial pass.
* **Status**: OPEN — surfaced 2026-05-27 via sub-phase 3.1 smoke run
* **Owner**: agent surfaces; user decides direction
* **Resolution Path** (4 candidate paths; user picks one):
  1. **Query L3 about counterparties, not pools.** Use L3 tables that record *interactions* (`transaction_events`, `approval_events`, `liquidity_events`, `cluster_events`) to find flagged contracts that have *transacted on* our monitored pools. Different graph-math notion ("who's swapping here?") that may actually match L3's data shape.
  2. **Broaden the monitored set.** Extend Phase 2 enumeration past top-TVL into the long tail where L3-flagged contracts live. Costly + changes Phase 2 scope.
  3. **Pivot GraphLens away from L3 enrichment.** Have it compute graph metrics on the *interaction graph* derived from on-chain swap/transfer logs directly (no L3 join). L3 becomes one input among many, not the primary edge labeler.
  4. **Accept partial pass.** Document the data overlap finding; ship sub-phase 3.1 as "infrastructure works; real-signal validation deferred to sub-phase 3.2 when the next lens lands." Lowest cost, but defers the real test.
* **Linked Decision**: **D-023** (`decisions/D-023_graph-lens-v1-and-unk-013-resolution.md`) — Path 1 chosen and implemented.
* **Deadline / Trigger**: Blocks sub-phase 3.1 close-out. Must be resolved before D-021/D-022/D-023 derivative decisions are filed.
* **Status**: RESOLVED — 2026-05-27
* **Resolution**: **Path 1 chosen** — re-grounded GraphLens on L3 interaction tables. Diagnostic showed `org_transfer_events.to_address` has 244K hits across 50 of our 217 monitored addresses, and `poisoning_events` has 1 distinct hit. Three signal types re-defined: `cluster_detected` on shared `org_id`, `centrality_spike` on transfer-volume in-degree, `subgraph_anomaly` on poisoning_events + high-risk `from_role` ∈ {laundry, unknown} + legacy org_wallets. Added `prefetch_for_scan` bulk-fetch hook to the Layer3CorpusSource Protocol so the un-indexed `to_address` column doesn't cause 10-min-per-scan latency. **30-min smoke produced 2,940 signals, all validating cleanly, 0 drops, 3 distinct types** — sub-phase 3.1 acceptance bar cleared by 29×.

---

## UNK-014: Engine lenses scan static L3 state — no temporal dynamics until live data or temporal replay

* **Description**: Sub-phase 3.2's lenses (graph, stochastic, information) each scan the *current full state* of the local L3 SQLite copy on every scan, rather than advancing through time. Because the L3 copy is static during a smoke run (sync is paused; engine is $0-CU offline per D-024), every scan emits an identical signal set. The 1-hour 3.2 smoke confirmed this: all 13 aggregate windows had byte-identical weighted scores (0.5013), and every lens's per-window max-strength series had **zero variance**.
* **Why it matters**: The orchestrator's cross-lens correlation matrix — a spec-required boundary-report artifact and a load-bearing input to sub-phase 3.3's conflict engine + regime engine — is **undefined** on zero-variance series (Pearson correlation is 0/0). We cannot observe whether lenses agree/disagree over time, detect regime transitions, or surface conflicts, because there is no temporal variation in the input. The signal *generation* and *aggregation* machinery is proven correct; the *dynamics* are not yet observable.
* **Impact**: Sub-phase 3.3 (synthesis + regime engine + conflict engine) cannot be meaningfully validated against static data. Its acceptance criteria — "regime engine classifies each 5-min window with a label + confidence", "conflict engine emits ≥1 ConflictSignal per 1000 Signals", "CompositeSignal per coordinated event" — all require the input to vary over time.
* **Status**: OPEN — surfaced 2026-05-28 via sub-phase 3.2 smoke
* **Owner**: agent surfaces; resolution is a sub-phase 3.3 design decision
* **Resolution Path** (candidate approaches for 3.3):
  1. **Temporal replay**: instead of "scan current full state," have each lens advance a time cursor through the historical L3 data (e.g. each scan processes the next N-minute slice of `org_transfer_events`/`liquidity_events` by timestamp). This synthesizes a time series from historical data — $0 CU, and gives real temporal variation. Most promising.
  2. **Live data** (Phase 4): when bloxroute/Alchemy budget returns, lenses read a live stream and dynamics emerge naturally. Deferred per D-020/D-024.
  3. **Backtest windows**: replay a specific known-interesting historical window (e.g. the May 17 cross-chain burst from UNK-012) as a bounded temporal sequence to validate regime/conflict detection against ground truth.
* **Linked Decision**: **D-029** (`decisions/D-029_temporal-replay-mechanism.md`) — option 1 (temporal replay) chosen + implemented.
* **Deadline / Trigger**: Blocks sub-phase 3.3 acceptance. Must be resolved at 3.3 design time, before the regime + conflict engines are built.
* **Status**: RESOLVED — 2026-05-28
* **Resolution**: **Option 1 (temporal replay) implemented** via `ReplayClock` + adapter raw-row caching with `as_of_ts` + lens replay mode + lockstep driver + orchestrator event-time windowing. The 200-window replay over the 49.2-day data span produced aggregate scores with **166 distinct values of 200** (zero-variance defect gone), all 9 signal types firing, all 5 regimes appearing, 238 composites + 194 conflicts. The temporal dynamics that regime/conflict detection require are now present. D-029 documents the mechanism + the lockstep correctness fix (concurrent lenses fragmented windows; lockstep made the watermark monotonic).

---

## UNK-015: H5 margin only +7pp — proxy weakness, lens correlation, or genuine?

* **Description**: Sub-phase 3.4b's H5 test (orchestrator beats best single lens) came out DIRECTIONALLY correct but WEAK: orchestrator score↔outcome correlation |0.242| vs best single lens (graph) |0.172| = **+7.0pp**, short of H5's formal **≥15pp** bar. Three candidate explanations, indistinguishable on current evidence.
* **Why it matters**: H5 is the load-bearing Phase 3 hypothesis — "does multi-lens synthesis beat the best single lens?" If the orchestrator only adds 7pp, the whole multi-lens apparatus may not justify its complexity over a graph-lens-only detector.
* **Impact**: Determines whether Phase 3's architecture earns its keep, and whether Phase 4 should invest in more lenses or simplify.
* **Status**: RESOLVED (FINAL) — 2026-05-28 via D-041 then D-042 (entity-specific re-test)
* **Owner**: sub-phase 3.5 (replay harness) + Phase 4 (real outcomes)
* **RESOLUTION (D-041 → D-042 final)**: Candidate (c) GENUINE — the multi-lens synthesis adds no edge; it DILUTES the one predictive lens. Confirmed across SIX tests (proxy IS +7pp only; proxy OOS −5.1; real basket IS/OOS −11.7/−8.4; real entity-specific IS/OOS **−16.6/−11.0**). The un-exhausted refinement (entity-specific outcomes) was run and made the gap WIDER: it strengthened the information lens (corr 0.19→0.29) but the orchestrator still lost by −16.6pp because the near-non-predictive graph lens (corr 0.035) dominates signal volume and drags synthesis toward noise. Real predictive signal exists but ONLY in the information lens alone (~0.29). Phase 5 = NO-GO FINAL; productive follow-on is a single information-lens detector, not the orchestrator.
* **Resolution Path**: Candidate causes: (a) **proxy weakness** — the D-034 forward-flow proxy may not separate orchestrator from best-lens even if real P&L would; test by re-running H5 against Phase 4 realized outcomes. (b) **lens correlation** — graph dominates signal volume (7,632 of 7,938); stochastic+information are sparse, so the orchestrator is mostly graph anyway. Test by measuring inter-lens signal correlation + forcing balanced lens weighting. (c) **genuine** — multi-lens really adds little here; would falsify the architecture's value. The 3.5 replay harness (learned vs initial on held-out window) + Phase 4 real outcomes disambiguate.
* **Linked Decision**: none yet — will be a D-NNN after Phase 4 real-outcome H5 re-test. (D-039 records the 3.5 OOS evidence but does NOT resolve the unknown.)
* **Deadline / Trigger**: Before Phase 3 is declared a success/failure externally. Trigger = Phase 4 real outcomes available.
* **3.5 out-of-sample evidence (2026-05-28, D-039)**: The rigorous train/holdout test made it WORSE, not better. In-sample the orchestrator beat best-lens by +7.0pp; **out-of-sample it was −5.1pp** (orch |corr| 0.016 vs best lens stochastic |corr| 0.067 on 60 holdout windows). All proxy correlations are near-zero (0.016–0.067), and observability shows no lens is a strong escalation predictor. This rules IN candidate (a) proxy-weakness as a likely contributor (the forward-flow proxy may simply be too weak a target) and keeps (c) genuinely-no-edge live; it cannot exonerate the architecture. Only Phase 4 realized-outcome data can distinguish "proxy too weak" from "multi-lens adds nothing." Until then, **the orchestrator's edge over a single lens is UNPROVEN.**

---

## UNK-016: Does H9 (entropy_drop-on-roles → return) survive survivorship + costs + multi-window?

* **Description**: D-043 found `entropy_drop` on a token's transfer-role distribution predicts forward return out-of-sample (signed corr ≈+0.48, n=74 holdout, 24-48h). It is the first OOS-surviving signal in the project. But three threats remain unaddressed: survivorship bias, transaction costs, and single-window over-fit.
* **Why it matters**: H9 is the only live tradable-edge candidate after the multi-lens NO-GO (D-042). Whether it's real or an artifact determines if there's ANY path to a future execution phase.
* **Impact**: Decides whether the project has a viable signal at all, and whether a focused single-signal detector is worth building/financing.
* **Status**: CLOSED — 2026-06-08 via D-047 (input foundation contaminated; H9 thesis withdrawn). [Was: RESOLVED 2026-05-28 via D-044 "validated with caveats"; then live OOS negative 2026-06-02; now CLOSED on root cause.]
* **CAPSTONE / ROOT CAUSE (D-047, 2026-06-08)**: L3 retired itself and declared its behavioral LABELS unreliable ("observations sound, labels suspect"; org/role mapping collapsed). H9 = `entropy_drop` on the `from_role` distribution — **exactly the contaminated label layer.** So H9 was computing entropy over largely-mislabeled noise. This is the CAUSAL root of the in-sample-good / out-of-sample-bad pattern (a noise-label signal does exactly that). The question is now moot: the input is known-bad, the live OOS read was already negative (−$6,444, 27% win), and the paper trade is wound down. No viable single-signal edge; no proprietary-data edge remains.
* **Owner**: closed.
* **GATE 1 (survivorship) — PASSED 2026-05-28** (`engine/data/info_lens_survivorship.log`): 0 of 86 `entropy_drop`/roles signals fired on a token that died mid-window or was unpriceable at signal time. Death-aware corr == survivor-only corr exactly (holdout +0.480 @24h, +0.506 @48h). The directional edge is NOT a survivorship artifact. Nuance: partly because (a) the signal self-selects liquid tokens (needs ≥10 transfer events/window to fire) and (b) the monitored universe is established tokens, not fresh-launch rugs — so H9 is survivorship-robust ON ESTABLISHED TOKENS specifically.
* **GATE 3 (beta-vs-alpha) — PASSED 2026-05-28** (`engine/data/info_lens_regime.log`): A true second window is impossible (L3 role labels end 2026-05-18 + are L3-proprietary; Alchemy CU can't reconstruct them — sync would have to resume). So tested market-neutrality instead. (a) Market basket mean was only ~+1% (NOT a bull run); the +25.8% @48h was the SIGNAL's tokens vs ~+1.5% market = wide outperformance. (b) corr(strength, EXCESS return) = +0.46 @24h / **+0.49–0.50 @48h** — barely below the raw corr → the edge is ALPHA, not beta. (c) Horizon caveat: up/down-phase split shows 24h INVERTS in down-markets (corr −0.53, n=25) while **48h is regime-robust** (+0.50 up / +0.58 down). → use the 48h horizon; 24h would need market-direction-awareness. Down-phase subsamples small (n=21-25), suggestive not definitive.
* **REMAINING — GATE 2 (costs)**: the last make-or-break. +0.50 excess corr on tokens averaging ~+24% excess @48h is large, but memecoin enter+exit slippage on thin pools could eat most of it. Apply Phase 2 cost model (D-010 fees + depth-based slippage). CU note: exact pool depth at signal blocks would sharpen slippage (<100K CU, well within the 77M available) but TVL-proxy is a fine first pass.
* **Resolution Path**:
  1. **Survivorship**: re-run including dead/delisted tokens (assign ≈−100% or last-traded price to tokens that lost price coverage mid-window). Re-test whether the DIRECTIONAL (signed) edge holds; magnitude edge is more robust. $0 CU (DefiLlama + dead-token list).
  2. **Costs**: apply the Phase 2 cost model (D-010 static fees + slippage on memecoin-pool depth) to convert the corr into net expectancy per trade. A signal with +0.5 corr but negative after-cost expectancy is not tradable.
  3. **Multi-window**: replicate on ≥2 other historical windows / different market regimes. The current result is one 49-day period.
* **Linked Decision**: none yet — will be a D-NNN after the survivorship + cost + multi-window tests either confirm H9 (tradable) or kill it (artifact).
* **Deadline / Trigger**: Before any single-signal execution consideration. Trigger = focused H9 validation study runs.

* **LIVE FORWARD REPLICATION (ongoing, the true second window) — first read NEGATIVE-LEANING, 2026-06-02:**
  L3 resumed; surveillance.db delta-synced to 2026-06-02 (~15 days of genuinely forward data past the 2026-05-18 in-sample cutoff). The live paper-trade harness (`engine/scripts/paper_trade_h9.py`, OOS-cutoff-gated, $0 capital/$0 CU) ran its **first true out-of-sample window**:
  - 29 OOS entropy_drop/roles signals, **26 closed trades**.
  - **Win rate 27%** (vs 42% in-sample), **mean net −2.5%/trade** (vs +37%), **best trade only +3%** (in-sample edge was carried by +200–300% tail winners — NONE appeared), cumulative **−$6,444** @ $10k/trade, 0 deaths.
  - **Read:** the in-sample edge has NOT replicated so far. The dropped win rate (42%→27%) is a negative signal independent of the tails; the absence of any winner is consistent EITHER with overfitting OR with the convex strategy's normal dry spell (≈1-in-6 chance of 0 winners in 26 trades by luck) + a quiet memecoin window. n=26 / 15 days is too small for a tail-dependent strategy to be conclusive, but the trajectory does NOT support deploying capital.
  - **Operational:** Windows scheduled task `H9PaperTrade` runs the harness every 12h (StartWhenAvailable → catches up after reboot/update); state writes are atomic + .bak-backed. Record accumulates hands-off. WATCH: (1) does any tail winner appear, (2) does win rate recover toward 42% or hold ~27%.
  - **Financier relevance:** this live paper record is the concrete, non-theoretical argument against trading real capital now — the strategy is currently DOWN over the exact period capital deployment was being pushed.
