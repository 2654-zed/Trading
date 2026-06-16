"""Realized outcome attributor — Phase 4 (D-040, replaces the D-034 proxy).

Same interface as `OutcomeAttributor` (compute_outcome / attribute_all),
so the entire replay / learner / analysis / OOS-H8 machinery runs
unchanged — only the OUTCOME source changes from forward-flow proxy to
REAL forward price returns.

Outcome metric (window-level, mirrors the proxy's escalated/quiet shape
so phase3_analysis + replay work unchanged):
    for decision at window ending T, over horizon h:
        per-token forward return r_i = (P_i(T+h) - P_i(T)) / P_i(T)
        turbulence = mean_i |r_i|        (over tokens with prices at both T, T+h)
        outcome_value = min(turbulence / TURB_NORM, 1.0)        ∈ [0,1]
        ground_truth  = "escalated" if outcome_value >= ESC_THRESHOLD else "quiet"

"Turbulence" (mean absolute forward return of the monitored set) is the
real analog of the proxy's "did activity escalate": a high-magnitude
forward move is the eventful outcome a risk/adversarial regime decision
should anticipate. Direction-agnostic by design (the engine is
risk-detection-oriented; signed-return variants are a later refinement).
"""

from __future__ import annotations

import json
import time
from typing import Optional


class RealizedOutcomeAttributor:
    TURB_NORM = 0.20          # 20% mean abs forward move saturates to 1.0
    ESC_THRESHOLD = 0.25      # outcome_value ≥ 0.25 → "escalated"

    def __init__(self, price_source, monitored_tokens: list[str], *,
                 horizon_seconds: float, entity_mode: bool = False):
        """`entity_mode` (Phase 4 run-2): when True, each decision's
        outcome is the forward turbulence of ONLY the tokens its signals
        fired on (read from the ledger's `entities`), instead of the whole
        monitored basket. Falls back to the basket for windows with no
        priceable entities."""
        self._px = price_source
        self._tokens = [t.lower() for t in monitored_tokens if t]
        self._h = float(horizon_seconds)
        self._entity_mode = entity_mode
        self.attributed = 0
        self.entity_windows = 0
        self.fallback_windows = 0

    def _turbulence(self, tokens, T) -> tuple[Optional[float], int]:
        Th = T + self._h
        rets: list[float] = []
        for tok in tokens:
            p0 = self._px.price_at(tok, T)
            p1 = self._px.price_at(tok, Th)
            if p0 and p1 and p0 > 0:
                rets.append(abs((p1 - p0) / p0))
        if not rets:
            return None, 0
        return sum(rets) / len(rets), len(rets)

    def compute_outcome(self, window_end: float,
                        entities: Optional[list] = None) -> tuple[float, str, dict]:
        T = window_end
        used_entities = False
        turb = None
        n_priced = 0
        if self._entity_mode and entities:
            ent_toks = [e.lower() for e in entities]
            turb, n_priced = self._turbulence(ent_toks, T)
            if turb is not None:
                used_entities = True
        if turb is None:
            turb, n_priced = self._turbulence(self._tokens, T)
        if turb is None:
            return 0.0, "quiet", {"n_priced": 0, "turbulence": None,
                                  "entity_specific": False}
        outcome_value = round(min(turb / self.TURB_NORM, 1.0), 6)
        gt = "escalated" if outcome_value >= self.ESC_THRESHOLD else "quiet"
        return outcome_value, gt, {
            "n_priced": n_priced,
            "turbulence": turb,
            "horizon_seconds": self._h,
            "entity_specific": used_entities,
        }

    def attribute_all(self, ledger_writer) -> int:
        decisions = ledger_writer.all_decisions()
        now = time.time()
        n = 0
        for d in decisions:
            entities = None
            if self._entity_mode:
                try:
                    entities = json.loads(d.get("entities") or "[]")
                except (TypeError, ValueError):
                    entities = None
            value, gt, detail = self.compute_outcome(d["window_end"], entities)
            if detail.get("entity_specific"):
                self.entity_windows += 1
            else:
                self.fallback_windows += 1
            ledger_writer.write_outcome(
                decision_id=d["id"],
                outcome_type="realized_forward_return_entity"
                    if detail.get("entity_specific") else "realized_forward_return",
                outcome_value=value, ground_truth=gt,
                realized_at=now, lag_seconds=self._h,
            )
            n += 1
        self.attributed = n
        return n


__all__ = ["RealizedOutcomeAttributor"]
