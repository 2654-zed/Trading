"""Unit tests for the AMM quote package.

Each math module gets its own test section. Hand-verified analytical cases use
"balanced" pools (token0 ~ token1 in normalized terms) where the math collapses
to a closed form we can compute by hand. Real-pool sanity checks use Base pool
parameters drawn from the canonical WETH/USDC pair.
"""

from __future__ import annotations

import pytest

from layer3_trading_exp.pool_monitor import AerodromePoolState, UniV3PoolState
from layer3_trading_exp.pool_set import PoolInfo, PoolProtocol, TokenInfo
from layer3_trading_exp.quote import (
    AERODROME_STABLE_DEFAULT_FEE_BPS,
    AERODROME_VOLATILE_DEFAULT_FEE_BPS,
    cl_quote,
    cpamm_quote,
    quote_swap_via_pool,
    stableswap_quote,
)


# ============================================================================
# constant-product (Aerodrome v1 volatile) — cpamm_quote
# ============================================================================

def test_cpamm_hand_verified_v2_swap():
    """Pool: 1000 WETH / 2_000_000 USDC, fee 30 bps, swap 1 WETH in.

    Expected (V2 closed form):
        amount_in_eff = 1e18 * 9970/10000 = 9.97e17
        amount_out = 9.97e17 * 2_000_000e6 / (1000e18 + 9.97e17)
                   = (9.97e17 * 2e12) / (1.001e21)
                   ≈ 1.992020e9 USDC raw  (~$1992)
    """
    out = cpamm_quote.quote_swap(
        reserve0=1000 * 10**18,        # WETH (18 decimals)
        reserve1=2_000_000 * 10**6,    # USDC (6 decimals)
        amount_in=1 * 10**18,          # 1 WETH
        zero_for_one=True,
        fee_bps=30,
    )
    # Closed form computed independently:
    expected = (10**18 * 9970 * 2_000_000 * 10**6) // (1000 * 10**18 * 10000 + 10**18 * 9970)
    assert out == expected
    # ~$1992 ± small slippage.
    assert 1_990_000_000 < out < 1_995_000_000


def test_cpamm_zero_amount_returns_zero():
    out = cpamm_quote.quote_swap(
        reserve0=10**24, reserve1=10**24, amount_in=0,
        zero_for_one=True, fee_bps=30,
    )
    assert out == 0


def test_cpamm_zero_reserves_returns_zero():
    out = cpamm_quote.quote_swap(
        reserve0=0, reserve1=10**24, amount_in=10**18,
        zero_for_one=True, fee_bps=30,
    )
    assert out == 0


def test_cpamm_zero_fee_matches_pure_xy_invariant():
    """With fee=0, the result is exactly the constant-product formula in pure ints.

    For reserves R0, R1 and amount_in A:
        amount_out = (A * R1) // (R0 + A)
    """
    R0, R1, A = 1_000_000, 2_000_000, 5_000
    out = cpamm_quote.quote_swap(
        reserve0=R0, reserve1=R1, amount_in=A,
        zero_for_one=True, fee_bps=0,
    )
    assert out == (A * R1) // (R0 + A)


def test_cpamm_reverse_direction():
    """Swapping the same nominal value the other way should produce close to
    the inverse output, modulo fees."""
    out_forward = cpamm_quote.quote_swap(
        reserve0=10**24, reserve1=10**24, amount_in=10**18,
        zero_for_one=True, fee_bps=30,
    )
    out_reverse = cpamm_quote.quote_swap(
        reserve0=10**24, reserve1=10**24, amount_in=10**18,
        zero_for_one=False, fee_bps=30,
    )
    # Symmetric pool, identical math both ways.
    assert out_forward == out_reverse


def test_cpamm_rejects_invalid_fee():
    with pytest.raises(ValueError, match="out of range"):
        cpamm_quote.quote_swap(
            reserve0=10**24, reserve1=10**24, amount_in=10**18,
            zero_for_one=True, fee_bps=10_000,  # 100% — invalid
        )


# ============================================================================
# concentrated-liquidity (UniV3, Slipstream) — cl_quote
# ============================================================================

# Balanced pool at price 1.0, both tokens 18 decimals.
# sqrtPriceX96 = sqrt(1) * 2^96 = 2^96.
SQRT_PRICE_1_0 = 1 << 96


def test_cl_balanced_pool_collapses_to_cpamm():
    """At price 1.0 with both tokens 18-decimal and amount_in << liquidity,
    CL math reduces to CPAMM math:
        amount_out = L * amount_in_eff / (L + amount_in_eff)
    """
    L = 10**24                    # deep liquidity
    amount_in = 10**18            # 1 unit
    fee_pips = 3000               # 0.3%

    out = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0,
        liquidity=L,
        amount_in=amount_in,
        zero_for_one=True,
        fee_pips=fee_pips,
    )
    amount_in_eff = amount_in * (10**6 - fee_pips) // 10**6
    expected = (L * amount_in_eff) // (L + amount_in_eff)
    # Allow small rounding tolerance — UniV3 truncates intermediate products.
    assert abs(out - expected) <= 10  # 10 wei out of 10^17 is < 1e-16 relative


def test_cl_zero_inputs_return_zero():
    assert cl_quote.quote_swap(
        sqrt_price_x96=0, liquidity=10**18, amount_in=10**18,
        zero_for_one=True, fee_pips=3000,
    ) == 0
    assert cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=0, amount_in=10**18,
        zero_for_one=True, fee_pips=3000,
    ) == 0
    assert cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=10**18, amount_in=0,
        zero_for_one=True, fee_pips=3000,
    ) == 0


def test_cl_higher_fee_yields_less_output():
    L = 10**24
    amount_in = 10**18
    out_5bp = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=L, amount_in=amount_in,
        zero_for_one=True, fee_pips=500,
    )
    out_30bp = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=L, amount_in=amount_in,
        zero_for_one=True, fee_pips=3000,
    )
    out_100bp = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=L, amount_in=amount_in,
        zero_for_one=True, fee_pips=10_000,
    )
    assert out_5bp > out_30bp > out_100bp


def test_cl_directionality():
    """For a balanced pool at price 1.0, both directions should yield the same
    output (math is symmetric). Use this to verify the one_for_zero branch."""
    L = 10**24
    amount_in = 10**18
    out_z4o = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=L, amount_in=amount_in,
        zero_for_one=True, fee_pips=3000,
    )
    out_o4z = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=L, amount_in=amount_in,
        zero_for_one=False, fee_pips=3000,
    )
    # Should be equal up to rounding (a few wei).
    assert abs(out_z4o - out_o4z) <= 100


def test_cl_spot_price_helper():
    # sqrtPriceX96 = 2 * Q96 means price = 4
    assert cl_quote.spot_price_token0_per_token1(2 * cl_quote.Q96) == pytest.approx(4.0)
    assert cl_quote.spot_price_token0_per_token1(0) == 0.0


def test_cl_rejects_invalid_fee():
    with pytest.raises(ValueError, match="out of range"):
        cl_quote.quote_swap(
            sqrt_price_x96=SQRT_PRICE_1_0, liquidity=10**18, amount_in=10**18,
            zero_for_one=True, fee_pips=1_000_000,  # 100%
        )


# ============================================================================
# Solidly stableswap (Aerodrome v1 stable) — stableswap_quote
# ============================================================================

def test_stable_symmetric_near_peg():
    """USDC/USDT-style pool, both 6 decimals, 1M each. Swap 1000 USDC → ~1000 USDT
    minus 5 bps fee, with very low slippage (the curve concentrates liquidity)."""
    out = stableswap_quote.quote_swap(
        reserve0=1_000_000 * 10**6,
        reserve1=1_000_000 * 10**6,
        decimals0=6, decimals1=6,
        amount_in=1_000 * 10**6,
        zero_for_one=True,
        fee_bps=5,
    )
    # Expect ~999.5 (1000 - 5 bps fee) USDT minus a tiny slippage.
    # On-peg stableswap slippage for 1000 against 1M reserves is ~10^-7, negligible.
    fee_loss = 1_000 * 10**6 * 5 // 10_000  # 500_000 raw = $0.50
    expected_min = 1_000 * 10**6 - fee_loss - 100  # allow tiny slippage
    expected_max = 1_000 * 10**6 - fee_loss + 100
    assert expected_min <= out <= expected_max


def test_stable_handles_decimal_asymmetry():
    """USDC (6 dec) / DAI (18 dec) at peg. Normalization must scale both sides
    correctly so the invariant sees a balanced pool."""
    out = stableswap_quote.quote_swap(
        reserve0=1_000_000 * 10**6,    # 1M USDC
        reserve1=1_000_000 * 10**18,   # 1M DAI
        decimals0=6, decimals1=18,
        amount_in=1_000 * 10**6,       # 1000 USDC
        zero_for_one=True,
        fee_bps=5,
    )
    # Expect ~1000 DAI (1e18 raw scale) minus 5 bps fee.
    expected = 1_000 * 10**18 - (1_000 * 10**18 * 5 // 10_000)
    # Allow 0.5% tolerance (the asymmetric-decimals quotient introduces some rounding).
    assert abs(out - expected) < expected // 200


def test_stable_zero_inputs():
    assert stableswap_quote.quote_swap(
        reserve0=10**12, reserve1=10**12, decimals0=6, decimals1=6,
        amount_in=0, zero_for_one=True, fee_bps=5,
    ) == 0
    assert stableswap_quote.quote_swap(
        reserve0=0, reserve1=10**12, decimals0=6, decimals1=6,
        amount_in=10**6, zero_for_one=True, fee_bps=5,
    ) == 0


def test_stable_off_peg_reserves_show_price_impact():
    """Asymmetric pool (more of one token than the other) should price the
    cheaper token favorably for swappers buying it."""
    # 2M USDC : 500K USDT — USDT is the scarcer side; buying USDT (zero_for_one=True)
    # should yield more than buying USDC (zero_for_one=False) for the same input.
    out_buy_scarce = stableswap_quote.quote_swap(
        reserve0=2_000_000 * 10**6,
        reserve1=500_000 * 10**6,
        decimals0=6, decimals1=6,
        amount_in=10_000 * 10**6,
        zero_for_one=True,  # USDC -> USDT (buy scarce)
        fee_bps=5,
    )
    out_buy_abundant = stableswap_quote.quote_swap(
        reserve0=2_000_000 * 10**6,
        reserve1=500_000 * 10**6,
        decimals0=6, decimals1=6,
        amount_in=10_000 * 10**6,
        zero_for_one=False,  # USDT -> USDC (buy abundant)
        fee_bps=5,
    )
    # Buying the scarce side is more expensive — expect less out.
    assert out_buy_scarce < out_buy_abundant


def test_stable_round_trip_loses_only_fees_near_peg():
    """A→B→A round trip at peg should leave us with ~original input minus 2x fee."""
    reserves = (1_000_000 * 10**6, 1_000_000 * 10**6)
    fee_bps = 5
    amount_in = 1_000 * 10**6

    leg1_out = stableswap_quote.quote_swap(
        reserve0=reserves[0], reserve1=reserves[1],
        decimals0=6, decimals1=6,
        amount_in=amount_in, zero_for_one=True, fee_bps=fee_bps,
    )
    # Update reserves to reflect leg 1 (input added, output removed) before leg 2.
    reserves_after_leg1 = (reserves[0] + amount_in, reserves[1] - leg1_out)
    leg2_out = stableswap_quote.quote_swap(
        reserve0=reserves_after_leg1[0], reserve1=reserves_after_leg1[1],
        decimals0=6, decimals1=6,
        amount_in=leg1_out, zero_for_one=False, fee_bps=fee_bps,
    )
    # Round trip should lose ~2 * 5 bps = 10 bps. Accept up to 15 bps total loss
    # (allows for tiny slippage drift).
    loss_bps = (amount_in - leg2_out) * 10_000 // amount_in
    assert 9 <= loss_bps <= 15


# ============================================================================
# dispatcher — quote_swap_via_pool
# ============================================================================

def _token(addr: str, sym: str = "TKN", dec: int = 18) -> TokenInfo:
    return TokenInfo(address=addr.lower(), symbol=sym, decimals=dec)


def _univ3_pool(token0_addr: str, token1_addr: str, fee_bps: int) -> PoolInfo:
    return PoolInfo(
        address="0x" + "11" * 20,
        protocol=PoolProtocol.UNISWAP_V3,
        token0=_token(token0_addr, dec=18),
        token1=_token(token1_addr, dec=18),
        fee_bps=fee_bps,
        tvl_usd_at_enumeration=2e6,
        enumerated_at_block=1,
    )


def _aero_pool(token0_addr: str, token1_addr: str, stable: bool) -> PoolInfo:
    return PoolInfo(
        address="0x" + "22" * 20,
        protocol=PoolProtocol.AERODROME_STABLE if stable else PoolProtocol.AERODROME_VOLATILE,
        token0=_token(token0_addr, dec=6),
        token1=_token(token1_addr, dec=6),
        fee_bps=None,
        tvl_usd_at_enumeration=8e5,
        enumerated_at_block=1,
    )


def test_dispatcher_routes_univ3_to_cl_quote():
    pool = _univ3_pool("0x" + "aa" * 20, "0x" + "bb" * 20, fee_bps=30)
    state = UniV3PoolState(pool.address, sqrt_price_x96=SQRT_PRICE_1_0,
                           tick=0, liquidity=10**24)
    out = quote_swap_via_pool(pool, state, "0x" + "aa" * 20, 10**18)
    direct = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=10**24,
        amount_in=10**18, zero_for_one=True, fee_pips=3000,
    )
    assert out == direct


def test_dispatcher_routes_slipstream_to_cl_quote():
    pool = PoolInfo(
        address="0x" + "33" * 20,
        protocol=PoolProtocol.AERODROME_SLIPSTREAM,
        token0=_token("0x" + "aa" * 20, dec=18),
        token1=_token("0x" + "bb" * 20, dec=18),
        fee_bps=5,
        tvl_usd_at_enumeration=2e6,
        enumerated_at_block=1,
    )
    state = UniV3PoolState(pool.address, sqrt_price_x96=SQRT_PRICE_1_0,
                           tick=0, liquidity=10**24)
    out = quote_swap_via_pool(pool, state, "0x" + "aa" * 20, 10**18)
    # fee_bps=5 -> fee_pips=500
    direct = cl_quote.quote_swap(
        sqrt_price_x96=SQRT_PRICE_1_0, liquidity=10**24,
        amount_in=10**18, zero_for_one=True, fee_pips=500,
    )
    assert out == direct


def test_dispatcher_routes_aero_volatile_to_cpamm():
    pool = _aero_pool("0x" + "aa" * 20, "0x" + "bb" * 20, stable=False)
    state = AerodromePoolState(pool.address, reserve0=10**12, reserve1=2 * 10**12,
                                block_timestamp_last=0)
    out = quote_swap_via_pool(pool, state, "0x" + "aa" * 20, 10**6)
    direct = cpamm_quote.quote_swap(
        reserve0=10**12, reserve1=2 * 10**12,
        amount_in=10**6, zero_for_one=True,
        fee_bps=AERODROME_VOLATILE_DEFAULT_FEE_BPS,
    )
    assert out == direct


def test_dispatcher_routes_aero_stable_to_stableswap():
    pool = _aero_pool("0x" + "aa" * 20, "0x" + "bb" * 20, stable=True)
    state = AerodromePoolState(pool.address, reserve0=10**12, reserve1=10**12,
                                block_timestamp_last=0)
    out = quote_swap_via_pool(pool, state, "0x" + "aa" * 20, 10**6)
    direct = stableswap_quote.quote_swap(
        reserve0=10**12, reserve1=10**12,
        decimals0=6, decimals1=6,
        amount_in=10**6, zero_for_one=True,
        fee_bps=AERODROME_STABLE_DEFAULT_FEE_BPS,
    )
    assert out == direct


def test_dispatcher_rejects_token_not_in_pool():
    pool = _univ3_pool("0x" + "aa" * 20, "0x" + "bb" * 20, fee_bps=30)
    state = UniV3PoolState(pool.address, sqrt_price_x96=SQRT_PRICE_1_0,
                           tick=0, liquidity=10**24)
    with pytest.raises(ValueError, match="not in pool"):
        quote_swap_via_pool(pool, state, "0x" + "cc" * 20, 10**18)


def test_dispatcher_rejects_state_protocol_mismatch():
    """Aerodrome pool state passed to a UniV3 pool should fail loudly."""
    pool = _univ3_pool("0x" + "aa" * 20, "0x" + "bb" * 20, fee_bps=30)
    bad_state = AerodromePoolState(pool.address, reserve0=1, reserve1=1,
                                    block_timestamp_last=0)
    with pytest.raises(TypeError, match="UniV3PoolState"):
        quote_swap_via_pool(pool, bad_state, "0x" + "aa" * 20, 10**18)
