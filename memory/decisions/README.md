# decisions/

Append-only record of operational and strategy decisions. Newest at the
bottom. Each decision lives in its own file:

```
D-001_<short-name>.md
D-002_<short-name>.md
...
```

## When to log a decision

- Any change to active strategy (hypotheses, success criteria, candidate sets)
- Any architectural choice (sync model, deployment topology, schema)
- Any deferred work (Phase N scoped out — explicitly recorded)
- Any operational fix that establishes a pattern (e.g. "always use MSYS_NO_PATHCONV")
- Any reversal of a prior decision

## When NOT to log a decision

- Routine PRs with no strategic implication → commit message is enough
- Tactical bug fixes that don't change behavior → use `failures/FAILURE_LOG.md`
- Implementation details below the architectural level → code comments

## Schema (per `../loop/LOOP.md` Step 5)

```markdown
# D-NNN: <decision name>
**Date**: YYYY-MM-DD
**Made by**: <user / agent name>
**Status**: ACTIVE / REVERSED / SUPERSEDED-BY-DXXX

## Context
What was happening that required a decision.

## Options considered
1. Option A — pros / cons
2. Option B — pros / cons
...

## Decision
Which option, in one sentence.

## Rationale
Why this option won.

## Consequences expected
What we predict happens because of this.

## Reversal criteria
What would make us undo this. **Always include this** — decisions without
reversal criteria become path-locks.

## Links
- PRs / commits
- Failures referenced (`../failures/FAILURE_LOG.md`)
- Unknowns this resolves (`../unknowns/UNKNOWNS.md`)
```

## Current active decisions

| ID | Decision | Date | Status |
|---|---|---|---|
| D-001 | Use DefiLlama instead of factory event scan for Phase 1.1 pool enumeration | 2026-05-06 | ACTIVE |
| D-002 | Deploy as sibling Railway service in `blockchain` project (not shared volume, not API-only) | 2026-05-12 | ACTIVE |
| D-003 | Cut run length from 7 days to 1 day | 2026-05-13 | ACTIVE |
| D-004 | Defer cross-chain (Arb + OP) to Phase 2; do not build in current session | 2026-05-13 | ACTIVE |
| D-005 | Add functional indexes (`LOWER(col)`) to synced L3 DB | 2026-05-13 | ACTIVE |
| D-006 | Phase 2 bridge model = Across Protocol (30s, ~10 bps/hop) | 2026-05-13 | ACTIVE |
| D-007 | H1 INVALIDATED at current Base-only floors; pivot next focus to Phase 2 spec drafting | 2026-05-16 | ACTIVE |
| D-008 | Resolve UNK-002 (MSUSD/USDC did not reopen) and UNK-006 (lag stable post-index-fix) jointly | 2026-05-16 | ACTIVE |
| D-009 | Phase 2 cross-chain spec approved; implementation cleared under sub-phase discipline | 2026-05-16 | ACTIVE |
| D-010 | Across static fee model locked; first verifier run shows live fees ~5-10x lower than D-006 baseline | 2026-05-16 | ACTIVE |
| D-011 | JSONL schema v1→v2 additive migration; resolves UNK-008 (`per_address` retained) | 2026-05-16 | ACTIVE |
| D-012 | Per-table L3 freshness thresholds; resolves UNK-005 (Phase 1 replay: 100% → 0% fully degraded) | 2026-05-16 | ACTIVE |
| D-013 | H1' + H4 analysis parameters locked (margin floor 50 bps, top-20% covers ≥80%, falsification <10/day or <40%) | 2026-05-16 | ACTIVE |
| D-014 | Phase 2 go-live; EXP-002 deployment authorized (7-day run on Base + Arb + OP) | 2026-05-16 | ACTIVE |
| D-015 | Alchemy CU budget for EXP-002 — measured 1.5 req/s aggregate, ~34% utilization. Resolves UNK-003. | 2026-05-17 | ACTIVE |
| D-016 | Two-level WS reconnect: PoolMonitor escalation after K stalls + ChainMonitor recycles WS via async-with re-entry. Architectural fix for FAILURE_LOG 2026-05-21. | 2026-05-21 | ACTIVE |
| D-017 | WebSocket subscription lifecycle: transport tracks `_active_sub_id` and unsubscribes before re-subscribing + on iterator exit. Fixes 2026-05-24 CU spike (newHeads subscription leak, peak ~170 active subs on Base). | 2026-05-25 | ACTIVE |
| D-018 | UNK-002 reversal — MSUSD/USDC arb DID reopen (2 reopenings in EXP-002, open windows 6-8h, inter-burst gaps 4-5 days). D-008's RESOLVED status REVERSED. | 2026-05-25 | ACTIVE |
| D-019 | H2 → SUPPORTED-WITH-CAVEAT (4.01% overall / 84.5% cross-chain hard-flag rate, concentrated on rule_2 + one Arbitrum WETH/USDC Slipstream pool). H3 now testable. | 2026-05-25 | ACTIVE |
| D-020 | Phase 3 multi-lens engine spec approved (`PHASE_3_MULTI_LENS_ENGINE_SPEC.md`); engine ships as separate `engine/` package; bloxroute migration deferred to Phase 4. | 2026-05-25 | ACTIVE |
