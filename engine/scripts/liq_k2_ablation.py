#!/usr/bin/env python3
"""Phase-0 K2 vol-ablation for the liquidation-cascade FEAS.

The decisive question (per the red-team): is "fragility predicts the forward move"
just a restatement of VOLATILITY CLUSTERING? Test whether fragility features add
INCREMENTAL forward-vol prediction BEYOND current realized vol. If not -> the edge
is a vol artifact -> NO-GO. See docs/FEAS_liquidation-cascade.md (K2).

Builds 5-min bars from derivative_ticker (mark price, OI, funding) + liquidations.
  control  : rv_t       = EWMA realized vol (current)            <- vol clustering
  fragility: liqburst_t = log1p(liq USD this bar)
             oi_roc_t   = |1h open-interest rate-of-change|
  target   : fwd_rv     = realized vol over the NEXT hour        <- "adverse move"
Stdlib only. K2 PASSES iff fragility beats a vol-only benchmark out-of-the-box.
"""
from __future__ import annotations
import csv, glob, gzip, math, datetime as dt
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "tardis"
BAR_S = 300              # 5-min bars
H = 12                  # forward horizon = 12 bars = 1h
EWMA_HL = 24            # realized-vol EWMA halflife (bars) ~ 2h
OI_LB = 12             # OI rate-of-change lookback (1h)


def bar_key(ts_us):
    return int(ts_us // 1_000_000 // BAR_S) * BAR_S  # unix seconds, bar-aligned


def build_bars():
    mark, oi, fund = {}, {}, {}    # bar -> last value
    liq = defaultdict(float)        # bar -> liq USD
    for fp in sorted(glob.glob(str(DATA / "binance-futures_derivative_ticker_*_BTCUSDT.csv.gz"))):
        with gzip.open(fp, "rt") as f:
            for row in csv.DictReader(f):
                try:
                    b = bar_key(int(row["timestamp"]))
                except (ValueError, KeyError, TypeError):
                    continue
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
                try:
                    liq[bar_key(int(row["timestamp"]))] += float(row["price"]) * float(row["amount"])
                except (ValueError, KeyError, TypeError):
                    continue
    return mark, oi, fund, liq


def pearson(x, y):
    n = len(x); mx = sum(x)/n; my = sum(y)/n
    sxy = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    sxx = sum((x[i]-mx)**2 for i in range(n)); syy = sum((y[i]-my)**2 for i in range(n))
    return sxy/math.sqrt(sxx*syy) if sxx and syy else 0.0


def solve(A, b):  # Gaussian elimination, small system
    n = len(A); M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        if abs(piv) < 1e-12: continue
        M[c] = [v/piv for v in M[c]]
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c]; M[r] = [M[r][k]-f*M[c][k] for k in range(n+1)]
    return [M[i][n] for i in range(n)]


def ols_r2(cols, y):
    """cols: list of feature columns (each a list). Returns R^2 with intercept."""
    n = len(y); A = [[1.0] + [cols[j][i] for j in range(len(cols))] for i in range(n)]
    p = len(A[0])
    ATA = [[sum(A[i][a]*A[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    ATy = [sum(A[i][a]*y[i] for i in range(n)) for a in range(p)]
    beta = solve(ATA, ATy)
    yb = sum(y)/n
    yhat = [sum(beta[a]*A[i][a] for a in range(p)) for i in range(n)]
    ssr = sum((y[i]-yhat[i])**2 for i in range(n)); sst = sum((y[i]-yb)**2 for i in range(n))
    return 1 - ssr/sst if sst else 0.0


def main():
    mark, oi, fund, liq = build_bars()
    bars = sorted(mark)
    if len(bars) < 1000:
        print(f"only {len(bars)} bars with mark price — derivative_ticker may not be pulled yet."); return
    # contiguous 5-min grid from first to last bar
    grid = list(range(bars[0], bars[-1] + BAR_S, BAR_S))
    px = []
    last = mark[bars[0]]
    for b in grid:
        last = mark.get(b, last); px.append(last)
    # log returns
    ret = [0.0] + [math.log(px[i]/px[i-1]) if px[i-1] > 0 else 0.0 for i in range(1, len(grid))]
    # EWMA realized vol (current)
    alpha = 1 - 0.5**(1/EWMA_HL); ev = 0.0; rv = []
    for r in ret:
        ev = alpha*(r*r) + (1-alpha)*ev; rv.append(math.sqrt(ev))
    # forward realized vol over next H bars
    n = len(grid)
    fwd = [0.0]*n
    for i in range(n):
        s = sum(ret[j]*ret[j] for j in range(i+1, min(i+1+H, n)))
        fwd[i] = math.sqrt(s)
    # fragility features
    liqb = [math.log1p(liq.get(b, 0.0)) for b in grid]
    oiv = []
    last_oi = None; oi_series = []
    lo = oi[bars[0]] if bars[0] in oi else (next(iter(oi.values())) if oi else 1.0)
    for b in grid:
        lo = oi.get(b, lo); oi_series.append(lo)
    for i in range(n):
        j = max(0, i-OI_LB)
        oiv.append(abs((oi_series[i]-oi_series[j])/oi_series[j]) if oi_series[j] else 0.0)

    # valid window (skip warmup + need forward horizon)
    idx = [i for i in range(EWMA_HL, n-H) if fwd[i] > 0]
    rv_, liqb_, oiv_, y_ = ([v[i] for i in idx] for v in (rv, liqb, oiv, fwd))
    m = len(idx)
    print(f"=== K2 vol-ablation — {m:,} 5-min bars ({dt.datetime.fromtimestamp(grid[idx[0]],dt.timezone.utc):%Y-%m-%d} .. "
          f"{dt.datetime.fromtimestamp(grid[idx[-1]],dt.timezone.utc):%Y-%m-%d}) ===")
    print(f"target = forward {H*BAR_S//60}min realized vol; control = current EWMA rv; fragility = liqburst, |OI-ROC|")

    rA = ols_r2([rv_], y_)
    rB = ols_r2([rv_, liqb_, oiv_], y_)
    print(f"\n  R^2  vol-only            : {rA:.4f}")
    print(f"  R^2  vol + fragility     : {rB:.4f}")
    print(f"  >> INCREMENTAL R^2       : {rB-rA:+.4f}   ({100*(rB-rA):.2f} percentage points)")

    # partial correlations with fwd, controlling for rv
    def pcorr(x):
        rxy, rxz, ryz = pearson(x, y_), pearson(x, rv_), pearson(y_, rv_)
        d = math.sqrt((1-rxz*rxz)*(1-ryz*ryz))
        return (rxy - rxz*ryz)/d if d else 0.0
    print(f"\n  corr(liqburst, fwd_rv)            : {pearson(liqb_, y_):+.3f}")
    print(f"  PARTIAL corr(liqburst, fwd | rv)  : {pcorr(liqb_):+.3f}")
    print(f"  PARTIAL corr(OI-ROC,  fwd | rv)   : {pcorr(oiv_):+.3f}")

    # vol-matched comparison: within rv deciles, do high-liqburst bars have higher fwd_rv?
    order = sorted(range(m), key=lambda i: rv_[i])
    dec = m//10
    hi_minus_lo = []
    for d_ in range(10):
        seg = order[d_*dec:(d_+1)*dec] if d_ < 9 else order[d_*dec:]
        seg.sort(key=lambda i: liqb_[i])
        h = seg[len(seg)//2:]; l = seg[:len(seg)//2]
        mh = sum(y_[i] for i in h)/len(h); ml = sum(y_[i] for i in l)/len(l)
        hi_minus_lo.append(mh-ml)
    pos = sum(1 for d_ in hi_minus_lo if d_ > 0)
    print(f"\n  vol-matched: within each realized-vol decile, mean fwd_rv(high-liqburst) - (low):")
    print(f"    positive in {pos}/10 deciles; avg gap = {sum(hi_minus_lo)/10:+.6f}")

    verdict = "SURVIVES K2 (fragility adds info beyond vol)" if (rB-rA) > 0.01 and pcorr(liqb_) > 0.05 and pos >= 7 \
              else "FAILS K2 (vol artifact — fragility adds ~nothing beyond realized vol)"
    print(f"\n  VERDICT: {verdict}")


if __name__ == "__main__":
    main()
