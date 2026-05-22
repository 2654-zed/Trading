"""Markdown report template generator for end-of-run analysis.

Consumes the three stats dicts (h1, h2, h3) plus paths to PNG plots, produces
a single Markdown file at `data/analysis/report_{date_range}.md`.

Per spec acceptance: "Final report template includes all raw numbers, all plots,
and explicit placeholder sections for Jason's interpretation (labeled
'TO BE COMPLETED BY JASON')". Each hypothesis section has a clearly-marked
heading the user can search for.

Idempotent: same inputs produce byte-identical output (raw stats serialized
with `json.dumps(..., sort_keys=True, indent=2)`).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _format_threshold_check(met: bool) -> str:
    return "MEETS THRESHOLD" if met else "DOES NOT MEET THRESHOLD"


def _format_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.3f}%"


def _format_num(x, digits: int = 3) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, int):
        return f"{x:,}"
    return f"{x:,.{digits}f}"


def _section_h1(stats: dict, plots: dict[str, str]) -> str:
    threshold = stats["h1_threshold_opps_per_day"]
    lines = [
        "## H1 — Opportunity rate",
        "",
        f"**Hypothesis:** >= {threshold:,} opportunities/day at >= 0.3% gross margin?",
        "",
        f"- Days observed:   **{stats['n_days_observed']}**",
        f"- Total emissions: **{stats['emissions_total']:,}**",
        f"- Total unique:    **{stats['unique_total']:,}**",
        f"- Total episodes:  **{stats['episodes_total']:,}**",
        "",
        f"### Per-day rate (95% bootstrap CI, seed={stats['bootstrap_seed']}, "
        f"resamples={stats['bootstrap_resamples']:,})",
        "",
        "| Mode | Mean / day | 95% CI | Threshold met? |",
        "|---|---|---|---|",
        f"| emissions | {_format_num(stats['emissions_mean_per_day'])} | "
        f"[{_format_num(stats['emissions_ci_low'])}, {_format_num(stats['emissions_ci_high'])}] | "
        f"{_format_threshold_check(stats['h1_emissions_threshold_met'])} |",
        f"| unique    | {_format_num(stats['unique_mean_per_day'])} | "
        f"[{_format_num(stats['unique_ci_low'])}, {_format_num(stats['unique_ci_high'])}] | "
        f"{_format_threshold_check(stats['h1_unique_threshold_met'])} |",
        f"| episodes  | {_format_num(stats['episodes_mean_per_day'])} | "
        f"[{_format_num(stats['episodes_ci_low'])}, {_format_num(stats['episodes_ci_high'])}] | "
        f"{_format_threshold_check(stats['h1_episodes_threshold_met'])} |",
        "",
        "### Margin distribution",
        "",
        f"- min: **{_format_num(stats['margin_bps_min'])} bps**",
        f"- p50: **{_format_num(stats['margin_bps_p50'])} bps**",
        f"- p95: **{_format_num(stats['margin_bps_p95'])} bps**",
        f"- max: **{_format_num(stats['margin_bps_max'])} bps**",
        "",
        f"![per-day counts]({plots['h1_per_day']})",
        "",
        f"![margin histogram]({plots['h1_margin_hist']})",
        "",
        "## TO BE COMPLETED BY JASON: H1 interpretation",
        "",
        "_Jason: which count mode is meaningful for this run, and does it support H1?_",
        "_Note any caveats about persistence, episode definitions, or sampling_",
        "_artifacts that affect the interpretation._",
        "",
    ]
    return "\n".join(lines)


def _section_h2(stats: dict, plots: dict[str, str]) -> str:
    threshold = stats["h2_threshold_fire_rate"]
    lines = [
        "## H2 — Filter activation",
        "",
        f"**Hypothesis:** does any rule fire on >= {threshold:.0%} of opportunities?",
        "",
        f"- Total records:        **{stats['total_records']:,}**",
        f"- Degraded (excluded):  **{stats['degraded_records']:,}**",
        f"- Eligible:             **{stats['eligible_records']:,}**",
        "",
        f"- Hard-flag rate:       **{_format_pct(stats['hard_flag_rate'])}**",
        f"- Soft-flag-only rate:  **{_format_pct(stats['soft_flag_rate_only'])}**",
        f"- Unflagged rate:       **{_format_pct(stats['unflagged_rate'])}**",
        "",
        f"- Any rule meets H2 threshold? **{stats['any_rule_meets_h2_threshold']}**",
        "",
        "### Per-rule fire rates",
        "",
        "| Rule | Tier | Fires | Rate | Meets >= 1%? |",
        "|---|---|---|---|---|",
    ]
    for rid in range(1, 14):
        info = stats["per_rule"][f"rule_{rid}"]
        lines.append(
            f"| R{rid} | {info['tier'] or '-'} | {info['fire_count']:,} | "
            f"{_format_pct(info['fire_rate'])} | "
            f"{'yes' if info['meets_h2_threshold'] else 'no'} |"
        )
    lines += [
        "",
        f"![per-rule fire rates]({plots['h2_rule_rates']})",
        "",
        f"![rule co-occurrence]({plots['h2_co_occurrence']})",
        "",
        "## TO BE COMPLETED BY JASON: H2 interpretation",
        "",
        "_Jason: did Layer 3 corpus achieve the H2 threshold? Which rules contributed?_",
        "_Are the rule fires concentrated in particular pool/token combinations?_",
        "_If H2 fails, is it because Layer 3 has not catalogued the relevant_",
        "_intelligence, because the monitored set is structurally clean, or both?_",
        "",
    ]
    return "\n".join(lines)


def _section_h3(stats: dict, plots: dict[str, str]) -> str:
    sizes = stats["group_sizes"]
    lines = [
        "## H3 — Distributional comparison",
        "",
        "**Hypothesis:** distributional differences between flagged and unflagged opportunities?",
        "",
        f"- Hard-flagged:           **{sizes['hard_flagged']:,}**",
        f"- Soft-flagged only:      **{sizes['soft_flagged_only']:,}**",
        f"- Unflagged:              **{sizes['unflagged']:,}**",
        f"- Flagged (any):          **{sizes['flagged_any']:,}**",
        "",
        "### Numeric comparisons (Mann-Whitney U two-sided, flagged_any vs unflagged)",
        "",
        "| Field | Flagged median | Unflagged median | p-value | rank-biserial r |",
        "|---|---|---|---|---|",
    ]
    for field, info in stats["numeric_comparisons"].items():
        f = info["flagged"]
        u = info["unflagged"]
        mw = info["mann_whitney"]
        lines.append(
            f"| {field} | {_format_num(f['median'])} | {_format_num(u['median'])} | "
            f"{_format_num(mw['p_value']) if mw['p_value'] is not None else 'n/a'} | "
            f"{_format_num(mw['rank_biserial']) if mw['rank_biserial'] is not None else 'n/a'} |"
        )
    lines += [
        "",
        "### Categorical comparisons (Chi-square independence)",
        "",
    ]
    for field, info in stats["categorical_comparisons"].items():
        chi = info["chi_square"]
        lines.append(f"#### {field}")
        lines.append("")
        lines.append("| category | flagged | unflagged |")
        lines.append("|---|---|---|")
        for cat, counts in sorted(info["table"].items()):
            lines.append(f"| `{cat}` | {counts[0]:,} | {counts[1]:,} |")
        lines.append("")
        if chi.get("p_value") is not None:
            lines.append(f"**Chi-square**: chi2={_format_num(chi['chi2'])}, "
                         f"dof={chi['dof']}, p={_format_num(chi['p_value'])}")
        else:
            lines.append(f"**Chi-square**: not computed ({chi.get('reason', 'unknown')})")
        lines.append("")
    lines += [
        f"![margin distributions]({plots['h3_margin_dists']})",
        "",
        f"![protocol distribution]({plots['h3_protocol_dist']})",
        "",
        "## TO BE COMPLETED BY JASON: H3 interpretation",
        "",
        "_Jason: are the distributions meaningfully different? Are p-values_",
        "_practically meaningful, or just powered by sample size? What does_",
        "_the effect size (rank-biserial r) suggest in the context of the_",
        "_observed margins and pool/token distributions?_",
        "",
    ]
    return "\n".join(lines)


def _section_h1_prime(stats: dict, plots: dict[str, str]) -> str:
    """Phase 2 sub-phase 2.7 (D-013): H1' cross-chain section."""
    lines: list[str] = [
        "## H1' — cross-chain opportunities per day (Phase 2)",
        "",
        f"- Threshold: **≥{stats['h1_prime_threshold_opps_per_day']}** "
        f"distinct cross-chain opportunities/day at "
        f"≥{stats['cross_chain_margin_floor_bps']} bps post-haircut margin",
        f"- Falsification: **<{stats['h1_prime_falsification_threshold_opps_per_day']}** "
        f"unique/day after 7 days",
        f"- Days observed: {stats['n_days_observed']}",
        "",
        "### Counts",
        f"- Emissions total: {stats['emissions_total']:,}",
        f"- Unique total (per-day-dedup): {stats['unique_total']:,}",
        f"- Episodes total: {stats['episodes_total']:,}",
        "",
        "### Per-day rate (bootstrap 95% CI)",
        f"- Unique: **{stats['unique_mean_per_day']:.2f}** "
        f"[{stats['unique_ci_low']:.2f}, {stats['unique_ci_high']:.2f}]",
        f"- Episodes: {stats['episodes_mean_per_day']:.2f} "
        f"[{stats['episodes_ci_low']:.2f}, {stats['episodes_ci_high']:.2f}]",
        f"- Emissions: {stats['emissions_mean_per_day']:.2f} "
        f"[{stats['emissions_ci_low']:.2f}, {stats['emissions_ci_high']:.2f}]",
        "",
        f"- H1' threshold met (unique)? "
        f"**{_format_threshold_check(stats['h1_prime_unique_threshold_met'])}**",
        f"- H1' falsified (unique <{stats['h1_prime_falsification_threshold_opps_per_day']}/day "
        f"after 7d)? **{stats.get('h1_prime_falsified', False)}**",
    ]
    pairs = stats.get("chain_pair_unique_counts") or {}
    if pairs:
        lines.extend([
            "",
            "### Per (src → dst) chain-pair breakdown (unique total)",
            "",
        ])
        for pair_label, n in pairs.items():
            lines.append(f"- `{pair_label}`: {n:,} unique")
    p50_post = stats.get("margin_bps_post_haircut_p50")
    p50_raw = stats.get("margin_bps_raw_p50")
    if p50_post is not None and p50_raw is not None:
        lines.extend([
            "",
            "### Margin distribution",
            f"- Post-haircut p50: {p50_post} bps",
            f"- Pre-haircut (raw) p50: {p50_raw} bps",
            f"- Latency-drift haircut p50: {stats.get('haircut_bps_p50', 0)} bps",
        ])
    if "h1_prime_per_day" in plots:
        lines.extend(["", f"![H1' per-day]({plots['h1_prime_per_day']})"])
    if "h1_prime_chain_pairs" in plots:
        lines.extend(["", f"![H1' chain-pair distribution]({plots['h1_prime_chain_pairs']})"])
    lines.extend([
        "",
        "### TO BE COMPLETED BY JASON: H1' interpretation",
        "",
        "_Jason: at the verified Across fee level (D-010), is the observed_",
        "_cross-chain rate sufficient to justify Phase 3 execution work?_",
        "",
        "_If H1' is falsified, is the failure attributable to specific chain pairs_",
        "_or tokens, or is it a uniform low-rate result across the matrix?_",
        "",
    ])
    return "\n".join(lines)


def _section_h4(stats: dict, plots: dict[str, str]) -> str:
    """Phase 2 sub-phase 2.7 (D-013): H4 Pareto-concentration section."""
    top_pct = int(stats["h4_pareto_top_fraction"] * 100)
    cov_thresh = int(stats["h4_pareto_coverage_threshold"] * 100)
    lines: list[str] = [
        "## H4 — Pareto concentration across (chain-pair, token) buckets (Phase 2)",
        "",
        f"- Threshold: top {top_pct}% of buckets covers ≥{cov_thresh}% of opportunities",
        f"- Falsification: top {top_pct}% covers "
        f"<{int(stats['h4_falsification_coverage'] * 100)}%",
        "",
        f"- Distinct buckets observed: {stats['n_unique_buckets']}",
        f"- Unique opportunity events: {stats['n_unique_events']}",
        f"- Top-{top_pct}% buckets (n={stats['top_n_buckets']}) cover "
        f"**{stats['top_n_coverage'] * 100:.1f}%** of events",
        f"- H4 supported? **{_format_threshold_check(stats['h4_supported'])}**",
        f"- H4 falsified? **{stats.get('h4_falsified', False)}**",
    ]
    buckets = stats.get("bucket_counts") or {}
    if buckets:
        top10 = list(buckets.items())[:10]
        lines.extend([
            "",
            "### Top-10 buckets by unique opportunity count",
            "",
            "| rank | (src → dst : token) | count |",
            "|---|---|---|",
        ])
        for i, (label, n) in enumerate(top10, start=1):
            lines.append(f"| {i} | `{label}` | {n:,} |")
    if "h4_pareto" in plots:
        lines.extend(["", f"![H4 Pareto curve]({plots['h4_pareto']})"])
    if "h4_buckets" in plots:
        lines.extend(["", f"![H4 top buckets]({plots['h4_buckets']})"])
    lines.extend([
        "",
        "### TO BE COMPLETED BY JASON: H4 interpretation",
        "",
        "_Jason: if H4 is supported, which token routes drive the concentration?_",
        "_Does that match the bridge-liquidity profile we modeled in D-006?_",
        "",
    ])
    return "\n".join(lines)


def render_report(
    *,
    h1_stats: dict,
    h2_stats: dict,
    h3_stats: dict,
    plot_paths: dict[str, str],
    start_date: str,
    end_date: str,
    generated_at: str | None = None,
    h1_prime_stats: dict | None = None,
    h4_stats: dict | None = None,
) -> str:
    """Render the full Markdown report. Returns the text; caller writes it.

    `plot_paths` keys (relative paths embedded in the Markdown):
        h1_per_day, h1_margin_hist,
        h2_rule_rates, h2_co_occurrence,
        h3_margin_dists, h3_protocol_dist,
        h1_prime_per_day, h1_prime_chain_pairs,  # Phase 2 sub-phase 2.7
        h4_pareto, h4_buckets                    # Phase 2 sub-phase 2.7

    Phase 2 sections (`h1_prime_stats`, `h4_stats`) render only when
    provided AND when their data is non-empty. For Phase 1-only datasets
    (no cross-chain records), the caller may pass None or empty dicts and
    the sections are suppressed — Phase 1 replay produces a Phase 1-shaped
    report unchanged.
    """
    when = generated_at or datetime.now(timezone.utc).isoformat()
    has_cross_chain = bool(h1_prime_stats and h1_prime_stats.get("emissions_total"))

    parts: list[str] = []
    title_prefix = "Phase 2" if has_cross_chain else "Phase 1"
    parts.append(f"# {title_prefix} end-of-run analysis — {start_date} to {end_date}")
    parts.append("")
    parts.append(f"_Generated at {when} UTC. Tooling output — Jason annotates the interpretation sections._")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(_section_h1(h1_stats, plot_paths))
    parts.append("---")
    parts.append("")
    # H1' + H4 sections render only when the run produced cross-chain data.
    if has_cross_chain:
        parts.append(_section_h1_prime(h1_prime_stats, plot_paths))
        parts.append("---")
        parts.append("")
        if h4_stats:
            parts.append(_section_h4(h4_stats, plot_paths))
            parts.append("---")
            parts.append("")
    parts.append(_section_h2(h2_stats, plot_paths))
    parts.append("---")
    parts.append("")
    parts.append(_section_h3(h3_stats, plot_paths))
    parts.append("---")
    parts.append("")
    parts.append("## Appendix — raw stats (full JSON dump)")
    parts.append("")
    parts.append("### H1")
    parts.append("```json")
    parts.append(json.dumps(h1_stats, sort_keys=True, indent=2, default=str))
    parts.append("```")
    parts.append("")
    if has_cross_chain:
        parts.append("### H1'")
        parts.append("```json")
        parts.append(json.dumps(h1_prime_stats, sort_keys=True, indent=2, default=str))
        parts.append("```")
        parts.append("")
        if h4_stats:
            parts.append("### H4")
            parts.append("```json")
            parts.append(json.dumps(h4_stats, sort_keys=True, indent=2, default=str))
            parts.append("```")
            parts.append("")
    parts.append("### H2")
    parts.append("```json")
    parts.append(json.dumps(h2_stats, sort_keys=True, indent=2, default=str))
    parts.append("```")
    parts.append("")
    parts.append("### H3")
    parts.append("```json")
    parts.append(json.dumps(h3_stats, sort_keys=True, indent=2, default=str))
    parts.append("```")
    parts.append("")
    return "\n".join(parts)


def write_report(
    *,
    h1_stats: dict,
    h2_stats: dict,
    h3_stats: dict,
    plot_paths: dict[str, str],
    start_date: str,
    end_date: str,
    out_path: Path,
    generated_at: str | None = None,
    h1_prime_stats: dict | None = None,
    h4_stats: dict | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_report(
        h1_stats=h1_stats, h2_stats=h2_stats, h3_stats=h3_stats,
        plot_paths=plot_paths,
        start_date=start_date, end_date=end_date,
        generated_at=generated_at,
        h1_prime_stats=h1_prime_stats, h4_stats=h4_stats,
    )
    out_path.write_text(text, encoding="utf-8")
    return out_path


__all__ = ["render_report", "write_report"]
