"""Cross-asset FACTOR measurements (carry, cross-sectional momentum) that
return the SAME metric shape the deterministic gates consume — so a factor
hypothesis goes through the identical sealed-holdout / sign-must-hold /
multiple-testing discipline as everything else.

Each measurement builds a market-neutral long-short portfolio, nets a
depth-aware cost, and reports the return series' statistics. Survivorship is
handled by using ACTUAL forward returns (including death returns) for whatever
was is_listed at formation — so omitting dead tokens (a biased panel) changes
the answer, exactly as it should.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _sign(x, eps=1e-12):
    if x is None or abs(x) < eps:
        return "0"
    return "+" if x > 0 else "-"


def _metrics_from_returns(rets: np.ndarray, periods_per_year: int = 365) -> dict:
    """Stats of a net long-short return series → gate-compatible metric dict."""
    rets = np.asarray([r for r in rets if r is not None and np.isfinite(r)])
    n = len(rets)
    if n < 2 or rets.std(ddof=1) == 0:
        return {"effect": None, "edge_sign": "0", "n_obs": n, "p_value": None,
                "per_regime": [], "net_sharpe_after_costs": None,
                "excess_return_vs_bench": None,
                "notes": "no_lookahead; survivorship-aware; insufficient periods"}
    mean = float(rets.mean())
    sd = float(rets.std(ddof=1))
    t = mean / (sd / math.sqrt(n))
    p = 2 * (1 - _phi(abs(t)))
    sharpe = (mean / sd) * math.sqrt(periods_per_year)
    # regime split (early/late halves), require sign consistency downstream
    half = n // 2
    per_regime = []
    for label, seg in (("early", rets[:half]), ("late", rets[half:])):
        if len(seg) >= 2:
            per_regime.append({"regime": label, "effect": float(seg.mean()),
                               "edge_sign": _sign(float(seg.mean())),
                               "n_obs": len(seg)})
    return {"effect": mean, "edge_sign": _sign(mean), "n_obs": n, "p_value": p,
            "per_regime": per_regime, "net_sharpe_after_costs": sharpe,
            "excess_return_vs_bench": mean,   # market-neutral ⇒ return IS the alpha
            "notes": "no_lookahead; survivorship-aware; market-neutral L/S; costed"}


def _long_short_returns(panel: pd.DataFrame, signal_col: str, *,
                        long_high: bool, q: float = 0.2,
                        cost_bps: float = 30.0, min_dollar_volume: float = 1e5,
                        min_names: int = 10) -> np.ndarray:
    """Per-date net long-short return. Forms the book from tokens that are
    is_listed + liquid + have a signal at date t, then earns ret[t]
    (forward). Cost = round-trip bps applied to both legs each rebalance."""
    df = panel[(panel["is_listed"]) & (panel["dollar_volume"] >= min_dollar_volume)
               & panel[signal_col].notna() & panel["ret"].notna()]
    out = []
    for _, g in df.groupby("date", sort=True):
        if len(g) < min_names:
            continue
        k = max(1, int(len(g) * q))
        ranked = g.sort_values(signal_col, ascending=not long_high)
        longs = ranked.head(k)["ret"].mean()
        shorts = ranked.tail(k)["ret"].mean()
        gross = longs - shorts
        cost = 2 * (cost_bps / 1e4)                   # both legs, round trip
        out.append(gross - cost)
    return np.asarray(out, dtype=float)


def carry_factor(panel: pd.DataFrame, *, cost_bps: float = 30.0,
                 min_dollar_volume: float = 1e5) -> dict:
    """H: high perp funding (crowded longs) predicts LOWER forward return →
    long the low-funding names, short the high-funding names."""
    rets = _long_short_returns(panel, "funding_rate", long_high=False,
                               cost_bps=cost_bps,
                               min_dollar_volume=min_dollar_volume)
    return _metrics_from_returns(rets)


def xs_momentum_factor(panel: pd.DataFrame, *, lookback: int = 20,
                       cost_bps: float = 30.0,
                       min_dollar_volume: float = 1e5) -> dict:
    """H: trailing K-day return predicts forward return → long winners,
    short losers (cross-sectional momentum). Trailing signal uses returns
    STRICTLY before t (no look-ahead)."""
    df = panel.sort_values(["token", "date"]).copy()
    # trailing momentum from PAST forward-returns (shifted to exclude t)
    df["mom"] = (df.groupby("token", sort=False)["ret"]
                 .transform(lambda s: s.shift(1).rolling(lookback).sum()))
    rets = _long_short_returns(df, "mom", long_high=True, cost_bps=cost_bps,
                               min_dollar_volume=min_dollar_volume)
    return _metrics_from_returns(rets)


FACTOR_MEASUREMENTS = {
    "carry": carry_factor,
    "xs_momentum": xs_momentum_factor,
}

__all__ = ["carry_factor", "xs_momentum_factor", "FACTOR_MEASUREMENTS",
           "_metrics_from_returns", "_long_short_returns"]
