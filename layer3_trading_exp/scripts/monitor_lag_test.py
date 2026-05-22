"""Phase 1.1 monitor lag-measurement driver.

Subscribes to newHeads on Base via WebSocket, fetches state for a small fixed
pool set (WETH/USDC pools on UniV3 + Aerodrome), and reports the lag from block
production timestamp to ingest completion. Runs for a fixed number of blocks
then exits.

Uses a hand-built PoolSet (NOT the run-frozen monitored_pools.json) — this
script is a connectivity/lag-measurement check, not the run-start enumeration.

Run from the experiment root:
    python -m layer3_trading_exp.scripts.monitor_lag_test
"""

from __future__ import annotations

import asyncio
import json
import statistics
from datetime import datetime, timezone
from typing import Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from web3 import AsyncWeb3
from web3.providers.persistent import WebSocketProvider

from ..config import DEFAULT
from ..pool_monitor import (
    AGGREGATE3_SELECTOR,
    BlockPoolStates,
    GapMarker,
    MULTICALL3_ADDRESS,
    PoolMonitor,
    _decode_aggregate3_return,
    _encode_aggregate3_call,
)
from ..pool_set import PoolInfo, PoolProtocol, PoolSet, TokenInfo


WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
WETH_USDC_UNIV3_005 = "0xd0b53d9277642d899df5c87a3966a349a798f224"
WETH_USDC_AERODROME_VOLATILE = "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59"


def _build_test_set() -> PoolSet:
    weth = TokenInfo(WETH_BASE.lower(), "WETH", 18)
    usdc = TokenInfo(USDC_BASE.lower(), "USDC", 6)
    return PoolSet(
        pools=(
            PoolInfo(
                address=WETH_USDC_UNIV3_005.lower(),
                protocol=PoolProtocol.UNISWAP_V3,
                token0=weth, token1=usdc,
                fee_bps=5,
                tvl_usd_at_enumeration=0.0,
                enumerated_at_block=0,
            ),
            PoolInfo(
                address=WETH_USDC_AERODROME_VOLATILE.lower(),
                protocol=PoolProtocol.AERODROME_VOLATILE,
                token0=weth, token1=usdc,
                fee_bps=None,
                tvl_usd_at_enumeration=0.0,
                enumerated_at_block=0,
            ),
        ),
        chain="base",
        enumerated_at_block=0,
        enumerated_at="lag-test",
        uniswap_v3_tvl_floor_usd=1_000_000.0,
        aerodrome_tvl_floor_usd=500_000.0,
    )


class _AlchemyTransport:
    """Wraps an AsyncWeb3 instance to match PoolMonitor's transport protocol."""

    def __init__(self, w3: AsyncWeb3, http_w3):
        self._w3 = w3
        self._http_w3 = http_w3

    async def subscribe_new_heads(self):
        sub_id = await self._w3.eth.subscribe("newHeads")
        async for msg in self._w3.socket.process_subscriptions():
            res = msg["result"]
            yield {"number": int(res["number"], 16) if isinstance(res["number"], str) else int(res["number"]),
                   "timestamp": int(res["timestamp"], 16) if isinstance(res["timestamp"], str) else int(res["timestamp"])}

    async def call_multicall3(self, call_data: bytes, block: int) -> bytes:
        # use the synchronous HTTP client for the multicall — simpler than async-routing
        raw = self._http_w3.eth.call({
            "to": self._http_w3.to_checksum_address(MULTICALL3_ADDRESS),
            "data": "0x" + call_data.hex(),
        }, block_identifier=block)
        return bytes(raw)


async def run(num_blocks: int = 15) -> None:
    cfg = DEFAULT
    if not cfg.base_wss_url:
        raise SystemExit("BASE_WSS_URL is empty. Check .env.")
    pool_set = _build_test_set()

    from web3 import Web3
    http_w3 = Web3(Web3.HTTPProvider(cfg.base_rpc_url))

    blocks: list[BlockPoolStates] = []
    gaps: list[GapMarker] = []

    async def on_block(b: BlockPoolStates) -> None:
        blocks.append(b)
        uni_count = len(b.uniswap_v3)
        aero_count = len(b.aerodrome)
        lag = b.ingest_lag_seconds
        print(f"block {b.block_number:,}  uni={uni_count} aero={aero_count}  lag={lag:.2f}s")
        if len(blocks) >= num_blocks:
            raise asyncio.CancelledError("done")

    async def on_gap(g: GapMarker) -> None:
        gaps.append(g)
        print(f"GAP: missed {g.missed_blocks} blocks ({g.last_block} -> {g.first_block_after_reconnect}): {g.error}")

    print(f"subscribing to {cfg.base_wss_url[:60]}... (target {num_blocks} blocks)")

    async with AsyncWeb3(WebSocketProvider(cfg.base_wss_url)) as ws_w3:
        transport = _AlchemyTransport(ws_w3, http_w3)
        monitor = PoolMonitor(
            transport=transport, pool_set=pool_set,
            on_block=on_block, on_gap=on_gap,
            rate_limit_rps=20.0,
        )
        try:
            await monitor.run()
        except asyncio.CancelledError:
            pass

    if not blocks:
        print("no blocks observed")
        return
    lags = [b.ingest_lag_seconds for b in blocks]
    print()
    print(f"observed {len(blocks)} blocks across {blocks[-1].block_number - blocks[0].block_number + 1} block range")
    print(f"ingest lag (s): p50={statistics.median(lags):.2f}  "
          f"min={min(lags):.2f}  max={max(lags):.2f}  mean={statistics.mean(lags):.2f}")
    print(f"gap markers: {len(gaps)}")


if __name__ == "__main__":
    asyncio.run(run())
