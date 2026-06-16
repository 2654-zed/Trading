# D-022: Event bus implementation — asyncio queues + topic routing + backpressure

**Date**: 2026-05-27
**Made by**: agent (per D-020 pre-staged derivative decisions, sub-phase 3.1 close-out)
**Status**: ACTIVE

## Context

Per the Phase 3 spec, lenses publish to a bus and consumers subscribe to topics. The bus is the only sanctioned cross-lens / cross-consumer communication channel (I-15: lenses have no other interface). Sub-phase 3.1 required a working bus before any lens could be built.

## Decisions locked

### Topic routing: dot-notation + trailing `*` wildcard

Topics use dot-separated paths. The lens base class publishes to `signal.{lens_label}.{signal_type}` (e.g. `signal.graph.cluster_detected`).

Subscribers register a pattern. Supported pattern shapes:
- Exact match: `"signal.graph.cluster_detected"` matches that exact topic.
- Single-trailing-star: `"signal.graph.*"` matches any topic starting with `signal.graph.`. The star is multi-level (matches `signal.graph.x` AND `signal.graph.x.y`).
- Star-alone: `"*"` matches every topic.

Implementation: `_topic_matches(pattern, topic)` in `engine/core/event_bus.py`. Rejected alternatives:
- MQTT-style `+` single-level wildcards: rejected because we don't need single-level granularity at the engine scale.
- Regex patterns: rejected because they're a debugging hazard and overkill for sub-phase 3.1.

### Per-subscriber bounded asyncio queue + three backpressure modes

Each `Subscriber` owns its own `asyncio.Queue` with a configurable size (default 1024). The bus delivers a copy of the payload reference to each matching queue. Three backpressure modes selectable per-subscribe:

| Mode | Behavior on full queue | When to use |
|---|---|---|
| `BACKPRESSURE_BLOCK` | `publish()` awaits until queue has space | Critical consumers; backpressure must propagate to the publisher |
| `BACKPRESSURE_DROP_NEWEST` | New message dropped, `subscriber.dropped` incremented | Observability sinks where freshness < liveness |
| `BACKPRESSURE_DROP_OLDEST` | Oldest message in queue evicted, new message enqueued | Streaming consumers that always want the most recent N |

### Publish-time validation (enforces I-16)

`EventBus.publish(topic, signal)` calls `validate_signal(signal)` for every `Signal` payload. Invalid Signals are silently dropped (NOT raised) and `bus.validation_failure_count` is incremented. Why silent drop and not raise:
- Raising would let a single bad lens crash the publisher and cascade.
- The counter + structured log lets us detect schema drift at the bus boundary without dropping the system.
- Per I-16, validation failure is loud at the metric level — `bus.stats()` exposes the count.

### Acceptance test: 10K signals/sec, 2 subscribers, no drops

The acceptance test `test_stress_10k_signals_two_subscribers_no_drops` publishes 10,000 Signals to two matching subscribers and asserts every signal reached both queues with zero drops AND throughput stays under 10 seconds total. Empirically: ~98K signals/sec on a dev machine.

## Acceptance evidence

- 11 unit tests in `engine/tests/test_event_bus.py` cover all the surfaces above (topic matching, backpressure modes, validation behavior, closed-bus rejection, stats snapshot).
- Stress test passes well under the 10s ceiling.
- Sub-phase 3.1 smoke: 2,940 publishes → 2,940 deliveries to the consumer, **0 drops, 0 validation failures**.

## Reversal triggers

- A subsequent sub-phase adds a consumer that needs single-level wildcard routing (MQTT-style `+`) → extend `_topic_matches` in a D-NNN and add tests for the new pattern shape.
- Throughput hits the 10K/sec ceiling under real load (Phase 4 with bloxroute may push higher) → swap asyncio.Queue for a different primitive (e.g. lock-free ringbuffer) and re-run the stress test in a D-NNN.

## Links

- D-020 (Phase 3 spec approval) — parent decision
- D-021 (signal schema locked) — sibling decision (the validation contract this bus enforces)
- I-15 (lens independence — bus is the only inter-lens interface) — invariant served by this bus
- I-16 (Signal validation at every publish) — invariant enforced by this bus
- Code: `engine/core/event_bus.py`, tests: `engine/tests/test_event_bus.py`
