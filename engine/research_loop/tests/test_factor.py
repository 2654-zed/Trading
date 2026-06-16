"""Factor-test path: planted-signal detection, cost-drag null, survivorship
bias, and that factor metrics flow through the SAME deterministic gates."""

from __future__ import annotations

from engine.research_loop.factor_data import (
    make_synthetic_panel, make_survivorship_biased, split_by_date,
    REQUIRED_COLUMNS, validate_panel,
)
from engine.research_loop.factor_measurements import (
    carry_factor, xs_momentum_factor,
)
from engine.research_loop.state import MetricBlock
from engine.research_loop.gates import evaluate_hard_gate, evaluate_oos_gate


def test_panel_contract_validates():
    df = make_synthetic_panel(n_tokens=30, n_days=40, seed=1)
    validate_panel(df)                                   # has all required cols
    assert set(REQUIRED_COLUMNS) <= set(df.columns)
    assert (~df["is_listed"]).any()                      # some tokens die


def test_planted_momentum_detected():
    df = make_synthetic_panel(planted="momentum", seed=2)
    r = xs_momentum_factor(df, lookback=20)
    assert r["edge_sign"] == "+" and r["p_value"] < 0.01
    assert r["net_sharpe_after_costs"] > 0


def test_planted_carry_detected():
    df = make_synthetic_panel(planted="carry", seed=3)
    r = carry_factor(df)
    assert r["edge_sign"] == "+" and r["p_value"] < 0.01


def test_null_panel_shows_no_positive_edge():
    # no planted factor: after costs a no-skill long-short does NOT show a
    # significant POSITIVE edge (cost drag makes it ~zero/negative).
    df = make_synthetic_panel(planted=None, seed=4)
    r = xs_momentum_factor(df, lookback=20)
    assert not (r["edge_sign"] == "+" and (r["p_value"] or 1) < 0.05)


def test_survivorship_bias_changes_the_answer():
    df = make_synthetic_panel(planted="momentum", seed=5, deaths=True)
    full = xs_momentum_factor(df, lookback=20)
    biased = xs_momentum_factor(make_survivorship_biased(df), lookback=20)
    # dropping every token that ever died materially moves the measured effect
    assert full["effect"] is not None and biased["effect"] is not None
    assert abs(full["effect"] - biased["effect"]) > 1e-4


def test_factor_metrics_flow_through_the_same_gates():
    df = make_synthetic_panel(planted="carry", seed=6)
    train, hold = split_by_date(df)
    ism = carry_factor(train)
    oosm = carry_factor(hold)
    is_block = MetricBlock(segment_label="IN_SAMPLE", **{
        k: ism[k] for k in ("effect", "edge_sign", "n_obs", "p_value",
                            "per_regime", "net_sharpe_after_costs",
                            "excess_return_vs_bench", "notes")})
    g = evaluate_hard_gate(is_block, "PRICE_SERIES", ledger_n=1)
    assert g.is_stage_passed is True                     # clean planted factor clears IS
    oos_block = MetricBlock(segment_label="OUT_OF_SAMPLE",
                            effect=oosm["effect"], edge_sign=oosm["edge_sign"],
                            n_obs=oosm["n_obs"])
    ok, _ = evaluate_oos_gate(is_block, oos_block)
    assert ok is True                                    # sign holds IS->OOS
