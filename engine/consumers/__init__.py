"""Stub consumers for sub-phase 3.1. These get replaced by the real
synthesis + orchestrator components in sub-phases 3.2 and 3.3.

A "consumer" here is anything that subscribes to the event bus and
acts on Signals (logging, aggregation, persistence, downstream
processing). Per I-15, consumers may read across lenses freely — the
lens-independence rule only binds lenses.
"""

from .signal_logger import SignalLoggerConsumer

__all__ = ["SignalLoggerConsumer"]
