"""Tests for sync.py — local DB status reporting.

Per Addendum A #6 the experiment does not sync from Railway; this module reports only
what the local filesystem knows about the Layer 3 SQLite copy.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from layer3_trading_exp import sync as sync_module
from layer3_trading_exp.config import Config


def _cfg_pointing_at(path: Path) -> Config:
    return Config(l3_db_path=path)


def test_missing_db_reports_non_existent(tmp_path):
    cfg = _cfg_pointing_at(tmp_path / "does_not_exist.db")
    status = sync_module.check_local_db_status(cfg)
    assert status.exists is False
    assert status.size_bytes is None
    assert status.mtime is None
    assert status.age_seconds is None


def test_present_db_reports_size_and_mtime(tmp_path):
    db = tmp_path / "local.db"
    db.write_bytes(b"SQLite format 3\x00")
    cfg = _cfg_pointing_at(db)

    status = sync_module.check_local_db_status(cfg)

    assert status.exists is True
    assert status.size_bytes == len(b"SQLite format 3\x00")
    assert status.mtime is not None
    assert status.mtime.tzinfo is not None
    assert status.age_seconds is not None
    assert status.age_seconds >= 0


def test_age_reflects_custom_now(tmp_path):
    db = tmp_path / "local.db"
    db.write_bytes(b"x")
    cfg = _cfg_pointing_at(db)

    real_mtime = datetime.fromtimestamp(db.stat().st_mtime, tz=timezone.utc)
    later = real_mtime + timedelta(hours=5)

    status = sync_module.check_local_db_status(cfg, now=later)

    assert status.age_seconds is not None
    assert 5 * 3600 - 5 < status.age_seconds < 5 * 3600 + 5
