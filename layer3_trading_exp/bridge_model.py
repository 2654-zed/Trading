"""Phase 2 sub-phase 2.3 (D-009) — Across Protocol bridge model.

Per D-006 + PHASE_2_CROSS_CHAIN_SPEC.md sub-phase 2.4: bridge fees are a
static table loaded at run start (I-13). The live Across API is queried
once at deployment time (`scripts/verify_across_fees.py` — produced in
sub-phase 2.4) to confirm the static values are within ±5 bps of live;
this module never makes live API calls during detection.

What this module provides:
  - `BridgeLeg` dataclass: one bridge hop with src/dst/token/fee/latency
  - `BridgeModel.fee_bps(src, dst, token)` — static fee lookup
  - `BridgeModel.is_route_supported(src, dst, token)` — gate via I-12 +
    Across's actual route coverage
  - `BridgeModel.latency_seconds(src, dst, token)` — usually 30s per D-006
    point estimate; per-token overrides allowed
  - `BridgeModel.latency_drift_haircut_bps(token, notional_usd)` — I-14
    haircut for destination-pool price-drift over the bridge latency window
  - `BridgeModel.bridge_cost_bps(src, dst, token)` — full round-trip cost
    helper: fee_bps + haircut

What this module does NOT do:
  - No live API calls
  - No Across SDK
  - No slippage modeling beyond the simple bps haircut
  - No L1 finality consideration (we model L2↔L2 Across fills only)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .token_registry import DEFAULT as DEFAULT_TOKEN_REGISTRY, TokenRegistry


# Spec sub-phase 2.4 STARTING VALUES (D-006 baselines). The
# deployment-time verification step rewrites any value that drifts > 5 bps
# from live Across. >20 bps drift aborts deployment.
#
# Symmetric across src/dst pairs by default — Across publishes the same
# fee for both directions on each L2↔L2 route. If we observe asymmetry
# during verification, the static table is updated to use
# (src, dst, token) keys.
_DEFAULT_FEE_BPS_BY_TOKEN: dict[str, float] = {
    "USDC":  10.0,   # D-006 point estimate
    "WETH":  8.0,    # Across historical
    "USDT":  12.0,   # Across historical
    "DAI":   10.0,   # D-006 point estimate
    "cbBTC": 15.0,   # Across historical
}


# Per-token volatility class for latency-drift haircut (I-14). Higher
# vol → wider drift window over the 30s fill latency. Numbers are bps
# of notional per 30s of latency, calibrated for the post-2024 L2 vol
# regime (subject to refresh if vol regimes shift; not adaptive at runtime
# per I-5).
#
# Stablecoins: ~10 bps for any 30s window is generous (their actual 30s
# vol is ~1-3 bps). The spec defaults to 0.1% (10 bps) uniformly per I-14;
# we keep that floor and let WETH / cbBTC bump to a class-appropriate value.
_DRIFT_HAIRCUT_BPS_BY_TOKEN: dict[str, float] = {
    "USDC":  10.0,    # I-14 default
    "USDT":  10.0,
    "DAI":   10.0,
    "WETH":  15.0,    # ~50% bump for the more-volatile asset
    "cbBTC": 20.0,    # ~2x for BTC class
}


# Default bridge latency per D-006: 30 seconds for Across L2↔L2 fills.
_DEFAULT_LATENCY_SECONDS: float = 30.0


@dataclass(frozen=True)
class BridgeLeg:
    """One bridge hop. Used in Opportunity.bridge_legs (schema migration
    lands in sub-phase 2.5; the dataclass is introduced here so 2.3's
    cross-chain detector can populate it)."""
    src_chain: str
    dst_chain: str
    token: str           # symbol, e.g. "USDC"
    token_address_src: str  # lowercase address on src chain
    token_address_dst: str  # lowercase address on dst chain
    fee_bps: float       # static, from BridgeModel.fee_bps()
    latency_seconds: float

    def to_dict(self) -> dict:
        return {
            "src_chain": self.src_chain,
            "dst_chain": self.dst_chain,
            "token": self.token,
            "token_address_src": self.token_address_src,
            "token_address_dst": self.token_address_dst,
            "fee_bps": float(self.fee_bps),
            "latency_seconds": float(self.latency_seconds),
        }


class BridgeModel:
    """Static Across fee + latency model. Per I-13, frozen for the run.

    Construct once with an optional override fee table (e.g. from the
    deployment-time verifier output). Calls into this class are pure
    functions — no I/O, no random access — so it's safe to call from
    inside the asyncio event loop.
    """

    def __init__(
        self,
        token_registry: TokenRegistry = DEFAULT_TOKEN_REGISTRY,
        fee_bps_by_token: Optional[dict[str, float]] = None,
        drift_haircut_bps_by_token: Optional[dict[str, float]] = None,
        default_latency_seconds: float = _DEFAULT_LATENCY_SECONDS,
        fee_bps_by_route: Optional[dict[tuple[str, str, str], float]] = None,
    ):
        self._token_registry = token_registry
        self._fee_bps_by_token = dict(fee_bps_by_token or _DEFAULT_FEE_BPS_BY_TOKEN)
        self._drift_haircut = dict(drift_haircut_bps_by_token or _DRIFT_HAIRCUT_BPS_BY_TOKEN)
        self._default_latency = float(default_latency_seconds)
        # Optional per-route fee override (e.g. populated by the deploy-time
        # verifier when live Across quotes differ between routes). Keyed by
        # (src_chain, dst_chain, token_symbol).
        self._fee_bps_by_route = dict(fee_bps_by_route or {})

    # ----- gate / route validation ------------------------------------------

    def is_route_supported(self, src_chain: str, dst_chain: str, token: str) -> bool:
        """True iff (src, dst, token) is a route Across can plausibly serve
        AND the token is canonical on both chains (I-12).

        Across supports the Base/Arb/OP triangle for our canonical token set
        (verified at module-write-time; the deployment verifier confirms).
        Same-chain routes are rejected — those are intra-chain trades, not
        bridges.
        """
        if src_chain == dst_chain:
            return False
        # I-12 enforcement: both ends must be in the canonical registry.
        if not self._token_registry.canonical_address(token, src_chain):
            return False
        if not self._token_registry.canonical_address(token, dst_chain):
            return False
        # Across explicitly does not serve some niche assets; for our
        # canonical set on the Base/Arb/OP triangle it serves all combos
        # we'd plausibly use. cbBTC only exists on Base in our registry,
        # so the bridgeable_pairs filter on the token side already excludes
        # cbBTC routes.
        return True

    # ----- fee + latency lookups --------------------------------------------

    def fee_bps(self, src_chain: str, dst_chain: str, token: str) -> float:
        """Static fee for one bridge hop, in basis points (bps).

        Lookup order:
          1. Per-route override (`fee_bps_by_route` constructor arg)
          2. Per-token default

        Raises KeyError if `token` isn't in the default fee table — i.e.
        the spec's curated set. Callers should `is_route_supported` first.
        """
        per_route = self._fee_bps_by_route.get((src_chain, dst_chain, token))
        if per_route is not None:
            return float(per_route)
        return float(self._fee_bps_by_token[token])

    def latency_seconds(self, src_chain: str, dst_chain: str, token: str) -> float:
        """Latency for one bridge hop. D-006 point estimate is 30s for
        Across L2↔L2 fills. Per-route variation is small enough that we
        treat it as constant here; if measurement shows otherwise we
        introduce a per-route override later (same pattern as fee_bps)."""
        return self._default_latency

    # ----- I-14 haircut for destination-pool price drift --------------------

    def latency_drift_haircut_bps(self, token: str, notional_usd: float) -> float:
        """How much margin (in bps) to deduct to absorb 30s of destination-
        chain price drift on `token`.

        Currently insensitive to notional_usd — drift is multiplicative on
        price, not on size — but we keep `notional_usd` in the signature
        so a notional-aware refinement (size discount) can land later
        without an API change.

        Raises KeyError for unknown tokens; callers should validate against
        the registry before calling this.
        """
        return float(self._drift_haircut[token])

    # ----- composed cost helpers --------------------------------------------

    def round_trip_cost_bps(
        self, src_chain: str, dst_chain: str, token: str,
        *, notional_usd: float = 10_000.0,
    ) -> float:
        """Total bps cost of a round-trip bridge structure for one token:

            cost = 2 × fee_bps + latency_drift_haircut_bps

        The haircut counts once because the price-drift risk applies to
        one end of the round-trip (the destination swap is at a different
        time than detection; the return-leg swap happens roughly
        simultaneously with the first return-leg bridge initiation).

        For asymmetric routes (different fee per direction), the cost is
        `fee(src→dst) + fee(dst→src) + haircut`, computed per direction.
        """
        fee_out = self.fee_bps(src_chain, dst_chain, token)
        fee_back = self.fee_bps(dst_chain, src_chain, token)
        haircut = self.latency_drift_haircut_bps(token, notional_usd)
        return fee_out + fee_back + haircut

    def make_bridge_leg(
        self, src_chain: str, dst_chain: str, token: str,
    ) -> BridgeLeg:
        """Construct a BridgeLeg using current static parameters.

        Raises if the route isn't supported — callers can use
        is_route_supported() to gate. Token addresses are resolved through
        the bound token_registry.
        """
        if not self.is_route_supported(src_chain, dst_chain, token):
            raise ValueError(
                f"unsupported route: {src_chain}->{dst_chain} for {token}. "
                f"Pre-check with is_route_supported()."
            )
        return BridgeLeg(
            src_chain=src_chain,
            dst_chain=dst_chain,
            token=token,
            token_address_src=self._token_registry.canonical_address(token, src_chain),
            token_address_dst=self._token_registry.canonical_address(token, dst_chain),
            fee_bps=self.fee_bps(src_chain, dst_chain, token),
            latency_seconds=self.latency_seconds(src_chain, dst_chain, token),
        )

    # ----- introspection -----------------------------------------------------

    @property
    def supported_tokens(self) -> tuple[str, ...]:
        return tuple(sorted(self._fee_bps_by_token.keys()))

    def __repr__(self) -> str:
        fees = ", ".join(
            f"{t}={bps:g}bps" for t, bps in sorted(self._fee_bps_by_token.items())
        )
        return f"BridgeModel(latency={self._default_latency:g}s; {fees})"

    # ----- sub-phase 2.4: load verified table ------------------------------

    @classmethod
    def load_verified_table(
        cls,
        path: Path,
        *,
        token_registry: TokenRegistry = DEFAULT_TOKEN_REGISTRY,
        default_latency_seconds: float = _DEFAULT_LATENCY_SECONDS,
        drift_haircut_bps_by_token: Optional[dict[str, float]] = None,
    ) -> "BridgeModel":
        """Construct a `BridgeModel` from the JSON file produced by
        `scripts/verify_across_fees.py`.

        The file schema:
          {
            "schema_version": 1,
            "verified_at": "<iso8601>",
            "notional_usd": <float>,
            "drift_thresholds_bps": {"no_op": 5.0, "abort": 20.0},
            "routes_bps": {"<src>-><dst>:<symbol>": <bps>, ...},
          }

        Per I-13: this table is read once at start and frozen. The
        returned `BridgeModel` rebuilds `_fee_bps_by_route` from
        `routes_bps`, so detection uses the verified values.
        Default per-token fees are kept as a fallback for any route the
        verifier didn't cover (e.g. an OP↔Arb route Across temporarily
        didn't quote).
        """
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if int(payload.get("schema_version", 0)) != 1:
            raise ValueError(
                f"unsupported across_fee_table schema_version: "
                f"{payload.get('schema_version')!r}"
            )
        routes_raw = payload.get("routes_bps", {})
        fee_by_route: dict[tuple[str, str, str], float] = {}
        for key, bps in routes_raw.items():
            # Key format: "<src>-><dst>:<symbol>"
            try:
                left, sym = key.rsplit(":", 1)
                src, dst = left.split("->", 1)
            except ValueError as e:
                raise ValueError(
                    f"malformed across_fee_table key {key!r}: {e}"
                ) from e
            fee_by_route[(src.strip(), dst.strip(), sym.strip())] = float(bps)
        return cls(
            token_registry=token_registry,
            fee_bps_by_route=fee_by_route,
            drift_haircut_bps_by_token=drift_haircut_bps_by_token,
            default_latency_seconds=default_latency_seconds,
        )


# Module-level default — wired against the default token registry.
DEFAULT = BridgeModel()


__all__ = ["BridgeLeg", "BridgeModel", "DEFAULT"]
