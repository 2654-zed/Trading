"""Phase 1.1 — async pool monitor for Base.

Subscribes to newHeads over WebSocket and, for each block, fetches pool state via
a single Multicall3 eth_call to stay inside Alchemy's free-tier compute-unit budget
(spec §Phase 1.1 acceptance: "RPC rate limit respected (80% ceiling)").

Per protocol:
- Uniswap V3 + Aerodrome Slipstream → slot0 (sqrtPriceX96, tick) + liquidity
- Aerodrome v1 (volatile and stable) → getReserves (reserve0, reserve1, ts_last)

Invariants:
- Read-only RPC — never constructs or submits a transaction.
- Loud logging — disconnect/reconnect cycles emit GapMarker records with the
  last observed block and the first block after reconnect. Silent drops are
  not acceptable (spec invariant #10).
- The monitored pool set is frozen at run start; this module reads it from
  pool_set.PoolSet and does not mutate it.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode

from .pool_set import PoolInfo, PoolProtocol, PoolSet


MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

SELECTOR_SLOT0 = bytes.fromhex("3850c7bd")
SELECTOR_LIQUIDITY = bytes.fromhex("1a686502")
SELECTOR_GETRESERVES = bytes.fromhex("0902f1ac")

SLOT0_RETURN_TYPES = ("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool")
# Aerodrome Slipstream's slot0 omits the `uint8 feeProtocol` field that UniV3
# carries — fee handling differs in Slipstream and the field was removed from
# the slot0 struct. The 6-tuple (sqrtPriceX96, tick, obsIdx, obsCard, obsCardNext,
# unlocked) is the on-chain return shape; ignoring this distinction caused all
# Slipstream slot0 calls to silently fail to decode in the Phase 1.2 smoke test.
SLIPSTREAM_SLOT0_RETURN_TYPES = ("uint160", "int24", "uint16", "uint16", "uint16", "bool")
LIQUIDITY_RETURN_TYPES = ("uint128",)
AERODROME_GETRESERVES_RETURN_TYPES = ("uint256", "uint256", "uint256")

AGGREGATE3_SELECTOR = bytes.fromhex("82ad56cb")


@dataclass(frozen=True)
class UniV3PoolState:
    pool_address: str
    sqrt_price_x96: int
    tick: int
    liquidity: int


@dataclass(frozen=True)
class AerodromePoolState:
    pool_address: str
    reserve0: int
    reserve1: int
    block_timestamp_last: int


@dataclass(frozen=True)
class BlockPoolStates:
    block_number: int
    block_timestamp: int
    received_at: datetime
    fetched_at: datetime
    uniswap_v3: dict[str, UniV3PoolState]
    aerodrome: dict[str, AerodromePoolState]
    # Aerodrome Slipstream pools share the concentrated-liquidity state shape
    # (sqrtPriceX96 + tick + liquidity) with UniV3, so we reuse UniV3PoolState.
    # Kept in a separate dict so callers can attribute back to protocol when
    # selecting AMM math (concentrated-liquidity quote) and fees (lower bps tiers).
    slipstream: dict[str, UniV3PoolState] = field(default_factory=dict)
    # Phase 2 sub-phase 2.2 (D-009): chain attribution. Defaults to "base" for
    # Phase 1 callsite back-compat; Phase 2 ChainMonitors override via PoolMonitor's
    # chain_label.
    chain: str = "base"

    @property
    def ingest_lag_seconds(self) -> float:
        return (self.fetched_at.timestamp() - self.block_timestamp)


@dataclass(frozen=True)
class GapMarker:
    last_block: int
    first_block_after_reconnect: int
    disconnected_at: datetime
    reconnected_at: datetime
    error: str
    # Phase 2 sub-phase 2.2: gaps need chain attribution so the orchestrator
    # can tell which chain lost connectivity without parsing message text.
    chain: str = "base"

    @property
    def missed_blocks(self) -> int:
        return max(0, self.first_block_after_reconnect - self.last_block - 1)


class AsyncRateLimiter:
    """Token-bucket rate limiter. Default: 20 rps (80% of Alchemy free 25 rps)."""

    def __init__(self, rate_per_second: float, burst: Optional[int] = None):
        self._rate = float(rate_per_second)
        self._capacity = float(burst or max(1, int(rate_per_second)))
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: int = 1) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                await asyncio.sleep(deficit / self._rate)


def _encode_aggregate3_call(calls: list[tuple[str, bool, bytes]]) -> bytes:
    """Encode a Multicall3.aggregate3((address,bool,bytes)[]) call.

    eth-abi's address encoder accepts 0x-prefixed hex strings (40 hex chars =
    20 bytes). Callers must pass full-length addresses.
    """
    body = abi_encode(["(address,bool,bytes)[]"], [calls])
    return AGGREGATE3_SELECTOR + body


def _decode_aggregate3_return(raw: bytes) -> list[tuple[bool, bytes]]:
    """Decode Multicall3.aggregate3 return: (bool success, bytes returnData)[]."""
    (items,) = abi_decode(["(bool,bytes)[]"], raw)
    return [(bool(s), bytes(d)) for (s, d) in items]


def build_pool_state_calls(pool_set: PoolSet) -> list[tuple[str, bool, bytes, str, str]]:
    """Return list of (target, allowFailure, callData, pool_address, kind) tuples.

    `kind` in {"uni_slot0", "uni_liquidity", "slip_slot0", "slip_liquidity",
    "aero_getreserves"} — used during result decoding to route bytes back to
    the right pool+field+protocol.

    Concentrated-liquidity protocols (UniV3, Aerodrome Slipstream, Velodrome
    Slipstream) emit two calls per pool (slot0 + liquidity); classic UniV2
    forks + Solidly stable/volatile (Aerodrome v1, Velodrome v1, Camelot V2,
    SushiSwap V2) emit one call (getReserves).

    Phase 2 sub-phase 2.3 (D-009): protocol coverage extended to Arb + OP.
    Camelot V3 (Algebra V3 fork) is enumerated but SKIPPED here — its slot0
    return shape differs from UniV3 and needs a dedicated decoder; deferred
    to sub-phase 2.4 prep. Affected pools log a one-time warning per pool
    address at construction time.
    """
    calls: list[tuple[str, bool, bytes, str, str]] = []
    skipped_camelot_v3: list[str] = []
    for p in pool_set.pools:
        if p.protocol == PoolProtocol.UNISWAP_V3:
            calls.append((p.address, True, SELECTOR_SLOT0, p.address, "uni_slot0"))
            calls.append((p.address, True, SELECTOR_LIQUIDITY, p.address, "uni_liquidity"))
        elif p.protocol in (
            PoolProtocol.AERODROME_SLIPSTREAM,
            PoolProtocol.VELODROME_SLIPSTREAM,
        ):
            # Both are Slipstream-style CL (UniV3-style slot0 minus feeProtocol);
            # share the 6-tuple decoder.
            calls.append((p.address, True, SELECTOR_SLOT0, p.address, "slip_slot0"))
            calls.append((p.address, True, SELECTOR_LIQUIDITY, p.address, "slip_liquidity"))
        elif p.protocol in (
            PoolProtocol.AERODROME_VOLATILE,
            PoolProtocol.AERODROME_STABLE,
            PoolProtocol.VELODROME_VOLATILE,
            PoolProtocol.VELODROME_STABLE,
            PoolProtocol.CAMELOT_V2,
            PoolProtocol.SUSHISWAP_V2,
        ):
            # All UniV2-style CPAMM and Solidly forks expose `getReserves()`
            # with the same `(uint256,uint256,uint256)` shape — eth-abi decodes
            # uint112/uint32 packed reserves into uint256 cleanly.
            calls.append((p.address, True, SELECTOR_GETRESERVES, p.address, "aero_getreserves"))
        elif p.protocol == PoolProtocol.CAMELOT_V3:
            # Algebra V3 slot0 shape: (uint160, int24, uint16, uint16, uint8,
            # uint8, bool) — different from UniV3's (uint160, int24, uint16,
            # uint16, uint16, uint8, bool). Skipped here; full support lands
            # in sub-phase 2.4 prep with a new ALGEBRA_V3 decoder.
            skipped_camelot_v3.append(p.address)
        else:
            raise ValueError(f"unsupported protocol for monitor: {p.protocol!r}")
    if skipped_camelot_v3:
        import sys as _sys
        print(f"[pool_monitor] CAMELOT_V3 state fetch deferred to sub-phase 2.4 "
              f"(skipping {len(skipped_camelot_v3)} pools): "
              f"{skipped_camelot_v3[:3]}{'…' if len(skipped_camelot_v3) > 3 else ''}",
              file=_sys.stderr, flush=True)
    return calls


def decode_pool_state_results(
    calls: list[tuple[str, bool, bytes, str, str]],
    results: list[tuple[bool, bytes]],
) -> tuple[dict[str, UniV3PoolState], dict[str, AerodromePoolState], dict[str, UniV3PoolState]]:
    """Returns (uniswap_v3, aerodrome, slipstream) dicts.

    A concentrated-liquidity pool only appears in its return dict if BOTH slot0
    and liquidity calls succeeded; partial state is dropped (loud-skip — no
    silent half-state where slippage math would be wrong).
    """
    if len(calls) != len(results):
        raise ValueError(f"call/result length mismatch: {len(calls)} vs {len(results)}")

    uni_partial: dict[str, dict] = {}
    slip_partial: dict[str, dict] = {}
    aero: dict[str, AerodromePoolState] = {}

    for (_target, _allow, _data, pool_addr, kind), (success, payload) in zip(calls, results):
        if not success or not payload:
            continue
        try:
            if kind == "uni_slot0":
                sqrt_price, tick, *_ = abi_decode(SLOT0_RETURN_TYPES, payload)
                uni_partial.setdefault(pool_addr, {})["slot0"] = (int(sqrt_price), int(tick))
            elif kind == "uni_liquidity":
                (liq,) = abi_decode(LIQUIDITY_RETURN_TYPES, payload)
                uni_partial.setdefault(pool_addr, {})["liquidity"] = int(liq)
            elif kind == "slip_slot0":
                sqrt_price, tick, *_ = abi_decode(SLIPSTREAM_SLOT0_RETURN_TYPES, payload)
                slip_partial.setdefault(pool_addr, {})["slot0"] = (int(sqrt_price), int(tick))
            elif kind == "slip_liquidity":
                (liq,) = abi_decode(LIQUIDITY_RETURN_TYPES, payload)
                slip_partial.setdefault(pool_addr, {})["liquidity"] = int(liq)
            elif kind == "aero_getreserves":
                r0, r1, ts = abi_decode(AERODROME_GETRESERVES_RETURN_TYPES, payload)
                aero[pool_addr] = AerodromePoolState(pool_addr, int(r0), int(r1), int(ts))
        except Exception:
            continue

    def _build_cl(partial: dict[str, dict]) -> dict[str, UniV3PoolState]:
        out: dict[str, UniV3PoolState] = {}
        for addr, parts in partial.items():
            if "slot0" not in parts or "liquidity" not in parts:
                continue
            sqrt_price, tick = parts["slot0"]
            out[addr] = UniV3PoolState(addr, sqrt_price, tick, parts["liquidity"])
        return out

    return _build_cl(uni_partial), aero, _build_cl(slip_partial)


BlockCallback = Callable[[BlockPoolStates], Awaitable[None]]
GapCallback = Callable[[GapMarker], Awaitable[None]]


class WSStallError(Exception):
    """Raised when a WebSocket newHeads subscription appears alive but has
    stopped advancing — either by delivering the same block repeatedly OR
    by going silent (no message within threshold).

    Phase 2 sub-phase 2.8.1 (2026-05-18): introduced after EXP-002 first
    deploy stalled with Base's WS delivering block 46,136,019 ~10x/s for
    23h while Arb/OP WS went silent, with NO exception ever raised by the
    underlying transport. PoolMonitor's existing reconnect loop only fires
    on exception; this exception class is the explicit trigger.
    """
    def __init__(self, chain_label: str, kind: str, detail: str):
        super().__init__(f"WS stall on {chain_label}: {kind} ({detail})")
        self.chain_label = chain_label
        self.kind = kind  # "silent" | "same_block_repeating"
        self.detail = detail


# Default per-chain block-time estimates used to scale the stall threshold.
# Threshold = max(20s, block_time * 10) so a single missed tick isn't a stall
# but ~10 consecutive missed ticks is. For Arb's 250ms blocks, 10× still hits
# 2.5s which would be too aggressive — use the 20s floor for those cases.
_BLOCK_TIME_SECONDS_BY_CHAIN: dict[str, float] = {
    "base": 2.0,
    "optimism": 2.0,
    "arbitrum": 0.25,
}


def _default_stall_threshold(chain_label: str) -> float:
    bt = _BLOCK_TIME_SECONDS_BY_CHAIN.get(chain_label, 2.0)
    return max(20.0, bt * 10.0)


class PoolMonitor:
    """Async monitor driven by an injected RPC transport.

    The transport must expose:
      - subscribe_new_heads() -> async iterator of {'number': int, 'timestamp': int}
      - call_multicall3(call_data: bytes, block: Optional[int]) -> bytes
    """

    def __init__(
        self,
        transport,
        pool_set: PoolSet,
        on_block: BlockCallback,
        on_gap: GapCallback,
        rate_limit_rps: float = 20.0,
        reconnect_backoff_seconds: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0),
        *,
        chain_label: str = "base",
        block_sampling_n: int = 1,
        stall_threshold_seconds: Optional[float] = None,
        max_consecutive_stalls: int = 3,
    ):
        # Phase 2 sub-phase 2.2: chain_label tags every BlockPoolStates and
        # GapMarker this monitor emits, so a downstream orchestrator can route
        # by chain without inspecting addresses. block_sampling_n controls
        # WHICH blocks trigger a multicall fetch: 1 = every block (Base / OP,
        # 2s block times), 8 = every 8th block (Arbitrum, 250ms block times,
        # sampled to ~2s wall cadence per the UNK-003 budget decision).
        # Sampling counts headers received since startup; the first observed
        # block always fetches.
        #
        # Phase 2 sub-phase 2.8.1: stall_threshold_seconds wraps the WS
        # iterator with two stall detectors:
        #   (1) silence: no message in >threshold seconds raises WSStallError
        #   (2) same_block_repeating: block_number stuck at the same value
        #       for >threshold seconds raises WSStallError
        # Either path triggers the existing reconnect loop. Default per
        # _default_stall_threshold(chain_label) — None falls back to that.
        self._transport = transport
        self._pool_set = pool_set
        self._on_block = on_block
        self._on_gap = on_gap
        self._limiter = AsyncRateLimiter(rate_limit_rps)
        self._backoff = reconnect_backoff_seconds
        self._calls = build_pool_state_calls(pool_set)
        self._aggregate_calldata = _encode_aggregate3_call(
            [(a, allow, data) for (a, allow, data, _pool, _kind) in self._calls]
        )
        self._chain_label = chain_label
        if block_sampling_n < 1:
            raise ValueError(f"block_sampling_n must be >= 1, got {block_sampling_n}")
        self._sampling_n = int(block_sampling_n)
        self._header_count = 0
        self._stall_threshold = (
            float(stall_threshold_seconds)
            if stall_threshold_seconds is not None
            else _default_stall_threshold(chain_label)
        )
        if self._stall_threshold <= 0:
            raise ValueError(
                f"stall_threshold_seconds must be > 0, got {self._stall_threshold}"
            )
        # Phase 2 sub-phase 2.8.3 (2026-05-21, RCA): when the underlying
        # WS connection goes silent at the TCP level, re-subscribing on
        # the same socket doesn't recover — we keep hitting WSStallError
        # forever. After `max_consecutive_stalls` WSStallErrors with no
        # intervening successful tick, escalate out of run() so the
        # caller (ChainMonitor) can close+re-open the WS connection.
        if max_consecutive_stalls < 1:
            raise ValueError(
                f"max_consecutive_stalls must be >= 1, got {max_consecutive_stalls}"
            )
        self._max_consecutive_stalls = int(max_consecutive_stalls)

    @property
    def stall_threshold_seconds(self) -> float:
        return self._stall_threshold

    @property
    def max_consecutive_stalls(self) -> int:
        return self._max_consecutive_stalls

    @property
    def chain_label(self) -> str:
        return self._chain_label

    async def run(self) -> None:
        last_block = -1
        attempt = 0
        disconnected_at: Optional[datetime] = None
        last_error = ""
        # Sub-phase 2.8.3 + 2.8.4 fix (2026-05-22): track consecutive
        # failures of ANY kind (WSStallError, ConnectionClosedError, OSError,
        # etc.) since the last successful tick. After max_consecutive_stalls
        # (variable name preserved for back-compat), raise the most recent
        # exception out so ChainMonitor can recycle the WS connection.
        # Earlier the counter only tracked WSStallError, which left
        # ConnectionClosedError to loop forever in the silent retry path —
        # see FAILURE_LOG 2026-05-22 for the live regression.
        consecutive_failures = 0
        while True:
            try:
                # Phase 2 sub-phase 2.8.1: manual __anext__ iteration so we can
                # wrap each yield with asyncio.wait_for (silence detection) AND
                # track time-since-last-NEW-block (same-block-repeat detection).
                # Either stall condition raises WSStallError → outer except
                # branch catches and triggers reconnect.
                head_stream = self._transport.subscribe_new_heads()
                ait_iter = head_stream.__aiter__() if hasattr(head_stream, "__aiter__") else head_stream
                last_new_block_at = time.monotonic()
                while True:
                    try:
                        head = await asyncio.wait_for(
                            ait_iter.__anext__(),
                            timeout=self._stall_threshold,
                        )
                    except asyncio.TimeoutError:
                        raise WSStallError(
                            self._chain_label, "silent",
                            f"no head within {self._stall_threshold:g}s "
                            f"(last_block={last_block})",
                        ) from None
                    except StopAsyncIteration:
                        # Stream ended naturally (e.g. fake transport in tests
                        # exhausted its canned heads). Outer except handles via
                        # backoff + re-subscribe.
                        break

                    attempt = 0
                    # Sub-phase 2.8.3: a head arriving resets the consecutive
                    # failure counter — the WS is delivering messages again.
                    consecutive_failures = 0
                    block_number = int(head["number"])
                    block_timestamp = int(head["timestamp"])
                    received_at = datetime.now(timezone.utc)
                    self._header_count += 1
                    now_mono = time.monotonic()

                    if disconnected_at is not None and last_block >= 0:
                        await self._on_gap(GapMarker(
                            last_block=last_block,
                            first_block_after_reconnect=block_number,
                            disconnected_at=disconnected_at,
                            reconnected_at=received_at,
                            error=last_error,
                            chain=self._chain_label,
                        ))
                        disconnected_at = None
                        last_error = ""

                    # Same-block-repeating detection: if block_number ==
                    # last_block, do NOT update last_new_block_at. If we've
                    # been stuck on the same block longer than threshold,
                    # raise to trigger reconnect.
                    if block_number == last_block:
                        if (now_mono - last_new_block_at) > self._stall_threshold:
                            raise WSStallError(
                                self._chain_label, "same_block_repeating",
                                f"block_number={block_number} unchanged for "
                                f"{now_mono - last_new_block_at:.1f}s "
                                f"(threshold {self._stall_threshold:g}s)",
                            )
                        # Same block, still within tolerance — skip fetch
                        # (we already have this block's state).
                        continue

                    # New block observed — reset the freshness timer.
                    last_new_block_at = now_mono

                    # Phase 2 sub-phase 2.2: sampling cadence. Skip the multicall
                    # for blocks not on the sampling stride, but track last_block
                    # so GapMarker math stays consistent across skipped headers.
                    last_block = block_number
                    if self._sampling_n > 1 and (self._header_count % self._sampling_n) != 1:
                        # `% N != 1` so the first observed header (header_count=1)
                        # always fetches; subsequent sampled headers are 1+N, 1+2N, ...
                        continue

                    states = await self._fetch_states(block_number, block_timestamp, received_at)
                    await self._on_block(states)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Sub-phase 2.8.4 (2026-05-22): unified failure handling.
                # ANY exception (WSStallError, ConnectionClosedError, OSError,
                # whatever) increments the consecutive_failures counter and
                # is escalated after max_consecutive_stalls attempts.
                # Re-subscribing on the same WS doesn't recover from socket-
                # level failures, so escalating lets ChainMonitor close +
                # re-open the WS connection (the only correct recovery).
                consecutive_failures += 1
                disconnected_at = datetime.now(timezone.utc)
                last_error = f"{type(exc).__name__}: {exc}"
                import sys as _sys
                if consecutive_failures >= self._max_consecutive_stalls:
                    print(
                        f"[pool_monitor:{self._chain_label}] escalating after "
                        f"{consecutive_failures} consecutive failures; "
                        f"last_error={last_error}",
                        file=_sys.stderr, flush=True,
                    )
                    raise
                delay = self._backoff[min(attempt, len(self._backoff) - 1)]
                print(
                    f"[pool_monitor:{self._chain_label}] subscription-level "
                    f"retry {consecutive_failures}/{self._max_consecutive_stalls} "
                    f"after {last_error}; sleeping {delay}s "
                    f"(last_block={last_block})",
                    file=_sys.stderr, flush=True,
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def _fetch_states(
        self, block_number: int, block_timestamp: int, received_at: datetime,
    ) -> BlockPoolStates:
        await self._limiter.acquire()
        raw = await self._transport.call_multicall3(self._aggregate_calldata, block_number)
        results = _decode_aggregate3_return(raw)
        uni, aero, slip = decode_pool_state_results(self._calls, results)
        return BlockPoolStates(
            block_number=block_number,
            block_timestamp=block_timestamp,
            received_at=received_at,
            fetched_at=datetime.now(timezone.utc),
            uniswap_v3=uni,
            aerodrome=aero,
            slipstream=slip,
            chain=self._chain_label,
        )
