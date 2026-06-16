"""Engine core abstractions: Signal schema + event bus.

Per I-16 (signal schema normalization): every lens MUST emit Signal
objects conforming to `signal_schema.Signal`. `validate_signal()` is
invoked at every event-bus publish; non-conforming signals are dropped
with a loud error.

Per blueprint § 8: event bus is asyncio-based pub/sub with topic
routing. Subscribers register topic prefixes; the bus delivers each
published signal to every matching subscriber.
"""

from .signal_schema import (
    Signal,
    SignalValidationError,
    TIME_HORIZONS,
    VALID_LENSES,
    validate_signal,
)
from .event_bus import EventBus, BusClosedError, Subscriber

__all__ = [
    "Signal",
    "SignalValidationError",
    "TIME_HORIZONS",
    "VALID_LENSES",
    "validate_signal",
    "EventBus",
    "BusClosedError",
    "Subscriber",
]
