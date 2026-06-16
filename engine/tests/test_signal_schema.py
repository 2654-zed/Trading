"""Tests for engine.core.signal_schema — Phase 3 sub-phase 3.1.

Per I-16: every Signal must validate. These tests pin down the
validation surface so future schema drift gets caught.
"""

from __future__ import annotations

import time
import pytest

from engine.core.signal_schema import (
    Signal,
    SignalValidationError,
    TIME_HORIZONS,
    VALID_LENSES,
    new_signal_id,
    validate_signal,
)


def _good() -> Signal:
    """A well-formed Signal for positive-path tests."""
    return Signal(
        id=new_signal_id(),
        timestamp=time.time(),
        lens="graph",
        type="cluster_detected",
        strength=0.5,
        confidence=0.7,
        time_horizon="medium",
        metadata={"k": "v"},
    )


def test_valid_signal_passes_validation():
    validate_signal(_good())  # should not raise


def test_valid_lenses_set_matches_blueprint():
    # The 5 canonical lenses from the blueprint § 3.
    assert VALID_LENSES == frozenset({
        "stochastic", "topology", "graph", "game", "information",
    })


def test_time_horizons_set():
    assert TIME_HORIZONS == frozenset({"short", "medium", "long"})


def test_invalid_lens_rejected():
    bad = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="not_a_real_lens",
        type="cluster_detected",
        strength=0.5, confidence=0.5, time_horizon="short",
    )
    with pytest.raises(SignalValidationError, match="lens"):
        validate_signal(bad)


def test_invalid_time_horizon_rejected():
    bad = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="graph", type="cluster_detected",
        strength=0.5, confidence=0.5,
        time_horizon="forever",
    )
    with pytest.raises(SignalValidationError, match="time_horizon"):
        validate_signal(bad)


def test_strength_out_of_range_rejected():
    for bad_val in (-0.1, 1.1, 100.0):
        bad = Signal(
            id=new_signal_id(), timestamp=time.time(),
            lens="graph", type="x", strength=bad_val, confidence=0.5,
            time_horizon="short",
        )
        with pytest.raises(SignalValidationError, match="strength"):
            validate_signal(bad)


def test_confidence_out_of_range_rejected():
    for bad_val in (-0.5, 2.0):
        bad = Signal(
            id=new_signal_id(), timestamp=time.time(),
            lens="graph", type="x", strength=0.5, confidence=bad_val,
            time_horizon="short",
        )
        with pytest.raises(SignalValidationError, match="confidence"):
            validate_signal(bad)


def test_empty_id_rejected():
    bad = Signal(
        id="", timestamp=time.time(),
        lens="graph", type="x", strength=0.5, confidence=0.5,
        time_horizon="short",
    )
    with pytest.raises(SignalValidationError, match="id"):
        validate_signal(bad)


def test_negative_timestamp_rejected():
    bad = Signal(
        id=new_signal_id(), timestamp=-1.0,
        lens="graph", type="x", strength=0.5, confidence=0.5,
        time_horizon="short",
    )
    with pytest.raises(SignalValidationError, match="timestamp"):
        validate_signal(bad)


def test_empty_type_rejected():
    bad = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="graph", type="",
        strength=0.5, confidence=0.5, time_horizon="short",
    )
    with pytest.raises(SignalValidationError, match="type"):
        validate_signal(bad)


def test_metadata_must_be_dict():
    bad = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="graph", type="x", strength=0.5, confidence=0.5,
        time_horizon="short",
        metadata=["not", "a", "dict"],  # type: ignore[arg-type]
    )
    with pytest.raises(SignalValidationError, match="metadata"):
        validate_signal(bad)


def test_vector_must_be_numeric_sequence():
    bad = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="graph", type="x", strength=0.5, confidence=0.5,
        time_horizon="short",
        vector=["a", "b"],  # type: ignore[list-item]
    )
    with pytest.raises(SignalValidationError, match="vector"):
        validate_signal(bad)


def test_vector_empty_rejected():
    bad = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="graph", type="x", strength=0.5, confidence=0.5,
        time_horizon="short",
        vector=[],
    )
    with pytest.raises(SignalValidationError, match="vector"):
        validate_signal(bad)


def test_vector_valid_numeric_passes():
    good = Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens="graph", type="x", strength=0.5, confidence=0.5,
        time_horizon="short",
        vector=[0.1, 0.2, 0.3],
    )
    validate_signal(good)


def test_to_dict_round_trip_shape():
    sig = _good()
    d = sig.to_dict()
    for f in ("id", "timestamp", "lens", "type", "strength", "confidence",
              "time_horizon", "metadata"):
        assert f in d
    # vector omitted when None
    assert "vector" not in d

    sig2 = Signal(**{**{
        "id": new_signal_id(), "timestamp": time.time(),
        "lens": "graph", "type": "x", "strength": 0.5, "confidence": 0.5,
        "time_horizon": "short",
    }, "vector": [1.0, 2.0]})
    d2 = sig2.to_dict()
    assert d2["vector"] == [1.0, 2.0]


def test_non_signal_input_rejected():
    with pytest.raises(SignalValidationError):
        validate_signal({"not": "a Signal"})  # type: ignore[arg-type]


def test_signal_is_frozen():
    sig = _good()
    with pytest.raises(Exception):
        sig.strength = 0.9  # type: ignore[misc]
