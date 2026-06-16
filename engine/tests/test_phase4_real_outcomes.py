"""Tests for Phase 4 real outcomes: price-history interpolation +
RealizedOutcomeAttributor (mock price source, no network)."""

from __future__ import annotations

import time

import pytest

from engine.adapters.price_history import DefiLlamaPriceHistory
from engine.feedback.realized_outcome import RealizedOutcomeAttributor
from engine.feedback.outcome_ledger import LedgerWriter
from engine.orchestrator.decision_engine import DecisionEngine
from engine.orchestrator.orchestrator import WindowAggregate


# ===== price_at interpolation (inject series directly, no network) =======

def _px_with_series(series_by_token):
    px = DefiLlamaPriceHistory()
    for tok, pts in series_by_token.items():
        px._series[tok.lower()] = sorted(pts)
        px._chain[tok.lower()] = "base"
    px.resolved_count = len(px._series)
    return px


def test_price_at_nearest_point():
    px = _px_with_series({"0xweth": [(100.0, 10.0), (200.0, 20.0), (300.0, 30.0)]})
    assert px.price_at("0xweth", 100.0) == 10.0
    assert px.price_at("0xweth", 305.0) == 30.0   # clamps to last
    assert px.price_at("0xweth", 50.0) == 10.0    # clamps to first
    # 140 closer to 100 than 200
    assert px.price_at("0xweth", 140.0) == 10.0
    # 160 closer to 200
    assert px.price_at("0xweth", 160.0) == 20.0


def test_price_at_missing_token():
    px = _px_with_series({"0xweth": [(100.0, 10.0)]})
    assert px.price_at("0xunknown", 100.0) is None


def test_coverage():
    px = _px_with_series({"0xa": [(1.0, 1.0)], "0xb": [(1.0, 2.0)]})
    assert px.coverage(["0xa", "0xb", "0xc"]) == pytest.approx(2/3)


# ===== RealizedOutcomeAttributor =========================================

class _MockPx:
    def __init__(self, prices):  # prices: {token: {ts: price}}
        self._p = prices
    def price_at(self, token, ts):
        d = self._p.get(token.lower())
        if not d:
            return None
        # nearest key
        return d.get(ts)


def test_realized_outcome_escalates_on_big_forward_move():
    # token doubles over horizon → |return| = 1.0 → outcome saturates.
    px = _MockPx({"0xtok": {0.0: 10.0, 100.0: 20.0}})
    attr = RealizedOutcomeAttributor(px, ["0xtok"], horizon_seconds=100.0)
    value, gt, detail = attr.compute_outcome(0.0)
    assert detail["n_priced"] == 1
    assert value == 1.0          # |1.0|/0.20 capped at 1.0
    assert gt == "escalated"


def test_realized_outcome_quiet_on_small_move():
    # 1% move → turbulence 0.01 → outcome 0.05 < 0.25 threshold.
    px = _MockPx({"0xtok": {0.0: 100.0, 100.0: 101.0}})
    attr = RealizedOutcomeAttributor(px, ["0xtok"], horizon_seconds=100.0)
    value, gt, _ = attr.compute_outcome(0.0)
    assert value == pytest.approx(0.05, abs=1e-6)
    assert gt == "quiet"


def test_realized_outcome_direction_agnostic():
    up = _MockPx({"0xtok": {0.0: 100.0, 100.0: 110.0}})
    down = _MockPx({"0xtok": {0.0: 100.0, 100.0: 90.0}})
    a_up = RealizedOutcomeAttributor(up, ["0xtok"], horizon_seconds=100.0)
    a_dn = RealizedOutcomeAttributor(down, ["0xtok"], horizon_seconds=100.0)
    # both 10% magnitude → same outcome
    assert a_up.compute_outcome(0.0)[0] == a_dn.compute_outcome(0.0)[0]


def test_realized_outcome_averages_across_tokens():
    px = _MockPx({
        "0xa": {0.0: 100.0, 100.0: 140.0},   # +40%
        "0xb": {0.0: 100.0, 100.0: 100.0},   # 0%
    })
    attr = RealizedOutcomeAttributor(px, ["0xa", "0xb"], horizon_seconds=100.0)
    value, gt, detail = attr.compute_outcome(0.0)
    # mean(|0.4|, |0|) = 0.20 → outcome 1.0
    assert detail["turbulence"] == pytest.approx(0.20)
    assert value == pytest.approx(1.0)


def test_realized_outcome_no_prices_is_quiet():
    px = _MockPx({})
    attr = RealizedOutcomeAttributor(px, ["0xtok"], horizon_seconds=100.0)
    value, gt, detail = attr.compute_outcome(0.0)
    assert value == 0.0
    assert gt == "quiet"
    assert detail["n_priced"] == 0


def test_realized_attribute_all_writes_outcomes(tmp_path):
    w = LedgerWriter(tmp_path / "l.db")
    eng = DecisionEngine()
    agg = WindowAggregate(
        window_start=0.0, window_end=100.0,
        per_lens_max_strength={"graph": 0.6},
        per_lens_signal_count={"graph": 1},
        weights_used={"graph": 0.25, "stochastic": 0.2, "information": 0.1,
                      "topology": 0.2, "game": 0.25},
        weighted_aggregate=0.6, contributing_signal_ids=["s"],
        regime_label="trend", regime_confidence=0.8, base_score=0.6,
        conflict_count=0, conflict_boost=0.0)
    d = eng.decide(agg)
    object.__setattr__(d, "window_end", 100.0)
    w.write_decision(d, created_at=time.time())
    px = _MockPx({"0xtok": {100.0: 100.0, 300.0: 130.0}})
    attr = RealizedOutcomeAttributor(px, ["0xtok"], horizon_seconds=200.0)
    n = attr.attribute_all(w)
    assert n == 1
    assert w.count("outcomes") == 1
    joined = w.outcomes_joined()
    assert joined[0]["ground_truth"] in ("escalated", "quiet")
    w.close()
