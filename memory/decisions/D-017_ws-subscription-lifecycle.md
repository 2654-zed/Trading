# D-017: WebSocket subscription lifecycle — transport owns its subscription_id

**Date**: 2026-05-25
**Made by**: user RCA on 2026-05-24 CU spike (`failures/FAILURE_LOG.md` 2026-05-24); agent files the decision capturing the architectural lesson
**Status**: ACTIVE

## Context

The 2026-05-24 Alchemy CU spike (+500M then +400M over 2 days, root cause
documented in `failures/FAILURE_LOG.md` 2026-05-24) was caused by
`_AlchemyTransport.subscribe_new_heads()` repeatedly opening new
`eth_subscribe("newHeads")` requests on the SAME WebSocket connection
each time PoolMonitor's "subscription-level retry" branch fired,
WITHOUT unsubscribing the previous subscription_id. Alchemy kept
delivering messages to every still-registered subscription on the
shared WS, multiplying the newHeads CU cost by the number of leaked
subscriptions (peak ~170 active on Base, vs intended 1).

The prior sub-phase 2.8.x patches (2.8.2 visibility, 2.8.3 two-level
recovery, 2.8.4 unified-exception escalation) all assumed:
- The expensive resource was the TCP CONNECTION
- Subscription re-establishment was free / stateless
- A new `subscribe_new_heads()` call replaced the previous subscription

The last assumption was wrong. Each `eth_subscribe` is a new
subscription_id; the server retains all of them until told otherwise.

This decision documents the architectural pattern that prevents this
class of bug going forward.

## Options considered

### Option 1: Track subscription_id in transport; unsubscribe before re-subscribe + on iterator exit ← **CHOSEN**

The transport owns its WS state (since `async with self._transport_factory(...)` produced
the `ws_w3` and the transport wraps it). It's the right layer to also
own subscription lifecycle. PoolMonitor doesn't need to know about
subscription_ids at all.

Implementation (landed in commit `a810799`):
- `_AlchemyTransport.__init__` initializes `self._active_sub_id = None`
- `subscribe_new_heads()` calls `eth.unsubscribe(self._active_sub_id)`
  before each new `eth.subscribe("newHeads")` if a prior id exists
- A `try/finally` wraps the message iterator; on any exit path the
  active subscription is unsubscribed
- All unsubscribe calls are best-effort (try/except) because the WS
  may already be dead

Pros:
- Surgical fix at the right layer
- PoolMonitor's reconnect logic doesn't change
- The "transport owns its server-side state" invariant becomes
  consistent with the "transport owns the WS connection" invariant
- Future transports (e.g. a different web3 provider) follow the same
  pattern

Cons:
- Unsubscribe failures are silently logged (could mask other bugs).
  Mitigation: telemetry instrumentation in commit `9ca2565` exposes
  per-method CU rate, so any silent drift becomes visible.

### Option 2: Force PoolMonitor to recycle the WS connection on EVERY retry (skip subscription-level retry entirely)

Pros: simpler — never re-subscribe on a stale WS, always rebuild.

Cons: TCP+TLS+WS reconnect is expensive (~hundreds of ms + Alchemy
side handshake overhead). For transient one-shot failures that DO
recover on the same WS (e.g. a single missed newHeads message), this
burns the cheap retry path. Also doesn't preclude future bugs of the
same SHAPE (re-using a stateful protocol resource without explicit
lifecycle management).

### Option 3: Use a single long-lived subscription and never re-subscribe; rely on WS-level reconnect for ALL recovery

Pros: simplest mental model — one subscription per WS lifetime.

Cons: incompatible with PoolMonitor's existing retry semantics
(subscription-level retry exists specifically to handle transient
in-subscription failures without TCP teardown). Requires deeper
refactor; not warranted given option 1 solves the bug surgically.

## Decision

**Adopt option 1: transport-owned subscription lifecycle.**

The principle going forward:

> **Any code that opens a stateful server-side handle (subscription,
> session, cursor, etc.) on a WebSocket OR HTTP connection MUST track
> the handle's identity AND tear it down explicitly before opening a
> new one of the same kind on the same connection. The teardown MUST
> be best-effort (the connection may be dead) but MUST happen on every
> exit path (success, exception, cancellation, generator close).**

Specific to our codebase:
- `_AlchemyTransport` owns `_active_sub_id` and manages its lifecycle
  inside `subscribe_new_heads()`
- Any future transport implementing the PoolMonitor transport protocol
  MUST follow the same pattern for any subscription kind it opens

## Rationale

- The actual fix is small (~30 lines including comments + the
  try/finally) and lives at the layer that already owns related state.
- The principle generalizes: it's not just `eth_subscribe`. ANY
  stateful server-side handle has this risk. The decision codifies a
  pattern, not just a single-bug fix.
- The telemetry commit (`9ca2565`) provides ongoing protection: even
  if a future bug introduces another leak, per-method CU instrumentation
  catches it before it becomes a 900M-CU surprise on the bill.

## Consequences expected

- newHeads CU rate drops from peak ~445M/day on Base back to the
  expected ~2.6M/day (single active subscription per chain). Per-chain
  rates for Arb/OP similarly fall back to baseline.
- Total Alchemy account CU rate returns to the pre-2026-05-22 trend
  (~30M/day average, mostly driven by stellar-embrace's surveillance
  pipeline — which we don't touch per I-3).
- PoolMonitor's subscription-level retry branch keeps the same behavior
  externally — but now the resource accounting underneath it is sane.
- D-015's "~34% utilization" projection becomes valid again at the
  application layer (we're now actually using what the projection said
  we'd use).
- Future failures of this shape (silent resource leak across application
  retries) become detectable via the new telemetry instrumentation.

## Reversal criteria

- **The fix doesn't actually reduce newHeads CU rate after redeploy**.
  Test: post-deploy, check Alchemy's "WebSocket usage by Network"
  panel for Base. Should be ≤ ~3M CU/day per chain. If observed > 10M
  CU/day per chain, the fix isn't working as designed and we need to
  investigate further (could be `eth.unsubscribe` not actually reaping
  on Alchemy's side, or another leak source).
- **A different subscription kind leaks**. We currently only use
  `newHeads`. If we add `eth_subscribe` calls for other kinds (logs,
  pendingTransactions, etc.), each new kind needs its own tracked
  active-id and lifecycle. The principle scales but the implementation
  has to be applied per kind.
- **The transport protocol changes** such that subscription ownership
  moves out of `_AlchemyTransport` (e.g. into web3.py natively or via
  a connection-pool abstraction). At that point, this decision's
  prescription gets adapted to the new ownership boundary, not
  reversed.

## Consequences for the memory system

- `decisions/README.md` active table — add D-017
- `failures/FAILURE_LOG.md` 2026-05-24 entry → status updated to "ROOT
  CAUSE IDENTIFIED + CODE FIX LANDED 2026-05-25" (already done)
- `D-015_alchemy-cu-budget-phase-2.md` — REVERSAL TRIGGERED state stays
  until post-deploy verification confirms the CU rate drops. Then
  flips back to ACTIVE.
- `SYSTEM_STATE.md` — deployment status stays HALTED; redeploy
  authorization is the next user gate
- `INVARIANTS.md` — no immediate change, but worth considering whether
  to add a new invariant codifying the "stateful server-side resource
  lifecycle" principle. Deferred until we've validated the fix lands.

## Links

- Failure entry that drove this: `../failures/FAILURE_LOG.md` 2026-05-24
- Related earlier decision: `D-016_two-level-ws-reconnect.md` (covered
  TCP-level recycle; this decision covers subscription-level lifecycle)
- Budget decision now in REVERSAL TRIGGERED state: `D-015_alchemy-cu-budget-phase-2.md`
- Code:
  - `../../layer3_trading_exp/scripts/detect_dry_run.py` — `_AlchemyTransport`
    now tracks `_active_sub_id`; commit `a810799` is the fix
  - `../../...` — RPC telemetry instrumentation, commit `9ca2565`
