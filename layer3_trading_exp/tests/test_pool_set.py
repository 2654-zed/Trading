"""Unit tests for pool_set.py.

Focus: data-structure invariants (immutability, duplicate detection, JSON roundtrip),
DefiLlama TVL-source parsing, and pool-meta parsers. Full enumeration with live
factory.getPool calls is exercised in the Phase 1.1 acceptance run, not unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer3_trading_exp import pool_set as pset
from layer3_trading_exp.pool_set import (
    DEFILLAMA_CHAIN_BY_LABEL,
    SUPPORTED_CHAINS,
    DefiLlamaTvlSource,
    PoolInfo,
    PoolProtocol,
    PoolSet,
    TokenInfo,
    _parse_slipstream_meta,
    _parse_v3_fee_tier,
    enumerate_chain_pools,
)


def _token(addr: str, sym: str = "TKN", dec: int = 18) -> TokenInfo:
    return TokenInfo(address=addr.lower(), symbol=sym, decimals=dec)


def _uni_pool(addr: str, tvl: float) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(),
        protocol=PoolProtocol.UNISWAP_V3,
        token0=_token("0xaaa", "WETH"),
        token1=_token("0xbbb", "USDC", 6),
        fee_bps=5,
        tvl_usd_at_enumeration=tvl,
        enumerated_at_block=12_000_000,
    )


def _aero_pool(addr: str, tvl: float, stable: bool = False) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(),
        protocol=PoolProtocol.AERODROME_STABLE if stable else PoolProtocol.AERODROME_VOLATILE,
        token0=_token("0xccc", "DAI"),
        token1=_token("0xddd", "USDC", 6),
        fee_bps=None,
        tvl_usd_at_enumeration=tvl,
        enumerated_at_block=12_000_000,
    )


def _set(pools) -> PoolSet:
    return PoolSet(
        pools=tuple(pools),
        chain="base",
        enumerated_at_block=12_000_000,
        enumerated_at="2026-04-21T12:00:00+00:00",
        uniswap_v3_tvl_floor_usd=1_000_000.0,
        aerodrome_tvl_floor_usd=500_000.0,
    )


def test_pool_set_rejects_duplicate_addresses():
    with pytest.raises(ValueError, match="duplicate"):
        _set([_uni_pool("0x1111", 2_000_000), _uni_pool("0x1111", 3_000_000)])


def test_pool_set_duplicate_detection_is_case_insensitive():
    with pytest.raises(ValueError):
        PoolSet(
            pools=(_uni_pool("0x1111", 2_000_000), _uni_pool("0X1111", 2_000_000)),
            chain="base",
            enumerated_at_block=1, enumerated_at="t",
            uniswap_v3_tvl_floor_usd=1, aerodrome_tvl_floor_usd=1,
        )


def test_pool_set_len_and_by_protocol():
    s = _set([_uni_pool("0x1111", 2e6), _aero_pool("0x2222", 6e5), _aero_pool("0x3333", 8e5, stable=True)])
    assert len(s) == 3
    assert len(s.by_protocol(PoolProtocol.UNISWAP_V3)) == 1
    assert len(s.by_protocol(PoolProtocol.AERODROME_VOLATILE)) == 1
    assert len(s.by_protocol(PoolProtocol.AERODROME_STABLE)) == 1


def test_pool_set_json_roundtrip(tmp_path):
    s = _set([_uni_pool("0x1111", 2e6), _aero_pool("0x2222", 6e5)])
    path = tmp_path / "monitored_pools.json"
    s.write_to(path)
    loaded = PoolSet.read_from(path)
    assert loaded == s


def test_write_refuses_to_overwrite(tmp_path):
    s = _set([_uni_pool("0x1111", 2e6)])
    path = tmp_path / "monitored_pools.json"
    s.write_to(path)
    with pytest.raises(FileExistsError, match="must not be modified"):
        s.write_to(path)


def test_pool_info_frozen():
    p = _uni_pool("0x1111", 1e6)
    with pytest.raises(Exception):
        p.tvl_usd_at_enumeration = 2.0  # type: ignore[misc]


class _FakeResponse:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener_returning(payload: dict):
    def _opener(url, timeout):
        return _FakeResponse(payload)
    return _opener


def test_parse_v3_fee_tier_handles_common_values():
    assert _parse_v3_fee_tier("0.01%") == 100
    assert _parse_v3_fee_tier("0.05%") == 500
    assert _parse_v3_fee_tier("0.3%") == 3000
    assert _parse_v3_fee_tier("1%") == 10000


def test_parse_v3_fee_tier_rejects_empty():
    with pytest.raises(ValueError, match="missing"):
        _parse_v3_fee_tier("")


def test_parse_slipstream_meta_handles_common_values():
    # Standard tick spacings
    assert _parse_slipstream_meta("CL50 - 0.05%") == (50, 0.05)
    assert _parse_slipstream_meta("CL100 - 0.026%") == (100, 0.026)
    assert _parse_slipstream_meta("CL1 - 0.0085%") == (1, 0.0085)
    assert _parse_slipstream_meta("CL2000 - 1%") == (2000, 1.0)


def test_parse_slipstream_meta_rejects_malformed():
    with pytest.raises(ValueError, match="missing"):
        _parse_slipstream_meta("")
    with pytest.raises(ValueError, match="shape"):
        _parse_slipstream_meta("CL50 0.05%")
    with pytest.raises(ValueError, match="prefix"):
        _parse_slipstream_meta("50 - 0.05%")
    with pytest.raises(ValueError, match="tick_spacing"):
        _parse_slipstream_meta("CLfoo - 0.05%")


def test_defillama_tvl_source_filters_chain_project_and_floor():
    """DefiLlamaTvlSource should keep only entries matching chain+project AND
    above the TVL floor AND with exactly two underlyingTokens."""
    payload = {
        "data": [
            {  # match
                "chain": "Base", "project": "uniswap-v3",
                "tvlUsd": 5_000_000, "poolMeta": "0.3%",
                "underlyingTokens": ["0xaaa", "0xbbb"],
                "symbol": "WETH-USDC",
            },
            {  # below floor
                "chain": "Base", "project": "uniswap-v3",
                "tvlUsd": 500_000, "poolMeta": "0.05%",
                "underlyingTokens": ["0xccc", "0xddd"],
            },
            {  # wrong chain
                "chain": "Ethereum", "project": "uniswap-v3",
                "tvlUsd": 99_000_000, "poolMeta": "0.3%",
                "underlyingTokens": ["0xeee", "0xfff"],
            },
            {  # wrong project
                "chain": "Base", "project": "uniswap-v2",
                "tvlUsd": 5_000_000, "poolMeta": None,
                "underlyingTokens": ["0xggg", "0xhhh"],
            },
            {  # missing token (single-sided staking pool, etc.)
                "chain": "Base", "project": "uniswap-v3",
                "tvlUsd": 5_000_000, "poolMeta": "0.3%",
                "underlyingTokens": ["0xiii"],
            },
            {  # match — second valid entry
                "chain": "Base", "project": "uniswap-v3",
                "tvlUsd": 1_500_000, "poolMeta": "1%",
                "underlyingTokens": ["0xjjj", "0xkkk"],
                "symbol": "FOO-BAR",
            },
        ],
    }
    src = DefiLlamaTvlSource(opener=_opener_returning(payload))
    out = src.get_pools_above_floor("Base", "uniswap-v3", 1_000_000)
    syms = sorted(e.get("symbol", "") for e in out)
    assert syms == ["FOO-BAR", "WETH-USDC"]


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.1 (D-009): chain attribution + cross-chain enumeration.
# These tests are unit-level only — live factory.getPool round-trips are
# exercised in the sub-phase 2.1 acceptance run.
# ----------------------------------------------------------------------------


def test_pool_info_chain_defaults_to_base_for_back_compat():
    """Phase 1 callsites construct PoolInfo without `chain`; the default
    keeps them working unchanged."""
    p = PoolInfo(
        address="0xabc",
        protocol=PoolProtocol.UNISWAP_V3,
        token0=_token("0xaaa"),
        token1=_token("0xbbb"),
        fee_bps=5,
        tvl_usd_at_enumeration=1e6,
        enumerated_at_block=1,
    )
    assert p.chain == "base"


def test_pool_info_from_dict_accepts_records_without_chain():
    """Phase 1 monitored_pools.json files have no `chain` per-record field.
    PoolInfo.from_dict must load them as chain='base' without raising."""
    legacy_record = {
        "address": "0xdead",
        "protocol": "uniswap_v3",
        "token0": {"address": "0xaaa", "symbol": "A", "decimals": 18},
        "token1": {"address": "0xbbb", "symbol": "B", "decimals": 6},
        "fee_bps": 5,
        "tvl_usd_at_enumeration": 2_000_000.0,
        "enumerated_at_block": 45_900_000,
    }
    p = PoolInfo.from_dict(legacy_record)
    assert p.chain == "base"
    assert p.address == "0xdead"


def test_pool_info_from_dict_preserves_explicit_chain():
    """Phase 2 records carry an explicit `chain` field per record."""
    record = {
        "address": "0xbeef",
        "protocol": "camelot_v3",
        "token0": {"address": "0xaaa", "symbol": "A", "decimals": 18},
        "token1": {"address": "0xbbb", "symbol": "B", "decimals": 6},
        "fee_bps": 5,
        "tvl_usd_at_enumeration": 1_000_000.0,
        "enumerated_at_block": 200_000_000,
        "chain": "arbitrum",
    }
    p = PoolInfo.from_dict(record)
    assert p.chain == "arbitrum"
    assert p.protocol == PoolProtocol.CAMELOT_V3


def test_pool_info_json_roundtrip_preserves_chain(tmp_path):
    """Serializing a multi-chain PoolSet and reading it back must preserve
    each pool's chain attribution exactly."""
    pools = [
        PoolInfo(
            address="0xbase",
            protocol=PoolProtocol.UNISWAP_V3,
            token0=_token("0xaaa"), token1=_token("0xbbb"),
            fee_bps=5, tvl_usd_at_enumeration=1e6, enumerated_at_block=1,
            chain="base",
        ),
        PoolInfo(
            address="0xarb",
            protocol=PoolProtocol.CAMELOT_V3,
            token0=_token("0xccc"), token1=_token("0xddd"),
            fee_bps=None, tvl_usd_at_enumeration=2e6, enumerated_at_block=1,
            chain="arbitrum",
        ),
        PoolInfo(
            address="0xop",
            protocol=PoolProtocol.VELODROME_SLIPSTREAM,
            token0=_token("0xeee"), token1=_token("0xfff"),
            fee_bps=5, tvl_usd_at_enumeration=3e6, enumerated_at_block=1,
            chain="optimism",
        ),
    ]
    s = PoolSet(
        pools=tuple(pools), chain="all",
        enumerated_at_block=1, enumerated_at="t",
        uniswap_v3_tvl_floor_usd=500_000.0,
        aerodrome_tvl_floor_usd=250_000.0,
    )
    path = tmp_path / "monitored_pools.json"
    s.write_to(path)
    loaded = PoolSet.read_from(path)
    assert {p.chain for p in loaded.pools} == {"base", "arbitrum", "optimism"}
    assert loaded == s


def test_supported_chains_is_exactly_the_three():
    assert SUPPORTED_CHAINS == ("base", "arbitrum", "optimism")


def test_defillama_chain_mapping_uses_verified_labels():
    """DefiLlama uses inconsistent chain labels. Verified against the
    live /pools response 2026-05-17: Optimism is "OP Mainnet", not
    "Optimism". Phase 2 sub-phase 2.8 first deploy uncovered this."""
    assert DEFILLAMA_CHAIN_BY_LABEL["base"] == "Base"
    assert DEFILLAMA_CHAIN_BY_LABEL["arbitrum"] == "Arbitrum"
    assert DEFILLAMA_CHAIN_BY_LABEL["optimism"] == "OP Mainnet"


def test_new_phase2_protocols_present_in_enum():
    """The PoolProtocol enum must enumerate every Phase 2 protocol added
    in sub-phase 2.1 — guards against accidental enum-value typos that
    would break PoolInfo.from_dict for cross-chain records."""
    expected_new = {
        "camelot_v2", "camelot_v3", "sushiswap_v2",
        "velodrome_volatile", "velodrome_stable", "velodrome_slipstream",
    }
    actual = {p.value for p in PoolProtocol}
    missing = expected_new - actual
    assert not missing, f"missing Phase 2 protocols in enum: {missing}"


def test_enumerate_chain_pools_rejects_unsupported_chain():
    """Loud failure (per I-6) when caller passes a chain we don't support."""
    with pytest.raises(ValueError, match="unsupported chain_label"):
        # We don't need a real w3 because the check fires first.
        enumerate_chain_pools(
            w3=None, tvl_source=None, chain_label="ethereum",
            cl_tvl_floor_usd=1e6, cpamm_tvl_floor_usd=5e5,
        )


def test_pool_set_rejects_duplicate_across_chains():
    """If the same address appears on two chains, PoolSet's duplicate guard
    fires. (Real CREATE2 collisions are exceedingly rare but possible; the
    enumeration script warns separately, and PoolSet rejects the merged set
    to prevent silent data corruption.)"""
    p1 = PoolInfo(
        address="0xshared",
        protocol=PoolProtocol.UNISWAP_V3,
        token0=_token("0xaaa"), token1=_token("0xbbb"),
        fee_bps=5, tvl_usd_at_enumeration=1e6, enumerated_at_block=1,
        chain="base",
    )
    p2 = PoolInfo(
        address="0xshared",
        protocol=PoolProtocol.UNISWAP_V3,
        token0=_token("0xaaa"), token1=_token("0xbbb"),
        fee_bps=5, tvl_usd_at_enumeration=2e6, enumerated_at_block=1,
        chain="arbitrum",
    )
    with pytest.raises(ValueError, match="duplicate"):
        PoolSet(
            pools=(p1, p2), chain="all",
            enumerated_at_block=1, enumerated_at="t",
            uniswap_v3_tvl_floor_usd=1, aerodrome_tvl_floor_usd=1,
        )


def test_defillama_tvl_source_caches_fetch():
    """Multiple get_pools_above_floor() calls should hit the network once."""
    payload = {
        "data": [{
            "chain": "Base", "project": "uniswap-v3",
            "tvlUsd": 5_000_000, "poolMeta": "0.3%",
            "underlyingTokens": ["0xa", "0xb"], "symbol": "S",
        }],
    }
    fetches = {"n": 0}

    def opener(url, timeout):
        fetches["n"] += 1
        return _FakeResponse(payload)

    src = DefiLlamaTvlSource(opener=opener)
    src.get_pools_above_floor("Base", "uniswap-v3", 1)
    src.get_pools_above_floor("Base", "uniswap-v3", 1)
    src.get_pools_above_floor("Ethereum", "uniswap-v3", 1)
    assert fetches["n"] == 1
