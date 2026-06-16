"""Tests for sub-phase 3.4b: learner (I-19), weighting engine, dry-run
strategy router (execution gated off), and the closed-loop integration
test (signal → decision → outcome → weight update)."""

from __future__ import annotations

import time

import pytest

from engine.feedback.outcome_ledger import LedgerWriter
from engine.feedback.learner import Learner, STALE_AFTER_SECONDS
from engine.orchestrator.weighting_engine import WeightingEngine
from engine.orchestrator.decision_engine import DecisionEngine
from engine.orchestrator.orchestrator import WindowAggregate
from engine.orchestrator.static_weights import validate_weights
from engine.execution.strategy_router import (
    StrategyRouter, IntendedOrder, ExecutionNotAuthorizedError,
)


def _agg(regime, score, lenses, *, conf=0.8) -> WindowAggregate:
    return WindowAggregate(
        window_start=0.0, window_end=300.0,
        per_lens_max_strength=lenses,
        per_lens_signal_count={l: 1 for l in lenses},
        weights_used={"graph": 0.25, "stochastic": 0.2, "information": 0.1,
                      "topology": 0.2, "game": 0.25},
        weighted_aggregate=score, contributing_signal_ids=["s"],
        regime_label=regime, regime_confidence=conf,
        base_score=score, conflict_count=0, conflict_boost=0.0,
    )


def _seed_ledger(w, regime, n, *, strong_lens, outcome_for_strong, other_outcome):
    """Write n decisions in `regime` where `strong_lens` fires strongly;
    outcome is high when strong_lens fires."""
    eng = DecisionEngine()
    for i in range(n):
        lenses = {strong_lens: 0.9, "stochastic": 0.2}
        agg = _agg(regime, 0.6, lenses)
        d = eng.decide(agg)
        object.__setattr__(d, "window_start", float(i))
        object.__setattr__(d, "window_end", float(i) + 1)
        did = w.write_decision(d, created_at=time.time())
        w.write_outcome(did, "forward_activity_proxy", outcome_for_strong,
                        "escalated" if outcome_for_strong > 0.25 else "quiet",
                        realized_at=time.time(), lag_seconds=1.0)


# ===== Learner ===========================================================

def test_learner_produces_valid_weight_tables(tmp_path):
    w = LedgerWriter(tmp_path / "l.db")
    _seed_ledger(w, "trend", 30, strong_lens="graph",
                 outcome_for_strong=0.8, other_outcome=0.1)
    learner = Learner()
    learned = learner.learn(w)
    # Every regime table is valid + normalized.
    for regime, tbl in learned.items():
        validate_weights(tbl)
    w.close()


def test_learner_shifts_weight_toward_predictive_lens(tmp_path):
    w = LedgerWriter(tmp_path / "l.db")
    # graph fires strongly AND outcomes escalate → graph should gain weight
    # in 'trend' vs its static prior (0.20).
    _seed_ledger(w, "trend", 40, strong_lens="graph",
                 outcome_for_strong=0.9, other_outcome=0.0)
    learner = Learner(blend_alpha=0.7)
    learned = learner.learn(w)
    from engine.orchestrator.regime_weights import REGIME_WEIGHTS
    prior_graph = REGIME_WEIGHTS["trend"]["graph"]
    assert learned["trend"]["graph"] > prior_graph, (
        f"graph weight {learned['trend']['graph']} should exceed prior "
        f"{prior_graph}"
    )
    w.close()


def test_learner_persists_and_loads(tmp_path):
    w = LedgerWriter(tmp_path / "l.db")
    _seed_ledger(w, "trend", 20, strong_lens="graph",
                 outcome_for_strong=0.7, other_outcome=0.1)
    learner = Learner()
    learned = learner.learn(w)
    path = tmp_path / "weights.json"
    learner.persist(learned, path)
    loaded = Learner.load(path)
    assert loaded is not None
    assert "weights" in loaded
    assert "last_run_ts" in loaded
    assert set(loaded["weights"].keys()) >= {"trend", "adversarial"}
    w.close()


def test_learner_no_data_keeps_priors(tmp_path):
    w = LedgerWriter(tmp_path / "l.db")
    learner = Learner()
    learned = learner.learn(w)  # empty ledger
    from engine.orchestrator.regime_weights import REGIME_WEIGHTS
    assert learned["trend"] == REGIME_WEIGHTS["trend"]
    w.close()


def test_learner_staleness(tmp_path):
    learner = Learner()
    assert learner.is_stale()  # never run
    w = LedgerWriter(tmp_path / "l.db")
    learner.learn(w)
    assert not learner.is_stale()
    assert learner.staleness_seconds() < 5
    w.close()


# ===== Weighting engine ==================================================

def test_weighting_engine_falls_back_to_static_when_no_file(tmp_path):
    we = WeightingEngine(tmp_path / "missing.json")
    from engine.orchestrator.regime_weights import get_regime_weights
    assert we.get_weights("adversarial") == get_regime_weights("adversarial")
    assert not we.using_learned


def test_weighting_engine_uses_learned_when_fresh(tmp_path):
    w = LedgerWriter(tmp_path / "l.db")
    _seed_ledger(w, "trend", 40, strong_lens="graph",
                 outcome_for_strong=0.9, other_outcome=0.0)
    learner = Learner(blend_alpha=0.7)
    learned = learner.learn(w)
    path = tmp_path / "weights.json"
    learner.persist(learned, path)
    we = WeightingEngine(path)
    assert we.using_learned
    assert we.get_weights("trend") == learned["trend"]
    w.close()


def test_weighting_engine_stale_falls_back(tmp_path):
    import json
    path = tmp_path / "weights.json"
    old = time.time() - (STALE_AFTER_SECONDS + 100)
    from engine.orchestrator.regime_weights import REGIME_WEIGHTS
    path.write_text(json.dumps({
        "last_run_ts": old, "weights": dict(REGIME_WEIGHTS), "summary": {},
    }))
    we = WeightingEngine(path)
    assert we.is_stale()
    assert not we.using_learned


# ===== Strategy router (execution GATED OFF) =============================

def _decision(action, *, conf=0.8, risk=0.5):
    from engine.orchestrator.decision_engine import Decision
    return Decision(window_start=0.0, window_end=300.0, action=action,
                    confidence=conf, risk_score=risk, regime_label="trend",
                    aggregate_score=0.6, rationale={}, decided_at=time.time())


def test_router_dry_run_by_default():
    r = StrategyRouter()
    assert r.live_enabled is False


def test_router_live_enable_blocked_without_authorization():
    with pytest.raises(ExecutionNotAuthorizedError):
        StrategyRouter(live_enabled=True)


def test_router_execute_always_raises():
    r = StrategyRouter()
    order = r.route(_decision("hedge"))
    with pytest.raises(ExecutionNotAuthorizedError):
        r.execute(order)


def test_router_hold_produces_no_order():
    r = StrategyRouter()
    assert r.route(_decision("hold")) is None
    assert r.dropped_holds == 1


def test_router_hedge_is_reduce_risk():
    r = StrategyRouter()
    o = r.route(_decision("hedge"))
    assert isinstance(o, IntendedOrder)
    assert o.side == "reduce_risk"
    assert o.to_dict()["DRY_RUN"] is True


def test_router_enter_is_add_exposure():
    r = StrategyRouter()
    o = r.route(_decision("enter"))
    assert o.side == "add_exposure"


# ===== Closed-loop integration test ======================================

def test_closed_loop_signal_to_decision_to_outcome_to_weight_update(tmp_path):
    """The spec's closed-loop test: a decision whose outcome escalates when
    a lens fires should, after learning, raise that lens's weight; the
    WeightingEngine then serves the updated weight to the orchestrator."""
    w = LedgerWriter(tmp_path / "loop.db")
    from engine.orchestrator.regime_weights import REGIME_WEIGHTS
    prior = REGIME_WEIGHTS["adversarial"]["graph"]

    # Seed: in 'adversarial', graph fires strong and outcomes escalate.
    _seed_ledger(w, "adversarial", 50, strong_lens="graph",
                 outcome_for_strong=0.9, other_outcome=0.0)

    # Learn → persist → serve.
    learner = Learner(blend_alpha=0.6)
    learned = learner.learn(w)
    path = tmp_path / "weights.json"
    learner.persist(learned, path)
    we = WeightingEngine(path)

    updated = we.get_weights("adversarial")["graph"]
    assert updated != prior, "weight did not change after learning"
    assert updated > prior, "predictive lens should gain weight"
    validate_weights(we.get_weights("adversarial"))
    w.close()
