"""Tests for sub-phase 3.5: ledger admin, observability, replay analytics."""

from __future__ import annotations

import time

import pytest

from engine.feedback.outcome_ledger import LedgerWriter
from engine.feedback.ledger_admin import LedgerAdmin
from engine.feedback.observability import (
    compute_performance, render_markdown, FIRE_THRESHOLD,
)
from engine.feedback.replay import pearson, score_outcome_correlation
from engine.feedback.outcome_attributor import OutcomeAttributor
from engine.orchestrator.decision_engine import DecisionEngine
from engine.orchestrator.orchestrator import WindowAggregate


def _agg(regime, score, lenses):
    return WindowAggregate(
        window_start=0.0, window_end=300.0, per_lens_max_strength=lenses,
        per_lens_signal_count={l: 1 for l in lenses},
        weights_used={"graph": 0.25, "stochastic": 0.2, "information": 0.1,
                      "topology": 0.2, "game": 0.25},
        weighted_aggregate=score, contributing_signal_ids=["s"],
        regime_label=regime, regime_confidence=0.8, base_score=score,
        conflict_count=0, conflict_boost=0.0)


def _seed(w, n, regime, lens, strength, outcome):
    eng = DecisionEngine()
    for i in range(n):
        d = eng.decide(_agg(regime, 0.6, {lens: strength, "stochastic": 0.1}))
        object.__setattr__(d, "window_start", float(i))
        object.__setattr__(d, "window_end", float(i) + 1)
        did = w.write_decision(d, created_at=time.time())
        w.write_outcome(did, "forward_activity_proxy", outcome,
                        "escalated" if outcome > 0.25 else "quiet",
                        realized_at=time.time(), lag_seconds=1.0)


# ===== pearson / correlation =============================================

def test_pearson_perfect_positive():
    assert abs(pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    assert abs(pearson([1, 2, 3, 4], [8, 6, 4, 2]) + 1.0) < 1e-9


def test_pearson_too_few_points():
    assert pearson([1, 2], [1, 2]) is None


def test_pearson_zero_variance():
    assert pearson([1, 1, 1], [1, 2, 3]) is None


def test_score_outcome_correlation():
    rows = [{"weighted_aggregate": i * 0.1, "outcome_value": i * 0.1}
            for i in range(1, 6)]
    assert abs(score_outcome_correlation(rows) - 1.0) < 1e-9


# ===== ledger admin ======================================================

def test_ledger_admin_counts_and_regime(tmp_path):
    p = tmp_path / "l.db"
    w = LedgerWriter(p)
    _seed(w, 10, "trend", "graph", 0.9, 0.5)
    _seed(w, 5, "adversarial", "graph", 0.9, 0.1)
    w.close()
    admin = LedgerAdmin(p)
    assert admin.counts()["decisions"] == 15
    assert admin.counts()["outcomes"] == 15
    by_regime = admin.decisions_by_regime()
    assert by_regime["trend"] == 10
    assert by_regime["adversarial"] == 5
    assert admin.find_missing_outcomes() == []
    admin.close()


def test_ledger_admin_repair_missing(tmp_path):
    p = tmp_path / "l.db"
    w = LedgerWriter(p)
    # Write decisions WITHOUT outcomes.
    eng = DecisionEngine()
    for i in range(3):
        d = eng.decide(_agg("trend", 0.6, {"graph": 0.9}))
        object.__setattr__(d, "window_end", float(i) + 1)
        w.write_decision(d, created_at=time.time())
    admin = LedgerAdmin(p)
    assert len(admin.find_missing_outcomes()) == 3

    class _FakeData:
        def get_total_flow_in_window(self, s, e): return 10.0
        def get_poisoning_count_in_window(self, s, e): return 0
    attr = OutcomeAttributor(_FakeData(), horizon_seconds=1.0)
    repaired = admin.repair_missing_outcomes(attr, writer=w)
    assert repaired == 3
    assert admin.find_missing_outcomes() == []
    admin.close()
    w.close()


def test_ledger_admin_dump(tmp_path):
    p = tmp_path / "l.db"
    w = LedgerWriter(p)
    _seed(w, 4, "trend", "graph", 0.9, 0.5)
    w.close()
    admin = LedgerAdmin(p)
    d = admin.dump(recent=2)
    assert d["counts"]["decisions"] == 4
    assert len(d["recent_decisions"]) == 2
    assert d["missing_outcomes"] == 0
    admin.close()


# ===== observability =====================================================

def test_observability_precision_recall(tmp_path):
    p = tmp_path / "l.db"
    w = LedgerWriter(p)
    # In 'trend': graph fires strong (0.9) and outcome escalates → high precision.
    _seed(w, 10, "trend", "graph", 0.9, 0.8)   # graph fired, escalated
    joined = w.outcomes_joined()
    perf = compute_performance(joined)
    assert "trend" in perf
    g = perf["trend"]["lenses"]["graph"]
    assert g["fired"] == 10
    assert g["precision"] == 1.0   # every fired window escalated
    assert g["recall"] == 1.0
    w.close()


def test_observability_lens_below_threshold_not_fired(tmp_path):
    p = tmp_path / "l.db"
    w = LedgerWriter(p)
    _seed(w, 5, "trend", "graph", 0.2, 0.8)  # graph strength below FIRE_THRESHOLD
    joined = w.outcomes_joined()
    perf = compute_performance(joined)
    g = perf["trend"]["lenses"].get("graph")
    # graph never "fired" (0.2 < 0.5).
    assert g is None or g["fired"] == 0
    w.close()


def test_observability_markdown_renders(tmp_path):
    p = tmp_path / "l.db"
    w = LedgerWriter(p)
    _seed(w, 6, "adversarial", "graph", 0.9, 0.6)
    md = render_markdown(compute_performance(w.outcomes_joined()))
    assert "adversarial" in md
    assert "precision" in md
    assert md.startswith("# Per-lens")
    w.close()
