"""Strategy router (sub-phase 3.4b) — DRY-RUN ONLY.

Translates a `Decision` into an intended order representation. Per the
experiment charter (detection + logging only), invariants I-1 (read-only
on-chain, no signing, no private keys) and I-3 (read-only L3), AND the
explicit decision NOT to authorize execution (see
D-037_execution-router-dry-run + the ABSENT
D-NNN_phase-3-execution-authorization):

  EXECUTION IS HARD-DISABLED. There is no code path that signs or sends a
  transaction. `live_enabled` defaults False and flipping it raises
  unless an execution-authorization token is supplied — which this
  experiment never creates.

The router only ever LOGS the intended order. It exists to satisfy the
spec's "Decision → order translation, dry-run by default" criterion and
to make the execution boundary explicit + auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class IntendedOrder:
    """What the system WOULD do — never sent anywhere."""
    window_start: float
    action: str
    side: Optional[str]          # 'reduce_risk' | 'add_exposure' | None
    notional_usd: float          # nominal, for dry-run sizing only
    confidence: float
    risk_score: float
    regime_label: str
    note: str

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start,
            "action": self.action,
            "side": self.side,
            "notional_usd": self.notional_usd,
            "confidence": self.confidence,
            "risk_score": self.risk_score,
            "regime_label": self.regime_label,
            "note": self.note,
            "DRY_RUN": True,
        }


class ExecutionNotAuthorizedError(RuntimeError):
    pass


class StrategyRouter:
    """Decision → IntendedOrder. Dry-run only; never signs/sends."""

    # Nominal dry-run sizing per action (USD). Illustrative only.
    BASE_NOTIONAL_USD = 10_000.0

    def __init__(self, *, live_enabled: bool = False,
                 execution_authorization_token: Optional[str] = None):
        # The ONLY way to enable live execution would be to pass a valid
        # authorization token, which requires the (deliberately absent)
        # D-NNN_phase-3-execution-authorization decision. This experiment
        # never creates one, so live execution is unreachable.
        if live_enabled:
            if execution_authorization_token != "AUTHORIZED_BY_D-NNN":
                raise ExecutionNotAuthorizedError(
                    "Live execution requires an explicit "
                    "D-NNN_phase-3-execution-authorization decision, which "
                    "does not exist. Execution stays disabled (I-1/I-3 + "
                    "detection-only charter)."
                )
        self._live = False  # hard-off regardless; see D-037
        self.total_orders = 0
        self.dropped_holds = 0

    @property
    def live_enabled(self) -> bool:
        return self._live

    def route(self, decision) -> Optional[IntendedOrder]:
        """Map a Decision → IntendedOrder (dry-run). `hold` → no order."""
        action = decision.action
        if action == "hold":
            self.dropped_holds += 1
            return None
        if action in ("hedge", "exit"):
            side = "reduce_risk"
        elif action == "enter":
            side = "add_exposure"
        else:
            side = None
        # Size scales with confidence, damped by risk.
        notional = self.BASE_NOTIONAL_USD * decision.confidence * (1.0 - 0.5 * decision.risk_score)
        order = IntendedOrder(
            window_start=decision.window_start,
            action=action,
            side=side,
            notional_usd=round(notional, 2),
            confidence=decision.confidence,
            risk_score=decision.risk_score,
            regime_label=decision.regime_label,
            note="DRY-RUN — no on-chain action taken (execution disabled).",
        )
        self.total_orders += 1
        return order

    def execute(self, order: IntendedOrder) -> None:
        """There is intentionally NO live execution path."""
        raise ExecutionNotAuthorizedError(
            "execute() is not implemented — this build cannot transact."
        )


def write_dry_run_orders(orders, path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for o in orders:
            if o is None:
                continue
            fh.write(json.dumps(o.to_dict(), default=str))
            fh.write("\n")
            n += 1
    return n


__all__ = [
    "StrategyRouter", "IntendedOrder", "ExecutionNotAuthorizedError",
    "write_dry_run_orders",
]
