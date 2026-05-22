"""Local-DB sync status helper.

Per Addendum A #6, the experiment does NOT initiate sync against Railway. Jason
refreshes the local SQLite copy manually (railway ssh + sqlite3 .dump, or any method
he chooses). This module only reports what the experiment can see — file existence,
size, and mtime of the local copy. Per-table freshness lives in freshness.py.

Kept at filename sync.py to minimize spec divergence (Phase 1.0 file list in the
original spec names sync.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config, DEFAULT


@dataclass(frozen=True)
class LocalDBStatus:
    db_path: Path
    exists: bool
    size_bytes: Optional[int]
    mtime: Optional[datetime]
    age_seconds: Optional[float]


def check_local_db_status(
    cfg: Config = DEFAULT,
    now: Optional[datetime] = None,
) -> LocalDBStatus:
    now = now or datetime.now(timezone.utc)
    path = cfg.l3_db_path
    if not path.exists():
        return LocalDBStatus(
            db_path=path, exists=False, size_bytes=None, mtime=None, age_seconds=None
        )
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return LocalDBStatus(
        db_path=path,
        exists=True,
        size_bytes=stat.st_size,
        mtime=mtime,
        age_seconds=(now - mtime).total_seconds(),
    )
