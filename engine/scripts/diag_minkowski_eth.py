"""Steelman backtest of the financiers' "8D Minkowski predictive engine."

Their core code reduces to a Minkowski-signature interval over 8 feature
deltas:  M = -(d1^2) + (d2^2) + ... + (d8^2)   [signature (-,+,+,+,+,+,+,+)]

The MOST legitimate backtestable version: build an 8-dim ETH market-STATE
vector, z-score each feature vs its trailing baseline (so d_k = deviation
from "normal" in std units), compute their exact Minkowski interval, and
test whether it predicts forward ETH returns OUT-OF-SAMPLE.

Controls (the honest part):
  - EUCLIDEAN distance (all +): does the Minkowski sign flip add anything
    over plain multivariate deviation?
  - MOMENTUM alone (the timelike feature z1): is any signal just momentum?

Features (from Hyperliquid ETH hourly candles + funding; the rest of the
engine's inputs — mempool, latency, gas, simulation — are LIVE-EXECUTION
only and cannot be backtested, which is itself the finding):
  1 mom_24h (timelike)  2 mom_7d  3 realized_vol  4 accel(d mom)
  5 volume_z            6 hi_lo_range  7 funding  8 funding_change

$0 — Hyperliquid public API. Read-only, no trading.
"""

from __future__ import annotations

import json, math, sys, time, urllib.request
from bisect import bisect_right

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

API = "https://api.hyperliquid.xyz/info"
DAYS = 180
H = 3600_000


def post(body, timeout=40):
    req = urllib.request.Request(API, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "diag/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None
           and not math.isnan(x) and not math.isnan(y)]
    n = len(pts)
    if n < 8:
        return None, n
    mx = sum(p[0] for p in pts)/n; my = sum(p[1] for p in pts)/n
    cov = sum((p[0]-mx)*(p[1]-my) for p in pts)
    vx = sum((p[0]-mx)**2 for p in pts); vy = sum((p[1]-my)**2 for p in pts)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov/math.sqrt(vx*vy), n


def paginate(kind, coin, start, end):
    out, cur = [], start
    while cur < end:
        if kind == "f":
            b = post({"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": end})
            key = "time"
        else:
            b = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h",
                      "startTime": cur, "endTime": end}})
            key = "t"
        if not b:
            break
        out.extend(b)
        nxt = b[-1][key] + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(b) < 100:
            break
    seen, dd = set(), []
    for r in out:
        if r[key] not in seen:
            seen.add(r[key]); dd.append(r)
    return sorted(dd, key=lambda r: r[key])


def zscore(series, i, lb=168):
    lo = max(0, i-lb)
    w = [x for x in series[lo:i+1] if x is not None and not math.isnan(x)]
    if len(w) < 24:
        return None
    m = sum(w)/len(w)
    v = sum((x-m)**2 for x in w)/len(w)
    s = math.sqrt(v)
    if s <= 0:
        return 0.0
    return (series[i]-m)/s


def main():
    now = int(time.time()*1000); start = now - DAYS*86400*1000
    cand = paginate("c", "ETH", start, now)
    fund = paginate("f", "ETH", start, now)
    print(f"candles {len(cand)}  funding {len(fund)}  (~{DAYS}d hourly)")
    if len(cand) < 400:
        print("insufficient"); return 1

    t = [c["t"] for c in cand]
    close = [float(c["c"]) for c in cand]
    high = [float(c["h"]) for c in cand]
    low = [float(c["l"]) for c in cand]
    vol = [float(c["v"]) for c in cand]
    ft = [r["time"] for r in fund]; fr = [float(r["fundingRate"]) for r in fund]

    def funding_at(ts):
        i = bisect_right(ft, ts)-1
        return fr[i] if i >= 0 else 0.0

    N = len(cand)
    ret1 = [None]*N
    for i in range(1, N):
        ret1[i] = close[i]/close[i-1]-1 if close[i-1] > 0 else None

    # raw features
    f_mom24 = [None]*N; f_mom7d = [None]*N; f_vol = [None]*N; f_accel = [None]*N
    f_volz = [None]*N; f_range = [None]*N; f_fund = [None]*N; f_fundchg = [None]*N
    lv = [math.log(v+1) for v in vol]
    for i in range(N):
        if i >= 24: f_mom24[i] = close[i]/close[i-24]-1
        if i >= 168: f_mom7d[i] = close[i]/close[i-168]-1
        if i >= 48:
            w = [r for r in ret1[i-48:i] if r is not None]
            f_vol[i] = math.sqrt(sum(x*x for x in w)/len(w)) if w else None
        if i >= 48 and f_mom24[i] is not None and f_mom24[i-24] is not None:
            f_accel[i] = f_mom24[i]-f_mom24[i-24]
        f_volz[i] = lv[i]
        if i >= 24:
            f_range[i] = sum((high[j]-low[j])/close[j] for j in range(i-24, i) if close[j] > 0)/24
        f_fund[i] = funding_at(t[i])
        if i >= 24: f_fundchg[i] = funding_at(t[i]) - funding_at(t[i]-24*H)

    feats = [f_mom24, f_mom7d, f_vol, f_accel, f_volz, f_range, f_fund, f_fundchg]

    def price_at(ts):
        i = bisect_right(t, ts)-1
        return close[i] if i >= 0 else None

    for hours in (24, 48):
        h = hours*H
        M, E, Z1, fwd = [], [], [], []
        i = 200
        while t[i] + h <= now:
            zs = [zscore(f, i) for f in feats]
            if all(z is not None for z in zs):
                d = zs  # deviation-from-baseline in std units
                M.append(-(d[0]**2) + sum(x*x for x in d[1:]))   # their signature
                E.append(sum(x*x for x in d))                     # control: Euclidean
                Z1.append(d[0])                                   # control: momentum alone
                p1 = price_at(t[i]+h)
                fwd.append(p1/close[i]-1 if p1 else None)
            i += hours  # non-overlapping
        n = len(fwd); sp = int(0.7*n)
        print(f"\n{'='*64}\n8D-MINKOWSKI vs forward {hours}h ETH return (non-overlap, n={n})\n{'='*64}")
        print(f"{'metric':22s} {'IS corr (n)':>15s} {'OOS corr (n)':>15s}")
        for name, ser in (("Minkowski interval", M), ("Euclidean (control)", E),
                          ("momentum z1 (control)", Z1)):
            cis, nis = pearson(ser[:sp], fwd[:sp])
            coos, noos = pearson(ser[sp:], fwd[sp:])
            fmt = lambda c, k: (f"{c:+.3f}(n={k})" if c is not None else f"n/a(n={k})")
            print(f"{name:22s} {fmt(cis,nis):>15s} {fmt(coos,noos):>15s}")
    print("\nRead: for the Minkowski metric to mean anything it must (a) show a")
    print("forward corr that HOLDS in sign IS->OOS, and (b) BEAT the controls.")
    print("If controls match/beat it, the '8D Minkowski' framing adds nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
