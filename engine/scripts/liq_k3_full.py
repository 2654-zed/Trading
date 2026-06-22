#!/usr/bin/env python3
"""Phase-0 K3-FULL for the liquidation-cascade FEAS — book-resilience lead test.

The trader red-team said the only genuinely under-arbitraged signal is BOOK
RESILIENCE (how thin the book is vs normal, and how fast it's thinning) — not the
public OI/funding that K3-lite already showed are flat. This tests it on
book_snapshot_25 (top-25 levels) for 3 stress days (different regimes) + 3 calm days.

Per 10s bar (per day, no cross-day returns):
  mid        = (best_ask + best_bid)/2
  near_depth = sum(bid+ask amount) within K_BPS of mid     (the liquidity a sweep eats)
  thinness   = -log(near_depth / trailing-1h median)        (high = book thinner than normal)
  thinning   = thinness rise over the last ~1min            (book actively evaporating)
Target  : log(fwd_5min_vol / trailing_5min_vol)             (price-only vol acceleration)
PASS iff book thinness / thinning LEADS the acceleration beyond vol AND beats a
vol-matched placebo. (First cut: 6 days, ~clustered — directional, not a powered gate.)
"""
from __future__ import annotations
import csv, glob, gzip, math, random, statistics as st
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "tardis"
BAR_S, H, TR, K_BPS = 10, 30, 30, 10.0      # 10s bars, 5min fwd/trail, near-touch within 10bps
MEDW, THIN_L = 360, 6                          # 1h depth-median window, 1min thinning lag
N_PERM = 400
STRESS = {"2026-06-02", "2026-04-17", "2026-05-23"}
CALM = {"2026-06-12", "2026-04-28", "2026-03-19"}
random.seed(11)


def pearson(x, y):
    n = len(x); mx = sum(x)/n; my = sum(y)/n
    sxy = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    sxx = sum((x[i]-mx)**2 for i in range(n)); syy = sum((y[i]-my)**2 for i in range(n))
    return sxy/math.sqrt(sxx*syy) if sxx and syy else 0.0


def day_bars(fp):
    """Stream one book_snapshot_25 file -> 10s bars: (bar_ts, last_mid, mean_near_depth)."""
    mid_last, dep_sum, dep_n, cur = {}, defaultdict(float), defaultdict(int), None
    with gzip.open(fp, "rt") as f:
        r = csv.reader(f); next(r, None)  # header
        for row in r:
            try:
                ts = int(row[2]); ap = float(row[4]); bp = float(row[6])
            except (ValueError, IndexError):
                continue
            if ap <= 0 or bp <= 0:
                continue
            mid = (ap+bp)/2.0; lim = mid*K_BPS/10000.0
            dep = 0.0
            for i in range(25):
                base = 4+4*i
                try:
                    apx, aam, bpx, bam = float(row[base]), float(row[base+1]), float(row[base+2]), float(row[base+3])
                except (ValueError, IndexError):
                    break
                if apx-mid <= lim and apx > 0: dep += aam
                if mid-bpx <= lim and bpx > 0: dep += bam
                if apx-mid > lim and mid-bpx > lim: break
            b = (ts//1_000_000//BAR_S)*BAR_S
            mid_last[b] = mid; dep_sum[b] += dep; dep_n[b] += 1
    bars = sorted(mid_last)
    return [(b, mid_last[b], dep_sum[b]/dep_n[b]) for b in bars]


def main():
    files = {}
    for fp in glob.glob(str(DATA / "binance-futures_book_snapshot_25_*_BTCUSDT.csv.gz")):
        d = Path(fp).name.split("book_snapshot_25_")[1][:10]
        if d in STRESS or d in CALM:
            files[d] = fp
    have = sorted(files)
    print(f"=== K3-FULL book-resilience — days present: {len(have)} ===")
    print(f"  stress: {sorted(d for d in have if d in STRESS)}")
    print(f"  calm  : {sorted(d for d in have if d in CALM)}")
    if not have:
        print("no book_snapshot_25 files yet — pull still running?"); return

    THN, THR, EXP, RV, TAG = [], [], [], [], []   # pooled across days
    for d in have:
        bars = day_bars(files[d]); n = len(bars)
        if n < 2*H+MEDW:
            print(f"  {d}: only {n} bars, skipping"); continue
        ts = [b[0] for b in bars]; mid = [b[1] for b in bars]; dep = [b[2] for b in bars]
        ret = [0.0]+[math.log(mid[i]/mid[i-1]) if mid[i-1] > 0 else 0.0 for i in range(1, n)]
        rv = [math.sqrt(sum(ret[j]*ret[j] for j in range(max(0, i-TR+1), i+1))) for i in range(n)]
        fwd = [math.sqrt(sum(ret[j]*ret[j] for j in range(i+1, min(i+1+H, n)))) for i in range(n)]
        thin = []
        for i in range(n):
            base = st.median(dep[max(0, i-MEDW):i+1]) or 1.0
            thin.append(-math.log(max(dep[i], 1e-9)/base))
        thr = [thin[i]-thin[max(0, i-THIN_L)] for i in range(n)]
        for i in range(MEDW, n-H):
            if rv[i] > 1e-6 and fwd[i] > 0:
                THN.append(thin[i]); THR.append(thr[i]); RV.append(rv[i])
                EXP.append(math.log(fwd[i]/max(rv[i], 1e-6))); TAG.append(1 if d in STRESS else 0)
        print(f"  {d}: {n} bars  (${(dep and sum(dep)/len(dep)):.1f} avg near-depth BTC)")

    m = len(EXP)
    if m < 500:
        print(f"only {m} pooled bars — need more days."); return
    # winsorize target
    s = sorted(EXP); lo, hi = s[len(s)//100], s[-len(s)//100]
    EXP = [min(max(v, lo), hi) for v in EXP]
    print(f"\n  pooled bars: {m:,}  ({sum(TAG)} stress, {m-sum(TAG)} calm)\n")
    print(f"  corr(book THINNESS, vol-acceleration)   : {pearson(THN, EXP):+.3f}")
    print(f"  corr(book THINNING rate, vol-accel)     : {pearson(THR, EXP):+.3f}")

    # vol-matched placebo for thinness
    order = sorted(range(m), key=lambda i: RV[i]); dsz = m//10
    dec = [0]*m
    for di in range(10):
        for i in (order[di*dsz:(di+1)*dsz] if di < 9 else order[di*dsz:]): dec[i] = di
    by = defaultdict(list)
    for i in range(m): by[dec[i]].append(i)
    real = pearson(THN, EXP); null = []
    for _ in range(N_PERM):
        sh = THN[:]
        for di, ids in by.items():
            vv = [THN[i] for i in ids]; random.shuffle(vv)
            for i, v in zip(ids, vv): sh[i] = v
        null.append(pearson(sh, EXP))
    nm = sum(null)/len(null); nsd = (sum((x-nm)**2 for x in null)/len(null))**0.5
    print(f"  vol-matched placebo (shuffle thinness in rv-deciles): null {nm:+.3f}+/-{nsd:.3f} -> real {(real-nm)/nsd:+.1f} sd")

    # stress vs calm
    for name, t in [("stress days", 1), ("calm days", 0)]:
        ix = [i for i in range(m) if TAG[i] == t]
        if ix:
            print(f"  corr(thinness, accel) on {name:<11}: {pearson([THN[i] for i in ix], [EXP[i] for i in ix]):+.3f}  (n={len(ix)})")

    z = (real-nm)/nsd if nsd else 0
    passes = (pearson(THN, EXP) > 0.05 or pearson(THR, EXP) > 0.05) and z > 3
    print(f"\n  VERDICT: {'BOOK-RESILIENCE LEADS (K3-full first cut survives — worth the real build)' if passes else 'no clean book-resilience lead (K3-full first cut does NOT survive)'}")
    print("  NOTE: 6 clustered days — directional first cut, not a powered OOS gate.")


if __name__ == "__main__":
    main()
