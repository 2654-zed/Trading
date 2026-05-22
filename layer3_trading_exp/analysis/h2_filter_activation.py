"""H2: does Layer 3's intelligence corpus flag >= 1% of detected opportunities?

Spec hypothesis (LAYER3_TRADING_EXPERIMENT.md §Hypotheses):
    H2: Does Layer 3 corpus flag >= 1% of opportunities?

Computation:
  - For each rule (1-13): fire_count, fire_rate (over non-degraded evals)
  - Aggregate flag rates: hard_flag_rate, soft_flag_rate, unflagged_rate
  - Co-occurrence: 13x13 matrix counting how often rule i and rule j fire on
    the same opportunity (diagonal = total fires for that rule)

Degraded evaluations are EXCLUDED from rate denominators. Per spec invariant
#8, evaluations against stale L3 data carry `layer3_stale=true` and are not
research-meaningful for H2 (the rules can't fire correctly against stale
intel). Degraded counts are reported separately in the same dict.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..schema import RULE_TIER


H2_THRESHOLD_FIRE_RATE = 0.01  # 1% of opportunities


def _rule_fired(record: dict, rule_id: int) -> bool:
    """True if any filter_results entry whose key matches rule_id has fired=True.

    Filter results in the JSONL are keyed `rule_{id}_{name}`. We accept any
    key matching the leading `rule_{id}_` prefix to remain robust to rule
    renames across runs (the schema's tier mapping is keyed by id, not name).
    """
    fr = record.get("filter_results")
    if not isinstance(fr, dict):
        return False
    prefix = f"rule_{rule_id}_"
    for key, value in fr.items():
        if not key.startswith(prefix):
            continue
        if isinstance(value, dict) and value.get("fired"):
            return True
    return False


def compute(records: list[dict]) -> dict:
    """Compute H2 statistics. Pure function over loaded records.

    Returns a dict with per-rule and aggregate flag rates plus the
    co-occurrence matrix. Degraded records are excluded from rate denominators.
    """
    total = len(records)
    degraded = sum(1 for r in records if r.get("layer3_stale"))
    eligible = [r for r in records if not r.get("layer3_stale")]
    n_eligible = len(eligible)

    # Per-rule fire counts and rates over eligible records.
    per_rule: dict[str, dict] = {}
    fire_matrix = np.zeros((13, 13), dtype=int)
    rule_fires_per_record: list[set[int]] = []
    for r in eligible:
        fired_set: set[int] = set()
        for rid in range(1, 14):
            if _rule_fired(r, rid):
                fired_set.add(rid)
        rule_fires_per_record.append(fired_set)
        for rid in fired_set:
            for rid2 in fired_set:
                fire_matrix[rid - 1, rid2 - 1] += 1

    for rid in range(1, 14):
        fires = sum(1 for s in rule_fires_per_record if rid in s)
        rate = (fires / n_eligible) if n_eligible else 0.0
        per_rule[f"rule_{rid}"] = {
            "rule_id": rid,
            "tier": RULE_TIER.get(rid),
            "fire_count": fires,
            "fire_rate": rate,
            "meets_h2_threshold": rate >= H2_THRESHOLD_FIRE_RATE,
        }

    hard_flagged = sum(1 for r in eligible if r.get("hard_flagged"))
    soft_flagged = sum(1 for r in eligible if r.get("soft_flagged"))
    soft_only = sum(1 for r in eligible if r.get("soft_flagged") and not r.get("hard_flagged"))
    unflagged = sum(1 for r in eligible if not r.get("hard_flagged") and not r.get("soft_flagged"))

    any_rule_meets = any(p["meets_h2_threshold"] for p in per_rule.values())

    return {
        "h2_threshold_fire_rate": H2_THRESHOLD_FIRE_RATE,
        "total_records": total,
        "degraded_records": degraded,
        "eligible_records": n_eligible,
        "hard_flagged_count": hard_flagged,
        "soft_flagged_count": soft_flagged,
        "soft_flagged_only_count": soft_only,
        "unflagged_count": unflagged,
        "hard_flag_rate": (hard_flagged / n_eligible) if n_eligible else 0.0,
        "soft_flag_rate_only": (soft_only / n_eligible) if n_eligible else 0.0,
        "unflagged_rate": (unflagged / n_eligible) if n_eligible else 0.0,
        "any_rule_meets_h2_threshold": any_rule_meets,
        "per_rule": per_rule,
        "co_occurrence_matrix": fire_matrix.tolist(),
    }


def plot_per_rule_fire_rates(stats: dict, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    per_rule = stats["per_rule"]
    rule_ids = list(range(1, 14))
    rates = [per_rule[f"rule_{rid}"]["fire_rate"] for rid in rule_ids]
    tiers = [per_rule[f"rule_{rid}"]["tier"] for rid in rule_ids]
    color_by_tier = {"A": "#d62728", "B": "#ff7f0e", None: "#7f7f7f"}
    colors = [color_by_tier.get(t, "#7f7f7f") for t in tiers]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(rule_ids, rates, color=colors, edgecolor="black", linewidth=0.5)
    ax.axhline(stats["h2_threshold_fire_rate"], linestyle="--", color="red", alpha=0.6,
               label=f"H2 threshold (>= {stats['h2_threshold_fire_rate']:.0%})")
    ax.set_xticks(rule_ids)
    ax.set_xticklabels([f"R{i}" for i in rule_ids])
    ax.set_xlabel("rule id")
    ax.set_ylabel("fire rate (eligible records)")
    ax.set_title(f"H2: per-rule fire rates  (n={stats['eligible_records']:,} eligible)")

    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor="#d62728", label="Tier A"),
        Patch(facecolor="#ff7f0e", label="Tier B"),
        Patch(facecolor="#7f7f7f", label="(no tier)"),
    ]
    ax.legend(handles=legend_handles + [
        plt.Line2D([], [], linestyle="--", color="red", alpha=0.6, label="1% threshold")
    ], loc="best")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_co_occurrence_heatmap(stats: dict, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matrix = np.asarray(stats["co_occurrence_matrix"])
    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(matrix, cmap="Reds", aspect="auto")
    ax.set_xticks(range(13))
    ax.set_yticks(range(13))
    ax.set_xticklabels([f"R{i+1}" for i in range(13)])
    ax.set_yticklabels([f"R{i+1}" for i in range(13)])
    ax.set_xlabel("rule j")
    ax.set_ylabel("rule i")
    ax.set_title("H2: rule co-occurrence (i, j fire on same opportunity)\n"
                 "diagonal = total fires for that rule")
    # Annotate counts in each cell.
    for i in range(13):
        for j in range(13):
            v = matrix[i, j]
            if v == 0:
                continue
            ax.text(j, i, str(v), ha="center", va="center",
                    color="white" if v > matrix.max() / 2 else "black",
                    fontsize=8)
    fig.colorbar(im, ax=ax, label="co-occurrence count")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


__all__ = [
    "compute",
    "plot_per_rule_fire_rates",
    "plot_co_occurrence_heatmap",
    "H2_THRESHOLD_FIRE_RATE",
]
