"""The deterministic gates — the only things that may clear the way to a
positive verdict. No LLM calls in this file, by design.

HARD_GATE: evaluates the IS metrics against pre-registered, multiple-testing
  -corrected bars. OOS_GATE: 'sign must hold IS→OOS' + effect survives on the
sealed holdout. Plus the holdout seal/tripwire so a holdout is read once.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .state import DataContract, HardGate, MetricBlock


# ── holdout sealing (fresh-data-per-hypothesis) ─────────────────────────────

def seal_holdout(windows: list[str]) -> str:
    """A sha over the (sorted) holdout window keys. Verified on every read;
    if the holdout set changes mid-hypothesis, the run aborts."""
    h = hashlib.sha256("|".join(sorted(windows)).encode()).hexdigest()
    return h


def verify_holdout(contract: DataContract) -> None:
    if seal_holdout(contract["holdout_windows"]) != contract["holdout_sha256"]:
        raise RuntimeError("HOLDOUT SEAL BROKEN — holdout windows changed "
                           "after sealing; refusing to proceed (anti-overfit).")


# ── HARD_GATE (in-sample) ───────────────────────────────────────────────────

def evaluate_hard_gate(is_metrics: MetricBlock, data_regime: str,
                       ledger_n: int, *, min_obs_floor: int = 30,
                       alpha: float = 0.05) -> HardGate:
    """Compute every boolean from the IS metrics. Trading-only checks
    (costs, market-relative) are auto-satisfied for forensic measurements
    (they don't apply) but REQUIRED for PRICE_SERIES."""
    g = HardGate(min_obs_floor=min_obs_floor)
    reasons: list[str] = []
    is_forensic = data_regime in ("MEMPOOL_FORENSIC", "MEMPOOL_PARTIAL_VANTAGE")

    # min observations (a Sharpe / correlation on <30 obs is noise)
    g.min_obs_met = (is_metrics.n_obs or 0) >= min_obs_floor
    if not g.min_obs_met:
        reasons.append(f"n_obs={is_metrics.n_obs} < floor {min_obs_floor}")

    # multiple-testing-corrected significance vs the ledger's running N
    thr = alpha / max(1, ledger_n)
    g.mc_corrected_significant = (is_metrics.p_value is not None
                                  and is_metrics.p_value <= thr)
    if not g.mc_corrected_significant:
        reasons.append(f"p={is_metrics.p_value} > Bonferroni thr {thr:.2e} (N={ledger_n})")

    # regime robustness — the effect must hold (same sign) in each sub-window
    signs = {r.get("edge_sign") for r in (is_metrics.per_regime or [])
             if r.get("edge_sign") in ("+", "-")}
    g.regime_robust = len(is_metrics.per_regime) >= 2 and len(signs) == 1
    if not g.regime_robust:
        reasons.append("effect not sign-consistent across ≥2 sub-regimes")

    # look-ahead: measurements are stamped no_lookahead=True by the executor
    g.no_lookahead_verified = bool(is_metrics.notes and "no_lookahead" in is_metrics.notes)
    if not g.no_lookahead_verified:
        reasons.append("look-ahead freedom not asserted by the executor")

    # trading-only gates
    if is_forensic:
        g.survives_costs = True
        g.market_relative_ok = True
    else:
        g.survives_costs = (is_metrics.net_sharpe_after_costs or -1) > 0
        g.market_relative_ok = (is_metrics.excess_return_vs_bench or -1) > 0
        if not g.survives_costs:
            reasons.append("does not survive costs (net Sharpe ≤ 0)")
        if not g.market_relative_ok:
            reasons.append("no positive market-relative excess (beta ≠ alpha)")

    g.reasons = reasons
    return g


# ── OOS_GATE (the only door to a positive verdict) ──────────────────────────

def evaluate_oos_gate(is_metrics: MetricBlock, oos_metrics: MetricBlock,
                      *, max_decay: float = 0.5) -> tuple[bool, str]:
    """'Sign must hold IS→OOS', and the effect must not decay past budget.
    Returns (passed, reason)."""
    if is_metrics.edge_sign in (None, "0") or oos_metrics.edge_sign in (None, "0"):
        return False, "edge sign undefined IS or OOS (effect ~0 / unmeasured)"
    if is_metrics.edge_sign != oos_metrics.edge_sign:
        return False, (f"SIGN FLIP IS={is_metrics.edge_sign} → OOS="
                       f"{oos_metrics.edge_sign} — fatal")
    ie, oe = abs(is_metrics.effect or 0.0), abs(oos_metrics.effect or 0.0)
    if ie > 0 and oe < (1 - max_decay) * ie:
        return False, (f"effect decayed {1 - oe/ie:.0%} IS→OOS "
                       f"(> {max_decay:.0%} budget)")
    if (oos_metrics.n_obs or 0) < 30:
        return False, f"OOS n_obs={oos_metrics.n_obs} too small to trust"
    return True, (f"sign held ({oos_metrics.edge_sign}); effect {ie:.4g}→{oe:.4g} "
                  f"within decay budget; OOS n={oos_metrics.n_obs}")


__all__ = ["seal_holdout", "verify_holdout", "evaluate_hard_gate",
           "evaluate_oos_gate"]
