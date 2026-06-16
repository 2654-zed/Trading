"""Tests for engine.lenses.information.lens.InformationLens — sub-phase 3.2.

Per D-024: entropy + KL divergence over L3 categorical distributions
(liquidity_events.event_type + org_transfer_events.from_role).
"""

from __future__ import annotations

import asyncio
import math
from typing import Optional

import pytest

from engine.core.event_bus import EventBus
from engine.core.signal_schema import validate_signal
from engine.lenses.information.lens import (
    InformationLens, shannon_entropy, kl_divergence,
)


class FakeMonitoredSet:
    def __init__(self, pools: list[dict]):
        self._pools = pools

    def list_pools(self) -> list[dict]:
        return list(self._pools)


class FakeInformationData:
    def __init__(
        self,
        liq: Optional[dict] = None,
        roles: Optional[dict] = None,
        raise_on_liq: bool = False,
        raise_on_roles: bool = False,
    ):
        self._liq = {k.lower(): v for k, v in (liq or {}).items()}
        self._roles = {k.lower(): v for k, v in (roles or {}).items()}
        self._raise_on_liq = raise_on_liq
        self._raise_on_roles = raise_on_roles

    def prefetch_for_scan(self, addresses): return None

    def get_liquidity_event_distributions(self, address, *, days_current=7,
                                          days_baseline=7, as_of_ts=None):
        if self._raise_on_liq:
            raise RuntimeError("simulated liq failure")
        return self._liq.get(address.lower())

    def get_role_distributions(self, address, *, days_current=7,
                              days_baseline=7, as_of_ts=None):
        if self._raise_on_roles:
            raise RuntimeError("simulated role failure")
        return self._roles.get(address.lower())


def _pool(addr: str, chain: str = "base", t0: str = "0xaaa", t1: str = "0xbbb") -> dict:
    return {
        "address": addr, "chain": chain, "protocol": "uniswap-v3",
        "token0": {"address": t0, "symbol": "T0"},
        "token1": {"address": t1, "symbol": "T1"},
    }


async def _drain(bus, sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


async def _scan(lens, bus, sub):
    await lens._scan_once(bus)
    await asyncio.sleep(0)
    return await _drain(bus, sub)


# ----- information-theoretic helpers -------------------------------------

def test_shannon_entropy_uniform_two_categories_is_1_bit():
    h = shannon_entropy({"a": 50, "b": 50})
    assert abs(h - 1.0) < 1e-9


def test_shannon_entropy_single_category_is_zero():
    h = shannon_entropy({"a": 100})
    assert h == 0.0


def test_shannon_entropy_empty_is_zero():
    assert shannon_entropy({}) == 0.0


def test_kl_divergence_identical_distributions_is_zero():
    p = {"a": 100, "b": 100}
    assert kl_divergence(p, p) == pytest.approx(0.0, abs=1e-9)


def test_kl_divergence_disjoint_distributions_is_large():
    p = {"a": 100}
    q = {"b": 100}
    div = kl_divergence(p, q)
    assert div > 1.0  # log2 of high ratio after smoothing


def test_kl_divergence_empty_input_is_zero():
    assert kl_divergence({}, {"a": 1}) == 0.0
    assert kl_divergence({"a": 1}, {}) == 0.0


# ----- construction ------------------------------------------------------

def test_information_lens_label():
    lens = InformationLens(FakeMonitoredSet([]), FakeInformationData())
    assert lens.lens_label == "information"


# ----- entropy_drop ------------------------------------------------------

def test_entropy_drop_fires_when_current_diversity_collapses():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        # Baseline: uniform 50/50 distribution → H = 1.0 bits
        # Current: all-one-category → H = 0
        liq = {
            addr: {
                "current": {"add_liquidity": 30, "remove_liquidity": 0},
                "baseline": {"add_liquidity": 50, "remove_liquidity": 50},
                "current_total": 30, "baseline_total": 100,
            }
        }
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(liq=liq))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.entropy_drop")
        sigs = await _scan(lens, bus, sub)
        assert len(sigs) >= 1
        s = sigs[0]
        assert s.type == "entropy_drop"
        assert s.metadata["current_entropy_bits"] < s.metadata["baseline_entropy_bits"]
        validate_signal(s)
    asyncio.run(go())


def test_entropy_drop_does_not_fire_on_similar_distributions():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        liq = {
            addr: {
                "current": {"add_liquidity": 51, "remove_liquidity": 49},
                "baseline": {"add_liquidity": 50, "remove_liquidity": 50},
                "current_total": 100, "baseline_total": 100,
            }
        }
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(liq=liq))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.entropy_drop")
        sigs = await _scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


# ----- regime_surprise + divergence_spike --------------------------------

def test_divergence_spike_fires_on_high_kl():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        # Big distribution shift → high KL
        liq = {
            addr: {
                "current": {"add_liquidity": 100, "remove_liquidity": 0},
                "baseline": {"add_liquidity": 0, "remove_liquidity": 100},
                "current_total": 100, "baseline_total": 100,
            }
        }
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(liq=liq))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.divergence_spike")
        sigs = await _scan(lens, bus, sub)
        assert len(sigs) >= 1
        assert sigs[0].type == "divergence_spike"
        validate_signal(sigs[0])
    asyncio.run(go())


def test_regime_surprise_fires_on_moderate_kl():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        liq = {
            addr: {
                "current": {"add_liquidity": 30, "remove_liquidity": 70},
                "baseline": {"add_liquidity": 60, "remove_liquidity": 40},
                "current_total": 100, "baseline_total": 100,
            }
        }
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(liq=liq))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.regime_surprise")
        sigs = await _scan(lens, bus, sub)
        assert len(sigs) >= 1
        assert sigs[0].type == "regime_surprise"
        validate_signal(sigs[0])
    asyncio.run(go())


# ----- role-distribution source path -------------------------------------

def test_role_distributions_source_fires():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        roles = {
            addr: {
                "current": {"laundry": 50, "gas_station": 0},
                "baseline": {"gas_station": 100},
                "current_total": 50, "baseline_total": 100,
            }
        }
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(roles=roles))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.*")
        sigs = await _scan(lens, bus, sub)
        # Should fire at least divergence_spike (totally different roles).
        assert any(s.metadata.get("source") == "org_transfer_roles" for s in sigs)
        for s in sigs:
            validate_signal(s)
    asyncio.run(go())


# ----- below-minimum samples ---------------------------------------------

def test_too_few_samples_emits_nothing():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        liq = {
            addr: {
                "current": {"add_liquidity": 3},
                "baseline": {"add_liquidity": 100},
                "current_total": 3, "baseline_total": 100,
            }
        }
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(liq=liq))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.*")
        sigs = await _scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


# ----- resilience --------------------------------------------------------

def test_liq_query_failure_does_not_crash_lens():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(raise_on_liq=True))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.*")
        sigs = await _scan(lens, bus, sub)
        # No signals from liq path; scan completed cleanly.
        assert all(s.metadata.get("source") != "liquidity_events" for s in sigs)
    asyncio.run(go())


def test_role_query_failure_does_not_crash_lens():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = InformationLens(FakeMonitoredSet(pools),
                               FakeInformationData(raise_on_roles=True))
        bus = EventBus()
        sub = await bus.subscribe("signal.information.*")
        sigs = await _scan(lens, bus, sub)
        assert all(s.metadata.get("source") != "org_transfer_roles" for s in sigs)
    asyncio.run(go())


# ----- I-15 --------------------------------------------------------------

def test_lens_does_not_import_other_lenses():
    import engine.lenses.information.lens as mod
    src = open(mod.__file__).read()
    for forbidden in (
        "engine.lenses.graph", "engine.lenses.topology",
        "engine.lenses.game", "engine.lenses.stochastic",
    ):
        assert forbidden not in src
