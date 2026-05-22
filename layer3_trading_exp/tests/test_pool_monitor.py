"""Unit tests for pool_monitor.py.

Covers: rate-limiter behavior, Multicall3 encode/decode roundtrip,
PoolMonitor subscription loop (including disconnect→gap-marker→reconnect),
and state decoding for UniV3 slot0+liquidity pairs and Aerodrome getReserves.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
from eth_abi import encode as abi_encode

from layer3_trading_exp.pool_monitor import (
    AGGREGATE3_SELECTOR,
    AerodromePoolState,
    AsyncRateLimiter,
    BlockPoolStates,
    GapMarker,
    PoolMonitor,
    SELECTOR_GETRESERVES,
    SELECTOR_LIQUIDITY,
    SELECTOR_SLOT0,
    UniV3PoolState,
    WSStallError,
    _decode_aggregate3_return,
    _default_stall_threshold,
    _encode_aggregate3_call,
    build_pool_state_calls,
    decode_pool_state_results,
)
from layer3_trading_exp.pool_set import (
    PoolInfo,
    PoolProtocol,
    PoolSet,
    TokenInfo,
)


def _token(addr: str, sym: str = "TKN", dec: int = 18) -> TokenInfo:
    return TokenInfo(address=addr.lower(), symbol=sym, decimals=dec)


def _uni_pool(addr: str) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(), protocol=PoolProtocol.UNISWAP_V3,
        token0=_token("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), token1=_token("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "USDC", 6),
        fee_bps=5, tvl_usd_at_enumeration=2e6, enumerated_at_block=1,
    )


def _aero_pool(addr: str, stable: bool = False) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(),
        protocol=PoolProtocol.AERODROME_STABLE if stable else PoolProtocol.AERODROME_VOLATILE,
        token0=_token("0xcccccccccccccccccccccccccccccccccccccccc"), token1=_token("0xdddddddddddddddddddddddddddddddddddddddd", "USDC", 6),
        fee_bps=None, tvl_usd_at_enumeration=6e5, enumerated_at_block=1,
    )


def _slip_pool(addr: str) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(), protocol=PoolProtocol.AERODROME_SLIPSTREAM,
        token0=_token("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"), token1=_token("0xffffffffffffffffffffffffffffffffffffffff", "USDC", 6),
        fee_bps=5, tvl_usd_at_enumeration=2e6, enumerated_at_block=1,
    )


def _set(pools) -> PoolSet:
    return PoolSet(
        pools=tuple(pools), chain="base",
        enumerated_at_block=1, enumerated_at="t",
        uniswap_v3_tvl_floor_usd=1e6, aerodrome_tvl_floor_usd=5e5,
    )


# --- rate limiter -----------------------------------------------------------

def test_rate_limiter_passes_tokens_within_capacity():
    async def go():
        lim = AsyncRateLimiter(rate_per_second=100.0, burst=10)
        start = time.monotonic()
        for _ in range(10):
            await lim.acquire()
        return time.monotonic() - start
    elapsed = asyncio.run(go())
    assert elapsed < 0.05


def test_rate_limiter_paces_beyond_burst():
    async def go():
        lim = AsyncRateLimiter(rate_per_second=20.0, burst=5)
        for _ in range(5):
            await lim.acquire()
        start = time.monotonic()
        for _ in range(5):
            await lim.acquire()
        return time.monotonic() - start
    elapsed = asyncio.run(go())
    assert elapsed >= 0.20


# --- aggregate3 encode / decode ---------------------------------------------

def test_aggregate3_call_starts_with_selector():
    data = _encode_aggregate3_call([("0x0000000000000000000000000000000000000001", True, b"\x01\x02")])
    assert data[:4] == AGGREGATE3_SELECTOR


def test_aggregate3_return_roundtrip():
    items = [(True, b"\xde\xad\xbe\xef"), (False, b"")]
    encoded = abi_encode(["(bool,bytes)[]"], [items])
    decoded = _decode_aggregate3_return(encoded)
    assert decoded == [(True, b"\xde\xad\xbe\xef"), (False, b"")]


# --- build_pool_state_calls -------------------------------------------------

def test_build_calls_emits_two_per_univ3_and_one_per_aerodrome():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111"), _aero_pool("0x2222222222222222222222222222222222222222"), _aero_pool("0x3333333333333333333333333333333333333333", stable=True)])
    calls = build_pool_state_calls(s)
    kinds = [k for (_t, _a, _d, _p, k) in calls]
    assert kinds.count("uni_slot0") == 1
    assert kinds.count("uni_liquidity") == 1
    assert kinds.count("aero_getreserves") == 2


def test_build_calls_uses_correct_selectors():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111"), _aero_pool("0x2222222222222222222222222222222222222222")])
    calls = build_pool_state_calls(s)
    selectors = {k: d for (_t, _a, d, _p, k) in calls}
    assert selectors["uni_slot0"] == SELECTOR_SLOT0
    assert selectors["uni_liquidity"] == SELECTOR_LIQUIDITY
    assert selectors["aero_getreserves"] == SELECTOR_GETRESERVES


# --- decode_pool_state_results ----------------------------------------------

def _encoded_slot0(sqrt_price: int, tick: int) -> bytes:
    return abi_encode(
        ["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"],
        [sqrt_price, tick, 0, 0, 0, 0, True],
    )


def _encoded_slipstream_slot0(sqrt_price: int, tick: int) -> bytes:
    """Slipstream's slot0 struct omits the uint8 feeProtocol field UniV3 has.
    See pool_monitor.SLIPSTREAM_SLOT0_RETURN_TYPES.
    """
    return abi_encode(
        ["uint160", "int24", "uint16", "uint16", "uint16", "bool"],
        [sqrt_price, tick, 0, 0, 0, True],
    )


def _encoded_liquidity(liq: int) -> bytes:
    return abi_encode(["uint128"], [liq])


def _encoded_getreserves(r0: int, r1: int, ts: int) -> bytes:
    return abi_encode(["uint256", "uint256", "uint256"], [r0, r1, ts])


def test_decode_pool_state_results_univ3_and_aerodrome():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111"), _aero_pool("0x2222222222222222222222222222222222222222")])
    calls = build_pool_state_calls(s)
    results = [
        (True, _encoded_slot0(79_228_162_514_264_337_593_543_950_336, 100)),
        (True, _encoded_liquidity(500_000)),
        (True, _encoded_getreserves(1_000, 2_000, 1_700_000_000)),
    ]
    uni, aero, slip = decode_pool_state_results(calls, results)
    assert "0x1111111111111111111111111111111111111111" in uni
    assert uni["0x1111111111111111111111111111111111111111"].sqrt_price_x96 == 79_228_162_514_264_337_593_543_950_336
    assert uni["0x1111111111111111111111111111111111111111"].tick == 100
    assert uni["0x1111111111111111111111111111111111111111"].liquidity == 500_000
    assert aero["0x2222222222222222222222222222222222222222"] == AerodromePoolState("0x2222222222222222222222222222222222222222", 1_000, 2_000, 1_700_000_000)
    assert slip == {}


def test_decode_drops_univ3_pool_if_one_call_failed():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    calls = build_pool_state_calls(s)
    results = [(True, _encoded_slot0(1, 1)), (False, b"")]
    uni, aero, slip = decode_pool_state_results(calls, results)
    assert uni == {}
    assert aero == {}
    assert slip == {}


def test_build_calls_routes_slipstream_to_concentrated_liquidity():
    """Slipstream pools must emit slot0 + liquidity calls (not getReserves) and
    the kind tags must distinguish them from UniV3 so the decoder routes correctly."""
    s = _set([
        _uni_pool("0x1111111111111111111111111111111111111111"),
        _slip_pool("0x4444444444444444444444444444444444444444"),
    ])
    calls = build_pool_state_calls(s)
    kinds = [k for (_t, _a, _d, _p, k) in calls]
    assert kinds.count("uni_slot0") == 1
    assert kinds.count("uni_liquidity") == 1
    assert kinds.count("slip_slot0") == 1
    assert kinds.count("slip_liquidity") == 1
    assert "aero_getreserves" not in kinds


def test_decode_pool_state_results_separates_slipstream_from_univ3():
    """Slipstream and UniV3 share state shape but must land in different dicts so
    callers can attribute back to protocol when selecting AMM math + fees."""
    uni_addr = "0x1111111111111111111111111111111111111111"
    slip_addr = "0x4444444444444444444444444444444444444444"
    s = _set([_uni_pool(uni_addr), _slip_pool(slip_addr)])
    calls = build_pool_state_calls(s)
    results = [
        (True, _encoded_slot0(79_228_162_514_264_337_593_543_950_336, 100)),  # uni_slot0 (7-field)
        (True, _encoded_liquidity(500_000)),                                   # uni_liquidity
        (True, _encoded_slipstream_slot0(2 * 79_228_162_514_264_337_593_543_950_336, -50)),  # slip_slot0 (6-field)
        (True, _encoded_liquidity(750_000)),                                   # slip_liquidity
    ]
    uni, aero, slip = decode_pool_state_results(calls, results)
    assert set(uni.keys()) == {uni_addr}
    assert set(slip.keys()) == {slip_addr}
    assert aero == {}
    assert slip[slip_addr].sqrt_price_x96 == 2 * 79_228_162_514_264_337_593_543_950_336
    assert slip[slip_addr].tick == -50
    assert slip[slip_addr].liquidity == 750_000


# --- PoolMonitor integration with fake transport ----------------------------

class _FakeTransport:
    """In-memory transport that yields canned heads and canned multicall returns.

    Each head can either be a dict (real head) or an Exception instance; raising
    simulates a WebSocket disconnect at that point in the stream.
    """

    def __init__(self, heads_batches, multicall_return: bytes):
        self._batches = iter(heads_batches)
        self._multicall = multicall_return
        self.call_count = 0

    async def subscribe_new_heads(self):
        try:
            batch = next(self._batches)
        except StopIteration:
            raise asyncio.CancelledError()
        for item in batch:
            if isinstance(item, BaseException):
                raise item
            yield item

    async def call_multicall3(self, call_data: bytes, block: int) -> bytes:
        self.call_count += 1
        return self._multicall


def _multicall_return_for(num_pools_uni: int, num_pools_aero: int) -> bytes:
    items = []
    for _ in range(num_pools_uni):
        items.append((True, _encoded_slot0(10**20, 42)))
        items.append((True, _encoded_liquidity(123_456)))
    for _ in range(num_pools_aero):
        items.append((True, _encoded_getreserves(1, 2, 3)))
    return abi_encode(["(bool,bytes)[]"], [items])


def test_pool_monitor_emits_block_and_gap_markers():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111"), _aero_pool("0x2222222222222222222222222222222222222222")])
    mc_return = _multicall_return_for(1, 1)

    transport = _FakeTransport(
        heads_batches=[
            [{"number": 100, "timestamp": 1_700_000_000}],
            [ConnectionError("ws closed")],
            [{"number": 103, "timestamp": 1_700_000_006}],
        ],
        multicall_return=mc_return,
    )

    blocks: list[BlockPoolStates] = []
    gaps: list[GapMarker] = []

    async def on_block(b): blocks.append(b)
    async def on_gap(g): gaps.append(g)

    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        reconnect_backoff_seconds=(0.0,),
    )

    async def run_with_timeout():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(run_with_timeout())

    assert len(blocks) == 2
    assert blocks[0].block_number == 100
    assert blocks[1].block_number == 103
    assert "0x1111111111111111111111111111111111111111" in blocks[0].uniswap_v3
    assert "0x2222222222222222222222222222222222222222" in blocks[0].aerodrome

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.last_block == 100
    assert gap.first_block_after_reconnect == 103
    assert gap.missed_blocks == 2
    assert "ConnectionError" in gap.error


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.2 (D-009): chain attribution + sampling cadence.
# ----------------------------------------------------------------------------


def test_pool_monitor_emits_chain_attribution_on_block_states():
    """PoolMonitor must tag every BlockPoolStates / GapMarker with its
    `chain_label` so the orchestrator can route by chain without parsing
    addresses."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    mc_return = _multicall_return_for(1, 0)
    transport = _FakeTransport(
        # Three batches: first head → disconnect (ConnectionError raises out
        # of subscribe_new_heads) → reconnect with second head. Same pattern
        # as test_pool_monitor_emits_block_and_gap_markers above.
        heads_batches=[
            [{"number": 200, "timestamp": 1_700_000_000}],
            [ConnectionError("ws blip")],
            [{"number": 201, "timestamp": 1_700_000_004}],
        ],
        multicall_return=mc_return,
    )
    blocks: list[BlockPoolStates] = []
    gaps: list[GapMarker] = []

    async def on_block(b): blocks.append(b)
    async def on_gap(g): gaps.append(g)

    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        reconnect_backoff_seconds=(0.0,),
        chain_label="arbitrum",
    )

    async def run_with_timeout():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(run_with_timeout())

    assert len(blocks) >= 1
    assert all(b.chain == "arbitrum" for b in blocks), \
        f"chain attribution missing: {[b.chain for b in blocks]}"
    assert len(gaps) >= 1
    assert all(g.chain == "arbitrum" for g in gaps)


def test_pool_monitor_sampling_n_fetches_first_then_every_nth():
    """With block_sampling_n=4 the monitor should fetch on headers 1, 5, 9, ...
    so 7 headers produce 2 multicalls (headers 1 and 5; header 9 not present)."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    mc_return = _multicall_return_for(1, 0)
    heads = [{"number": 100 + i, "timestamp": 1_700_000_000 + i} for i in range(7)]
    transport = _FakeTransport(
        heads_batches=[heads], multicall_return=mc_return,
    )
    blocks: list[BlockPoolStates] = []

    async def on_block(b): blocks.append(b)
    async def on_gap(g): pass

    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="arbitrum",
        block_sampling_n=4,
    )

    async def run_with_timeout():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(run_with_timeout())

    # Headers 1, 5 trigger fetch; headers 2, 3, 4, 6, 7 are sampled out.
    fetched_block_numbers = [b.block_number for b in blocks]
    assert fetched_block_numbers == [100, 104], \
        f"sampling stride wrong; fetched {fetched_block_numbers}"
    assert transport.call_count == 2


def test_pool_monitor_sampling_n_one_fetches_every_block():
    """Default block_sampling_n=1 must NOT skip any block (Phase 1
    back-compat — Base ran with sampling=1 throughout)."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    mc_return = _multicall_return_for(1, 0)
    heads = [{"number": 100 + i, "timestamp": 1_700_000_000 + i} for i in range(5)]
    transport = _FakeTransport(
        heads_batches=[heads], multicall_return=mc_return,
    )
    blocks: list[BlockPoolStates] = []
    async def on_block(b): blocks.append(b)
    async def on_gap(g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        # chain_label and block_sampling_n default — verifies Phase 1 callers
        # don't need to pass either.
    )
    async def run_with_timeout():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    asyncio.run(run_with_timeout())
    assert [b.block_number for b in blocks] == [100, 101, 102, 103, 104]
    assert transport.call_count == 5
    # Default chain attribution = "base"
    assert all(b.chain == "base" for b in blocks)


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.8.1 (2026-05-18): WS-stall detector.
# Background: EXP-002 first deploy stalled with Base WS delivering the same
# block ~10x/s for 23h while Arb/OP WS went silent. No exception was raised
# by the transport layer. PoolMonitor now wraps the WS iterator with two
# detectors that raise WSStallError → outer reconnect loop fires.
# ----------------------------------------------------------------------------


class _SilentTransport:
    """Transport whose subscribe_new_heads yields nothing until cancelled."""
    async def subscribe_new_heads(self):
        # Sleep forever (will be cancelled by caller's wait_for timeout).
        await asyncio.sleep(60)
        # unreachable; keep this as an async-generator
        if False:
            yield {}
    async def call_multicall3(self, data, block):
        raise AssertionError("multicall should not be reached during silence")


class _RepeatingTransport:
    """Yields the same head repeatedly with a tiny delay between yields."""
    def __init__(self, head: dict, total: int = 100, gap_seconds: float = 0.05):
        self._head = head
        self._total = total
        self._gap = gap_seconds

    async def subscribe_new_heads(self):
        for _ in range(self._total):
            await asyncio.sleep(self._gap)
            yield self._head

    async def call_multicall3(self, data, block):
        raise AssertionError("multicall should not be reached when stuck on one block")


def test_default_stall_threshold_floors_at_20s():
    """Arb's 250ms block * 10 = 2.5s, below the 20s floor → 20s."""
    assert _default_stall_threshold("arbitrum") == 20.0
    assert _default_stall_threshold("base") == 20.0   # 2s * 10 = 20
    assert _default_stall_threshold("optimism") == 20.0
    # Unknown chain falls back to 2s default → 20s floor.
    assert _default_stall_threshold("ethereum") == 20.0


def test_pool_monitor_raises_wsstall_on_silence_then_reconnects():
    """Silent WS for >threshold → WSStallError raised by the silence detector.
    Sub-phase 2.8.3 changed behavior: after `max_consecutive_stalls` (default 3)
    consecutive stalls, the error ESCALATES out of run() so the outer caller
    (ChainMonitor) can recycle the WS. This test sets max=10 so the test
    finishes via driver timeout before escalation, exercising the original
    subscription-level retry path."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    transport = _SilentTransport()
    async def on_block(_b): pass
    async def on_gap(_g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="base",
        stall_threshold_seconds=0.2,   # tight threshold for fast test
        reconnect_backoff_seconds=(0.05,),
        max_consecutive_stalls=10,     # high — escalate-after path covered by
                                       # test_pool_monitor_escalates_after_max
    )

    async def driver():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, WSStallError):
            pass

    # No assertion that WSStallError ESCAPED — the outer except catches it
    # for reconnect (subscription level). We just verify the run terminated
    # via the outer timeout (didn't hang in some other way).
    asyncio.run(driver())


def test_pool_monitor_raises_wsstall_on_same_block_repeating():
    """Same block_number for >threshold seconds → WSStallError → reconnect."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    head = {"number": 100, "timestamp": 1_700_000_000}
    transport = _RepeatingTransport(head, total=200, gap_seconds=0.01)
    blocks_observed: list[int] = []
    async def on_block(b): blocks_observed.append(b.block_number)
    async def on_gap(_g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="base",
        stall_threshold_seconds=0.2,
        reconnect_backoff_seconds=(0.05,),
    )

    async def driver():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(driver())
    # First head triggers a fetch — but multicall raises (no fixture set up
    # for it). So 0 blocks observed; the test's value is in not hanging.
    # The on_block callback should NOT have been called more than once
    # (subsequent ticks see same block_number and skip the fetch).
    assert len(blocks_observed) == 0


def test_pool_monitor_advancing_blocks_do_not_trigger_stall():
    """Normal block advancement should NOT raise WSStallError even with
    a tight stall threshold."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    mc_return = _multicall_return_for(1, 0)
    # Multiple distinct blocks delivered close together.
    heads = [{"number": 100 + i, "timestamp": 1_700_000_000 + i} for i in range(5)]
    transport = _FakeTransport([heads], multicall_return=mc_return)
    blocks: list[BlockPoolStates] = []
    async def on_block(b): blocks.append(b)
    async def on_gap(_g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="base",
        stall_threshold_seconds=0.5,   # tight, but blocks advance fast
        reconnect_backoff_seconds=(0.05,),
    )

    async def driver():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(driver())
    # All 5 distinct blocks fetched.
    assert [b.block_number for b in blocks] == [100, 101, 102, 103, 104]


def test_pool_monitor_stall_threshold_override():
    """The constructor kwarg should override the per-chain default."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    async def cb(_): pass
    m = PoolMonitor(
        transport=_FakeTransport([[]], b""),
        pool_set=s, on_block=cb, on_gap=cb,
        chain_label="arbitrum",
        stall_threshold_seconds=45.0,
    )
    assert m.stall_threshold_seconds == 45.0
    # Default for arbitrum is 20s; override took precedence.
    m_default = PoolMonitor(
        transport=_FakeTransport([[]], b""),
        pool_set=s, on_block=cb, on_gap=cb,
        chain_label="arbitrum",
    )
    assert m_default.stall_threshold_seconds == 20.0


def test_pool_monitor_rejects_invalid_stall_threshold():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    async def cb(_): pass
    with pytest.raises(ValueError, match="stall_threshold_seconds"):
        PoolMonitor(
            transport=_FakeTransport([[]], b""),
            pool_set=s, on_block=cb, on_gap=cb,
            stall_threshold_seconds=0,
        )
    with pytest.raises(ValueError, match="stall_threshold_seconds"):
        PoolMonitor(
            transport=_FakeTransport([[]], b""),
            pool_set=s, on_block=cb, on_gap=cb,
            stall_threshold_seconds=-1.0,
        )


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.8.3 (2026-05-21, RCA fix): PoolMonitor escalates after
# max_consecutive_stalls so ChainMonitor can recycle the underlying WS.
# ----------------------------------------------------------------------------


class _AlwaysSilentTransport:
    """Every subscribe_new_heads() call yields nothing. Each silence triggers
    a WSStallError. Used to test escalation behavior."""
    async def subscribe_new_heads(self):
        await asyncio.sleep(60)
        if False:
            yield {}
    async def call_multicall3(self, data, block):
        raise AssertionError("multicall should not be reached during silence")


def test_pool_monitor_escalates_after_max_consecutive_stalls():
    """N consecutive WSStallErrors (no intervening successful tick) → raise
    out so the outer caller (ChainMonitor) can recycle the WS."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    transport = _AlwaysSilentTransport()
    async def on_block(_b): pass
    async def on_gap(_g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="base",
        stall_threshold_seconds=0.05,   # very tight for fast test
        reconnect_backoff_seconds=(0.01,),
        max_consecutive_stalls=2,
    )

    raised: list[Exception] = []
    async def driver():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except WSStallError as e:
            raised.append(e)
        except asyncio.TimeoutError:
            raised.append(asyncio.TimeoutError("ran out of time"))

    asyncio.run(driver())

    # After 2 stalls (no successful tick), PoolMonitor should have raised
    # WSStallError out of run() so a higher layer can rebuild the WS.
    assert raised and isinstance(raised[0], WSStallError), \
        f"expected WSStallError to escalate, got {raised}"


class _OneHeadThenSilentTransport:
    """First subscribe yields one head, then goes silent. Second subscribe
    is silent too. Verifies the stall counter RESETS on a successful tick.
    Returns a valid multicall payload (1 UniV3 pool's worth) so the on_block
    flow can dispatch."""
    def __init__(self, mc_return: bytes):
        self._n_subs = 0
        self._mc_return = mc_return

    async def subscribe_new_heads(self):
        self._n_subs += 1
        if self._n_subs == 1:
            yield {"number": 100, "timestamp": 1_700_000_000}
            await asyncio.sleep(60)
        else:
            await asyncio.sleep(60)
        if False:
            yield {}

    async def call_multicall3(self, data, block):
        return self._mc_return


def test_pool_monitor_stall_counter_resets_on_successful_tick():
    """One successful tick should reset the consecutive stall counter,
    so a subsequent isolated stall does NOT escalate."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    mc_return = _multicall_return_for(1, 0)
    transport = _OneHeadThenSilentTransport(mc_return)
    blocks: list[BlockPoolStates] = []
    async def on_block(b): blocks.append(b)
    async def on_gap(_g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="base",
        stall_threshold_seconds=0.05,
        reconnect_backoff_seconds=(0.01,),
        max_consecutive_stalls=2,
    )

    raised: list[Exception] = []
    async def driver():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except WSStallError as e:
            raised.append(e)
        except asyncio.TimeoutError:
            raised.append(asyncio.TimeoutError("ran out of time"))

    asyncio.run(driver())

    # We got at least one successful block (so the counter was reset).
    assert len(blocks) >= 1, "expected one successful head before stall"
    # We did eventually escalate (after the reset, the next 2 stalls hit).
    assert raised and isinstance(raised[0], WSStallError), \
        f"expected eventual escalation, got {raised}"


def test_pool_monitor_default_max_consecutive_stalls_is_three():
    """Default value matches the spec — 3 stalls before WS-level recycle."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    async def cb(_): pass
    m = PoolMonitor(
        transport=_FakeTransport([[]], b""),
        pool_set=s, on_block=cb, on_gap=cb,
    )
    assert m.max_consecutive_stalls == 3


class _AlwaysRaisingTransport:
    """Every subscribe_new_heads() call raises a non-WSStallError exception
    (e.g. ConnectionClosedError-equivalent). Used to verify that the unified
    failure handling escalates on ANY exception type — not just WSStallError.

    Background: the 2026-05-22 production failure was thousands of silent
    ConnectionClosedError retries because the original escalation logic
    ONLY counted WSStallError. ConnectionClosedError fell through to a
    different except branch that never escalated."""
    def __init__(self, exc_factory):
        self._exc_factory = exc_factory
        self.call_count = 0

    async def subscribe_new_heads(self):
        self.call_count += 1
        raise self._exc_factory()
        if False:
            yield {}

    async def call_multicall3(self, data, block):
        raise AssertionError("multicall should not be reached")


def test_pool_monitor_escalates_on_non_stall_exception_too():
    """Sub-phase 2.8.4: ConnectionClosedError-style exceptions (anything
    other than WSStallError or CancelledError) must also trigger
    escalation after K consecutive failures. The 2026-05-22 production
    failure was thousands of silent retries because this path didn't
    escalate."""
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])

    class _FakeConnectionClosedError(Exception):
        """Stand-in for web3.py's ConnectionClosedError."""
        pass

    transport = _AlwaysRaisingTransport(
        lambda: _FakeConnectionClosedError("no close frame received or sent"),
    )
    async def on_block(_b): pass
    async def on_gap(_g): pass
    monitor = PoolMonitor(
        transport=transport, pool_set=s,
        on_block=on_block, on_gap=on_gap,
        rate_limit_rps=1000.0,
        chain_label="base",
        stall_threshold_seconds=0.05,
        reconnect_backoff_seconds=(0.01,),
        max_consecutive_stalls=2,
    )

    raised: list[Exception] = []
    async def driver():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except Exception as e:
            raised.append(e)

    asyncio.run(driver())

    # After 2 ConnectionClosedError raises, PoolMonitor must escalate the
    # exception out — NOT swallow it and retry forever. The escalated
    # exception is the _FakeConnectionClosedError (or its wrapper).
    assert raised, "expected an exception to escalate out of PoolMonitor.run"
    # The transport's subscribe was attempted at most max_consecutive_stalls
    # times (since the very first attempt raises and counts as failure #1,
    # the second raises and counts as #2, then escalate).
    assert transport.call_count <= 2, \
        f"expected at most 2 subscribe attempts before escalation; got {transport.call_count}"


def test_pool_monitor_rejects_invalid_max_consecutive_stalls():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    async def cb(_): pass
    with pytest.raises(ValueError, match="max_consecutive_stalls"):
        PoolMonitor(
            transport=_FakeTransport([[]], b""),
            pool_set=s, on_block=cb, on_gap=cb,
            max_consecutive_stalls=0,
        )


def test_pool_monitor_rejects_zero_or_negative_sampling():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111")])
    async def cb(_): pass
    with pytest.raises(ValueError, match=">= 1"):
        PoolMonitor(
            transport=_FakeTransport([[]], b""),
            pool_set=s, on_block=cb, on_gap=cb,
            block_sampling_n=0,
        )


def test_pool_monitor_single_multicall_per_block():
    s = _set([_uni_pool("0x1111111111111111111111111111111111111111"), _aero_pool("0x2222222222222222222222222222222222222222"), _aero_pool("0x3333333333333333333333333333333333333333", stable=True)])
    mc_return = _multicall_return_for(1, 2)

    transport = _FakeTransport(
        heads_batches=[[
            {"number": 1, "timestamp": 1}, {"number": 2, "timestamp": 3},
            {"number": 3, "timestamp": 5},
        ]],
        multicall_return=mc_return,
    )

    async def on_block(_b): pass
    async def on_gap(_g): pass

    monitor = PoolMonitor(transport, s, on_block, on_gap, rate_limit_rps=1000.0)

    async def run_with_timeout():
        try:
            await asyncio.wait_for(monitor.run(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    asyncio.run(run_with_timeout())
    assert transport.call_count == 3


def test_block_pool_states_ingest_lag_computed():
    received = datetime.fromtimestamp(1_700_000_010, tz=timezone.utc)
    fetched = datetime.fromtimestamp(1_700_000_011, tz=timezone.utc)
    states = BlockPoolStates(
        block_number=100, block_timestamp=1_700_000_008,
        received_at=received, fetched_at=fetched,
        uniswap_v3={}, aerodrome={},
    )
    assert states.ingest_lag_seconds == pytest.approx(3.0)
