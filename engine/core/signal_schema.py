"""Signal schema — the universal contract every lens MUST emit.

Per blueprint § 2 + I-16: this is the data contract. If lenses produce
inconsistent shapes, the entire downstream pipeline (synthesis,
orchestrator, feedback) collapses. `validate_signal()` enforces shape
at every event-bus publish; the publish-side check is the only safety
net against schema drift.

Schema design notes:
- `id` is a UUID string. Generated at emit time by the lens.
- `timestamp` is a Unix epoch float (seconds since epoch). Lenses set it
  at emit time; downstream consumers do NOT rewrite it.
- `lens` is one of 5 strings (the canonical lens set from blueprint § 3).
  Adding new lenses requires updating VALID_LENSES + the orchestrator
  weight table.
- `type` is free-form (e.g. "cluster_detected", "entropy_drop"). Each
  lens publishes its own taxonomy; the orchestrator can semantic-group
  via the `metadata` field.
- `strength` and `confidence` are independent dimensions in [0.0, 1.0].
  Strength = magnitude of the underlying signal. Confidence = how
  certain the lens is that the signal is real (vs noise).
- `time_horizon` is one of {"short", "medium", "long"}. Lens-defined
  semantics; convention: short ≤ 5 min, medium ≤ 1 hour, long > 1 hour.
- `metadata` carries lens-specific detail (the addresses involved, the
  computed numbers, etc.). The orchestrator may semantically group on
  metadata fields but doesn't depend on any specific keys.
- `vector` is optional. When present, it's an embedding for similarity
  comparison in synthesis. Length is lens-defined (typically 32-256).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional


# Locked at sub-phase 3.1. Adding a new lens requires updating this AND
# the orchestrator's weight table + regime → weights routing.
VALID_LENSES: frozenset[str] = frozenset({
    "stochastic", "topology", "graph", "game", "information",
})

TIME_HORIZONS: frozenset[str] = frozenset({"short", "medium", "long"})


class SignalValidationError(ValueError):
    """Raised by validate_signal when a Signal violates the schema.

    Carries the field name + offending value for diagnostic clarity.
    Per I-16: validation failure is loud, not silent.
    """
    def __init__(self, field_name: str, value: Any, reason: str):
        super().__init__(f"Signal.{field_name} invalid: {value!r} — {reason}")
        self.field_name = field_name
        self.value = value
        self.reason = reason


@dataclass(frozen=True)
class Signal:
    """The universal lens output contract.

    Frozen so signals don't get mutated mid-pipeline. To "modify" a
    signal (e.g. annotation by filter pipeline), the annotator emits a
    new Signal with the original's metadata merged in.
    """
    id: str
    timestamp: float
    lens: str   # one of VALID_LENSES
    type: str
    strength: float    # [0.0, 1.0]
    confidence: float  # [0.0, 1.0]
    time_horizon: str  # one of TIME_HORIZONS
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: Optional[list[float]] = None

    def to_dict(self) -> dict:
        """JSON-serializable representation. Used by ledger writers."""
        d = {
            "id": self.id,
            "timestamp": self.timestamp,
            "lens": self.lens,
            "type": self.type,
            "strength": self.strength,
            "confidence": self.confidence,
            "time_horizon": self.time_horizon,
            "metadata": dict(self.metadata),
        }
        if self.vector is not None:
            d["vector"] = list(self.vector)
        return d


def new_signal_id() -> str:
    """Standard ID generator. Centralized so the ID shape is stable."""
    return str(uuid.uuid4())


def validate_signal(signal: Signal) -> None:
    """Validate a Signal against the schema. Raises SignalValidationError
    on the first violation. Returns None on success.

    Called by EventBus.publish on every publish. Lens implementations
    may also call this defensively during construction; the publish-side
    check is the authoritative one.

    Validation rules:
      - `id` is a non-empty string
      - `timestamp` is a positive number
      - `lens` is in VALID_LENSES
      - `type` is a non-empty string
      - `strength` is a float in [0.0, 1.0]
      - `confidence` is a float in [0.0, 1.0]
      - `time_horizon` is in TIME_HORIZONS
      - `metadata` is a dict (not None, not a sequence)
      - `vector` is None or a non-empty list of floats
    """
    if not isinstance(signal, Signal):
        raise SignalValidationError(
            "<root>", type(signal).__name__,
            "value is not a Signal instance",
        )
    if not isinstance(signal.id, str) or not signal.id:
        raise SignalValidationError("id", signal.id, "must be a non-empty string")
    if not isinstance(signal.timestamp, (int, float)):
        raise SignalValidationError(
            "timestamp", signal.timestamp,
            "must be a numeric Unix epoch seconds value",
        )
    if signal.timestamp <= 0:
        raise SignalValidationError(
            "timestamp", signal.timestamp, "must be > 0",
        )
    if signal.lens not in VALID_LENSES:
        raise SignalValidationError(
            "lens", signal.lens,
            f"must be one of {sorted(VALID_LENSES)}",
        )
    if not isinstance(signal.type, str) or not signal.type:
        raise SignalValidationError(
            "type", signal.type, "must be a non-empty string",
        )
    if not isinstance(signal.strength, (int, float)):
        raise SignalValidationError(
            "strength", signal.strength, "must be numeric",
        )
    if not (0.0 <= float(signal.strength) <= 1.0):
        raise SignalValidationError(
            "strength", signal.strength, "must be in [0.0, 1.0]",
        )
    if not isinstance(signal.confidence, (int, float)):
        raise SignalValidationError(
            "confidence", signal.confidence, "must be numeric",
        )
    if not (0.0 <= float(signal.confidence) <= 1.0):
        raise SignalValidationError(
            "confidence", signal.confidence, "must be in [0.0, 1.0]",
        )
    if signal.time_horizon not in TIME_HORIZONS:
        raise SignalValidationError(
            "time_horizon", signal.time_horizon,
            f"must be one of {sorted(TIME_HORIZONS)}",
        )
    if not isinstance(signal.metadata, dict):
        raise SignalValidationError(
            "metadata", type(signal.metadata).__name__,
            "must be a dict",
        )
    if signal.vector is not None:
        if not isinstance(signal.vector, (list, tuple)):
            raise SignalValidationError(
                "vector", type(signal.vector).__name__,
                "must be None or a list/tuple of floats",
            )
        if len(signal.vector) == 0:
            raise SignalValidationError(
                "vector", signal.vector,
                "must be None or a non-empty sequence",
            )
        if not all(isinstance(v, (int, float)) for v in signal.vector):
            raise SignalValidationError(
                "vector", "<sequence>",
                "all elements must be numeric",
            )


__all__ = [
    "Signal",
    "SignalValidationError",
    "TIME_HORIZONS",
    "VALID_LENSES",
    "new_signal_id",
    "validate_signal",
]
