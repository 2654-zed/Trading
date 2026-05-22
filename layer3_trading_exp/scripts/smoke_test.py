"""Phase 1.1 connectivity smoke test.

Verifies the Alchemy endpoint configured in .env responds, reports current Base
block number, and times a few representative read patterns to inform the
enumeration strategy.

Run from the experiment root:
    python -m layer3_trading_exp.scripts.smoke_test
"""

from __future__ import annotations

import time

from web3 import Web3

from ..config import DEFAULT
from ..pool_set import (
    AERODROME_FACTORY_BASE,
    UNISWAP_V3_FACTORY_BASE,
    UNI_V3_FACTORY_ABI,
    AERODROME_FACTORY_ABI,
)


def main() -> None:
    cfg = DEFAULT
    if not cfg.base_rpc_url:
        raise SystemExit("BASE_RPC_URL is empty. Check .env.")

    w3 = Web3(Web3.HTTPProvider(cfg.base_rpc_url))

    t0 = time.perf_counter()
    block = w3.eth.block_number
    block_dt = (time.perf_counter() - t0) * 1000
    chain_id = w3.eth.chain_id
    print(f"connected: chain_id={chain_id}, latest_block={block:,}, eth_blockNumber={block_dt:.1f}ms")

    uni_factory = w3.eth.contract(
        address=w3.to_checksum_address(UNISWAP_V3_FACTORY_BASE),
        abi=UNI_V3_FACTORY_ABI,
    )
    aero_factory = w3.eth.contract(
        address=w3.to_checksum_address(AERODROME_FACTORY_BASE),
        abi=AERODROME_FACTORY_ABI,
    )

    sample_size = 10_000
    t0 = time.perf_counter()
    uni_logs_sample = uni_factory.events.PoolCreated.get_logs(
        from_block=block - sample_size, to_block=block,
    )
    uni_dt = (time.perf_counter() - t0) * 1000
    print(f"uniswap_v3 PoolCreated last {sample_size:,} blocks: {len(uni_logs_sample)} events, {uni_dt:.0f}ms")

    t0 = time.perf_counter()
    aero_logs_sample = aero_factory.events.PoolCreated.get_logs(
        from_block=block - sample_size, to_block=block,
    )
    aero_dt = (time.perf_counter() - t0) * 1000
    print(f"aerodrome PoolCreated last {sample_size:,} blocks:  {len(aero_logs_sample)} events, {aero_dt:.0f}ms")

    print()
    print(f"projected full uniswap_v3 scan ({block - 1_371_680:,} blocks at {sample_size:,}/call):")
    n_calls = (block - 1_371_680 + sample_size - 1) // sample_size
    proj_seconds = n_calls * uni_dt / 1000
    print(f"  ~{n_calls:,} getLogs calls · ~{proj_seconds:.0f}s ({proj_seconds / 60:.1f} min) at observed latency")

    print(f"projected full aerodrome scan ({block - 3_200_559:,} blocks at {sample_size:,}/call):")
    n_calls = (block - 3_200_559 + sample_size - 1) // sample_size
    proj_seconds = n_calls * aero_dt / 1000
    print(f"  ~{n_calls:,} getLogs calls · ~{proj_seconds:.0f}s ({proj_seconds / 60:.1f} min) at observed latency")


if __name__ == "__main__":
    main()
