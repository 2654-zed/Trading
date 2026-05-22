"""Phase 1.4 — daily rollup CSV generator.

Reads a single day's JSONL log (`{log_dir}/{YYYY-MM-DD}.jsonl`), aggregates
the spec §Phase 1.4 columns, writes the result to
`{rollup_dir}/{YYYY-MM-DD}.csv`. Idempotent: rerunning produces byte-identical
output.

Idempotency requirements:
  - Column order fixed in `schema.ROLLUP_CSV_COLUMNS`.
  - Pool-type-distribution serialized via `json.dumps(..., sort_keys=True)`.
  - Float fields formatted with a consistent precision specifier.
  - Counter sums are insertion-order-independent (we use Python's
    `collections.Counter`, sorted at serialization time).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from .schema import ROLLUP_CSV_COLUMNS


def _read_records(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    records: list[dict] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Tolerate one bad line — could be a truncated final record from
                # a hard kill. Skip-and-count rather than abort the whole rollup.
                continue
    return records


def _format_float(value: float) -> str:
    """Stable string for floats so byte-identical output across runs.

    Six decimal places is enough for fractional rates (`hard_flag_rate`) and
    median bps (which are integers anyway in the input). Using `repr()` would
    give platform-dependent formatting; using `f"{x:.6f}"` is portable.
    """
    return f"{value:.6f}"


def _median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def compute_rollup(date: str, log_dir: Path) -> dict:
    """Compute the rollup row dict for one UTC date.

    `date` must be ISO YYYY-MM-DD. Reads `log_dir/{date}.jsonl` (returns
    zero-counts row if absent or empty). The returned dict has every column
    in `ROLLUP_CSV_COLUMNS`; `write_rollup_csv` consumes it directly.
    """
    log_path = log_dir / f"{date}.jsonl"
    records = _read_records(log_path)
    total = len(records)

    hard = sum(1 for r in records if r.get("hard_flagged"))
    soft_only = sum(
        1 for r in records
        if r.get("soft_flagged") and not r.get("hard_flagged")
    )
    unflagged = sum(
        1 for r in records
        if not r.get("hard_flagged") and not r.get("soft_flagged")
    )
    degraded = sum(1 for r in records if r.get("layer3_stale"))
    freshness_violations = sum(
        1 for r in records
        if (r.get("layer3_freshness", {}) or {}).get("stale_tables")
    )

    rule_fire_counts = Counter()
    for r in records:
        for key, info in (r.get("filter_results") or {}).items():
            if not isinstance(info, dict) or not info.get("fired"):
                continue
            try:
                rule_id = int(key.split("_")[1])
            except (IndexError, ValueError):
                continue
            if 1 <= rule_id <= 13:
                rule_fire_counts[rule_id] += 1

    margins_all = [r.get("margin_gross_bps", 0) for r in records]
    margins_unflagged = [
        r.get("margin_gross_bps", 0) for r in records
        if not r.get("hard_flagged") and not r.get("soft_flagged")
    ]
    margins_hard = [
        r.get("margin_gross_bps", 0) for r in records
        if r.get("hard_flagged")
    ]

    pool_type_counts: Counter = Counter()
    for r in records:
        protos = r.get("pool_protocols")
        if isinstance(protos, list) and protos:
            key = " + ".join(sorted(p for p in protos if isinstance(p, str)))
            pool_type_counts[key] += 1

    hard_rate = (hard / total) if total else 0.0
    soft_rate = (soft_only / total) if total else 0.0

    row: dict = {
        "date": date,
        "total_opportunities": total,
        "hard_flagged_count": hard,
        "soft_flagged_count": soft_only,
        "unflagged_count": unflagged,
        "hard_flag_rate": _format_float(hard_rate),
        "soft_flag_rate": _format_float(soft_rate),
    }
    for i in range(1, 14):
        row[f"rule_{i}_fire_count"] = rule_fire_counts.get(i, 0)

    row["median_gross_margin_bps_all"] = _median_or_none(margins_all)
    row["median_gross_margin_bps_unflagged"] = _median_or_none(margins_unflagged)
    row["median_gross_margin_bps_hard_flagged"] = _median_or_none(margins_hard)

    # Sorted-keys ensures byte-identical re-serialization.
    row["pool_type_distribution"] = json.dumps(
        dict(pool_type_counts), sort_keys=True, separators=(",", ":"),
    )
    row["degraded_opportunities_count"] = degraded
    row["freshness_violations_count"] = freshness_violations
    return row


def write_rollup_csv(date: str, log_dir: Path, rollup_dir: Path) -> Path:
    """Compute and write the rollup CSV for `date`. Returns the output path.

    The file always has exactly two lines: a header row and one data row.
    Existing files at the destination are overwritten — the rollup is
    deterministic, so an overwrite is safe (idempotent semantics).
    """
    rollup_dir.mkdir(parents=True, exist_ok=True)
    row = compute_rollup(date, log_dir)
    out_path = rollup_dir / f"{date}.csv"
    # Newline='' so csv module controls line endings (idempotency across OSes).
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ROLLUP_CSV_COLUMNS))
        writer.writeheader()
        # Replace None medians with empty cells (csv conventional null).
        printable = {
            k: ("" if v is None else v)
            for k, v in row.items()
        }
        writer.writerow(printable)
    return out_path


__all__ = ["compute_rollup", "write_rollup_csv"]
