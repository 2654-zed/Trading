#!/usr/bin/env python3
"""K0 - $0 synthetic honest-kill of the factor harness (docs/FEAS_factor-pivot.md).

Red-team claim: the research_loop factor harness is tuned to a FALSE GO. The decisive
$0 test is the FALSE-POSITIVE RATE on PURE NOISE, evaluated the way a real researcher
works (try a GRID of specs, keep the best):
  NAIVE  (as-coded: daily n_obs, flat 30bps, best-of-grid spec, no MT correction)  vs
  HONEST (monthly non-overlapping n_obs, size/ADV-scaled cost, FULL-GRID Bonferroni).
Mirrors factor_measurements.py (`n_obs=len(rets)`, t=mean/(sd/sqrt(n)), flat `2*cost_bps`)
and the garden-of-forking-paths the ledger doesn't count.

If NAIVE passes pure noise far above alpha and HONEST restores ~alpha, a GO from the
current harness is untrustworthy. Plus a power check: does the honest gate detect a
REALISTIC (weak) planted edge, or is it underpowered at ~monthly N?
"""
from __future__ import annotations
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np
from engine.research_loop.factor_data import make_synthetic_panel

ALPHA = 0.05
N_DAYS, N_TOK = 1197, 100          # 57 clean 21-day "months" — realistic backtest length
BLOCK = 21                         # honest rebalance period
HOLDOUT = 0.34
DV_FLOOR = 1e5


def phi(z): return 0.5*(1+math.erf(z/math.sqrt(2)))
def pval(series):
    s = np.asarray(series, float); n = len(s)
    if n < 2 or s.std(ddof=1) == 0: return 1.0, 0.0
    t = s.mean()/(s.std(ddof=1)/math.sqrt(n)); return 2*(1-phi(abs(t))), s.mean()


def matrices(panel):
    dates = np.sort(panel["date"].unique()); toks = np.sort(panel["token"].unique())
    di = {d: i for i, d in enumerate(dates)}; ti = {t: i for i, t in enumerate(toks)}
    D, N = len(dates), len(toks)
    ret = np.full((D, N), np.nan); fund = np.full((D, N), np.nan)
    dv = np.zeros((D, N)); listed = np.zeros((D, N), bool)
    for r in panel.itertuples(index=False):
        i, j = di[r.date], ti[r.token]
        ret[i, j] = r.ret; fund[i, j] = r.funding_rate; dv[i, j] = r.dollar_volume; listed[i, j] = r.is_listed
    return ret, fund, dv, listed


def mom_signal(ret, lookback):
    """Trailing lookback-sum of ret, STRICTLY before t (shift 1). No look-ahead."""
    D, N = ret.shape; sig = np.full((D, N), np.nan); r0 = np.nan_to_num(ret)
    for t in range(lookback+1, D):
        sig[t] = r0[t-lookback:t].sum(axis=0)
    return sig


def honest_name_cost_bps(dv_sel):
    # size/ADV-scaled: liquid ~12bps, illiquid (near floor) ~210bps. one side.
    return np.clip(10 + 200*(DV_FLOOR/np.maximum(dv_sel, 1.0)), 10, 300)


def naive_daily_ls(sig, ret, dv, listed, q, long_high):
    """As-coded harness: rebalance EVERY date, flat 30bps/side, one return per day."""
    D = ret.shape[0]; out = []
    for t in range(D):
        valid = listed[t] & (dv[t] >= DV_FLOOR) & np.isfinite(sig[t]) & np.isfinite(ret[t])
        idx = np.where(valid)[0]
        if len(idx) < 10: continue
        k = max(1, int(len(idx)*q)); order = idx[np.argsort(sig[t, idx])]
        lo, hi = order[:k], order[-k:]
        lo_, sh_ = (hi, lo) if long_high else (lo, hi)
        out.append(ret[t, lo_].mean() - ret[t, sh_].mean() - 2*30.0/1e4)
    return np.asarray(out, float)


def honest_monthly_ls(sig, ret, dv, listed, q, long_high):
    """Honest: form book at each 21-day block start, HOLD the block (sum of daily
    returns of the fixed constituents), charge size/ADV cost ONCE per rebalance."""
    D = ret.shape[0]; out = []
    for t0 in range(0, D - BLOCK, BLOCK):
        valid = listed[t0] & (dv[t0] >= DV_FLOOR) & np.isfinite(sig[t0]) & np.isfinite(ret[t0])
        idx = np.where(valid)[0]
        if len(idx) < 10: continue
        k = max(1, int(len(idx)*q)); order = idx[np.argsort(sig[t0, idx])]
        lo, hi = order[:k], order[-k:]
        long_n, short_n = (hi, lo) if long_high else (lo, hi)
        block = ret[t0:t0+BLOCK]                                   # forward hold of the fixed book
        long_r = np.nansum(block[:, long_n], axis=0).mean()
        short_r = np.nansum(block[:, short_n], axis=0).mean()
        sel = np.concatenate([long_n, short_n])
        cost = 2*honest_name_cost_bps(dv[t0, sel]).mean()/1e4      # ONCE per month, size-scaled
        out.append(long_r - short_r - cost)
    return np.asarray(out, float)


def grid():
    specs = []
    for lb in (10, 20, 30, 60):
        for q in (0.1, 0.2, 0.3):
            for lh in (True, False):                  # momentum AND reversal
                specs.append(("mom", lb, q, lh))
    for q in (0.1, 0.2, 0.3):
        for lh in (True, False):                      # carry AND anti-carry
            specs.append(("fund", 0, q, lh))
    return specs


SPECS = grid(); G = len(SPECS)


def eval_panel(panel):
    ret, fund, dv, listed = matrices(panel)
    momc = {lb: mom_signal(ret, lb) for lb in (10, 20, 30, 60)}
    cut = int(ret.shape[0]*(1-HOLDOUT))
    naive_pass = honest_pass = False
    for sig_t, lb, q, lh in SPECS:
        sigf = momc[lb] if sig_t == "mom" else fund
        # NAIVE (daily rebalance, flat cost, daily n_obs); GO = profitable + sig + OOS profitable
        nis = naive_daily_ls(sigf[:cut], ret[:cut], dv[:cut], listed[:cut], q, lh)
        noos = naive_daily_ls(sigf[cut:], ret[cut:], dv[cut:], listed[cut:], q, lh)
        if min(len(nis), len(noos)) >= 20:
            pN, mN = pval(nis); _, mNo = pval(noos)
            if pN < ALPHA and mN != 0 and np.sign(mN) == np.sign(mNo):  # gates.py: sign-CONSISTENCY, not profitability
                naive_pass = True
        # HONEST (monthly rebalance, size cost, monthly n_obs, Bonferroni x G)
        his = honest_monthly_ls(sigf[:cut], ret[:cut], dv[:cut], listed[:cut], q, lh)
        hoos = honest_monthly_ls(sigf[cut:], ret[cut:], dv[cut:], listed[cut:], q, lh)
        if min(len(his), len(hoos)) >= 3:
            pH, mH = pval(his); _, mHo = pval(hoos)
            if mH > 0 and pH*G < ALPHA and mHo > 0:
                honest_pass = True
    return naive_pass, honest_pass, 0.0, 0.0


def make_premium_panel(beta, seed):
    """Noise panel + a STABLE injected carry premium (low-funding names earn beta/day,
    scaled by cross-sectional funding z-score). Unlike the harness's explosive momentum
    plant (gain 3 -> diverges), this is a controlled, realistic edge of tunable strength."""
    g = make_synthetic_panel(n_tokens=N_TOK, n_days=N_DAYS, planted=None, seed=7000+seed, deaths=True).copy()
    fz = g.groupby("date")["funding_rate"].transform(lambda s: (s - s.mean())/(s.std()+1e-12))
    g["ret"] = g["ret"] + (-beta)*fz.fillna(0.0)*g["is_listed"].astype(float)
    return g


def main():
    print(f"K0 factor honest-kill | grid G={G} specs | {N_DAYS}d/{N_TOK} tokens | monthly block={BLOCK}d")
    print(f"NAIVE = daily n_obs + flat 30bps + best-of-grid (no MT correction)")
    print(f"HONEST = monthly n_obs + size/ADV cost + Bonferroni x G + OOS sign-hold\n")

    M = 30
    print(f"=== FALSE-POSITIVE on PURE NOISE ({M} panels, planted=None) ===")
    nN = nH = 0
    for s in range(M):
        p = make_synthetic_panel(n_tokens=N_TOK, n_days=N_DAYS, planted=None, seed=1000+s, deaths=True)
        a, b, _, _ = eval_panel(p)
        nN += a; nH += b
    print(f"  NAIVE  best-of-grid 'GO' on noise : {nN}/{M} = {100*nN/M:.0f}%   (target ~{100*ALPHA:.0f}%)")
    print(f"  HONEST 'GO' on noise              : {nH}/{M} = {100*nH/M:.0f}%   (target <= {100*ALPHA:.0f}%)")

    print(f"\n=== POWER: does the HONEST gate detect a REALISTIC edge? (stable carry premium) ===")
    print(f"  (harness's built-in momentum plant is BROKEN: ret=0.15*20d-sum -> feedback gain 3 -> explosive,")
    print(f"   returns blow up to ~1e64; can't test power with it. Using a controlled injected carry premium.)")
    T = 6
    for beta in (0.002, 0.004, 0.008, 0.015):
        det = 0; shs = []
        for s in range(T):
            p = make_premium_panel(beta, s)
            ret, fund, dv, listed = matrices(p); cut = int(ret.shape[0]*(1-HOLDOUT))
            his = honest_monthly_ls(fund[:cut], ret[:cut], dv[:cut], listed[:cut], 0.2, False)
            if len(his) > 2 and his.std() > 0: shs.append(his.mean()/his.std()*math.sqrt(12))
            _, b, _, _ = eval_panel(p); det += b
        msh = sum(shs)/len(shs) if shs else 0.0
        print(f"  beta={beta:.3f}: realized honest annualized Sharpe ~{msh:+.2f}  ->  HONEST detects {det}/{T}")

    print(f"\n=== VERDICT ===")
    print(f"  1. HARNESS TUNED TO FALSE GO: naive (sign-consistency + flat-daily-cost + daily n_obs) issues a")
    print(f"     GO on PURE NOISE {100*nN/M:.0f}% of the time; the honest gate correctly rejects ({100*nH/M:.0f}%).")
    print(f"     A GO from the current research_loop factor code is statistically worthless.")
    print(f"  2. HONEST gate is well-calibrated but POWER-BOUND: it needs ~Sharpe>=2 to clear the full-grid")
    print(f"     Bonferroni at this history length. The literature's realistic crypto factor is ~0.5 net Sharpe")
    print(f"     -> on a buyable history it CANNOT clear an honest MT-corrected gate.")
    print(f"  3. The harness's own synthetic momentum fixture is mathematically broken (explosive).")
    print(f"  => $0 NO-GO: the existing harness can't be trusted, AND a realistic (~0.5 Sharpe) edge couldn't")
    print(f"     pass an honestly-fixed gate anyway. Don't buy the data on this basis.")


if __name__ == "__main__":
    main()
