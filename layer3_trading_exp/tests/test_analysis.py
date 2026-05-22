"""Unit tests for the analysis package.

Pure-function tests against synthetic JSONL records. Each module's `compute`
function is tested with hand-crafted input where expected output is derivable
without running the implementation. Plot functions are smoke-tested for
non-empty PNG output (we don't validate pixels).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer3_trading_exp.analysis import (
    _loader,
    generate_report as _gr,
    h1_opportunity_rate as _h1,
    h2_filter_activation as _h2,
    h3_distributional_comparison as _h3,
)


def _make_record(
    opp_id: str,
    *,
    date: str = "2026-05-08",
    block: int = 1,
    margin_bps: int = 31,
    expected_gain_usd: float = 26.0,
    hard: bool = False,
    soft: bool = False,
    fired_rule_ids: tuple[int, ...] = (),
    protocols: tuple[str, str] = ("uniswap_v3", "aerodrome_slipstream"),
    borrowed: str = "0x" + "11" * 20,
    other: str = "0x" + "22" * 20,
    pools: tuple[str, str] = ("0x" + "aa" * 20, "0x" + "bb" * 20),
    layer3_stale: bool = False,
) -> dict:
    fr = {}
    for rid in range(1, 14):
        fr[f"rule_{rid}_x"] = {"fired": rid in fired_rule_ids,
                               "severity": "hard" if rid <= 5 else "soft" if rid <= 9 else "weak",
                               "matches": []}
    return {
        "opportunity_id": opp_id,
        "timestamp": f"{date}T03:00:00+00:00",
        "block_number": block,
        "chain": "base",
        "path": {"tokens": [borrowed, other], "pools": list(pools)},
        "pool_protocols": list(protocols),
        "margin_gross_bps": margin_bps,
        "gas_estimate_usd": 0.005,
        "expected_gain_usd": expected_gain_usd,
        "depth_notional_usd": 10_000.0,
        "filter_results": fr,
        "layer3_freshness": {"stale_tables": [], "degraded": layer3_stale},
        "hard_flagged": hard,
        "soft_flagged": soft,
        "flag_summary": [],
        "layer3_stale": layer3_stale,
    }


def _write_jsonl(records: list[dict], date: str, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{date}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


# ---- _loader ----

def test_loader_reads_date_range_and_skips_missing(tmp_path: Path):
    _write_jsonl([_make_record("a", date="2026-05-08")], "2026-05-08", tmp_path)
    # 2026-05-09 has no file → skip silently.
    _write_jsonl([_make_record("b", date="2026-05-10")], "2026-05-10", tmp_path)
    records = _loader.load_records("2026-05-08", "2026-05-10", tmp_path)
    assert [r["opportunity_id"] for r in records] == ["a", "b"]


def test_loader_filters_by_flag(tmp_path: Path):
    _write_jsonl([
        _make_record("clean"),
        _make_record("hard", hard=True),
        _make_record("soft", soft=True),
    ], "2026-05-08", tmp_path)
    only_hard = _loader.load_records_filtered("2026-05-08", "2026-05-08", tmp_path, hard_flagged=True)
    only_unflagged = _loader.load_records_filtered("2026-05-08", "2026-05-08", tmp_path, unflagged_only=True)
    assert [r["opportunity_id"] for r in only_hard] == ["hard"]
    assert [r["opportunity_id"] for r in only_unflagged] == ["clean"]


def test_loader_dedup_unique_per_day_collapses_emissions(tmp_path: Path):
    """Same opportunity_key emitted 5 times in a day -> 1 unique record kept."""
    same_args = dict(borrowed="0x" + "11" * 20, other="0x" + "22" * 20,
                     pools=("0x" + "aa" * 20, "0x" + "bb" * 20))
    records = [_make_record(f"emit-{i}", **same_args) for i in range(5)]
    deduped = _loader.dedup_unique_per_day(records)
    assert len(deduped) == 1
    assert deduped[0]["opportunity_id"] == "emit-0"


def test_loader_dedup_keeps_distinct_borrowed_token_separate(tmp_path: Path):
    a = _make_record("borrow-A", borrowed="0x" + "aa" * 20, other="0x" + "bb" * 20)
    b = _make_record("borrow-B", borrowed="0x" + "bb" * 20, other="0x" + "aa" * 20)
    deduped = _loader.dedup_unique_per_day([a, b])
    assert len(deduped) == 2


# ---- H1 ----

def test_h1_counts_emissions_unique_episodes(tmp_path: Path):
    """3 emissions of the same key in the same block -> 1 episode + 1 unique."""
    same = dict(borrowed="0x" + "11" * 20, other="0x" + "22" * 20,
                pools=("0x" + "aa" * 20, "0x" + "bb" * 20))
    records = [_make_record(f"r-{i}", block=100, **same) for i in range(3)]
    stats = _h1.compute(records)
    assert stats["emissions_total"] == 3
    assert stats["unique_total"] == 1
    assert stats["episodes_total"] == 1


def test_h1_episodes_count_arrivals_after_gaps():
    """Key fires in block 1, absent in block 2, fires again in block 3 -> 2 episodes."""
    same = dict(borrowed="0x" + "11" * 20, other="0x" + "22" * 20,
                pools=("0x" + "aa" * 20, "0x" + "bb" * 20))
    records = [
        _make_record("first", block=1, **same),
        _make_record("again", block=3, **same),  # gap at block 2
    ]
    stats = _h1.compute(records)
    assert stats["episodes_total"] == 2


def test_h1_bootstrap_ci_reproducible_with_seed():
    """Same input with the same seed must produce identical CI bounds."""
    values = [10, 12, 8, 11, 9, 14, 7, 13, 10, 12]
    a = _h1._bootstrap_mean_ci(values, seed=42)
    b = _h1._bootstrap_mean_ci(values, seed=42)
    assert a == b


def test_h1_bootstrap_ci_brackets_mean():
    values = [100, 110, 90, 105, 95, 115, 85]
    mean, low, high = _h1._bootstrap_mean_ci(values)
    assert low <= mean <= high


def test_h1_threshold_check_when_above_1000_per_day():
    same = dict(borrowed="0x" + "11" * 20, other="0x" + "22" * 20,
                pools=("0x" + "aa" * 20, "0x" + "bb" * 20))
    # Two days with 1500 emissions each → mean 1500 > 1000 threshold.
    records = []
    for i in range(1500):
        records.append(_make_record(f"d1-{i}", date="2026-05-08", block=i, **same))
        records.append(_make_record(f"d2-{i}", date="2026-05-09", block=i, **same))
    stats = _h1.compute(records)
    assert stats["emissions_mean_per_day"] == pytest.approx(1500.0, rel=1e-9)
    assert stats["h1_emissions_threshold_met"] is True
    # Unique-per-day = 1 each day → falls under threshold.
    assert stats["h1_unique_threshold_met"] is False


# ---- H2 ----

def test_h2_per_rule_fire_rates_match_hand_count():
    records = [
        _make_record("a", fired_rule_ids=(1,)),
        _make_record("b", fired_rule_ids=(1, 6)),
        _make_record("c", fired_rule_ids=()),
        _make_record("d", fired_rule_ids=(6,)),
    ]
    stats = _h2.compute(records)
    # Rule 1 fires on 2/4, Rule 6 fires on 2/4.
    assert stats["per_rule"]["rule_1"]["fire_count"] == 2
    assert stats["per_rule"]["rule_1"]["fire_rate"] == pytest.approx(0.5)
    assert stats["per_rule"]["rule_6"]["fire_count"] == 2
    assert stats["per_rule"]["rule_3"]["fire_count"] == 0


def test_h2_excludes_degraded_records_from_denominator():
    """Degraded records are not eligible — their rule fires are not counted
    AND they don't inflate the denominator."""
    records = [
        _make_record("clean", fired_rule_ids=(1,), layer3_stale=False),
        _make_record("stale", fired_rule_ids=(1,), layer3_stale=True),
    ]
    stats = _h2.compute(records)
    # 1 eligible record with rule_1 firing → rate = 1.0
    assert stats["eligible_records"] == 1
    assert stats["degraded_records"] == 1
    assert stats["per_rule"]["rule_1"]["fire_rate"] == pytest.approx(1.0)


def test_h2_co_occurrence_diagonal_equals_total_fires():
    records = [
        _make_record("a", fired_rule_ids=(1, 6)),
        _make_record("b", fired_rule_ids=(1,)),
    ]
    stats = _h2.compute(records)
    matrix = stats["co_occurrence_matrix"]
    # Rule 1 fired in 2 records → diagonal[0][0] = 2.
    assert matrix[0][0] == 2
    # Rule 1 + Rule 6 co-fired in 1 record → off-diagonal[0][5] = matrix[5][0] = 1.
    assert matrix[0][5] == 1
    assert matrix[5][0] == 1


def test_h2_threshold_met_at_1_pct_or_more():
    # 100 records with rule 1 firing in 1 → exactly 1% rate, meets threshold.
    records = [_make_record(f"r-{i}", fired_rule_ids=(1,) if i == 0 else ()) for i in range(100)]
    stats = _h2.compute(records)
    assert stats["per_rule"]["rule_1"]["fire_rate"] == pytest.approx(0.01)
    assert stats["per_rule"]["rule_1"]["meets_h2_threshold"] is True
    # Any rule meets threshold → top-level boolean True
    assert stats["any_rule_meets_h2_threshold"] is True


# ---- H3 ----

def test_h3_partition_groups_match_records():
    records = [
        _make_record("clean"),
        _make_record("hard", hard=True),
        _make_record("soft", soft=True),
        _make_record("both", hard=True, soft=True),
        _make_record("stale", layer3_stale=True),  # excluded
    ]
    stats = _h3.compute(records)
    # 4 eligible records (excluding the stale one).
    assert stats["group_sizes"]["all_eligible"] == 4
    assert stats["group_sizes"]["hard_flagged"] == 2  # `hard` + `both`
    assert stats["group_sizes"]["soft_flagged_only"] == 1  # `soft`
    assert stats["group_sizes"]["unflagged"] == 1  # `clean`
    assert stats["group_sizes"]["flagged_any"] == 3


def test_h3_mann_whitney_returns_p_value_for_distinct_distributions():
    # flagged margins = [50, 60, 70, 80], unflagged = [20, 25, 30, 35]
    flagged = [_make_record(f"f-{i}", hard=True, margin_bps=m)
               for i, m in enumerate([50, 60, 70, 80])]
    unflagged = [_make_record(f"u-{i}", margin_bps=m)
                 for i, m in enumerate([20, 25, 30, 35])]
    stats = _h3.compute(flagged + unflagged)
    mw = stats["numeric_comparisons"]["margin_gross_bps"]["mann_whitney"]
    # Non-overlapping distributions → p should be small.
    assert mw["p_value"] is not None
    assert mw["p_value"] < 0.05
    # Effect size: flagged > unflagged → rank-biserial near +1.
    assert mw["rank_biserial"] is not None
    assert mw["rank_biserial"] > 0.5


def test_h3_chi_square_handles_degenerate_table():
    """Single-category contingency table → chi-square not computed, reason given."""
    records = [
        _make_record("a", hard=True, protocols=("uniswap_v3", "aerodrome_volatile")),
        _make_record("b",            protocols=("uniswap_v3", "aerodrome_volatile")),
    ]
    stats = _h3.compute(records)
    chi = stats["categorical_comparisons"]["pool_protocols"]["chi_square"]
    assert chi["p_value"] is None
    assert "table too small" in (chi["reason"] or "")


# ---- generate_report ----

def test_generate_report_contains_required_sections():
    h1 = _h1.compute([_make_record("a")])
    h2 = _h2.compute([_make_record("a")])
    h3 = _h3.compute([_make_record("a")])
    plots = {
        "h1_per_day": "p1.png", "h1_margin_hist": "p2.png",
        "h2_rule_rates": "p3.png", "h2_co_occurrence": "p4.png",
        "h3_margin_dists": "p5.png", "h3_protocol_dist": "p6.png",
    }
    text = _gr.render_report(
        h1_stats=h1, h2_stats=h2, h3_stats=h3,
        plot_paths=plots,
        start_date="2026-05-01", end_date="2026-05-30",
        generated_at="2026-05-31T00:00:00+00:00",
    )
    # Spec-required hypothesis sections, all present.
    assert "## H1 — Opportunity rate" in text
    assert "## H2 — Filter activation" in text
    assert "## H3 — Distributional comparison" in text
    # Spec acceptance: explicit `TO BE COMPLETED BY JASON` placeholder, one per H.
    assert text.count("TO BE COMPLETED BY JASON") == 3
    # All plots referenced.
    for p in plots.values():
        assert p in text


def test_generate_report_idempotent_for_same_inputs():
    h1 = _h1.compute([_make_record("a"), _make_record("b", hard=True)])
    h2 = _h2.compute([_make_record("a"), _make_record("b", hard=True)])
    h3 = _h3.compute([_make_record("a"), _make_record("b", hard=True)])
    plots = {
        "h1_per_day": "p1.png", "h1_margin_hist": "p2.png",
        "h2_rule_rates": "p3.png", "h2_co_occurrence": "p4.png",
        "h3_margin_dists": "p5.png", "h3_protocol_dist": "p6.png",
    }
    a = _gr.render_report(
        h1_stats=h1, h2_stats=h2, h3_stats=h3, plot_paths=plots,
        start_date="2026-05-01", end_date="2026-05-30",
        generated_at="2026-05-31T00:00:00+00:00",
    )
    b = _gr.render_report(
        h1_stats=h1, h2_stats=h2, h3_stats=h3, plot_paths=plots,
        start_date="2026-05-01", end_date="2026-05-30",
        generated_at="2026-05-31T00:00:00+00:00",
    )
    assert a == b


# ---- Plot smoke tests ----

def test_plots_produce_non_empty_png(tmp_path: Path):
    """Plots should write PNGs without raising. We don't validate pixels."""
    records = [
        _make_record("a", margin_bps=31, hard=True, fired_rule_ids=(1,)),
        _make_record("b", margin_bps=40),
        _make_record("c", margin_bps=35, soft=True, fired_rule_ids=(6,)),
    ]
    h1_stats = _h1.compute(records)
    h2_stats = _h2.compute(records)

    out = tmp_path / "h1_per_day.png"
    _h1.plot_per_day_counts(h1_stats, out)
    assert out.exists() and out.stat().st_size > 0

    out = tmp_path / "h1_margin.png"
    _h1.plot_margin_histogram(records, out)
    assert out.exists() and out.stat().st_size > 0

    out = tmp_path / "h2_rates.png"
    _h2.plot_per_rule_fire_rates(h2_stats, out)
    assert out.exists() and out.stat().st_size > 0

    out = tmp_path / "h2_heatmap.png"
    _h2.plot_co_occurrence_heatmap(h2_stats, out)
    assert out.exists() and out.stat().st_size > 0

    out = tmp_path / "h3_margins.png"
    _h3.plot_margin_distributions(records, out)
    assert out.exists() and out.stat().st_size > 0

    out = tmp_path / "h3_protocols.png"
    _h3.plot_protocol_distribution(records, out)
    assert out.exists() and out.stat().st_size > 0


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.7 (D-009, D-013): H1' + H4 analyses.
# ----------------------------------------------------------------------------


from layer3_trading_exp.analysis import (
    h1_prime as _h1p,
    h4_pareto as _h4,
)


def _cross_chain_record(
    opp_id: str,
    *,
    date: str = "2026-06-01",
    block: int = 1,
    margin_bps: int = 60,          # post-haircut
    margin_raw_bps: int = 75,       # pre-haircut
    haircut_bps: float = 15.0,
    src_chain: str = "base",
    dst_chain: str = "arbitrum",
    mid_token: str = "WETH",
    borrow_token: str = "USDC",
    borrow_addr: str = "0xu1" + "00" * 19,
    mid_addr_src: str = "0xw1" + "00" * 19,
    mid_addr_dst: str = "0xw2" + "00" * 19,
    pools: tuple[str, str] = ("0xb1" + "00" * 19, "0xa1" + "00" * 19),
) -> dict:
    # Build a minimal v2 record with bridge_legs populated.
    return {
        "opportunity_id": opp_id,
        "timestamp": f"{date}T12:00:00+00:00",
        "block_number": block,
        "chain": src_chain,
        "path": {"tokens": [borrow_addr, mid_addr_src], "pools": list(pools)},
        "pool_protocols": ["uniswap_v3", "uniswap_v3"],
        "margin_gross_bps": margin_bps,
        "gas_estimate_usd": 0.05,
        "expected_gain_usd": 50.0,
        "depth_notional_usd": 10_000.0,
        "filter_results": {},
        "layer3_freshness": {"stale_tables": [], "degraded": False},
        "hard_flagged": False, "soft_flagged": False,
        "flag_summary": [], "layer3_stale": False,
        "schema_version": 2,
        "chains": sorted({src_chain, dst_chain}),
        "path_chains": [src_chain, dst_chain],
        "bridge_legs": [
            {"src_chain": src_chain, "dst_chain": dst_chain, "token": mid_token,
             "token_address_src": mid_addr_src, "token_address_dst": mid_addr_dst,
             "fee_bps": 8.0, "latency_seconds": 30.0},
            {"src_chain": dst_chain, "dst_chain": src_chain, "token": borrow_token,
             "token_address_src": borrow_addr, "token_address_dst": borrow_addr,
             "fee_bps": 10.0, "latency_seconds": 30.0},
        ],
        "latency_drift_haircut_bps": haircut_bps,
        "gross_margin_raw_bps": margin_raw_bps,
        "borrow_chain": src_chain,
        "cost_breakdown": {
            "gas_usd": 0.05, "flash_loan_fee_usd": 5.0,
            "bridge_fees_bps": 18.0,
            "swap_fees_bps_total": 10.0,
            "latency_drift_haircut_bps": haircut_bps,
        },
    }


def test_h1_prime_returns_zero_for_v1_only_dataset():
    """Phase 1 records (no bridge_legs) → H1' computes 0 cross-chain opps."""
    records = [
        _make_record(f"opp-{i}", date="2026-05-13", block=100 + i)
        for i in range(10)
    ]
    stats = _h1p.compute(records)
    assert stats["emissions_total"] == 0
    assert stats["unique_total"] == 0
    assert stats["episodes_total"] == 0
    assert not stats["h1_prime_unique_threshold_met"]


def test_h1_prime_counts_cross_chain_records_per_day():
    """Manufacture 60 unique cross-chain records over 1 day; H1' should
    extrapolate above the 50/day threshold."""
    records = []
    # 60 different opportunities on 2026-06-01 — unique by pool_pair.
    for i in range(60):
        records.append(_cross_chain_record(
            f"cc-{i}", date="2026-06-01", block=i,
            pools=(f"0xb{i:>03d}" + "0" * 36, f"0xa{i:>03d}" + "0" * 36),
        ))
    stats = _h1p.compute(records)
    assert stats["emissions_total"] == 60
    assert stats["unique_total"] == 60
    assert stats["unique_mean_per_day"] == 60.0
    assert stats["h1_prime_unique_threshold_met"]
    # Single-day data shouldn't trigger falsification (requires ≥7 days).
    assert not stats["h1_prime_falsified"]


def test_h1_prime_dedups_repeated_emissions_to_single_unique():
    """20 emissions of the same opportunity on one day → 1 unique."""
    records = [
        _cross_chain_record(f"cc-{i}", date="2026-06-01", block=100 + i,
                            pools=("0xb1" + "00" * 19, "0xa1" + "00" * 19))
        for i in range(20)
    ]
    stats = _h1p.compute(records)
    assert stats["emissions_total"] == 20
    assert stats["unique_total"] == 1


def test_h1_prime_chain_pair_breakdown():
    """Two distinct chain pairs each get their own bucket."""
    records = []
    for i in range(5):
        records.append(_cross_chain_record(
            f"ba-{i}", date="2026-06-01", block=i,
            src_chain="base", dst_chain="arbitrum",
            pools=(f"0xba{i:03d}" + "0" * 35, f"0xab{i:03d}" + "0" * 35),
        ))
    for i in range(3):
        records.append(_cross_chain_record(
            f"bo-{i}", date="2026-06-01", block=100 + i,
            src_chain="base", dst_chain="optimism",
            pools=(f"0xbo{i:03d}" + "0" * 35, f"0xob{i:03d}" + "0" * 35),
        ))
    stats = _h1p.compute(records)
    pairs = stats["chain_pair_unique_counts"]
    assert pairs.get("base->arbitrum") == 5
    assert pairs.get("base->optimism") == 3


def test_h1_prime_haircut_reflected_in_margin_distribution():
    rec = _cross_chain_record("cc-1", margin_bps=60, margin_raw_bps=75,
                              haircut_bps=15.0)
    stats = _h1p.compute([rec])
    assert stats["margin_bps_post_haircut_p50"] == 60
    assert stats["margin_bps_raw_p50"] == 75
    assert stats["haircut_bps_p50"] == 15.0


def test_h4_concentration_top_20pct_covers_80pct():
    """Manufacture a skewed distribution: the hot (base, arbitrum, USDC)
    bucket is active across many days; each cold bucket is active only
    one day. H4 dedup is per-(day, bucket), so the hot bucket gets many
    events while the cold ones each get one. Top-1 / 5 = top-20%; with
    16 hot vs 4 cold events the top-20% covers 80%."""
    records = []
    # 16 days of activity in the hot bucket — 16 (day, bucket) events.
    for i in range(16):
        records.append(_cross_chain_record(
            f"hot-{i}", date=f"2026-06-{i + 1:02d}", block=100 + i,
            src_chain="base", dst_chain="arbitrum", borrow_token="USDC",
            pools=(f"0x{i:040x}", f"0x{i+1000:040x}"),
        ))
    # 4 cold buckets, each active for one day → 1 event each.
    other_routes = [
        ("base", "optimism", "USDC"),
        ("arbitrum", "base", "USDC"),
        ("arbitrum", "optimism", "USDC"),
        ("optimism", "base", "USDC"),
    ]
    for j, (src, dst, tok) in enumerate(other_routes):
        records.append(_cross_chain_record(
            f"cold-{j}", date="2026-07-01", block=10000 + j,
            src_chain=src, dst_chain=dst, borrow_token=tok,
            pools=(f"0x{10**8 + j:040x}",
                   f"0x{10**8 + j + 5:040x}"),
        ))
    stats = _h4.compute(records)
    assert stats["n_unique_buckets"] == 5
    assert stats["top_n_buckets"] == 1  # 20% of 5
    assert stats["n_unique_events"] == 20  # 16 hot + 4 cold
    # Top 1 bucket has 16/20 = 80% of events.
    assert stats["top_n_coverage"] >= 0.80
    assert stats["h4_supported"]


def test_h4_uniform_distribution_triggers_falsification():
    """5 buckets, each with equal count → top-20% covers ~20% of events
    → H4 falsified (top-20% < 40%)."""
    records = []
    routes = [
        ("base", "arbitrum"), ("arbitrum", "base"), ("base", "optimism"),
        ("optimism", "base"), ("arbitrum", "optimism"),
    ]
    for j, (src, dst) in enumerate(routes):
        for k in range(10):
            # Distinct pools and days so per-day dedup keeps all of them.
            records.append(_cross_chain_record(
                f"u-{j}-{k}", date=f"2026-06-{k + 1:02d}",
                block=100 + j * 100 + k,
                src_chain=src, dst_chain=dst,
                pools=(f"0x{j * 100 + k:040x}",
                       f"0x{j * 100 + k + 1000:040x}"),
            ))
    stats = _h4.compute(records)
    assert stats["n_unique_buckets"] == 5
    assert stats["h4_falsified"]
    assert not stats["h4_supported"]


def test_h4_empty_v1_dataset_returns_zero_stats():
    records = [
        _make_record(f"opp-{i}", date="2026-05-13", block=i) for i in range(5)
    ]
    stats = _h4.compute(records)
    assert stats["n_unique_buckets"] == 0
    assert stats["n_unique_events"] == 0
    assert not stats["h4_supported"]


def _plot_paths_phase_1() -> dict[str, str]:
    return {
        "h1_per_day": "p1.png", "h1_margin_hist": "p2.png",
        "h2_rule_rates": "p3.png", "h2_co_occurrence": "p4.png",
        "h3_margin_dists": "p5.png", "h3_protocol_dist": "p6.png",
    }


def _plot_paths_phase_2() -> dict[str, str]:
    base = _plot_paths_phase_1()
    base.update({
        "h1_prime_per_day": "p7.png",
        "h1_prime_chain_pairs": "p8.png",
        "h4_pareto": "p9.png",
        "h4_buckets": "p10.png",
    })
    return base


def test_report_renders_phase_1_shape_when_no_cross_chain():
    """Phase 1 replay back-compat: render_report with no cross-chain stats
    produces the original Phase 1 report shape."""
    records = [_make_record("opp-1")]
    h1_stats = _h1.compute(records)
    h2_stats = _h2.compute(records)
    h3_stats = _h3.compute(records)
    text = _gr.render_report(
        h1_stats=h1_stats, h2_stats=h2_stats, h3_stats=h3_stats,
        plot_paths=_plot_paths_phase_1(),
        start_date="2026-05-13", end_date="2026-05-13",
        generated_at="2026-05-16T00:00:00+00:00",
    )
    # Phase 1 heading; no H1'/H4 sections.
    assert "Phase 1 end-of-run analysis" in text
    assert "H1' — cross-chain" not in text
    assert "H4 — Pareto" not in text


def test_report_renders_phase_2_shape_with_cross_chain_stats():
    """Phase 2 dataset with cross-chain stats produces an expanded report."""
    records = [_cross_chain_record(f"cc-{i}", block=i,
                                   pools=(f"0xb{i:039x}", f"0xa{i:039x}"))
               for i in range(3)]
    h1_stats = _h1.compute(records)
    h1p_stats = _h1p.compute(records)
    h4_stats = _h4.compute(records)
    h2_stats = _h2.compute(records)
    h3_stats = _h3.compute(records)
    text = _gr.render_report(
        h1_stats=h1_stats, h1_prime_stats=h1p_stats, h4_stats=h4_stats,
        h2_stats=h2_stats, h3_stats=h3_stats,
        plot_paths=_plot_paths_phase_2(),
        start_date="2026-06-01", end_date="2026-06-01",
        generated_at="2026-06-02T00:00:00+00:00",
    )
    assert "Phase 2 end-of-run analysis" in text
    assert "H1' — cross-chain" in text
    assert "H4 — Pareto" in text
