"""Gas + flash-loan cost estimator for hypothetical arbitrage paths on Base.

Phase 1.2 detection-only modeling. The numbers here represent the cost a
real flash-loan-funded execution would face; the detector subtracts this
from gross arbitrage gain before applying the 0.3% margin floor.

Cost components:
  1. L2 execution gas — sum of per-protocol swap gas + flash-loan callback
     overhead + per-bundle fixed overhead. Multiplied by current Base base-fee
     (gwei) and current ETH/USD price.
  2. L1 data fee — Base posts calldata to Ethereum mainnet. Approximated as
     a fixed offset; for typical 2-hop swaps with ~1500 bytes of calldata at
     current L1 base fees this is $0.01-0.05. Treated as part of the per-
     bundle overhead.
  3. Flash-loan provider fee — Aave V3 charges 0.05% of borrowed amount.
     For a $10K notional arb the fee is $5, which dominates gas costs on
     Base. Phase 2 execution work may select Balancer V3 (free flash loans
     for whitelisted tokens), but conservative detection should use Aave's
     fee to avoid false-positive opportunities that wouldn't be profitable
     under realistic borrowing.

Spot price source:
  ETH/USD is derived from the canonical WETH/USDC UniV3 pool's slot0
  sqrtPriceX96 — no external oracle, no extra RPC, the value is already
  in `BlockPoolStates.uniswap_v3` for any block where that pool's state
  was successfully fetched.
"""

from __future__ import annotations

from typing import Iterable

from .pool_set import PoolInfo, PoolProtocol


# Per-swap gas cost (L2 execution, conservative empirical estimates).
# Validated against on-chain transactions for representative pools; may drift
# as protocols upgrade. Phase 1.2 acceptance dry-run can refine.
_GAS_UNITS_PER_SWAP: dict[PoolProtocol, int] = {
    PoolProtocol.UNISWAP_V3: 120_000,
    PoolProtocol.AERODROME_SLIPSTREAM: 120_000,
    PoolProtocol.AERODROME_VOLATILE: 80_000,
    PoolProtocol.AERODROME_STABLE: 100_000,  # stableswap math is more expensive
}

FLASH_LOAN_CALLBACK_GAS = 50_000
PER_BUNDLE_OVERHEAD_GAS = 30_000  # includes typical L1 data fee at Base scale

# Aave V3 flash loan fee on Base. 0.05% on borrowed amount.
# https://docs.aave.com/developers/guides/flash-loans
AAVE_V3_FLASH_LOAN_BPS = 5

# Reasonable Base default base fee (gwei). Phase 1.2 acceptance run can pin
# this to the actual block's baseFeePerGas if BlockPoolStates is extended.
DEFAULT_BASE_FEE_GWEI = 0.01

# WETH and USDC contract addresses on Base (lowercase). Used to identify the
# canonical pricing pool for ETH/USD.
WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


def gas_units_for_path(pools: Iterable[PoolInfo]) -> int:
    """Total gas units to execute a swap path through the given pools, including
    flash-loan callback overhead and a per-bundle fixed cost."""
    swap_gas = sum(_GAS_UNITS_PER_SWAP.get(p.protocol, 120_000) for p in pools)
    return swap_gas + FLASH_LOAN_CALLBACK_GAS + PER_BUNDLE_OVERHEAD_GAS


def gas_cost_usd(gas_units: int, base_fee_gwei: float, eth_usd_price: float) -> float:
    """USD cost: gas_units * (base_fee_gwei * 1e-9 ETH/gas) * (USD/ETH)."""
    if gas_units <= 0 or base_fee_gwei < 0 or eth_usd_price < 0:
        return 0.0
    eth_per_gas = base_fee_gwei * 1e-9
    return gas_units * eth_per_gas * eth_usd_price


def flash_loan_fee_usd(
    amount_borrowed_usd: float,
    fee_bps: int = AAVE_V3_FLASH_LOAN_BPS,
) -> float:
    """Flash-loan provider fee in USD. Default = Aave V3's 0.05%."""
    if amount_borrowed_usd <= 0 or fee_bps <= 0:
        return 0.0
    return amount_borrowed_usd * fee_bps / 10_000


def total_execution_cost_usd(
    *,
    pools: Iterable[PoolInfo],
    base_fee_gwei: float,
    eth_usd_price: float,
    amount_borrowed_usd: float,
    flash_loan_bps: int = AAVE_V3_FLASH_LOAN_BPS,
) -> float:
    """Total USD cost of a flash-loan-funded swap path.

        cost = gas_cost(path) + flash_loan_fee(amount_borrowed)

    Detector compares this against `gross_gain_usd` to decide profitability.
    """
    gas = gas_cost_usd(
        gas_units_for_path(pools), base_fee_gwei, eth_usd_price,
    )
    fl = flash_loan_fee_usd(amount_borrowed_usd, flash_loan_bps)
    return gas + fl


def eth_usd_from_weth_usdc_v3(sqrt_price_x96: int) -> float:
    """ETH/USD spot derived from a WETH/USDC UniV3 pool's slot0.

    Assumes the pool's token0 is WETH and token1 is USDC — true for the
    canonical WETH/USDC pools on Base because WETH (0x4200…) sorts below
    USDC (0x8335…). Caller is responsible for verifying via PoolInfo before
    invoking; if the assumption is wrong the returned price is junk.

    Math:
        sqrtPriceX96 = sqrt(token1_raw / token0_raw) * 2^96
                     = sqrt(USDC_raw / WETH_raw) * 2^96
        price_raw    = (sqrtPriceX96 / 2^96)^2 = USDC_raw / WETH_raw

    Convert raw to human:
        ETH_in_USDC = price_raw * (10^WETH_decimals / 10^USDC_decimals)
                    = price_raw * 10^(18-6)
                    = price_raw * 1e12
    """
    if sqrt_price_x96 <= 0:
        return 0.0
    Q96 = 1 << 96
    ratio = sqrt_price_x96 / Q96
    return ratio * ratio * 1e12


def find_canonical_eth_usd_pool(
    pool_set,
    weth_address: str = WETH_BASE,
    usdc_address: str = USDC_BASE,
) -> "PoolInfo | None":
    """Pick the highest-TVL WETH/USDC UniV3 pool from the monitored set, to
    use as the ETH/USD price source. Returns None if no such pool exists.

    Phase 2 sub-phase 2.3 (D-009): `weth_address` and `usdc_address` are now
    parameters (defaulting to Base for back-compat). Callers on Arb/OP pass
    chain-specific addresses from the token registry.
    """
    weth_lower = weth_address.lower()
    usdc_lower = usdc_address.lower()
    candidates = []
    for p in pool_set.pools:
        if p.protocol != PoolProtocol.UNISWAP_V3:
            continue
        t0 = p.token0.address.lower()
        t1 = p.token1.address.lower()
        if {t0, t1} == {weth_lower, usdc_lower}:
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.tvl_usd_at_enumeration)


__all__ = [
    "gas_units_for_path",
    "gas_cost_usd",
    "flash_loan_fee_usd",
    "total_execution_cost_usd",
    "eth_usd_from_weth_usdc_v3",
    "find_canonical_eth_usd_pool",
    "AAVE_V3_FLASH_LOAN_BPS",
    "DEFAULT_BASE_FEE_GWEI",
    "FLASH_LOAN_CALLBACK_GAS",
    "PER_BUNDLE_OVERHEAD_GAS",
    "WETH_BASE",
    "USDC_BASE",
]
