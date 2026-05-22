"""Phase 2 sub-phase 2.3 (D-009) — cross-chain opportunity detector.

Detects round-trip cross-chain arbitrage opportunities of the form:

    borrow TOKEN_A on src_chain (flash from Aave V3)
      → swap TOKEN_A → TOKEN_B on pool_src (src_chain) at src price P_src
      → bridge TOKEN_B src → dst via Across (fee + latency)
      → swap TOKEN_B → TOKEN_A on pool_dst (dst_chain) at dst price P_dst
      → bridge TOKEN_A dst → src via Across (fee + latency)
      → repay flash on src_chain

The opportunity is the inter-chain price drift between `pool_src` and
`pool_dst`. If P_dst > P_src by enough to cover (2× bridge fees +
latency-drift haircut + flash-loan fee + 2× swap gas + intra-chain swap
fees), the path clears the cross-chain margin floor (0.50%).

This is the canonical cross-chain arb shape that real traders pursue
(one pool per chain, price drift between chains). The spec's example
pseudocode (PHASE_2_CROSS_CHAIN_SPEC.md sub-phase 2.3 "Opportunity
definition") drew a variant with both swaps on dst_chain; that variant
is structurally never profitable in isolation because if an intra-chain
arb cleared the bridge cost it would also clear the (lower) intra-chain
floor. Detected and flagged as a follow-up in the sub-phase 2.3 stop
report.

What this module provides:
  - `MultiChainPoolSet` helper for managing per-chain pool subsets
  - `CrossChainDetector` class — composes per-chain pricing + bridge model
  - `CrossChainDetector.detect(states_by_chain)` — scans price drift
    across all (src, dst, borrowed_token, mid_token) tuples that the
    registry + bridge model permit
  - Emits Opportunity records with `bridge_legs` and `path_chains`
    populated; `gross_margin_raw` = pre-haircut margin;
    `latency_drift_haircut_bps` = haircut applied

What this module does NOT do:
  - No live bridge fee queries (uses BridgeModel's static table)
  - No 3-chain triangle paths (out of scope per spec)
  - No support for tokens outside the canonical registry (I-12)
  - No live order-book or partial-fill modeling
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .bridge_model import DEFAULT as DEFAULT_BRIDGE_MODEL, BridgeModel, BridgeLeg
from .gas_estimator import (
    AAVE_V3_FLASH_LOAN_BPS,
    DEFAULT_BASE_FEE_GWEI,
    flash_loan_fee_usd,
    gas_cost_usd,
    gas_units_for_path,
)
from .opportunity_detector import (
    GROSS_MARGIN_FLOOR_CROSS_CHAIN,
    NOTIONAL_USD_FLOOR,
    Opportunity,
    OpportunityDetector,
    PoolLeg,
)
from .pool_monitor import BlockPoolStates
from .pool_set import PoolInfo, PoolSet
from .quote import quote_swap_via_pool
from .token_registry import DEFAULT as DEFAULT_TOKEN_REGISTRY, TokenRegistry


@dataclass(frozen=True)
class _ChainContext:
    """Per-chain state held by CrossChainDetector."""
    chain: str
    detector: OpportunityDetector       # used for ETH/USD pricing + sizing
    pool_set: PoolSet
    pair_index: dict[frozenset, list[PoolInfo]]  # (token0, token1) -> pools


class CrossChainDetector:
    """Detects cross-chain arbitrage paths across the Base/Arb/OP triangle.

    Stateful: caches the most-recent `BlockPoolStates` per chain. When
    `detect(states_by_chain)` is called, the caller passes the freshest
    state per chain — typically the just-emitted state for the triggering
    chain plus the cached state for the others. The detector uses both
    sides to compute price drift and emit cross-chain Opportunity objects.

    Pricing references (ETH/USD) come from each chain's own
    OpportunityDetector instance; the cross-chain detector treats them
    as black-box sizers.
    """

    def __init__(
        self,
        per_chain_detectors: dict[str, OpportunityDetector],
        bridge_model: BridgeModel = DEFAULT_BRIDGE_MODEL,
        token_registry: TokenRegistry = DEFAULT_TOKEN_REGISTRY,
        *,
        margin_floor: float = GROSS_MARGIN_FLOOR_CROSS_CHAIN,
        notional_usd: float = NOTIONAL_USD_FLOOR,
        flash_loan_chain: str = "base",
    ):
        if not per_chain_detectors:
            raise ValueError("CrossChainDetector requires at least one chain")
        self._bridge_model = bridge_model
        self._registry = token_registry
        self._margin_floor = float(margin_floor)
        self._notional_usd = float(notional_usd)
        # Phase 2 sub-phase 2.3: per spec D-006, flash loans happen on the
        # src_chain side. For our triangle, Base is the default flash-loan
        # source (Aave V3 USDC liquidity is deepest on Base of the three).
        # Configurable so cross-chain detection can later run with src=Arb
        # if Aave V3 on Arb proves cheaper.
        self._flash_loan_chain = flash_loan_chain

        self._contexts: dict[str, _ChainContext] = {}
        for chain, detector in per_chain_detectors.items():
            pair_index: dict[frozenset, list[PoolInfo]] = {}
            for p in detector._pool_set.pools:
                key = frozenset({p.token0.address.lower(), p.token1.address.lower()})
                pair_index.setdefault(key, []).append(p)
            self._contexts[chain] = _ChainContext(
                chain=chain, detector=detector,
                pool_set=detector._pool_set, pair_index=pair_index,
            )

        # Precompute which (src, dst, borrowed_token, mid_token) tuples are
        # actually scannable: bridgeable per registry + bridge model, AND
        # both chains have at least one pool for the (borrowed, mid) pair.
        # Done once at construction so detect() stays hot-path-cheap.
        self._scan_routes: list[tuple] = []
        self._build_scan_routes()

    @property
    def chains(self) -> tuple[str, ...]:
        return tuple(sorted(self._contexts.keys()))

    @property
    def route_count(self) -> int:
        return len(self._scan_routes)

    def _build_scan_routes(self) -> None:
        """Enumerate (src_chain, dst_chain, borrow_token_sym, mid_token_sym)
        tuples for which both chains carry a pool with both tokens AND the
        bridge route is supported for both legs."""
        for borrow_sym, mid_sym in self._candidate_token_pairs():
            # Skip same-token pairs (borrow = mid is degenerate).
            if borrow_sym == mid_sym:
                continue
            for src_chain in self._contexts:
                for dst_chain in self._contexts:
                    if src_chain == dst_chain:
                        continue
                    # Borrowed token bridges src→dst then dst→src (round-trip).
                    # Mid token bridges src→dst (or dst→src depending on which
                    # direction we bridge first). Both must be supported.
                    if not self._bridge_model.is_route_supported(
                        src_chain, dst_chain, borrow_sym,
                    ):
                        continue
                    if not self._bridge_model.is_route_supported(
                        dst_chain, src_chain, borrow_sym,
                    ):
                        continue
                    if not self._bridge_model.is_route_supported(
                        src_chain, dst_chain, mid_sym,
                    ):
                        continue
                    # Resolve addresses per chain via registry (I-12 already
                    # enforced by is_route_supported).
                    borrow_src = self._registry.canonical_address(borrow_sym, src_chain)
                    borrow_dst = self._registry.canonical_address(borrow_sym, dst_chain)
                    mid_src = self._registry.canonical_address(mid_sym, src_chain)
                    mid_dst = self._registry.canonical_address(mid_sym, dst_chain)
                    # Need at least one pool on src with (borrow_src, mid_src)
                    # and one on dst with (borrow_dst, mid_dst).
                    src_pair_key = frozenset({borrow_src, mid_src})
                    dst_pair_key = frozenset({borrow_dst, mid_dst})
                    src_pools = self._contexts[src_chain].pair_index.get(src_pair_key, [])
                    dst_pools = self._contexts[dst_chain].pair_index.get(dst_pair_key, [])
                    if not src_pools or not dst_pools:
                        continue
                    self._scan_routes.append((
                        src_chain, dst_chain, borrow_sym, mid_sym,
                        borrow_src, borrow_dst, mid_src, mid_dst,
                        tuple(src_pools), tuple(dst_pools),
                    ))

    def _candidate_token_pairs(self) -> list[tuple[str, str]]:
        """Candidate (borrow_token, mid_token) symbol pairs. Both must be
        canonical (per I-12) on at least two chains. Order matters
        (USDC/WETH treated separately from WETH/USDC because the borrow
        direction differs)."""
        pairs: list[tuple[str, str]] = []
        # Limit borrow tokens to those we know how to size in USD
        # (WETH + USDC — same constraint as Phase 1 OpportunityDetector).
        for borrow_sym in ("USDC", "WETH"):
            for mid_sym in self._registry.symbols:
                if mid_sym == borrow_sym:
                    continue
                pairs.append((borrow_sym, mid_sym))
        return pairs

    def detect(
        self,
        states_by_chain: dict[str, BlockPoolStates],
        *,
        base_fee_gwei: float = DEFAULT_BASE_FEE_GWEI,
        flash_loan_bps: int = AAVE_V3_FLASH_LOAN_BPS,
    ) -> list[Opportunity]:
        """Scan all pre-enumerated cross-chain routes against the
        provided per-chain states. Returns Opportunity records that clear
        the cross-chain margin floor after bridge fees + latency-drift
        haircut + flash-loan + gas costs."""
        # Need src + dst states for any opportunity. If a chain's state
        # is missing, skip any route that touches it.
        opportunities: list[Opportunity] = []

        # Derive ETH/USD per chain (used for sizing + USD conversion).
        eth_usd_by_chain: dict[str, float] = {}
        for chain, ctx in self._contexts.items():
            states = states_by_chain.get(chain)
            if states is None:
                continue
            price = ctx.detector.derive_eth_usd_price(states)
            if price is not None and price > 0:
                eth_usd_by_chain[chain] = price

        # If src OR dst lacks an ETH/USD price, we can't size or convert
        # gain to USD. The dst-side price is needed even when the borrow
        # token is USDC (mid token might be WETH-denominated).
        for route in self._scan_routes:
            (src_chain, dst_chain, borrow_sym, mid_sym,
             borrow_src, borrow_dst, mid_src, mid_dst,
             src_pools, dst_pools) = route
            states_src = states_by_chain.get(src_chain)
            states_dst = states_by_chain.get(dst_chain)
            if states_src is None or states_dst is None:
                continue
            if src_chain not in eth_usd_by_chain or dst_chain not in eth_usd_by_chain:
                continue
            eth_usd_src = eth_usd_by_chain[src_chain]
            eth_usd_dst = eth_usd_by_chain[dst_chain]
            src_ctx = self._contexts[src_chain]
            dst_ctx = self._contexts[dst_chain]

            # Size borrow notional in src-chain raw units.
            try:
                amount_in_raw = src_ctx.detector._sizer.usd_to_token_raw(
                    borrow_src, self._notional_usd, eth_usd_src,
                )
            except ValueError:
                continue
            if amount_in_raw <= 0:
                continue

            # For each (src_pool, dst_pool) candidate, quote the round trip.
            for src_pool in src_pools:
                src_state = src_ctx.detector._state_for(src_pool, states_src)
                if src_state is None:
                    continue
                # Leg 1: borrow_src → mid_src via src_pool.
                amount_mid_src_raw = quote_swap_via_pool(
                    src_pool, src_state, borrow_src, amount_in_raw,
                )
                if amount_mid_src_raw <= 0:
                    continue

                # Across pays the bridge fee out of the bridged amount. We
                # model the fee deduction at dst (Across actually deducts
                # at source but the accounting equivalence holds for
                # detection-only purposes).
                bridge_fee_src_to_dst_bps = self._bridge_model.fee_bps(
                    src_chain, dst_chain, mid_sym,
                )
                amount_mid_dst_raw = int(
                    amount_mid_src_raw * (10_000 - bridge_fee_src_to_dst_bps) / 10_000
                )
                if amount_mid_dst_raw <= 0:
                    continue

                for dst_pool in dst_pools:
                    dst_state = dst_ctx.detector._state_for(dst_pool, states_dst)
                    if dst_state is None:
                        continue
                    # Leg 2: mid_dst → borrow_dst via dst_pool.
                    amount_borrow_dst_raw = quote_swap_via_pool(
                        dst_pool, dst_state, mid_dst, amount_mid_dst_raw,
                    )
                    if amount_borrow_dst_raw <= 0:
                        continue
                    # Bridge borrow_dst → borrow_src.
                    bridge_fee_dst_to_src_bps = self._bridge_model.fee_bps(
                        dst_chain, src_chain, borrow_sym,
                    )
                    amount_out_raw = int(
                        amount_borrow_dst_raw * (10_000 - bridge_fee_dst_to_src_bps) / 10_000
                    )
                    if amount_out_raw <= amount_in_raw:
                        continue

                    gross_margin_raw = (amount_out_raw - amount_in_raw) / amount_in_raw
                    # Latency-drift haircut (I-14): subtract from raw margin.
                    haircut_bps = self._bridge_model.latency_drift_haircut_bps(
                        mid_sym, self._notional_usd,
                    )
                    gross_margin = gross_margin_raw - (haircut_bps / 10_000)
                    if gross_margin < self._margin_floor:
                        continue

                    # USD gain (denominate on src chain because that's where
                    # the flash loan repays).
                    gross_gain_usd = src_ctx.detector._sizer.token_raw_to_usd(
                        borrow_src, amount_out_raw - amount_in_raw, eth_usd_src,
                    )
                    # Gas: two swap legs + two bridge sends. Use the avg of
                    # src+dst eth_usd for gas USD conversion (gas paid on the
                    # chain executing each leg). For simplicity in 2.3 we
                    # use src_chain gas for both legs — refinement deferred
                    # if measurement shows materially different per-chain gas.
                    gas_usd_total = gas_cost_usd(
                        gas_units_for_path((src_pool, dst_pool)),
                        base_fee_gwei, eth_usd_src,
                    )
                    flash_usd = flash_loan_fee_usd(
                        self._notional_usd, flash_loan_bps,
                    )
                    cost_usd = gas_usd_total + flash_usd
                    if gross_gain_usd <= cost_usd:
                        continue

                    # Materialize Opportunity. Per the schema migration in
                    # sub-phase 2.5: bridge_legs is a tuple of BridgeLeg
                    # objects; path_chains gives the chain per pool hop.
                    leg1 = PoolLeg(
                        pool_address=src_pool.address,
                        protocol=src_pool.protocol.value,
                        fee_bps=src_pool.fee_bps,
                        token_in_addr=borrow_src,
                        token_in_symbol=borrow_sym,
                        token_out_addr=mid_src,
                        token_out_symbol=mid_sym,
                        amount_in_raw=amount_in_raw,
                        amount_out_raw=amount_mid_src_raw,
                    )
                    leg2 = PoolLeg(
                        pool_address=dst_pool.address,
                        protocol=dst_pool.protocol.value,
                        fee_bps=dst_pool.fee_bps,
                        token_in_addr=mid_dst,
                        token_in_symbol=mid_sym,
                        token_out_addr=borrow_dst,
                        token_out_symbol=borrow_sym,
                        amount_in_raw=amount_mid_dst_raw,
                        amount_out_raw=amount_borrow_dst_raw,
                    )
                    bridge_leg_outbound = self._bridge_model.make_bridge_leg(
                        src_chain, dst_chain, mid_sym,
                    )
                    bridge_leg_return = self._bridge_model.make_bridge_leg(
                        dst_chain, src_chain, borrow_sym,
                    )
                    # Detection-time block_number comes from the src-chain
                    # state (where the flash loan starts).
                    opp = Opportunity(
                        opportunity_id=str(uuid.uuid4()),
                        block_number=states_src.block_number,
                        block_timestamp=states_src.block_timestamp,
                        detected_at=datetime.now(timezone.utc).isoformat(),
                        legs=(leg1, leg2),
                        borrowed_token_addr=borrow_src,
                        borrowed_token_symbol=borrow_sym,
                        notional_usd=self._notional_usd,
                        amount_in_raw=amount_in_raw,
                        amount_out_raw=amount_out_raw,
                        gross_margin=gross_margin,
                        gross_gain_usd=gross_gain_usd,
                        gas_cost_usd=gas_usd_total,
                        flash_loan_fee_usd=flash_usd,
                        expected_net_gain_usd=gross_gain_usd - cost_usd,
                        # --- cross-chain fields
                        bridge_legs=(bridge_leg_outbound, bridge_leg_return),
                        path_chains=(src_chain, dst_chain),
                        gross_margin_raw=gross_margin_raw,
                        latency_drift_haircut_bps=haircut_bps,
                        borrow_chain=src_chain,
                        bridge_fee_bps_total=(
                            bridge_fee_src_to_dst_bps + bridge_fee_dst_to_src_bps
                        ),
                    )
                    opportunities.append(opp)
        return opportunities


__all__ = ["CrossChainDetector"]
