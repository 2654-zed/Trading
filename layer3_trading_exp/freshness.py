"""Freshness checks against the local Layer 3 SQLite copy.

Mirrors the table→column mapping in Layer 3's web/api_v1.py::_TABLE_FRESHNESS_COLUMN
and §3.4 of layer3_consumable_intelligence.md. If Layer 3's mapping diverges from this
one, the experiment's freshness reading becomes wrong — keep them in sync.

Per Addendum A #2, the envelope key observed on the Layer 3 HTTP API is meta.computed_at;
this module works directly against the local SQLite copy rather than the HTTP envelope.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


TABLE_FRESHNESS_COLUMN: dict[str, str] = {
    "contracts": "last_updated",
    "deployers": "last_seen",
    "bytecode_families": "last_updated",
    "camouflage_metrics": "date",
    "daily_metrics": "date",
    "trust_amplification": "last_updated",
    "trap_events": "timestamp",
    "alerts": "timestamp",
}


# Subset of the mapping that the Phase 1.3 filter rules actually depend on.
# These are continuous-cadence tables; the 30-minute staleness threshold from
# spec invariant #8 applies meaningfully to this subset. daily_metrics and
# camouflage_metrics are nightly-cadence and excluded by construction; alerts
# is a surface table not touched by any filter rule.
FILTER_CRITICAL_TABLES: frozenset[str] = frozenset({
    "contracts",
    "deployers",
    "trap_events",
    "trust_amplification",
    "bytecode_families",
})


# Phase 2 sub-phase 2.6 (D-009, D-012) — per-table freshness thresholds.
# Resolves UNK-005. Phase 1 ran with a single 30-min threshold across all
# tables, which was wrong for any table whose natural cadence exceeded
# 30 min — every filter eval was marked degraded, making H2 structurally
# vacuous (100% of EXP-001 records had `layer3_stale=True`).
#
# Thresholds chosen per L3's actual table cadences observed in production:
#   contracts / deployers     — minutes-cadence updates from L3's
#                                infrastructure-registry pipeline
#   bytecode_families         — multi-hour family classifier batch
#   trap_events               — daily trap-detection job
#   trust_amplification       — slowest table; CRITICAL flags stable for days
#
# Tables not in this map fall back to DEFAULT_FRESHNESS_THRESHOLD_SECONDS.
PER_TABLE_THRESHOLDS_SECONDS: dict[str, int] = {
    "contracts":           1 * 60 * 60,   # 1 hour
    "deployers":           1 * 60 * 60,   # 1 hour
    "bytecode_families":   6 * 60 * 60,   # 6 hours
    "trap_events":        24 * 60 * 60,   # 24 hours
    "trust_amplification": 36 * 60 * 60,  # 36 hours
}

# Default threshold for any filter-critical table not explicitly mapped.
# Matches the bytecode_families cadence as a conservative floor.
DEFAULT_FRESHNESS_THRESHOLD_SECONDS: int = 6 * 60 * 60   # 6 hours


def threshold_for_table(table: str) -> int:
    """Per-table threshold lookup with default fallback. Used by Phase 2
    code paths that need a single source of truth for freshness windows."""
    return PER_TABLE_THRESHOLDS_SECONDS.get(table, DEFAULT_FRESHNESS_THRESHOLD_SECONDS)


@dataclass(frozen=True)
class TableFreshness:
    table: str
    latest_ts: Optional[str]
    age_seconds: Optional[float]
    stale: bool


def _parse_ts(ts: str) -> Optional[datetime]:
    if ts is None:
        return None
    raw = ts.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_table_timestamp(conn: sqlite3.Connection, table: str) -> Optional[str]:
    col = TABLE_FRESHNESS_COLUMN.get(table)
    if col is None:
        raise KeyError(f"Unknown freshness table: {table}")
    try:
        row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def check_table(
    conn: sqlite3.Connection,
    table: str,
    threshold_seconds: int,
    now: Optional[datetime] = None,
) -> TableFreshness:
    now = now or datetime.now(timezone.utc)
    latest = get_table_timestamp(conn, table)
    if latest is None:
        return TableFreshness(table=table, latest_ts=None, age_seconds=None, stale=True)
    parsed = _parse_ts(latest)
    if parsed is None:
        return TableFreshness(table=table, latest_ts=latest, age_seconds=None, stale=True)
    age = (now - parsed).total_seconds()
    return TableFreshness(
        table=table,
        latest_ts=latest,
        age_seconds=age,
        stale=age > threshold_seconds,
    )


def check_all(
    conn: sqlite3.Connection,
    threshold_seconds: int,
    now: Optional[datetime] = None,
) -> dict[str, TableFreshness]:
    return {t: check_table(conn, t, threshold_seconds, now=now) for t in TABLE_FRESHNESS_COLUMN}


def any_stale(
    conn: sqlite3.Connection,
    threshold_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    return any(tf.stale for tf in check_all(conn, threshold_seconds, now=now).values())


def any_filter_critical_stale(
    conn: sqlite3.Connection,
    threshold_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    """Spec invariant #8 check: is any filter-rule-critical table older than the threshold?"""
    return any(
        check_table(conn, t, threshold_seconds, now=now).stale
        for t in FILTER_CRITICAL_TABLES
    )


def check_filter_critical_per_table(
    conn: sqlite3.Connection,
    now: Optional[datetime] = None,
    *,
    thresholds: Optional[dict[str, int]] = None,
    default_threshold: int = DEFAULT_FRESHNESS_THRESHOLD_SECONDS,
) -> dict[str, TableFreshness]:
    """Phase 2 sub-phase 2.6 — per-table-threshold freshness check.

    For each filter-critical table, looks up its per-table threshold from
    `thresholds` (defaulting to `PER_TABLE_THRESHOLDS_SECONDS`) and runs
    `check_table` against that threshold. Returns a dict of TableFreshness
    keyed by table name.

    This is the source of truth for Phase 2 H2 calibration: a single table
    being stale doesn't mark the whole evaluation degraded; the caller
    inspects per-table results.
    """
    th_map = dict(thresholds or PER_TABLE_THRESHOLDS_SECONDS)
    out: dict[str, TableFreshness] = {}
    for table in FILTER_CRITICAL_TABLES:
        thresh = th_map.get(table, default_threshold)
        out[table] = check_table(conn, table, thresh, now=now)
    return out


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)
