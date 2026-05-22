"""Unit tests for chain_monitor.py (Phase 2 sub-phase 2.2, D-009).

Focus:
  - filter_pool_set_by_chain selects only matching-chain pools
  - ChainMonitor exposes per-chain stats correctly
  - ChainMonitor.run() routes through a custom transport_factory with
    the right kwargs
  - Default sampling cadences match the spec table
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from layer3_trading_exp.chain_monitor import (
    DEFAULT_SAMPLING_BY_CHAIN,
    ChainEndpoints,
    ChainMonitor,
    filter_pool_set_by_chain,
)
from layer3_trading_exp.pool_set import (
    PoolInfo,
    PoolProtocol,
    PoolSet,
    TokenInfo,
)


def _token(addr: str, sym: str = "TKN", dec: int = 18) -> TokenInfo:
    return TokenInfo(address=addr.lower(), symbol=sym, decimals=dec)


def _pool(addr: str, chain: str, protocol: PoolProtocol = PoolProtocol.UNISWAP_V3) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(), protocol=protocol,
        token0=_token("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        token1=_token("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "USDC", 6),
        fee_bps=5, tvl_usd_at_enumeration=1e6, enumerated_at_block=1,
        chain=chain,
    )


def _multi_chain_set() -> PoolSet:
    return PoolSet(
        pools=(
            _pool("0x1111111111111111111111111111111111111111", "base"),
            _pool("0x2222222222222222222222222222222222222222", "base"),
            _pool("0x3333333333333333333333333333333333333333", "arbitrum",
                  protocol=PoolProtocol.CAMELOT_V3),
            _pool("0x4444444444444444444444444444444444444444", "optimism",
                  protocol=PoolProtocol.VELODROME_SLIPSTREAM),
            _pool("0x5555555555555555555555555555555555555555", "optimism",
                  protocol=PoolProtocol.VELODROME_VOLATILE),
        ),
        chain="all",
        enumerated_at_block=1, enumerated_at="t",
        uniswap_v3_tvl_floor_usd=500_000.0, aerodrome_tvl_floor_usd=250_000.0,
    )


def test_filter_pool_set_by_chain_returns_only_matching_pools():
    full = _multi_chain_set()
    base = filter_pool_set_by_chain(full, "base")
    arb = filter_pool_set_by_chain(full, "arbitrum")
    op = filter_pool_set_by_chain(full, "optimism")

    assert len(base) == 2
    assert len(arb) == 1
    assert len(op) == 2
    assert all(p.chain == "base" for p in base.pools)
    assert all(p.chain == "arbitrum" for p in arb.pools)
    assert all(p.chain == "optimism" for p in op.pools)


def test_filter_pool_set_by_chain_preserves_metadata():
    full = _multi_chain_set()
    arb = filter_pool_set_by_chain(full, "arbitrum")
    assert arb.chain == "arbitrum"
    assert arb.enumerated_at_block == full.enumerated_at_block
    assert arb.enumerated_at == full.enumerated_at
    assert arb.uniswap_v3_tvl_floor_usd == full.uniswap_v3_tvl_floor_usd
    assert arb.aerodrome_tvl_floor_usd == full.aerodrome_tvl_floor_usd


def test_filter_pool_set_by_chain_returns_empty_for_unknown_chain():
    """Empty per-chain subset is valid (graceful degradation). PoolSet is
    immutable so this returns a new empty set without raising."""
    full = _multi_chain_set()
    nothing = filter_pool_set_by_chain(full, "ethereum")
    assert len(nothing) == 0
    assert nothing.chain == "ethereum"


def test_default_sampling_matches_spec_table():
    """Sub-phase 2.2 table — Base/OP every block, Arb every 8 blocks."""
    assert DEFAULT_SAMPLING_BY_CHAIN["base"] == 1
    assert DEFAULT_SAMPLING_BY_CHAIN["optimism"] == 1
    assert DEFAULT_SAMPLING_BY_CHAIN["arbitrum"] == 8


def test_chain_monitor_exposes_chain_label_and_pool_count():
    full = _multi_chain_set()
    cm = ChainMonitor(
        endpoints=ChainEndpoints(chain_label="optimism",
                                 wss_url="wss://stub", rpc_url="https://stub"),
        pool_set=full,
        on_block=_async_noop,
        on_gap=_async_noop,
        transport_factory=_unused_factory,
    )
    assert cm.chain_label == "optimism"
    assert cm.pool_count == 2  # filtered from the multi-chain set
    assert cm.sampling_n == 1  # OP default


def test_chain_monitor_explicit_sampling_overrides_default():
    full = _multi_chain_set()
    cm = ChainMonitor(
        endpoints=ChainEndpoints(chain_label="arbitrum",
                                 wss_url="wss://x", rpc_url="https://x"),
        pool_set=full,
        on_block=_async_noop,
        on_gap=_async_noop,
        transport_factory=_unused_factory,
        sampling_n=16,
    )
    assert cm.sampling_n == 16


def test_chain_monitor_rejects_invalid_sampling():
    full = _multi_chain_set()
    with pytest.raises(ValueError, match=">= 1"):
        ChainMonitor(
            endpoints=ChainEndpoints(chain_label="base",
                                     wss_url="wss://x", rpc_url="https://x"),
            pool_set=full, on_block=_async_noop, on_gap=_async_noop,
            transport_factory=_unused_factory,
            sampling_n=0,
        )


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.8.3 (2026-05-21, RCA fix): ChainMonitor exits + re-enters
# the asynccontextmanager when PoolMonitor escalates a WSStallError. This is
# the architectural fix for the 2026-05-21 silent-stall failure.
# ----------------------------------------------------------------------------


def test_chain_monitor_recycles_ws_on_pool_monitor_escalation():
    """When PoolMonitor escalates WSStallError, ChainMonitor must close and
    re-open the WS via the asynccontextmanager."""
    from layer3_trading_exp.pool_monitor import WSStallError

    full = _multi_chain_set()
    enter_count = {"n": 0}
    exit_count = {"n": 0}

    @asynccontextmanager
    async def recycling_factory(*, chain_label, wss_url, rpc_url):
        enter_count["n"] += 1
        n = enter_count["n"]
        try:
            yield _EscalatingTransport(escalation_after_attempts=1)
        finally:
            exit_count["n"] += 1
        # On the SECOND entry, raise CancelledError to break the outer loop.
        # (We can't easily express that from here; instead we use a counter
        # and the driver's wait_for timeout to bound the test.)

    cm = ChainMonitor(
        endpoints=ChainEndpoints(chain_label="base",
                                 wss_url="wss://test", rpc_url="https://test"),
        pool_set=full, on_block=_async_noop, on_gap=_async_noop,
        transport_factory=recycling_factory,
        ws_reconnect_backoff_seconds=(0.01,),
        max_consecutive_stalls=1,
        stall_threshold_seconds=0.05,   # tight so the test runs fast
    )

    async def driver():
        try:
            await asyncio.wait_for(cm.run(), timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(driver())

    # The factory should have been entered MULTIPLE times (WS-level reconnect
    # fired at least once). Original buggy code entered exactly once.
    assert enter_count["n"] >= 2, \
        f"expected ≥2 WS opens (recycle behavior); got {enter_count['n']}"
    # Every enter is matched by an exit when the WSStallError propagates.
    assert exit_count["n"] >= 1, \
        f"expected ≥1 WS close; got {exit_count['n']}"


def test_chain_monitor_rejects_invalid_max_consecutive_stalls():
    full = _multi_chain_set()
    with pytest.raises(ValueError, match="max_consecutive_stalls"):
        ChainMonitor(
            endpoints=ChainEndpoints(chain_label="base",
                                     wss_url="wss://x", rpc_url="https://x"),
            pool_set=full, on_block=_async_noop, on_gap=_async_noop,
            transport_factory=_unused_factory,
            max_consecutive_stalls=0,
        )


def test_chain_monitor_run_invokes_transport_factory_with_chain_context():
    """Verify the transport_factory contract: gets called with chain_label,
    wss_url, rpc_url kwargs from the endpoints. Cancellation inside
    `monitor.run()` propagates cleanly back through the async context."""
    full = _multi_chain_set()
    captured: dict = {}

    @asynccontextmanager
    async def fake_factory(*, chain_label: str, wss_url: str, rpc_url: str):
        captured["chain_label"] = chain_label
        captured["wss_url"] = wss_url
        captured["rpc_url"] = rpc_url
        yield _CancellingTransport()

    cm = ChainMonitor(
        endpoints=ChainEndpoints(chain_label="base",
                                 wss_url="wss://test.example",
                                 rpc_url="https://test.example"),
        pool_set=full, on_block=_async_noop, on_gap=_async_noop,
        transport_factory=fake_factory,
    )

    async def driver():
        try:
            await asyncio.wait_for(cm.run(), timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(driver())

    assert captured["chain_label"] == "base"
    assert captured["wss_url"] == "wss://test.example"
    assert captured["rpc_url"] == "https://test.example"


# --- helpers -----------------------------------------------------------------

async def _async_noop(_arg):
    return None


def _unused_factory(**kwargs):
    raise AssertionError("transport_factory should not have been called in this test")


class _CancellingTransport:
    """A transport whose subscribe_new_heads raises CancelledError immediately,
    so PoolMonitor.run() exits cleanly without external timeout."""
    async def subscribe_new_heads(self):
        raise asyncio.CancelledError("test-driven cancellation")
        yield  # unreachable; keeps signature an async generator
    async def call_multicall3(self, data, block):
        raise AssertionError("multicall should not be called when subscription cancels first")


class _EscalatingTransport:
    """A transport that hangs on subscribe_new_heads, forcing PoolMonitor's
    stall detector to fire. Combined with max_consecutive_stalls=1 in the
    PoolMonitor under test, this triggers an escalation on the very first
    silence, which ChainMonitor must catch + recycle the WS via the factory."""
    def __init__(self, escalation_after_attempts: int = 1):
        self._n = 0
        self._escalation_after_attempts = escalation_after_attempts

    async def subscribe_new_heads(self):
        self._n += 1
        # Hang silently — the WS-stall detector with a short threshold (set
        # via PoolMonitor.stall_threshold_seconds in the test) will fire.
        await asyncio.sleep(60)
        if False:
            yield {}

    async def call_multicall3(self, data, block):
        raise AssertionError("multicall should not be reached during silence")
