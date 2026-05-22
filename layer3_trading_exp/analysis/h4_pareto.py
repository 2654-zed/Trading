"""H4 (Phase 2): Pareto concentration over (chain-pair, token) combinations.

Hypothesis (`STRATEGY_STATE.md`):
    H4: Cross-chain opportunities concentrate on a small subset of the
    chain × token matrix — ≥80% of opportunities involve ≤20% of the
    (chain-pair, token) combinations.

Falsification: top-20% combinations account for <40% of opportunities
(i.e. effectively no concentration).

The bucket key is `(src_chain, dst_chain, borrow_token_symbol)` — the
minimal observable for "which route did this opportunity use." Mid-token
isn't included because (src=base, dst=arb, borrow=USDC, mid=WETH) and
(src=base, dst=arb, borrow=USDC, mid=USDT) are conceptually the same
"route slot"; refining at the analysis layer is easy if H4 is supported.

Like H1', this operates on JSONL records produced by sub-phase 2.5's v2
schema. v1 records (intra-chain only) are excluded.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import _loader


H4_PARETO_TOP_FRACTION = 0.20         # "top 20% of combos"
H4_PARETO_COVERAGE_THRESHOLD = 0.80   # "covers >=80% of opps"
H4_FALSIFICATION_COVERAGE = 0.40      # ">=80/20 falsified if top-20% covers <40%"


def _is_cross_chain(record: dict) -> bool:
    return bool(record.get("bridge_legs"))


def _bucket_key(record: dict) -> tuple[str, str, str]:
    """(src_chain, dst_chain, borrow_token_symbol) bucket key."""
    legs = record.get("bridge_legs") or []
    if not legs:
        return ("", "", "")
    src = str(legs[0].get("src_chain", ""))
    dst = str(legs[0].get("dst_chain", ""))
    # Use the second bridge leg's token (the borrow return-hop token)
    # because the outbound leg's token is the mid token. Fall back to
    # path.tokens[0] if only one bridge leg present (defensive).
    if len(legs) >= 2:
        token = str(legs[1].get("token", ""))
    else:
        tokens = (record.get("path") or {}).get("tokens") or []
        token = str(tokens[0]) if tokens else ""
    return (src, dst, token)


def _bucket_label(key: tuple[str, str, str]) -> str:
    src, dst, token = key
    return f"{src}->{dst}:{token}"


def compute(records: list[dict]) -> dict:
    """Compute H4 Pareto statistics over unique cross-chain opportunities.

    Returns a dict carrying:
      - bucket-level counts
      - cumulative coverage curve sorted by descending count
      - top-N% coverage at the 20%-cutoff and the falsification point
      - threshold checks
    """
    # Use unique-per-day records as the population — emission counts are
    # noisy from WS double-fires and arb persistence.
    cross_records = [r for r in records if _is_cross_chain(r)]

    # Per-day dedup at the bucket level produces "unique opportunity events"
    # per (day, src, dst, token) so we don't double-count the same route's
    # repeated emissions within one day.
    seen: set[tuple[str, tuple]] = set()
    unique_events: list[tuple[str, tuple[str, str, str]]] = []
    for r in cross_records:
        d = _loader._record_date(r)
        if d is None:
            continue
        key = _bucket_key(r)
        sig = (d, key)
        if sig in seen:
            continue
        seen.add(sig)
        unique_events.append((d, key))

    # Aggregate counts per bucket.
    counts: dict[tuple[str, str, str], int] = {}
    for _, key in unique_events:
        counts[key] = counts.get(key, 0) + 1

    sorted_buckets = sorted(counts.items(), key=lambda kv: -kv[1])
    total = sum(counts.values())
    n_buckets = len(sorted_buckets)

    # Cumulative coverage curve.
    cumulative_curve: list[dict] = []
    running = 0
    for i, (key, n) in enumerate(sorted_buckets, start=1):
        running += n
        cumulative_curve.append({
            "rank": i,
            "bucket": _bucket_label(key),
            "count": n,
            "cumulative_count": running,
            "cumulative_fraction": running / total if total > 0 else 0.0,
            "rank_fraction": i / n_buckets if n_buckets > 0 else 0.0,
        })

    # Coverage at the top-20% cutoff.
    if n_buckets > 0:
        top_n = max(1, int(round(n_buckets * H4_PARETO_TOP_FRACTION)))
        top_count = sum(n for _, n in sorted_buckets[:top_n])
        top_coverage = top_count / total if total > 0 else 0.0
    else:
        top_n = 0
        top_coverage = 0.0

    return {
        "h4_pareto_top_fraction": H4_PARETO_TOP_FRACTION,
        "h4_pareto_coverage_threshold": H4_PARETO_COVERAGE_THRESHOLD,
        "h4_falsification_coverage": H4_FALSIFICATION_COVERAGE,

        "n_unique_buckets": n_buckets,
        "n_unique_events": total,
        "top_n_buckets": top_n,
        "top_n_coverage": top_coverage,
        "h4_supported": top_coverage >= H4_PARETO_COVERAGE_THRESHOLD,
        "h4_falsified":
            (n_buckets >= 5 and top_coverage < H4_FALSIFICATION_COVERAGE),

        "bucket_counts": {_bucket_label(k): n for k, n in sorted_buckets},
        "cumulative_curve": cumulative_curve,
    }


def plot_pareto_curve(stats: dict, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    curve = stats.get("cumulative_curve") or []
    fig, ax = plt.subplots(figsize=(10, 5))
    if curve:
        x = [c["rank_fraction"] for c in curve]
        y = [c["cumulative_fraction"] for c in curve]
        ax.plot(x, y, marker="o", color="#1f77b4")
    ax.plot([0, 1], [0, 1], linestyle=":", color="gray", alpha=0.6,
            label="uniform distribution baseline")
    ax.axvline(stats["h4_pareto_top_fraction"], linestyle="--",
               color="red", alpha=0.6,
               label=f"top {stats['h4_pareto_top_fraction']:.0%} cutoff")
    ax.axhline(stats["h4_pareto_coverage_threshold"], linestyle="--",
               color="green", alpha=0.6,
               label=f"H4 support threshold ({stats['h4_pareto_coverage_threshold']:.0%})")
    ax.set_xlabel("fraction of buckets (sorted by count, desc)")
    ax.set_ylabel("cumulative fraction of opportunities")
    ax.set_title("H4: Pareto concentration over (chain-pair, token) buckets")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_bucket_bars(stats: dict, out_path: Path, top_k: int = 12) -> None:
    """Bar chart of the top-K buckets by count."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    counts = stats.get("bucket_counts") or {}
    items = list(counts.items())[:top_k]
    fig, ax = plt.subplots(figsize=(11, 5))
    if items:
        labels = [k for k, _ in items]
        values = [v for _, v in items]
        ax.bar(np.arange(len(items)), values, color="#1f77b4",
               alpha=0.85, edgecolor="black", linewidth=0.4)
        ax.set_xticks(np.arange(len(items)))
        ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("unique opportunities (per day)")
    ax.set_title(f"H4: top {top_k} (chain-pair, token) buckets")
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


__all__ = [
    "compute",
    "plot_pareto_curve",
    "plot_bucket_bars",
    "H4_PARETO_TOP_FRACTION",
    "H4_PARETO_COVERAGE_THRESHOLD",
    "H4_FALSIFICATION_COVERAGE",
]
