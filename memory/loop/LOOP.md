# LOOP.md

**The reflection loop is REQUIRED behavior.** Per `INVARIANTS.md` I-10,
skipping the loop after a triggering event is itself an invariant
violation.

This file describes the loop the agent MUST execute. The loop is the only
mechanism that prevents silent drift of state, unknowns, and decisions.

---

## When the loop fires

Run the full loop AFTER any of:

1. **A completed detection run** — i.e. when `detect_dry_run.py` exits (whether via `--minutes` timeout, kill switch, SIGTERM, or crash).
2. **A backtest or analysis run** — running `scripts/run_analysis.py` against accumulated JSONL.
3. **A code change touching runtime behavior** — modifications to `opportunity_detector.py`, `filter_pipeline.py`, `filter_rules.py`, `pool_monitor.py`, `pool_set.py`, `sync_l3_db.py`, `logger.py`, or any of the quote modules.
4. **A live deployment** — `railway up`, `railway redeploy`, or any env var change that triggers an auto-redeploy.
5. **A discovered surprise during operation** — anything that makes you say "huh, that's weird" while watching the system. Loop the moment you can.

---

## The six steps

Run each step in order. Skipping a step is a loop violation.

### Step 1 — Opportunity Review

(For a detection-only system. In a future Phase 2 execution variant this becomes "Trade Review.")

Read the JSONL log(s) covering the run window. Compute:

```
- Total records
- Distinct opportunity keys (pool_pair × borrowed_token)
- Margin distribution (min / p50 / p95 / max)
- Net gain USD distribution
- Hard-flagged count, soft-flagged count, unflagged count
- Degraded count (layer3_stale=true)
- Per-protocol-combo distribution
```

For the running deployment, use:

```bash
railway ssh "python -c \"
import json, collections, os
log = '/app/data/logs/2026-05-13.jsonl'  # adjust date
recs = [json.loads(l) for l in open(log) if l.strip()]
print(f'records: {len(recs)}')
keys = collections.Counter()
for r in recs:
    pp = tuple(sorted(p.lower() for p in r['path']['pools'][:2]))
    b = r['path']['tokens'][0].lower()
    keys[(pp, b)] += 1
print(f'unique keys: {len(keys)}')
\""
```

Capture the snapshot in `trades/YYYY-MM-DD_review.md`.

### Step 2 — Strategy Check

Compare against `STRATEGY_STATE.md`:

- Were any active hypotheses (H1/H2/H3) supported or weakened by this run?
- Were any success criteria met?
- Did any DECISION POINT trigger fire? (See bottom of STRATEGY_STATE.md.)

If a hypothesis was clearly weakened, this is a strategy-change signal.
**Do not edit `STRATEGY_STATE.md` here yet** — that's Step 5. Just note the observation.

### Step 3 — Risk Check

Walk every invariant in `INVARIANTS.md`. For each, verify:

| Invariant | Check |
|---|---|
| I-1 (read-only on-chain) | `grep -rn "eth_sendTransaction\|signTransaction\|privateKey" layer3_trading_exp/` returns 0 |
| I-2 (frozen pool set) | `monitored_pools.json` mtime unchanged during the run |
| I-3 (read-only on L3) | No new L3 HTTP write paths in code |
| I-4 (no LLM in runtime) | `grep -rn "openai\|anthropic\|llm" layer3_trading_exp/` returns 0 |
| I-5 (no adaptive rules) | No runtime config writes since run start |
| I-6 (loud failure) | `railway logs --deployment | grep -iE "GAP\|FAILED\|Traceback"` reviewed |
| I-7 (perf budgets) | Filter eval p95 from JSONL evaluation_times, Alchemy CU from dashboard |
| I-8 (kill switch authority) | No unauthorized restart/redeploy in `railway deployment list` |
| I-9 (secrets) | `railway variables` was not run unfiltered this session |
| I-10 (loop completion) | This loop is running — passes by construction |

**Any failed check → STOP and file an entry in `failures/FAILURE_LOG.md` before continuing.**

### Step 4 — Unknown Extraction

Look at the run output, the surprises, and the questions you couldn't answer cleanly. For each new unknown that emerged:

- Add a `UNK-NNN: <name>` entry to `unknowns/UNKNOWNS.md` using the schema there.
- If a previous UNK is now resolved, mark it `STATUS: RESOLVED <date>` with the resolution.

**Concrete prompts to find unknowns:**
- What did the data show that you can't explain?
- What metric was outside its expected range with no clear cause?
- What did you assume about the system that this run made you doubt?
- What would a future agent need to know that isn't documented?

### Step 5 — Decision Logging

For every operational or strategy decision made during/about this run:

Create or append to a `decisions/D-NNN_<short-name>.md` file. The decision log entry must include:

```
# D-NNN: <decision name>
**Date**: YYYY-MM-DD
**Made by**: <user or agent>
**Context**: what was happening that required a decision
**Options considered**: ordered list of alternatives
**Decision**: which option, in one sentence
**Rationale**: why this option
**Consequences expected**: what we predict happens because of this
**Reversal criteria**: what would make us undo this
**Links**: PRs / commits / failures referenced
```

If the decision modifies strategy intent or active hypotheses → update `STRATEGY_STATE.md`.
If the decision modifies the active configuration → update `SYSTEM_STATE.md`.

### Step 6 — Hypothesis Scoring (CRITICAL)

This step forces explicit belief updates. Without it, the system passively
observes and never lets evidence change strategy.

For EACH active hypothesis in `STRATEGY_STATE.md`:

1. Re-read the hypothesis statement.
2. Compare it against the data surfaced in Step 1 (Opportunity Review).
3. Assign a status:
   - **ACTIVE** — evidence so far supports the hypothesis or is consistent with it; continue investing in measurement
   - **WEAKENED** — evidence so far does not support it as strongly as expected; the hypothesis remains testable but probability has dropped; **MUST log a one-sentence observation** in the relevant `trades/YYYY-MM-DD_review.md`
   - **INVALIDATED** — evidence contradicts the hypothesis at a level we can't ignore; the hypothesis is wrong as stated and a strategy update is required

#### Required behavior by status

| Status assigned | Required action |
|---|---|
| ACTIVE | None beyond noting "Hypothesis HN: ACTIVE — no change" in the review file |
| WEAKENED | Add one-line observation to `trades/YYYY-MM-DD_review.md` under a `## Hypothesis check` section, citing the metric or sample size that weakens it |
| INVALIDATED | **MANDATORY**: (a) create a new `decisions/D-NNN_hypothesis-update-HN.md` documenting what to do about it; (b) update `STRATEGY_STATE.md` — move the row in the hypothesis table to a new "Invalidated hypotheses" section (do not delete) with the date and the linked decision ID; (c) re-evaluate Step 7 (Next Focus) since strategy intent just shifted |

#### Concrete guidance for assigning status

- A hypothesis with a numeric threshold (e.g. H1: ≥1,000 opps/day) → WEAKENED when extrapolated rate is half the threshold; INVALIDATED when extrapolated rate is <10% of threshold over a sample size that should have caught it.
- A hypothesis predicting a phenomenon exists (e.g. H2: L3 flags ≥1% of opps) → WEAKENED when the phenomenon hasn't appeared yet but sample is small; INVALIDATED when sample is large enough that null finding has tight confidence interval AND the cause is structural (e.g. 100% degraded → H2 untestable, INVALIDATE-for-cause).
- A hypothesis predicting a difference (e.g. H3: flagged ≠ unflagged distributions) → WEAKENED on p > 0.05 with small sample; INVALIDATED on p > 0.05 with sample size that gives ≥80% power against the alternative we cared about.

#### What WEAKENED vs INVALIDATED is NOT

- WEAKENED is not "I'm worried about this hypothesis" — it needs a specific datum
- INVALIDATED is not "the run didn't show the result I wanted" — it requires the result PLUS a structural reason the result can't appear later
- Neither is a license to skip Step 7 (Next Focus); both feed INTO it

### Step 7 — Next Focus Selection

After 1-6, pick ONE concrete next focus. Not a list — one thing.

If Step 6 produced any INVALIDATED hypothesis, the next focus MUST address the invalidation (either pivot strategy, redesign measurement, or close out the affected experiment). Don't default back to the prior plan.

Update `SYSTEM_STATE.md`'s health snapshot section if state changed. If the next focus needs a new spec, draft it. If the next focus is "wait for more data," explicitly document the duration and the trigger condition.

Write the focus selection as the LAST line of the loop session, e.g.:
```
NEXT FOCUS (set 2026-05-13 06:30 UTC): Wait for EXP-001 1-day run to complete.
Trigger to act: --minutes 1440 timer expires OR run summary written to data/run_metadata/
```

---

## Loop output discipline

Each loop execution produces:

1. A new `trades/YYYY-MM-DD_review.md` file (Step 1)
2. Any new entries in `failures/FAILURE_LOG.md` (Step 3 if a check failed)
3. Any new/updated entries in `unknowns/UNKNOWNS.md` (Step 4)
4. Any new files in `decisions/` (Step 5)
5. An updated NEXT FOCUS line in this file (Step 6) OR in `SYSTEM_STATE.md`'s health snapshot

If a loop execution produces ZERO output files, that's a signal the run was too quiet to warrant a loop, OR the loop was performed cargo-cult — flag the latter case in the next decision log.

---

## What this loop is NOT

- Not a status report for human consumption
- Not a checklist to skim
- Not optional when "everything looks fine"
- Not a place to celebrate (or commiserate)

It's a forcing function to ensure decisions, failures, and surprises don't accumulate silently.

---

## Loop history

Append a one-line entry per loop execution at the bottom.

```
Format: YYYY-MM-DD HH:MM | trigger | review file | new failures | new unknowns | new decisions | NEXT FOCUS
```

| Date | Trigger | Review | Failures+ | Unknowns+ | Decisions+ | H1 | H2 | H3 | NEXT FOCUS |
|---|---|---|---|---|---|---|---|---|---|
| 2026-05-13 06:30 | Memory system initialization | trades/2026-05-13_pre-index-fix.md | 0 (backfilled 6) | 0 (backfilled 9) | 0 (5 seeded) | ACTIVE | WEAKENED (100% degraded — structurally untestable until UNK-005 resolves) | ACTIVE | Wait for EXP-001 1-day run; then full loop |
| 2026-05-16 14:30 | EXP-001 terminated (`railway down`) + Phase 1.5 analysis run | trades/2026-05-13_exp-001-final.md | +1 (timer-didn't-terminate, OPEN) | UNK-002 RESOLVED (D-008), UNK-006 RESOLVED (D-008) | +2 (D-007 H1 invalidated, D-008 UNK-002/006 resolution) | **INVALIDATED** (D-007, at current floors) | WEAKENED (unchanged — still depends on UNK-005) | WEAKENED (no flagged group existed in EXP-001 sample) | Draft Phase 2 cross-chain spec (D-006 Across model unblocks; resolve UNK-003 + UNK-008 + UNK-005 inside the spec) |
| 2026-05-25 | EXP-002 halted (FAILURE_LOG 2026-05-24 CU spike) + 7-step LOOP against preserved data | trades/2026-05-25_exp-002-summary.md | 0 new (status updates only) | UNK-002 REVERTED-to-OPEN (D-018), +3 new (UNK-010 stale L3 tables, UNK-011 single-pool flag verification, UNK-012 cross-chain burst structure) | +2 (D-018 UNK-002 reversal, D-019 H2 supported-with-caveat) | **INVALIDATED** (unchanged, D-007) | **SUPPORTED-WITH-CAVEAT** (D-019; 4.01% overall / 84.5% cross-chain hard-flag rate — first time H2 met across both phases) | NEW DATA AVAILABLE — 1,047 flagged vs 25,075 unflagged records now enable Mann-Whitney/χ² tests; H4 UNDETERMINED (n=2 buckets too thin); H1' WEAKENED (12 unique cross-chain in 1h window, then 0 across remaining ~13h detection) | Push to GitHub → Railway auto-deploy with D-017 fix → run 7d continuous → answer H1' SUPPORTED vs FALSIFIED at next LOOP |

---

## NEXT FOCUS (current)

**Set 2026-05-25 (Phase 3 spec approved, EXP-003 active)**: Phase 2's
LOOP closed (`trades/2026-05-25_exp-002-summary.md`). User approved the
Phase 3 multi-lens engine spec (`PHASE_3_MULTI_LENS_ENGINE_SPEC.md`)
per **D-020**. Engine ships as separate `engine/` package at repo root.
bloxroute migration deferred to Phase 4 (after engine is coded).

**Current focus**: Produce the **mandatory pre-work summary** per the
Phase 3 spec § MANDATORY PRE-WORK (10 files to read; one-paragraph
output covering data-path changes, module reuse mapping, new
abstractions introduced). User reviews before sub-phase 3.1 begins. No
engine code until the summary is approved.

**Next gate after pre-work**: agent starts sub-phase 3.1 (Signal schema
+ event bus + first lens = graph). Each sub-phase has a "stop here"
boundary with explicit acceptance criteria.

**Parallel work that doesn't block Phase 3**:
- H3 distributional test against the 1,047 flagged + 25,075 unflagged
  records from EXP-002 — the H3 analysis pipeline is ready and the
  data exists locally; doesn't need a running detector
- UNK-010 / UNK-011 / UNK-012 resolution (L3 cadence investigation;
  rule_2 true-positive attestation; cross-chain burst characterization)
- H1' falsification on a future long-running observation window —
  blocked until Phase 3 sub-phase 3.4 produces signals at observation
  cadence OR an earlier Phase-2-style run is scheduled (no current
  plan to do the latter)

> Earlier focus (2026-05-25 post-RCA-fix): Push to GitHub → run 7 days
> continuous to validate D-017 + check H1' / H3. SUPERSEDED 2026-05-25
> by D-020 — instead of continuing Phase 2 measurement, we pivot to
> Phase 3 engine build. Phase 2 outstanding items roll into the parallel
> work list above.

---

> Below: pre-D-020 retained focus context for history. (Phase 3 superseded.)

**Set 2026-05-25 (post-EXP-002-LOOP, pre-Phase-3)**: Push to GitHub → Railway
auto-deploys with the D-017 subscription-lifecycle fix. Run 7 days
continuous, then execute the next LOOP. Three load-bearing outcomes
this run answers:

1. **D-017 fix validation** (D-015 budget): post-deploy, Base newHeads
   CU rate ≤ ~3M/day per chain (vs peak ~445M/day during the leak).
   Sample window: first 30 minutes. Pass → D-015 flips back to ACTIVE +
   FAILURE_LOG 2026-05-24 closes RESOLVED.
2. **H1' SUPPORTED vs FALSIFIED**: cross-chain emissions across a clean
   7-day window. ≥1 new burst → H1' remains testable; could be SUPPORTED
   if the burst rate extrapolates ≥10/day (above falsification floor).
   Zero new bursts → H1' falsifies at <10/day with 7d wall-clock.
3. **H3 distributional test** runs against the 1,047-flagged + 25,075-
   unflagged sample from EXP-002 — answers whether L3's rule_2 flag
   identifies structurally-different opportunities (margin, protocol,
   token distributions) or just a single-pool overlap with no structural
   significance.

**Pre-deploy gate** (D-017 reversal criteria): the GitHub push to main
triggers Railway auto-deploy. Within 30 min, confirm via Alchemy
dashboard "WebSocket usage by Network" panel that Base newHeads CU is
≤ ~3M/day. If observed > 10M/day → halt + investigate; D-017 didn't
land properly.

**Bound on the next run**: 7 days. If H1' falsifies at run-end, Phase 2
closes out and we make the Phase 3 go/no-go decision. If H1' remains
testable + H3 shows structural difference, Phase 2 extends with
sharper measurement focus.

**Loop-history-able events triggering an earlier LOOP**:
- I-11 T-A (lag>30s anywhere)
- I-11 T-B (any hypothesis flips status)
- I-11 T-C (repeat failure of same class)
- I-11 T-D (UNK blocking execution)
- D-017 reversal trigger fires (Base newHeads CU > 10M/day)

> Earlier focus (2026-05-25 post-RCA, awaiting redeploy): Fix root cause
> of newHeads subscription leak. Done — fix in commits `9ca2565` +
> `a810799`, D-017 documents the pattern. This next focus is the
> post-fix observation cycle.

**Redeploy is now operator-gated** (only remaining precondition):
- Push to GitHub triggers Railway auto-deploy (per the May 22 wiring)
- Within ~30 min of healthy operation, check Alchemy "WebSocket usage by
  Network" panel: Base newHeads CU rate should be ≤ ~3M CU/day (vs
  peak ~445M/day during the leak). If observed > 10M/day per chain →
  D-017 reversal trigger fires; halt + investigate.
- After one clean cycle (24h), `D-015` flips back from REVERSAL TRIGGERED
  to ACTIVE.

**Work that's available without redeploying** (and useful regardless):
execute the LOOP against the preserved JSONL data — Run 1's 7h
cross-chain burst (12 unique keys) + May 23 MSUSD/USDC arb (~7.9h
open window, 12,153 records, 1 unique key) + May 24 transient WETH
arbs (2 records, 146/93 bps margins). The data + analysis pipeline are
the deliverable; further CU burn is optional at this point. Possible
output: `trades/2026-05-XX_exp-002-summary.md` with H1' / H2 / H4
verdicts + the formal D-008 UNK-002 reversal write-up.

> Earlier focus (2026-05-18 ~23:15 UTC post-redeploy): Monitor EXP-002
> resumed with WS-stall detector. Multiple subsequent failure modes hit
> (silent stalls, ConnectionClosedError loops, Railway CLI upload
> dysfunction). Resolved via 2.8.4 unified-escalation + GitHub auto-deploy.
> Then CU spike forced this halt. Sequence captured in FAILURE_LOG
> 2026-05-18 / -20 / -21 / -22 / -24 entries + D-016 (two-level reconnect).

> Earlier focus (2026-05-17 ~17:25 UTC): monitor EXP-002 + sample CU at
> T+30min. Completed — UNK-003 resolved via D-015 (~34% utilization).
> Interim analysis at T+24h found Phase 2 cross-chain DETECTION worked
> (1,238 cross-chain records / 12 unique opp keys in first 7h before
> stall) but run had stalled. Failure resolved via 2.8.1 patch.

**Trigger conditions for early intervention (per I-11):**
- Lag > 30s on any chain (T-A)
- Hypothesis invalidation (T-B) — note that D-008's UNK-002 resolution
  reversal is already informally fired (MSUSD/USDC arb appears to have
  reopened); formally addressed at LOOP time
- Repeat failures (T-C)
- Unknown blocking execution (T-D)

**At run-end:**
1. Pull JSONL from `/app/data/logs/` to `run_artifacts/exp_002/`
2. Run `scripts/run_analysis.py --start 2026-05-17 --end 2026-05-24`
3. Execute full LOOP — produce `trades/2026-05-XX_exp-002-summary.md`
   with H1' / H2 / H3 / H4 verdicts
4. Address D-008 UNK-002 reversal in the loop's Step 5 decision logging
5. File D-015 resolving UNK-003 from observed CU usage

> Earlier focus (set 2026-05-16 post-approval): Complete the mandatory
> pre-work summary, then execute sub-phases 2.1–2.8. Completed:
> all sub-phases landed + deployed; EXP-002 went LIVE 2026-05-17 17:20 UTC.

- **Trigger to set this focus**: H1 INVALIDATED at current Base-only floors
  (D-007). LOOP Step 7 mandates the next focus address the invalidation.
  Phase 2 measures a different opportunity surface (Base + Arb + OP via
  Across bridge) that does not depend on intra-Base price drift remaining
  open. Bridge model is unblocked (D-006).
- **First action when this focus is picked up**: outline the spec covering
  (a) monitored pool sets per chain at floors comparable to Phase 1,
  (b) Across bridge integration (per D-006 parameters — 30s latency,
  10 bps point estimate fee, 5-20 bps sensitivity), (c) token registry
  approach (curated canonical list vs auto-discovery — sub-decision),
  (d) schema migrations adding `chain` field to PoolInfo / Opportunity
  / JSONL, (e) round-trip vs one-way trade structure resolution,
  (f) projected Alchemy CU budget against 300M/month free tier (this
  resolves UNK-003), (g) projected JSONL size at 3-chain volume (this
  resolves UNK-008), (h) per-table L3 freshness thresholds so H2 is not
  structurally vacuous in Phase 2 (this resolves UNK-005).
- **Constraint**: spec must be drafted for user review BEFORE any code
  changes. Same workflow as Phase 1 sub-phases.
- **Do NOT**: redeploy EXP-001, re-run Base-only at current floors, or
  start coding Phase 2 implementation. The spec is the single deliverable.
- **Side task during spec drafting**: investigate the `--minutes 1440`
  timer-didn't-terminate failure (2026-05-16 entry in FAILURE_LOG) — the
  fix must land before any future time-bounded run.
