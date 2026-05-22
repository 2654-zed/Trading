"""Phase 1.5 — end-of-run analysis driver.

Loads JSONL records over a date range, runs H1/H2/H3 analyses, writes plots
+ Markdown report. Idempotent: rerunning produces byte-identical Markdown
+ identical-bytes plots given the same input.

Usage:
    python -m layer3_trading_exp.scripts.run_analysis --start 2026-05-01 --end 2026-05-30
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout so the Markdown report path prints cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..analysis import (
    _loader,
    generate_report as _gr,
    h1_opportunity_rate as _h1,
    h1_prime as _h1p,
    h2_filter_activation as _h2,
    h3_distributional_comparison as _h3,
    h4_pareto as _h4,
)
from ..config import DEFAULT


def _parse_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}: {e}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=_parse_date, required=True)
    p.add_argument("--end", type=_parse_date, required=True)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = DEFAULT
    analysis_dir = cfg.data_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading records from {cfg.log_dir} for {args.start} to {args.end}...")
    records = _loader.load_records(args.start, args.end, cfg.log_dir)
    print(f"  loaded {len(records):,} records")

    print("computing H1 (opportunity rate)...")
    h1_stats = _h1.compute(records)

    # Phase 2 sub-phase 2.7 (D-013): H1' + H4 cross-chain analyses.
    # Both compute() functions return coherent zero-stats on v1-only
    # records (no bridge_legs) — they don't need a feature flag here.
    print("computing H1' (cross-chain opportunity rate)...")
    h1_prime_stats = _h1p.compute(records)

    print("computing H4 (Pareto concentration over chain-pair-token buckets)...")
    h4_stats = _h4.compute(records)

    print("computing H2 (filter activation)...")
    h2_stats = _h2.compute(records)

    print("computing H3 (distributional comparison)...")
    h3_stats = _h3.compute(records)

    print("rendering plots...")
    plot_dir = analysis_dir / f"plots_{args.start}_{args.end}"
    plot_dir.mkdir(parents=True, exist_ok=True)
    plots: dict[str, str] = {}

    p = plot_dir / "h1_per_day_counts.png"
    _h1.plot_per_day_counts(h1_stats, p);            plots["h1_per_day"] = p.name
    p = plot_dir / "h1_margin_histogram.png"
    _h1.plot_margin_histogram(records, p);           plots["h1_margin_hist"] = p.name
    p = plot_dir / "h2_per_rule_fire_rates.png"
    _h2.plot_per_rule_fire_rates(h2_stats, p);       plots["h2_rule_rates"] = p.name
    p = plot_dir / "h2_co_occurrence_heatmap.png"
    _h2.plot_co_occurrence_heatmap(h2_stats, p);     plots["h2_co_occurrence"] = p.name
    p = plot_dir / "h3_margin_distributions.png"
    _h3.plot_margin_distributions(records, p);       plots["h3_margin_dists"] = p.name
    p = plot_dir / "h3_protocol_distribution.png"
    _h3.plot_protocol_distribution(records, p);      plots["h3_protocol_dist"] = p.name

    # Phase 2 sub-phase 2.7 plots — written only when cross-chain data exists,
    # since the underlying plot helpers would produce empty figures otherwise.
    has_cross_chain = bool(h1_prime_stats.get("emissions_total"))
    if has_cross_chain:
        p = plot_dir / "h1_prime_per_day_counts.png"
        _h1p.plot_per_day_counts(h1_prime_stats, p);     plots["h1_prime_per_day"] = p.name
        p = plot_dir / "h1_prime_chain_pairs.png"
        _h1p.plot_chain_pair_distribution(h1_prime_stats, p); plots["h1_prime_chain_pairs"] = p.name
        p = plot_dir / "h4_pareto_curve.png"
        _h4.plot_pareto_curve(h4_stats, p);              plots["h4_pareto"] = p.name
        p = plot_dir / "h4_top_buckets.png"
        _h4.plot_bucket_bars(h4_stats, p);               plots["h4_buckets"] = p.name

    # Plot links are relative to the report file (which lives in analysis_dir).
    plot_links = {k: f"plots_{args.start}_{args.end}/{v}" for k, v in plots.items()}

    out_path = analysis_dir / f"report_{args.start}_{args.end}.md"
    _gr.write_report(
        h1_stats=h1_stats, h2_stats=h2_stats, h3_stats=h3_stats,
        h1_prime_stats=h1_prime_stats if has_cross_chain else None,
        h4_stats=h4_stats if has_cross_chain else None,
        plot_paths=plot_links,
        start_date=args.start, end_date=args.end,
        out_path=out_path,
    )
    size = out_path.stat().st_size
    print()
    print(f"wrote {out_path} ({size:,} bytes)")
    print(f"plots: {plot_dir}/ ({len(plots)} files)")


if __name__ == "__main__":
    main()
