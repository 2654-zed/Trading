"""Unit tests for daily_rollup.py.

Covers:
  - Empty-day rollup (no records → zero counts row, no missing columns)
  - Single-record, all-clean
  - Multi-record with mixed flags + per-rule counts + median margins
  - Idempotency: rerun produces byte-identical output
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from layer3_trading_exp.daily_rollup import compute_rollup, write_rollup_csv
from layer3_trading_exp.schema import ROLLUP_CSV_COLUMNS


def _make_record(
    opp_id: str,
    *,
    block: int = 1,
    margin_bps: int = 31,
    hard: bool = False,
    soft: bool = False,
    fired_rule_ids: tuple[int, ...] = (),
    protocols: tuple[str, str] = ("uniswap_v3", "aerodrome_slipstream"),
    stale_tables: tuple[str, ...] = (),
) -> dict:
    filter_results = {}
    for rid in range(1, 14):
        filter_results[f"rule_{rid}_some_name"] = {
            "fired": rid in fired_rule_ids,
            "severity": "hard" if rid <= 5 else "soft" if rid <= 9 else "weak",
            "tier": "A" if rid <= 5 else "B" if rid <= 10 else None,
            "matches": [],
        }
    return {
        "opportunity_id": opp_id,
        "timestamp": "2026-05-08T03:00:00+00:00",
        "block_number": block,
        "chain": "base",
        "path": {"tokens": ["0x" + "11" * 20], "pools": ["0x" + "aa" * 20, "0x" + "bb" * 20]},
        "pool_protocols": list(protocols),
        "margin_gross_bps": margin_bps,
        "gas_estimate_usd": 0.005,
        "expected_gain_usd": 26.0,
        "depth_notional_usd": 10_000.0,
        "filter_results": filter_results,
        "layer3_freshness": {"stale_tables": list(stale_tables), "degraded": bool(stale_tables)},
        "hard_flagged": hard,
        "soft_flagged": soft,
        "flag_summary": [],
        "layer3_stale": bool(stale_tables),
    }


def _write_jsonl(records: list[dict], date: str, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{date}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def test_compute_rollup_empty_day_returns_zero_row(tmp_path: Path):
    """No JSONL file for the date → row with zeros, every column present."""
    row = compute_rollup("2026-05-08", tmp_path)
    assert row["date"] == "2026-05-08"
    assert row["total_opportunities"] == 0
    assert row["hard_flagged_count"] == 0
    assert row["soft_flagged_count"] == 0
    assert row["unflagged_count"] == 0
    assert row["hard_flag_rate"] == "0.000000"
    for i in range(1, 14):
        assert row[f"rule_{i}_fire_count"] == 0
    # Medians on empty input are None (rendered as empty cell by writer).
    assert row["median_gross_margin_bps_all"] is None
    # Pool dist serializes empty Counter as `{}`.
    assert row["pool_type_distribution"] == "{}"
    # Every spec column is in the output dict.
    for col in ROLLUP_CSV_COLUMNS:
        assert col in row


def test_compute_rollup_single_clean_record(tmp_path: Path):
    rec = _make_record("opp-1", margin_bps=31)
    _write_jsonl([rec], "2026-05-08", tmp_path)
    row = compute_rollup("2026-05-08", tmp_path)
    assert row["total_opportunities"] == 1
    assert row["unflagged_count"] == 1
    assert row["hard_flagged_count"] == 0
    assert row["soft_flagged_count"] == 0
    assert row["median_gross_margin_bps_all"] == 31
    assert row["median_gross_margin_bps_unflagged"] == 31
    # No hard-flagged records → median is None.
    assert row["median_gross_margin_bps_hard_flagged"] is None
    pool_dist = json.loads(row["pool_type_distribution"])
    assert pool_dist == {"aerodrome_slipstream + uniswap_v3": 1}


def test_compute_rollup_multi_record_with_mixed_flags(tmp_path: Path):
    records = [
        _make_record("opp-1", margin_bps=30),
        _make_record("opp-2", margin_bps=50, hard=True, fired_rule_ids=(1,)),
        _make_record("opp-3", margin_bps=40, soft=True, fired_rule_ids=(6,)),
        _make_record("opp-4", margin_bps=35, hard=True, soft=True, fired_rule_ids=(1, 6)),
        _make_record("opp-5", margin_bps=20),
    ]
    _write_jsonl(records, "2026-05-08", tmp_path)
    row = compute_rollup("2026-05-08", tmp_path)
    assert row["total_opportunities"] == 5
    assert row["hard_flagged_count"] == 2  # opps 2 + 4
    # soft_flagged_count counts soft-flag-WITHOUT-hard-flag — only opp-3.
    assert row["soft_flagged_count"] == 1
    # Unflagged: opps 1 and 5.
    assert row["unflagged_count"] == 2
    # Per-rule counts: rule 1 fired in 2 opps, rule 6 in 2 opps, rest 0.
    assert row["rule_1_fire_count"] == 2
    assert row["rule_6_fire_count"] == 2
    assert row["rule_3_fire_count"] == 0
    # Medians on margin_gross_bps:
    #   all = [30, 50, 40, 35, 20] → 35
    assert row["median_gross_margin_bps_all"] == 35
    #   unflagged = [30, 20] → 25
    assert row["median_gross_margin_bps_unflagged"] == 25
    #   hard_flagged = [50, 35] → 42.5
    assert row["median_gross_margin_bps_hard_flagged"] == 42.5


def test_compute_rollup_counts_freshness_violations(tmp_path: Path):
    records = [
        _make_record("clean"),
        _make_record("stale-1", stale_tables=("contracts",)),
        _make_record("stale-2", stale_tables=("trap_events",)),
    ]
    _write_jsonl(records, "2026-05-08", tmp_path)
    row = compute_rollup("2026-05-08", tmp_path)
    assert row["degraded_opportunities_count"] == 2
    assert row["freshness_violations_count"] == 2


def test_write_rollup_csv_header_and_row(tmp_path: Path):
    rec = _make_record("opp-1")
    log_dir = tmp_path / "log"
    rollup_dir = tmp_path / "roll"
    _write_jsonl([rec], "2026-05-08", log_dir)
    out = write_rollup_csv("2026-05-08", log_dir, rollup_dir)
    assert out == rollup_dir / "2026-05-08.csv"
    text = out.read_text(encoding="utf-8")
    lines = text.strip().splitlines()
    assert len(lines) == 2
    reader = csv.DictReader(lines)
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-08"
    assert rows[0]["total_opportunities"] == "1"


def test_write_rollup_csv_idempotent(tmp_path: Path):
    """Acceptance criterion: rerunning produces byte-identical output."""
    rec1 = _make_record("opp-1", margin_bps=30)
    rec2 = _make_record("opp-2", margin_bps=50, hard=True, fired_rule_ids=(1,))
    log_dir = tmp_path / "log"
    rollup_dir = tmp_path / "roll"
    _write_jsonl([rec1, rec2], "2026-05-08", log_dir)

    out1 = write_rollup_csv("2026-05-08", log_dir, rollup_dir)
    bytes1 = out1.read_bytes()
    # Mutate output (simulate prior partial write) then rerun — must overwrite
    # cleanly, byte-identical to first run.
    out1.write_text("CORRUPTED\n")
    out2 = write_rollup_csv("2026-05-08", log_dir, rollup_dir)
    bytes2 = out2.read_bytes()
    assert bytes1 == bytes2


def test_write_rollup_csv_pool_type_distribution_sorted(tmp_path: Path):
    """Idempotency requires deterministic JSON serialization of the
    pool_type_distribution cell — sorted keys regardless of insertion order."""
    records = [
        _make_record("a", protocols=("uniswap_v3", "aerodrome_slipstream")),
        _make_record("b", protocols=("aerodrome_volatile", "aerodrome_stable")),
        _make_record("c", protocols=("uniswap_v3", "aerodrome_slipstream")),
    ]
    _write_jsonl(records, "2026-05-08", tmp_path)
    row = compute_rollup("2026-05-08", tmp_path)
    parsed = json.loads(row["pool_type_distribution"])
    # Both combos should appear, with counts.
    assert parsed == {
        "aerodrome_slipstream + uniswap_v3": 2,
        "aerodrome_stable + aerodrome_volatile": 1,
    }
    # The JSON cell itself must be sorted-keys.
    assert row["pool_type_distribution"] == json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def test_compute_rollup_tolerates_malformed_jsonl_lines(tmp_path: Path):
    """A truncated last line (e.g. from a hard kill) must be skipped, not abort
    the rollup."""
    log_dir = tmp_path
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "2026-05-08.jsonl"
    rec = _make_record("opp-1")
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.write('{"truncated": "no-closing-brace')  # bad line
    row = compute_rollup("2026-05-08", log_dir)
    assert row["total_opportunities"] == 1  # only the well-formed record counted
