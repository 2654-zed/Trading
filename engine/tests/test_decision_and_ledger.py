"""Tests for sub-phase 3.4a: decision engine, ledger writer, outcome
attributor (forward-looking $0-CU proxy)."""

from __future__ import annotations

import asyncio
import time

import pytest

from engine.core.event_bus import EventBus
from engine.orchestrator.orchestrator import WindowAggregate
from engine.orchestrator.decision_engine import (
    Decision, DecisionEngine, ACTIONS,
)
from engine.feedback.outcome_ledger import LedgerWriter, LEDGER_TABLES
from engine.feedback.outcome_attributor import OutcomeAttributor


def _agg(regime, score, *, conf=0.8, conflict=0, lenses=None) -> WindowAggregate:
    return WindowAggregate(
        window_start=0.0, window_end=300.0,
        per_lens_max_strength=lenses or {"graph": score},
        per_lens_signal_count={"graph": 1},
        weights_used={"graph": 0.25, "stochastic": 0.2, "information": 0.1,
                      "topology": 0.2, "game": 0.25},
        weighted_aggregate=score,
        contributing_signal_ids=["s1"],
        regime_label=regime, regime_confidence=conf,
        base_score=score, conflict_count=conflict, conflict_boost=0.0,
    )


# ===== Decision engine ===================================================

def test_decision_engine_actions_valid():
    eng = DecisionEngine()
    for regime in ("trend", "adversarial", "exploit_risk", "chaotic", "low_liquidity"):
        d = eng.decide(_agg(regime, 0.6))
        assert d.action in ACTIONS
        assert 0.0 <= d.confidence <= 1.0
        assert 0.0 <= d.risk_score <= 1.0


def test_trend_high_score_enters():
    eng = DecisionEngine()
    d = eng.decide(_agg("trend", 0.7))
    assert d.action == "enter"


def test_trend_low_score_holds():
    eng = DecisionEngine()
    d = eng.decide(_agg("trend", 0.1))
    assert d.action == "hold"


def test_adversarial_high_score_hedges():
    eng = DecisionEngine()
    d = eng.decide(_agg("adversarial", 0.7))
    assert d.action == "hedge"


def test_adversarial_higher_risk_than_trend():
    eng = DecisionEngine()
    d_adv = eng.decide(_agg("adversarial", 0.6))
    d_trend = eng.decide(_agg("trend", 0.6))
    assert d_adv.risk_score > d_trend.risk_score


def test_conflict_raises_risk():
    eng = DecisionEngine()
    d_no = eng.decide(_agg("adversarial", 0.6, conflict=0))
    d_yes = eng.decide(_agg("adversarial", 0.6, conflict=3))
    assert d_yes.risk_score > d_no.risk_score


def test_rationale_captures_top_lenses():
    eng = DecisionEngine()
    d = eng.decide(_agg("trend", 0.7, lenses={"graph": 0.8, "stochastic": 0.4,
                                              "information": 0.1}))
    top = d.rationale["top_lenses"]
    assert top[0]["lens"] == "graph"
    assert "reason" in d.rationale


def test_decision_engine_emits_to_bus():
    async def go():
        bus = EventBus()
        eng = DecisionEngine()
        sub = await bus.subscribe(DecisionEngine.OUTPUT_TOPIC)
        await eng._handle(bus, _agg("trend", 0.7))
        d = sub.queue.get_nowait()
        assert isinstance(d, Decision)
        assert d.action == "enter"
        assert eng.total_decisions == 1
    asyncio.run(go())


# ===== Ledger writer =====================================================

def test_ledger_writer_creates_and_writes(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.db")
    for t in LEDGER_TABLES:
        assert w.count(t) == 0
    eng = DecisionEngine()
    d = eng.decide(_agg("adversarial", 0.6, conflict=2))
    did = w.write_decision(d, created_at=time.time())
    assert did > 0
    assert w.count("decisions") == 1
    rows = w.all_decisions()
    assert rows[0]["regime_label"] == "adversarial"
    w.close()


def test_ledger_writer_outcome_join(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.db")
    eng = DecisionEngine()
    d = eng.decide(_agg("trend", 0.7))
    did = w.write_decision(d, created_at=time.time())
    w.write_outcome(did, "forward_activity_proxy", 0.42, "escalated",
                    realized_at=time.time(), lag_seconds=600.0)
    joined = w.outcomes_joined()
    assert len(joined) == 1
    assert joined[0]["outcome_value"] == 0.42
    assert joined[0]["ground_truth"] == "escalated"
    assert joined[0]["regime_label"] == "trend"
    w.close()


# ===== Outcome attributor ================================================

class _FakeData:
    """flow keyed by (start,end) won't be exact; instead implement by a
    schedule: returns flow for any [s,e) by summing events in a list."""
    def __init__(self, transfers, poisons):
        # transfers: list[(ts, value)], poisons: list[ts]
        self._transfers = transfers
        self._poisons = poisons

    def get_total_flow_in_window(self, start, end):
        return sum(v for ts, v in self._transfers if start <= ts < end)

    def get_poisoning_count_in_window(self, start, end):
        return sum(1 for ts in self._poisons if start <= ts < end)


def test_attributor_escalation_when_forward_flow_spikes():
    # Baseline window [−h,0): low flow; forward [0,h): high flow.
    h = 100.0
    transfers = [(-50.0, 1.0), (50.0, 100.0)]  # baseline 1, forward 100
    data = _FakeData(transfers, poisons=[])
    attr = OutcomeAttributor(data, horizon_seconds=h)
    value, gt, detail = attr.compute_outcome(window_end=0.0)
    assert detail["forward_flow"] == 100.0
    assert detail["baseline_flow"] == 1.0
    assert gt == "escalated"
    assert value > 0.25


def test_attributor_quiet_when_no_forward_activity():
    h = 100.0
    transfers = [(-50.0, 100.0)]  # baseline only; forward empty
    data = _FakeData(transfers, poisons=[])
    attr = OutcomeAttributor(data, horizon_seconds=h)
    value, gt, detail = attr.compute_outcome(window_end=0.0)
    assert detail["forward_flow"] == 0.0
    assert gt == "quiet"


def test_attributor_poisoning_contributes():
    h = 100.0
    data = _FakeData(transfers=[], poisons=[10.0, 20.0, 30.0, 40.0, 50.0])
    attr = OutcomeAttributor(data, horizon_seconds=h)
    value, gt, detail = attr.compute_outcome(window_end=0.0)
    # 5 forward poison events saturate advr_term to 0.5.
    assert detail["forward_poison"] == 5
    assert detail["advr_term"] == 0.5
    assert gt == "escalated"


def test_attributor_outcome_value_bounded():
    h = 100.0
    data = _FakeData(transfers=[(50.0, 1e9)], poisons=[10.0]*100)
    attr = OutcomeAttributor(data, horizon_seconds=h)
    value, gt, _ = attr.compute_outcome(window_end=0.0)
    assert 0.0 <= value <= 1.0


def test_attributor_attribute_all_writes_outcomes(tmp_path):
    w = LedgerWriter(tmp_path / "ledger.db")
    eng = DecisionEngine()
    # Two decisions at different window ends.
    d1 = eng.decide(_agg("trend", 0.7))
    object.__setattr__(d1, "window_end", 100.0)
    w.write_decision(d1, created_at=time.time())
    data = _FakeData(transfers=[(150.0, 50.0), (50.0, 1.0)], poisons=[])
    attr = OutcomeAttributor(data, horizon_seconds=100.0)
    n = attr.attribute_all(w)
    assert n == 1
    assert w.count("outcomes") == 1
    w.close()
