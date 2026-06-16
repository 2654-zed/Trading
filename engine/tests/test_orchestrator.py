"""Tests for engine.orchestrator — sub-phase 3.2.

Covers: static weight validation, windowing, aggregation math, latency
measurement, and shutdown semantics.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from engine.core.event_bus import EventBus
from engine.core.signal_schema import Signal, new_signal_id
from engine.orchestrator.orchestrator import (
    AggregateLoggerConsumer,
    Orchestrator,
    WindowAggregate,
)
from engine.orchestrator.static_weights import (
    INITIAL_WEIGHTS,
    get_lens_weights,
    validate_weights,
)


# ----- static weights ----------------------------------------------------

def test_initial_weights_sum_to_one():
    assert abs(sum(INITIAL_WEIGHTS.values()) - 1.0) < 1e-9


def test_initial_weights_cover_all_canonical_lenses():
    from engine.core.signal_schema import VALID_LENSES
    assert set(INITIAL_WEIGHTS.keys()) == VALID_LENSES


def test_validate_weights_accepts_initial():
    validate_weights(INITIAL_WEIGHTS)  # no raise


def test_validate_weights_rejects_bad_key():
    bad = {"not_a_lens": 0.5, "graph": 0.5}
    with pytest.raises(ValueError):
        validate_weights(bad)


def test_validate_weights_rejects_out_of_range():
    bad = dict(INITIAL_WEIGHTS); bad["graph"] = 2.0
    with pytest.raises(ValueError):
        validate_weights(bad)


def test_validate_weights_rejects_off_normalized_sum():
    bad = dict(INITIAL_WEIGHTS); bad["graph"] = 0.0
    with pytest.raises(ValueError):
        validate_weights(bad)


def test_get_lens_weights_returns_copy():
    w = get_lens_weights()
    w["graph"] = 999.0
    assert INITIAL_WEIGHTS["graph"] != 999.0


# ----- orchestrator basics ----------------------------------------------

def _signal(lens: str, type_: str, strength: float, *, ts=None) -> Signal:
    return Signal(
        id=new_signal_id(),
        timestamp=ts if ts is not None else time.time(),
        lens=lens, type=type_,
        strength=strength, confidence=0.9,
        time_horizon="short",
    )


def test_window_alignment_to_wall_clock():
    o = Orchestrator(window_seconds=60.0)
    # Window starts should be multiples of 60 (floor-aligned).
    assert o._window_start_for(123.4) == 120.0  # 123.4 // 60 = 2 → 120
    assert o._window_start_for(180.0) == 180.0
    assert o._window_start_for(59.999) == 0.0


def test_per_lens_max_strength_taken_not_summed():
    async def go():
        bus = EventBus()
        o = Orchestrator(window_seconds=60.0)
        # Emit two signals from graph in same window.
        # Max should be the larger.
        ts = 100.0
        o._on_signal(_signal("graph", "x", 0.3, ts=ts), arrival_ts=ts + 0.001)
        o._on_signal(_signal("graph", "y", 0.8, ts=ts + 1), arrival_ts=ts + 1.001)
        # Force emit.
        agg_sub = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC)
        await o._emit_window(bus, 60.0)
        # 100.0 // 60 = 1, so window_start = 60
        # Drain
        out = []
        while not agg_sub.queue.empty():
            out.append(agg_sub.queue.get_nowait())
        assert len(out) == 1
        wa = out[0]
        assert isinstance(wa, WindowAggregate)
        assert wa.per_lens_max_strength["graph"] == 0.8
        assert wa.per_lens_signal_count["graph"] == 2
    asyncio.run(go())


def test_weighted_aggregate_is_bilinear_strength_times_confidence():
    """v2 (I-17): aggregation is bilinear in strength × confidence.
    graph=0.6 + stochastic=0.5, both confidence 0.9, no conflict, default
    weights → Σ w·strength·conf."""
    async def go():
        bus = EventBus()
        o = Orchestrator(window_seconds=60.0, regime_aware=False)
        ts = 100.0
        o._on_signal(_signal("graph", "x", 0.6, ts=ts), arrival_ts=ts + 0.001)
        o._on_signal(_signal("stochastic", "y", 0.5, ts=ts), arrival_ts=ts + 0.001)
        agg_sub = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC)
        await o._emit_window(bus, 60.0)
        wa = agg_sub.queue.get_nowait()
        expected = (0.6 * 0.9 * INITIAL_WEIGHTS["graph"] +
                    0.5 * 0.9 * INITIAL_WEIGHTS["stochastic"])
        assert abs(wa.weighted_aggregate - expected) < 1e-9
        assert abs(wa.base_score - expected) < 1e-9
        assert wa.conflict_count == 0
        for missing in ("topology", "game", "information"):
            assert wa.per_lens_max_strength.get(missing, 0.0) == 0.0
    asyncio.run(go())


def test_latency_tracking_reports_p95():
    async def go():
        o = Orchestrator(window_seconds=60.0)
        now = time.time()
        # Inject signals with varying publish-to-arrival delay.
        for i, delay in enumerate([0.001, 0.005, 0.010, 0.020, 0.050, 0.100]):
            s = _signal("graph", "x", 0.5, ts=now + i)
            o._on_signal(s, arrival_ts=s.timestamp + delay)
        stats = o.latency_stats()
        assert stats["count"] == 6
        # p95 should be high end of the distribution.
        assert stats["p95_ms"] >= 50.0
    asyncio.run(go())


def test_signal_with_orchestrator_p95_under_500ms_target():
    """The spec's latency target: p95 < 500ms publish→consume. Verify
    the orchestrator records consume latency for round-trip via the bus."""
    async def go():
        bus = EventBus()
        o = Orchestrator(window_seconds=60.0)
        stop_event = asyncio.Event()
        task = asyncio.create_task(o.run(bus, stop_event=stop_event))
        # Yield control so the orchestrator can subscribe before we publish.
        await asyncio.sleep(0.05)
        for _ in range(50):
            sig = _signal("graph", "x", 0.5)
            await bus.publish("signal.graph.x", sig)
        await asyncio.sleep(0.6)  # give the consumer time to drain
        stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)
        stats = o.latency_stats()
        assert stats["count"] >= 50
        # Should be MUCH less than 500ms — in-process asyncio queues
        # are microsecond-class.
        assert stats["p95_ms"] is not None and stats["p95_ms"] < 500.0
    asyncio.run(go())


def test_orchestrator_ignores_non_signal_payloads():
    async def go():
        o = Orchestrator(window_seconds=60.0)
        o._on_signal({"not": "a Signal"}, arrival_ts=time.time())
        assert o.total_signals_received == 0
    asyncio.run(go())


def test_aggregate_logger_writes_one_line_per_window(tmp_path):
    async def go():
        bus = EventBus()
        o = Orchestrator(window_seconds=60.0)
        out = tmp_path / "agg.jsonl"
        logger = AggregateLoggerConsumer(out)
        stop_event = asyncio.Event()
        logger_task = asyncio.create_task(logger.run(bus, stop_event=stop_event))

        # Yield so the logger can subscribe before we publish aggregates.
        await asyncio.sleep(0.05)

        # Manually emit two windows.
        o._on_signal(_signal("graph", "x", 0.5, ts=60.0),
                     arrival_ts=60.001)
        o._on_signal(_signal("graph", "y", 0.7, ts=120.0),
                     arrival_ts=120.001)
        await o._emit_window(bus, 60.0)
        await o._emit_window(bus, 120.0)
        await asyncio.sleep(0.2)
        stop_event.set()
        try:
            await asyncio.wait_for(logger_task, timeout=2.0)
        except asyncio.TimeoutError:
            logger_task.cancel()

        with open(out) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert logger.total_received == 2
    asyncio.run(go())


def test_max_pending_windows_caps_growth():
    async def go():
        o = Orchestrator(window_seconds=60.0, max_pending_windows=2)
        # Open 3 distinct windows; the 3rd should be dropped.
        o._on_signal(_signal("graph", "x", 0.5, ts=60.0), arrival_ts=60.001)
        o._on_signal(_signal("graph", "x", 0.5, ts=120.0), arrival_ts=120.001)
        # This signal lands in a brand-new 3rd window; should be dropped.
        o._on_signal(_signal("graph", "x", 0.5, ts=180.0), arrival_ts=180.001)
        assert len(o._windows) == 2
        assert o.signals_dropped_outside_window == 1
    asyncio.run(go())
