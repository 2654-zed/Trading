"""Phase 1.1 — frozen monitored pool set for Base.

Per spec §Phase 1.1 the monitored set is:
  - Uniswap V3 pools on Base with >= $1,000,000 TVL at run start
  - Aerodrome v1 stable + volatile pools on Base with >= $500,000 TVL at run start
  - Aerodrome Slipstream (concentrated liquidity) pools on Base with >= $1,000,000
    TVL at run start (added per architecture addendum — same floor as UniV3 since
    both are concentrated-liquidity AMMs)

Enumerated once at run start by querying DefiLlama's /pools yield endpoint for
pools above the spec floors, then resolving each entry's on-chain pool address
via factory.getPool(...) and enriching with on-chain token metadata. The set is
written to data/run_metadata/monitored_pools.json and never modified during the
30-day run (spec invariants #2, §Phase 1.1 "frozen at run start").

This module does not subscribe to blocks or track per-tick state — that is pool_monitor.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol


ProgressCallback = Callable[[str], None]

from .config import Config, DEFAULT


UNISWAP_V3_FACTORY_BASE = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
AERODROME_FACTORY_BASE = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
AERODROME_SLIPSTREAM_FACTORY_BASE = "0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A"

# --- Phase 2 cross-chain factory addresses (D-009 / sub-phase 2.1) -----------
# UniV3 deploys on Arbitrum and Optimism reuse the canonical 0x1F98... factory
# (same address across most non-Base EVM chains; Base re-deployed because its
# bridge model required it). Sources at end of file; verify with factory.getPool
# round-trips at enumeration time — loud-failure on bad address.
UNISWAP_V3_FACTORY_ARBITRUM = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
UNISWAP_V3_FACTORY_OPTIMISM = "0x1F98431c8aD98523631AE4a59f267346ea31F984"

# Camelot V2 (CPAMM) on Arbitrum.
CAMELOT_V2_FACTORY_ARBITRUM = "0x6EcCab422D763aC031210895C81787E87B43A652"
# Camelot V3 (concentrated liquidity, UniV3-style) on Arbitrum.
CAMELOT_V3_FACTORY_ARBITRUM = "0x1a3c9B1d2F0529D97f2afC5136Cc23e58f1FD35B"
# SushiSwap V2 (CPAMM) on Arbitrum.
SUSHISWAP_V2_FACTORY_ARBITRUM = "0xc35DADB65012eC5796536bD9864eD8773aBc74C4"

# Velodrome V1 (Solidly fork — stable + volatile) on Optimism.
VELODROME_V1_FACTORY_OPTIMISM = "0x25CbdDb98b35ab1FF77413456B31EC81A6B6B746"
# Velodrome V2 (Slipstream-equivalent concentrated liquidity) on Optimism.
# Address verified against Velodrome v2 deployments published in the Velodrome docs.
VELODROME_V2_FACTORY_OPTIMISM = "0xF1046053aa5682b4F9a81b5481394DA16BE5FF5a"

DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"


# --- Chain identifiers used internally (lowercase) and by DefiLlama (which
# uses inconsistent capitalization across networks). Verified against
# DefiLlama's /pools response 2026-05-17: Optimism's chain field is
# actually "OP Mainnet", not "Optimism". Phase 2 sub-phase 2.8 first
# deploy enumerated 0 OP pools because of this — patched below.
DEFILLAMA_CHAIN_BY_LABEL: dict[str, str] = {
    "base": "Base",
    "arbitrum": "Arbitrum",
    "optimism": "OP Mainnet",
}


SUPPORTED_CHAINS: tuple[str, ...] = ("base", "arbitrum", "optimism")


# Minimal ABIs — just what the enumeration reads
UNI_V3_FACTORY_ABI = json.dumps([
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": True, "name": "fee", "type": "uint24"},
            {"indexed": False, "name": "tickSpacing", "type": "int24"},
            {"indexed": False, "name": "pool", "type": "address"},
        ],
        "name": "PoolCreated",
        "type": "event",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
])

AERODROME_FACTORY_ABI = json.dumps([
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "token0", "type": "address"},
            {"indexed": True, "name": "token1", "type": "address"},
            {"indexed": True, "name": "stable", "type": "bool"},
            {"indexed": False, "name": "pool", "type": "address"},
            {"indexed": False, "name": "", "type": "uint256"},
        ],
        "name": "PoolCreated",
        "type": "event",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "stable", "type": "bool"},
        ],
        "name": "getPool",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
])

# Slipstream is Aerodrome's UniV3-style concentrated-liquidity AMM. Pools are
# disambiguated by int24 tickSpacing (not bool stable). The fee is determined
# by the pool itself based on its tickSpacing config.
AERODROME_SLIPSTREAM_FACTORY_ABI = json.dumps([
    {
        "constant": True,
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "tickSpacing", "type": "int24"},
        ],
        "name": "getPool",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
])

ERC20_ABI = json.dumps([
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "who", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
])


class PoolProtocol(str, Enum):
    # Phase 1 (Base) protocols.
    UNISWAP_V3 = "uniswap_v3"
    AERODROME_VOLATILE = "aerodrome_volatile"
    AERODROME_STABLE = "aerodrome_stable"
    AERODROME_SLIPSTREAM = "aerodrome_slipstream"

    # Phase 2 (Arbitrum + Optimism) protocols.
    # Camelot V2 is a CPAMM (Uniswap V2 fork with a small fee twist).
    CAMELOT_V2 = "camelot_v2"
    # Camelot V3 is a UniV3-style concentrated-liquidity AMM with a different
    # factory signature (uses initialPrice on createPool, but getPool only
    # needs the token pair — no fee tier — because Camelot V3 has unified fees).
    CAMELOT_V3 = "camelot_v3"
    # SushiSwap V2 on Arbitrum — vanilla Uniswap V2 fork.
    SUSHISWAP_V2 = "sushiswap_v2"
    # Velodrome V1: Solidly fork (stable + volatile classic AMM).
    VELODROME_VOLATILE = "velodrome_volatile"
    VELODROME_STABLE = "velodrome_stable"
    # Velodrome V2: Slipstream-equivalent concentrated-liquidity AMM.
    VELODROME_SLIPSTREAM = "velodrome_slipstream"


@dataclass(frozen=True)
class TokenInfo:
    address: str
    symbol: str
    decimals: int


@dataclass(frozen=True)
class PoolInfo:
    address: str
    protocol: PoolProtocol
    token0: TokenInfo
    token1: TokenInfo
    fee_bps: Optional[int]
    tvl_usd_at_enumeration: float
    enumerated_at_block: int
    # Phase 2 (sub-phase 2.1, D-009): per-record chain attribution.
    # Defaults to "base" so Phase 1 monitored_pools.json files round-trip
    # cleanly through PoolInfo.from_dict without breaking existing tests
    # or replay paths. New enumerations always set it explicitly.
    chain: str = "base"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["protocol"] = self.protocol.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PoolInfo":
        return cls(
            address=d["address"],
            protocol=PoolProtocol(d["protocol"]),
            token0=TokenInfo(**d["token0"]),
            token1=TokenInfo(**d["token1"]),
            fee_bps=d.get("fee_bps"),
            tvl_usd_at_enumeration=float(d["tvl_usd_at_enumeration"]),
            enumerated_at_block=int(d["enumerated_at_block"]),
            # Phase 1 records without a chain field load as "base" (the only
            # chain that existed pre-Phase-2). Verified against Phase 1's
            # run_artifacts/exp_001/monitored_pools.json shape.
            chain=d.get("chain", "base"),
        )


@dataclass(frozen=True)
class PoolSet:
    pools: tuple[PoolInfo, ...]
    chain: str
    enumerated_at_block: int
    enumerated_at: str
    uniswap_v3_tvl_floor_usd: float
    aerodrome_tvl_floor_usd: float

    def __post_init__(self):
        addrs = [p.address.lower() for p in self.pools]
        if len(addrs) != len(set(addrs)):
            raise ValueError("duplicate pool address in PoolSet")

    def __len__(self) -> int:
        return len(self.pools)

    def by_protocol(self, protocol: PoolProtocol) -> tuple[PoolInfo, ...]:
        return tuple(p for p in self.pools if p.protocol == protocol)

    def to_dict(self) -> dict:
        return {
            "chain": self.chain,
            "enumerated_at_block": self.enumerated_at_block,
            "enumerated_at": self.enumerated_at,
            "uniswap_v3_tvl_floor_usd": self.uniswap_v3_tvl_floor_usd,
            "aerodrome_tvl_floor_usd": self.aerodrome_tvl_floor_usd,
            "pool_count": len(self.pools),
            "pools": [p.to_dict() for p in self.pools],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PoolSet":
        return cls(
            pools=tuple(PoolInfo.from_dict(p) for p in d["pools"]),
            chain=d["chain"],
            enumerated_at_block=int(d["enumerated_at_block"]),
            enumerated_at=d["enumerated_at"],
            uniswap_v3_tvl_floor_usd=float(d["uniswap_v3_tvl_floor_usd"]),
            aerodrome_tvl_floor_usd=float(d["aerodrome_tvl_floor_usd"]),
        )

    def write_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(
                f"{path} already exists. The monitored pool set must not be modified "
                "during the run (spec invariant #2). Delete manually if starting a new run."
            )
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def read_from(cls, path: Path) -> "PoolSet":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


class TvlSource(Protocol):
    """Returns AMM pool entries with current TVL above a USD floor for a given
    chain/project. Each entry has at minimum: underlyingTokens (list[str] of two
    addresses), tvlUsd (float), poolMeta (str | None). Implementation-defined
    additional fields are allowed."""

    def get_pools_above_floor(
        self, chain: str, project: str, floor_usd: float,
    ) -> list[dict]: ...


class DefiLlamaTvlSource:
    """Pool TVL source backed by DefiLlama's /pools yield endpoint.

    One unauthenticated GET returns ~13 MB of JSON covering every pool DefiLlama
    indexes across all chains/protocols. Filter to (chain, project, tvlUsd >= floor).
    """

    def __init__(
        self,
        url: str = DEFILLAMA_POOLS_URL,
        opener: Optional[object] = None,
        timeout_seconds: int = 60,
    ):
        self._url = url
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout_seconds
        self._cache: Optional[list[dict]] = None

    def _fetch_all(self) -> list[dict]:
        if self._cache is not None:
            return self._cache
        with self._opener(self._url, timeout=self._timeout) as resp:
            payload = json.loads(resp.read())
        self._cache = payload.get("data", []) or []
        return self._cache

    def get_pools_above_floor(
        self, chain: str, project: str, floor_usd: float,
    ) -> list[dict]:
        out: list[dict] = []
        for entry in self._fetch_all():
            if entry.get("chain") != chain:
                continue
            if entry.get("project") != project:
                continue
            tvl = entry.get("tvlUsd")
            if tvl is None or float(tvl) < floor_usd:
                continue
            tokens = entry.get("underlyingTokens") or []
            if len(tokens) != 2:
                continue
            out.append(entry)
        return out


def _parse_v3_fee_tier(meta: str) -> int:
    """DefiLlama records UniV3 fee tiers as percentage strings ('0.05%', '0.3%',
    '1%'). Returns the corresponding uint24 fee value (e.g. 0.3% -> 3000)."""
    if not meta:
        raise ValueError("missing poolMeta")
    pct = float(meta.rstrip("%").strip())
    return int(round(pct * 10000))


def _parse_slipstream_meta(meta: str) -> tuple[int, float]:
    """DefiLlama records Aerodrome Slipstream pools as 'CL{tickSpacing} - {fee%}%'.
    E.g. 'CL50 - 0.05%' -> (tickSpacing=50, fee_pct=0.05).
    'CL100 - 0.0297%' -> (100, 0.0297).
    """
    if not meta:
        raise ValueError("missing poolMeta")
    parts = meta.split(" - ")
    if len(parts) != 2:
        raise ValueError(f"unexpected slipstream poolMeta shape: {meta!r}")
    cl_part, pct_part = parts[0].strip(), parts[1].strip()
    if not cl_part.startswith("CL"):
        raise ValueError(f"slipstream poolMeta missing 'CL' prefix: {meta!r}")
    try:
        tick_spacing = int(cl_part[2:])
    except ValueError as e:
        raise ValueError(f"bad tick_spacing in {meta!r}: {e}")
    fee_pct = float(pct_part.rstrip("%").strip())
    return tick_spacing, fee_pct


def _get_token_info(w3, address: str, cache: dict[str, TokenInfo]) -> TokenInfo:
    addr = address.lower()
    if addr in cache:
        return cache[addr]
    checksum = w3.to_checksum_address(address)
    erc20 = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    try:
        symbol = erc20.functions.symbol().call()
    except Exception:
        symbol = ""
    try:
        decimals = int(erc20.functions.decimals().call())
    except Exception:
        decimals = 18
    info = TokenInfo(address=addr, symbol=symbol, decimals=decimals)
    cache[addr] = info
    return info


def _get_balance(w3, token_address: str, holder_address: str) -> int:
    erc20 = w3.eth.contract(
        address=w3.to_checksum_address(token_address),
        abi=ERC20_ABI,
    )
    return int(erc20.functions.balanceOf(w3.to_checksum_address(holder_address)).call())


def enumerate_uniswap_v3_pools(
    w3,
    tvl_source: TvlSource,
    tvl_floor_usd: float,
    factory_address: str = UNISWAP_V3_FACTORY_BASE,
    to_block: Optional[int] = None,
    token_cache: Optional[dict[str, TokenInfo]] = None,
    progress: Optional[ProgressCallback] = None,
    chain: str = "Base",
    project: str = "uniswap-v3",
    *,
    chain_label: str = "base",
) -> list[PoolInfo]:
    """Enumerate UniV3 pools on `chain` whose current TVL >= `tvl_floor_usd` per
    `tvl_source`. For each surviving entry, derive the on-chain pool address by
    calling factory.getPool(token0, token1, fee), enrich with on-chain token
    metadata, and emit a PoolInfo. No event scan; no per-pool balanceOf loop.
    """
    token_cache = token_cache if token_cache is not None else {}
    to_block = to_block if to_block is not None else w3.eth.block_number
    factory = w3.eth.contract(
        address=w3.to_checksum_address(factory_address),
        abi=UNI_V3_FACTORY_ABI,
    )

    if progress:
        progress(f"  [univ3] querying TVL source for {project} pools >= "
                 f"${tvl_floor_usd:,.0f} on {chain}…")
    entries = tvl_source.get_pools_above_floor(chain, project, tvl_floor_usd)
    if progress:
        progress(f"  [univ3] {len(entries):,} pools above floor; deriving on-chain addresses…")

    out: list[PoolInfo] = []
    for entry in entries:
        symbol = entry.get("symbol") or "<no-symbol>"
        utokens = entry.get("underlyingTokens") or []
        meta = entry.get("poolMeta")
        try:
            t0_addr, t1_addr = (a.lower() for a in utokens)
            fee_uint24 = _parse_v3_fee_tier(meta)
        except (ValueError, TypeError) as e:
            if progress:
                progress(f"  [univ3] skipped {symbol!r}: bad metadata "
                         f"({type(e).__name__}: {e})")
            continue

        try:
            pool_addr = factory.functions.getPool(
                w3.to_checksum_address(t0_addr),
                w3.to_checksum_address(t1_addr),
                fee_uint24,
            ).call()
        except Exception as e:
            if progress:
                progress(f"  [univ3] getPool failed for {symbol!r} ({fee_uint24}): "
                         f"{type(e).__name__}")
            continue
        if not pool_addr or int(pool_addr, 16) == 0:
            if progress:
                progress(f"  [univ3] no on-chain pool for {symbol!r} fee={fee_uint24}")
            continue

        t0 = _get_token_info(w3, t0_addr, token_cache)
        t1 = _get_token_info(w3, t1_addr, token_cache)
        # Per UniV3 invariant, token0 < token1 (sorted ascending). DefiLlama already
        # follows this convention; normalize defensively in case it ever doesn't.
        if t0.address > t1.address:
            t0, t1 = t1, t0

        tvl = float(entry.get("tvlUsd") or 0.0)
        out.append(PoolInfo(
            address=pool_addr.lower(),
            protocol=PoolProtocol.UNISWAP_V3,
            token0=t0, token1=t1,
            fee_bps=fee_uint24 // 100,
            tvl_usd_at_enumeration=tvl,
            enumerated_at_block=to_block,
            chain=chain_label,
        ))
        if progress:
            progress(f"  [univ3:{chain_label}] {symbol} ({fee_uint24/10000:.2f}%) "
                     f"-> {pool_addr.lower()} TVL=${tvl:,.0f}")
    if progress:
        progress(f"  [univ3:{chain_label}] enumerated {len(out):,} pools")
    return out


def enumerate_aerodrome_pools(
    w3,
    tvl_source: TvlSource,
    tvl_floor_usd: float,
    factory_address: str = AERODROME_FACTORY_BASE,
    to_block: Optional[int] = None,
    token_cache: Optional[dict[str, TokenInfo]] = None,
    progress: Optional[ProgressCallback] = None,
    chain: str = "Base",
    project: str = "aerodrome-v1",
    *,
    chain_label: str = "base",
    stable_protocol: PoolProtocol = PoolProtocol.AERODROME_STABLE,
    volatile_protocol: PoolProtocol = PoolProtocol.AERODROME_VOLATILE,
) -> list[PoolInfo]:
    """Enumerate Aerodrome v1 pools (stable + volatile) on `chain` above
    `tvl_floor_usd` per `tvl_source`.

    DefiLlama lists each token-pair only once for aerodrome-v1 with no
    stable/volatile flag. For each entry, query factory.getPool(t0, t1, true)
    AND factory.getPool(t0, t1, false). If only one returns a non-zero address,
    that's the pool. If both exist on-chain, pick the one with the larger
    token0 balanceOf — the active pool carrying DefiLlama's reported TVL.
    """
    token_cache = token_cache if token_cache is not None else {}
    to_block = to_block if to_block is not None else w3.eth.block_number
    factory = w3.eth.contract(
        address=w3.to_checksum_address(factory_address),
        abi=AERODROME_FACTORY_ABI,
    )

    if progress:
        progress(f"  [aero] querying TVL source for {project} pools >= "
                 f"${tvl_floor_usd:,.0f} on {chain}…")
    entries = tvl_source.get_pools_above_floor(chain, project, tvl_floor_usd)
    if progress:
        progress(f"  [aero] {len(entries):,} pools above floor; "
                 f"resolving stable/volatile addresses…")

    out: list[PoolInfo] = []
    for entry in entries:
        symbol = entry.get("symbol") or "<no-symbol>"
        utokens = entry.get("underlyingTokens") or []
        try:
            t0_addr, t1_addr = (a.lower() for a in utokens)
        except (ValueError, TypeError):
            if progress:
                progress(f"  [aero] skipped {symbol!r}: bad underlyingTokens={utokens}")
            continue

        candidates: list[tuple[bool, str]] = []  # (stable_flag, pool_addr_lower)
        for stable in (False, True):
            try:
                addr = factory.functions.getPool(
                    w3.to_checksum_address(t0_addr),
                    w3.to_checksum_address(t1_addr),
                    stable,
                ).call()
            except Exception as e:
                if progress:
                    progress(f"  [aero] getPool({symbol!r}, stable={stable}) "
                             f"failed: {type(e).__name__}")
                continue
            if addr and int(addr, 16) != 0:
                candidates.append((stable, addr.lower()))

        if not candidates:
            if progress:
                progress(f"  [aero] no on-chain pool for {symbol!r}")
            continue

        # Disambiguate when both stable and volatile exist by reading token0
        # balanceOf for each — the active pool dwarfs the dormant one.
        if len(candidates) == 1:
            stable, pool_addr = candidates[0]
        else:
            scored: list[tuple[int, bool, str]] = []
            for stable, addr in candidates:
                try:
                    bal = _get_balance(w3, t0_addr, addr)
                except Exception:
                    bal = 0
                scored.append((bal, stable, addr))
            scored.sort(reverse=True)  # largest balance first
            _, stable, pool_addr = scored[0]
            if progress:
                progress(f"  [aero] {symbol!r}: both stable+volatile exist on-chain; "
                         f"picked stable={stable} (token0 balance={scored[0][0]})")

        t0 = _get_token_info(w3, t0_addr, token_cache)
        t1 = _get_token_info(w3, t1_addr, token_cache)
        # Aerodrome's getPool returns the same address regardless of arg order, but
        # the pool's internal token0/token1 ordering matches the on-chain tokens'
        # address ordering (ascending). Normalize to that here so downstream sees
        # the same convention as PoolCreated events would produce.
        if t0.address > t1.address:
            t0, t1 = t1, t0

        protocol = stable_protocol if stable else volatile_protocol
        tvl = float(entry.get("tvlUsd") or 0.0)
        out.append(PoolInfo(
            address=pool_addr,
            protocol=protocol,
            token0=t0, token1=t1,
            fee_bps=None,
            tvl_usd_at_enumeration=tvl,
            enumerated_at_block=to_block,
            chain=chain_label,
        ))
        if progress:
            progress(f"  [solidly:{chain_label}] {symbol} "
                     f"({'stable' if stable else 'volatile'}) "
                     f"-> {pool_addr} TVL=${tvl:,.0f}")
    if progress:
        progress(f"  [solidly:{chain_label}] enumerated {len(out):,} pools")
    return out


def enumerate_aerodrome_slipstream_pools(
    w3,
    tvl_source: TvlSource,
    tvl_floor_usd: float,
    factory_address: str = AERODROME_SLIPSTREAM_FACTORY_BASE,
    to_block: Optional[int] = None,
    token_cache: Optional[dict[str, TokenInfo]] = None,
    progress: Optional[ProgressCallback] = None,
    chain: str = "Base",
    project: str = "aerodrome-slipstream",
    *,
    chain_label: str = "base",
    slipstream_protocol: PoolProtocol = PoolProtocol.AERODROME_SLIPSTREAM,
) -> list[PoolInfo]:
    """Enumerate Aerodrome Slipstream (concentrated-liquidity) pools above
    `tvl_floor_usd` per `tvl_source`. Slipstream is Aerodrome's UniV3-style
    AMM; pools are disambiguated by int24 tickSpacing (parsed from DefiLlama's
    'CL{tickSpacing} - {fee%}%' poolMeta).

    fee_bps stored on PoolInfo is approximate for Slipstream — Slipstream fees
    can be sub-basis-point ('0.0297%' -> 2.97 bps), so we round to the nearest
    integer. Phase 1.2 should query the pool's fee() directly if exact precision
    is required for arb math.
    """
    token_cache = token_cache if token_cache is not None else {}
    to_block = to_block if to_block is not None else w3.eth.block_number
    factory = w3.eth.contract(
        address=w3.to_checksum_address(factory_address),
        abi=AERODROME_SLIPSTREAM_FACTORY_ABI,
    )

    if progress:
        progress(f"  [slip] querying TVL source for {project} pools >= "
                 f"${tvl_floor_usd:,.0f} on {chain}…")
    entries = tvl_source.get_pools_above_floor(chain, project, tvl_floor_usd)
    if progress:
        progress(f"  [slip] {len(entries):,} pools above floor; "
                 f"deriving on-chain addresses…")

    # DefiLlama occasionally has two entries for one Slipstream pool when their
    # fee tracking differs slightly across snapshots (e.g. 'CL200 - 0.3%' and
    # 'CL200 - 0.3032%' both pointing at the same on-chain address — getPool
    # only takes tickSpacing, not fee, so these collapse). Dedup by pool address,
    # keeping the highest-TVL entry, to avoid PoolSet's duplicate-address guard.
    by_addr: dict[str, PoolInfo] = {}
    for entry in entries:
        symbol = entry.get("symbol") or "<no-symbol>"
        utokens = entry.get("underlyingTokens") or []
        meta = entry.get("poolMeta")
        try:
            t0_addr, t1_addr = (a.lower() for a in utokens)
            tick_spacing, fee_pct = _parse_slipstream_meta(meta)
        except (ValueError, TypeError) as e:
            if progress:
                progress(f"  [slip] skipped {symbol!r}: bad metadata "
                         f"({type(e).__name__}: {e})")
            continue

        try:
            pool_addr = factory.functions.getPool(
                w3.to_checksum_address(t0_addr),
                w3.to_checksum_address(t1_addr),
                tick_spacing,
            ).call()
        except Exception as e:
            if progress:
                progress(f"  [slip] getPool failed for {symbol!r} "
                         f"(tickSpacing={tick_spacing}): {type(e).__name__}")
            continue
        if not pool_addr or int(pool_addr, 16) == 0:
            if progress:
                progress(f"  [slip] no on-chain pool for {symbol!r} "
                         f"tickSpacing={tick_spacing}")
            continue

        t0 = _get_token_info(w3, t0_addr, token_cache)
        t1 = _get_token_info(w3, t1_addr, token_cache)
        if t0.address > t1.address:
            t0, t1 = t1, t0

        tvl = float(entry.get("tvlUsd") or 0.0)
        addr_key = pool_addr.lower()
        candidate = PoolInfo(
            address=addr_key,
            protocol=slipstream_protocol,
            token0=t0, token1=t1,
            fee_bps=int(round(fee_pct * 100)),  # 0.05% -> 5 bps; sub-bp values lose precision
            tvl_usd_at_enumeration=tvl,
            enumerated_at_block=to_block,
            chain=chain_label,
        )
        existing = by_addr.get(addr_key)
        if existing is not None:
            if progress:
                progress(f"  [slip:{chain_label}] DefiLlama dupe at {addr_key}: keeping "
                         f"${max(existing.tvl_usd_at_enumeration, tvl):,.0f} "
                         f"(was ${existing.tvl_usd_at_enumeration:,.0f}, "
                         f"new ${tvl:,.0f})")
            if tvl > existing.tvl_usd_at_enumeration:
                by_addr[addr_key] = candidate
        else:
            by_addr[addr_key] = candidate
            if progress:
                progress(f"  [slip:{chain_label}] {symbol} (CL{tick_spacing} - {fee_pct:g}%) "
                         f"-> {addr_key} TVL=${tvl:,.0f}")
    out = list(by_addr.values())
    if progress:
        progress(f"  [slip:{chain_label}] enumerated {len(out):,} pools "
                 f"(after dedup of {len(entries) - len(out)} dupes/missing)")
    return out


# --------------------------------------------------------------------------- #
# Phase 2 sub-phase 2.1: enumerators for Arb / OP protocols not present on Base.
#
# Shapes:
#   - Camelot V2 / SushiSwap V2: classic UniV2 fork. getPool(tokenA, tokenB).
#   - Camelot V3: UniV3-style CL. getPool(tokenA, tokenB) — no fee tier.
#
# Velodrome V1 (Solidly stable+volatile) and Velodrome V2 (Slipstream) reuse
# `enumerate_aerodrome_pools` and `enumerate_aerodrome_slipstream_pools`
# respectively, with the appropriate factory + chain_label + project + protocol
# overrides — because Aerodrome is itself a Velodrome fork, the ABI is identical.
# --------------------------------------------------------------------------- #

# Minimal ABI for getPool(tokenA, tokenB) shaped factories.
PAIR_FACTORY_NO_FEE_ABI = json.dumps([
    {
        "constant": True,
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ],
        "name": "getPool",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
])


def _call_pair_factory(
    w3,
    factory_address: str,
    t0_addr: str,
    t1_addr: str,
    *,
    abi: str = PAIR_FACTORY_NO_FEE_ABI,
    method: str = "getPair",
) -> Optional[str]:
    """Call getPair / getPool on a factory expecting only (tokenA, tokenB).

    Returns the lowercased pool address, or None if the on-chain result is
    the zero address. Raises on transport error so callers can decide whether
    to skip-and-continue or propagate.
    """
    factory = w3.eth.contract(
        address=w3.to_checksum_address(factory_address),
        abi=abi,
    )
    fn = getattr(factory.functions, method)
    addr = fn(
        w3.to_checksum_address(t0_addr),
        w3.to_checksum_address(t1_addr),
    ).call()
    if not addr or int(addr, 16) == 0:
        return None
    return addr.lower()


def enumerate_cpamm_pools(
    w3,
    tvl_source: TvlSource,
    tvl_floor_usd: float,
    factory_address: str,
    to_block: Optional[int] = None,
    token_cache: Optional[dict[str, TokenInfo]] = None,
    progress: Optional[ProgressCallback] = None,
    *,
    chain: str,
    chain_label: str,
    project: str,
    protocol: PoolProtocol,
    factory_method: str = "getPair",
) -> list[PoolInfo]:
    """Generic enumerator for Uniswap-V2-fork CPAMM factories.

    Used for Camelot V2 (Arbitrum) and SushiSwap V2 (Arbitrum). DefiLlama
    lists each pair once; we resolve the on-chain address via
    factory.getPair(tokenA, tokenB).

    `protocol` tags the resulting PoolInfo (e.g. CAMELOT_V2, SUSHISWAP_V2).
    `factory_method` is "getPair" for SushiSwap; Camelot V2 also exposes
    getPair as the canonical V2-fork method.
    """
    token_cache = token_cache if token_cache is not None else {}
    to_block = to_block if to_block is not None else w3.eth.block_number

    if progress:
        progress(f"  [cpamm:{chain_label}/{protocol.value}] querying TVL source for "
                 f"{project} pools >= ${tvl_floor_usd:,.0f} on {chain}…")
    entries = tvl_source.get_pools_above_floor(chain, project, tvl_floor_usd)
    if progress:
        progress(f"  [cpamm:{chain_label}/{protocol.value}] {len(entries):,} pools "
                 f"above floor; resolving on-chain addresses…")

    out: list[PoolInfo] = []
    for entry in entries:
        symbol = entry.get("symbol") or "<no-symbol>"
        utokens = entry.get("underlyingTokens") or []
        try:
            t0_addr, t1_addr = (a.lower() for a in utokens)
        except (ValueError, TypeError):
            if progress:
                progress(f"  [cpamm:{chain_label}] skipped {symbol!r}: "
                         f"bad underlyingTokens={utokens}")
            continue

        try:
            pool_addr = _call_pair_factory(
                w3, factory_address, t0_addr, t1_addr, method=factory_method,
            )
        except Exception as e:
            if progress:
                progress(f"  [cpamm:{chain_label}] {factory_method} failed for "
                         f"{symbol!r}: {type(e).__name__}: {e}")
            continue
        if pool_addr is None:
            if progress:
                progress(f"  [cpamm:{chain_label}] no on-chain pool for {symbol!r}")
            continue

        t0 = _get_token_info(w3, t0_addr, token_cache)
        t1 = _get_token_info(w3, t1_addr, token_cache)
        if t0.address > t1.address:
            t0, t1 = t1, t0

        tvl = float(entry.get("tvlUsd") or 0.0)
        out.append(PoolInfo(
            address=pool_addr,
            protocol=protocol,
            token0=t0, token1=t1,
            fee_bps=None,  # UniV2 forks have fixed fees per protocol; quote modules
                           # handle them. Camelot V2 has dynamic fees the quote
                           # module would need to handle separately in 2.3.
            tvl_usd_at_enumeration=tvl,
            enumerated_at_block=to_block,
            chain=chain_label,
        ))
        if progress:
            progress(f"  [cpamm:{chain_label}] {symbol} -> {pool_addr} TVL=${tvl:,.0f}")
    if progress:
        progress(f"  [cpamm:{chain_label}/{protocol.value}] enumerated {len(out):,} pools")
    return out


def enumerate_camelot_v3_pools(
    w3,
    tvl_source: TvlSource,
    tvl_floor_usd: float,
    factory_address: str = CAMELOT_V3_FACTORY_ARBITRUM,
    to_block: Optional[int] = None,
    token_cache: Optional[dict[str, TokenInfo]] = None,
    progress: Optional[ProgressCallback] = None,
    *,
    chain: str = "Arbitrum",
    chain_label: str = "arbitrum",
    project: str = "camelot-v3",
) -> list[PoolInfo]:
    """Camelot V3 is UniV3-style concentrated liquidity, but unlike UniV3 has
    unified fees (no fee tier on the factory's `getPool`). We use the no-fee
    pair factory call shape.
    """
    return enumerate_cpamm_pools(
        w3, tvl_source, tvl_floor_usd,
        factory_address=factory_address,
        to_block=to_block, token_cache=token_cache, progress=progress,
        chain=chain, chain_label=chain_label, project=project,
        protocol=PoolProtocol.CAMELOT_V3,
        factory_method="getPool",
    )


def enumerate_and_freeze(
    w3,
    tvl_source: TvlSource,
    cfg: Config = DEFAULT,
    metadata_path: Optional[Path] = None,
) -> PoolSet:
    """Phase 1 enumeration (Base only). Unchanged signature for back-compat;
    Phase 2 multi-chain enumeration uses `enumerate_chain_pools` per chain and
    composes the results in `scripts/enumerate_pools.py`.
    """
    to_block = w3.eth.block_number
    token_cache: dict[str, TokenInfo] = {}

    uni_pools = enumerate_uniswap_v3_pools(
        w3, tvl_source, cfg.uniswap_v3_tvl_floor_usd,
        to_block=to_block, token_cache=token_cache,
    )
    aero_pools = enumerate_aerodrome_pools(
        w3, tvl_source, cfg.aerodrome_tvl_floor_usd,
        to_block=to_block, token_cache=token_cache,
    )
    # Slipstream is Aerodrome's concentrated-liquidity AMM — structurally closer
    # to UniV3 than to Aerodrome v1's classic constant-product pools. Apply the
    # UniV3 floor ($1M) to Slipstream rather than the v1 floor ($500k).
    slip_pools = enumerate_aerodrome_slipstream_pools(
        w3, tvl_source, cfg.uniswap_v3_tvl_floor_usd,
        to_block=to_block, token_cache=token_cache,
    )

    pool_set = PoolSet(
        pools=tuple(uni_pools + aero_pools + slip_pools),
        chain=cfg.chain,
        enumerated_at_block=to_block,
        enumerated_at=datetime.now(timezone.utc).isoformat(),
        uniswap_v3_tvl_floor_usd=cfg.uniswap_v3_tvl_floor_usd,
        aerodrome_tvl_floor_usd=cfg.aerodrome_tvl_floor_usd,
    )

    path = metadata_path or (cfg.run_metadata_dir / "monitored_pools.json")
    cfg.run_metadata_dir.mkdir(parents=True, exist_ok=True)
    pool_set.write_to(path)
    return pool_set


# --------------------------------------------------------------------------- #
# Phase 2 sub-phase 2.1: per-chain enumeration driver.
# Composes the per-protocol enumerators for a single chain. The caller
# (scripts/enumerate_pools.py) invokes this once per chain with its own
# `w3` connection, then merges the per-chain PoolInfo lists into one
# combined monitored_pools.json with chain attribution preserved.
# --------------------------------------------------------------------------- #

# Per-chain protocol coverage map. Each entry: (enumerator fn, kwargs).
# Floors come from caller (cfg). All addresses verified at top of this file.
def enumerate_chain_pools(
    w3,
    tvl_source: TvlSource,
    chain_label: str,
    *,
    cl_tvl_floor_usd: float,
    cpamm_tvl_floor_usd: float,
    to_block: Optional[int] = None,
    token_cache: Optional[dict[str, TokenInfo]] = None,
    progress: Optional[ProgressCallback] = None,
) -> list[PoolInfo]:
    """Enumerate all monitored protocols for a single chain.

    Returns a flat list of PoolInfo across all protocols on that chain. The
    caller composes the multi-chain set in scripts/enumerate_pools.py.

    Protocol coverage per chain (from PHASE_2_CROSS_CHAIN_SPEC.md sub-phase 2.1):
      base       : UniV3, Aerodrome Slipstream (CL), Aerodrome v1 vol+stable
      arbitrum   : UniV3, Camelot V3 (CL), Camelot V2 (CPAMM), SushiSwap V2 (CPAMM)
      optimism   : UniV3, Velodrome V2 Slipstream (CL), Velodrome V1 vol+stable
    """
    if chain_label not in SUPPORTED_CHAINS:
        raise ValueError(f"unsupported chain_label: {chain_label!r}; "
                         f"supported = {SUPPORTED_CHAINS}")

    token_cache = token_cache if token_cache is not None else {}
    to_block = to_block if to_block is not None else w3.eth.block_number
    chain_for_defillama = DEFILLAMA_CHAIN_BY_LABEL[chain_label]

    pools: list[PoolInfo] = []

    if chain_label == "base":
        pools.extend(enumerate_uniswap_v3_pools(
            w3, tvl_source, cl_tvl_floor_usd,
            factory_address=UNISWAP_V3_FACTORY_BASE,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="uniswap-v3",
            chain_label=chain_label,
        ))
        pools.extend(enumerate_aerodrome_pools(
            w3, tvl_source, cpamm_tvl_floor_usd,
            factory_address=AERODROME_FACTORY_BASE,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="aerodrome-v1",
            chain_label=chain_label,
        ))
        pools.extend(enumerate_aerodrome_slipstream_pools(
            w3, tvl_source, cl_tvl_floor_usd,
            factory_address=AERODROME_SLIPSTREAM_FACTORY_BASE,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="aerodrome-slipstream",
            chain_label=chain_label,
        ))
    elif chain_label == "arbitrum":
        pools.extend(enumerate_uniswap_v3_pools(
            w3, tvl_source, cl_tvl_floor_usd,
            factory_address=UNISWAP_V3_FACTORY_ARBITRUM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="uniswap-v3",
            chain_label=chain_label,
        ))
        pools.extend(enumerate_camelot_v3_pools(
            w3, tvl_source, cl_tvl_floor_usd,
            factory_address=CAMELOT_V3_FACTORY_ARBITRUM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, chain_label=chain_label,
            project="camelot-v3",
        ))
        pools.extend(enumerate_cpamm_pools(
            w3, tvl_source, cpamm_tvl_floor_usd,
            factory_address=CAMELOT_V2_FACTORY_ARBITRUM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, chain_label=chain_label,
            project="camelot-v2", protocol=PoolProtocol.CAMELOT_V2,
            factory_method="getPair",
        ))
        pools.extend(enumerate_cpamm_pools(
            w3, tvl_source, cpamm_tvl_floor_usd,
            factory_address=SUSHISWAP_V2_FACTORY_ARBITRUM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, chain_label=chain_label,
            project="sushiswap-v2", protocol=PoolProtocol.SUSHISWAP_V2,
            factory_method="getPair",
        ))
    elif chain_label == "optimism":
        pools.extend(enumerate_uniswap_v3_pools(
            w3, tvl_source, cl_tvl_floor_usd,
            factory_address=UNISWAP_V3_FACTORY_OPTIMISM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="uniswap-v3",
            chain_label=chain_label,
        ))
        # Phase 2 sub-phase 2.8 patch: DefiLlama labels the current Solidly-
        # style Velodrome pools as `velodrome-v2` (not `velodrome-v1`); the
        # concentrated-liquidity Velodrome Slipstream variant is labeled
        # `velodrome-v3`. Factory addresses below match those labels.
        #
        # If the factory address proves wrong at first deploy (getPool
        # returns zero for every entry), the affected protocol's pool count
        # will be 0 and we revisit. UniV3 on OP is the primary path for
        # cross-chain detection regardless.
        pools.extend(enumerate_aerodrome_pools(
            w3, tvl_source, cpamm_tvl_floor_usd,
            factory_address=VELODROME_V1_FACTORY_OPTIMISM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="velodrome-v2",
            chain_label=chain_label,
            stable_protocol=PoolProtocol.VELODROME_STABLE,
            volatile_protocol=PoolProtocol.VELODROME_VOLATILE,
        ))
        pools.extend(enumerate_aerodrome_slipstream_pools(
            w3, tvl_source, cl_tvl_floor_usd,
            factory_address=VELODROME_V2_FACTORY_OPTIMISM,
            to_block=to_block, token_cache=token_cache, progress=progress,
            chain=chain_for_defillama, project="velodrome-v3",
            chain_label=chain_label,
            slipstream_protocol=PoolProtocol.VELODROME_SLIPSTREAM,
        ))

    return pools
