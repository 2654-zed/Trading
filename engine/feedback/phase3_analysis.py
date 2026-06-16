"""Phase 3 hypothesis analysis from the outcome ledger (sub-phase 3.4b).

Computes proxy verdicts for H5 + H7 from attributed outcomes. All
verdicts are PROXY-validated (outcome = forward flow/adversarial-event
escalation per D-034, NOT realized P&L). Pure stdlib (no numpy).

  H5 (orchestrator > best single lens): compare correlation between the
      orchestrator's weighted_aggregate and outcome vs each lens's
      strength and outcome. Orchestrator "wins" if its |corr| exceeds the
      best single lens's |corr|.

  H7 (conflicts are alpha): compare the outcome distribution of
      conflict-flagged windows vs non-conflict windows (mean diff +
      Mann-Whitney-U proxy via rank-sum).
"""

from __future__ import annotations

import json
import math
from typing import Optional


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def analyze_h5(joined_rows: list[dict]) -> dict:
    """joined_rows from LedgerWriter.outcomes_joined()."""
    if len(joined_rows) < 3:
        return {"verdict": "INSUFFICIENT_DATA", "n": len(joined_rows)}
    outcomes = [float(r["outcome_value"] or 0.0) for r in joined_rows]
    orch_scores = [float(r["weighted_aggregate"] or 0.0) for r in joined_rows]
    orch_corr = _pearson(orch_scores, outcomes)

    # Per-lens strength series.
    lenses = ("graph", "stochastic", "information")
    lens_corr: dict[str, Optional[float]] = {}
    for lens in lenses:
        series = []
        for r in joined_rows:
            try:
                pls = json.loads(r.get("per_lens_strength") or "{}")
            except (TypeError, ValueError):
                pls = {}
            series.append(float(pls.get(lens, 0.0)))
        lens_corr[lens] = _pearson(series, outcomes)

    valid_lens = {k: v for k, v in lens_corr.items() if v is not None}
    best_lens = max(valid_lens, key=lambda k: abs(valid_lens[k])) if valid_lens else None
    best_lens_corr = abs(valid_lens[best_lens]) if best_lens else 0.0
    orch_abs = abs(orch_corr) if orch_corr is not None else 0.0

    wins = orch_abs >= best_lens_corr
    return {
        "n": len(joined_rows),
        "orchestrator_corr": orch_corr,
        "lens_corr": lens_corr,
        "best_lens": best_lens,
        "best_lens_abs_corr": best_lens_corr,
        "orchestrator_abs_corr": orch_abs,
        "orchestrator_beats_best_lens": wins,
        "margin_pp": round((orch_abs - best_lens_corr) * 100, 1),
        "verdict": ("SUPPORTED-PROXY" if wins and orch_abs > 0
                    else "NOT-SUPPORTED-PROXY"),
    }


def _rank_sum(a: list[float], b: list[float]) -> Optional[float]:
    """Mann-Whitney-U proxy: return the rank-biserial-ish effect (prob
    a > b minus prob b > a). Range [-1, 1]; 0 = no difference."""
    if not a or not b:
        return None
    greater = 0
    ties = 0
    for x in a:
        for y in b:
            if x > y:
                greater += 1
            elif x == y:
                ties += 1
    total = len(a) * len(b)
    # P(a>b) - P(b>a) = (greater - (total - greater - ties)) / total
    return (2 * greater + ties - total) / total


def analyze_h7(joined_rows: list[dict]) -> dict:
    conflict = [float(r["outcome_value"] or 0.0)
                for r in joined_rows if int(r.get("conflict_count") or 0) > 0]
    noconf = [float(r["outcome_value"] or 0.0)
              for r in joined_rows if int(r.get("conflict_count") or 0) == 0]
    if len(conflict) < 3 or len(noconf) < 3:
        return {"verdict": "INSUFFICIENT_DATA",
                "n_conflict": len(conflict), "n_noconflict": len(noconf)}
    mc = sum(conflict) / len(conflict)
    mn = sum(noconf) / len(noconf)
    effect = _rank_sum(conflict, noconf)
    # "Different distribution" proxy: |effect| above a small threshold.
    differs = effect is not None and abs(effect) >= 0.1
    return {
        "n_conflict": len(conflict),
        "n_noconflict": len(noconf),
        "mean_outcome_conflict": round(mc, 4),
        "mean_outcome_noconflict": round(mn, 4),
        "rank_effect": round(effect, 4) if effect is not None else None,
        "distributions_differ": differs,
        "verdict": ("SUPPORTED-PROXY" if differs else "NOT-SUPPORTED-PROXY"),
    }


__all__ = ["analyze_h5", "analyze_h7"]
