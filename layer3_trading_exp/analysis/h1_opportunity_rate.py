"""H1: opportunities per day at >= 0.3% gross margin.

Spec hypothesis (LAYER3_TRADING_EXPERIMENT.md §Hypotheses):
    H1: >= 1,000 opportunities/day at >= 0.3% gross margin?

The detector already enforces the 0.3% margin floor at emission time, so every
record in the JSONL log already meets the threshold. This module focuses on
counting/distribution + per-day rate confidence intervals.

Three count modes are reported because the dataset has structurally repeated
emissions (3-5 WS double-fires per block + arb persistence):
  - emissions: total log records (= total emissions)
  - unique:    deduped by (pool_pair, borrowed_token) per UTC day
  - episodes:  emissions where the opportunity_key was absent in the prior
               block, then present (counts arrival events, not steady state)

H1 is checked against all three; Jason chooses which is the meaningful one in
the report annotation.

Confidence interval: bootstrap with fixed seed for reproducibility. We resample
the per-day counts vector 1000 times, compute the mean each time, take the
2.5th/97.5th percentiles. Bootstrap is robust to the dependence structure
(serial correlation across blocks within a day) that violates Poisson CI's
independence assumption.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional

import numpy as np

from . import _loader


H1_THRESHOLD_OPPS_PER_DAY = 1_000
BOOTSTRAP_SEED = 42
BOOTSTRAP_RESAMPLES = 1_000


def _count_per_day(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        d = _loader._record_date(r)
        if d is None:
            continue
        counts[d] = counts.get(d, 0) + 1
    return counts


def _episode_count(records: list[dict]) -> dict[str, int]:
    """Count opportunity arrival events per day.

    For each unique opportunity_key, walk the sorted block_numbers it fired in.
    A contiguous run of blocks (each block_n+1 differs from block_n by exactly
    1) is ONE episode. A gap of >1 block between consecutive fires means the
    opportunity disappeared and reappeared — counts as a new episode.

    Note: the JSONL log records only fires, so we can't distinguish "block had
    no fire for any pair" from "block was never observed". We use the gap-of-
    block-numbers heuristic, which matches OpportunityAggregator's behavior
    when the runner observes every block (the common case on a healthy WS).
    """
    by_day = _loader.group_by_day(records)
    out: dict[str, int] = {}
    for day, day_records in by_day.items():
        blocks_by_key: dict[tuple, list[int]] = {}
        for r in day_records:
            key = _loader._record_key(r)
            blk = r.get("block_number", 0)
            blocks_by_key.setdefault(key, []).append(blk)
        episodes = 0
        for key, blocks in blocks_by_key.items():
            unique_blocks = sorted(set(blocks))
            if not unique_blocks:
                continue
            # First fire always starts an episode; each subsequent gap of
            # >1 block starts another.
            episodes += 1
            for prev, cur in zip(unique_blocks, unique_blocks[1:]):
                if cur - prev > 1:
                    episodes += 1
        out[day] = episodes
    return out


def _bootstrap_mean_ci(
    values: list[int],
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Bootstrap CI for the mean of a count vector.

    Returns (mean, ci_low, ci_high). Empty input returns (0.0, 0.0, 0.0) —
    callers should check `len(values) > 0` if that distinction matters.
    """
    if not values:
        return (0.0, 0.0, 0.0)
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    sample_size = len(arr)
    means = np.empty(resamples, dtype=float)
    for i in range(resamples):
        idx = rng.integers(0, sample_size, size=sample_size)
        means[i] = arr[idx].mean()
    alpha = 1.0 - confidence
    low = float(np.quantile(means, alpha / 2))
    high = float(np.quantile(means, 1 - alpha / 2))
    return (float(arr.mean()), low, high)


def compute(records: list[dict]) -> dict:
    """Compute H1 statistics.

    Pure function: input is the loaded record list, output is a dict with
    counts, per-day timeseries, bootstrap CIs, and threshold checks for each
    of the three count modes (emissions / unique / episodes).
    """
    emission_records = list(records)
    unique_records = _loader.dedup_unique_per_day(records)

    emission_per_day = _count_per_day(emission_records)
    unique_per_day = _count_per_day(unique_records)
    episode_per_day = _episode_count(emission_records)

    margins = [r.get("margin_gross_bps", 0) for r in records]

    days = sorted(set(emission_per_day) | set(unique_per_day) | set(episode_per_day))

    def _pad(d: dict[str, int]) -> list[int]:
        return [d.get(day, 0) for day in days]

    emission_series = _pad(emission_per_day)
    unique_series = _pad(unique_per_day)
    episode_series = _pad(episode_per_day)

    em_mean, em_lo, em_hi = _bootstrap_mean_ci(emission_series)
    un_mean, un_lo, un_hi = _bootstrap_mean_ci(unique_series)
    ep_mean, ep_lo, ep_hi = _bootstrap_mean_ci(episode_series)

    return {
        "h1_threshold_opps_per_day": H1_THRESHOLD_OPPS_PER_DAY,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "n_days_observed": len(days),
        "days": days,

        "emissions_total": len(emission_records),
        "emissions_per_day": emission_series,
        "emissions_mean_per_day": em_mean,
        "emissions_ci_low": em_lo,
        "emissions_ci_high": em_hi,
        "h1_emissions_threshold_met": em_mean >= H1_THRESHOLD_OPPS_PER_DAY,

        "unique_total": len(unique_records),
        "unique_per_day": unique_series,
        "unique_mean_per_day": un_mean,
        "unique_ci_low": un_lo,
        "unique_ci_high": un_hi,
        "h1_unique_threshold_met": un_mean >= H1_THRESHOLD_OPPS_PER_DAY,

        "episodes_total": sum(episode_series),
        "episodes_per_day": episode_series,
        "episodes_mean_per_day": ep_mean,
        "episodes_ci_low": ep_lo,
        "episodes_ci_high": ep_hi,
        "h1_episodes_threshold_met": ep_mean >= H1_THRESHOLD_OPPS_PER_DAY,

        "margin_bps_min": min(margins) if margins else None,
        "margin_bps_p50": statistics.median(margins) if margins else None,
        "margin_bps_p95": float(np.quantile(margins, 0.95)) if margins else None,
        "margin_bps_max": max(margins) if margins else None,
    }


def plot_per_day_counts(stats: dict, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")  # headless, deterministic, no GUI
    import matplotlib.pyplot as plt

    days = stats["days"]
    fig, ax = plt.subplots(figsize=(10, 5))
    if days:
        x = np.arange(len(days))
        ax.plot(x, stats["emissions_per_day"], marker="o", label="emissions", color="#888888")
        ax.plot(x, stats["unique_per_day"], marker="s", label="unique (per day)", color="#1f77b4")
        ax.plot(x, stats["episodes_per_day"], marker="^", label="episodes", color="#2ca02c")
        ax.set_xticks(x)
        ax.set_xticklabels(days, rotation=45, ha="right")
    ax.axhline(stats["h1_threshold_opps_per_day"], linestyle="--", color="red", alpha=0.6,
               label=f"H1 threshold (>= {stats['h1_threshold_opps_per_day']:,}/day)")
    ax.set_ylabel("opportunities per day")
    ax.set_title("H1: per-day opportunity counts (3 modes)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_margin_histogram(records: list[dict], out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    margins = [r.get("margin_gross_bps", 0) for r in records]
    fig, ax = plt.subplots(figsize=(10, 5))
    if margins:
        ax.hist(margins, bins=40, color="#1f77b4", alpha=0.8, edgecolor="black", linewidth=0.4)
    ax.axvline(30, linestyle="--", color="red", alpha=0.6, label="0.30% floor (30 bps)")
    ax.set_xlabel("gross margin (bps)")
    ax.set_ylabel("opportunity count")
    ax.set_title("H1: gross margin distribution across run")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


__all__ = [
    "compute",
    "plot_per_day_counts",
    "plot_margin_histogram",
    "H1_THRESHOLD_OPPS_PER_DAY",
    "BOOTSTRAP_SEED",
    "BOOTSTRAP_RESAMPLES",
]
