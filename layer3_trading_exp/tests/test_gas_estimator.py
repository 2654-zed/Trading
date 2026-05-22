"""Unit tests for gas_estimator.

Direct math checks for cost components (gas, flash loan), the ETH/USD
spot derivation, and the canonical-pool finder.
"""

from __future__ import annotations

import math

import pytest

from layer3_trading_exp.gas_estimator import (
    AAVE_V3_FLASH_LOAN_BPS,
    DEFAULT_BASE_FEE_GWEI,
    FLASH_LOAN_CALLBACK_GAS,
    PER_BUNDLE_OVERHEAD_GAS,
    USDC_BASE,
    WETH_BASE,
    eth_usd_from_weth_usdc_v3,
    find_canonical_eth_usd_pool,
    flash_loan_fee_usd,
    gas_cost_usd,
    gas_units_for_path,
    total_execution_cost_usd,
)
from layer3_trading_exp.pool_set import PoolInfo, PoolProtocol, PoolSet, TokenInfo


WETH = TokenInfo(address=WETH_BASE.lower(), symbol="WETH", decimals=18)
USDC = TokenInfo(address=USDC_BASE.lower(), symbol="USDC", decimals=6)


def _univ3_pool(addr: str, t0: TokenInfo, t1: TokenInfo, fee_bps: int, tvl: float) -> PoolInfo:
    return PoolInfo(
        address=addr.lower(),
        protocol=PoolProtocol.UNISWAP_V3,
        token0=t0, token1=t1,
        fee_bps=fee_bps,
        tvl_usd_at_enumeration=tvl,
        enumerated_at_block=1,
    )


def test_gas_units_for_path_sums_per_pool_plus_overhead():
    p1 = _univ3_pool("0x" + "aa" * 20, WETH, USDC, 5, 1e7)
    p2 = _univ3_pool("0x" + "bb" * 20, WETH, USDC, 30, 1e7)
    units = gas_units_for_path([p1, p2])
    # 2 UniV3 swaps (120k each) + 50k flash loan callback + 30k bundle overhead
    assert units == 2 * 120_000 + FLASH_LOAN_CALLBACK_GAS + PER_BUNDLE_OVERHEAD_GAS


def test_gas_units_protocol_distinct():
    """Aerodrome volatile/stable use different gas budgets than UniV3/Slipstream."""
    p_uni = _univ3_pool("0x" + "aa" * 20, WETH, USDC, 5, 1e7)
    p_aero = PoolInfo(
        address="0x" + "bb" * 20,
        protocol=PoolProtocol.AERODROME_VOLATILE,
        token0=WETH, token1=USDC, fee_bps=30,
        tvl_usd_at_enumeration=2e6, enumerated_at_block=1,
    )
    units_pure_uni = gas_units_for_path([p_uni, p_uni])
    units_mixed = gas_units_for_path([p_uni, p_aero])
    # Aerodrome volatile is cheaper per-swap than UniV3 → mixed path uses fewer units
    assert units_mixed < units_pure_uni


def test_gas_cost_usd_arithmetic():
    """200k gas at 0.01 gwei base fee with $2000 ETH:
        cost = 200_000 * 0.01e-9 * 2000 = 200_000 * 2e-8 = 0.004
    """
    cost = gas_cost_usd(200_000, base_fee_gwei=0.01, eth_usd_price=2000.0)
    assert cost == pytest.approx(0.004, rel=1e-9)


def test_gas_cost_handles_degenerate_inputs():
    assert gas_cost_usd(0, 0.01, 2000) == 0.0
    assert gas_cost_usd(100_000, -1, 2000) == 0.0
    assert gas_cost_usd(100_000, 0.01, -1) == 0.0


def test_flash_loan_fee_usd_aave_v3_rate():
    """Aave V3: 0.05% on $10K = $5."""
    fee = flash_loan_fee_usd(10_000.0)
    assert fee == pytest.approx(5.0, rel=1e-9)


def test_flash_loan_fee_zero_for_zero_amount():
    assert flash_loan_fee_usd(0.0) == 0.0
    assert flash_loan_fee_usd(-5.0) == 0.0


def test_total_cost_dominated_by_flash_loan_at_base_gas_rates():
    """At Base's typical low gas (~0.01 gwei), flash-loan fee dominates the
    total cost calculation. Confirms the priority ordering for the detector
    (flash-loan modeling matters more than gas precision)."""
    p = _univ3_pool("0x" + "aa" * 20, WETH, USDC, 5, 1e7)
    cost = total_execution_cost_usd(
        pools=[p, p],
        base_fee_gwei=DEFAULT_BASE_FEE_GWEI,
        eth_usd_price=2000.0,
        amount_borrowed_usd=10_000.0,
    )
    # gas portion ≈ $0.006, flash loan ≈ $5.00
    fl = flash_loan_fee_usd(10_000.0)
    assert cost > fl  # total includes some gas
    assert (cost - fl) / fl < 0.01  # gas is < 1% of flash loan fee


def test_eth_usd_from_weth_usdc_v3_at_2000():
    """sqrtPriceX96 corresponding to 1 ETH = 2000 USDC:
        sqrtP = sqrt(2000 * 1e6 / 1e18) * 2^96 = sqrt(2e-9) * 2^96
    The function should recover ~$2000."""
    sqrt_p = int(math.sqrt(2000 * 1e6 / 1e18) * (1 << 96))
    price = eth_usd_from_weth_usdc_v3(sqrt_p)
    assert price == pytest.approx(2000.0, rel=1e-3)  # 0.1% tolerance


def test_eth_usd_handles_zero_input():
    assert eth_usd_from_weth_usdc_v3(0) == 0.0
    assert eth_usd_from_weth_usdc_v3(-1) == 0.0


def test_find_canonical_eth_usd_pool_picks_highest_tvl():
    """Multiple WETH/USDC UniV3 pools exist (different fee tiers); the helper
    must return the highest-TVL one for the most accurate price quote."""
    p_high = _univ3_pool("0x" + "aa" * 20, WETH, USDC, 30, 130_000_000.0)
    p_low = _univ3_pool("0x" + "bb" * 20, WETH, USDC, 5, 13_000_000.0)
    p_unrelated = _univ3_pool(
        "0x" + "cc" * 20, WETH,
        TokenInfo(address="0x" + "ee" * 20, symbol="OTHER", decimals=18),
        30, 50_000_000.0,
    )
    pset = PoolSet(
        pools=(p_high, p_low, p_unrelated), chain="base",
        enumerated_at_block=1, enumerated_at="t",
        uniswap_v3_tvl_floor_usd=1e6, aerodrome_tvl_floor_usd=5e5,
    )
    result = find_canonical_eth_usd_pool(pset)
    assert result is p_high


def test_find_canonical_eth_usd_pool_returns_none_when_absent():
    OTHER = TokenInfo(address="0x" + "ee" * 20, symbol="OTHER", decimals=18)
    p = _univ3_pool("0x" + "aa" * 20, WETH, OTHER, 30, 1e7)
    pset = PoolSet(
        pools=(p,), chain="base",
        enumerated_at_block=1, enumerated_at="t",
        uniswap_v3_tvl_floor_usd=1e6, aerodrome_tvl_floor_usd=5e5,
    )
    assert find_canonical_eth_usd_pool(pset) is None
