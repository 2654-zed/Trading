"""Weighting engine (sub-phase 3.4b).

Serves per-regime lens weights to the orchestrator. Prefers the LEARNED
weights table (persisted by the Learner) when present + fresh; otherwise
falls back to the static regime tables (regime_weights.py). This is the
seam that makes the orchestrator's weighting DYNAMIC (I-19): after the
learner runs, the orchestrator picks up the updated weights via this
engine on its next construction.

Staleness (I-19): if the learned table is older than STALE_AFTER_SECONDS,
`is_stale()` returns True and the caller should surface a loud warning
AND fall back to static weights (don't trust stale learning).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from .regime_weights import get_regime_weights
from .static_weights import validate_weights
from ..feedback.learner import Learner, STALE_AFTER_SECONDS


class WeightingEngine:
    def __init__(self, learned_weights_path=None):
        self._path = Path(learned_weights_path) if learned_weights_path else None
        self._payload = Learner.load(self._path) if self._path else None
        # Validate any learned tables; drop to static on corruption.
        if self._payload:
            try:
                for tbl in self._payload.get("weights", {}).values():
                    validate_weights(tbl)
            except Exception:
                self._payload = None

    @property
    def using_learned(self) -> bool:
        return self._payload is not None and not self.is_stale()

    def is_stale(self) -> bool:
        if not self._payload:
            return True
        last = self._payload.get("last_run_ts")
        if last is None:
            return True
        return (time.time() - last) > STALE_AFTER_SECONDS

    def get_weights(self, regime: str) -> dict[str, float]:
        """Learned weights for the regime if fresh, else static prior."""
        if self.using_learned:
            tbl = self._payload.get("weights", {}).get(regime)
            if tbl:
                return dict(tbl)
        return get_regime_weights(regime)

    def status(self) -> dict:
        return {
            "source": "learned" if self.using_learned else "static",
            "stale": self.is_stale(),
            "path": str(self._path) if self._path else None,
            "summary": (self._payload or {}).get("summary"),
        }


__all__ = ["WeightingEngine"]
