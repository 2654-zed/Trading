"""Concentrated-liquidity AMM swap math (Uniswap V3 / Aerodrome Slipstream).

Within-tick approximation: assumes the swap completes inside the current tick range
without crossing tick boundaries. For high-TVL pools and $10K-notional trades, this
is a good approximation — the active liquidity at the current tick is typically
many multiples of $10K. For edge cases (low-liquidity pools, very-thin ticks, or
trades larger than active-tick depth), this approximation will OVER-estimate the
output amount because it doesn't account for liquidity falling off at tick boundaries.

Phase 1.2 acceptance run can validate empirically by comparing quote_swap output
against `quoter.quoteExactInputSingle` on a representative sample. Tick traversal
is documented as Phase 2 work in PHASE_1_1_ADDENDUM.md.

Math reference: Uniswap V3 whitepaper §6.2.

For an exactInput swap (amount_in known, amount_out computed):

    fee_pips_denom = 10**6
    amount_in_eff = amount_in * (fee_pips_denom - fee_pips) / fee_pips_denom

    if zero_for_one (price decreases — swap token0 in, token1 out):
        sqrtP_next = (L * sqrtP_curr * 2^96) // (L * 2^96 + amount_in_eff * sqrtP_curr)
        amount_out = L * (sqrtP_curr - sqrtP_next) / 2^96

    if one_for_zero (price increases — swap token1 in, token0 out):
        sqrtP_next = sqrtP_curr + (amount_in_eff * 2^96) // L
        amount_out = L * (sqrtP_next - sqrtP_curr) * 2^96 // (sqrtP_curr * sqrtP_next)

Fee in UniV3/Slipstream is in hundredths of a basis point ("pips"):
    100   = 0.01%
    500   = 0.05%
    3000  = 0.30%
    10000 = 1.00%

Exposed `fee_pips` here is in those native units.
"""

from __future__ import annotations


Q96 = 1 << 96
_FEE_PIPS_DENOM = 1_000_000


def quote_swap(
    *,
    sqrt_price_x96: int,
    liquidity: int,
    amount_in: int,
    zero_for_one: bool,
    fee_pips: int,
) -> int:
    """Quote amount_out for a within-tick concentrated-liquidity swap.

    Args:
        sqrt_price_x96: current pool sqrtPriceX96 (slot0).
        liquidity: pool's active in-range liquidity (uint128).
        amount_in: input amount in raw units of the input token.
        zero_for_one: True if swapping token0 -> token1 (price decreases).
        fee_pips: fee in hundredths of a basis point (UniV3 native units).

    Returns:
        amount_out in raw units of the output token. 0 for degenerate inputs.
    """
    if amount_in <= 0 or liquidity <= 0 or sqrt_price_x96 <= 0:
        return 0
    if not (0 <= fee_pips < _FEE_PIPS_DENOM):
        raise ValueError(f"fee_pips {fee_pips} out of range [0, {_FEE_PIPS_DENOM})")

    # Take fee off input first, integer-truncated (matches UniV3 contract behavior).
    amount_in_eff = amount_in * (_FEE_PIPS_DENOM - fee_pips) // _FEE_PIPS_DENOM
    if amount_in_eff <= 0:
        return 0

    if zero_for_one:
        # Price moves down: token0 in, token1 out.
        # sqrtP_next = (L * sqrtP * Q96) / (L * Q96 + amount_in * sqrtP)
        numerator = liquidity * sqrt_price_x96 * Q96
        denominator = liquidity * Q96 + amount_in_eff * sqrt_price_x96
        if denominator == 0:
            return 0
        sqrt_price_next = numerator // denominator
        if sqrt_price_next >= sqrt_price_x96:
            # Numerical degeneracy (amount_in too small relative to liquidity).
            return 0
        # amount1_out = L * (sqrtP_curr - sqrtP_next) / Q96
        delta = sqrt_price_x96 - sqrt_price_next
        return (liquidity * delta) // Q96

    # one_for_zero: price moves up, token1 in, token0 out.
    # sqrtP_next = sqrtP_curr + (amount_in * Q96) / L
    sqrt_price_next = sqrt_price_x96 + (amount_in_eff * Q96) // liquidity
    if sqrt_price_next <= sqrt_price_x96:
        return 0
    delta = sqrt_price_next - sqrt_price_x96
    # amount0_out = L * delta * Q96 / (sqrtP_curr * sqrtP_next)
    return (liquidity * delta * Q96) // (sqrt_price_x96 * sqrt_price_next)


def spot_price_token0_per_token1(sqrt_price_x96: int) -> float:
    """Convenience: return the spot price of token0 in units of token1.

    For UniV3, sqrtPriceX96 = sqrt(token1/token0) * 2^96.
    So price (token1/token0) = (sqrtPriceX96 / 2^96)^2.
    Caller still needs to apply token decimal adjustment to get human-readable
    units; this returns the raw on-chain ratio.
    """
    if sqrt_price_x96 <= 0:
        return 0.0
    ratio = sqrt_price_x96 / Q96
    return ratio * ratio


__all__ = ["quote_swap", "spot_price_token0_per_token1", "Q96"]
