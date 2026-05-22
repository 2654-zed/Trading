"""Phase 1.2 — two-hop arbitrage opportunity detector.

Per spec §Phase 1.2, an opportunity is a path

    Token A -> Pool X -> Token B -> Pool Y -> Token A

where the round-trip yields:
  - >= 0.3% gross margin (pre-slippage, after pool fees)
  - amount_out_usd > amount_in_usd + estimated_execution_cost_usd
  - both pools in the monitored set
  - input token has at least $10K notional depth in the relevant pool direction

Phase 1.2 limitation (documented in spec stop-gate report):
The detector only sizes opportunities where the borrowed (input) token is WETH
or USDC. This covers the overwhelming majority of monitored pools (every pool
has WETH or USDC as token0 or token1 except a few stable-coin and CBBTC-paired
exotics). Arbs that don't touch WETH or USDC are skipped pending a Phase 2
extension that adds USD pricing for additional tokens.

Algorithm per block:
  1. From `BlockPoolStates`, look up the canonical WETH/USDC pool to derive
     ETH/USD spot.
  2. For each token pair with >= 2 pools in the monitored set:
     For each ordered pair (X, Y) with X != Y:
       For each input direction (A or B as the borrowed token), provided the
       input token is WETH or USDC:
         - Quote leg 1: amount_in (token_A) -> amount_mid (token_B) via X.
         - Quote leg 2: amount_mid (token_B) -> amount_out (token_A) via Y.
         - Compute gross_margin = (amount_out - amount_in) / amount_in.
         - If gross_margin < 0.003 (= 0.3%): skip.
         - Compute USD gain and execution cost.
         - If USD gain <= cost: skip.
         - Emit Opportunity.

The detector is stateless (apart from the precomputed pair index): each call
to detect() processes one block in isolation. State changes between blocks
are reflected in the new BlockPoolStates passed in.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from .gas_estimator import (
    AAVE_V3_FLASH_LOAN_BPS,
    DEFAULT_BASE_FEE_GWEI,
    USDC_BASE,
    WETH_BASE,
    eth_usd_from_weth_usdc_v3,
    find_canonical_eth_usd_pool,
    total_execution_cost_usd,
)
from .pool_monitor import AerodromePoolState, BlockPoolStates, UniV3PoolState
from .pool_set import PoolInfo, PoolProtocol, PoolSet
from .quote import quote_swap_via_pool
from .token_registry import DEFAULT as DEFAULT_TOKEN_REGISTRY, TokenRegistry


GROSS_MARGIN_FLOOR = 0.003  # spec §Phase 1.2: 0.3%
NOTIONAL_USD_FLOOR = 10_000.0  # spec §Phase 1.2: $10K depth

# Phase 2 sub-phase 2.3 (D-009): cross-chain margin floor. Raised from
# Phase 1's 0.30% to absorb the ~20 bps round-trip bridge cost. Per spec
# H1' falsification: a "cross-chain opportunity" must clear this floor
# AFTER all bridge + haircut + flash + gas costs.
GROSS_MARGIN_FLOOR_CROSS_CHAIN = 0.005  # 0.50%


@dataclass(frozen=True)
class PoolLeg:
    """One hop in an arbitrage path."""
    pool_address: str
    protocol: str  # PoolProtocol value
    fee_bps: Optional[int]
    token_in_addr: str
    token_in_symbol: str
    token_out_addr: str
    token_out_symbol: str
    amount_in_raw: int
    amount_out_raw: int


@dataclass(frozen=True)
class Opportunity:
    """A round-trip arbitrage opportunity that cleared all spec gates.

    Fields mirror the spec §Phase 1.2 acceptance criterion:
      "Opportunities emitted include: timestamp, path (pools and tokens),
       computed margin, gas estimate, depth markers"

    Phase 2 sub-phase 2.3 (D-009): the dataclass picks up optional
    cross-chain fields. All are defaulted so Phase 1 callsites and
    intra-chain detection paths remain unchanged.
    """
    opportunity_id: str
    block_number: int
    block_timestamp: int
    detected_at: str  # ISO 8601 UTC

    # Path: Phase 1.2 is strictly 2-leg.
    legs: tuple[PoolLeg, PoolLeg]

    # Borrowed token (= leg[0].token_in = leg[1].token_out).
    borrowed_token_addr: str
    borrowed_token_symbol: str
    notional_usd: float  # $10K in Phase 1.2

    # Profitability.
    amount_in_raw: int
    amount_out_raw: int
    gross_margin: float       # (amount_out - amount_in) / amount_in, decimal
    gross_gain_usd: float
    gas_cost_usd: float
    flash_loan_fee_usd: float
    expected_net_gain_usd: float  # gross - gas - flash_loan_fee

    # --- Phase 2 cross-chain fields (defaulted; only set for cross-chain opps).
    # Empty tuple = intra-chain opportunity (Phase 1 shape).
    bridge_legs: tuple = ()  # tuple[BridgeLeg, ...] — typed via bridge_model
    # Chain per pool hop. For Phase 1 / intra-chain: empty (back-compat) or
    # ("base", "base") if explicitly populated. For cross-chain (sub-phase 2.3
    # spec variant 1): ("dst", "dst") for the two intra-chain hops on dst_chain.
    path_chains: tuple = ()  # tuple[str, ...]
    # Pre-haircut gross margin; equals `gross_margin` for intra-chain opps,
    # higher than `gross_margin` for cross-chain (haircut subtracts from it).
    gross_margin_raw: float = 0.0
    # Bps of margin consumed by the latency-drift haircut (I-14). 0 for
    # intra-chain opps.
    latency_drift_haircut_bps: float = 0.0
    # The chain where the flash loan happens (= where capital is borrowed
    # and repaid). For intra-chain opps this equals the pool chains. For
    # cross-chain opps this is the SRC chain in the bridge round-trip.
    borrow_chain: str = "base"
    # Total bridge fees paid (bps of notional), summed across all bridge_legs.
    bridge_fee_bps_total: float = 0.0

    @property
    def is_cross_chain(self) -> bool:
        return len(self.bridge_legs) > 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["legs"] = [asdict(l) for l in self.legs]
        # bridge_legs are bridge_model.BridgeLeg instances (frozen dataclasses);
        # asdict on the parent handles them, but produces nested dicts.
        return d


@dataclass(frozen=True)
class _SizingHelper:
    """Per-detector cached unit conversions for the supported borrow tokens.

    Phase 2 sub-phase 2.3 (D-009): the WETH + USDC addresses are now
    instance state (resolved per chain via the token registry), not module
    constants — so a chain-aware detector can size correctly on Arb / OP.
    """
    weth_address: str
    usdc_address: str
    weth_decimals: int = 18
    usdc_decimals: int = 6

    def usd_to_token_raw(self, token_addr: str, usd: float, eth_usd_price: float) -> int:
        token_lower = token_addr.lower()
        if token_lower == self.weth_address.lower():
            return int(usd / eth_usd_price * (10 ** self.weth_decimals))
        if token_lower == self.usdc_address.lower():
            return int(usd * (10 ** self.usdc_decimals))
        raise ValueError(f"unsized token: {token_addr}")

    def token_raw_to_usd(self, token_addr: str, raw: int, eth_usd_price: float) -> float:
        token_lower = token_addr.lower()
        if token_lower == self.weth_address.lower():
            return raw / (10 ** self.weth_decimals) * eth_usd_price
        if token_lower == self.usdc_address.lower():
            return raw / (10 ** self.usdc_decimals)
        raise ValueError(f"unpriced token: {token_addr}")

    @property
    def priceable_borrow_tokens(self) -> frozenset:
        return frozenset({self.weth_address.lower(), self.usdc_address.lower()})


# Phase 1 back-compat: module-level constant for the Base set. Kept so any
# external callers still importing it don't break. Internally, the detector
# now uses `self._sizer.priceable_borrow_tokens` so each chain's instance
# uses the right addresses.
_PRICEABLE_BORROW_TOKENS = frozenset({WETH_BASE.lower(), USDC_BASE.lower()})


class OpportunityDetector:
    """Stateless per-block arbitrage detector.

    Construct once with the frozen PoolSet (builds the pair index). Call
    detect() with each new BlockPoolStates from the monitor.
    """

    def __init__(
        self,
        pool_set: PoolSet,
        *,
        chain: str = "base",
        token_registry: TokenRegistry = DEFAULT_TOKEN_REGISTRY,
    ) -> None:
        # Phase 2 sub-phase 2.3 (D-009): per-chain instantiation. Token
        # registry resolves WETH + USDC addresses per chain so the same
        # detector class works for Base / Arb / OP. `chain` defaults to
        # "base" so Phase 1 callsites (single-arg constructor) remain
        # unchanged.
        self._pool_set = pool_set
        self._chain = chain
        self._token_registry = token_registry

        weth_addr = token_registry.weth_address(chain)
        usdc_addr = token_registry.usdc_address(chain)
        if weth_addr is None or usdc_addr is None:
            raise ValueError(
                f"OpportunityDetector cannot size on chain {chain!r}: "
                f"WETH={weth_addr}, USDC={usdc_addr}. "
                f"Token registry must have canonical WETH+USDC for the chain."
            )
        self._sizer = _SizingHelper(
            weth_address=weth_addr,
            usdc_address=usdc_addr,
            weth_decimals=token_registry.decimals("WETH"),
            usdc_decimals=token_registry.decimals("USDC"),
        )

        # Pair index: frozenset({token0_addr, token1_addr}) -> list of pools.
        # Pools sorted by protocol name + fee for deterministic ordering.
        self._pair_index: dict[frozenset, list[PoolInfo]] = {}
        for p in pool_set.pools:
            key = frozenset({p.token0.address.lower(), p.token1.address.lower()})
            self._pair_index.setdefault(key, []).append(p)

        # Canonical WETH/USDC pool for ETH/USD spot, looked up against the
        # chain-specific addresses. None if not in monitored set.
        self._eth_usd_pool = find_canonical_eth_usd_pool(
            pool_set, weth_address=weth_addr, usdc_address=usdc_addr,
        )

    @property
    def chain(self) -> str:
        return self._chain

    @property
    def pair_count(self) -> int:
        return len(self._pair_index)

    @property
    def arbitrageable_pair_count(self) -> int:
        """Number of pairs with >= 2 pools — i.e. candidates for cross-pool arb."""
        return sum(1 for pools in self._pair_index.values() if len(pools) >= 2)

    def derive_eth_usd_price(self, states: BlockPoolStates) -> Optional[float]:
        """Return ETH/USD spot from the canonical WETH/USDC pool's slot0,
        or None if the pool wasn't fetched this block (or wasn't in the set)."""
        if self._eth_usd_pool is None:
            return None
        st = states.uniswap_v3.get(self._eth_usd_pool.address)
        if st is None:
            return None
        # Verify token ordering: WETH should be token0 for the on-chain math.
        # Comparison uses chain-specific WETH address from the registry.
        if self._eth_usd_pool.token0.address.lower() != self._sizer.weth_address.lower():
            return None
        return eth_usd_from_weth_usdc_v3(st.sqrt_price_x96)

    def detect(
        self,
        states: BlockPoolStates,
        *,
        base_fee_gwei: float = DEFAULT_BASE_FEE_GWEI,
        flash_loan_bps: int = AAVE_V3_FLASH_LOAN_BPS,
        eth_usd_price_override: Optional[float] = None,
    ) -> list[Opportunity]:
        """Scan all multi-pool pairs for round-trip arbitrage opportunities
        clearing the 0.3% margin floor and the cost-vs-gain check.

        Returns a (possibly empty) list of opportunities. Order is deterministic
        — pairs in pair-index insertion order, pool combos in nested-loop order.
        """
        eth_usd = eth_usd_price_override
        if eth_usd is None:
            eth_usd = self.derive_eth_usd_price(states)
        if eth_usd is None or eth_usd <= 0:
            # Without ETH/USD we can't size or convert gains to USD.
            return []

        opportunities: list[Opportunity] = []
        for pair_key, pools in self._pair_index.items():
            if len(pools) < 2:
                continue
            pair_addrs = list(pair_key)
            for i in range(len(pools)):
                for j in range(len(pools)):
                    if i == j:
                        continue
                    pool_x = pools[i]
                    pool_y = pools[j]
                    for borrow_addr in pair_addrs:
                        # Phase 2 sub-phase 2.3: chain-specific priceable set
                        # (chain's WETH + USDC from the registry). Phase 1
                        # back-compat preserved by the "base" default.
                        if borrow_addr not in self._sizer.priceable_borrow_tokens:
                            continue
                        opp = self._evaluate(
                            pool_x=pool_x, pool_y=pool_y,
                            borrow_addr=borrow_addr,
                            states=states,
                            eth_usd=eth_usd,
                            base_fee_gwei=base_fee_gwei,
                            flash_loan_bps=flash_loan_bps,
                        )
                        if opp is not None:
                            opportunities.append(opp)
        return opportunities

    def _evaluate(
        self,
        *,
        pool_x: PoolInfo,
        pool_y: PoolInfo,
        borrow_addr: str,
        states: BlockPoolStates,
        eth_usd: float,
        base_fee_gwei: float,
        flash_loan_bps: int,
    ) -> Optional[Opportunity]:
        """Quote both legs and decide whether the round trip clears the floor."""
        # Other token in the pair (B in A->X->B->Y->A).
        if pool_x.token0.address.lower() == borrow_addr:
            mid_token = pool_x.token1
        else:
            mid_token = pool_x.token0
        borrow_token = pool_x.token0 if pool_x.token0.address.lower() == borrow_addr else pool_x.token1

        # Get state for both pools (skip if either is missing — gap markers
        # earlier in the monitor will already have logged the cause).
        state_x = self._state_for(pool_x, states)
        state_y = self._state_for(pool_y, states)
        if state_x is None or state_y is None:
            return None

        # Size $10K notional in raw units of the borrow token.
        try:
            amount_in_raw = self._sizer.usd_to_token_raw(borrow_addr, NOTIONAL_USD_FLOOR, eth_usd)
        except ValueError:
            return None
        if amount_in_raw <= 0:
            return None

        # Leg 1: borrow -> mid via pool_x.
        amount_mid_raw = quote_swap_via_pool(pool_x, state_x, borrow_addr, amount_in_raw)
        if amount_mid_raw <= 0:
            return None
        # Leg 2: mid -> borrow via pool_y.
        amount_out_raw = quote_swap_via_pool(pool_y, state_y, mid_token.address, amount_mid_raw)
        if amount_out_raw <= 0:
            return None

        if amount_out_raw <= amount_in_raw:
            return None
        gross_margin = (amount_out_raw - amount_in_raw) / amount_in_raw
        if gross_margin < GROSS_MARGIN_FLOOR:
            return None

        # Convert to USD for cost comparison.
        gross_gain_usd = self._sizer.token_raw_to_usd(
            borrow_addr, amount_out_raw - amount_in_raw, eth_usd,
        )
        cost_usd = total_execution_cost_usd(
            pools=(pool_x, pool_y),
            base_fee_gwei=base_fee_gwei,
            eth_usd_price=eth_usd,
            amount_borrowed_usd=NOTIONAL_USD_FLOOR,
            flash_loan_bps=flash_loan_bps,
        )
        if gross_gain_usd <= cost_usd:
            return None

        # Decompose cost into gas vs flash-loan for the record.
        from .gas_estimator import flash_loan_fee_usd, gas_units_for_path, gas_cost_usd
        gas_usd = gas_cost_usd(
            gas_units_for_path((pool_x, pool_y)), base_fee_gwei, eth_usd,
        )
        fl_usd = flash_loan_fee_usd(NOTIONAL_USD_FLOOR, flash_loan_bps)

        leg1 = PoolLeg(
            pool_address=pool_x.address,
            protocol=pool_x.protocol.value,
            fee_bps=pool_x.fee_bps,
            token_in_addr=borrow_token.address,
            token_in_symbol=borrow_token.symbol,
            token_out_addr=mid_token.address,
            token_out_symbol=mid_token.symbol,
            amount_in_raw=amount_in_raw,
            amount_out_raw=amount_mid_raw,
        )
        leg2 = PoolLeg(
            pool_address=pool_y.address,
            protocol=pool_y.protocol.value,
            fee_bps=pool_y.fee_bps,
            token_in_addr=mid_token.address,
            token_in_symbol=mid_token.symbol,
            token_out_addr=borrow_token.address,
            token_out_symbol=borrow_token.symbol,
            amount_in_raw=amount_mid_raw,
            amount_out_raw=amount_out_raw,
        )

        return Opportunity(
            opportunity_id=str(uuid.uuid4()),
            block_number=states.block_number,
            block_timestamp=states.block_timestamp,
            detected_at=datetime.now(timezone.utc).isoformat(),
            legs=(leg1, leg2),
            borrowed_token_addr=borrow_token.address,
            borrowed_token_symbol=borrow_token.symbol,
            notional_usd=NOTIONAL_USD_FLOOR,
            amount_in_raw=amount_in_raw,
            amount_out_raw=amount_out_raw,
            gross_margin=gross_margin,
            gross_gain_usd=gross_gain_usd,
            gas_cost_usd=gas_usd,
            flash_loan_fee_usd=fl_usd,
            expected_net_gain_usd=gross_gain_usd - cost_usd,
        )

    def _state_for(self, pool: PoolInfo, states: BlockPoolStates):
        """Return the right state object for `pool` from `states`, or None
        if the pool's state wasn't fetched this block."""
        if pool.protocol == PoolProtocol.UNISWAP_V3:
            return states.uniswap_v3.get(pool.address)
        # Slipstream-family CL state lives in `states.slipstream` regardless
        # of which chain's Slipstream variant deployed it.
        if pool.protocol in (
            PoolProtocol.AERODROME_SLIPSTREAM,
            PoolProtocol.VELODROME_SLIPSTREAM,
        ):
            return states.slipstream.get(pool.address)
        # All UniV2-fork CPAMM + Solidly stable pools share the getReserves
        # state shape and live in `states.aerodrome`.
        if pool.protocol in (
            PoolProtocol.AERODROME_VOLATILE,
            PoolProtocol.AERODROME_STABLE,
            PoolProtocol.VELODROME_VOLATILE,
            PoolProtocol.VELODROME_STABLE,
            PoolProtocol.CAMELOT_V2,
            PoolProtocol.SUSHISWAP_V2,
        ):
            return states.aerodrome.get(pool.address)
        # CAMELOT_V3 intentionally returns None — its state isn't fetched in
        # 2.3 (Algebra V3 decoder deferred to 2.4 prep).
        return None


__all__ = [
    "OpportunityDetector",
    "Opportunity",
    "PoolLeg",
    "GROSS_MARGIN_FLOOR",
    "NOTIONAL_USD_FLOOR",
]
