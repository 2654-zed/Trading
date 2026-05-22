"""Constant-product AMM swap math (Uniswap V2 / Aerodrome v1 volatile).

Invariant: x * y = k. Fee is taken off the input side before applying the swap.

Closed form: amount_out = (amount_in_eff * reserve_out) / (reserve_in + amount_in_eff)
where amount_in_eff = amount_in * (10000 - fee_bps) / 10000.

For the closed form to be exact in integer arithmetic, cross-multiply:
    amount_out = amount_in * (10000 - fee_bps) * reserve_out
                 / (reserve_in * 10000 + amount_in * (10000 - fee_bps))
"""

from __future__ import annotations


_FEE_DENOM = 10_000


def quote_swap(
    *,
    reserve0: int,
    reserve1: int,
    amount_in: int,
    zero_for_one: bool,
    fee_bps: int,
) -> int:
    """Quote amount_out for a constant-product swap.

    Args:
        reserve0, reserve1: pool reserves in raw token units.
        amount_in: input amount in raw units of the input token.
        zero_for_one: True if swapping token0 -> token1, else token1 -> token0.
        fee_bps: swap fee in basis points (e.g. 30 = 0.30%).

    Returns:
        amount_out in raw units of the output token. 0 if amount_in or
        reserves are non-positive (no liquidity), preventing div-by-zero.
    """
    if amount_in <= 0 or reserve0 <= 0 or reserve1 <= 0:
        return 0
    if not (0 <= fee_bps < _FEE_DENOM):
        raise ValueError(f"fee_bps {fee_bps} out of range [0, {_FEE_DENOM})")

    reserve_in, reserve_out = (reserve0, reserve1) if zero_for_one else (reserve1, reserve0)
    fee_factor = _FEE_DENOM - fee_bps
    # amount_in * fee_factor — we keep this product because both numerator
    # and denominator share it, so any common factor in token decimals cancels.
    amount_in_with_fee = amount_in * fee_factor
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * _FEE_DENOM + amount_in_with_fee
    return numerator // denominator


__all__ = ["quote_swap"]
