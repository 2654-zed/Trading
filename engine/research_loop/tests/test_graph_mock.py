"""Stage-2: the full LangGraph loop driven by a MockLLM down every routing
path. The measurement is monkeypatched to controlled numbers so the GRAPH
PLUMBING (cyclic debate, the gates, the asymmetry) is tested deterministically,
independent of what the real archives happen to contain. One end-to-end test
then runs against real data."""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from engine.research_loop import graph as G
from engine.research_loop.llm import MockLLMClient
from engine.research_loop.ledger import Ledger
from engine.research_loop.state import new_state

CFG = {"recursion_limit": 50}


@pytest.fixture
def fake_windows(monkeypatch):
    monkeypatch.setattr(G, "available_windows",
                        lambda: [f"2026010{i}_10" for i in range(1, 7)])


def _quant(sign="+"):
    return {"hypothesis": "h", "math_definition": "r(x,y)", "falsifier": "sign flips OOS",
            "predicted_sign": sign, "family_tag": "famA",
            "measurement_key": "private_share_vs_blocksize"}

def _clean_metric(sign="+", p=0.0001, n=200):
    return {"effect": 0.3 if sign == "+" else -0.3, "edge_sign": sign, "n_obs": n,
            "p_value": p, "per_regime": [{"edge_sign": sign}, {"edge_sign": sign}],
            "coverage_fraction": 0.45, "notes": "no_lookahead"}


def _run(llm, monkeypatch, measure):
    monkeypatch.setattr(G, "run_measurement", measure)
    g = G.build_graph(llm, Ledger(":memory:"))
    return g.invoke(new_state("H", "seed"), CFG)


def test_theory_fail_routes_to_rejected_theory(fake_windows, monkeypatch):
    llm = MockLLMClient({"quant": [_quant()],
                         "critic_theory": [{"score": 10, "verdict": "FAIL",
                                            "flaws": ["fatal: lives in private blind spot"], "demands": []}]})
    out = _run(llm, monkeypatch, lambda *a, **k: _clean_metric())
    assert out["status"] == "REJECTED_THEORY"


def test_three_weak_revisions_exhaust(fake_windows, monkeypatch):
    llm = MockLLMClient({
        "quant": [_quant(), _quant(), _quant()],
        "critic_theory": [{"score": 50, "verdict": "PASS", "flaws": ["weak"], "demands": ["x"]}] * 3})
    out = _run(llm, monkeypatch, lambda *a, **k: _clean_metric())
    assert out["status"] == "REJECTED_EXHAUSTED"
    assert out["iteration"] == 3


def test_weak_in_sample_rejected(fake_windows, monkeypatch):
    llm = MockLLMClient({"quant": [_quant()],
                         "critic_theory": [{"score": 85, "verdict": "PASS", "flaws": ["c"], "demands": []}],
                         "coder": [{"measurement_key": "private_share_vs_blocksize", "params": {}}]})
    # IS measurement is insignificant (p=0.5) → fails hard gate before holdout
    out = _run(llm, monkeypatch, lambda *a, **k: _clean_metric(p=0.5))
    assert out["status"] == "REJECTED_WEAK_IS"
    assert out.get("holdout_consumed") is not True       # holdout NEVER touched


def test_oos_sign_flip_rejected(fake_windows, monkeypatch):
    calls = {"n": 0}
    def measure(key, windows):
        calls["n"] += 1
        return _clean_metric("+") if calls["n"] == 1 else _clean_metric("-")  # IS + , OOS -
    llm = MockLLMClient({"quant": [_quant()],
                         "critic_theory": [{"score": 85, "verdict": "PASS", "flaws": ["c"], "demands": []}],
                         "coder": [{"measurement_key": "private_share_vs_blocksize", "params": {}}]})
    out = _run(llm, monkeypatch, measure)
    assert out["status"] == "REJECTED_OOS"
    assert out["holdout_consumed"] is True               # holdout read exactly once


def test_confirmed_oos_only_through_both_gates(fake_windows, monkeypatch):
    llm = MockLLMClient({"quant": [_quant()],
                         "critic_theory": [{"score": 85, "verdict": "PASS", "flaws": ["c"], "demands": []}],
                         "coder": [{"measurement_key": "private_share_vs_blocksize", "params": {}}],
                         "critic_postmortem": [{"verdict": "PASS", "flaws": []}]})
    out = _run(llm, monkeypatch, lambda *a, **k: _clean_metric("+"))  # holds IS & OOS
    assert out["status"] == "CONFIRMED_OOS"
    assert out["hard_gate"].passed is True


def test_postmortem_can_veto_a_passing_gate(fake_windows, monkeypatch):
    # gates pass, but the post-mortem critic finds an empirical trap → veto
    llm = MockLLMClient({"quant": [_quant()],
                         "critic_theory": [{"score": 85, "verdict": "PASS", "flaws": ["c"], "demands": []}],
                         "coder": [{"measurement_key": "private_share_vs_blocksize", "params": {}}],
                         "critic_postmortem": [{"verdict": "FAIL", "flaws": ["effect is one sub-window"]}]})
    out = _run(llm, monkeypatch, lambda *a, **k: _clean_metric("+"))
    assert out["status"] == "REJECTED_POSTMORTEM"        # LLM can veto a passing gate


def test_end_to_end_on_real_archives_reaches_a_terminal():
    from engine.research_loop.forensic_data import available_windows
    if len(available_windows()) < 4:
        pytest.skip("need ≥4 overlapping capture windows")
    from engine.research_loop.run import _mock_llm
    g = G.build_graph(_mock_llm(), Ledger(":memory:"))
    out = g.invoke(new_state("H", "seed"), CFG)
    assert out["status"] in {"CONFIRMED_OOS", "REJECTED_WEAK_IS", "REJECTED_OOS",
                             "REJECTED_POSTMORTEM", "REJECTED_NO_DATA"}
