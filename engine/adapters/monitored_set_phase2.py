"""Adapter: wrap Phase 1+2's `pool_set.PoolSet` as a
`MonitoredSetSource` per `engine/lenses/graph/lens.py`'s protocol.

Read-only. The adapter does not modify the underlying PoolSet — it just
materializes pool records in the shape lenses expect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from layer3_trading_exp.pool_set import PoolSet


class Phase2MonitoredSetAdapter:
    """Wraps a PoolSet for lens consumption. Constructed once at engine
    startup from the on-disk monitored_pools.json (same file the Phase 2
    detector reads)."""

    def __init__(self, pool_set: PoolSet):
        self._pool_set = pool_set

    @classmethod
    def from_path(cls, path: Path) -> "Phase2MonitoredSetAdapter":
        return cls(PoolSet.read_from(path))

    def list_pools(self) -> list[dict]:
        """Return all pools in the monitored set as plain dicts.

        Shape matches the GraphLens protocol: each entry has at least
        address, chain, protocol, token0, token1.
        """
        out: list[dict] = []
        for p in self._pool_set.pools:
            out.append({
                "address": p.address.lower(),
                "chain": p.chain,
                "protocol": p.protocol.value,
                "fee_bps": p.fee_bps,
                "tvl_usd_at_enumeration": p.tvl_usd_at_enumeration,
                "token0": {
                    "address": p.token0.address.lower(),
                    "symbol": p.token0.symbol,
                    "decimals": p.token0.decimals,
                },
                "token1": {
                    "address": p.token1.address.lower(),
                    "symbol": p.token1.symbol,
                    "decimals": p.token1.decimals,
                },
            })
        return out


__all__ = ["Phase2MonitoredSetAdapter"]
