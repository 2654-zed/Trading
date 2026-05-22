"""Unit tests for OpportunityLogger.

Tests cover:
  - Write/read roundtrip (records read back match what was submitted)
  - Daily rotation when timestamp dates differ
  - Append mode (same-day re-init does not truncate)
  - Bounded queue + soft overflow on full queue (counter increments,
    warning to stderr, no exception raised)
  - Validation rejection (malformed records logged + dropped, not crashed)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from layer3_trading_exp.logger import OpportunityLogger


def _record(opp_id: str, ts: str = "2026-05-08T03:00:00+00:00", block: int = 1) -> dict:
    """A minimal but spec-required-fields-complete record."""
    return {
        "opportunity_id": opp_id,
        "timestamp": ts,
        "block_number": block,
        "chain": "base",
        "path": {"tokens": ["0x" + "11" * 20], "pools": ["0x" + "aa" * 20]},
        "pool_protocols": ["uniswap_v3"],
        "margin_gross_bps": 31,
        "gas_estimate_usd": 0.005,
        "expected_gain_usd": 26.0,
        "depth_notional_usd": 10_000.0,
        "filter_results": {},
        "layer3_freshness": {"stale_tables": [], "degraded": False},
        "hard_flagged": False,
        "soft_flagged": False,
        "flag_summary": [],
        "layer3_stale": False,
    }


def _drain(records_in: list[dict], log_dir: Path, **logger_kwargs) -> OpportunityLogger:
    """Helper: start a logger, submit each record, stop, return the logger.

    Returns the logger so caller can inspect counters.
    """
    async def go():
        logger = OpportunityLogger(log_dir, **logger_kwargs)
        await logger.start()
        for r in records_in:
            logger.submit(r)
        await logger.stop()
        return logger
    return asyncio.run(go())


def test_logger_write_read_roundtrip(tmp_path: Path):
    records = [_record(f"opp-{i}", block=i + 1) for i in range(5)]
    logger = _drain(records, tmp_path)
    log_path = tmp_path / "2026-05-08.jsonl"
    assert log_path.exists()
    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 5
    parsed = [json.loads(ln) for ln in lines]
    assert [r["opportunity_id"] for r in parsed] == [r["opportunity_id"] for r in records]
    assert logger.records_written == 5
    assert logger.bytes_written > 0


def test_logger_daily_rotation_at_midnight(tmp_path: Path):
    """Records straddling 23:59 and 00:00 must land in separate files."""
    records = [
        _record("late",  ts="2026-05-08T23:59:55+00:00"),
        _record("early", ts="2026-05-09T00:00:05+00:00"),
    ]
    _drain(records, tmp_path)
    assert (tmp_path / "2026-05-08.jsonl").exists()
    assert (tmp_path / "2026-05-09.jsonl").exists()
    parsed_d1 = [json.loads(ln) for ln in (tmp_path / "2026-05-08.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    parsed_d2 = [json.loads(ln) for ln in (tmp_path / "2026-05-09.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    assert [r["opportunity_id"] for r in parsed_d1] == ["late"]
    assert [r["opportunity_id"] for r in parsed_d2] == ["early"]


def test_logger_appends_on_same_day_restart(tmp_path: Path):
    """A second logger init for the same date must append to the existing file,
    not truncate it. Critical for runner restarts."""
    _drain([_record("first")], tmp_path)
    _drain([_record("second")], tmp_path)
    log_path = tmp_path / "2026-05-08.jsonl"
    parsed = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln]
    assert [r["opportunity_id"] for r in parsed] == ["first", "second"]


def test_logger_overflow_increments_counter_and_does_not_raise(tmp_path: Path, capsys):
    """With a tiny queue and validation disabled (so we can hammer fast), forcing
    overflow must:
      - increment overflow_count
      - emit a stderr warning per drop (containing the opportunity_id)
      - never raise
    """
    async def go():
        # Tiny queue. Disable consumer drainage by NOT calling start(); that
        # way put_nowait() fills the queue immediately and overflows after
        # `queue_size` puts. start() would let the consumer drain in real time.
        logger = OpportunityLogger(tmp_path, queue_size=2, validate=False)
        # Don't start — we want submits to hit the bounded queue without drain.
        for i in range(5):
            logger.submit(_record(f"opp-{i}"))
        # Now start so stop() can clean up properly. Consumer will drain
        # whatever's in the queue at start, but the 3 dropped records are
        # already counted.
        await logger.start()
        await logger.stop()
        return logger

    logger = asyncio.run(go())
    captured = capsys.readouterr()
    # 5 submitted, queue_size=2, so 3 overflowed.
    assert logger.overflow_count == 3
    # Stderr should mention the dropped opp ids.
    assert "OVERFLOW" in captured.err
    assert "opp-2" in captured.err  # first dropped after queue fills


def test_logger_rejects_malformed_records_with_validation(tmp_path: Path, capsys):
    """validate=True (default) → malformed records logged to stderr and dropped.
    No exception raised, no record written."""
    async def go():
        logger = OpportunityLogger(tmp_path, queue_size=10, validate=True)
        await logger.start()
        good = _record("good")
        bad = {"opportunity_id": "missing-fields"}  # incomplete
        logger.submit(good)
        logger.submit(bad)
        await logger.stop()
        return logger

    logger = asyncio.run(go())
    captured = capsys.readouterr()
    # `good` writes; `bad` is rejected.
    assert logger.records_written == 1
    assert "REJECTED" in captured.err


def test_logger_skip_validation_writes_anything(tmp_path: Path):
    """validate=False bypasses schema check (used by the overflow test)."""
    async def go():
        logger = OpportunityLogger(tmp_path, queue_size=10, validate=False)
        await logger.start()
        logger.submit({"opportunity_id": "lax", "timestamp": "2026-05-08T00:00:00+00:00"})
        await logger.stop()
        return logger

    logger = asyncio.run(go())
    assert logger.records_written == 1
    log_path = tmp_path / "2026-05-08.jsonl"
    parsed = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln]
    assert parsed[0]["opportunity_id"] == "lax"
