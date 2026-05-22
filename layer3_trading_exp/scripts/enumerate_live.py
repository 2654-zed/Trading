"""Phase 1.1 live enumeration driver.

Connects to Base mainnet, queries DefiLlama for pools above the spec TVL floors,
derives each pool's on-chain address via factory.getPool(...), enriches with
on-chain token metadata, and writes the frozen monitored pool set to
data/run_metadata/monitored_pools.json.

This is a one-shot script. Re-running while monitored_pools.json exists raises
FileExistsError (spec invariant #2 — set is frozen at run start).

Run from the experiment root:
    python -m layer3_trading_exp.scripts.enumerate_live
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

# Progress messages may contain Unicode; Windows cp1252 redirected stdout
# can't encode them and crashes mid-run. Force UTF-8 explicitly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from web3 import Web3

from ..config import DEFAULT
from ..pool_set import (
    AERODROME_FACTORY_BASE,
    AERODROME_SLIPSTREAM_FACTORY_BASE,
    UNISWAP_V3_FACTORY_BASE,
    DefiLlamaTvlSource,
    PoolSet,
    enumerate_aerodrome_pools,
    enumerate_aerodrome_slipstream_pools,
    enumerate_uniswap_v3_pools,
)


def main() -> None:
    cfg = DEFAULT
    out_path = cfg.run_metadata_dir / "monitored_pools.json"
    if out_path.exists():
        raise SystemExit(
            f"{out_path} already exists. Refusing to re-enumerate (spec invariant #2)."
        )

    cfg.run_metadata_dir.mkdir(parents=True, exist_ok=True)

    w3 = Web3(Web3.HTTPProvider(cfg.base_rpc_url))
    head_block = w3.eth.block_number

    print(f"connected to Base, head_block={head_block:,}")
    print(f"uniswap_v3 factory      : {UNISWAP_V3_FACTORY_BASE}")
    print(f"aerodrome v1 factory    : {AERODROME_FACTORY_BASE}")
    print(f"aerodrome slip factory  : {AERODROME_SLIPSTREAM_FACTORY_BASE}")
    print(f"univ3 TVL floor         : ${cfg.uniswap_v3_tvl_floor_usd:,.0f}")
    print(f"aerodrome v1 TVL floor  : ${cfg.aerodrome_tvl_floor_usd:,.0f}")
    print(f"slipstream TVL floor    : ${cfg.uniswap_v3_tvl_floor_usd:,.0f}  (concentrated-liquidity, uses UniV3 floor)")
    print(f"tvl source              : DefiLlama /pools")
    print()

    tvl_source = DefiLlamaTvlSource()
    token_cache: dict = {}

    def progress(msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)

    print("== uniswap v3 enumeration ==", flush=True)
    t0 = time.perf_counter()
    uni_pools = enumerate_uniswap_v3_pools(
        w3, tvl_source, cfg.uniswap_v3_tvl_floor_usd,
        to_block=head_block, token_cache=token_cache,
        progress=progress,
    )
    uni_dt = time.perf_counter() - t0
    print(f"  {len(uni_pools)} pools above ${cfg.uniswap_v3_tvl_floor_usd:,.0f} TVL ({uni_dt:.0f}s)", flush=True)

    print("== aerodrome v1 enumeration ==", flush=True)
    t0 = time.perf_counter()
    aero_pools = enumerate_aerodrome_pools(
        w3, tvl_source, cfg.aerodrome_tvl_floor_usd,
        to_block=head_block, token_cache=token_cache,
        progress=progress,
    )
    aero_dt = time.perf_counter() - t0
    print(f"  {len(aero_pools)} pools above ${cfg.aerodrome_tvl_floor_usd:,.0f} TVL ({aero_dt:.0f}s)", flush=True)

    print("== aerodrome slipstream enumeration ==", flush=True)
    t0 = time.perf_counter()
    slip_pools = enumerate_aerodrome_slipstream_pools(
        w3, tvl_source, cfg.uniswap_v3_tvl_floor_usd,
        to_block=head_block, token_cache=token_cache,
        progress=progress,
    )
    slip_dt = time.perf_counter() - t0
    print(f"  {len(slip_pools)} pools above ${cfg.uniswap_v3_tvl_floor_usd:,.0f} TVL ({slip_dt:.0f}s)", flush=True)

    pool_set = PoolSet(
        pools=tuple(uni_pools + aero_pools + slip_pools),
        chain=cfg.chain,
        enumerated_at_block=head_block,
        enumerated_at=datetime.now(timezone.utc).isoformat(),
        uniswap_v3_tvl_floor_usd=cfg.uniswap_v3_tvl_floor_usd,
        aerodrome_tvl_floor_usd=cfg.aerodrome_tvl_floor_usd,
    )
    pool_set.write_to(out_path)

    print()
    print(f"wrote {out_path}")
    print(f"total pools: {len(pool_set)}")
    print(f"  uniswap_v3:           {len(uni_pools)}")
    print(f"  aerodrome volatile:   {sum(1 for p in aero_pools if p.protocol.value == 'aerodrome_volatile')}")
    print(f"  aerodrome stable:     {sum(1 for p in aero_pools if p.protocol.value == 'aerodrome_stable')}")
    print(f"  aerodrome slipstream: {len(slip_pools)}")
    total_dt = uni_dt + aero_dt + slip_dt
    print(f"total wall time:    {total_dt:.0f}s ({total_dt / 60:.1f} min)")


if __name__ == "__main__":
    main()
