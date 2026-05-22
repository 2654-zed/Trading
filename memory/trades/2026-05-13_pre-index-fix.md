# Opportunity review — 2026-05-13 pre-index-fix window

**Window**: 2026-05-13 04:28:21 UTC → 06:19:29 UTC (~111 min)
**Block range**: 45,928,573 → 45,930,992 (2,419 blocks)
**Run**: EXP-001, pre-restart segment
**Trigger**: Mid-session analysis requested by user

## Headline numbers

| Metric | Value |
|---|---|
| JSONL records | 2,420 |
| Block coverage | 2,419 (1 record/block, no gaps) |
| Unique opportunity keys | **1** |
| Margin distinct values | **1** (all pinned at 30 bps = 0.301%) |
| Net gain USD distinct (rounded) | 14 |
| Net gain USD range | $25.06 – $25.08 |
| Hard-flagged | 0 / 2,420 |
| Soft-flagged | 0 / 2,420 |
| Layer3_stale | **2,420 / 2,420** (100% degraded) |
| Per-rule fires | NONE — zero rules fired across the whole window |

## The single opportunity

```
borrowed:  0x833589fcd6edb6e08f4c7c32d4f71b54bda02913  (USDC)
pool_x:    0x7501bc8bb51616f79bfa524e464fb7b41f0b10fb  (Aerodrome Slipstream MSUSD/USDC CL50)
pool_y:    0xcefc8b799a8ee5d9b312aeca73262645d664aaf7  (Aerodrome stable MSUSD/USDC)
margin:    0.301% (30 bps after round)
net_gain:  ~$25.07
fires:     0 of 13 rules
```

This is the same persistent arb observed in every Phase 1.2 dry-run during
development. It represents structural pricing inefficiency between two
Aerodrome pools holding MSUSD/USDC. Layer 3 has no flags against either
pool or the involved tokens — consistent with both being legitimate
Aerodrome infrastructure.

## Caveats on this data

1. **All records `layer3_stale=True`** because the 30-min freshness threshold
   doesn't match L3's actual table update cadence (multi-hour). See
   `../unknowns/UNKNOWNS.md` UNK-005.

2. **Lag grew from 0.4s to 1,836s during this window** because of the
   missing-index bug. Block timestamps in records are accurate, but the
   `evaluated_at` timestamp lags reality by an amount that grows
   linearly. Treat timestamp-based grouping with caution.

3. **Zero variety** in margin / gain / opportunity key. The detector was
   functioning correctly — there was simply one persistent inefficiency
   that was the only thing in the candidate space clearing the 30 bps
   floor.

## What this tells us about H1/H2/H3 from this window

- **H1** (≥1,000 opportunities/day): 2,420 emissions in 111 min → 31,300/day extrapolated. But the EMISSION count is meaningless — at the unique-key level it's **1**. H1's "opportunity" definition matters here. Jason's call in the report.
- **H2** (Layer 3 flags ≥1%): vacuous — 100% degraded, 0 fires. No conclusion possible from this window.
- **H3** (distributional differences): no flagged group exists — single-group analysis only.

## Strategy-check observations

- The 2,420 records of one arb are below the threshold of variety needed for H3.
- The persistent arb closed during the slow-processing outage at the end of this window. Post-restart records show **0 opportunities** so far. We're now in a different observation regime.
- The lag bug nearly invalidated this data window. The fix (`decisions/D-005`) was a structural lesson, not just a bug fix — future synced DBs MUST have indexes from creation.

## Next focus

Loop completed for this window. NEXT FOCUS (per `../loop/LOOP.md`): wait
for EXP-001 to complete the full 1-day window post-restart, then run a
fresh review with the full 24-hour data.
