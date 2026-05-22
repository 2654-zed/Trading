"""Unit tests for bridge_model.py (Phase 2 sub-phase 2.3, D-009)."""

from __future__ import annotations

import pytest

from layer3_trading_exp.bridge_model import (
    DEFAULT as DEFAULT_BRIDGE,
    BridgeLeg,
    BridgeModel,
)
from layer3_trading_exp.token_registry import TokenRegistry


def test_default_supports_usdc_across_l2_triangle():
    for (src, dst) in [("base", "arbitrum"), ("base", "optimism"),
                       ("arbitrum", "optimism"), ("arbitrum", "base"),
                       ("optimism", "base"), ("optimism", "arbitrum")]:
        assert DEFAULT_BRIDGE.is_route_supported(src, dst, "USDC"), \
            f"USDC route {src}->{dst} should be supported"


def test_default_rejects_same_chain_routes():
    assert not DEFAULT_BRIDGE.is_route_supported("base", "base", "USDC")
    assert not DEFAULT_BRIDGE.is_route_supported("arbitrum", "arbitrum", "WETH")


def test_default_rejects_non_canonical_tokens_per_i12():
    """I-12: only registry-canonical tokens. cbBTC isn't on Arb/OP."""
    assert not DEFAULT_BRIDGE.is_route_supported("base", "arbitrum", "cbBTC")
    assert not DEFAULT_BRIDGE.is_route_supported("arbitrum", "optimism", "cbBTC")
    # USDT not on Base.
    assert not DEFAULT_BRIDGE.is_route_supported("base", "arbitrum", "USDT")
    assert not DEFAULT_BRIDGE.is_route_supported("arbitrum", "base", "USDT")


def test_fee_bps_per_token_defaults():
    """Per D-006 / spec sub-phase 2.4 starting table."""
    assert DEFAULT_BRIDGE.fee_bps("base", "arbitrum", "USDC") == 10.0
    assert DEFAULT_BRIDGE.fee_bps("base", "arbitrum", "WETH") == 8.0
    assert DEFAULT_BRIDGE.fee_bps("arbitrum", "optimism", "USDT") == 12.0
    assert DEFAULT_BRIDGE.fee_bps("arbitrum", "base", "DAI") == 10.0


def test_fee_bps_per_route_override_wins_over_default():
    """Deployment-time verifier may produce per-route overrides."""
    bm = BridgeModel(
        fee_bps_by_route={("base", "arbitrum", "USDC"): 15.0},
    )
    # Override applies on the configured route.
    assert bm.fee_bps("base", "arbitrum", "USDC") == 15.0
    # Same token, different route: default applies.
    assert bm.fee_bps("arbitrum", "base", "USDC") == 10.0


def test_latency_seconds_default_is_thirty():
    """D-006 point estimate."""
    for token in ("USDC", "WETH", "USDT", "DAI", "cbBTC"):
        assert DEFAULT_BRIDGE.latency_seconds("base", "arbitrum", token) == 30.0


def test_latency_drift_haircut_per_volatility_class():
    """Stablecoins lowest, WETH bumped, cbBTC bumped more."""
    assert DEFAULT_BRIDGE.latency_drift_haircut_bps("USDC", 10_000) == 10.0
    assert DEFAULT_BRIDGE.latency_drift_haircut_bps("DAI", 10_000) == 10.0
    assert DEFAULT_BRIDGE.latency_drift_haircut_bps("WETH", 10_000) == 15.0
    assert DEFAULT_BRIDGE.latency_drift_haircut_bps("cbBTC", 10_000) == 20.0


def test_round_trip_cost_combines_fees_and_haircut():
    """Two USDC bridge hops + USDC haircut:
       fee(base->arb, USDC) + fee(arb->base, USDC) + haircut(USDC)
       = 10 + 10 + 10 = 30 bps."""
    cost = DEFAULT_BRIDGE.round_trip_cost_bps("base", "arbitrum", "USDC")
    assert cost == 30.0


def test_make_bridge_leg_returns_populated_leg():
    leg = DEFAULT_BRIDGE.make_bridge_leg("base", "arbitrum", "USDC")
    assert isinstance(leg, BridgeLeg)
    assert leg.src_chain == "base"
    assert leg.dst_chain == "arbitrum"
    assert leg.token == "USDC"
    assert leg.fee_bps == 10.0
    assert leg.latency_seconds == 30.0
    assert leg.token_address_src != leg.token_address_dst  # different USDC addrs


def test_make_bridge_leg_raises_for_unsupported_route():
    with pytest.raises(ValueError, match="unsupported route"):
        DEFAULT_BRIDGE.make_bridge_leg("base", "arbitrum", "cbBTC")
    with pytest.raises(ValueError, match="unsupported route"):
        DEFAULT_BRIDGE.make_bridge_leg("base", "base", "USDC")  # same-chain


def test_bridge_leg_to_dict_roundtrip_fields():
    leg = DEFAULT_BRIDGE.make_bridge_leg("arbitrum", "optimism", "WETH")
    d = leg.to_dict()
    assert d["src_chain"] == "arbitrum"
    assert d["dst_chain"] == "optimism"
    assert d["token"] == "WETH"
    assert d["fee_bps"] == 8.0
    assert d["latency_seconds"] == 30.0


def test_custom_token_registry_drives_route_support():
    """The bridge model defers I-12 enforcement to the bound token registry —
    so swapping in a custom registry should change the supported route set."""
    minimal = TokenRegistry(
        canonical_tokens={
            "MYUSD": {"base": "0xa1", "arbitrum": "0xa2"},
        },
        decimals={"MYUSD": 6},
    )
    bm = BridgeModel(
        token_registry=minimal,
        fee_bps_by_token={"MYUSD": 5.0},
        drift_haircut_bps_by_token={"MYUSD": 10.0},
    )
    assert bm.is_route_supported("base", "arbitrum", "MYUSD")
    # USDC not in the custom registry → not supported.
    assert not bm.is_route_supported("base", "arbitrum", "USDC")
