"""Shared JSONL loader for the analysis package.

Reads records from `{log_dir}/{YYYY-MM-DD}.jsonl` files across a date range,
returns plain dict records (one per line). All other analysis modules consume
this output — they don't touch the filesystem directly.

Supports a `unique_only` mode that dedups by `(pool_x, pool_y, borrowed_token)`
within each UTC day, returning one record per (key, day). Useful for H1: the
spec talks about "opportunities" but the dataset has many emissions per
underlying arb (3-5x duplicate WS heads + persistence across blocks). Both
counts are research-relevant — the loader gives Jason both options.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path


def _date_range(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        raise ValueError(f"end date {end} before start {start}")
    out: list[str] = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y-%m-%d"))
        d = d + timedelta(days=1)
    return out


def _record_date(record: dict) -> str | None:
    ts = record.get("timestamp")
    if isinstance(ts, str) and len(ts) >= 10:
        return ts[:10]
    return None


def _record_key(record: dict) -> tuple[str, str, str]:
    """Stable identity for an arbitrage opportunity, ignoring leg order.

    Used for unique-day dedup. We pair the two pool addresses as a frozenset-
    serialized string so (X, Y) and (Y, X) produce the same key. Borrowed
    token is the third element since A->X->B->Y->A and B->X->A->Y->B are
    distinct opportunities even on the same pool pair.
    """
    pools = (record.get("path") or {}).get("pools") or []
    if len(pools) < 2:
        return ("", "", record.get("opportunity_id", ""))
    pool_pair = "|".join(sorted([str(pools[0]).lower(), str(pools[1]).lower()]))
    # The borrowed token is the first element of `path.tokens` by construction
    # in build_log_record (token_in of leg 0). Defensive fallback if absent.
    tokens = (record.get("path") or {}).get("tokens") or []
    borrowed = str(tokens[0]).lower() if tokens else ""
    return (pool_pair, borrowed, "")


def load_records(
    start_date: str,
    end_date: str,
    log_dir: Path,
) -> list[dict]:
    """Read JSONL records for every UTC date in `[start_date, end_date]`.

    Missing date files are silently skipped (a 30-day run might have days with
    zero opportunities and therefore no JSONL file). Malformed JSON lines are
    also skipped to tolerate truncated final records from hard kills.
    """
    records: list[dict] = []
    for d in _date_range(start_date, end_date):
        path = log_dir / f"{d}.jsonl"
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def load_records_filtered(
    start_date: str,
    end_date: str,
    log_dir: Path,
    *,
    hard_flagged: bool | None = None,
    soft_flagged: bool | None = None,
    unflagged_only: bool = False,
) -> list[dict]:
    """Convenience wrapper around `load_records` with filter predicates.

    `hard_flagged=True` keeps only hard-flagged records; `False` excludes them.
    `None` (default) does not filter on this dimension. Same for `soft_flagged`.
    `unflagged_only=True` overrides both flags and keeps only records where
    BOTH hard_flagged and soft_flagged are False.
    """
    records = load_records(start_date, end_date, log_dir)
    if unflagged_only:
        return [r for r in records if not r.get("hard_flagged") and not r.get("soft_flagged")]
    if hard_flagged is True:
        records = [r for r in records if r.get("hard_flagged")]
    elif hard_flagged is False:
        records = [r for r in records if not r.get("hard_flagged")]
    if soft_flagged is True:
        records = [r for r in records if r.get("soft_flagged")]
    elif soft_flagged is False:
        records = [r for r in records if not r.get("soft_flagged")]
    return records


def dedup_unique_per_day(records: list[dict]) -> list[dict]:
    """Return one record per (opportunity_key, UTC date) pair.

    Within each day, the FIRST record observed for a given opportunity key is
    kept; subsequent emissions on the same arb are dropped. Cross-day records
    are independent — the same arb appearing on two different days produces
    two records. This matches the spec's H1 framing of "opportunities per day".
    """
    seen: set[tuple[str, tuple[str, str, str]]] = set()
    out: list[dict] = []
    for r in records:
        d = _record_date(r)
        if d is None:
            continue
        key = (d, _record_key(r))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def group_by_day(records: list[dict]) -> dict[str, list[dict]]:
    """Bucket records by UTC date string."""
    buckets: dict[str, list[dict]] = {}
    for r in records:
        d = _record_date(r)
        if d is None:
            continue
        buckets.setdefault(d, []).append(r)
    return buckets


__all__ = [
    "load_records",
    "load_records_filtered",
    "dedup_unique_per_day",
    "group_by_day",
]
