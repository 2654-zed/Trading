#!/usr/bin/env python3
"""Phase-0 K3-lite for the liquidation-cascade FEAS — placebo + circularity firewall.

Two things K2 didn't do:
  (a) CIRCULARITY firewall — the label is built from PRICE PRIMITIVES ONLY (forward
      vol EXPANSION beyond the current vol regime), never from a liquidation series;
      and we check that NON-liquidation features (OI-ROC, funding) also lead it.
  (b) PLACEBO — is the fragility->expansion link a real temporal lead, or just both
      living in the same vol regime? Compare the real correlation to a VOL-MATCHED
      permutation null (shuffle fragility within realized-vol deciles).

Target  : log_expansion_t = log( fwd_1h_vol / current_EWMA_vol )   (price-only, vol-residual)
Features: liqburst (liq-derived), OI-ROC + |funding| (NON-liq).  Stdlib only.
PASS iff fragility leads the price-defined expansion AND beats the vol-matched
placebo AND non-liquidation features also lead (not a burst-predicts-burst artifact).
"""
from __future__ import annotations
import csv, glob, gzip, math, random, datetime as dt
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "tardis"
BAR_S, H, EWMA_HL, OI_LB = 300, 12, 24, 12
RV_FLOOR = 1e-5
N_PERM = 400
random.seed(7)


def bar_key(ts_us):
    return int(ts_us // 1_000_000 // BAR_S) * BAR_S


def pearson(x, y):
    n = len(x); mx = sum(x)/n; my = sum(y)/n
    sxy = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    sxx = sum((x[i]-mx)**2 for i in range(n)); syy = sum((y[i]-my)**2 for i in range(n))
    return sxy/math.sqrt(sxx*syy) if sxx and syy else 0.0


def build():
    mark, oi, fund = {}, {}, {}
    liq = defaultdict(float)
    for fp in sorted(glob.glob(str(DATA / "binance-futures_derivative_ticker_*_BTCUSDT.csv.gz"))):
        with gzip.open(fp, "rt") as f:
            for row in csv.DictReader(f):
                try: b = bar_key(int(row["timestamp"]))
                except (ValueError, KeyError, TypeError): continue
                mp = row.get("mark_price") or row.get("last_price")
                if mp:
                    try: mark[b] = float(mp)
                    except ValueError: pass
                if row.get("open_interest"):
                    try: oi[b] = float(row["open_interest"])
                    except ValueError: pass
                if row.get("funding_rate"):
                    try: fund[b] = float(row["funding_rate"])
                    except ValueError: pass
    for fp in sorted(glob.glob(str(DATA / "binance-futures_liquidations_*_BTCUSDT.csv.gz"))):
        with gzip.open(fp, "rt") as f:
            for row in csv.DictReader(f):
                try: liq[bar_key(int(row["timestamp"]))] += float(row["price"])*float(row["amount"])
                except (ValueError, KeyError, TypeError): continue
    return mark, oi, fund, liq


def main():
    mark, oi, fund, liq = build()
    bars = sorted(mark)
    if len(bars) < 1000:
        print(f"only {len(bars)} bars — pull derivative_ticker first."); return
    grid = list(range(bars[0], bars[-1]+BAR_S, BAR_S)); n = len(grid)
    px = []; last = mark[bars[0]]
    for b in grid:
        last = mark.get(b, last); px.append(last)
    ret = [0.0] + [math.log(px[i]/px[i-1]) if px[i-1] > 0 else 0.0 for i in range(1, n)]
    # SAME scale: trailing-1h vs next-1h realized vol, so expansion ratio has baseline ~1
    rv = [math.sqrt(sum(ret[j]*ret[j] for j in range(max(0, i-H+1), i+1))) for i in range(n)]
    fwd = [math.sqrt(sum(ret[j]*ret[j] for j in range(i+1, min(i+1+H, n)))) for i in range(n)]
    liqb = [math.log1p(liq.get(b, 0.0)) for b in grid]
    lo = next(iter(oi.values())) if oi else 1.0; ois = []
    for b in grid: lo = oi.get(b, lo); ois.append(lo)
    oiv = [abs((ois[i]-ois[max(0, i-OI_LB)])/ois[max(0, i-OI_LB)]) if ois[max(0, i-OI_LB)] else 0.0 for i in range(n)]
    lf = next(iter(fund.values())) if fund else 0.0; fs = []
    for b in grid: lf = fund.get(b, lf); fs.append(abs(lf))

    idx = [i for i in range(H, n-H) if rv[i] > RV_FLOOR and fwd[i] > 0]
    logexp = [math.log(fwd[i]/max(rv[i], RV_FLOOR)) for i in idx]
    # winsorize target 1/99
    s = sorted(logexp); lo_w, hi_w = s[len(s)//100], s[-len(s)//100]
    logexp = [min(max(v, lo_w), hi_w) for v in logexp]
    liqb_, oiv_, fs_, rv_ = ([v[i] for i in idx] for v in (liqb, oiv, fs, rv))
    m = len(idx)
    print(f"=== K3-lite — {m:,} bars ({dt.datetime.fromtimestamp(grid[idx[0]],dt.timezone.utc):%Y-%m-%d}.."
          f"{dt.datetime.fromtimestamp(grid[idx[-1]],dt.timezone.utc):%Y-%m-%d}) ===")
    print("label = log(fwd_1h_vol / current_vol)  [PRICE-ONLY, vol-residualized]\n")

    r_liq = pearson(liqb_, logexp)
    r_oi = pearson(oiv_, logexp)
    r_fund = pearson(fs_, logexp)
    print(f"  corr(liqburst, expansion)  : {r_liq:+.3f}   (liq-derived feature)")
    print(f"  corr(OI-ROC,   expansion)  : {r_oi:+.3f}   (NON-liq — circularity firewall)")
    print(f"  corr(|funding|,expansion)  : {r_fund:+.3f}   (NON-liq)")

    # vol-matched permutation placebo for liqburst
    order = sorted(range(m), key=lambda i: rv_[i]); dsz = m//10
    decile = [0]*m
    for d in range(10):
        for i in order[d*dsz:(d+1)*dsz] if d < 9 else order[d*dsz:]:
            decile[i] = d
    by_dec = defaultdict(list)
    for i in range(m): by_dec[decile[i]].append(i)
    null = []
    for _ in range(N_PERM):
        shuffled = liqb_[:]
        for d, ids in by_dec.items():
            vals = [liqb_[i] for i in ids]; random.shuffle(vals)
            for i, v in zip(ids, vals): shuffled[i] = v
        null.append(pearson(shuffled, logexp))
    nm = sum(null)/len(null); nsd = (sum((x-nm)**2 for x in null)/len(null))**0.5
    z = (r_liq-nm)/nsd if nsd else 0.0
    print(f"\n  vol-matched placebo (shuffle liqburst within rv-deciles, {N_PERM}x):")
    print(f"    null corr ~ {nm:+.3f} ± {nsd:.3f}   ->   real is {z:+.1f} sigma above placebo")

    # binary cross-check: does liqburst discriminate big expansion even within calm vs stress?
    big = [1 if v > math.log(1.5) else 0 for v in logexp]   # next-hr vol > 1.5x current
    def rate(ids): return sum(big[i] for i in ids)/len(ids) if ids else 0.0
    lo_rv = [i for i in range(m) if decile[i] < 5]; hi_rv = [i for i in range(m) if decile[i] >= 5]
    for name, sub in [("calm half (low rv)", lo_rv), ("stress half (high rv)", hi_rv)]:
        sub_sorted = sorted(sub, key=lambda i: liqb_[i])
        loq = sub_sorted[:len(sub)//5]; hiq = sub_sorted[-len(sub)//5:]
        print(f"  {name:<22}: P(big expansion) bottom-liqburst={rate(loq):.3f}  top-liqburst={rate(hiq):.3f}")

    passes = (r_liq > 0.05 and z > 3 and (r_oi > 0.03 or r_fund > 0.03))
    print(f"\n  VERDICT: {'SURVIVES K3-lite (real vol-residualized lead; non-liq features lead too; beats placebo)' if passes else 'FAILS K3-lite'}")


if __name__ == "__main__":
    main()
