"""Unit tests for token_registry.py (Phase 2 sub-phase 2.3, D-009)."""

from __future__ import annotations

import pytest

from layer3_trading_exp.token_registry import DEFAULT, TokenRegistry


def test_default_has_canonical_usdc_on_all_three_chains():
    """USDC is canonical on Base/Arb/OP per I-12."""
    for chain in ("base", "arbitrum", "optimism"):
        addr = DEFAULT.canonical_address("USDC", chain)
        assert addr is not None, f"USDC missing for chain {chain!r}"
        assert addr.startswith("0x")
        assert addr == addr.lower(), "addresses must be stored lowercase"


def test_default_weth_addresses_for_all_three_chains():
    for chain in ("base", "arbitrum", "optimism"):
        addr = DEFAULT.weth_address(chain)
        assert addr is not None
    # OP-stack predeploy address is shared between Base and OP.
    assert DEFAULT.weth_address("base") == DEFAULT.weth_address("optimism")
    # Arb has its own WETH address.
    assert DEFAULT.weth_address("arbitrum") != DEFAULT.weth_address("base")


def test_default_decimals_are_chain_invariant():
    assert DEFAULT.decimals("USDC") == 6
    assert DEFAULT.decimals("WETH") == 18
    assert DEFAULT.decimals("USDT") == 6
    assert DEFAULT.decimals("DAI") == 18
    assert DEFAULT.decimals("cbBTC") == 8


def test_default_is_canonical_case_insensitive():
    base_usdc = DEFAULT.canonical_address("USDC", "base")
    assert DEFAULT.is_canonical(base_usdc, "base")
    assert DEFAULT.is_canonical(base_usdc.upper(), "base")
    # Same address on a different chain → not canonical (since the actual
    # USDC address on Arb is different — the registry catches this).
    assert not DEFAULT.is_canonical(base_usdc, "arbitrum")


def test_default_symbol_for_returns_known_token():
    weth_arb = DEFAULT.weth_address("arbitrum")
    assert DEFAULT.symbol_for(weth_arb, "arbitrum") == "WETH"
    assert DEFAULT.symbol_for("0xdead", "base") is None


def test_default_chains_for_token():
    assert DEFAULT.chains_for("USDC") == ("arbitrum", "base", "optimism")
    # cbBTC is Base-only in our registry (Coinbase hasn't bridged it).
    assert DEFAULT.chains_for("cbBTC") == ("base",)
    # USDT not on Base.
    assert "base" not in DEFAULT.chains_for("USDT")


def test_bridgeable_pairs_excludes_same_chain():
    pairs = list(DEFAULT.bridgeable_pairs())
    for src, dst, sym in pairs:
        assert src != dst, f"same-chain pair leaked through: ({src}, {dst}, {sym})"


def test_bridgeable_pairs_includes_expected_usdc_routes():
    """USDC has 6 directional pairs across 3 chains: (base,arb), (base,op),
    (arb,base), (arb,op), (op,base), (op,arb)."""
    pairs = [(s, d, t) for (s, d, t) in DEFAULT.bridgeable_pairs() if t == "USDC"]
    expected = {
        ("base", "arbitrum"), ("base", "optimism"),
        ("arbitrum", "base"), ("arbitrum", "optimism"),
        ("optimism", "base"), ("optimism", "arbitrum"),
    }
    assert {(s, d) for (s, d, t) in pairs} == expected


def test_bridgeable_pairs_excludes_single_chain_tokens():
    """cbBTC is Base-only → can't be bridged (no second chain to pair with)."""
    cbbtc_pairs = [(s, d, t) for (s, d, t) in DEFAULT.bridgeable_pairs() if t == "cbBTC"]
    assert cbbtc_pairs == []


def test_registry_can_be_constructed_with_overrides():
    """For test fixtures: instantiate a custom registry."""
    custom = TokenRegistry(
        canonical_tokens={"FOO": {"base": "0xfoo"}, "BAR": {"base": "0xbar", "arbitrum": "0xbar2"}},
        decimals={"FOO": 18, "BAR": 6},
    )
    assert custom.canonical_address("FOO", "base") == "0xfoo"
    assert custom.canonical_address("FOO", "arbitrum") is None
    assert custom.decimals("BAR") == 6
    # FOO is one-chain so isn't in bridgeable_pairs; BAR is two-chain.
    pairs = list(custom.bridgeable_pairs())
    assert ("base", "arbitrum", "BAR") in pairs
    assert ("arbitrum", "base", "BAR") in pairs
    assert all(t != "FOO" for (_s, _d, t) in pairs)


def test_lookup_returns_full_token_info():
    info = DEFAULT.lookup("USDC", "arbitrum")
    assert info is not None
    assert info.symbol == "USDC"
    assert info.chain == "arbitrum"
    assert info.decimals == 6
    assert info.address.startswith("0x")
    assert DEFAULT.lookup("USDC", "ethereum") is None
