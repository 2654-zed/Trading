"""Phase 2 sub-phase 2.1 — multi-chain live enumeration driver.

Replaces the Phase-1-only `enumerate_live.py` (which stays in-tree for back-
compat with Phase 1 reruns). Reads per-chain RPC URLs from config, queries
DefiLlama for pools above the spec TVL floors on each requested chain,
resolves each pool's on-chain address via the appropriate factory call, and
writes a SINGLE `monitored_pools.json` with per-pool `chain` attribution.

Run from the experiment root:
    # All three chains (default)
    python -m layer3_trading_exp.scripts.enumerate_pools

    # Single chain
    python -m layer3_trading_exp.scripts.enumerate_pools --chain base
    python -m layer3_trading_exp.scripts.enumerate_pools --chain arbitrum
    python -m layer3_trading_exp.scripts.enumerate_pools --chain optimism

    # Explicit "all"
    python -m layer3_trading_exp.scripts.enumerate_pools --chain all

Per spec invariant #2: this is a one-shot. Refuses to overwrite
`monitored_pools.json` if it exists. Delete manually if starting fresh.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# Progress messages may contain Unicode; Windows cp1252 redirected stdout
# can't encode them and crashes mid-run. Force UTF-8 explicitly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from web3 import Web3

from ..config import DEFAULT, Config
from ..pool_set import (
    DEFILLAMA_CHAIN_BY_LABEL,
    SUPPORTED_CHAINS,
    DefiLlamaTvlSource,
    PoolInfo,
    PoolProtocol,
    PoolSet,
    TokenInfo,
    enumerate_chain_pools,
)


# Per-chain RPC URL env vars. We accept BOTH the legacy Phase-1 vars (e.g.
# BASE_RPC_URL) and Phase 2 multi-chain vars so the same .env still works.
RPC_ENV_VARS_BY_CHAIN: dict[str, tuple[str, ...]] = {
    "base":     ("BASE_RPC_URL",),
    "arbitrum": ("ARB_RPC_URL", "ARBITRUM_RPC_URL"),
    "optimism": ("OP_RPC_URL", "OPTIMISM_RPC_URL"),
}


def _rpc_url_for(chain_label: str) -> str:
    candidates = RPC_ENV_VARS_BY_CHAIN[chain_label]
    for key in candidates:
        v = os.environ.get(key)
        if v:
            return v
    raise SystemExit(
        f"Missing RPC URL for chain {chain_label!r}. Set one of: "
        f"{', '.join(candidates)} as an env var or in .env."
    )


def _connect(chain_label: str) -> Web3:
    url = _rpc_url_for(chain_label)
    w3 = Web3(Web3.HTTPProvider(url))
    # Loud failure: verify reachability before consuming DefiLlama.
    _ = w3.eth.block_number
    return w3


def _print_summary_by_protocol(chain_label: str, pools: list[PoolInfo]) -> None:
    by_proto: dict[str, int] = {}
    for p in pools:
        by_proto[p.protocol.value] = by_proto.get(p.protocol.value, 0) + 1
    for proto in sorted(by_proto):
        print(f"    {proto:30s} {by_proto[proto]:>5d}")
    print(f"    {'TOTAL':30s} {len(pools):>5d}")


def _make_progress() -> "object":
    def progress(msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)
    return progress


def main() -> None:
    ap = argparse.ArgumentParser(description="Enumerate the monitored pool set "
                                             "across one or all chains.")
    ap.add_argument("--chain", choices=("base", "arbitrum", "optimism", "all"),
                    default="all", help="Which chain(s) to enumerate. Default: all")
    ap.add_argument("--output", default=None,
                    help="Override output path. Default: <run_metadata_dir>/monitored_pools.json")
    ap.add_argument("--cl-floor-usd", type=float, default=None,
                    help="Override concentrated-liquidity TVL floor (UniV3 + Slipstream "
                         "variants). Default from config.uniswap_v3_tvl_floor_usd.")
    ap.add_argument("--cpamm-floor-usd", type=float, default=None,
                    help="Override CPAMM / Solidly TVL floor. Default from "
                         "config.aerodrome_tvl_floor_usd.")
    args = ap.parse_args()

    cfg: Config = DEFAULT
    cl_floor = args.cl_floor_usd if args.cl_floor_usd is not None else cfg.uniswap_v3_tvl_floor_usd
    cpamm_floor = args.cpamm_floor_usd if args.cpamm_floor_usd is not None else cfg.aerodrome_tvl_floor_usd

    chains = SUPPORTED_CHAINS if args.chain == "all" else (args.chain,)

    if args.output:
        out_path = type(cfg.run_metadata_dir)(args.output)
    else:
        out_path = cfg.run_metadata_dir / "monitored_pools.json"

    if out_path.exists():
        raise SystemExit(
            f"{out_path} already exists. Refusing to re-enumerate "
            f"(spec invariant #2). Delete manually if starting a new run."
        )

    cfg.run_metadata_dir.mkdir(parents=True, exist_ok=True)

    print(f"chains to enumerate     : {', '.join(chains)}")
    print(f"CL floor (UniV3/Slip)   : ${cl_floor:,.0f}")
    print(f"CPAMM/Solidly floor     : ${cpamm_floor:,.0f}")
    print(f"output                  : {out_path}")
    print(f"tvl source              : DefiLlama /pools")
    print()

    # One DefiLlama source instance shared across chains — saves 13 MB per
    # chain on the cached _fetch_all() call.
    tvl_source = DefiLlamaTvlSource()
    # One token cache shared across chains — though tokens have different
    # addresses per chain, a couple (like the multichain Multicall3 etc.) may
    # repeat. Cheap to share, no correctness risk because the cache keys are
    # lowercased addresses which are chain-keyed by definition.
    token_cache: dict[str, TokenInfo] = {}

    progress = _make_progress()
    all_pools: list[PoolInfo] = []
    enumerated_at_block_by_chain: dict[str, int] = {}
    per_chain_timing: dict[str, float] = {}

    for chain_label in chains:
        print(f"== {chain_label} ==", flush=True)
        t0 = time.perf_counter()
        try:
            w3 = _connect(chain_label)
            head_block = w3.eth.block_number
            print(f"  connected, head_block={head_block:,}")
            chain_pools = enumerate_chain_pools(
                w3, tvl_source, chain_label,
                cl_tvl_floor_usd=cl_floor,
                cpamm_tvl_floor_usd=cpamm_floor,
                to_block=head_block,
                token_cache=token_cache,
                progress=progress,
            )
        except Exception as exc:
            # Loud failure: per I-6 we surface the error rather than silently
            # producing a partial pool set.
            print(f"  [ERROR] enumeration failed for {chain_label}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            raise

        per_chain_timing[chain_label] = time.perf_counter() - t0
        enumerated_at_block_by_chain[chain_label] = head_block
        print(f"  per-protocol counts on {chain_label}:")
        _print_summary_by_protocol(chain_label, chain_pools)
        print(f"  wall time: {per_chain_timing[chain_label]:.0f}s "
              f"({per_chain_timing[chain_label] / 60:.1f} min)")
        all_pools.extend(chain_pools)
        print()

    # Cross-chain duplicate check: addresses CAN collide across chains
    # (CREATE2 deploys), so duplicate-detection in PoolSet uses lowercased
    # addresses globally. If a collision happens, fail loudly.
    seen: dict[str, str] = {}  # addr -> chain that first claimed it
    for p in all_pools:
        prior = seen.get(p.address)
        if prior is not None and prior != p.chain:
            print(f"[WARN] address {p.address} appears on both {prior!r} and "
                  f"{p.chain!r} — PoolSet duplicate guard will reject. "
                  f"Investigate before retrying.", file=sys.stderr, flush=True)
        seen[p.address] = p.chain

    # PoolSet.chain is "all" when multi-chain (per-record chain field is the
    # operational source of truth; the bag-level chain becomes informational).
    bag_chain = chains[0] if len(chains) == 1 else "all"
    bag_block = (enumerated_at_block_by_chain[chains[0]]
                 if len(chains) == 1
                 else max(enumerated_at_block_by_chain.values()))

    pool_set = PoolSet(
        pools=tuple(all_pools),
        chain=bag_chain,
        enumerated_at_block=bag_block,
        enumerated_at=datetime.now(timezone.utc).isoformat(),
        uniswap_v3_tvl_floor_usd=cl_floor,
        aerodrome_tvl_floor_usd=cpamm_floor,
    )
    pool_set.write_to(out_path)

    print(f"wrote {out_path}")
    print(f"total pools across all chains: {len(pool_set)}")
    for chain_label in chains:
        chain_count = sum(1 for p in all_pools if p.chain == chain_label)
        print(f"  {chain_label:10s}: {chain_count}")
    total_time = sum(per_chain_timing.values())
    print(f"total wall time: {total_time:.0f}s ({total_time / 60:.1f} min)")


if __name__ == "__main__":
    main()
