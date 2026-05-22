"""Unit tests for cross_chain_detector.py (Phase 2 sub-phase 2.3, D-009).

These are SYNTHETIC tests using fabricated per-chain pool sets + states.
The Phase 2.3 spec calls for "detector finds a known cross-chain opportunity
in test data and rejects a known sub-floor case" — both are covered here.

Live cross-chain validation (against actual Base/Arb/OP pool state) is the
user's sub-phase 2.3 boundary check after this code lands.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from layer3_trading_exp.bridge_model import BridgeModel
from layer3_trading_exp.cross_chain_detector import CrossChainDetector
from layer3_trading_exp.opportunity_detector import (
    GROSS_MARGIN_FLOOR_CROSS_CHAIN,
    Opportunity,
    OpportunityDetector,
)
from layer3_trading_exp.pool_monitor import (
    AerodromePoolState,
    BlockPoolStates,
    UniV3PoolState,
)
from layer3_trading_exp.pool_set import (
    PoolInfo,
    PoolProtocol,
    PoolSet,
    TokenInfo,
)
from layer3_trading_exp.token_registry import TokenRegistry


# Synthetic addresses keyed by chain (so registry lookups + cross-chain
# detection have non-colliding fixtures across chains).
SYN = {
    "base": {
        "WETH": "0xb1" + "0" * 38,
        "USDC": "0xb2" + "0" * 38,
        "WETH_USDC_POOL": "0xb3" + "0" * 38,
    },
    "arbitrum": {
        "WETH": "0xa1" + "0" * 38,
        "USDC": "0xa2" + "0" * 38,
        "WETH_USDC_POOL": "0xa3" + "0" * 38,
    },
}


def _addr(chain: str, what: str) -> str:
    return SYN[chain][what].lower()


def _registry() -> TokenRegistry:
    return TokenRegistry(
        canonical_tokens={
            "USDC": {
                "base":     _addr("base", "USDC"),
                "arbitrum": _addr("arbitrum", "USDC"),
            },
            "WETH": {
                "base":     _addr("base", "WETH"),
                "arbitrum": _addr("arbitrum", "WETH"),
            },
        },
        decimals={"USDC": 6, "WETH": 18},
    )


def _weth_usdc_pool(chain: str) -> PoolInfo:
    """A WETH/USDC UniV3 pool on `chain` with WETH as token0 (lower address)
    so the canonical ETH/USD pricing path works."""
    weth_addr = _addr(chain, "WETH")
    usdc_addr = _addr(chain, "USDC")
    # Enforce WETH < USDC by address so it sorts as token0 (matches the
    # eth_usd_from_weth_usdc_v3 assumption).
    assert weth_addr < usdc_addr, f"fixture invariant: WETH < USDC on {chain}"
    return PoolInfo(
        address=_addr(chain, "WETH_USDC_POOL"),
        protocol=PoolProtocol.UNISWAP_V3,
        token0=TokenInfo(address=weth_addr, symbol="WETH", decimals=18),
        token1=TokenInfo(address=usdc_addr, symbol="USDC", decimals=6),
        fee_bps=5,  # 0.05% tier; fee_pips = 500
        tvl_usd_at_enumeration=10_000_000.0,
        enumerated_at_block=1,
        chain=chain,
    )


def _pool_set(chain: str) -> PoolSet:
    return PoolSet(
        pools=(_weth_usdc_pool(chain),),
        chain=chain,
        enumerated_at_block=1,
        enumerated_at="t",
        uniswap_v3_tvl_floor_usd=500_000.0,
        aerodrome_tvl_floor_usd=250_000.0,
    )


def _eth_price_sqrt_x96(eth_usd: float) -> int:
    """Inverse of eth_usd_from_weth_usdc_v3: produce a sqrtPriceX96 that
    decodes to `eth_usd`. Used to build synthetic UniV3 state."""
    Q96 = 1 << 96
    ratio = (eth_usd / 1e12) ** 0.5
    return int(ratio * Q96)


def _block_states(chain: str, *, eth_usd: float, liquidity: int = 10**24) -> BlockPoolStates:
    pool = _weth_usdc_pool(chain)
    sqrt_p = _eth_price_sqrt_x96(eth_usd)
    return BlockPoolStates(
        block_number=100, block_timestamp=1_700_000_000,
        received_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        uniswap_v3={pool.address: UniV3PoolState(
            pool_address=pool.address,
            sqrt_price_x96=sqrt_p, tick=0, liquidity=liquidity,
        )},
        aerodrome={},
        slipstream={},
        chain=chain,
    )


def _build_detector() -> CrossChainDetector:
    registry = _registry()
    per_chain = {}
    for chain in ("base", "arbitrum"):
        ps = _pool_set(chain)
        det = OpportunityDetector(ps, chain=chain, token_registry=registry)
        per_chain[chain] = det
    bridge = BridgeModel(token_registry=registry)
    return CrossChainDetector(per_chain, bridge_model=bridge, token_registry=registry)


def test_detector_enumerates_scan_routes_for_two_chains():
    cd = _build_detector()
    # USDC and WETH on 2 chains, each with both as borrow + mid. So:
    # (src, dst, borrow, mid) where borrow != mid:
    #   USDC borrow + WETH mid : base→arb, arb→base = 2
    #   WETH borrow + USDC mid : base→arb, arb→base = 2
    # Total: 4 routes (each requires both chains to have the WETH/USDC pool).
    assert cd.route_count == 4


def test_detector_finds_cross_chain_opportunity_when_price_drift_large():
    """Force a 1% drift: WETH at $3000 on base, $3100 on arb. Borrow USDC
    on base, swap USDC→WETH on base at $3000, bridge WETH to arb, swap
    WETH→USDC on arb at $3100, bridge USDC back. Should clear the floor."""
    cd = _build_detector()
    states_by_chain = {
        "base":     _block_states("base", eth_usd=3000.0),
        "arbitrum": _block_states("arbitrum", eth_usd=3100.0),
    }
    opps = cd.detect(states_by_chain)
    # Filter to the (USDC-borrow, WETH-mid, base→arb) case for clarity.
    matches = [o for o in opps
               if o.borrowed_token_symbol == "USDC"
               and o.borrow_chain == "base"
               and o.path_chains == ("base", "arbitrum")]
    assert len(matches) >= 1, \
        f"expected at least one base→arb cross-chain opp; got {len(opps)} total"
    opp = matches[0]
    assert opp.is_cross_chain
    assert len(opp.bridge_legs) == 2
    assert opp.bridge_legs[0].src_chain == "base"
    assert opp.bridge_legs[0].dst_chain == "arbitrum"
    assert opp.bridge_legs[0].token == "WETH"
    assert opp.bridge_legs[1].src_chain == "arbitrum"
    assert opp.bridge_legs[1].dst_chain == "base"
    assert opp.bridge_legs[1].token == "USDC"
    assert opp.latency_drift_haircut_bps == 15.0  # WETH mid → 15 bps haircut
    assert opp.gross_margin_raw > opp.gross_margin  # haircut subtracted
    assert opp.gross_margin >= GROSS_MARGIN_FLOOR_CROSS_CHAIN


def test_detector_rejects_sub_floor_opportunity_when_drift_too_small():
    """Tiny price drift — bridge fees + haircut should eat the margin."""
    cd = _build_detector()
    states_by_chain = {
        "base":     _block_states("base", eth_usd=3000.0),
        "arbitrum": _block_states("arbitrum", eth_usd=3001.0),  # ~3 bps drift
    }
    opps = cd.detect(states_by_chain)
    assert opps == [], f"expected no opps from sub-floor drift; got {len(opps)}"


def test_detector_skips_when_chain_state_missing():
    cd = _build_detector()
    states_by_chain = {
        "base": _block_states("base", eth_usd=3000.0),
        # arbitrum state missing
    }
    opps = cd.detect(states_by_chain)
    assert opps == []


def test_detector_skips_when_no_eth_usd_price():
    """If a chain's canonical WETH/USDC pool isn't in the state dict, the
    chain has no ETH/USD price → no opportunities from that chain."""
    cd = _build_detector()
    # Base state with an unrelated pool address (doesn't match the canonical).
    bad_base_state = BlockPoolStates(
        block_number=100, block_timestamp=1_700_000_000,
        received_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        uniswap_v3={"0xdeadbeef": UniV3PoolState(
            pool_address="0xdeadbeef", sqrt_price_x96=1, tick=0, liquidity=1,
        )},
        aerodrome={}, slipstream={}, chain="base",
    )
    arb_state = _block_states("arbitrum", eth_usd=3100.0)
    opps = cd.detect({"base": bad_base_state, "arbitrum": arb_state})
    assert opps == []


def test_detector_per_block_wall_time_under_budget():
    """Per spec sub-phase 2.3 acceptance: per-block detection wall time
    stays under 50ms. With 1 pool per chain × 4 routes the math is trivial;
    this test guards against a future fan-out regression."""
    import time
    cd = _build_detector()
    states = {
        "base":     _block_states("base", eth_usd=3000.0),
        "arbitrum": _block_states("arbitrum", eth_usd=3100.0),
    }
    t0 = time.perf_counter()
    cd.detect(states)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 50.0, f"detect() took {elapsed_ms:.1f} ms; spec budget 50 ms"


def test_detector_requires_at_least_one_chain():
    with pytest.raises(ValueError, match="at least one chain"):
        CrossChainDetector({})
