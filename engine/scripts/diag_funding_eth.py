"""Fit diagnostic: does Hyperliquid ETH perp FUNDING lead forward ETH returns?

The best idea from the "more data sources" memo: perp funding = real-money
leveraged positioning. Test whether funding (level / extremeness / prior-avg)
PREDICTS forward ETH return, out-of-sample, on NON-OVERLAPPING windows (so
overlapping-return autocorrelation doesn't fake significance), with a beta
control (does funding merely TRACK recent price rather than lead it?).

$0 — Hyperliquid public API only. Read-only, no trading.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from bisect import bisect_right

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

API = "https://api.hyperliquid.xyz/info"
DAYS = 180


def post(body, timeout=40):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "diag/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pts)
    if n < 8:
        return None, n
    mx = sum(p[0] for p in pts)/n; my = sum(p[1] for p in pts)/n
    cov = sum((p[0]-mx)*(p[1]-my) for p in pts)
    vx = sum((p[0]-mx)**2 for p in pts); vy = sum((p[1]-my)**2 for p in pts)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov/math.sqrt(vx*vy), n


def pull_funding(coin, start_ms, end_ms):
    out, cur = [], start_ms
    while cur < end_ms:
        batch = post({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": end_ms})
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1]["time"] + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 100:
            break
    # dedup by time
    seen, dd = set(), []
    for r in out:
        if r["time"] not in seen:
            seen.add(r["time"]); dd.append(r)
    return sorted(dd, key=lambda r: r["time"])


def pull_candles(coin, start_ms, end_ms):
    out, cur = [], start_ms
    while cur < end_ms:
        batch = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h",
                      "startTime": cur, "endTime": end_ms}})
        if not batch:
            break
        out.extend(batch)
        nxt = batch[-1]["t"] + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(batch) < 100:
            break
    seen, dd = set(), []
    for c in out:
        if c["t"] not in seen:
            seen.add(c["t"]); dd.append(c)
    return sorted(dd, key=lambda c: c["t"])


def main():
    now = int(time.time()*1000)
    start = now - DAYS*86400*1000
    fund = pull_funding("ETH", start, now)
    cand = pull_candles("ETH", start, now)
    print(f"funding points: {len(fund)}   candles: {len(cand)}   (~{DAYS}d hourly)")
    if len(fund) < 200 or len(cand) < 200:
        print("insufficient history"); return 1

    # price lookup: sorted (t, close)
    ptimes = [c["t"] for c in cand]
    pclose = [float(c["c"]) for c in cand]

    def price_at(t):
        i = bisect_right(ptimes, t) - 1
        if i < 0:
            return None
        return pclose[i]

    # funding series aligned to time
    ftimes = [r["time"] for r in fund]
    frates = [float(r["fundingRate"]) for r in fund]

    def funding_at_idx(t):
        i = bisect_right(ftimes, t) - 1
        return i if i >= 0 else None

    H = 3600*1000
    for hours in (24, 48):
        h = hours*H
        # NON-OVERLAPPING samples stepping by h.
        f_level, f_prioravg, f_z, fwd, prior = [], [], [], [], []
        t = start + 7*86400*1000  # leave room for trailing window
        while t + h <= now:
            fi = funding_at_idx(t)
            p0 = price_at(t); p1 = price_at(t+h); pprev = price_at(t-h)
            if fi is not None and p0 and p1 and p0 > 0:
                lvl = frates[fi]
                lo = max(0, fi-168)
                window = frates[lo:fi+1]
                mean = sum(window)/len(window)
                var = sum((x-mean)**2 for x in window)/len(window)
                std = math.sqrt(var) if var > 0 else 0
                prioravg = sum(frates[max(0, fi-hours):fi+1])/max(1, len(frates[max(0, fi-hours):fi+1]))
                f_level.append(lvl)
                f_prioravg.append(prioravg)
                f_z.append((lvl-mean)/std if std > 0 else 0.0)
                fwd.append((p1-p0)/p0)
                prior.append((p0-pprev)/pprev if pprev and pprev > 0 else None)
            t += h

        n = len(fwd)
        split = int(0.7*n)
        print(f"\n{'='*70}\nETH FUNDING -> forward {hours}h return (non-overlapping, n={n})\n{'='*70}")
        print(f"{'feature':14s} {'IS corr (n)':>15s} {'OOS corr (n)':>15s} {'BETA vs prior':>14s}")
        for name, series in (("funding_level", f_level), ("funding_prioravg", f_prioravg),
                             ("funding_zscore", f_z)):
            c_is, n_is = pearson(series[:split], fwd[:split])
            c_oos, n_oos = pearson(series[split:], fwd[split:])
            c_beta, _ = pearson(series, prior)
            fmt = lambda c, nn: (f"{c:+.3f}(n={nn})" if c is not None else f"n/a(n={nn})")
            print(f"{name:14s} {fmt(c_is,n_is):>15s} {fmt(c_oos,n_oos):>15s} "
                  f"{(f'{c_beta:+.3f}' if c_beta is not None else 'n/a'):>14s}")

    # current snapshot for context
    m = post({"type": "metaAndAssetCtxs"})
    uni = m[0]["universe"]; ctxs = m[1]
    i = next((k for k, u in enumerate(uni) if u["name"] == "ETH"), None)
    print(f"\ncurrent ETH: OI={float(ctxs[i]['openInterest']):,.0f} ETH  "
          f"funding={float(ctxs[i]['funding']):.6f}/hr  px=${ctxs[i]['oraclePx']}")
    print("\nRead: want a FORWARD corr that holds IS->OOS, same sign, and exceeds")
    print("|BETA vs prior| (else funding just reflects recent price, not leads it).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
