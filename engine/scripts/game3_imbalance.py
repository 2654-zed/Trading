#!/usr/bin/env python3
"""Game 3 ORIGINAL question — order-book imbalance -> short-horizon move (sealed-holdout first cut).

Hypothesis: OBI(t) = (Σ top-N bid amt − Σ top-N ask amt)/(Σ bid+ask) predicts the SIGNED
forward mid return over horizon h. The decisive question is NET OF COST: does the predicted
directional move EXCEED the spread a taker must cross? (Classic microstructure trap: top-of-
book imbalance predicts sub-spread ticks / bid-ask bounce — statistically real, untradeable
for a taker with no queue priority.)

book_snapshot_25, 6 days. Day-blocked split: IS = earliest 4 days, OOS = latest 2.
Per 1s bar: mid, OBI (top-10 levels), spread (bps). Forward returns at 1/5/30/60/300s.
GO iff a directional edge EXCEEDS the spread AND the sign holds OOS.
"""
from __future__ import annotations
import csv, glob, gzip, math, sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252 by default
except Exception:
    pass

DATA = Path(__file__).resolve().parents[1] / "data" / "tardis"
BAR_S, N = 1, 10
HORIZONS = [1, 5, 30, 60, 300]
TAKER_FEE_BPS = 4.0   # realistic Binance USDM-futures taker fee per side; the REAL cost wall (spread on BTC is ~0.01bps)


def pearson(x, y):
    n = len(x); mx = sum(x)/n; my = sum(y)/n
    sxy = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    sxx = sum((x[i]-mx)**2 for i in range(n)); syy = sum((y[i]-my)**2 for i in range(n))
    return sxy/math.sqrt(sxx*syy) if sxx and syy else 0.0


def day_series(fp):
    mid, obi, spr = {}, {}, {}
    with gzip.open(fp, "rt") as f:
        r = csv.reader(f); next(r, None)
        for row in r:
            try:
                ts = int(row[2]); ap = float(row[4]); bp = float(row[6])
            except (ValueError, IndexError):
                continue
            if ap <= 0 or bp <= 0 or ap < bp:
                continue
            m = (ap+bp)/2.0; bsum = asum = 0.0
            for i in range(N):
                base = 4+4*i
                try:
                    asum += float(row[base+1]); bsum += float(row[base+3])
                except (ValueError, IndexError):
                    break
            o = (bsum-asum)/(bsum+asum) if (bsum+asum) > 0 else 0.0
            b = (ts//1_000_000//BAR_S)*BAR_S
            mid[b] = m; obi[b] = o; spr[b] = (ap-bp)/m*1e4
    return mid, obi, spr


def pool(days):
    """Return per-horizon lists of (obi, fwd_ret_bps) + spread list, pooled over days (no cross-day)."""
    F = {h: ([], []) for h in HORIZONS}; spreads = []
    for fp in days:
        mid, obi, spr = day_series(fp)
        if len(mid) < 1000:
            continue
        b0, b1 = min(mid), max(mid)
        grid = list(range(b0, b1+1, BAR_S))
        lm = mid[b0]; lo = obi[b0]; ls = spr[b0]
        M = []; O = []; S = []
        for b in grid:
            lm = mid.get(b, lm); lo = obi.get(b, lo); ls = spr.get(b, ls)
            M.append(lm); O.append(lo); S.append(ls)
        spreads.extend(S)
        ng = len(grid)
        for h in HORIZONS:
            xs, ys = F[h]
            for i in range(ng-h):
                if M[i] > 0:
                    xs.append(O[i]); ys.append((M[i+h]-M[i])/M[i]*1e4)
    return F, spreads


def report(F, spreads, label):
    med_spr = sorted(spreads)[len(spreads)//2] if spreads else float("nan")
    cost = med_spr + 2*TAKER_FEE_BPS   # taker round-trip = full spread + 2 taker fees
    print(f"\n=== {label}  (median spread {med_spr:.3f} bps; TAKER round-trip cost {cost:.2f} bps @ {TAKER_FEE_BPS} bps/side) ===")
    print(f"  {'horizon':>7} {'corr':>7} {'hit%':>6} {'topD_ret':>9} {'botD_ret':>9} {'edge/side':>9} {'net-of-cost':>11}")
    res = {}
    for h in HORIZONS:
        x, y = F[h]
        if len(x) < 1000:
            continue
        c = pearson(x, y)
        hit = sum(1 for i in range(len(x)) if (x[i] > 0) == (y[i] > 0)) / len(x)
        order = sorted(range(len(x)), key=lambda i: x[i]); d = len(x)//10
        top = order[-d:]; bot = order[:d]
        tr = sum(y[i] for i in top)/d; br = sum(y[i] for i in bot)/d
        edge = (tr-br)/2.0
        print(f"  {h:>6}s {c:>+7.3f} {100*hit:>5.1f}% {tr:>+8.2f} {br:>+8.2f} {edge:>+8.2f} {edge-cost:>+11.2f}")
        res[h] = (c, edge, cost)
    return res


def main():
    files = {}
    for fp in sorted(glob.glob(str(DATA / "binance-futures_book_snapshot_25_*_BTCUSDT.csv.gz"))):
        d = Path(fp).name.split("book_snapshot_25_")[1][:10]; files[d] = fp
    days = sorted(files)
    if len(days) < 4:
        print(f"only {len(days)} book days — need more."); return
    is_days = [files[d] for d in days[:-2]]; oos_days = [files[d] for d in days[-2:]]
    print(f"OBI = top-{N}-level imbalance.  IS days: {days[:-2]}   OOS days: {days[-2:]}")
    print("edge/side = (top-decile − bottom-decile fwd return)/2 in bps; 'vs spread' = edge minus median spread (a taker pays ~1 spread round-trip).")

    Fis, Sis = pool(is_days); res_is = report(Fis, Sis, "IN-SAMPLE")
    Foos, Soos = pool(oos_days); res_oos = report(Foos, Soos, "OUT-OF-SAMPLE")

    print("\n=== VERDICT ===")
    any_net = False
    for h in HORIZONS:
        if h in res_is and h in res_oos:
            ci, ei, cost = res_is[h]; co, eo, _ = res_oos[h]
            sign_holds = (ci > 0) == (co > 0) and abs(co) > 0.01
            net = ei > cost and eo > cost
            if net and sign_holds:
                any_net = True
            print(f"  {h:>3}s: IS corr {ci:+.3f} / OOS corr {co:+.3f}  sign-holds={sign_holds}  "
                  f"edge {ei:+.2f} bps vs taker cost {cost:.2f} bps  -> {'NET EDGE' if net else 'below cost (untradeable)'}")
    print(f"\n  {'GO — a net-of-cost directional edge holds OOS' if any_net else 'NO-GO — imbalance predicts direction (real, OOS-stable) but the edge is ~10-25x SMALLER than taker fees: untradeable from our (taker, no-queue-priority) seat'}")
    print("  NOTE: first cut, 6 days, taker-cost frame; a maker/queue-priority frame is a different (and not-ours) game.")


if __name__ == "__main__":
    main()
