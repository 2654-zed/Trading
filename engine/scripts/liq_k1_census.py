#!/usr/bin/env python3
"""Phase-0 K1 power census for the liquidation-cascade FEAS.

Counts independent stress/cascade EPISODES in the BTCUSDT liquidation tape over
whatever window has been pulled into engine/data/tardis/ — to answer: does the
(date-limited) Tardis key reach enough independent events for an event-blocked
OOS test, or is the study dead on power? See docs/FEAS_liquidation-cascade.md (K1).

Stdlib only. Liquidation USD is a proxy for forced-flow stress (throttled feed =
floor, not truth — fine for COUNTING episodes). One observation = one episode.
"""
from __future__ import annotations
import collections
import csv
import datetime as dt
import glob
import gzip
import statistics
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "tardis"
PATTERN = "binance-futures_liquidations_*_BTCUSDT.csv.gz"


def load_hourly():
    hourly = collections.Counter()
    h_buy, h_sell = collections.Counter(), collections.Counter()
    daily = collections.Counter()
    rows = 0
    for fp in sorted(glob.glob(str(DATA / PATTERN))):
        with gzip.open(fp, "rt") as f:
            for row in csv.DictReader(f):
                try:
                    ts = int(row["timestamp"])              # microseconds
                    usd = float(row["price"]) * float(row["amount"])
                except (ValueError, KeyError, TypeError):
                    continue
                rows += 1
                t = dt.datetime.fromtimestamp(ts / 1e6, dt.timezone.utc)
                hk = t.strftime("%Y-%m-%d %H")
                daily[t.strftime("%Y-%m-%d")] += usd
                hourly[hk] += usd
                (h_buy if row.get("side") == "buy" else h_sell)[hk] += usd
    return hourly, h_buy, h_sell, daily, rows


def episodes(hourly, threshold, cooldown_h):
    """Independent episode = group of stress hours (>threshold) separated by gaps
    longer than cooldown_h. Returns list of (start, end, peak_usd, total_usd)."""
    stress = sorted(dt.datetime.strptime(h, "%Y-%m-%d %H").replace(tzinfo=dt.timezone.utc)
                    for h in hourly if hourly[h] > threshold)
    eps = []
    for t in stress:
        usd = hourly[t.strftime("%Y-%m-%d %H")]
        if eps and (t - eps[-1]["end"]).total_seconds() / 3600 <= cooldown_h:
            e = eps[-1]; e["end"] = t; e["peak"] = max(e["peak"], usd); e["total"] += usd
        else:
            eps.append({"start": t, "end": t, "peak": usd, "total": usd})
    return eps


def main():
    hourly, h_buy, h_sell, daily, rows = load_hourly()
    days = sorted(daily)
    dvals = [daily[d] for d in days]
    hvals = sorted(hourly.values())

    def pct(p):
        return hvals[min(len(hvals) - 1, int(len(hvals) * p))]

    print(f"=== K1 census — BTCUSDT liquidations, {len(days)} days "
          f"({days[0]}..{days[-1]}), {rows:,} prints ===")
    print(f"total liquidation USD: ${sum(dvals):,.0f}")
    print(f"daily liq USD: median=${statistics.median(dvals):,.0f}  "
          f"mean=${statistics.mean(dvals):,.0f}  max=${max(dvals):,.0f}")
    med = statistics.median(dvals)
    print("\n-- top 12 cascade days (and x-median) --")
    for d in sorted(daily, key=daily.get, reverse=True)[:12]:
        print(f"  {d}  ${daily[d]:>14,.0f}  ({daily[d]/med:5.1f}x median)")
    print(f"\n-- hourly liq USD distribution --")
    print(f"  p50=${pct(.50):,.0f}  p90=${pct(.90):,.0f}  p95=${pct(.95):,.0f}  "
          f"p99=${pct(.99):,.0f}  max=${max(hvals):,.0f}")

    print("\n-- independent EPISODE counts (the power census) --")
    holdout_start = dt.datetime.strptime(days[int(len(days) * 0.8)], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    for label, p in [("moderate (>p95)", .95), ("major (>p99)", .99)]:
        thr = pct(p)
        for cd in (12, 24):
            eps = episodes(hourly, thr, cd)
            in_holdout = sum(1 for e in eps if e["start"] >= holdout_start)
            print(f"  {label:<16} cooldown {cd}h: {len(eps):>3} independent episodes  "
                  f"({in_holdout} in last-20% holdout)")

    print(f"\n  (holdout = days from {days[int(len(days)*0.8)]} onward)")
    print("\n-- biggest episodes (major>p99, 24h cooldown) --")
    for e in sorted(episodes(hourly, pct(.99), 24), key=lambda e: e["peak"], reverse=True)[:8]:
        span = (e["end"] - e["start"]).total_seconds() / 3600
        print(f"  {e['start']:%Y-%m-%d %Hh} +{span:4.0f}h  peak=${e['peak']:>12,.0f}/h  total=${e['total']:>14,.0f}")


if __name__ == "__main__":
    main()
