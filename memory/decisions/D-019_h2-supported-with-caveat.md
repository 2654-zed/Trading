# D-019: H2 → SUPPORTED-WITH-CAVEAT (first L3 yield in EXP-002 data)

**Date**: 2026-05-25
**Made by**: agent (per `trades/2026-05-25_exp-002-summary.md` LOOP Step 6 hypothesis scoring + Step 5 decision logging)
**Status**: ACTIVE

## Context

H2 (`STRATEGY_STATE.md`): *"Layer 3's intelligence flags ≥1% of detected
opportunities"*. Falsification: *"0% flag rate after 7 days"*.

Across all of Phase 1 (EXP-001) and most of Phase 2 (EXP-002), H2 had
been WEAKENED-to-untestable because:
1. Phase 1 had 100% degraded eval rate (UNK-005, fixed by D-012)
2. Phase 2 Run 1's MSUSD/USDC arb produced 0% flag rate
3. Phase 2 Runs 2-3 stalled before producing data
4. The May 23-24 data continued at 0% flag rate

The 2026-05-25 LOOP re-analysis of the preserved EXP-002 data (which
my interim T+24h health checks had only partially sampled) surfaced
the actual flag distribution:

- **1,047 / 26,122 records hard-flagged = 4.01% overall**
- **1,046 / 1,238 cross-chain records flagged = 84.5%**
- All hard flags fired rule_2 `org_wallet_membership` (Tier A)
- 2 records additionally fired rule_3 `drain_detected_approval`

H2's letter threshold (≥1%) is met decisively. But the structure
underneath the number deserves an explicit caveat before the verdict
becomes load-bearing on future Phase 2/3 work.

## Options considered

### Option 1: Record H2 as SUPPORTED-WITH-CAVEAT ← **CHOSEN**

H2 is met by the letter. But the caveat must be documented:
- All flags fired one rule (rule_2)
- All flags traced to a small set of pool addresses (chiefly
  `0xc6962004f452be9203591991d15f6b388e09e8d0` — the canonical
  WETH/USDC Aerodrome Slipstream pool on Arbitrum)
- The 4.01% / 84.5% rate is bursty + concentrated, not spread across
  many independent opportunities

Pros: faithful to both the letter (≥1% met) and the spirit
(distinguishes "L3 flagged 100s of opportunities" from "L3 flagged a
small set of pool addresses that those opportunities all touched").

Cons: forces nuanced reading; "SUPPORTED-WITH-CAVEAT" is a stickier
verdict than a binary SUPPORTED.

### Option 2: Record H2 as SUPPORTED (full)

Pros: simple.

Cons: misleading. A future researcher reading "H2 SUPPORTED — 84.5%
cross-chain flag rate" would conclude L3 has identified hundreds of
distinct risky paths. The reality is more like "L3 has identified one
pool, and that pool happens to dominate our monitored cross-chain
graph". The two readings have very different implications for Phase 3
go/no-go.

### Option 3: Record H2 as WEAKENED (because concentration on one pool is too narrow)

Pros: conservative.

Cons: contradicts the explicit hypothesis text (≥1% threshold) and
the rule-fire evidence (rule_2 IS firing per L3's data on those pool
addresses). A future re-run that surfaces a SECOND independent flag
would flip back to SUPPORTED — but we'd have downgraded prematurely
in the interim.

## Decision

**H2 status: SUPPORTED-WITH-CAVEAT.**

The caveat language (to be carried forward in `STRATEGY_STATE.md` H2
row):

> H2 met by the letter — 4.01% overall hard-flag rate (84.5% on
> cross-chain). All flags fired Tier-A rule_2 (`org_wallet_membership`)
> on a small set of pool addresses, chiefly the canonical Arbitrum
> WETH/USDC Aerodrome Slipstream pool. The flag concentration on one
> pool address means H2's "fraction of opportunities flagged" is
> driven by one structural overlap rather than many independent flags.
> H3 (distributional comparison flagged vs unflagged) testable for
> the first time. Re-evaluate H2's spirit after H3 runs.

## Rationale

- The hypothesis text is met. Pretending otherwise is dishonest about
  the data.
- The caveat preserves the distinction between "letter satisfied" and
  "research finding strong enough to justify Phase 3 investment".
- The "SUPPORTED-WITH-CAVEAT" verdict explicitly cues the next decision
  point: H3 — does the L3-flagged set differ structurally from the
  unflagged set? That's the question whose answer determines whether
  H2's yield is operationally useful.

## Consequences expected

- `STRATEGY_STATE.md` H2 row updated with the caveat text + status
- H3 becomes the load-bearing hypothesis for the rest of Phase 2.
  Previously H3 was structurally untestable (no flagged group); now
  with 1,047 flagged + 25,075 unflagged records, the Mann-Whitney /
  χ² tests in `analysis/h3_distributional_comparison.py` are run-able.
- Phase 3 (execution-mode) go/no-go is now downstream of H3, not H2.
  If H3 shows flagged opportunities have structurally different
  margins / pool-type / token distributions, Phase 3 has signal worth
  pursuing. If H3 shows no structural difference, the H2 flag yield
  is a one-pool artifact and Phase 3's premise weakens.
- UNK-011 (new in this LOOP — "is rule_2 on `0xc6962004...` a true
  positive?") becomes a Phase-3-go/no-go gate as well.

## Reversal criteria

- **A subsequent run (post-D-017 redeploy) produces ZERO hard flags
  across a similar emission window.** Then the May 17 flags were a
  one-time L3 corpus state, not a sustainable signal. H2 demotes back
  to WEAKENED with this evidence.
- **H3 returns "no structural difference"** (Mann-Whitney p > 0.05 AND
  χ² p > 0.05 across all numeric + categorical dimensions). Then H2's
  letter is met but the spirit is unsatisfied — H2 demotes to
  SUPPORTED-BUT-NOT-OPERATIONALLY-USEFUL.
- **L3 changes its `org_wallets` classification rule** in a way that
  retroactively un-flags `0xc6962004...`. Then the 84.5% cross-chain
  flag rate evaporates and H2 demotes.

## Consequences for the memory system

- `decisions/README.md` active table — add D-019
- `STRATEGY_STATE.md` H2 row — replace the prior "WEAKENED — structurally
  untestable" text with the SUPPORTED-WITH-CAVEAT language above
- `unknowns/UNKNOWNS.md` UNK-011 — file as new UNK (concentration on
  one flagged pool; true-positive verification pending)
- `trades/2026-05-25_exp-002-summary.md` — this decision is referenced
  there as the hypothesis-scoring artifact

## Links

- LOOP write-up: `../trades/2026-05-25_exp-002-summary.md`
- Per-table freshness fix that made H2 testable: `D-012_per-table-freshness-thresholds.md`
- The flagged pool address: `0xc6962004f452be9203591991d15f6b388e09e8d0`
  (canonical WETH/USDC Aerodrome Slipstream on Arbitrum, fee tier ~5 bps)
- Rule definition: `layer3_trading_exp/filter_rules.py` rule_2_org_wallet_membership
