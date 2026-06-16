"""Information lens — Phase 3 sub-phase 3.2.

Per D-024: re-grounded on L3 interaction-data categorical distributions
because no $0-CU price feed exists. The math (Shannon entropy, KL
divergence) operates on any categorical distribution. Phase 4 may
augment with finer-grained per-block flow data when CU budget returns.

Two data angles per pool:
  (a) `liquidity_events.event_type` distribution → {add_liquidity, remove_liquidity}
  (b) `org_transfer_events.from_role` distribution → {gas_station, laundry, unknown, null}

Three signal types matching the spec's taxonomy:

  1. `entropy_drop` — current window's Shannon entropy of the
     distribution is significantly LOWER than the baseline window's
     entropy. A collapse of diversity (e.g. all transfers suddenly
     from gas_station, none from laundry) suggests a regime change.

  2. `regime_surprise` — the current categorical distribution diverges
     from a rolling baseline distribution per KL divergence above
     threshold. Captures "this pool's activity profile changed."

  3. `divergence_spike` — KL divergence current_window || baseline_window
     spikes (above a higher threshold than regime_surprise). Acute
     regime shift, fires less often than regime_surprise.

Per I-15: this lens reads ONLY from injected adapters.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Optional, Protocol

from .._base import Lens
from ...core.event_bus import EventBus


# ----- Adapter protocols --------------------------------------------------

class MonitoredSetSource(Protocol):
    def list_pools(self) -> list[dict]: ...


class InformationDataSource(Protocol):
    """Read-only categorical-distribution source for the information lens."""

    def prefetch_for_scan(self, addresses: list[str]) -> None: ...

    def get_liquidity_event_distributions(
        self, address: str, *, days_current: int = 7, days_baseline: int = 7,
    ) -> Optional[dict]: ...
    # Returns {
    #   "current": {event_type: count},   # last `days_current` days
    #   "baseline": {event_type: count},  # previous `days_baseline` days before that
    #   "current_total": int,
    #   "baseline_total": int,
    # } or None if no events at all.

    def get_role_distributions(
        self, address: str, *, days_current: int = 7, days_baseline: int = 7,
    ) -> Optional[dict]: ...
    # Same shape, but for org_transfer_events.from_role.


# ----- Information-theoretic helpers --------------------------------------

def shannon_entropy(counts: dict[str, int]) -> float:
    """Shannon entropy in bits of a categorical distribution. Returns 0
    if input is empty / single-category."""
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def kl_divergence(p_counts: dict[str, int], q_counts: dict[str, int],
                  smoothing: float = 1.0) -> float:
    """KL(P || Q) with additive (Laplace) smoothing so zero-prob bins
    don't blow up. Returns 0 when either distribution is empty."""
    p_total = sum(p_counts.values())
    q_total = sum(q_counts.values())
    if p_total <= 0 or q_total <= 0:
        return 0.0
    # Combined support
    keys = set(p_counts.keys()) | set(q_counts.keys())
    if not keys:
        return 0.0
    n_keys = len(keys)
    smooth_p_total = p_total + smoothing * n_keys
    smooth_q_total = q_total + smoothing * n_keys
    div = 0.0
    for k in keys:
        p = (p_counts.get(k, 0) + smoothing) / smooth_p_total
        q = (q_counts.get(k, 0) + smoothing) / smooth_q_total
        div += p * math.log2(p / q)
    return div


# ----- The lens ----------------------------------------------------------

class InformationLens(Lens):
    lens_label = "information"

    # Tunables.
    WINDOW_DAYS_CURRENT = 7
    WINDOW_DAYS_BASELINE = 7

    # Minimum sample counts. With ~60 events/day across our token set,
    # 7 days = ~400 events; per-pool will be much less. Don't fire on
    # tiny windows.
    MIN_TOTAL_CURRENT = 10
    MIN_TOTAL_BASELINE = 10

    # Signal thresholds (all dimensionless, in bits).
    # entropy_drop: how much the entropy dropped (current < baseline).
    ENTROPY_DROP_BITS_THRESHOLD = 0.3
    # regime_surprise: KL divergence threshold.
    KL_REGIME_THRESHOLD = 0.2
    # divergence_spike: higher KL threshold for acute spikes.
    KL_SPIKE_THRESHOLD = 1.0

    SCAN_INTERVAL_SECONDS = 30.0

    def __init__(
        self,
        monitored_set: MonitoredSetSource,
        data_source: InformationDataSource,
        *,
        scan_interval_seconds: Optional[float] = None,
        max_scans: Optional[int] = None,
        replay_clock=None,
    ):
        super().__init__()
        self._monitored_set = monitored_set
        self._data = data_source
        self._scan_interval = (
            float(scan_interval_seconds)
            if scan_interval_seconds is not None
            else self.SCAN_INTERVAL_SECONDS
        )
        self._max_scans = max_scans
        self._scan_count = 0
        self._replay_clock = replay_clock

    @property
    def scan_count(self) -> int:
        return self._scan_count

    async def run(
        self,
        bus: EventBus,
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        if self._replay_clock is not None:
            for window in self._replay_clock.windows():
                if stop_event is not None and stop_event.is_set():
                    return
                self._forced_timestamp = window.as_of
                try:
                    await self._scan_once(bus, as_of_ts=window.as_of)
                finally:
                    self._forced_timestamp = None
                self._scan_count += 1
            return
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            if self._max_scans is not None and self._scan_count >= self._max_scans:
                return
            await self._scan_once(bus)
            self._scan_count += 1
            if stop_event is None:
                await asyncio.sleep(self._scan_interval)
            else:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self._scan_interval,
                    )
                    return
                except asyncio.TimeoutError:
                    pass

    def _candidate_addresses(self, pool: dict) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        pool_addr = (pool.get("address") or "").lower()
        if pool_addr:
            candidates.append((pool_addr, "pool_address"))
        for tkey in ("token0", "token1"):
            tval = pool.get(tkey)
            taddr = ""
            if isinstance(tval, dict):
                taddr = (tval.get("address") or "").lower()
            elif isinstance(tval, str):
                taddr = tval.lower()
            if taddr:
                candidates.append((taddr, tkey))
        return candidates

    async def _scan_once(self, bus: EventBus, *,
                         as_of_ts: Optional[float] = None) -> None:
        pools = self._monitored_set.list_pools()
        if not pools:
            return

        addr_to_pool_refs: dict[str, list[tuple[dict, str]]] = {}
        for pool in pools:
            for addr, kind in self._candidate_addresses(pool):
                addr_to_pool_refs.setdefault(addr, []).append((pool, kind))

        # Perf hook.
        prefetch = getattr(self._data, "prefetch_for_scan", None)
        if callable(prefetch):
            try:
                prefetch(list(addr_to_pool_refs.keys()))
            except Exception:
                pass

        for addr in addr_to_pool_refs.keys():
            chains_meta = self._chains_from_refs(addr_to_pool_refs.get(addr, []))
            # Two parallel analyses — liquidity events + role mix.
            try:
                liq = self._data.get_liquidity_event_distributions(
                    addr,
                    days_current=self.WINDOW_DAYS_CURRENT,
                    days_baseline=self.WINDOW_DAYS_BASELINE,
                    as_of_ts=as_of_ts,
                )
            except Exception:
                liq = None
            if liq:
                await self._emit_signals_from(
                    bus, addr, liq, source="liquidity_events",
                    chains_meta=chains_meta,
                )
            try:
                roles = self._data.get_role_distributions(
                    addr,
                    days_current=self.WINDOW_DAYS_CURRENT,
                    days_baseline=self.WINDOW_DAYS_BASELINE,
                    as_of_ts=as_of_ts,
                )
            except Exception:
                roles = None
            if roles:
                await self._emit_signals_from(
                    bus, addr, roles, source="org_transfer_roles",
                    chains_meta=chains_meta,
                )

    def _chains_from_refs(self, refs: list[tuple[dict, str]]) -> list[str]:
        return sorted({p.get("chain", "") for p, _ in refs if p.get("chain")})

    async def _emit_signals_from(
        self, bus: EventBus, address: str, dists: dict,
        *, source: str, chains_meta: list[str],
    ) -> None:
        cur = dists.get("current") or {}
        base = dists.get("baseline") or {}
        cur_total = int(dists.get("current_total") or sum(cur.values()))
        base_total = int(dists.get("baseline_total") or sum(base.values()))
        if cur_total < self.MIN_TOTAL_CURRENT or base_total < self.MIN_TOTAL_BASELINE:
            return

        H_cur = shannon_entropy(cur)
        H_base = shannon_entropy(base)
        kl = kl_divergence(cur, base)

        common_meta = {
            "address": address,
            "source": source,
            "current_total": cur_total,
            "baseline_total": base_total,
            "current_distribution": dict(cur),
            "baseline_distribution": dict(base),
            "current_entropy_bits": H_cur,
            "baseline_entropy_bits": H_base,
            "kl_divergence_bits": kl,
            "chains": chains_meta,
        }

        # (1) entropy_drop
        entropy_delta = H_base - H_cur
        if entropy_delta >= self.ENTROPY_DROP_BITS_THRESHOLD:
            # Strength: normalize the drop against the baseline entropy.
            if H_base > 0:
                strength = min(entropy_delta / max(H_base, 1e-6), 1.0)
            else:
                strength = min(entropy_delta, 1.0)
            await self.emit(
                bus, "entropy_drop",
                strength=strength,
                confidence=0.85,
                time_horizon="medium",
                metadata={**common_meta, "entropy_delta_bits": entropy_delta},
            )

        # (2) regime_surprise (lower KL bar)
        if kl >= self.KL_REGIME_THRESHOLD and kl < self.KL_SPIKE_THRESHOLD:
            # Linear-scaled within the regime band.
            strength = min(kl / self.KL_SPIKE_THRESHOLD, 1.0)
            await self.emit(
                bus, "regime_surprise",
                strength=strength,
                confidence=0.8,
                time_horizon="medium",
                metadata=common_meta,
            )

        # (3) divergence_spike (higher KL bar)
        if kl >= self.KL_SPIKE_THRESHOLD:
            # Log-scaled past the spike threshold.
            strength = min(math.log2(1 + kl) / math.log2(8), 1.0)
            await self.emit(
                bus, "divergence_spike",
                strength=strength,
                confidence=0.9,
                time_horizon="short",
                metadata=common_meta,
            )


__all__ = [
    "InformationLens", "MonitoredSetSource", "InformationDataSource",
    "shannon_entropy", "kl_divergence",
]
