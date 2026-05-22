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

## UNK-002: Will the MSUSD/USDC arb reopen?

* **Description**: The persistent MSUSD/USDC arb (Aerodrome Slipstream CL50 ↔ Aerodrome stable) was emitting every block for 2 hours, then closed during the slow-processing outage. Will it reopen during the 1-day window? At what cadence?
* **Why it matters**: This is the dominant signal in our prior data. If it stays closed, H1 statistics from this 1-day run reflect a different opportunity regime than the first 2 hours did.
* **Impact**: We can't claim a steady-state arbitrage rate without observing the open/close cadence.
* **Status**: RESOLVED — 2026-05-16
* **Owner**: 1-day analysis (Phase 1.5 report)
* **Resolution Path**: When EXP-001 completes, group JSONL records by hour, count distinct opp keys per hour. If MSUSD/USDC reappears, note duration of each "open" window. Resolves with either D-NNN "MSUSD/USDC reopened — H1 supported via persistence pattern" OR D-NNN "MSUSD/USDC stayed closed — first 2h was anomalous".
* **Linked Decision**: **D-008** (`decisions/D-008_resolve-unk-002-and-006.md`)
* **Deadline / Trigger**: EXP-001 completes (--minutes 1440 timer expires, ~2026-05-14 05:30 UTC)
* **Resolution**: **The arb did NOT reopen in ~2.5 days of post-closure observation (~104K blocks).** The detector ran cleanly through this window with zero JSONL records. Strong negative result. The first 1.85 hours of observation captured an inefficiency that subsequently closed and stayed closed. Per D-008: this is consistent with the arb being structurally rare (rebalancing event) rather than persistently-available, OR with a market participant having taken the spread and rebalanced the pool.

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
