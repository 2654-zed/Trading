"""H3: distributional differences between flagged and unflagged opportunities.

Spec hypothesis (LAYER3_TRADING_EXPERIMENT.md §Hypotheses):
    H3: distributional differences between flagged and unflagged opportunities?

Per-field comparisons:
  - Numeric (margin_gross_bps, expected_gain_usd, depth_notional_usd):
      mean / median / std / p5 / p95 per group + Mann-Whitney U test (two-sided).
      Effect size: rank-biserial correlation, easier to interpret than p alone.
  - Categorical (pool_protocols combo, borrowed_token):
      frequency table per group + Chi-square test of independence (when sample
      sizes permit; degenerate cases reported as `chi2=null, reason='...'`).

Groups:
  - hard_flagged: records with hard_flagged=true
  - soft_flagged_only: records with soft_flagged=true AND hard_flagged=false
  - unflagged: records with both flags false
  - flagged (any): hard or soft (the most natural H3 contrast)

Degraded records are EXCLUDED from H3 comparisons (same reasoning as H2:
filter results are unreliable against stale L3 corpus).
"""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import stats


def _partition(records: list[dict]) -> dict[str, list[dict]]:
    eligible = [r for r in records if not r.get("layer3_stale")]
    return {
        "hard_flagged": [r for r in eligible if r.get("hard_flagged")],
        "soft_flagged_only": [r for r in eligible if r.get("soft_flagged") and not r.get("hard_flagged")],
        "unflagged": [r for r in eligible if not r.get("hard_flagged") and not r.get("soft_flagged")],
        "flagged_any": [r for r in eligible if r.get("hard_flagged") or r.get("soft_flagged")],
        "all_eligible": eligible,
    }


def _numeric_summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "std": None, "p5": None, "p95": None}
    arr = np.asarray(values, dtype=float)
    return {
        "n": len(values),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "std": float(arr.std(ddof=1)) if len(values) > 1 else 0.0,
        "p5": float(np.quantile(arr, 0.05)),
        "p95": float(np.quantile(arr, 0.95)),
    }


def _mann_whitney(a: list[float], b: list[float]) -> dict:
    """Mann-Whitney U two-sided test + rank-biserial effect size.

    scipy.stats.mannwhitneyu returns U_a = count of pairs (i, j) where a_i > b_j
    (resolving ties at half a count). Rank-biserial:
        r = 2 * U_a / (n_a * n_b) - 1
    Range: -1 (a always less than b) to +1 (a always greater than b); 0 = same.

    Degenerate cases (one group empty, all values identical) yield p=null.
    """
    if not a or not b:
        return {"p_value": None, "u_stat": None, "rank_biserial": None,
                "reason": "one or both groups empty"}
    if all(v == a[0] for v in a) and all(v == b[0] for v in b) and a[0] == b[0]:
        return {"p_value": None, "u_stat": None, "rank_biserial": None,
                "reason": "all values identical"}
    try:
        u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        rb = (2.0 * u) / (len(a) * len(b)) - 1.0
        return {"p_value": float(p), "u_stat": float(u), "rank_biserial": float(rb),
                "reason": None}
    except ValueError as e:
        return {"p_value": None, "u_stat": None, "rank_biserial": None,
                "reason": f"scipy ValueError: {e}"}


def _frequency_table(records: list[dict], extractor) -> Counter:
    return Counter(extractor(r) for r in records)


def _chi_square_independence(table: dict[str, list[int]]) -> dict:
    """Chi-square test of independence over a {category: counts_per_group} table.

    `table` example: {"WETH/USDC": [3, 5], "USDC/AERO": [1, 2]}
    where each list is [group_a_count, group_b_count].

    Returns p-value, chi2 statistic, dof. Degenerate cases (any group empty,
    fewer than 2 categories with counts) yield p=null with a reason string.
    """
    if not table:
        return {"p_value": None, "chi2": None, "dof": None, "reason": "empty table"}
    rows = [list(map(int, counts)) for counts in table.values()]
    arr = np.asarray(rows)
    if arr.shape[0] < 2 or arr.shape[1] < 2:
        return {"p_value": None, "chi2": None, "dof": None,
                "reason": f"contingency table too small: shape={arr.shape}"}
    if arr.sum(axis=0).min() == 0 or arr.sum(axis=1).min() == 0:
        return {"p_value": None, "chi2": None, "dof": None,
                "reason": "one or more groups/categories has zero total"}
    try:
        chi2, p, dof, _ = stats.chi2_contingency(arr)
        return {"p_value": float(p), "chi2": float(chi2), "dof": int(dof), "reason": None}
    except ValueError as e:
        return {"p_value": None, "chi2": None, "dof": None,
                "reason": f"scipy ValueError: {e}"}


def _proto_combo(record: dict) -> str:
    protos = record.get("pool_protocols") or []
    if not protos:
        return "<unknown>"
    return " + ".join(sorted(str(p) for p in protos))


def _borrowed_token(record: dict) -> str:
    tokens = (record.get("path") or {}).get("tokens") or []
    return str(tokens[0]).lower() if tokens else "<unknown>"


def compute(records: list[dict]) -> dict:
    """Compute H3 distributional comparisons. Pure function over loaded records."""
    parts = _partition(records)
    flagged = parts["flagged_any"]
    unflagged = parts["unflagged"]

    def _vec(group: list[dict], field: str) -> list[float]:
        return [float(r.get(field, 0)) for r in group]

    numeric_results: dict = {}
    for field in ("margin_gross_bps", "expected_gain_usd", "depth_notional_usd"):
        flagged_vals = _vec(flagged, field)
        unflagged_vals = _vec(unflagged, field)
        numeric_results[field] = {
            "flagged": _numeric_summary(flagged_vals),
            "unflagged": _numeric_summary(unflagged_vals),
            "mann_whitney": _mann_whitney(flagged_vals, unflagged_vals),
        }

    # Categorical: protocol combos + borrowed tokens.
    proto_table: dict[str, list[int]] = {}
    flagged_proto = _frequency_table(flagged, _proto_combo)
    unflagged_proto = _frequency_table(unflagged, _proto_combo)
    for combo in sorted(set(flagged_proto) | set(unflagged_proto)):
        proto_table[combo] = [flagged_proto.get(combo, 0), unflagged_proto.get(combo, 0)]

    token_table: dict[str, list[int]] = {}
    flagged_tok = _frequency_table(flagged, _borrowed_token)
    unflagged_tok = _frequency_table(unflagged, _borrowed_token)
    for tok in sorted(set(flagged_tok) | set(unflagged_tok)):
        token_table[tok] = [flagged_tok.get(tok, 0), unflagged_tok.get(tok, 0)]

    return {
        "group_sizes": {k: len(v) for k, v in parts.items()},
        "numeric_comparisons": numeric_results,
        "categorical_comparisons": {
            "pool_protocols": {
                "table": proto_table,
                "chi_square": _chi_square_independence(proto_table),
            },
            "borrowed_token": {
                "table": token_table,
                "chi_square": _chi_square_independence(token_table),
            },
        },
    }


def plot_margin_distributions(records: list[dict], out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parts = _partition(records)
    flagged = [r.get("margin_gross_bps", 0) for r in parts["flagged_any"]]
    unflagged = [r.get("margin_gross_bps", 0) for r in parts["unflagged"]]

    fig, ax = plt.subplots(figsize=(10, 5))
    if flagged:
        ax.hist(flagged, bins=30, color="#d62728", alpha=0.5,
                label=f"flagged (n={len(flagged)})", edgecolor="black", linewidth=0.4)
    if unflagged:
        ax.hist(unflagged, bins=30, color="#1f77b4", alpha=0.5,
                label=f"unflagged (n={len(unflagged)})", edgecolor="black", linewidth=0.4)
    ax.axvline(30, linestyle="--", color="black", alpha=0.4, label="0.30% floor")
    ax.set_xlabel("gross margin (bps)")
    ax.set_ylabel("opportunity count")
    ax.set_title("H3: margin distribution — flagged vs. unflagged")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_protocol_distribution(records: list[dict], out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    parts = _partition(records)
    flagged_freq = _frequency_table(parts["flagged_any"], _proto_combo)
    unflagged_freq = _frequency_table(parts["unflagged"], _proto_combo)
    combos = sorted(set(flagged_freq) | set(unflagged_freq))
    if not combos:
        # Empty plot still produced for report consistency
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, "no records", ha="center", va="center", transform=ax.transAxes)
        ax.set_title("H3: pool protocol distribution — flagged vs. unflagged")
        fig.tight_layout()
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        return

    x = np.arange(len(combos))
    fig, ax = plt.subplots(figsize=(max(8, len(combos) * 1.2), 5))
    width = 0.4
    ax.bar(x - width / 2, [flagged_freq.get(c, 0) for c in combos], width,
           color="#d62728", alpha=0.8, label="flagged", edgecolor="black", linewidth=0.4)
    ax.bar(x + width / 2, [unflagged_freq.get(c, 0) for c in combos], width,
           color="#1f77b4", alpha=0.8, label="unflagged", edgecolor="black", linewidth=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels(combos, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("opportunity count")
    ax.set_title("H3: pool protocol distribution — flagged vs. unflagged")
    ax.legend(loc="best")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


__all__ = [
    "compute",
    "plot_margin_distributions",
    "plot_protocol_distribution",
]
