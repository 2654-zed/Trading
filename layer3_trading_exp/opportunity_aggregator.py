"""Aggregator for opportunity emissions across many blocks.

The detector is stateless: each block, given current state, it emits whatever
arbs satisfy the spec floor. In a long run the same arb will emit again every
block until something changes (a swap moves price). For research purposes,
counting per-block emissions overstates "opportunity count" by 100-1000x.

This module wraps the detector's per-block output to count *unique opportunities*
and *episodes* — a maximal contiguous run of blocks where a particular opportunity
key persisted. Stats reported:

  - total_emissions: raw count of detector outputs (existing metric)
  - unique_keys: distinct (pool_x, pool_y, borrowed_token) triples ever seen
  - total_episodes: each maximal-contiguous-block run is one episode (same
    opportunity coming back after disappearing for >=1 block = new episode)
  - avg_episode_length / max_episode_length: in blocks
  - margin distribution computed over UNIQUE keys (one max-margin sample per key)
    rather than per-emission, removing the persistent-arb skew

Two duplicate-block defenses:
  - If a block number is observed twice (we've seen this with the WS subscription
    occasionally double-firing on Base), the second observation appends to the
    active episode without bumping its last_block forward, and does not end any
    other episodes.
  - An episode only ends when a block strictly greater than ep.last_block is
    observed AND the key did not emit. Gap markers (missed blocks) end episodes
    of keys that were active before the gap.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable

from .opportunity_detector import Opportunity


@dataclass(frozen=True)
class OpportunityKey:
    """Identity of an arbitrage opportunity, stable across blocks.

    Two emissions at different blocks with the same (pool_x, pool_y,
    borrowed_token) are the same opportunity in different states.
    """
    pool_x_addr: str
    pool_y_addr: str
    borrowed_token_addr: str


@dataclass
class Episode:
    """A maximal contiguous run of blocks during which one opportunity key
    persisted in the detector's output."""
    key: OpportunityKey
    first_block: int
    last_block: int
    sample_count: int
    margins: list[float] = field(default_factory=list)
    net_gains_usd: list[float] = field(default_factory=list)

    @property
    def length_blocks(self) -> int:
        return self.last_block - self.first_block + 1

    @property
    def max_margin(self) -> float:
        return max(self.margins) if self.margins else 0.0

    @property
    def median_margin(self) -> float:
        return statistics.median(self.margins) if self.margins else 0.0

    @property
    def max_net_gain_usd(self) -> float:
        return max(self.net_gains_usd) if self.net_gains_usd else 0.0


class OpportunityAggregator:
    """Stateful aggregator that converts per-block emissions into episodes."""

    def __init__(self) -> None:
        self._completed: list[Episode] = []
        self._active: dict[OpportunityKey, Episode] = {}
        self._last_block_seen: int = -1
        self._total_emissions: int = 0

    def record_block(self, block_number: int, opportunities: Iterable[Opportunity]) -> None:
        """Process one block's worth of opportunity emissions."""
        opps_list = list(opportunities)
        self._total_emissions += len(opps_list)

        keys_this_block: set[OpportunityKey] = set()
        for opp in opps_list:
            key = OpportunityKey(
                pool_x_addr=opp.legs[0].pool_address,
                pool_y_addr=opp.legs[1].pool_address,
                borrowed_token_addr=opp.borrowed_token_addr,
            )
            keys_this_block.add(key)
            ep = self._active.get(key)
            if ep is None:
                self._active[key] = Episode(
                    key=key,
                    first_block=block_number,
                    last_block=block_number,
                    sample_count=1,
                    margins=[opp.gross_margin],
                    net_gains_usd=[opp.expected_net_gain_usd],
                )
            else:
                # Continuation. Bump last_block only if strictly greater (handles
                # duplicate block events from WS reordering).
                if block_number > ep.last_block:
                    ep.last_block = block_number
                ep.sample_count += 1
                ep.margins.append(opp.gross_margin)
                ep.net_gains_usd.append(opp.expected_net_gain_usd)

        # End episodes whose key did NOT emit this block, but only if we've
        # advanced past their last_block (otherwise duplicate-block events
        # would spuriously close active episodes).
        if block_number > self._last_block_seen:
            ended_keys: list[OpportunityKey] = []
            for key, ep in self._active.items():
                if key not in keys_this_block and block_number > ep.last_block:
                    ended_keys.append(key)
            for key in ended_keys:
                self._completed.append(self._active.pop(key))

        if block_number > self._last_block_seen:
            self._last_block_seen = block_number

    def finalize(self) -> None:
        """Mark all currently-active episodes as completed. Call once at end of run."""
        for ep in self._active.values():
            self._completed.append(ep)
        self._active.clear()

    @property
    def total_emissions(self) -> int:
        return self._total_emissions

    @property
    def total_episodes(self) -> int:
        return len(self._completed) + len(self._active)

    @property
    def unique_keys(self) -> int:
        seen: set[OpportunityKey] = set()
        for ep in self._completed:
            seen.add(ep.key)
        for key in self._active.keys():
            seen.add(key)
        return len(seen)

    def summary(self) -> dict:
        """Per-spec stop-gate summary, dedup-aware."""
        episodes = list(self._completed) + list(self._active.values())
        if not episodes:
            return {
                "total_emissions": self._total_emissions,
                "unique_keys": 0,
                "total_episodes": 0,
            }

        episode_lengths = [ep.length_blocks for ep in episodes]
        # Margin distribution computed over UNIQUE keys' max margins, not over
        # raw per-emission samples — strips the persistent-arb skew.
        per_key_max: dict[OpportunityKey, float] = {}
        for ep in episodes:
            cur = per_key_max.get(ep.key, 0.0)
            if ep.max_margin > cur:
                per_key_max[ep.key] = ep.max_margin
        unique_max_margins = list(per_key_max.values())

        # Top per-key episode count
        per_key_episode_count: dict[OpportunityKey, int] = {}
        for ep in episodes:
            per_key_episode_count[ep.key] = per_key_episode_count.get(ep.key, 0) + 1

        # Top 5 most-active keys (by total emissions across all their episodes)
        per_key_emissions: dict[OpportunityKey, int] = {}
        for ep in episodes:
            per_key_emissions[ep.key] = per_key_emissions.get(ep.key, 0) + ep.sample_count
        top_keys = sorted(per_key_emissions.items(), key=lambda kv: -kv[1])[:5]

        return {
            "total_emissions": self._total_emissions,
            "unique_keys": self.unique_keys,
            "total_episodes": self.total_episodes,
            "avg_episode_length_blocks": statistics.mean(episode_lengths),
            "median_episode_length_blocks": statistics.median(episode_lengths),
            "max_episode_length_blocks": max(episode_lengths),
            "unique_margin_min_pct": min(unique_max_margins) * 100,
            "unique_margin_p50_pct": statistics.median(unique_max_margins) * 100,
            "unique_margin_p95_pct": _quantile(unique_max_margins, 0.95) * 100,
            "unique_margin_max_pct": max(unique_max_margins) * 100,
            "top_5_most_active_keys": [
                {
                    "pool_x": k.pool_x_addr,
                    "pool_y": k.pool_y_addr,
                    "borrowed": k.borrowed_token_addr,
                    "emissions": n,
                    "episodes": per_key_episode_count[k],
                }
                for k, n in top_keys
            ],
        }


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs_sorted = sorted(xs)
    idx = max(0, min(len(xs_sorted) - 1, int(q * len(xs_sorted))))
    return xs_sorted[idx]


__all__ = ["OpportunityAggregator", "OpportunityKey", "Episode"]
