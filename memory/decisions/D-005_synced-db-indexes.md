# D-005: Add functional indexes (`LOWER(col)`) to synced L3 DB

**Date**: 2026-05-13
**Made by**: agent (with user approval — "do 1-2 now")
**Status**: ACTIVE

## Context

Live run on Railway was 30 min behind real-time and lag was growing
linearly. Diagnosis: `filter_pipeline.evaluate()` was taking 3,200 ms per
opp (vs 95 ms in local tests). Per-block processing time of ~2.7s vs
Base's 2s block period → falling 27% behind real time.

Probe revealed Layer3Client's WHERE clauses use `LOWER(col) = LOWER(?)`
extensively. The synced L3 DB had no indexes — `sync_l3_db.py` created
tables with only an `id` primary key. Filter rules were doing full table
scans on 299K-row `contracts`, 70K-row `deployers`, etc., 13 times per
opportunity.

Single SQLite query latency was 0.01 ms (DB itself was fast). The 3,200 ms
was entirely full-table-scan cost.

See `failures/FAILURE_LOG.md` 2026-05-13 for the failure entry.

## Options considered

1. **Add plain indexes on raw columns** (`CREATE INDEX ON tbl(col)`)
   Tried first. Filter eval dropped from 3,200 ms to 3,090 ms — **almost
   no improvement**. Reason: SQLite query planner cannot use a plain
   index on `col` when the WHERE clause wraps it: `LOWER(col) = ...`
   compiles to a scan even if `col` is indexed.

2. **Functional indexes on `LOWER(col)`** ← **CHOSEN**
   `CREATE INDEX ON tbl(LOWER(col))` makes SQLite use the expression-keyed
   B-tree for the matching WHERE clause. Dropped filter eval to 239 ms p50.

3. **Rewrite Layer3Client queries to drop `LOWER()` wrappers**
   Possible if we guarantee both sides are lowercase. Synced data already
   appears lowercase. But this is a bigger code change with regression
   risk — preserved the wrappers + added matching functional indexes
   instead.

4. **Move sync to a separate read-replica DB** (architectural)
   Too heavy for the immediate problem; would help WAL contention in
   addition to query speed, but the index fix solves the primary issue.

## Decision

Two-part fix, both landed:

1. **Live fix**: ran `CREATE INDEX IF NOT EXISTS` statements via
   `railway ssh` against the running container's synced DB. Indexes
   persist on the volume.

2. **Permanent fix**: added `_INDEX_SPECS_BY_TABLE` to `sync_l3_db.py`
   and updated `_create_table_from_row` to apply the indexes at schema
   bootstrap. Future deploys (or DB rebuilds) maintain indexes
   automatically.

Index inventory (matched to Layer3Client query patterns):

```
Plain indexes (col = ?, no LOWER wrapper):
  contracts(contract_address)
  contracts(confidence_tier)
  deployers(deployer_address)
  trust_amplification(contract_address)
  bytecode_family_members(contract_address)
  approval_watchlist(drain_detected)

Functional indexes (LOWER(col) = LOWER(?)):
  trap_events(LOWER(trap_contract_address))
  org_wallets(LOWER(address))
  infrastructure_registry(LOWER(address))
  approval_watchlist(LOWER(contract_address))
```

## Rationale

- Recovers a 13× speedup on filter eval, dropping it from "blocking event
  loop" to "fits well within block period."
- Matches Layer3Client's query patterns exactly — no Python code changes
  required.
- Live fix means the running run doesn't lose data.
- Permanent fix means future runs won't hit this.

## Consequences expected

- ✅ Filter eval p50: 239 ms (down from 3,200 ms)
- ✅ Per-block compute fits inside Base's 2s block period
- ✅ Lag stops growing and burns down the existing backlog
- ⚠️ Index creation adds a small overhead to sync cycles (observed: sync went from 3-5s to ~20s, but still within budget)
- ⚠️ Slight increase in synced-DB size on disk (indexes cost storage; <10% overhead)

## Reversal criteria

- Layer3Client query patterns change such that these indexes no longer match (e.g. if a rule starts querying on a new column)
- Index maintenance during sync becomes the bottleneck (sync cycles approach 60s+)
- We replace the synced-DB approach with something else (e.g. read-replica or per-query HTTP) — at that point this decision is superseded, not reversed

## Links

- Failure entry: `../failures/FAILURE_LOG.md` 2026-05-13 "Synced L3 DB had no indexes"
- Resolves: (no UNK directly — diagnosed and fixed in one session)
- Code change: `layer3_trading_exp/scripts/sync_l3_db.py` — `_INDEX_SPECS_BY_TABLE` constant + `_create_table_from_row` apply step
