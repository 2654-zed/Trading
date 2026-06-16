"""Stochastic lens — Phase 3 sub-phase 3.2.

Per D-024: re-grounded on flow data (org_transfer_events) instead of
price data, because Phase 3 ships at $0 Alchemy CU cost. The math
(volatility / drift / diffusion decomposition) is generalizable to any
stochastic time series; Phase 4 will augment-or-replace the grounding
with a real price feed when CU budget returns.

Emits THREE signal types matching the spec's taxonomy:

  1. `volatility_regime_shift` — rolling-window standard deviation of
     bucketed transfer-flow volume deviates >K from baseline std
     (a classical regime-shift detector applied to flow).

  2. `drift_change` — rolling-window mean of bucketed flow deviates
     >K from baseline mean. Captures slow-moving directional changes.

  3. `diffusion_anomaly` — variance ratio between two sub-windows of
     the flow time series. Departures from ~1.0 indicate the time
     series is non-stationary (mean-reverting if < 1, super-diffusive
     if > 1). The standard Lo & MacKinlay variance-ratio diagnostic.

Per I-15: this lens reads ONLY from injected adapters
(MonitoredSetSource + StochasticDataSource). It does not import any
other lens.
"""

from __future__ import annotations

import asyncio
import math
import statistics
import time
from typing import Any, Optional, Protocol

from .._base import Lens
from ...core.event_bus import EventBus


# ----- Adapter protocols --------------------------------------------------

class MonitoredSetSource(Protocol):
    """Same shape as GraphLens — returns list[pool_dict]."""
    def list_pools(self) -> list[dict]: ...


class StochasticDataSource(Protocol):
    """Read-only stochastic time-series source.

    A concrete impl wraps Phase 1+2's L3 SQLite via the
    `Phase2L3CorpusAdapter`. A test impl returns canned data.

    Per UNK-013 perf pattern (D-023): the lens calls `prefetch_for_scan`
    once per scan with the full address set; subsequent per-address
    calls hit a per-scan in-memory cache.
    """
    def prefetch_for_scan(self, addresses: list[str]) -> None: ...

    def get_flow_buckets(
        self, address: str, *, bucket_seconds: int = 60,
    ) -> Optional[list[tuple[float, float, int]]]: ...
    # Returns list of (bucket_start_epoch, total_value_eth, transfer_count)
    # in ascending time order, OR None if no flow data for address.
    # `bucket_seconds` is the size of each bucket (default 60s).


# ----- The lens ----------------------------------------------------------

class StochasticLens(Lens):
    lens_label = "stochastic"

    # Tunables. Tuned empirically against the L3 SQLite distribution.
    # `bucket_seconds`: granularity of the per-bucket flow time series.
    # 60s is the natural granularity given Base block time ~2s and
    # the smoke-test cadence.
    BUCKET_SECONDS = 60

    # Minimum buckets required to compute statistics — too few and
    # the std/mean estimates are noise.
    MIN_BUCKETS = 30

    # Window split for current vs baseline.
    # `current_buckets` = newest N buckets used for the "now" estimate.
    # `baseline_buckets` = next-oldest M buckets used as reference.
    CURRENT_BUCKETS = 30   # latest 30 buckets
    BASELINE_BUCKETS = 90  # buckets [-120 .. -30] before that

    # Signal thresholds (z-score-like ratios).
    VOLATILITY_RATIO_THRESHOLD = 2.0   # current_std / baseline_std > 2 → fire
    DRIFT_RATIO_THRESHOLD = 2.0        # |current_mean - baseline_mean| / baseline_std > 2 → fire
    DIFFUSION_RATIO_LOW = 0.5          # variance ratio below this → mean-reverting
    DIFFUSION_RATIO_HIGH = 2.0         # variance ratio above this → super-diffusive

    SCAN_INTERVAL_SECONDS = 30.0

    def __init__(
        self,
        monitored_set: MonitoredSetSource,
        data_source: StochasticDataSource,
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

        # Perf hook (D-023 pattern).
        prefetch = getattr(self._data, "prefetch_for_scan", None)
        if callable(prefetch):
            try:
                prefetch(list(addr_to_pool_refs.keys()))
            except Exception:
                pass

        for addr in addr_to_pool_refs.keys():
            try:
                buckets = self._data.get_flow_buckets(
                    addr, bucket_seconds=self.BUCKET_SECONDS,
                    as_of_ts=as_of_ts,
                )
            except Exception:
                continue
            if not buckets or len(buckets) < self.MIN_BUCKETS:
                continue
            # Per-address analysis is defensively wrapped: a math edge
            # case on ONE address must not crash the whole lens scan.
            try:
                await self._analyze_flow_series(
                    bus, addr, buckets, addr_to_pool_refs.get(addr, []),
                )
            except Exception:
                self.emit_failures += 1
                continue

    async def _analyze_flow_series(
        self, bus: EventBus, address: str,
        buckets: list[tuple[float, float, int]],
        pool_refs: list[tuple[dict, str]],
    ) -> None:
        """Compute the three statistics + emit signals when thresholds
        are crossed."""
        # Values series (total_value_eth per bucket).
        values = [b[1] for b in buckets]

        # Split: latest CURRENT_BUCKETS for "current", next BASELINE_BUCKETS for "baseline".
        if len(values) < self.CURRENT_BUCKETS + self.BASELINE_BUCKETS:
            # Not enough data for both windows; use what we have, but skip
            # if either window would be < MIN_BUCKETS // 2.
            min_half = self.MIN_BUCKETS // 2
            if len(values) < min_half * 2:
                return
            current = values[-min_half:]
            baseline = values[:-min_half]
        else:
            current = values[-self.CURRENT_BUCKETS:]
            baseline = values[
                -(self.CURRENT_BUCKETS + self.BASELINE_BUCKETS):-self.CURRENT_BUCKETS
            ]

        # Means + stds (population variance for stability).
        cur_mean = statistics.fmean(current)
        base_mean = statistics.fmean(baseline)
        cur_std = statistics.pstdev(current) if len(current) >= 2 else 0.0
        base_std = statistics.pstdev(baseline) if len(baseline) >= 2 else 0.0

        # Metadata shared across signals.
        chains = sorted({p.get("chain", "") for p, _ in pool_refs if p.get("chain")})
        common_meta = {
            "address": address,
            "bucket_seconds": self.BUCKET_SECONDS,
            "current_window_buckets": len(current),
            "baseline_window_buckets": len(baseline),
            "current_mean": cur_mean,
            "current_std": cur_std,
            "baseline_mean": base_mean,
            "baseline_std": base_std,
            "chains": chains,
        }

        # (1) volatility_regime_shift
        if base_std > 0:
            vol_ratio = cur_std / base_std
            if vol_ratio >= self.VOLATILITY_RATIO_THRESHOLD or \
                    vol_ratio <= 1.0 / self.VOLATILITY_RATIO_THRESHOLD:
                # Strength: how far from 1.0 in log space, capped.
                # Guard log(0): if cur_std is 0 (vol_ratio=0), strength
                # saturates at the max (1.0).
                if vol_ratio <= 0.0:
                    strength = 1.0
                else:
                    strength = min(abs(math.log(vol_ratio)) / math.log(10.0), 1.0)
                await self.emit(
                    bus, "volatility_regime_shift",
                    strength=strength,
                    confidence=0.85,
                    time_horizon="medium",
                    metadata={**common_meta, "volatility_ratio": vol_ratio},
                )

        # (2) drift_change
        if base_std > 0:
            drift_z = abs(cur_mean - base_mean) / base_std
            if drift_z >= self.DRIFT_RATIO_THRESHOLD:
                strength = min(drift_z / 5.0, 1.0)
                await self.emit(
                    bus, "drift_change",
                    strength=strength,
                    confidence=0.8,
                    time_horizon="medium",
                    metadata={**common_meta, "drift_z_score": drift_z},
                )

        # (3) diffusion_anomaly — variance ratio test.
        # Split current window into two halves, compare variances.
        if len(current) >= 4:
            half = len(current) // 2
            first_half = current[:half]
            second_half = current[half:]
            var_first = statistics.pvariance(first_half) if len(first_half) >= 2 else 0.0
            var_second = statistics.pvariance(second_half) if len(second_half) >= 2 else 0.0
            if var_first > 0:
                vr = var_second / var_first
                if vr <= self.DIFFUSION_RATIO_LOW or vr >= self.DIFFUSION_RATIO_HIGH:
                    # Guard log(0) when the second-half variance is zero.
                    if vr <= 0.0:
                        strength = 1.0
                    else:
                        strength = min(abs(math.log(vr)) / math.log(10.0), 1.0)
                    regime = "super_diffusive" if vr > 1.0 else "mean_reverting"
                    await self.emit(
                        bus, "diffusion_anomaly",
                        strength=strength,
                        confidence=0.75,
                        time_horizon="short",
                        metadata={
                            **common_meta,
                            "variance_ratio": vr,
                            "regime": regime,
                        },
                    )


__all__ = ["StochasticLens", "MonitoredSetSource", "StochasticDataSource"]
