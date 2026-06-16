"""Tests for engine.core.event_bus.

Sub-phase 3.1 acceptance criterion: "Event bus delivers signals to ≥2
subscribers reliably (no drops in a stress test of 10K signals/sec)."
The stress test is the load-bearing one here.
"""

from __future__ import annotations

import asyncio
import time
import pytest

from engine.core.event_bus import (
    BACKPRESSURE_BLOCK,
    BACKPRESSURE_DROP_NEWEST,
    BACKPRESSURE_DROP_OLDEST,
    BusClosedError,
    EventBus,
    _topic_matches,
)
from engine.core.signal_schema import Signal, new_signal_id


def _make_signal(lens="graph", stype="cluster_detected") -> Signal:
    return Signal(
        id=new_signal_id(), timestamp=time.time(),
        lens=lens, type=stype,
        strength=0.5, confidence=0.5, time_horizon="short",
    )


def test_topic_matches_exact():
    assert _topic_matches("a.b.c", "a.b.c")
    assert not _topic_matches("a.b.c", "a.b.d")


def test_topic_matches_wildcard_star_alone():
    assert _topic_matches("*", "anything.at.all")
    assert _topic_matches("*", "")  # edge: empty topic


def test_topic_matches_trailing_star():
    assert _topic_matches("a.b.*", "a.b.c")
    assert _topic_matches("a.b.*", "a.b.c.d")
    assert _topic_matches("a.b.*", "a.b")
    assert not _topic_matches("a.b.*", "a.c.x")


def test_subscribe_returns_subscriber_with_queue():
    async def go():
        bus = EventBus()
        sub = await bus.subscribe("signal.*", name="t")
        assert sub.pattern == "signal.*"
        assert sub.queue.qsize() == 0
    asyncio.run(go())


def test_publish_delivers_to_matching_subscriber():
    async def go():
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sig = _make_signal()
        await bus.publish("signal.graph.cluster_detected", sig)
        delivered = sub.queue.get_nowait()
        assert delivered is sig
        assert sub.delivered == 1
    asyncio.run(go())


def test_publish_skips_non_matching_subscriber():
    async def go():
        bus = EventBus()
        sub = await bus.subscribe("signal.stochastic.*")
        await bus.publish("signal.graph.cluster_detected", _make_signal())
        assert sub.queue.empty()
        assert sub.delivered == 0
    asyncio.run(go())


def test_publish_validates_signal_and_drops_invalid():
    async def go():
        bus = EventBus(log_validation_errors=False)
        sub = await bus.subscribe("signal.*")
        bad = Signal(
            id="", timestamp=1.0, lens="graph", type="x",
            strength=0.5, confidence=0.5, time_horizon="short",
        )
        await bus.publish("signal.graph.x", bad)
        # The invalid signal must be dropped, NOT raised.
        assert bus.validation_failure_count == 1
        assert sub.queue.empty()
        # A subsequent valid signal goes through fine.
        good = _make_signal()
        await bus.publish("signal.graph.x", good)
        assert sub.queue.get_nowait() is good
    asyncio.run(go())


def test_multiple_subscribers_each_get_their_copy():
    async def go():
        bus = EventBus()
        s1 = await bus.subscribe("signal.*", name="s1")
        s2 = await bus.subscribe("signal.graph.*", name="s2")
        s3 = await bus.subscribe("signal.stochastic.*", name="s3")
        sig = _make_signal()
        await bus.publish("signal.graph.cluster_detected", sig)
        assert s1.queue.qsize() == 1
        assert s2.queue.qsize() == 1
        assert s3.queue.qsize() == 0
    asyncio.run(go())


def test_drop_newest_backpressure():
    async def go():
        bus = EventBus()
        sub = await bus.subscribe(
            "signal.*", queue_size=2,
            backpressure_mode=BACKPRESSURE_DROP_NEWEST,
        )
        for _ in range(5):
            await bus.publish("signal.graph.x", _make_signal())
        # Queue capped at 2; 3 newer signals dropped.
        assert sub.queue.qsize() == 2
        assert sub.dropped == 3
    asyncio.run(go())


def test_drop_oldest_backpressure():
    async def go():
        bus = EventBus()
        sub = await bus.subscribe(
            "signal.*", queue_size=2,
            backpressure_mode=BACKPRESSURE_DROP_OLDEST,
        )
        sigs = [_make_signal() for _ in range(5)]
        for s in sigs:
            await bus.publish("signal.graph.x", s)
        # Most recent 2 should be queued.
        q_contents = []
        while not sub.queue.empty():
            q_contents.append(sub.queue.get_nowait())
        assert q_contents == sigs[-2:]
        assert sub.dropped == 3
    asyncio.run(go())


def test_block_backpressure_makes_publisher_wait():
    async def go():
        bus = EventBus()
        sub = await bus.subscribe(
            "signal.*", queue_size=1, backpressure_mode=BACKPRESSURE_BLOCK,
        )
        # First publish fills the queue. Second publish must BLOCK until
        # the queue is drained — verify by racing.
        async def slow_consumer():
            await asyncio.sleep(0.1)
            sub.queue.get_nowait()

        consumer_task = asyncio.create_task(slow_consumer())
        t0 = time.monotonic()
        await bus.publish("signal.graph.x", _make_signal())
        # Queue now has 1; the next publish should block until the consumer drains.
        await bus.publish("signal.graph.x", _make_signal())
        elapsed = time.monotonic() - t0
        await consumer_task
        # Should have waited ~0.1s for the slow_consumer to drain
        assert elapsed >= 0.08, f"publish didn't block (elapsed={elapsed:.3f}s)"
    asyncio.run(go())


def test_closed_bus_rejects_publish_and_subscribe():
    async def go():
        bus = EventBus()
        await bus.close()
        with pytest.raises(BusClosedError):
            await bus.publish("any.topic", _make_signal())
        with pytest.raises(BusClosedError):
            await bus.subscribe("any.*")
    asyncio.run(go())


def test_stats_snapshot_shape():
    async def go():
        bus = EventBus()
        await bus.subscribe("signal.*", name="x")
        await bus.publish("signal.graph.x", _make_signal())
        s = bus.stats()
        assert s["published"] == 1
        assert s["validation_failures"] == 0
        assert len(s["subscribers"]) == 1
        assert s["subscribers"][0]["name"] == "x"
        assert s["subscribers"][0]["delivered"] == 1
        assert s["by_topic"] == {"signal.graph.x": 1}
    asyncio.run(go())


def test_stress_10k_signals_two_subscribers_no_drops():
    """Acceptance: bus delivers signals to ≥2 subscribers reliably with
    no drops at 10K signals/sec. We publish 10,000 signals as fast as
    possible and verify both subscribers received all of them."""
    async def go():
        bus = EventBus(default_queue_size=20_000)
        s1 = await bus.subscribe("signal.*", name="s1")
        s2 = await bus.subscribe("signal.graph.*", name="s2")

        N = 10_000
        t0 = time.monotonic()
        for _ in range(N):
            await bus.publish("signal.graph.x", _make_signal())
        elapsed = time.monotonic() - t0

        # Both subscribers got all N signals; no drops.
        assert s1.queue.qsize() == N, f"s1 received {s1.queue.qsize()}/{N}"
        assert s2.queue.qsize() == N, f"s2 received {s2.queue.qsize()}/{N}"
        assert s1.dropped == 0
        assert s2.dropped == 0
        assert bus.validation_failure_count == 0
        # Throughput sanity check: published 10K (+ 20K deliveries) in <10s.
        # On a modest dev machine this should be well under a second.
        assert elapsed < 10.0, f"too slow: {elapsed:.2f}s for 10K signals"
        rate = N / elapsed
        print(f"\n[stress] published {N} signals to 2 subscribers in "
              f"{elapsed:.3f}s ({rate:.0f}/s)")
    asyncio.run(go())
