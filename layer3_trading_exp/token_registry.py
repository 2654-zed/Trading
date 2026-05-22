"""Phase 2 sub-phase 2.3 (D-009) — curated cross-chain token registry.

Per I-12 (PHASE_2_CROSS_CHAIN_SPEC.md): same-token-different-chain identity
is canonical-only. A token is "the same token across chains" iff it appears
in `_CANONICAL_TOKENS` below. We do NOT infer same-token from symbol matching
or wrapped/unwrapped variants at runtime.

Address sourcing: native deployments only — i.e. Circle-issued native USDC,
the OP-stack WETH predeploy, etc. Bridged variants (USDC.e, USDT.e) are
deliberately excluded because cross-chain detection assumes Across moves
the native version. Across automatically routes to the right native asset
when both endpoints support it; if either side is bridged-only, Across
typically can't serve that route, so omitting them keeps the registry
honest about what's actually bridgeable.

What this module provides:
  - `TokenRegistry.canonical_address(symbol, chain)` — lookup
  - `TokenRegistry.is_canonical(address, chain)` — guard for I-12 enforcement
  - `TokenRegistry.symbol_for(address, chain)` — reverse lookup
  - `TokenRegistry.decimals(symbol)` — per-token decimals (chain-invariant)
  - `TokenRegistry.bridgeable_pairs()` — (src_chain, dst_chain, symbol) tuples
    where the symbol exists on both sides → eligible for Across routing
  - `TokenRegistry.weth_address(chain)`, `usdc_address(chain)` — helpers for
    `OpportunityDetector` price-of-ETH derivation

What this module does NOT do:
  - No auto-discovery (per spec's "what NOT to build")
  - No bridged-variant handling
  - No fee math (that's `bridge_model.py`)
  - No price data
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional


# Canonical tokens for Phase 2. Keyed by symbol → chain_label → address.
# All addresses are checksummed but stored lowercase for case-insensitive
# comparison. Decimals are chain-invariant for these tokens (verified by
# spot-checking each token's deployed contract; the upstream issuers
# preserve decimals across chains for the same token).
_CANONICAL_TOKENS: dict[str, dict[str, str]] = {
    "USDC": {
        # Native Circle USDC (not USDbC, not USDC.e — the bridged variants).
        "base":     "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "arbitrum": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        "optimism": "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
    },
    "WETH": {
        # OP-stack predeploy address on Base + Optimism; canonical on Arbitrum.
        "base":     "0x4200000000000000000000000000000000000006",
        "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        "optimism": "0x4200000000000000000000000000000000000006",
    },
    "USDT": {
        # Native USDT not well-established on Base (only bridged variants);
        # only Arb + OP carry native USDT in this registry. Per I-12, Across
        # routes including USDT-on-Base will be filtered out by
        # `bridgeable_pairs`.
        "arbitrum": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        "optimism": "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",
    },
    "DAI": {
        "base":     "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",
        "arbitrum": "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",
        "optimism": "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",
    },
    "cbBTC": {
        # Coinbase Wrapped BTC — Coinbase has only deployed natively on Base
        # (and Ethereum mainnet). Not bridgeable to Arb/OP at this writing,
        # so paths involving cbBTC will be Base-only in `bridgeable_pairs`.
        "base":     "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf",
    },
}

# Decimals are chain-invariant for the canonical set.
_DECIMALS: dict[str, int] = {
    "USDC":  6,
    "WETH":  18,
    "USDT":  6,
    "DAI":   18,
    "cbBTC": 8,
}


@dataclass(frozen=True)
class TokenLookup:
    """Result of a (symbol, chain) → address lookup."""
    symbol: str
    chain: str
    address: str
    decimals: int


class TokenRegistry:
    """Curated cross-chain token registry per I-12.

    The registry is immutable. Instances are cheap (single dict reference
    sharing); the module-level `DEFAULT` instance is sufficient for the
    runtime. Tests can construct an alternate registry by passing a custom
    mapping to `__init__`.
    """

    def __init__(
        self,
        canonical_tokens: Optional[dict[str, dict[str, str]]] = None,
        decimals: Optional[dict[str, int]] = None,
    ):
        # Defensive copy + lowercase normalization for addresses.
        src = canonical_tokens if canonical_tokens is not None else _CANONICAL_TOKENS
        self._by_symbol: dict[str, dict[str, str]] = {
            sym: {chain: addr.lower() for chain, addr in chain_map.items()}
            for sym, chain_map in src.items()
        }
        # Reverse index: (chain, lowercase address) -> symbol. Built once.
        self._by_address: dict[tuple[str, str], str] = {}
        for sym, chain_map in self._by_symbol.items():
            for chain, addr in chain_map.items():
                self._by_address[(chain, addr)] = sym
        self._decimals: dict[str, int] = dict(decimals or _DECIMALS)

    # ----- single-chain lookups ----------------------------------------------

    def canonical_address(self, symbol: str, chain: str) -> Optional[str]:
        """Return the canonical address for `symbol` on `chain`, or None
        if not in the registry. Lowercase."""
        return self._by_symbol.get(symbol, {}).get(chain)

    def is_canonical(self, address: str, chain: str) -> bool:
        """True iff (chain, address) is one of our curated canonical tokens.
        Address comparison is case-insensitive."""
        return (chain, address.lower()) in self._by_address

    def symbol_for(self, address: str, chain: str) -> Optional[str]:
        """Reverse lookup: which canonical token is this address on `chain`?
        Returns None if the address isn't in the registry."""
        return self._by_address.get((chain, address.lower()))

    def decimals(self, symbol: str) -> int:
        """Per-token decimals. Raises KeyError for unknown symbols."""
        return self._decimals[symbol]

    def lookup(self, symbol: str, chain: str) -> Optional[TokenLookup]:
        addr = self.canonical_address(symbol, chain)
        if addr is None:
            return None
        return TokenLookup(symbol=symbol, chain=chain, address=addr,
                           decimals=self._decimals[symbol])

    # ----- cross-chain operators ---------------------------------------------

    def chains_for(self, symbol: str) -> tuple[str, ...]:
        """Chains on which `symbol` has a canonical deployment.

        Used by `bridge_model` to gate Across routes: a route (src, dst,
        symbol) is only valid if `symbol` is canonical on BOTH src and dst.
        """
        return tuple(sorted(self._by_symbol.get(symbol, {}).keys()))

    def bridgeable_pairs(self) -> Iterator[tuple[str, str, str]]:
        """Yield every (src_chain, dst_chain, symbol) tuple where the
        symbol has a canonical deployment on both sides.

        Source of truth for what cross-chain routes are detectable.
        Per I-12 and bridge_model.is_route_supported. Excludes (src == dst)
        — that's intra-chain and uses the regular detector.
        """
        for symbol, chain_map in self._by_symbol.items():
            chains = sorted(chain_map.keys())
            for src in chains:
                for dst in chains:
                    if src == dst:
                        continue
                    yield (src, dst, symbol)

    # ----- convenience helpers used by OpportunityDetector pricing ----------

    def weth_address(self, chain: str) -> Optional[str]:
        """Per-chain WETH lookup. Returns lowercase address or None."""
        return self.canonical_address("WETH", chain)

    def usdc_address(self, chain: str) -> Optional[str]:
        """Per-chain USDC lookup (native Circle USDC). Lowercase or None."""
        return self.canonical_address("USDC", chain)

    # ----- introspection -----------------------------------------------------

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_symbol.keys()))

    def __repr__(self) -> str:
        chain_summary = ", ".join(
            f"{sym}({len(chain_map)})"
            for sym, chain_map in sorted(self._by_symbol.items())
        )
        return f"TokenRegistry({chain_summary})"


# Module-level default — the canonical registry for the Phase 2 run.
DEFAULT = TokenRegistry()


__all__ = ["TokenRegistry", "TokenLookup", "DEFAULT"]
