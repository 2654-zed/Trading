# D-018: UNK-002 RESOLVED → REVERSED + REOPENED — MSUSD/USDC arb did reopen

**Date**: 2026-05-25
**Made by**: agent (per `trades/2026-05-25_exp-002-summary.md` LOOP Step 4 + Step 5)
**Status**: ACTIVE

## Context

D-008 (`decisions/D-008_resolve-unk-002-and-006.md`, 2026-05-16) closed
UNK-002 with the resolution: "the MSUSD/USDC arb did NOT reopen in
~2.5 days of post-closure observation." D-008 explicitly stated a
reversal criterion #1: "if a MSUSD/USDC arb (or similarly persistent
single-pair arb) is detected on Base in any subsequent run, re-open
UNK-002 and document the open-window distribution."

EXP-002 produced two MSUSD/USDC reopenings, both on the exact same
pool pair from D-008's documentation:

```
pool_x : 0x7501bc8bb51616f79bfa524e464fb7b41f0b10fb  (Aerodrome Slipstream MSUSD/USDC CL50)
pool_y : 0xcefc8b799a8ee5d9b312aeca73262645d664aaf7  (Aerodrome stable MSUSD/USDC)
borrow : 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913  (USDC on Base)
```

Reversal criterion firmly fired.

## Options considered

1. **Reverse D-008's UNK-002 resolution and re-open UNK-002 with new evidence** ← **CHOSEN**

2. Treat the reopenings as a fundamentally different opportunity (different epoch, different pool state) and leave UNK-002 RESOLVED

   Rejected: same exact pool addresses, same protocol pair, same emission
   pattern (~1 record per Base block when open, pinned at 30-36 bps).
   This is the same arb, not a different one.

## Decision

**Reverse D-008's UNK-002 resolution. UNK-002 is back to OPEN with
augmented evidence + revised resolution path.**

Observed reopenings:

| Date | Open window | Emissions | Margin |
|---|---|---|---|
| **2026-05-17 16:40-22:59 UTC** (EXP-002 Run 1 first 1.85h pinned at floor + then closes around the same time as Run 1's WS stall) | ~6.3h | ~12,729 | 30-36 bps (p50=32) |
| **2026-05-23 07:05-14:56 UTC** (EXP-002 Run 4 after the GitHub-auto-deploy redeploy + 2.8.4 patch) | **7.9h** | 12,153 | 30-36 bps (p50=32) |

Both reopenings produced the same emission signature as EXP-001's first
1.85h (D-008's original observation). Total emission count across the
two reopenings: ~24,882. Same single unique (pool_pair, borrow_token)
key.

Gap between EXP-001 close (~2026-05-13 06:19 UTC) and EXP-002 Run 1
reopen (2026-05-17 16:40 UTC) = **4.4 days closed**.

Gap between Run 1 close (2026-05-17 22:59 UTC) and Run 4 reopen
(2026-05-23 07:05 UTC) = **5.4 days closed**.

This is NOT a continuously-open arb. It's a **bursty / intermittent**
arb: open for 6-8 hours at a time, closed for 4-5+ days between bursts.
D-008's "did not reopen in 2.5 days" finding was within the closed
phase of this cycle — too short a window to capture the actual cadence.

## Rationale

- D-008 EXPLICITLY documented the reversal criterion. The criterion has
  fired. Decision discipline says reverse + document.
- The observed cadence (open ~7h, closed ~5 days) is research-relevant:
  it means EXP-002's intra-chain MSUSD/USDC emission counts are NOT a
  steady-state measurement of arb density on Base. They're snapshots
  of bursty cycles.
- The CORRECT framing for UNK-002 going forward isn't "does it reopen?"
  (we now know yes) but "what is the open-window cadence and what
  triggers reopens?"

## Consequences expected

- UNK-002 status → REVERTED-TO-OPEN with the revised question above
- D-008 status → REVERSED (UNK-002 resolution invalidated; UNK-006
  resolution UNCHANGED — D-008's lag-stability finding is independent
  and still holds)
- Future Base-only runs at the current $500K/$250K floors will continue
  to capture this arb during its open windows. The presence of the arb
  doesn't change H1's INVALIDATED status (D-007) — even 12K emissions of
  1 unique key per day doesn't beat the H1 1000/day threshold for
  UNIQUE opps.
- The MSUSD/USDC arb is now a useful CALIBRATION pattern for verifying
  detector correctness — when it's open we should see the signature;
  when it's closed we shouldn't.

## Reversal criteria

- **MSUSD/USDC arb stops appearing**: if subsequent runs across a
  multi-week window observe zero reopenings, the bursty-cycle model
  itself needs revising (maybe the arb is now permanently closed due
  to a pool state change or token migration). Action: re-resolve
  UNK-002 with new evidence.
- **The arb's open-window pattern changes** (e.g. now stays open
  continuously for >24h, or stays closed for >30 days): same — the
  bursty-cycle model isn't quite right and UNK-002 gets re-formulated.

## Consequences for the memory system

- `unknowns/UNKNOWNS.md` UNK-002 → status updated to OPEN with revised
  question + the observed open-window cadence data
- `decisions/D-008_resolve-unk-002-and-006.md` → status REVERSED (with
  pointer to D-018 for the new resolution path)
- `decisions/README.md` active table — add D-018
- `STRATEGY_STATE.md` — H1 row unchanged (INVALIDATED); add a footnote
  acknowledging the bursty MSUSD/USDC pattern doesn't lift H1
- `trades/2026-05-25_exp-002-summary.md` — references this decision

## Links

- Driving LOOP write-up: `../trades/2026-05-25_exp-002-summary.md`
- Reversed decision: `D-008_resolve-unk-002-and-006.md`
- UNK record: `../unknowns/UNKNOWNS.md` UNK-002
