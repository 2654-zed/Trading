"""H1' (Phase 2): cross-chain opportunities per day.

Re-formulation of H1 for Phase 2 (per `STRATEGY_STATE.md` after D-007's
H1 invalidation at current Base-only floors):

    H1' (Phase 2): ≥50 distinct cross-chain arbitrage opportunities per
    day exist on the Base–Arbitrum–Optimism triangle at ≥0.50% gross
    margin (after the Across point-estimate fee of 10 bps × 2 bridge
    legs).

Falsification: <10 distinct opportunities per day after 7 days.

`compute(records)` operates on JSONL records produced by sub-phase 2.5's
v2 schema. v1 records (Phase 1, intra-chain only) are excluded from H1'
counting because `bridge_legs` is absent or empty. The function still
returns a coherent dict for v1-only datasets — H1' simply reports 0
cross-chain opportunities, which is the correct answer.

Counts reported (same modes as H1 for symmetry):
  - emissions: total cross-chain log records
  - unique:    deduped by (chain_pair, borrow_token, path-pool-pair) per UTC day
  - episodes:  arrival events (gap-of-block-numbers heuristic, mirrors H1)
"""

from __future__ import annotations

import statistics
from pathlib import Path

import numpy as np

from . import _loader
from .h1_opportunity_rate import _bootstrap_mean_ci


H1_PRIME_THRESHOLD_OPPS_PER_DAY = 50
H1_PRIME_FALSIFICATION_OPPS_PER_DAY = 10
CROSS_CHAIN_MARGIN_FLOOR_BPS = 50   # 0.50%


def _is_cross_chain(record: dict) -> bool:
    bl = record.get("bridge_legs")
    return bool(bl)


def _cross_chain_key(record: dict) -> tuple[str, str, str]:
    """Stable identity for a cross-chain opportunity.

    Composed of: (src_chain, dst_chain, borrow_token, pool_pair). The
    pool_pair component ensures opportunities through different pools on
    the same chain pair count as distinct.

    For v1 records (no bridge_legs) this returns an empty tuple so they
    aggregate together — but the upstream filter excludes them anyway.
    """
    bridge_legs = record.get("bridge_legs") or []
    if not bridge_legs:
        return ("", "", "", "")
    # First bridge leg is the outbound hop (src → dst with mid_token).
    src = str(bridge_legs[0].get("src_chain", ""))
    dst = str(bridge_legs[0].get("dst_chain", ""))
    # Borrow token comes from path.tokens[0] (token_in of leg 0).
    tokens = (record.get("path") or {}).get("tokens") or []
    borrow = str(tokens[0]).lower() if tokens else ""
    pools = (record.get("path") or {}).get("pools") or []
    pool_pair = "|".join(sorted([str(p).lower() for p in pools[:2]]))
    return (src, dst, borrow, pool_pair)


def _count_per_day(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        d = _loader._record_date(r)
        if d is None:
            continue
        counts[d] = counts.get(d, 0) + 1
    return counts


def _dedup_unique_per_day(records: list[dict]) -> list[dict]:
    """Cross-chain-aware dedup: collapses by (day, cross_chain_key)."""
    seen: set[tuple[str, tuple]] = set()
    out: list[dict] = []
    for r in records:
        d = _loader._record_date(r)
        if d is None:
            continue
        key = (d, _cross_chain_key(r))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _episode_count(records: list[dict]) -> dict[str, int]:
    """Per-day episode count — same heuristic as h1_opportunity_rate."""
    out: dict[str, int] = {}
    by_day: dict[str, list[dict]] = {}
    for r in records:
        d = _loader._record_date(r)
        if d is None:
            continue
        by_day.setdefault(d, []).append(r)
    for day, day_records in by_day.items():
        blocks_by_key: dict[tuple, list[int]] = {}
        for r in day_records:
            key = _cross_chain_key(r)
            blk = r.get("block_number", 0)
            blocks_by_key.setdefault(key, []).append(blk)
        episodes = 0
        for blocks in blocks_by_key.values():
            unique_blocks = sorted(set(blocks))
            if not unique_blocks:
                continue
            episodes += 1
            for prev, cur in zip(unique_blocks, unique_blocks[1:]):
                if cur - prev > 1:
                    episodes += 1
        out[day] = episodes
    return out


def compute(records: list[dict]) -> dict:
    """Compute H1' statistics from the full record list.

    Records lacking `bridge_legs` (Phase 1 v1 records, intra-chain v2
    records) are filtered out — H1' is specifically about cross-chain
    opportunities. The function still returns coherent zero-count
    statistics when no cross-chain records exist in the dataset.
    """
    cross_records = [r for r in records if _is_cross_chain(r)]
    unique_records = _dedup_unique_per_day(cross_records)

    emission_per_day = _count_per_day(cross_records)
    unique_per_day = _count_per_day(unique_records)
    episode_per_day = _episode_count(cross_records)

    days = sorted(set(emission_per_day) | set(unique_per_day) | set(episode_per_day))

    def _pad(d: dict[str, int]) -> list[int]:
        return [d.get(day, 0) for day in days]

    emission_series = _pad(emission_per_day)
    unique_series = _pad(unique_per_day)
    episode_series = _pad(episode_per_day)

    em_mean, em_lo, em_hi = _bootstrap_mean_ci(emission_series)
    un_mean, un_lo, un_hi = _bootstrap_mean_ci(unique_series)
    ep_mean, ep_lo, ep_hi = _bootstrap_mean_ci(episode_series)

    # Net margin distribution — pre-haircut vs post-haircut.
    margins_post = [r.get("margin_gross_bps", 0) for r in cross_records]
    margins_raw = [r.get("gross_margin_raw_bps", r.get("margin_gross_bps", 0))
                   for r in cross_records]
    haircuts = [r.get("latency_drift_haircut_bps", 0) for r in cross_records]

    # Per-(chain-pair) breakdown.
    by_pair: dict[tuple[str, str], int] = {}
    for r in unique_records:
        legs = r.get("bridge_legs") or []
        if not legs:
            continue
        src = str(legs[0].get("src_chain", ""))
        dst = str(legs[0].get("dst_chain", ""))
        by_pair[(src, dst)] = by_pair.get((src, dst), 0) + 1
    # Render with stable key strings.
    chain_pair_unique = {f"{s}->{d}": n for (s, d), n in sorted(by_pair.items())}

    return {
        "h1_prime_threshold_opps_per_day": H1_PRIME_THRESHOLD_OPPS_PER_DAY,
        "h1_prime_falsification_threshold_opps_per_day":
            H1_PRIME_FALSIFICATION_OPPS_PER_DAY,
        "cross_chain_margin_floor_bps": CROSS_CHAIN_MARGIN_FLOOR_BPS,
        "n_days_observed": len(days),
        "days": days,

        "emissions_total": len(cross_records),
        "emissions_per_day": emission_series,
        "emissions_mean_per_day": em_mean,
        "emissions_ci_low": em_lo,
        "emissions_ci_high": em_hi,
        "h1_prime_emissions_threshold_met":
            em_mean >= H1_PRIME_THRESHOLD_OPPS_PER_DAY,

        "unique_total": len(unique_records),
        "unique_per_day": unique_series,
        "unique_mean_per_day": un_mean,
        "unique_ci_low": un_lo,
        "unique_ci_high": un_hi,
        "h1_prime_unique_threshold_met":
            un_mean >= H1_PRIME_THRESHOLD_OPPS_PER_DAY,
        "h1_prime_falsified":
            un_mean < H1_PRIME_FALSIFICATION_OPPS_PER_DAY and len(days) >= 7,

        "episodes_total": sum(episode_series),
        "episodes_per_day": episode_series,
        "episodes_mean_per_day": ep_mean,
        "episodes_ci_low": ep_lo,
        "episodes_ci_high": ep_hi,
        "h1_prime_episodes_threshold_met":
            ep_mean >= H1_PRIME_THRESHOLD_OPPS_PER_DAY,

        "chain_pair_unique_counts": chain_pair_unique,

        "margin_bps_post_haircut_min": min(margins_post) if margins_post else None,
        "margin_bps_post_haircut_p50":
            statistics.median(margins_post) if margins_post else None,
        "margin_bps_post_haircut_p95":
            float(np.quantile(margins_post, 0.95)) if margins_post else None,
        "margin_bps_post_haircut_max": max(margins_post) if margins_post else None,
        "margin_bps_raw_p50":
            statistics.median(margins_raw) if margins_raw else None,
        "haircut_bps_p50": statistics.median(haircuts) if haircuts else None,
        "haircut_bps_max": max(haircuts) if haircuts else None,
    }


def plot_per_day_counts(stats: dict, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    days = stats["days"]
    fig, ax = plt.subplots(figsize=(10, 5))
    if days:
        x = np.arange(len(days))
        ax.plot(x, stats["emissions_per_day"], marker="o",
                label="emissions", color="#888888")
        ax.plot(x, stats["unique_per_day"], marker="s",
                label="unique (per day)", color="#1f77b4")
        ax.plot(x, stats["episodes_per_day"], marker="^",
                label="episodes", color="#2ca02c")
        ax.set_xticks(x)
        ax.set_xticklabels(days, rotation=45, ha="right")
    ax.axhline(stats["h1_prime_threshold_opps_per_day"],
               linestyle="--", color="red", alpha=0.6,
               label=f"H1' threshold (>= {stats['h1_prime_threshold_opps_per_day']}/day)")
    ax.axhline(stats["h1_prime_falsification_threshold_opps_per_day"],
               linestyle=":", color="darkred", alpha=0.6,
               label=f"H1' falsified (< {stats['h1_prime_falsification_threshold_opps_per_day']}/day)")
    ax.set_ylabel("cross-chain opportunities per day")
    ax.set_title("H1': per-day cross-chain opportunity counts")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_chain_pair_distribution(stats: dict, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pairs = stats.get("chain_pair_unique_counts", {})
    fig, ax = plt.subplots(figsize=(10, 5))
    if pairs:
        keys = list(pairs.keys())
        vals = list(pairs.values())
        ax.bar(np.arange(len(keys)), vals, color="#1f77b4", alpha=0.85,
               edgecolor="black", linewidth=0.4)
        ax.set_xticks(np.arange(len(keys)))
        ax.set_xticklabels(keys, rotation=30, ha="right")
    ax.set_ylabel("unique opportunities (across whole run)")
    ax.set_title("H1': cross-chain opportunities by chain pair")
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


__all__ = [
    "compute",
    "plot_per_day_counts",
    "plot_chain_pair_distribution",
    "H1_PRIME_THRESHOLD_OPPS_PER_DAY",
    "H1_PRIME_FALSIFICATION_OPPS_PER_DAY",
    "CROSS_CHAIN_MARGIN_FLOOR_BPS",
]
