"""Graph lens — coordination + structure detection over the
deployer/contract/pool subgraph.

Per blueprint § 3.3: detects coordination + influence via centrality
metrics + subgraph detection. In Phase 3 sub-phase 3.1, the lens is
shallow but real: 3 distinct signal types backed by L3 lookups.
"""

from .lens import GraphLens

__all__ = ["GraphLens"]
