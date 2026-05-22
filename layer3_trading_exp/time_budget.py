"""Phase 2 sub-phase 2.2 — periodic time-budget watchdog.

Addresses the open `--minutes` timer-didn't-terminate failure
(`memory/failures/FAILURE_LOG.md` 2026-05-16). The Phase 1 implementation
checked the deadline only inside `on_block`. When `on_block` stopped
firing (WS dropped silently, or asyncio task starvation), the deadline
never fired and the container ran ~3 days past its `--minutes 1440`
budget on Railway.

This watchdog runs as its own asyncio background task. It wakes every
`check_interval_seconds` (default 5s) and, when `time.monotonic() >=
deadline`, cancels every other task in the loop. That cancellation
propagates through PoolMonitor's reconnect loops, the logger consumer,
the sync runner, and the daily rollup task — same shutdown path as the
kill switch.

The watchdog itself NEVER blocks on `on_block`, NEVER holds the event
loop, and NEVER depends on chain liveness. As long as the asyncio loop
itself is alive (which it is for any unhandled exception path), the
deadline fires.

Hard gate for Phase 2.8 deployment: `python -m
layer3_trading_exp.scripts.detect_dry_run --minutes 60` must terminate
within 90s of the deadline. The regression test is exercised in
`tests/test_time_budget.py`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional


async def time_budget_watchdog(
    deadline_monotonic: float,
    *,
    check_interval_seconds: float = 5.0,
    on_expiry_message: Optional[str] = "time budget reached",
    print_fn=print,
) -> None:
    """Background task: when `time.monotonic() >= deadline_monotonic`,
    cancel every other task in the running event loop.

    Returns normally after issuing cancellations; the loop's outer
    `asyncio.run` propagates the cancellations down to every other task.

    `print_fn` defaults to the builtin `print`; tests inject a capture.
    """
    while True:
        now = time.monotonic()
        if now >= deadline_monotonic:
            if on_expiry_message:
                print_fn(f"[time_budget] deadline reached "
                         f"(monotonic={now:.1f} >= {deadline_monotonic:.1f}); "
                         f"cancelling all tasks: {on_expiry_message}", flush=True)
            loop = asyncio.get_running_loop()
            current = asyncio.current_task()
            for task in asyncio.all_tasks(loop):
                if task is current:
                    continue
                task.cancel()
            return
        # Sleep until either the next check or the deadline, whichever is sooner.
        remaining = deadline_monotonic - now
        await asyncio.sleep(min(check_interval_seconds, max(0.1, remaining)))


__all__ = ["time_budget_watchdog"]
