"""Stage-1: the deterministic harness (the moat). No LLM, no LangGraph."""

from __future__ import annotations

from engine.research_loop.state import (
    MetricBlock, HardGate, GlobalState, new_state, promotion_block,
)
from engine.research_loop.ledger import Ledger
from engine.research_loop.gates import (
    seal_holdout, verify_holdout, evaluate_hard_gate, evaluate_oos_gate,
)
from engine.research_loop import forensic_data as fd


# ── ledger / multiple-testing ───────────────────────────────────────────────

def test_ledger_counts_and_correction(tmp_path):
    lg = Ledger(str(tmp_path / "l.db"))
    assert lg.count() == 0
    n1 = lg.record("h1", "famA", "IS", ts="t", p_value=0.04)
    n2 = lg.record("h2", "famA", "IS", ts="t", p_value=0.001)
    assert (n1, n2) == (1, 2)
    # p=0.04 passes uncorrected but FAILS after correcting for N=2 (0.05/2=0.025)
    assert lg.passes_correction(0.04, n=2) is False
    assert lg.passes_correction(0.001, n=2) is True
    assert lg.count("famA") == 2 and lg.count("other") == 0
    lg.close()


# ── holdout seal / tripwire ─────────────────────────────────────────────────

def test_holdout_seal_detects_tampering():
    win = ["20260610_13", "20260610_14"]
    contract = {"holdout_windows": list(win), "holdout_sha256": seal_holdout(win)}
    verify_holdout(contract)                       # unchanged: ok
    contract["holdout_windows"].append("20260610_15")   # someone widened it
    try:
        verify_holdout(contract)
        assert False, "expected seal-broken RuntimeError"
    except RuntimeError as e:
        assert "SEAL BROKEN" in str(e)


# ── HARD_GATE ───────────────────────────────────────────────────────────────

def _is(p, sign, n=200, regimes=("+", "+")):
    return MetricBlock(segment_label="IN_SAMPLE", effect=0.3, edge_sign=sign,
                       n_obs=n, p_value=p, notes="no_lookahead",
                       per_regime=[{"edge_sign": s} for s in regimes])


def test_hard_gate_passes_clean_forensic_result():
    g = evaluate_hard_gate(_is(0.0001, "+"), "MEMPOOL_FORENSIC", ledger_n=1)
    assert g.is_stage_passed is True        # IS stage clears -> spend the holdout
    assert g.passed is False                # but full gate needs OOS sign-holds
    g.sign_holds_is_to_oos = True           # (set by OOS_GATE later)
    assert g.passed is True


def test_hard_gate_rejects_on_multiple_testing():
    # p=0.01 is fine alone but fails once the ledger has tried 100 hypotheses
    g = evaluate_hard_gate(_is(0.01, "+"), "MEMPOOL_FORENSIC", ledger_n=100)
    assert g.mc_corrected_significant is False and g.passed is False


def test_hard_gate_rejects_regime_inconsistent():
    g = evaluate_hard_gate(_is(0.0001, "+", regimes=("+", "-")),
                           "MEMPOOL_FORENSIC", ledger_n=1)
    assert g.regime_robust is False and g.passed is False


def test_hard_gate_requires_costs_for_price_series():
    g = evaluate_hard_gate(_is(0.0001, "+"), "PRICE_SERIES", ledger_n=1)
    assert g.survives_costs is False and g.passed is False   # net_sharpe None


# ── OOS_GATE: the sign-must-hold law ────────────────────────────────────────

def test_oos_gate_sign_flip_is_fatal():
    is_m = MetricBlock(segment_label="IN_SAMPLE", effect=0.3, edge_sign="+", n_obs=200)
    oos = MetricBlock(segment_label="OUT_OF_SAMPLE", effect=-0.2, edge_sign="-", n_obs=200)
    ok, reason = evaluate_oos_gate(is_m, oos)
    assert ok is False and "SIGN FLIP" in reason


def test_oos_gate_passes_when_sign_holds():
    is_m = MetricBlock(segment_label="IN_SAMPLE", effect=0.30, edge_sign="+", n_obs=200)
    oos = MetricBlock(segment_label="OUT_OF_SAMPLE", effect=0.22, edge_sign="+", n_obs=200)
    ok, _ = evaluate_oos_gate(is_m, oos)
    assert ok is True


def test_oos_gate_rejects_excess_decay():
    is_m = MetricBlock(segment_label="IN_SAMPLE", effect=0.30, edge_sign="+", n_obs=200)
    oos = MetricBlock(segment_label="OUT_OF_SAMPLE", effect=0.05, edge_sign="+", n_obs=200)
    ok, reason = evaluate_oos_gate(is_m, oos)        # 83% decay > 50% budget
    assert ok is False and "decay" in reason


# ── promotion arithmetic ────────────────────────────────────────────────────

def test_promotion_blocked_until_everything_passes():
    s: GlobalState = new_state("h1", "seed")
    assert promotion_block(s) is not None            # no data contract yet
    s["data_contract"] = {"data_regime": "MEMPOOL_FORENSIC",
                          "source_uri": "duckdb:engine/data/mempool/*.jsonl",
                          "train_windows": [], "holdout_windows": [],
                          "holdout_sha256": "x", "coverage_fraction": 0.45}
    s["hard_gate"] = HardGate(sign_holds_is_to_oos=True, mc_corrected_significant=True,
                              min_obs_met=True, regime_robust=True,
                              no_lookahead_verified=True, survives_costs=True,
                              market_relative_ok=True)
    assert promotion_block(s) is not None            # critic hasn't PASSed
    s["critiques"] = [{"verdict": "PASS"}]
    assert promotion_block(s) is None                # now (and only now) clear


# ── forensic substrate against the REAL archives ────────────────────────────

def test_forensic_windows_and_measurement_run():
    windows = fd.available_windows()
    assert isinstance(windows, list)
    if not windows:
        return                                       # no overlapping data yet — skip
    train, hold = fd.split_windows(windows)
    assert train and hold and not set(train) & set(hold)   # disjoint
    res = fd.run_measurement("private_share_vs_blocksize", train)
    assert set(res) >= {"effect", "edge_sign", "n_obs", "p_value",
                        "per_regime", "coverage_fraction", "notes"}
    assert res["n_obs"] >= 0 and "no_lookahead" in res["notes"]
    if res["coverage_fraction"] is not None:
        assert 0.0 <= res["coverage_fraction"] <= 1.0
