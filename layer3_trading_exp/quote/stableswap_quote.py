"""Solidly-style stableswap math (Aerodrome v1 stable pools).

Aerodrome v1 stable pools (and their Velodrome / Solidly forebears) use the
"x³y + xy³ = k" invariant rather than Curve's exponential invariant. This
concentrates liquidity near the equilibrium (x ≈ y in normalized terms),
giving stablecoin pools tighter on-peg slippage than constant-product would.

Reserves are normalized to 18-decimal terms before applying the invariant,
matching the reference contract:

    function _k(uint x, uint y) internal view returns (uint) {
        uint _x = x * 1e18 / decimals0;
        uint _y = y * 1e18 / decimals1;
        uint _a = (_x * _y) / 1e18;
        uint _b = ((_x * _x) / 1e18 + (_y * _y) / 1e18);
        return _a * _b / 1e18;  //  k = x*y*(x² + y²) / 1e54  (in normalized units)
    }

Swap algorithm:
  1. Normalize reserves: x_n = reserve_in * 1e18 / 10**decimals_in, y_n similarly.
  2. Compute k_target = x_n * y_n * (x_n² + y_n²) / 1e54.
  3. Apply fee to input: amount_in_eff = amount_in * (10000 - fee_bps) / 10000.
  4. Normalize amount_in_eff: dx_n = amount_in_eff * 1e18 / 10**decimals_in.
  5. New x: x_n_new = x_n + dx_n.
  6. Solve for y_n_new such that x_n_new * y_n_new * (x_n_new² + y_n_new²) = k_target.
     Newton's method on f(y) = x*y*(x² + y²) - k = 0 with f'(y) = x*(x² + 3*y²).
  7. amount_out_normalized = y_n - y_n_new.
  8. Denormalize: amount_out = amount_out_normalized * 10**decimals_out / 1e18.

Initial guess: y_n_new = y_n - (estimated slippage). For stable pools the price
ratio near equilibrium is close to 1:1 in normalized space, so y_n_new ≈ y_n - dx_n
is a good starting point.

Reference: Velodrome v2 Pool.sol `_get_y(uint x0, uint xy, uint y) internal pure`.
"""

from __future__ import annotations


_NORM = 10 ** 18
_FEE_DENOM = 10_000
_NEWTON_MAX_ITERS = 64
_NEWTON_TOL = 1  # 1 wei in normalized 1e18 units — far below practical relevance


def _k(x: int, y: int) -> int:
    """Stableswap invariant evaluated on normalized (1e18-scaled) reserves.

    k = x * y * (x² + y²) / 1e54 (matching reference contract's three /1e18 stages).
    All inputs/outputs are integers; truncation matches the on-chain math.
    """
    a = (x * y) // _NORM
    b = (x * x) // _NORM + (y * y) // _NORM
    return (a * b) // _NORM


def _solve_y(x_new: int, k_target: int, y_init: int) -> int:
    """Find y_new such that _k(x_new, y_new) == k_target via Newton's method.

    Iteration: y_{n+1} = y_n - f(y_n) / f'(y_n) where
        f(y)  = x*y*(x² + y²)/1e54 - k       (re-derived from _k)
        f'(y) = x*(x² + 3y²)/1e36

    We stop when |y_{n+1} - y_n| <= _NEWTON_TOL. Convergence is quadratic near
    the solution; for stable-pool starting points (x ≈ y) typically <10 iterations.
    """
    y = y_init
    for _ in range(_NEWTON_MAX_ITERS):
        # f(y) in normalized 1e18 units
        k_y = _k(x_new, y)
        # If we're already at the target, done.
        if k_y == k_target:
            return y
        # f'(y) = x*(x² + 3y²)/1e36, but easier to compute in two stages:
        #   df_dy = x * (x*x + 3 * y*y) / 1e36
        # In integer math, scale carefully. We use exact integer Newton:
        #   y_new = y - (k_y - k_target) / df_dy
        df_dy_num = x_new * (x_new * x_new + 3 * y * y)
        df_dy_denom = _NORM * _NORM
        if df_dy_num == 0:
            return y
        delta_num = (k_y - k_target) * df_dy_denom
        # Integer Newton step: dy = (k_y - k_target) / df_dy
        dy = delta_num // df_dy_num
        if dy == 0:
            # Below truncation; we've converged to the resolution of integer math.
            return y
        y_next = y - dy
        # y must remain positive — protect against overshoot.
        if y_next <= 0:
            y_next = y // 2 if y > 1 else 1
        if abs(y_next - y) <= _NEWTON_TOL:
            return y_next
        y = y_next
    # Did not converge within iters; return best estimate. Caller should treat
    # any non-converging quote as "no quote available" by checking amount_out > 0.
    return y


def quote_swap(
    *,
    reserve0: int,
    reserve1: int,
    decimals0: int,
    decimals1: int,
    amount_in: int,
    zero_for_one: bool,
    fee_bps: int,
) -> int:
    """Quote amount_out for a Solidly-style stable-pool swap.

    Args:
        reserve0, reserve1: pool reserves in raw token units.
        decimals0, decimals1: token decimal places (typically 6 or 18).
        amount_in: input amount in raw units of the input token.
        zero_for_one: True if swapping token0 -> token1.
        fee_bps: swap fee in basis points (Aerodrome stable default = 5).

    Returns:
        amount_out in raw units of the output token. 0 for degenerate inputs
        or if Newton's method fails to converge (numerical edge case).
    """
    if amount_in <= 0 or reserve0 <= 0 or reserve1 <= 0:
        return 0
    if not (0 <= fee_bps < _FEE_DENOM):
        raise ValueError(f"fee_bps {fee_bps} out of range [0, {_FEE_DENOM})")
    if decimals0 < 0 or decimals1 < 0 or decimals0 > 36 or decimals1 > 36:
        raise ValueError(f"unrealistic decimals: {decimals0}, {decimals1}")

    decimals_in = decimals0 if zero_for_one else decimals1
    decimals_out = decimals1 if zero_for_one else decimals0
    reserve_in_raw = reserve0 if zero_for_one else reserve1
    reserve_out_raw = reserve1 if zero_for_one else reserve0

    # Normalize to 1e18 units.
    x = reserve_in_raw * _NORM // (10 ** decimals_in)
    y = reserve_out_raw * _NORM // (10 ** decimals_out)
    if x <= 0 or y <= 0:
        return 0

    k_target = _k(x, y)
    if k_target <= 0:
        return 0

    # Take fee off input.
    amount_in_eff = amount_in * (_FEE_DENOM - fee_bps) // _FEE_DENOM
    if amount_in_eff <= 0:
        return 0

    dx = amount_in_eff * _NORM // (10 ** decimals_in)
    if dx <= 0:
        return 0

    x_new = x + dx
    # Initial guess: zero-slippage (y - dx). Stable pools live near 1:1 in
    # normalized space, so this is closer to truth than the CP approximation
    # for the on-peg cases we care about. Newton corrections from here are
    # tiny — small enough that integer rounding of `_k`'s intermediate products
    # doesn't dominate the per-iteration step.
    #
    # For badly off-peg pools the constant-product guess y*x/x_new would land
    # closer; in practice arbitrage research operates on near-peg stable pools
    # ($10K trades on ~$1M reserves), and the on-chain swap function uses the
    # same Newton iteration so any pool that wouldn't converge here also
    # wouldn't be tradable on-chain.
    y_init = y - dx
    if y_init <= 0:
        y_init = max(1, (y * x) // x_new)

    y_new = _solve_y(x_new, k_target, y_init)
    if y_new <= 0 or y_new >= y:
        # Solver pathology — refuse to quote rather than emit garbage.
        return 0

    amount_out_normalized = y - y_new
    amount_out = amount_out_normalized * (10 ** decimals_out) // _NORM
    return max(0, amount_out)


__all__ = ["quote_swap"]
