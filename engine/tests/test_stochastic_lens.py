"""Tests for engine.lenses.stochastic.lens.StochasticLens — sub-phase 3.2.

Per D-024: the lens is re-grounded on transfer-flow time series.
These tests verify the volatility/drift/diffusion math fires on
synthetic time-series fixtures.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from engine.core.event_bus import EventBus
from engine.core.signal_schema import validate_signal
from engine.lenses.stochastic.lens import StochasticLens


class FakeMonitoredSet:
    def __init__(self, pools: list[dict]):
        self._pools = pools

    def list_pools(self) -> list[dict]:
        return list(self._pools)


class FakeStochasticData:
    """Maps address -> list[(ts, value_eth, count)] buckets."""
    def __init__(self, buckets: Optional[dict] = None,
                 raise_on_fetch: bool = False):
        self._buckets = {
            k.lower(): list(v) for k, v in (buckets or {}).items()
        }
        self._raise = raise_on_fetch

    def prefetch_for_scan(self, addresses: list[str]) -> None:
        return None

    def get_flow_buckets(self, address: str, *, bucket_seconds: int = 60,
                         as_of_ts=None):
        if self._raise:
            raise RuntimeError("simulated data failure")
        return self._buckets.get(address.lower())


def _pool(addr: str, chain: str = "base") -> dict:
    return {
        "address": addr, "chain": chain, "protocol": "uniswap-v3",
        "token0": {"address": "0xaaa", "symbol": "T0"},
        "token1": {"address": "0xbbb", "symbol": "T1"},
    }


async def _drain(bus: EventBus, sub) -> list:
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


async def _scan(lens: StochasticLens, bus: EventBus, sub) -> list:
    await lens._scan_once(bus)
    await asyncio.sleep(0)
    return await _drain(bus, sub)


def _flat_buckets(addr: str, n: int = 150, value: float = 1.0):
    """Constant-value buckets — no volatility, no drift, no diffusion."""
    return [(i * 60.0, value, 1) for i in range(n)]


def _high_vol_buckets(addr: str, n_baseline: int = 90, n_current: int = 30):
    """Baseline = quiet, current = wild. Should fire volatility_regime_shift."""
    import random
    random.seed(42)
    baseline = [(i * 60.0, 1.0 + random.gauss(0, 0.05), 1)
                for i in range(n_baseline)]
    current = [(((n_baseline) + i) * 60.0, 1.0 + random.gauss(0, 2.0), 1)
               for i in range(n_current)]
    return baseline + current


def _drift_buckets(n_baseline: int = 90, n_current: int = 30):
    """Baseline mean=1.0, current mean=5.0 → drift_change."""
    import random
    random.seed(1)
    baseline = [(i * 60.0, 1.0 + random.gauss(0, 0.1), 1)
                for i in range(n_baseline)]
    current = [(((n_baseline) + i) * 60.0, 5.0 + random.gauss(0, 0.1), 1)
               for i in range(n_current)]
    return baseline + current


# ----- construction + label ----------------------------------------------

def test_stochastic_lens_label():
    lens = StochasticLens(FakeMonitoredSet([]), FakeStochasticData())
    assert lens.lens_label == "stochastic"


# ----- volatility regime shift -------------------------------------------

def test_volatility_regime_shift_fires_when_std_jumps():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = StochasticLens(
            FakeMonitoredSet(pools),
            FakeStochasticData(buckets={addr: _high_vol_buckets(addr)}),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.volatility_regime_shift")
        sigs = await _scan(lens, bus, sub)
        assert len(sigs) >= 1
        s = sigs[0]
        assert s.type == "volatility_regime_shift"
        assert "volatility_ratio" in s.metadata
        assert s.metadata["volatility_ratio"] > 1.0
        validate_signal(s)
    asyncio.run(go())


def test_volatility_does_not_fire_on_flat_series():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = StochasticLens(
            FakeMonitoredSet(pools),
            FakeStochasticData(buckets={addr: _flat_buckets(addr)}),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.volatility_regime_shift")
        sigs = await _scan(lens, bus, sub)
        # Constant series has zero std baseline → vol-ratio code is guarded
        # by `if base_std > 0` so no signal fires.
        assert sigs == []
    asyncio.run(go())


# ----- drift change -------------------------------------------------------

def test_drift_change_fires_when_mean_shifts():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = StochasticLens(
            FakeMonitoredSet(pools),
            FakeStochasticData(buckets={addr: _drift_buckets()}),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.drift_change")
        sigs = await _scan(lens, bus, sub)
        assert len(sigs) >= 1
        assert sigs[0].type == "drift_change"
        assert sigs[0].metadata["drift_z_score"] >= lens.DRIFT_RATIO_THRESHOLD
        validate_signal(sigs[0])
    asyncio.run(go())


# ----- diffusion anomaly --------------------------------------------------

def test_diffusion_anomaly_fires_when_variance_ratio_anomalous():
    async def go():
        addr = "0xpool1" + "0" * 34
        # Build a current window with half quiet, half loud → variance
        # ratio between halves > 2.
        import random
        random.seed(7)
        baseline = [(i * 60.0, 1.0 + random.gauss(0, 0.3), 1)
                    for i in range(90)]
        cur_first = [(((90) + i) * 60.0, 1.0 + random.gauss(0, 0.01), 1)
                     for i in range(15)]
        cur_second = [(((105) + i) * 60.0, 1.0 + random.gauss(0, 5.0), 1)
                      for i in range(15)]
        buckets = baseline + cur_first + cur_second
        pools = [_pool(addr)]
        lens = StochasticLens(
            FakeMonitoredSet(pools),
            FakeStochasticData(buckets={addr: buckets}),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.diffusion_anomaly")
        sigs = await _scan(lens, bus, sub)
        assert len(sigs) >= 1
        assert sigs[0].type == "diffusion_anomaly"
        assert "regime" in sigs[0].metadata
        validate_signal(sigs[0])
    asyncio.run(go())


# ----- resilience ---------------------------------------------------------

def test_data_query_failure_does_not_crash_lens():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = StochasticLens(
            FakeMonitoredSet(pools),
            FakeStochasticData(raise_on_fetch=True),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.*")
        sigs = await _scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


def test_too_few_buckets_emits_nothing():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        # Only 10 buckets — below MIN_BUCKETS = 30.
        lens = StochasticLens(
            FakeMonitoredSet(pools),
            FakeStochasticData(buckets={addr: _flat_buckets(addr, n=10)}),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.*")
        sigs = await _scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


def test_empty_monitored_set_emits_nothing():
    async def go():
        lens = StochasticLens(FakeMonitoredSet([]), FakeStochasticData())
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.*")
        sigs = await _scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


# ----- run loop semantics -------------------------------------------------

def test_max_scans_terminates_run():
    async def go():
        pools = [_pool("0xpool1" + "0" * 34)]
        lens = StochasticLens(
            FakeMonitoredSet(pools), FakeStochasticData(),
            scan_interval_seconds=0.01, max_scans=3,
        )
        bus = EventBus()
        await lens.run(bus)
        assert lens.scan_count == 3
    asyncio.run(go())


def test_stop_event_terminates_run():
    async def go():
        pools = [_pool("0xpool1" + "0" * 34)]
        lens = StochasticLens(
            FakeMonitoredSet(pools), FakeStochasticData(),
            scan_interval_seconds=10.0,
        )
        bus = EventBus()
        stop_event = asyncio.Event()
        async def stop_soon():
            await asyncio.sleep(0.05)
            stop_event.set()
        task = asyncio.create_task(lens.run(bus, stop_event=stop_event))
        await asyncio.create_task(stop_soon())
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("lens did not honor stop_event")
    asyncio.run(go())


# ----- I-15 invariant -----------------------------------------------------

def test_lens_does_not_import_other_lenses():
    import engine.lenses.stochastic.lens as mod
    src = open(mod.__file__).read()
    for forbidden in (
        "engine.lenses.graph", "engine.lenses.topology",
        "engine.lenses.game", "engine.lenses.information",
    ):
        assert forbidden not in src
