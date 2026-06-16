"""Observability (sub-phase 3.5): per-lens-per-regime performance report.

Computes, from a ledger's attributed outcomes, how well each lens's
firing predicts a subsequent ESCALATED outcome — broken down by regime.
This is the I-19/I-20 visibility surface: it shows which lenses earn
their weight where, and feeds the operator's understanding of the
feedback loop.

Per (lens, regime):
  - fired = decisions where the lens's strength ≥ FIRE_THRESHOLD
  - precision = P(outcome escalated | lens fired)
  - recall    = P(lens fired | outcome escalated)
  - support   = # decisions in that regime

Output: a Markdown table (+ a dict for programmatic use).
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Optional

FIRE_THRESHOLD = 0.5
LENSES = ("graph", "stochastic", "information", "topology", "game")


def compute_performance(joined_rows: list[dict]) -> dict:
    """Returns {regime: {lens: {precision, recall, fired, escalated, support}}}."""
    by_regime: dict[str, list[dict]] = defaultdict(list)
    for r in joined_rows:
        by_regime[r["regime_label"]].append(r)

    out: dict[str, dict] = {}
    for regime, rows in by_regime.items():
        support = len(rows)
        escalated_total = sum(
            1 for r in rows if (r.get("ground_truth") == "escalated"))
        lens_stats: dict[str, dict] = {}
        for lens in LENSES:
            fired = 0
            fired_and_escalated = 0
            for r in rows:
                try:
                    pls = json.loads(r.get("per_lens_strength") or "{}")
                except (TypeError, ValueError):
                    pls = {}
                lens_fired = float(pls.get(lens, 0.0)) >= FIRE_THRESHOLD
                esc = r.get("ground_truth") == "escalated"
                if lens_fired:
                    fired += 1
                    if esc:
                        fired_and_escalated += 1
            precision = (fired_and_escalated / fired) if fired else None
            recall = (fired_and_escalated / escalated_total) if escalated_total else None
            if fired or precision is not None:
                lens_stats[lens] = {
                    "fired": fired,
                    "fired_and_escalated": fired_and_escalated,
                    "precision": precision,
                    "recall": recall,
                }
        out[regime] = {
            "support": support,
            "escalated_total": escalated_total,
            "lenses": lens_stats,
        }
    return out


def render_markdown(perf: dict) -> str:
    lines = ["# Per-lens-per-regime performance (proxy outcomes)", ""]
    lines.append("| regime | support | escalated | lens | fired | precision | recall |")
    lines.append("|---|---:|---:|---|---:|---:|---:|")
    for regime in sorted(perf.keys()):
        block = perf[regime]
        lstats = block["lenses"]
        if not lstats:
            lines.append(f"| {regime} | {block['support']} | "
                         f"{block['escalated_total']} | — | 0 | — | — |")
            continue
        first = True
        for lens, s in sorted(lstats.items()):
            p = f"{s['precision']:.2f}" if s["precision"] is not None else "—"
            rc = f"{s['recall']:.2f}" if s["recall"] is not None else "—"
            reg = regime if first else ""
            sup = str(block["support"]) if first else ""
            esc = str(block["escalated_total"]) if first else ""
            lines.append(f"| {reg} | {sup} | {esc} | {lens} | "
                         f"{s['fired']} | {p} | {rc} |")
            first = False
    return "\n".join(lines)


__all__ = ["compute_performance", "render_markdown", "FIRE_THRESHOLD"]
