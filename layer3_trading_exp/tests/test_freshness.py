"""Tests for freshness.py — the 30-minute staleness threshold per spec invariant #8."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from layer3_trading_exp import freshness


def _conn_with(table: str, col: str, value):
    conn = sqlite3.connect(":memory:")
    conn.execute(f"CREATE TABLE {table} ({col} TEXT)")
    conn.execute(f"INSERT INTO {table} ({col}) VALUES (?)", (value,))
    return conn


def test_parse_iso_with_z_suffix():
    dt = freshness._parse_ts("2026-04-21T12:00:00Z")
    assert dt == datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def test_parse_date_only():
    dt = freshness._parse_ts("2026-04-21")
    assert dt.tzinfo is not None
    assert dt.date() == datetime(2026, 4, 21).date()


def test_parse_invalid_returns_none():
    assert freshness._parse_ts("not a timestamp") is None
    assert freshness._parse_ts("") is None


def test_fresh_table_is_not_stale():
    now = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(minutes=5)).isoformat()
    conn = _conn_with("contracts", "last_updated", fresh)
    result = freshness.check_table(conn, "contracts", threshold_seconds=30 * 60, now=now)
    assert result.stale is False
    assert result.age_seconds is not None
    assert result.age_seconds < 30 * 60


def test_stale_table_exceeds_threshold():
    now = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    stale = (now - timedelta(minutes=45)).isoformat()
    conn = _conn_with("contracts", "last_updated", stale)
    result = freshness.check_table(conn, "contracts", threshold_seconds=30 * 60, now=now)
    assert result.stale is True
    assert result.age_seconds > 30 * 60


def test_exactly_at_threshold_is_not_stale():
    now = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
    boundary = (now - timedelta(seconds=30 * 60)).isoformat()
    conn = _conn_with("contracts", "last_updated", boundary)
    result = freshness.check_table(conn, "contracts", threshold_seconds=30 * 60, now=now)
    assert result.stale is False


def test_missing_table_marked_stale():
    conn = sqlite3.connect(":memory:")
    result = freshness.check_table(conn, "contracts", threshold_seconds=30 * 60)
    assert result.stale is True
    assert result.latest_ts is None


def test_unknown_table_raises():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(KeyError):
        freshness.get_table_timestamp(conn, "not_in_mapping")


def test_filter_critical_tables_fresh_against_seeded_db(seeded_db, now_utc):
    conn = freshness.open_readonly(seeded_db)
    try:
        assert freshness.any_filter_critical_stale(
            conn, threshold_seconds=30 * 60, now=now_utc
        ) is False
    finally:
        conn.close()


def test_daily_tables_are_stale_under_30min_threshold(seeded_db, now_utc):
    # camouflage_metrics / daily_metrics have a date-only cadence; at noon on the
    # seeded date they are ~12h old, which exceeds the 30-min threshold. This is
    # the expected behavior and the reason FILTER_CRITICAL_TABLES excludes them.
    conn = freshness.open_readonly(seeded_db)
    try:
        cm = freshness.check_table(conn, "camouflage_metrics", threshold_seconds=30 * 60, now=now_utc)
        dm = freshness.check_table(conn, "daily_metrics", threshold_seconds=30 * 60, now=now_utc)
        assert cm.stale is True
        assert dm.stale is True
    finally:
        conn.close()


def test_filter_critical_stale_with_advanced_clock(seeded_db, now_utc):
    conn = freshness.open_readonly(seeded_db)
    try:
        later = now_utc + timedelta(hours=3)
        assert freshness.any_filter_critical_stale(
            conn, threshold_seconds=30 * 60, now=later
        ) is True
    finally:
        conn.close()


def test_filter_critical_subset_is_documented():
    expected = {"contracts", "deployers", "trap_events",
                "trust_amplification", "bytecode_families"}
    assert set(freshness.FILTER_CRITICAL_TABLES) == expected
    assert freshness.FILTER_CRITICAL_TABLES.issubset(freshness.TABLE_FRESHNESS_COLUMN.keys())


def test_table_mapping_matches_spec_section_3_4():
    expected = {
        "contracts", "deployers", "bytecode_families", "camouflage_metrics",
        "daily_metrics", "trust_amplification", "trap_events", "alerts",
    }
    assert set(freshness.TABLE_FRESHNESS_COLUMN.keys()) == expected


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.6 (D-009, D-012): per-table freshness thresholds.
# ----------------------------------------------------------------------------


def test_per_table_thresholds_match_spec():
    """Sub-phase 2.6 table — see PHASE_2_CROSS_CHAIN_SPEC.md and D-012."""
    assert freshness.PER_TABLE_THRESHOLDS_SECONDS["contracts"] == 1 * 3600
    assert freshness.PER_TABLE_THRESHOLDS_SECONDS["deployers"] == 1 * 3600
    assert freshness.PER_TABLE_THRESHOLDS_SECONDS["bytecode_families"] == 6 * 3600
    assert freshness.PER_TABLE_THRESHOLDS_SECONDS["trap_events"] == 24 * 3600
    assert freshness.PER_TABLE_THRESHOLDS_SECONDS["trust_amplification"] == 36 * 3600
    # Default for any table not explicitly mapped.
    assert freshness.DEFAULT_FRESHNESS_THRESHOLD_SECONDS == 6 * 3600


def test_threshold_for_table_returns_per_table_value():
    assert freshness.threshold_for_table("contracts") == 3600
    assert freshness.threshold_for_table("trust_amplification") == 36 * 3600
    # Unknown table falls back to default.
    assert freshness.threshold_for_table("camouflage_metrics") == \
           freshness.DEFAULT_FRESHNESS_THRESHOLD_SECONDS


def _conn_with_multiple(now: datetime, rows: dict[str, tuple[str, datetime]]):
    """Build a connection with one row per (table, column, ts) entry."""
    conn = sqlite3.connect(":memory:")
    for table, (col, ts) in rows.items():
        conn.execute(f"CREATE TABLE {table} ({col} TEXT)")
        conn.execute(f"INSERT INTO {table} ({col}) VALUES (?)", (ts.isoformat(),))
    return conn


def test_check_filter_critical_per_table_distinguishes_freshness_per_threshold():
    """The key Phase 2 calibration: a slow table (trust_amplification at
    20h) is NOT stale under its 36h threshold, while a fast table
    (contracts at 2h) IS stale under its 1h threshold."""
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    conn = _conn_with_multiple(now, {
        "contracts":           ("last_updated", now - timedelta(hours=2)),
        "deployers":           ("last_seen", now - timedelta(minutes=30)),
        "bytecode_families":   ("last_updated", now - timedelta(hours=4)),
        "trap_events":         ("timestamp", now - timedelta(hours=12)),
        "trust_amplification": ("last_updated", now - timedelta(hours=20)),
    })
    result = freshness.check_filter_critical_per_table(conn, now=now)
    assert result["contracts"].stale            # 2h > 1h
    assert not result["deployers"].stale        # 30min < 1h
    assert not result["bytecode_families"].stale  # 4h < 6h
    assert not result["trap_events"].stale      # 12h < 24h
    assert not result["trust_amplification"].stale  # 20h < 36h


def test_check_filter_critical_per_table_old_trust_amplification_flagged():
    """Trust amplification at 40h IS stale even under the 36h threshold —
    if L3's pipeline is genuinely broken, freshness still flags it."""
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    conn = _conn_with_multiple(now, {
        "contracts":           ("last_updated", now - timedelta(minutes=10)),
        "deployers":           ("last_seen", now - timedelta(minutes=10)),
        "bytecode_families":   ("last_updated", now - timedelta(hours=1)),
        "trap_events":         ("timestamp", now - timedelta(hours=1)),
        "trust_amplification": ("last_updated", now - timedelta(hours=40)),
    })
    result = freshness.check_filter_critical_per_table(conn, now=now)
    assert result["trust_amplification"].stale
    # All others fresh.
    for t in ("contracts", "deployers", "bytecode_families", "trap_events"):
        assert not result[t].stale


def test_check_filter_critical_per_table_accepts_custom_threshold_overrides():
    """Tests + future re-calibration can override thresholds."""
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    conn = _conn_with_multiple(now, {
        "contracts":           ("last_updated", now - timedelta(minutes=10)),
        "deployers":           ("last_seen", now - timedelta(minutes=10)),
        "bytecode_families":   ("last_updated", now - timedelta(minutes=10)),
        "trap_events":         ("timestamp", now - timedelta(minutes=10)),
        "trust_amplification": ("last_updated", now - timedelta(minutes=10)),
    })
    # All within 1 minute — under default thresholds, all fresh.
    result = freshness.check_filter_critical_per_table(conn, now=now)
    for t in freshness.FILTER_CRITICAL_TABLES:
        assert not result[t].stale
    # Now override contracts to 5min threshold → contracts stale.
    result2 = freshness.check_filter_critical_per_table(
        conn, now=now,
        thresholds={"contracts": 5 * 60},  # 5 min
    )
    assert result2["contracts"].stale
