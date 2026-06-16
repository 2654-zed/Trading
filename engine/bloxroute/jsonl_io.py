"""Shared forensic JSONL writers for the capture pipeline.

Two write disciplines, used by BOTH the mempool capture (Tier 0) and the
block-confirmation ingest (Tier 1) so the archive semantics are identical:

  * RotatingJsonlWriter — append-only data archive with hourly rotation
    and periodic flush (crash-tolerant; never dedups, drops, or edits).
  * HealthWriter — structured, ALWAYS-FLUSHED event log; a crash cannot
    lose the record of what happened. Never used for data payloads.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import msvcrt          # Windows single-instance file lock
except ImportError:        # non-Windows: lock is a no-op (always acquires)
    msvcrt = None


def acquire_singleton_lock(out_dir, name: str = ".capture.lock"):
    """Acquire an OS ADVISORY lock so only ONE collector writes to a given
    archive dir at a time. The bloXroute $300 tier is a SINGLE concurrent
    stream, so a second mempool capture MUST refuse to start. The lock
    auto-releases when the holding process dies — even on a hard kill or
    reboot — so there is no stale-lock problem (unlike a PID file).

    Returns the held file handle (keep it open for the process lifetime), or
    None if another live process already holds it. On non-Windows it is a
    no-op that always 'acquires' (the project is Windows-only in practice)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fh = (out_dir / name).open("a+")
    if msvcrt is None:
        return fh
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    return fh


class RotatingJsonlWriter:
    """Append-only JSONL archive with hourly rotation. Crash-tolerant
    (append mode, periodic flush)."""

    def __init__(self, out_dir: Path, prefix: str, flush_every: int = 200):
        self._dir = Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._prefix = prefix
        self._flush_every = flush_every
        self._fh = None
        self._hour_key = None
        self.written = 0

    def write(self, row: dict) -> None:
        hk = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
        if hk != self._hour_key:
            if self._fh:
                self._fh.close()
            self._fh = (self._dir / f"{self._prefix}{hk}.jsonl").open(
                "a", encoding="utf-8")
            self._hour_key = hk
        self._fh.write(json.dumps(row, default=str))
        self._fh.write("\n")
        self.written += 1
        if self.written % self._flush_every == 0:
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.flush(); self._fh.close()


class HealthWriter:
    """Structured, ALWAYS-FLUSHED health/event log (JSONL). Every write is
    flushed immediately so a crash cannot lose the record of what happened."""

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._fh = path.open("a", encoding="utf-8")
        self.written = 0

    def event(self, event: str, **detail) -> None:
        rec = {"event": event, "ts": time.time(), **detail}
        self._fh.write(json.dumps(rec, default=str) + "\n")
        self._fh.flush()
        self.written += 1

    def close(self) -> None:
        try:
            self._fh.flush(); self._fh.close()
        except OSError:
            pass


__all__ = ["RotatingJsonlWriter", "HealthWriter"]
