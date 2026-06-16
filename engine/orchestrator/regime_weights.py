"""Regime-specific weight tables (sub-phase 3.3, blueprint § 5.2 / § 5.1).

Sub-phase 3.2 had a single static table. Sub-phase 3.3's regime engine
classifies each window; the orchestrator routes to the matching table
here. Each regime emphasizes the lenses most informative under that
condition:

  - trend:        stochastic drift dominates
  - chaotic:      stochastic volatility dominates, graph downweighted
  - low_liquidity:near-default, conservative
  - adversarial:  graph (subgraph anomalies) dominates
  - exploit_risk: graph + information (coordination + info shifts)

All tables include the 5 canonical lenses (topology + game weighted but
their lenses don't exist yet → contribute 0 in practice). All sum ~1.0.
"""

from __future__ import annotations

from .static_weights import INITIAL_WEIGHTS, validate_weights


REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "trend":         {"stochastic": 0.35, "graph": 0.20, "information": 0.10,
                      "topology": 0.15, "game": 0.20},
    "chaotic":       {"stochastic": 0.40, "graph": 0.15, "information": 0.15,
                      "topology": 0.15, "game": 0.15},
    "low_liquidity": {"stochastic": 0.20, "graph": 0.25, "information": 0.15,
                      "topology": 0.20, "game": 0.20},
    "adversarial":   {"stochastic": 0.10, "graph": 0.40, "information": 0.15,
                      "topology": 0.15, "game": 0.20},
    "exploit_risk":  {"stochastic": 0.10, "graph": 0.35, "information": 0.25,
                      "topology": 0.15, "game": 0.15},
    "default":       dict(INITIAL_WEIGHTS),
}

# Validate every table at import time — fail fast on a bad table.
for _name, _tbl in REGIME_WEIGHTS.items():
    validate_weights(_tbl)


def get_regime_weights(regime: str) -> dict[str, float]:
    """Return the weight table for a regime, falling back to default."""
    return dict(REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["default"]))


__all__ = ["REGIME_WEIGHTS", "get_regime_weights"]
