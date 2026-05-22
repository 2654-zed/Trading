"""Unit tests for time_budget.py (Phase 2 sub-phase 2.2, D-009).

Regression test for the open `--minutes` timer-didn't-terminate failure
(memory/failures/FAILURE_LOG.md 2026-05-16). The watchdog MUST fire
even if `on_block` stops being called (e.g. WS silent disconnect).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from layer3_trading_exp.time_budget import time_budget_watchdog


def test_watchdog_fires_when_deadline_reached_and_cancels_peers():
    """Deadline-arrived path: watchdog cancels every other task in the loop."""
    cancelled: list[str] = []

    async def stuck_task(name: str):
        try:
            # Simulate the failure mode: a task that NEVER yields back via
            # on_block (e.g. silently-dropped WS). The original timer bug
            # was that the deadline check only fired inside on_block.
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    async def driver():
        deadline = time.monotonic() + 0.2  # 200 ms
        peer1 = asyncio.create_task(stuck_task("peer1"))
        peer2 = asyncio.create_task(stuck_task("peer2"))
        watchdog = asyncio.create_task(time_budget_watchdog(
            deadline,
            check_interval_seconds=0.05,
            on_expiry_message=None,
            print_fn=lambda *a, **k: None,
        ))
        # Drive all tasks until watchdog cancels its peers.
        try:
            await asyncio.gather(peer1, peer2, watchdog, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())

    assert set(cancelled) == {"peer1", "peer2"}, \
        f"expected both peers cancelled, got {cancelled}"


def test_watchdog_does_not_fire_before_deadline():
    """Negative path: no cancellations before the deadline arrives."""
    cancelled: list[str] = []

    async def short_task(name: str):
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    async def driver():
        # Set deadline 5 seconds in the future; peer finishes well before.
        deadline = time.monotonic() + 5.0
        peer = asyncio.create_task(short_task("peer"))
        watchdog = asyncio.create_task(time_budget_watchdog(
            deadline,
            check_interval_seconds=0.5,
            print_fn=lambda *a, **k: None,
        ))
        await peer
        # Watchdog is still running; cancel it so the test ends.
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())
    assert cancelled == [], f"watchdog cancelled tasks prematurely: {cancelled}"


def test_watchdog_does_not_cancel_itself():
    """Self-protection: the watchdog must skip its own task when cancelling
    peers, otherwise the cancellation chain races against the watchdog's
    own return."""
    async def driver():
        deadline = time.monotonic() + 0.1
        # Run alone (no peers) — verifies normal return when only self exists.
        await time_budget_watchdog(
            deadline,
            check_interval_seconds=0.05,
            print_fn=lambda *a, **k: None,
        )

    # Should complete (not hang) and not raise.
    asyncio.run(driver())


def test_watchdog_prints_expiry_message_when_provided():
    """Operator-facing print: when on_expiry_message is set, the watchdog
    prints a line so the operator sees deadline-arrived events in logs."""
    captured_lines: list[str] = []

    def capture(*args, **kwargs):
        captured_lines.append(" ".join(str(a) for a in args))

    async def driver():
        deadline = time.monotonic() + 0.1
        await time_budget_watchdog(
            deadline,
            check_interval_seconds=0.05,
            on_expiry_message="test message",
            print_fn=capture,
        )

    asyncio.run(driver())
    assert any("test message" in line for line in captured_lines), \
        f"expected expiry message in captured prints; got {captured_lines}"
    assert any("deadline reached" in line for line in captured_lines)
