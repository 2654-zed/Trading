"""Phase 1.4 — daily rollup CLI driver.

Invoked manually or via OS scheduler (Windows Task Scheduler / cron) to produce
the day's CSV rollup from the JSONL log. Runs at 00:00 UTC for the prior day
in production, but can be run for any past date (idempotent — rerunning produces
byte-identical output).

Usage:
    python -m layer3_trading_exp.scripts.run_daily_rollup --date 2026-05-08
    python -m layer3_trading_exp.scripts.run_daily_rollup --yesterday
    python -m layer3_trading_exp.scripts.run_daily_rollup --backfill 2026-05-01:2026-05-08
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

# Force UTF-8 stdout for the same reason enumerate_live does.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..config import DEFAULT
from ..daily_rollup import write_rollup_csv


def _parse_date(value: str) -> str:
    # Validate format only — parse + reformat to ensure ISO YYYY-MM-DD.
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}: {e}")


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _date_range(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        raise SystemExit(f"backfill end ({end}) before start ({start})")
    out = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y-%m-%d"))
        d = d + timedelta(days=1)
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", type=_parse_date, help="single UTC date YYYY-MM-DD")
    g.add_argument("--yesterday", action="store_true", help="UTC date one day before now")
    g.add_argument("--backfill", help="inclusive range START:END (e.g. 2026-05-01:2026-05-08)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = DEFAULT
    if args.date:
        dates = [args.date]
    elif args.yesterday:
        dates = [_yesterday_utc()]
    else:
        start, _, end = args.backfill.partition(":")
        if not end:
            raise SystemExit(f"--backfill expects START:END, got {args.backfill!r}")
        dates = _date_range(_parse_date(start), _parse_date(end))

    print(f"log_dir:    {cfg.log_dir}")
    print(f"rollup_dir: {cfg.rollup_dir}")
    print(f"dates:      {dates}")
    print()

    for date in dates:
        out = write_rollup_csv(date, cfg.log_dir, cfg.rollup_dir)
        size = out.stat().st_size
        print(f"  {date}: wrote {out} ({size} bytes)")


if __name__ == "__main__":
    main()
