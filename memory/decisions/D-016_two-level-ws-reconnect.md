# D-016: Two-level WS reconnect — PoolMonitor escalation + ChainMonitor WS recycling

**Date**: 2026-05-21
**Made by**: agent (architectural RCA after FAILURE_LOG 2026-05-21)
**Status**: ACTIVE

## Context

Two production stall events in EXP-002 (2026-05-18, 2026-05-21) both
involved Alchemy WebSocket subscriptions going silent without raising
any exception. The 2026-05-18 incident introduced a stall detector
(`WSStallError`) in PoolMonitor that successfully detected silence,
but the recovery path was wrong: the outer `except` clause caught the
error, slept a backoff interval, then re-called `subscribe_new_heads()`
on the SAME underlying WS connection. When the WS TCP socket itself is
dead (e.g. after a Railway/GCP outage broke the connection without
delivering a FIN/RST), re-subscribing on it doesn't help — it just hits
silence again, raises `WSStallError` again, sleeps again, forever.

The 2026-05-21 incident exposed this: chain monitors silently looped in
reconnect-backoff with no log output for ~3.5 days. The 2026-05-21
visibility patch (sub-phase 2.8.2) made the reconnect attempts visible
in stderr but did NOT fix the underlying inability to recover.

This decision documents the architectural fix.

## Options considered

1. **Two-level recovery: PoolMonitor escalates, ChainMonitor recycles WS** ← **CHOSEN**
   PoolMonitor handles subscription-level retries (still useful for one-shot
   recoverable hiccups). After K consecutive stalls with no successful tick,
   PoolMonitor raises `WSStallError` out of `run()`. ChainMonitor wraps the
   `async with self._transport_factory(...)` in a retry loop that catches
   the escalation, closes the WS via context-manager exit, sleeps a backoff,
   then re-enters the context-manager (fresh TCP connection).

2. **Always recycle the WS on every stall (skip PoolMonitor subscription retries)**
   Simpler. But subscription-level transients (e.g. Alchemy returns a
   "subscription confirmed" but then loses the first head momentarily) DO
   recover on the same connection. Always recycling burns connection
   overhead for transient hiccups.

3. **Add a separate "WS liveness" coroutine that pings the WS independently**
   Complex. Requires reaching into the web3.py WebSocket internals to send
   a ping. We don't have direct control over WebSocketProvider's internals.

4. **Use Railway's `restartPolicyType: ON_FAILURE` — exit the container on stall**
   Brutal. Loses in-process state (logger queue, sync state, watchdog state).
   Restart adds ~30-60s of container boot time per failure event. Doesn't
   isolate failures to one chain — kills all 3 monitors when only one stalled.

## Decision

**Adopt option 1: two-level recovery.** Code lands in sub-phase 2.8.3
(`pool_monitor.py` + `chain_monitor.py`).

Mechanism:

| Layer | Responsibility | Trigger | Action |
|---|---|---|---|
| `PoolMonitor` (subscription level) | One-shot transient recovery | Single `WSStallError` | Sleep backoff (1-30s); re-subscribe on SAME WS |
| `PoolMonitor` (escalation) | Detect dead WS | N consecutive `WSStallError`s with no successful tick | Raise out of `run()` so `ChainMonitor` can recycle |
| `ChainMonitor` (WS level) | Recycle WS connection | `WSStallError` escalation OR transport-setup failure | Close + reopen via `async with self._transport_factory(...)`; sleep backoff (1-30s) |

Defaults:
- `max_consecutive_stalls = 3` on PoolMonitor
- WS-level backoff: `(1, 2, 5, 10, 30)` seconds
- Stall counter resets on every successful head arrival

Both layers log every transition to stderr — no more invisible
reconnect cascades.

## Rationale

- The 2026-05-21 RCA explicitly identified that re-subscribing on the
  same dead socket cannot recover. The fix MUST recycle the connection.
- Two-level recovery keeps the cheap "subscription-only retry" path for
  transient hiccups while ensuring genuine TCP-level failures are
  recovered without operator intervention.
- The escalation count (3) is small enough that legitimate stalls
  recover quickly (within ~3 × stall_threshold = ~60s) but large enough
  that one-shot transients don't burn through fresh TCP connections.
- Logging at every transition removes the "silent failure" class entirely.
- The fix is minimal: ~50 lines of code change across two files. Test
  suite remains at 309/309.

## Consequences expected

- Future WS silence events: PoolMonitor logs subscription-level retries
  (visible), then escalates after 3, then ChainMonitor logs the WS-level
  reconnect and opens a fresh TCP. If Alchemy is reachable, recovery is
  within ~60s of the first silence.
- If Alchemy is genuinely down, ChainMonitor's outer retry will keep
  cycling every 30s indefinitely (no give-up). That's correct behavior
  for a long-running detection daemon.
- `restartPolicyType: ON_FAILURE` becomes a fallback for unhandled
  exceptions, not the primary recovery path for WS issues.
- Each WS-level reconnect resets the `WSStallError` counter but does
  NOT reset the time-budget watchdog (which is process-level).

## Reversal criteria

- A future failure mode where ChainMonitor's WS-level retry doesn't
  recover (e.g. the asynccontextmanager's `__aexit__` itself hangs)
  → escalate to option 4 (container restart on stall) as a defensive
  layer above this fix.
- Empirical evidence that 3-stall escalation is wrong (too eager
  burning fresh TCP, OR too lazy missing fast recoveries) → tune
  `max_consecutive_stalls` per chain via the existing kwarg.
- A redesign of `web3.py`'s WebSocketProvider that exposes WS-level
  reconnect natively → migrate to that and remove the ChainMonitor
  retry loop.

## Consequences for the memory system

- `decisions/README.md` active table — add D-016
- `failures/FAILURE_LOG.md` 2026-05-21 entry → status RESOLVED (already done)
- `STRATEGY_STATE.md` — no change
- `INVARIANTS.md` — no change
- Next deployment of EXP-002 (whenever the operator decides to redeploy)
  carries this architectural fix; expected behavior shifts from "silent
  stall after first WS death" to "automatic recovery within ~60s"

## Links

- Failure that drove this: `../failures/FAILURE_LOG.md` 2026-05-21 entry
- Earlier subscription-level fix: `../failures/FAILURE_LOG.md` 2026-05-18 entry
- Code: `../../layer3_trading_exp/pool_monitor.py` (escalation),
  `../../layer3_trading_exp/chain_monitor.py` (WS recycling)
- Tests: `../../layer3_trading_exp/tests/test_pool_monitor.py`,
  `../../layer3_trading_exp/tests/test_chain_monitor.py`
