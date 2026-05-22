"""Phase 1.4 — JSONL opportunity logger with daily UTC rotation.

`OpportunityLogger` is the async writer used by the live runner. Producer
(detector callback) calls `submit(record)`; a single consumer task drains a
bounded `asyncio.Queue` and writes one JSON line per record to the day's log
file, rotating at 00:00 UTC.

Bounded queue + soft overflow per spec acceptance:
  "no log entry is dropped if disk writes are slower than detection rate
   (use bounded queue with explicit overflow logging rather than silent drop)"

Behavior on overflow:
  - Increment `overflow_count`.
  - Print a one-line warning to stderr with the dropped record's opportunity_id
    so it shows up in operator-visible output during the run.
  - Continue (do not raise / halt). User-approved soft semantics — a momentary
    disk hiccup must not kill a 30-day run.

Daily rotation:
  Each record's UTC date (the first 10 chars of `record["timestamp"]`) keys
  the output file: `{log_dir}/{YYYY-MM-DD}.jsonl`. When a record's date differs
  from the currently-open file's date, the writer closes the old handle and
  opens (append-mode) the new one. This handles natural midnight crossings as
  well as cases where the runner is restarted mid-day.

Append mode is intentional: a runner restart on the same day must not truncate
the day's accumulated records.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

from .schema import validate_record


_SHUTDOWN_SENTINEL: dict = {"__shutdown__": True}


class OpportunityLogger:
    """Async JSONL writer. Construct, await `start()`, `submit()` records,
    `await stop()` to drain and close cleanly.

    Producer side (`submit`) is non-blocking — uses `put_nowait` so the
    detector callback never awaits on disk. If the queue is full, the record
    is dropped (counted, logged); see module docstring for the rationale.

    Consumer side (`_consume`) writes serially. Each `flush()` after each
    record means partial-line tearing on crash is impossible — at worst we
    lose the last in-flight record.
    """

    def __init__(
        self,
        log_dir: Path,
        *,
        queue_size: int = 10_000,
        validate: bool = True,
    ) -> None:
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=queue_size)
        self._consumer_task: Optional[asyncio.Task] = None
        self._current_date: Optional[str] = None
        self._current_handle: Optional[IO] = None
        self._records_written = 0
        self._bytes_written = 0
        self._overflow_count = 0
        self._validate = validate

    @property
    def records_written(self) -> int:
        return self._records_written

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._consumer_task is not None:
            raise RuntimeError("OpportunityLogger.start() called twice")
        self._consumer_task = asyncio.create_task(self._consume(), name="opp_logger_consumer")

    async def stop(self) -> None:
        """Drain the queue, close the file handle. Idempotent."""
        if self._consumer_task is None:
            return
        # Push the sentinel — consumer will exit after processing remaining items.
        await self._queue.put(_SHUTDOWN_SENTINEL)
        try:
            await self._consumer_task
        finally:
            self._consumer_task = None
            if self._current_handle is not None:
                self._current_handle.close()
                self._current_handle = None

    def submit(self, record: dict) -> None:
        """Non-blocking enqueue. On full queue: increment overflow + log; never raise."""
        if self._validate:
            try:
                validate_record(record)
            except ValueError as e:
                # Invalid records are loud failures — don't silently swallow.
                print(
                    f"[opp_logger] REJECTED malformed record: {e} "
                    f"(opportunity_id={record.get('opportunity_id', '<unknown>')})",
                    file=sys.stderr, flush=True,
                )
                return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._overflow_count += 1
            opp_id = record.get("opportunity_id", "<unknown>")
            print(
                f"[opp_logger] OVERFLOW: queue full (max={self._queue.maxsize}); "
                f"dropped opportunity_id={opp_id}; total_dropped={self._overflow_count}",
                file=sys.stderr, flush=True,
            )

    async def _consume(self) -> None:
        while True:
            record = await self._queue.get()
            if record is _SHUTDOWN_SENTINEL:
                return
            self._write_record(record)

    def _write_record(self, record: dict) -> None:
        date_key = self._date_for_record(record)
        if date_key != self._current_date:
            self._rotate(date_key)
        # `compact_json` keeps records on one line and small; default separators
        # ensure no spaces between fields (smaller files, faster reads).
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        encoded = line.encode("utf-8")
        assert self._current_handle is not None
        self._current_handle.write(line)
        self._current_handle.flush()
        self._records_written += 1
        self._bytes_written += len(encoded)

    def _rotate(self, new_date: str) -> None:
        if self._current_handle is not None:
            self._current_handle.close()
            self._current_handle = None
        path = self._log_dir / f"{new_date}.jsonl"
        # Append: a same-day restart must not truncate the existing day's records.
        self._current_handle = path.open("a", encoding="utf-8")
        self._current_date = new_date

    def _date_for_record(self, record: dict) -> str:
        ts = record.get("timestamp")
        if isinstance(ts, str) and len(ts) >= 10:
            return ts[:10]
        # Fallback: use current UTC date. Should never trigger if records pass
        # validate_record — `timestamp` is a required field.
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


__all__ = ["OpportunityLogger"]
