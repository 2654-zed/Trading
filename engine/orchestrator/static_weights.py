"""Static weight tables for the orchestrator skeleton (sub-phase 3.2).

Per blueprint § 5.2 + D-026 (user direction 2026-05-27): all 5 canonical
lenses appear in the table. Lenses not yet implemented (topology, game)
contribute weight × 0 = 0 to aggregate scores until their lenses ship
in sub-phase 3.3. We keep them in the table to preserve identity for
the future, not strip-and-restore.

Sub-phase 3.3 introduces regime-aware weight tables that replace this
static table. This file is the v1 baseline.
"""

from __future__ import annotations

from ..core.signal_schema import VALID_LENSES


# Blueprint § 5.2 initial weights.
INITIAL_WEIGHTS: dict[str, float] = {
    "stochastic":  0.20,
    "topology":    0.20,
    "graph":       0.25,
    "game":        0.25,
    "information": 0.10,
}


def validate_weights(weights: dict[str, float]) -> None:
    """Sanity-check a weights table at construction time.

    Rules:
      - Every weight key must be a canonical lens (∈ VALID_LENSES).
      - Every weight value must be in [0.0, 1.0].
      - Sum should be in [0.95, 1.05] (i.e. ~normalized).
        We don't enforce exactly 1.0 because the blueprint table sums
        to 1.0 but future regime-specific tables may not.
    """
    for k in weights:
        if k not in VALID_LENSES:
            raise ValueError(
                f"weight key {k!r} is not a canonical lens "
                f"(expected one of {sorted(VALID_LENSES)})"
            )
    for k, v in weights.items():
        if not (0.0 <= float(v) <= 1.0):
            raise ValueError(f"weight {k}={v!r} not in [0.0, 1.0]")
    s = sum(weights.values())
    if not (0.95 <= s <= 1.05):
        raise ValueError(f"weights sum {s:.4f} not in [0.95, 1.05]")


def get_lens_weights(regime: str = "default") -> dict[str, float]:
    """Return the weights dict for a given regime.

    Sub-phase 3.2 has only the "default" regime. Sub-phase 3.3's regime
    engine will introduce per-regime tables; this function's signature
    is forward-compatible so the orchestrator can be wired without
    refactoring once regimes land.
    """
    if regime != "default":
        # Forward-compatibility: callers may pass regime labels;
        # until 3.3, they all map to the same table.
        pass
    return dict(INITIAL_WEIGHTS)


__all__ = ["INITIAL_WEIGHTS", "validate_weights", "get_lens_weights"]
