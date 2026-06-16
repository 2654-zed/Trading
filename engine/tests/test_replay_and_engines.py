"""Tests for sub-phase 3.3: ReplayClock, WindowAccumulator, and the
synthesis / regime / conflict engines + orchestrator v2 behaviors.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from engine.core.event_bus import EventBus
from engine.core.replay_clock import ReplayClock, ReplayWindow
from engine.core.signal_schema import Signal, new_signal_id
from engine.orchestrator.windowing import WindowAccumulator, ClosedWindow
from engine.orchestrator.orchestrator import Orchestrator, WindowAggregate
from engine.orchestrator.regime_engine import RegimeEngine, RegimeLabel, REGIMES
from engine.orchestrator.regime_weights import get_regime_weights, REGIME_WEIGHTS
from engine.orchestrator.conflict_engine import (
    ConflictEngine, ConflictSignal, CONFLICT_PAIRS, _semantic_tag,
)
from engine.synthesis.cross_lens_engine import (
    CrossLensEngine, CompositeSignal,
)


def _sig(lens, type_, strength, ts, *, conf=0.9, meta=None) -> Signal:
    return Signal(
        id=new_signal_id(), timestamp=ts, lens=lens, type=type_,
        strength=strength, confidence=conf, time_horizon="short",
        metadata=meta or {},
    )


# ===== ReplayClock =======================================================

def test_replay_clock_yields_covering_windows():
    clock = ReplayClock(0.0, 100.0, slice_seconds=25.0)
    windows = list(clock.windows())
    assert len(windows) == 4
    assert windows[0] == ReplayWindow(0.0, 25.0)
    assert windows[-1] == ReplayWindow(75.0, 100.0)
    # as_of is the window end.
    assert windows[0].as_of == 25.0


def test_replay_clock_clamps_final_window():
    clock = ReplayClock(0.0, 90.0, slice_seconds=40.0)
    windows = list(clock.windows())
    assert windows[-1].end == 90.0  # clamped, not 120
    assert windows[-1].duration == 10.0


def test_replay_clock_rejects_bad_range():
    with pytest.raises(ValueError):
        ReplayClock(100.0, 50.0, slice_seconds=10.0)
    with pytest.raises(ValueError):
        ReplayClock(0.0, 100.0, slice_seconds=0.0)


def test_replay_clock_from_data_range_target_windows():
    clock = ReplayClock.from_data_range(0.0, 1000.0, target_windows=10)
    windows = list(clock.windows())
    assert 9 <= len(windows) <= 11  # ~10


# ===== WindowAccumulator =================================================

def test_window_accumulator_closes_on_watermark():
    acc = WindowAccumulator(window_seconds=10.0, grace_fraction=0.5)
    # Window 0 = [0,10). Add a signal at t=5.
    closed = acc.add(_sig("graph", "x", 0.5, 5.0))
    assert closed == []  # watermark 5, window 0 ends at 10 + 5 grace = 15
    # Advance watermark to 16 → window 0 closes.
    closed = acc.add(_sig("graph", "y", 0.5, 16.0))
    assert len(closed) == 1
    assert closed[0].window_start == 0.0
    assert len(closed[0].signals) == 1


def test_window_accumulator_flush():
    acc = WindowAccumulator(window_seconds=10.0)
    acc.add(_sig("graph", "x", 0.5, 5.0))
    acc.add(_sig("graph", "y", 0.5, 25.0))
    flushed = acc.flush()
    # Two windows opened (0 and 20); both flushed.
    assert len(flushed) >= 1
    assert acc.flush() == []  # empty after


# ===== Conflict semantic tags + engine ===================================

def test_semantic_tags():
    assert _semantic_tag(_sig("graph", "cluster_detected", 0.5, 1.0)) == "coordinated"
    assert _semantic_tag(_sig("graph", "subgraph_anomaly", 0.5, 1.0)) == "adversarial"
    assert _semantic_tag(_sig("information", "entropy_drop", 0.5, 1.0)) == "ordering"
    # diffusion regime-dependent
    md_chaos = {"regime": "super_diffusive"}
    assert _semantic_tag(_sig("stochastic", "diffusion_anomaly", 0.5, 1.0, meta=md_chaos)) == "chaotic"
    md_stable = {"regime": "mean_reverting"}
    assert _semantic_tag(_sig("stochastic", "diffusion_anomaly", 0.5, 1.0, meta=md_stable)) == "stable"


def test_conflict_engine_emits_hidden_coordination():
    """graph coordination + stochastic stable on the same entity = conflict."""
    async def go():
        bus = EventBus()
        eng = ConflictEngine(window_seconds=10.0)
        sub = await bus.subscribe(ConflictEngine.OUTPUT_TOPIC)
        addr = "0xabc"
        # Both in window [0,10), same entity.
        await eng._handle(bus, _sig("graph", "cluster_detected", 0.8, 2.0,
                                    meta={"cluster_addresses": [addr]}))
        await eng._handle(bus, _sig("stochastic", "diffusion_anomaly", 0.6, 3.0,
                                    meta={"address": addr, "regime": "mean_reverting"}))
        # Advance watermark to close the window.
        await eng._handle(bus, _sig("graph", "x", 0.1, 100.0))
        out = []
        while not sub.queue.empty():
            out.append(sub.queue.get_nowait())
        conflicts = [c for c in out if isinstance(c, ConflictSignal)]
        assert any(c.conflict_type == "hidden_coordination" for c in conflicts)
        c = next(c for c in conflicts if c.conflict_type == "hidden_coordination")
        assert set(c.lenses_involved) == {"graph", "stochastic"}
    asyncio.run(go())


def test_conflict_engine_no_conflict_when_single_lens():
    async def go():
        bus = EventBus()
        eng = ConflictEngine(window_seconds=10.0)
        sub = await bus.subscribe(ConflictEngine.OUTPUT_TOPIC)
        # Two opposed tags but BOTH from graph — not a cross-lens conflict.
        await eng._handle(bus, _sig("graph", "cluster_detected", 0.8, 2.0,
                                    meta={"address": "0xabc"}))
        await eng._handle(bus, _sig("graph", "x", 0.1, 100.0))
        out = []
        while not sub.queue.empty():
            out.append(sub.queue.get_nowait())
        assert [c for c in out if isinstance(c, ConflictSignal)] == []
    asyncio.run(go())


# ===== Regime engine =====================================================

def test_regime_engine_classifies_adversarial():
    eng = RegimeEngine(window_seconds=10.0)
    sigs = [_sig("graph", "subgraph_anomaly", 0.9, 1.0, meta={"address": "0xa"})]
    regime, conf, scores, log = eng.classify(sigs)
    assert regime == "adversarial"
    assert conf > 0


def test_regime_engine_classifies_exploit_risk():
    eng = RegimeEngine(window_seconds=10.0)
    sigs = [
        _sig("graph", "cluster_detected", 0.8, 1.0, meta={"address": "0xa"}),
        _sig("graph", "subgraph_anomaly", 0.9, 1.0, meta={"address": "0xa"}),
    ]
    regime, conf, scores, log = eng.classify(sigs)
    # coordination + adversarial → exploit_risk should win or tie-high.
    assert scores["exploit_risk"] > 0
    assert regime in ("exploit_risk", "adversarial")


def test_regime_engine_classifies_chaotic():
    eng = RegimeEngine(window_seconds=10.0)
    sigs = [
        _sig("stochastic", "volatility_regime_shift", 0.9, 1.0),
        _sig("stochastic", "diffusion_anomaly", 0.8, 1.0,
             meta={"regime": "super_diffusive"}),
    ]
    regime, conf, scores, log = eng.classify(sigs)
    assert regime == "chaotic"


def test_regime_engine_low_liquidity_on_sparse_window():
    eng = RegimeEngine(window_seconds=10.0)
    sigs = [_sig("information", "regime_surprise", 0.2, 1.0)]
    regime, conf, scores, log = eng.classify(sigs)
    # Only 1 signal → low_liquidity scores high.
    assert scores["low_liquidity"] > 0


def test_regime_labels_cover_valid_set():
    eng = RegimeEngine(window_seconds=10.0)
    r, _, scores, _ = eng.classify([_sig("stochastic", "drift_change", 0.9, 1.0)])
    assert r in REGIMES
    assert set(scores.keys()) == set(REGIMES)


# ===== Regime weights ====================================================

def test_regime_weights_all_valid_and_distinct():
    # Each regime table sums ~1 and differs from default in emphasis.
    for regime in REGIMES:
        w = get_regime_weights(regime)
        assert abs(sum(w.values()) - 1.0) < 1e-9
    assert get_regime_weights("adversarial")["graph"] > \
        get_regime_weights("chaotic")["graph"]
    assert get_regime_weights("chaotic")["stochastic"] > \
        get_regime_weights("adversarial")["stochastic"]


def test_unknown_regime_falls_back_to_default():
    assert get_regime_weights("nonsense") == REGIME_WEIGHTS["default"]


# ===== Cross-lens synthesis ==============================================

def test_synthesis_emits_composite_on_two_lens_convergence():
    async def go():
        bus = EventBus()
        eng = CrossLensEngine(window_seconds=10.0)
        sub = await bus.subscribe(CrossLensEngine.OUTPUT_TOPIC)
        addr = "0xabc"
        await eng._handle(bus, _sig("graph", "centrality_spike", 0.7, 2.0,
                                    meta={"address": addr}))
        await eng._handle(bus, _sig("stochastic", "volatility_regime_shift", 0.6, 3.0,
                                    meta={"address": addr}))
        await eng._handle(bus, _sig("graph", "x", 0.1, 100.0))  # advance watermark
        out = []
        while not sub.queue.empty():
            out.append(sub.queue.get_nowait())
        comps = [c for c in out if isinstance(c, CompositeSignal)]
        assert len(comps) >= 1
        c = next(c for c in comps if c.shared_entity == addr)
        assert set(c.lenses_involved) == {"graph", "stochastic"}
        assert 0.0 <= c.convergence_score <= 1.0
    asyncio.run(go())


def test_synthesis_no_composite_for_single_lens_window():
    async def go():
        bus = EventBus()
        eng = CrossLensEngine(window_seconds=10.0)
        sub = await bus.subscribe(CrossLensEngine.OUTPUT_TOPIC)
        await eng._handle(bus, _sig("graph", "centrality_spike", 0.7, 2.0,
                                    meta={"address": "0xabc"}))
        await eng._handle(bus, _sig("graph", "x", 0.1, 100.0))
        out = []
        while not sub.queue.empty():
            out.append(sub.queue.get_nowait())
        assert [c for c in out if isinstance(c, CompositeSignal)] == []
    asyncio.run(go())


# ===== Orchestrator v2: regime routing + conflict boost (I-17) ===========

def test_orchestrator_routes_regime_weights():
    async def go():
        bus = EventBus()
        o = Orchestrator(window_seconds=10.0, regime_aware=True)
        ws = 0.0
        # Signal in window 0.
        o._on_signal(_sig("graph", "subgraph_anomaly", 1.0, 2.0,
                          meta={"address": "0xa"}), arrival_ts=2.001)
        # Inject an adversarial regime label for window 0.
        o._route(RegimeLabel(window_start=0.0, window_end=10.0,
                             regime="adversarial", confidence=0.8,
                             scores={}, feature_log={}, signal_count=1),
                 time.time())
        agg_sub = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC)
        await o._emit_window(bus, ws)
        wa = agg_sub.queue.get_nowait()
        assert wa.regime_label == "adversarial"
        # adversarial weights emphasize graph (0.40).
        assert wa.weights_used["graph"] == 0.40
    asyncio.run(go())


def test_orchestrator_conflict_boosts_score_nonlinearly():
    """I-17: a conflict in the window produces a DIFFERENT (higher) score
    than the same signals without conflict."""
    async def go():
        bus = EventBus()
        # Case A: no conflict.
        oa = Orchestrator(window_seconds=10.0, regime_aware=False)
        oa._on_signal(_sig("graph", "cluster_detected", 0.8, 2.0,
                           meta={"address": "0xa"}), arrival_ts=2.001)
        oa._on_signal(_sig("stochastic", "diffusion_anomaly", 0.6, 3.0,
                           meta={"address": "0xa", "regime": "mean_reverting"}),
                      arrival_ts=3.001)
        sub_a = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC, name="a")
        await oa._emit_window(bus, 0.0)
        wa = sub_a.queue.get_nowait()

        # Case B: same signals + a conflict for the window.
        ob = Orchestrator(window_seconds=10.0, regime_aware=False)
        ob._on_signal(_sig("graph", "cluster_detected", 0.8, 2.0,
                           meta={"address": "0xa"}), arrival_ts=2.001)
        ob._on_signal(_sig("stochastic", "diffusion_anomaly", 0.6, 3.0,
                           meta={"address": "0xa", "regime": "mean_reverting"}),
                      arrival_ts=3.001)
        ob._route(ConflictSignal(window_start=0.0, window_end=10.0,
                                 conflict_type="hidden_coordination",
                                 lenses_involved=["graph", "stochastic"],
                                 contradicting_types=["cluster_detected",
                                                      "diffusion_anomaly"],
                                 shared_entity="0xa", strength=0.9,
                                 component_signal_ids=[]), time.time())
        sub_b = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC, name="b")
        await ob._emit_window(bus, 0.0)
        wb = sub_b.queue.get_nowait()

        assert wb.conflict_count == 1
        assert wb.conflict_boost > 0
        # Non-linear adjustment: conflict score strictly greater.
        assert wb.weighted_aggregate > wa.weighted_aggregate
        # And NOT a simple average/weighted-average (base identical, score differs).
        assert abs(wb.base_score - wa.base_score) < 1e-9
        assert wb.weighted_aggregate != wb.base_score
    asyncio.run(go())


def test_orchestrator_aggregate_not_simple_average_i17():
    """Direct I-17 check: equal-strength signals from 2 lenses in conflict
    produce a different score than the same signals in agreement."""
    async def go():
        bus = EventBus()
        # Agreement: two converging signals, no conflict.
        o1 = Orchestrator(window_seconds=10.0, regime_aware=False)
        o1._on_signal(_sig("graph", "cluster_detected", 0.7, 2.0), arrival_ts=2.0)
        o1._on_signal(_sig("stochastic", "drift_change", 0.7, 2.0), arrival_ts=2.0)
        s1 = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC, name="agree")
        await o1._emit_window(bus, 0.0)
        agg1 = s1.queue.get_nowait()

        # Conflict: same strengths but flagged conflicting.
        o2 = Orchestrator(window_seconds=10.0, regime_aware=False)
        o2._on_signal(_sig("graph", "cluster_detected", 0.7, 2.0), arrival_ts=2.0)
        o2._on_signal(_sig("stochastic", "drift_change", 0.7, 2.0), arrival_ts=2.0)
        o2._route(ConflictSignal(0.0, 10.0, "x", ["graph", "stochastic"],
                                 ["a", "b"], None, 1.0, []), time.time())
        s2 = await bus.subscribe(Orchestrator.AGGREGATE_TOPIC, name="conflict")
        await o2._emit_window(bus, 0.0)
        agg2 = s2.queue.get_nowait()

        assert agg1.weighted_aggregate != agg2.weighted_aggregate
    asyncio.run(go())


# ===== Outcome ledger schema (design-only in 3.3) ========================

def test_outcome_ledger_schema_creates_cleanly():
    import sqlite3
    from engine.feedback.outcome_ledger import create_schema, LEDGER_TABLES
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    for t in LEDGER_TABLES:
        assert t in names, f"ledger table {t} missing"
    conn.close()


def test_outcome_ledger_conflict_key_index_exists():
    """I-18: conflicts keyed by (timestamp, lenses, contradicting_types)."""
    import sqlite3
    from engine.feedback.outcome_ledger import create_schema
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_conflicts_key'"
    ).fetchall()
    assert idx, "I-18 conflict key index missing"
    conn.close()
