"""Outcome attributor (sub-phase 3.4 / D-034).

Computes a forward-looking, $0-CU outcome proxy for each decision and
writes it to the outcome ledger. Runs as a POST-REPLAY pass: by then the
full history is available, so for a decision made at window ending T we
can measure activity over [T, T + horizon] — data the decision could NOT
see (no leakage: the decision used only data ≤ T via the replay as-of
cursor).

Outcome metric (per D-034, bounded [0,1]):
    flow_ratio   = forward_flow[T, T+h] / max(baseline_flow[T-h, T], eps)
    flow_term    = 0.5 * clamp(0.5*(tanh(flow_ratio - 1) + 1), 0, 1)... see below
    advr_term    = 0.5 * min(forward_poisoning / POISON_NORM, 1)
    outcome_value = flow_term + advr_term
    ground_truth  = "escalated" if outcome_value >= ESCALATION_THRESHOLD
                    else "quiet"

The metric measures whether the entity-set's activity ESCALATED after the
decision. The learner (3.4b) resolves per-action correctness (a hedge is
"right" if activity escalated; a hold is "right" if it stayed quiet).
"""

from __future__ import annotations

import math
import time
from typing import Optional


class OutcomeAttributor:
    POISON_NORM = 5.0          # forward poisoning events that saturate advr_term
    ESCALATION_THRESHOLD = 0.25

    def __init__(self, data_source, *, horizon_seconds: float):
        """`data_source` must expose get_total_flow_in_window(start, end)
        and get_poisoning_count_in_window(start, end) (the
        Phase2L3CorpusAdapter). `horizon_seconds` = forward + baseline
        window size (= 2 replay windows per the approved horizon)."""
        self._data = data_source
        self._h = float(horizon_seconds)
        self.attributed = 0

    def compute_outcome(self, window_end: float) -> tuple[float, str, dict]:
        """Compute (outcome_value, ground_truth, detail) for a decision at
        window ending `window_end`. Pure read of the cached forward data."""
        T = window_end
        h = self._h
        forward_flow = self._data.get_total_flow_in_window(T, T + h)
        baseline_flow = self._data.get_total_flow_in_window(T - h, T)
        eps = 1e-9
        flow_ratio = forward_flow / max(baseline_flow, eps)
        # Map ratio→[0,0.5]: ratio 1 → 0.25, ratio≫1 → 0.5, ratio≪1 → 0.
        # 0.5*(tanh(ratio-1)+1) is in [0,1]; scale by 0.5 for the half-weight.
        flow_term = 0.5 * min(max(0.5 * (math.tanh(flow_ratio - 1.0) + 1.0), 0.0), 1.0)

        forward_poison = self._data.get_poisoning_count_in_window(T, T + h)
        advr_term = 0.5 * min(forward_poison / self.POISON_NORM, 1.0)

        outcome_value = round(flow_term + advr_term, 6)
        ground_truth = ("escalated" if outcome_value >= self.ESCALATION_THRESHOLD
                        else "quiet")
        detail = {
            "forward_flow": forward_flow,
            "baseline_flow": baseline_flow,
            "flow_ratio": flow_ratio,
            "flow_term": flow_term,
            "forward_poison": forward_poison,
            "advr_term": advr_term,
        }
        return outcome_value, ground_truth, detail

    def attribute_all(self, ledger_writer) -> int:
        """For every decision in the ledger, compute + write its outcome.
        Returns the number of outcomes written."""
        decisions = ledger_writer.all_decisions()
        now = time.time()
        n = 0
        for d in decisions:
            value, gt, _detail = self.compute_outcome(d["window_end"])
            ledger_writer.write_outcome(
                decision_id=d["id"],
                outcome_type="forward_activity_proxy",
                outcome_value=value,
                ground_truth=gt,
                realized_at=now,
                lag_seconds=self._h,
            )
            n += 1
        self.attributed = n
        return n


__all__ = ["OutcomeAttributor"]
