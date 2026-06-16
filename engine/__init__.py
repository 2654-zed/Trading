"""Multi-Lens Decision Engine (Phase 3).

A separate top-level package per D-020 (2026-05-25). Reads raw
blockchain state via adapters that wrap Phase 1+2 infrastructure;
produces normalized Signal objects from independent mathematical
lenses; routes them through an event bus to a cross-lens synthesis
engine and adjudicating orchestrator.

Build discipline: each sub-phase has explicit acceptance criteria.
See `PHASE_3_MULTI_LENS_ENGINE_SPEC.md`.
"""

__version__ = "0.1.0-sub-phase-3.1"
