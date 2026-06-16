"""Token-panel data layer for the cross-asset FACTOR test (the financier's
mid/long-horizon pivot — a DIFFERENT game from the mempool MEV work).

The REQUIRED_COLUMNS below ARE the data contract the purchased dataset must
satisfy. The synthetic generator deliberately includes tokens that DIE
(delist / go to ~0) so survivorship handling is testable now, before any real
data is bought.

Convention (no look-ahead): `ret[t]` is the FORWARD return over (t → t+1).
A portfolio formed at date t using only information ≤ t earns `ret[t]`.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# The data contract. A vendor dataset MUST provide these (point-in-time).
REQUIRED_COLUMNS = ["date", "token", "ret", "funding_rate",
                    "dollar_volume", "is_listed"]


def validate_panel(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"panel missing required columns: {missing}")


def load_panel(path: str) -> pd.DataFrame:
    df = (pd.read_parquet(path) if path.endswith(".parquet")
          else pd.read_csv(path, parse_dates=["date"]))
    validate_panel(df)
    return df


def split_by_date(df: pd.DataFrame, holdout_frac: float = 0.34
                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split — the LATEST dates are the sealed holdout."""
    dates = np.sort(df["date"].unique())
    cut = dates[int(len(dates) * (1 - holdout_frac))]
    return df[df["date"] < cut].copy(), df[df["date"] >= cut].copy()


def make_synthetic_panel(n_tokens: int = 120, n_days: int = 400,
                         planted: Optional[str] = None, seed: int = 0,
                         deaths: bool = True) -> pd.DataFrame:
    """Synthetic long-format panel for tests.

    planted: None (pure noise), 'momentum' (forward ret loads on trailing
    return), or 'carry' (forward ret loads NEGATIVELY on funding rate).
    `deaths`: a fraction of tokens delist mid-sample with a -90% death return —
    so a survivorship-FREE panel can short them and a biased panel can't.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D")
    toks = [f"tok{i:03d}" for i in range(n_tokens)]
    base_vol = rng.lognormal(15, 1.5, n_tokens)            # $ volume per token

    # death day per token (or None)
    death_day = {}
    if deaths:
        dying = rng.choice(n_tokens, size=n_tokens // 5, replace=False)
        for i in dying:
            death_day[i] = int(rng.integers(n_days // 3, n_days - 5))

    rows = []
    # carry signal: persistent per-token funding level + noise
    fund_level = rng.normal(0, 0.0005, n_tokens)
    # store ret history per token for momentum planting
    ret_hist = {i: [] for i in range(n_tokens)}
    for d, date in enumerate(dates):
        for i, tok in enumerate(toks):
            dead = i in death_day and d >= death_day[i]
            listed = not dead
            funding = fund_level[i] + rng.normal(0, 0.0003)
            noise = rng.normal(0, 0.04)
            ret = noise
            if i in death_day and d == death_day[i]:
                ret = -0.90                                # the death return
            elif planted == "momentum" and d >= 20:
                trail = float(np.sum(ret_hist[i][-20:]))
                ret = 0.15 * trail + noise                 # momentum loads
            elif planted == "carry":
                ret = -8.0 * funding + noise               # high funding -> low fwd ret
            ret_hist[i].append(ret if listed else 0.0)
            rows.append((date, tok, ret if listed else 0.0, funding,
                         float(base_vol[i]) * (0 if dead else 1), listed))
    return pd.DataFrame(rows, columns=REQUIRED_COLUMNS)


def make_survivorship_biased(df: pd.DataFrame) -> pd.DataFrame:
    """Return the panel with EVERY token that ever delisted removed entirely —
    the classic mistake. Used to demonstrate the bias flips results."""
    ever_died = df.loc[~df["is_listed"], "token"].unique()
    return df[~df["token"].isin(ever_died)].copy()


__all__ = ["REQUIRED_COLUMNS", "validate_panel", "load_panel", "split_by_date",
           "make_synthetic_panel", "make_survivorship_biased"]
