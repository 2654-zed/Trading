"""Learner (sub-phase 3.4b, invariant I-19 "system must learn").

Pulls attributed outcomes from the ledger and updates per-regime
per-lens weights, persisting them to a JSON file the WeightingEngine
reads. Designed to run "nightly" (idempotent; each run recomputes from
the full ledger with exponential-decay recency weighting).

Learning rule (per D-036):
  For each regime R and lens L, compute a CREDIT:
      credit_{R,L} = Σ_t  decay_t · strength_{L,t} · outcome_t
  over decisions t in regime R (decay_t favors recent decisions). A lens
  that fires strongly in windows that subsequently ESCALATE (high
  outcome_value) accrues credit. Normalize credits across lenses to get
  the data-driven weight, then BLEND with the static prior so small
  samples don't overfit:
      learned_{R,L} = α · normalized_credit_{R,L} + (1−α) · prior_{R,L}
  Lenses with no data (topology, game) keep their prior. Final per-regime
  table is re-normalized to sum 1.0 and validated.

I-19 observability: `staleness_seconds()` / `is_stale()` surface whether
the learner has run recently (warn if > 7 days).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from ..orchestrator.regime_weights import REGIME_WEIGHTS, get_regime_weights
from ..orchestrator.static_weights import validate_weights
from ..core.signal_schema import VALID_LENSES


STALE_AFTER_SECONDS = 7 * 86400  # I-19: warn if no learner run in 7 days


class Learner:
    def __init__(self, *, blend_alpha: float = 0.5, decay_half_life: int = 50):
        """`blend_alpha` = weight on the learned signal vs the prior.
        `decay_half_life` = # decisions for the recency decay to halve."""
        self._alpha = float(blend_alpha)
        self._half_life = max(1, int(decay_half_life))
        self.last_run_ts: Optional[float] = None
        self.last_summary: dict = {}

    def learn(self, ledger_writer) -> dict[str, dict[str, float]]:
        """Recompute per-regime per-lens weights from the ledger.
        Returns {regime: {lens: weight}}. Does NOT persist (call persist).
        """
        rows = ledger_writer.outcomes_joined()
        # Group decisions by regime, newest last.
        by_regime: dict[str, list[dict]] = {}
        for r in rows:
            by_regime.setdefault(r["regime_label"], []).append(r)

        learned: dict[str, dict[str, float]] = {}
        per_regime_n: dict[str, int] = {}
        for regime, prior in REGIME_WEIGHTS.items():
            decisions = by_regime.get(regime, [])
            per_regime_n[regime] = len(decisions)
            if not decisions:
                learned[regime] = dict(prior)
                continue
            # Credit per lens.
            credit: dict[str, float] = {l: 0.0 for l in VALID_LENSES}
            n = len(decisions)
            for i, d in enumerate(decisions):
                # Recency decay: most recent decision (i = n-1) has weight 1.
                age = (n - 1) - i
                decay = 0.5 ** (age / self._half_life)
                outcome = float(d.get("outcome_value") or 0.0)
                try:
                    strengths = json.loads(d.get("per_lens_strength") or "{}")
                except (TypeError, ValueError):
                    strengths = {}
                for lens, s in strengths.items():
                    if lens in credit:
                        credit[lens] += decay * float(s) * outcome

            total_credit = sum(c for c in credit.values() if c > 0)
            blended: dict[str, float] = {}
            for lens in VALID_LENSES:
                prior_w = prior.get(lens, 0.0)
                if total_credit > 0 and credit[lens] > 0:
                    norm_credit = credit[lens] / total_credit
                    blended[lens] = self._alpha * norm_credit + (1 - self._alpha) * prior_w
                else:
                    # No positive credit for this lens → keep prior (decayed
                    # by alpha so the learned-credit lenses can gain share).
                    blended[lens] = (1 - self._alpha) * prior_w
            # Re-normalize to sum 1.0.
            s = sum(blended.values())
            if s > 0:
                blended = {l: w / s for l, w in blended.items()}
            else:
                blended = dict(prior)
            validate_weights(blended)
            learned[regime] = blended

        self.last_run_ts = time.time()
        self.last_summary = {
            "decisions_per_regime": per_regime_n,
            "total_outcomes": len(rows),
        }
        return learned

    def persist(self, learned: dict, path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run_ts": self.last_run_ts or time.time(),
            "weights": learned,
            "summary": self.last_summary,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def load(path) -> Optional[dict]:
        path = Path(path)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def staleness_seconds(self) -> Optional[float]:
        if self.last_run_ts is None:
            return None
        return time.time() - self.last_run_ts

    def is_stale(self) -> bool:
        s = self.staleness_seconds()
        return s is None or s > STALE_AFTER_SECONDS


__all__ = ["Learner", "STALE_AFTER_SECONDS"]
