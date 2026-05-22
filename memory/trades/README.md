# trades/

Per-run opportunity review snapshots. Naming convention:

```
YYYY-MM-DD_review.md           # Daily run review
YYYY-MM-DD_<event>.md          # Ad-hoc snapshot (e.g. 2026-05-13_index-fix.md)
```

This directory is named `trades/` for symmetry with conventional algo-trading
memory systems. **This codebase does NOT execute trades** (see
`../INVARIANTS.md` I-1). The reviews here are reviews of detected
arbitrage *opportunities* — hypothetical trades the system would have made
if it were in execution mode.

## What each review must contain

Per `../loop/LOOP.md` Step 1:

- **Window**: start/end timestamps + block numbers
- **Record count**: total JSONL records
- **Unique opportunity keys**: (pool_pair × borrowed_token) distinct count
- **Margin distribution**: min / p50 / p95 / max
- **Net gain USD**: min / p50 / max + distinct rounded count
- **Flag distribution**: hard / soft / unflagged / degraded
- **Protocol combos**: which protocol pairs dominated (top 5)
- **Notable observations**: anything that surprised the reviewer

## Seed entry

See `2026-05-13_index-fix-mid-run.md` for the format applied to the pre-fix
window of the current run.
