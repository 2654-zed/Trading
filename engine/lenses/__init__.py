"""Lens implementations. Each lens is an independent mathematical
interpretation of raw blockchain state.

Per I-15: no lens may read another lens's outputs, internal state, or
Signal stream. Lens-to-lens imports are forbidden. Each lens may read
raw blockchain state + L3 corpus + its own history; only the
orchestrator (downstream) composes across lenses.

Sub-phase 3.1 ships ONE lens (graph). Sub-phase 3.2 adds stochastic +
information. Sub-phase 3.3 + future sub-phases add topology + game.
"""

from ._base import Lens

__all__ = ["Lens"]
