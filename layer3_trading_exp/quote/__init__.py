"""AMM swap quote functions for the four AMM types in the monitored set.

Each module exposes a `quote_swap` function with the same signature shape:

    quote_swap(state, token_in_addr, amount_in_raw, fee_pips, *protocol_specifics)
        -> amount_out_raw

`amount_in_raw` and `amount_out_raw` are integer token units (NOT decimals-scaled).
`fee_pips` is the swap fee in hundredths of a basis point (e.g. 0.30% = 3000).

The dispatcher `quote_swap_via_pool(pool, state, token_in_addr, amount_in_raw)`
selects the right module by `pool.protocol` and pulls fee + auxiliary args from
`pool` and `state`.
"""

from __future__ import annotations

from typing import Union

from ..pool_monitor import AerodromePoolState, UniV3PoolState
from ..pool_set import PoolInfo, PoolProtocol
from . import cl_quote, cpamm_quote, stableswap_quote


# Aerodrome v1 default fees per protocol type, used when PoolInfo.fee_bps is None.
# Aerodrome's PoolFactory is parameterizable per-pool but in practice nearly all
# pools use the protocol defaults. Phase 1.2 acceptance run can validate by
# calling factory.getFee(pool, stable) for each pool; backfill into PoolInfo
# pending. Documented in PHASE_1_1_ADDENDUM.md.
AERODROME_VOLATILE_DEFAULT_FEE_BPS = 30
AERODROME_STABLE_DEFAULT_FEE_BPS = 5


def quote_swap_via_pool(
    pool: PoolInfo,
    state: Union[UniV3PoolState, AerodromePoolState],
    token_in_addr: str,
    amount_in_raw: int,
) -> int:
    """Dispatch to the right AMM math module for `pool.protocol`. Returns
    amount_out in raw token units of the OTHER token in the pool.

    Caller is responsible for ensuring `state` matches `pool` (i.e. the state
    came from monitor's bucket for this protocol). Mismatch → wrong math.
    """
    token_in_lower = token_in_addr.lower()
    if token_in_lower not in (pool.token0.address.lower(), pool.token1.address.lower()):
        raise ValueError(
            f"token_in {token_in_addr} not in pool {pool.address} "
            f"(token0={pool.token0.address}, token1={pool.token1.address})"
        )
    zero_for_one = token_in_lower == pool.token0.address.lower()

    # Phase 1 + Phase 2 concentrated-liquidity protocols (UniV3-style slot0+liquidity).
    # Velodrome Slipstream uses identical math to Aerodrome Slipstream (Aerodrome
    # forked it). Camelot V3 (Algebra V3) is skipped at the monitor layer in 2.3;
    # the quote dispatcher would otherwise need Algebra-aware fee handling.
    if pool.protocol in (
        PoolProtocol.UNISWAP_V3,
        PoolProtocol.AERODROME_SLIPSTREAM,
        PoolProtocol.VELODROME_SLIPSTREAM,
    ):
        if not isinstance(state, UniV3PoolState):
            raise TypeError(f"CL pool {pool.address} requires UniV3PoolState, got {type(state).__name__}")
        fee_pips = (pool.fee_bps or 0) * 100
        return cl_quote.quote_swap(
            sqrt_price_x96=state.sqrt_price_x96,
            liquidity=state.liquidity,
            amount_in=amount_in_raw,
            zero_for_one=zero_for_one,
            fee_pips=fee_pips,
        )

    # UniV2-style CPAMM volatile pools (Aerodrome v1 volatile, Velodrome v1 volatile,
    # Camelot V2, SushiSwap V2). All share the constant-product formula.
    if pool.protocol in (
        PoolProtocol.AERODROME_VOLATILE,
        PoolProtocol.VELODROME_VOLATILE,
        PoolProtocol.CAMELOT_V2,
        PoolProtocol.SUSHISWAP_V2,
    ):
        if not isinstance(state, AerodromePoolState):
            raise TypeError(f"CPAMM pool {pool.address} ({pool.protocol.value}) "
                            f"requires AerodromePoolState")
        fee_bps = pool.fee_bps if pool.fee_bps is not None else AERODROME_VOLATILE_DEFAULT_FEE_BPS
        return cpamm_quote.quote_swap(
            reserve0=state.reserve0,
            reserve1=state.reserve1,
            amount_in=amount_in_raw,
            zero_for_one=zero_for_one,
            fee_bps=fee_bps,
        )

    # Solidly stable pools (Aerodrome v1 stable, Velodrome v1 stable). Same
    # x^3*y + y^3*x = k curve, same fee semantics.
    if pool.protocol in (PoolProtocol.AERODROME_STABLE, PoolProtocol.VELODROME_STABLE):
        if not isinstance(state, AerodromePoolState):
            raise TypeError(f"Solidly stable pool {pool.address} ({pool.protocol.value}) "
                            f"requires AerodromePoolState")
        fee_bps = pool.fee_bps if pool.fee_bps is not None else AERODROME_STABLE_DEFAULT_FEE_BPS
        return stableswap_quote.quote_swap(
            reserve0=state.reserve0,
            reserve1=state.reserve1,
            decimals0=pool.token0.decimals,
            decimals1=pool.token1.decimals,
            amount_in=amount_in_raw,
            zero_for_one=zero_for_one,
            fee_bps=fee_bps,
        )

    raise ValueError(f"unsupported protocol for quote: {pool.protocol!r}")


__all__ = [
    "quote_swap_via_pool",
    "cl_quote",
    "cpamm_quote",
    "stableswap_quote",
    "AERODROME_VOLATILE_DEFAULT_FEE_BPS",
    "AERODROME_STABLE_DEFAULT_FEE_BPS",
]
