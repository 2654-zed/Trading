"""Phase 2 sub-phase 2.2 (D-009) — per-chain monitor wrapper.

A ChainMonitor bundles everything chain-specific (WS endpoint, HTTP RPC,
multicall transport, pool subset, sampling cadence, lifecycle) into one
object. The top-level orchestrator in `scripts/detect_dry_run.py` spins up
one ChainMonitor per active chain and runs them concurrently under one
asyncio event loop.

Acceptance criteria the design enforces (per PHASE_2_CROSS_CHAIN_SPEC.md §2.2):
  - Three instances run concurrently without asyncio deadlocks (one event
    loop, each ChainMonitor.run() awaits its own transport without holding
    process-wide locks)
  - WS disconnect on one chain is logged + emits a GapMarker + does NOT
    crash the other two (PoolMonitor.run already isolates failures behind
    its reconnect loop; ChainMonitor.run() wraps the same way)
  - Per-chain lag is bookkept on emitted BlockPoolStates and surfaced
    via the on_block callback signature
  - Sampling cadence is configurable per chain (Base/OP = every block,
    Arb = every 8 blocks)

This module has NO chain-specific hardcoded constants — chain identity
flows through `chain_label` and the transports passed in by the caller.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .pool_monitor import (
    MULTICALL3_ADDRESS,
    BlockPoolStates,
    GapMarker,
    PoolMonitor,
    WSStallError,
)
from .pool_set import PoolInfo, PoolSet


# Default block-sampling cadences per chain. These come from the spec sub-phase
# 2.2 table and are intentionally NOT auto-derived from block time so the
# decision is auditable in source rather than re-derived in numeric noise.
DEFAULT_SAMPLING_BY_CHAIN: dict[str, int] = {
    "base":     1,   # ~2s block time, observe every block
    "optimism": 1,   # ~2s block time, observe every block
    "arbitrum": 8,   # ~250ms block time, sample to ~2s wall cadence
}


BlockCallback = Callable[[BlockPoolStates], Awaitable[None]]
GapCallback = Callable[[GapMarker], Awaitable[None]]


def filter_pool_set_by_chain(pool_set: PoolSet, chain_label: str) -> PoolSet:
    """Return a new PoolSet containing only pools tagged with `chain_label`.

    Phase 2 monitored_pools.json carries per-record `chain` attribution
    (added in sub-phase 2.1). For multi-chain runs the bag-level
    PoolSet.chain is `"all"`; the per-chain subset is selected here.

    The returned set preserves the parent's floors + enumerated_at metadata
    so downstream consumers (logger, daily rollup) see a coherent record.
    """
    pools = tuple(p for p in pool_set.pools if p.chain == chain_label)
    return PoolSet(
        pools=pools,
        chain=chain_label,
        enumerated_at_block=pool_set.enumerated_at_block,
        enumerated_at=pool_set.enumerated_at,
        uniswap_v3_tvl_floor_usd=pool_set.uniswap_v3_tvl_floor_usd,
        aerodrome_tvl_floor_usd=pool_set.aerodrome_tvl_floor_usd,
    )


@dataclass(frozen=True)
class ChainEndpoints:
    """RPC + WS URLs for a single chain. Empty strings disable the chain."""
    chain_label: str
    wss_url: str
    rpc_url: str


class ChainMonitor:
    """Owns one chain's worth of WS+HTTP plumbing and PoolMonitor lifecycle.

    Construction is lazy: __init__ stores the chain context; the WebSocket
    + HTTP web3 instances are opened in `run()` so 3 ChainMonitors can be
    constructed up-front (e.g. for configuration logging) before any
    network I/O.

    `transport_factory` is a callable taking (chain_label, wss_url, rpc_url)
    and returning an object that satisfies the PoolMonitor transport
    Protocol: `subscribe_new_heads()` async iterator + `call_multicall3(data,
    block) -> bytes`. The script supplies an Alchemy-flavored factory; tests
    inject a fake factory.
    """

    def __init__(
        self,
        endpoints: ChainEndpoints,
        pool_set: PoolSet,
        on_block: BlockCallback,
        on_gap: GapCallback,
        *,
        transport_factory: Callable,
        sampling_n: Optional[int] = None,
        rate_limit_rps: float = 20.0,
        ws_reconnect_backoff_seconds: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0),
        max_consecutive_stalls: int = 3,
        stall_threshold_seconds: Optional[float] = None,
    ):
        self.endpoints = endpoints
        self._chain_label = endpoints.chain_label
        # Per-chain pool subset. Even with PoolSet.chain == "all" upstream,
        # each ChainMonitor sees only its own pools so multicall3 calldata
        # is built correctly.
        self._chain_pool_set = filter_pool_set_by_chain(pool_set, self._chain_label)
        self._on_block = on_block
        self._on_gap = on_gap
        self._transport_factory = transport_factory
        self._rate_limit_rps = rate_limit_rps
        # Sub-phase 2.8.3 (RCA): WS-level reconnect backoff used by
        # `run()` when PoolMonitor escalates after K consecutive stalls.
        # Separate from PoolMonitor's subscription-level backoff —
        # closing + reopening the WS is a heavier operation.
        self._ws_backoff = ws_reconnect_backoff_seconds
        if max_consecutive_stalls < 1:
            raise ValueError(
                f"max_consecutive_stalls must be >= 1, got {max_consecutive_stalls}"
            )
        self._max_consecutive_stalls = int(max_consecutive_stalls)
        # Passthrough to PoolMonitor — None means PoolMonitor uses its default.
        self._stall_threshold_seconds = stall_threshold_seconds

        if sampling_n is None:
            sampling_n = DEFAULT_SAMPLING_BY_CHAIN.get(self._chain_label, 1)
        if sampling_n < 1:
            raise ValueError(f"sampling_n must be >= 1, got {sampling_n}")
        self._sampling_n = int(sampling_n)

    @property
    def chain_label(self) -> str:
        return self._chain_label

    @property
    def pool_count(self) -> int:
        return len(self._chain_pool_set)

    @property
    def sampling_n(self) -> int:
        return self._sampling_n

    async def run(self) -> None:
        """Open the per-chain transport and run the PoolMonitor.

        Phase 2 sub-phase 2.8.3 (RCA fix, 2026-05-21): the original
        implementation opened the transport once via `async with` and
        called `await monitor.run()` which never returned. That meant
        the WS connection was opened ONCE and never recycled — when the
        underlying TCP socket went silent (e.g. during the 2026-05-19
        Railway/GCP outage), PoolMonitor's subscription-level retries
        kept trying on the same dead socket and never recovered.

        New behavior: a WS-level retry loop wraps the `async with`.
        PoolMonitor escalates after `max_consecutive_stalls` consecutive
        `WSStallError`s; this layer catches that, exits the
        asynccontextmanager (closing the WS at the TCP layer), sleeps a
        backoff interval, then re-enters the asynccontextmanager
        (opening a fresh WS connection).

        Crashes inside transport setup (e.g. unreachable RPC) raise out
        of this coroutine when the backoff is exhausted. The caller
        (`asyncio.gather` with `return_exceptions=True` recommended)
        decides whether one chain's failure cancels the rest.
        """
        ws_attempt = 0
        while True:
            try:
                async with self._transport_factory(
                    chain_label=self._chain_label,
                    wss_url=self.endpoints.wss_url,
                    rpc_url=self.endpoints.rpc_url,
                ) as transport:
                    monitor = PoolMonitor(
                        transport=transport,
                        pool_set=self._chain_pool_set,
                        on_block=self._on_block,
                        on_gap=self._on_gap,
                        rate_limit_rps=self._rate_limit_rps,
                        chain_label=self._chain_label,
                        block_sampling_n=self._sampling_n,
                        max_consecutive_stalls=self._max_consecutive_stalls,
                        stall_threshold_seconds=self._stall_threshold_seconds,
                    )
                    await monitor.run()
                # If monitor.run() returned normally (StopAsyncIteration on
                # the head stream — common in tests, not production), reset
                # backoff and re-enter cleanly. In production this branch
                # is rarely taken.
                ws_attempt = 0
            except asyncio.CancelledError:
                raise
            except WSStallError as exc:
                # PoolMonitor escalated. Close the WS (by leaving the
                # async-with block) and open a fresh one.
                delay = self._ws_backoff[min(ws_attempt, len(self._ws_backoff) - 1)]
                print(
                    f"[chain_monitor:{self._chain_label}] WS-level reconnect "
                    f"#{ws_attempt + 1} after {type(exc).__name__}: {exc}; "
                    f"sleeping {delay}s before re-opening WS",
                    file=sys.stderr, flush=True,
                )
                ws_attempt += 1
                await asyncio.sleep(delay)
            except Exception as exc:
                # Transport setup or unexpected error inside the WS lifecycle.
                # Same backoff + retry behavior, but a different log prefix
                # so causes are distinguishable at diagnosis time.
                delay = self._ws_backoff[min(ws_attempt, len(self._ws_backoff) - 1)]
                print(
                    f"[chain_monitor:{self._chain_label}] WS-level reconnect "
                    f"#{ws_attempt + 1} after non-stall {type(exc).__name__}: "
                    f"{exc}; sleeping {delay}s",
                    file=sys.stderr, flush=True,
                )
                ws_attempt += 1
                await asyncio.sleep(delay)


__all__ = [
    "BlockCallback",
    "ChainEndpoints",
    "ChainMonitor",
    "DEFAULT_SAMPLING_BY_CHAIN",
    "GapCallback",
    "filter_pool_set_by_chain",
]
