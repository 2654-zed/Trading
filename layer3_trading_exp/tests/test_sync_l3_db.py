"""Unit tests for L3SyncRunner.

Mocked /dump endpoint — no live HTTP. Tests cover:
  - Schema bootstrap from first /dump response (probe with limit=1)
  - Incremental sync via since_ts cursor (only pulls rows past last cursor)
  - Full-sync path (no cursor_col) clears local table and rewrites
  - Per-table failure isolation (one table failing doesn't kill the cycle)
  - 403 surfaces as RuntimeError (loud failure on bad token)
  - Meta table tracks last_cursor + row counts correctly
"""

from __future__ import annotations

import json
import sqlite3
import unittest.mock
from pathlib import Path
from urllib.error import HTTPError

import pytest

from layer3_trading_exp.scripts.sync_l3_db import L3SyncRunner, TableSync


def _make_runner(tmp_path: Path, tables: tuple[TableSync, ...]) -> L3SyncRunner:
    return L3SyncRunner(
        base_url="http://l3.local:8000",
        token="test-token",
        local_db_path=tmp_path / "sync.db",
        tables=tables,
        page_limit=100,
    )


def _mock_dump_response(rows: list[dict]) -> bytes:
    return json.dumps({
        "table": "x", "total_all": len(rows), "total_filtered": len(rows),
        "total": len(rows), "offset": 0, "limit": 100, "count": len(rows),
        "rows": rows, "filter": {},
    }).encode("utf-8")


class _FakeResponse:
    """Mimics urllib's response context manager."""
    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status
    def read(self) -> bytes:
        return self._payload
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


def _make_urlopen(responses_by_url_substring: dict[str, list[bytes]]):
    """Build a fake urlopen that returns pre-canned bytes based on URL substring match.

    responses_by_url_substring keys are substrings checked in order; values are
    lists used as a queue (FIFO) per matching call. Useful for paginated calls.
    """
    cursor = {k: 0 for k in responses_by_url_substring}
    def _urlopen(url, timeout=None):
        for substr, queue in responses_by_url_substring.items():
            if substr in url:
                idx = cursor[substr]
                if idx >= len(queue):
                    return _FakeResponse(_mock_dump_response([]))
                cursor[substr] += 1
                return _FakeResponse(queue[idx])
        raise AssertionError(f"unexpected url: {url}")
    return _urlopen


def test_schema_bootstrap_creates_local_table_from_probe(tmp_path):
    """First sync of a new table: probe limit=1, infer columns, CREATE TABLE."""
    runner = _make_runner(tmp_path, (TableSync("contracts", "last_updated"),))
    rows_page1 = [{"id": "1", "address": "0xabc", "last_updated": "2026-05-08T00:00:00"}]
    fake_urlopen = _make_urlopen({"table=contracts": [_mock_dump_response(rows_page1)]})

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
        result = runner.sync_once()

    assert result["contracts"]["error"] is None
    assert result["contracts"]["rows"] == 1
    # Local table now exists with the right columns.
    cur = runner._conn.execute("PRAGMA table_info([contracts])")
    cols = {row[1] for row in cur.fetchall()}
    assert {"id", "address", "last_updated"}.issubset(cols)
    # Row was upserted.
    row = runner._conn.execute("SELECT id, address FROM contracts").fetchone()
    assert row == ("1", "0xabc")
    runner.close()


def test_incremental_sync_only_pulls_past_cursor(tmp_path):
    """Second sync should send since_ts=<last_seen> in the URL."""
    runner = _make_runner(tmp_path, (TableSync("contracts", "last_updated"),))

    # First sync: 2 rows with timestamps.
    rows_first = [
        {"id": "1", "address": "0xa", "last_updated": "2026-05-08T01:00:00"},
        {"id": "2", "address": "0xb", "last_updated": "2026-05-08T02:00:00"},
    ]
    # Second sync (after the first cursor): 1 new row at later ts.
    rows_second = [
        {"id": "3", "address": "0xc", "last_updated": "2026-05-08T03:00:00"},
    ]
    captured_urls: list[str] = []
    response_queue = [_mock_dump_response(rows_first), _mock_dump_response(rows_second)]

    def fake_urlopen(url, timeout=None):
        captured_urls.append(url)
        return _FakeResponse(response_queue.pop(0))

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
        runner.sync_once()
        runner.sync_once()

    # First call should not contain since_ts (empty cursor); second should.
    assert "since_ts" not in captured_urls[0] or "since_ts=&" in captured_urls[0] or "since_ts=2026" not in captured_urls[0]
    assert "since_ts=2026-05-08T02%3A00%3A00" in captured_urls[1]
    # Meta updated to the max cursor seen.
    cursor = runner._conn.execute(
        "SELECT last_cursor FROM _sync_meta WHERE table_name='contracts'"
    ).fetchone()
    assert cursor[0] == "2026-05-08T03:00:00"
    runner.close()


def test_full_sync_clears_local_before_rewrite(tmp_path):
    """A cursor_col=None table is full-replaced each cycle: pre-existing rows get cleared."""
    runner = _make_runner(tmp_path, (TableSync("org_wallets", None),))

    initial = [{"id": "1", "address": "0xaaa", "org_id": "org_001"}]
    revised = [{"id": "2", "address": "0xbbb", "org_id": "org_002"}]
    queue = [_mock_dump_response(initial), _mock_dump_response(revised)]

    def fake_urlopen(url, timeout=None):
        return _FakeResponse(queue.pop(0))

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
        runner.sync_once()
        runner.sync_once()

    # After second sync, only the revised row remains (initial is deleted).
    rows = runner._conn.execute("SELECT id FROM org_wallets ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["2"]
    runner.close()


def test_per_table_failure_does_not_kill_cycle(tmp_path):
    """One table 4xx-ing shouldn't stop the other table from syncing."""
    runner = _make_runner(tmp_path, (
        TableSync("trap_events", "timestamp"),
        TableSync("trust_amplification", "last_updated"),
    ))
    good_rows = [{"id": "1", "address": "0xa", "last_updated": "2026-05-08T01:00:00"}]

    def fake_urlopen(url, timeout=None):
        if "table=trap_events" in url:
            raise HTTPError(url, 500, "internal server error", {}, None)
        return _FakeResponse(_mock_dump_response(good_rows))

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen), \
         unittest.mock.patch("time.sleep"):  # skip backoff delays
        result = runner.sync_once()

    # trap_events failed; trust_amplification succeeded.
    assert result["trap_events"]["error"] is not None
    assert result["trap_events"]["rows"] == 0
    assert result["trust_amplification"]["error"] is None
    assert result["trust_amplification"]["rows"] == 1
    runner.close()


def test_403_is_loud_failure(tmp_path):
    """Bad token (403) MUST raise RuntimeError; a misconfigured deployment shouldn't
    silently sync zero rows."""
    runner = _make_runner(tmp_path, (TableSync("contracts", "last_updated"),))

    def fake_urlopen(url, timeout=None):
        raise HTTPError(url, 403, "forbidden", {}, None)

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen), \
         unittest.mock.patch("time.sleep"):
        result = runner.sync_once()

    # The runner catches per-table exceptions and stores them as 'error',
    # so the 403 surfaces in the result dict (not raised globally).
    err = result["contracts"]["error"] or ""
    assert "403" in err or "LAYER3_ADMIN_TOKEN" in err
    runner.close()


def test_meta_tracks_max_cursor_across_pages(tmp_path):
    """When a sync pulls multiple pages, last_cursor = max ts across all pages."""
    runner = L3SyncRunner(
        base_url="http://l3.local:8000",
        token="t",
        local_db_path=tmp_path / "s.db",
        tables=(TableSync("contracts", "last_updated"),),
        page_limit=2,  # force pagination
    )
    page1 = [
        {"id": "1", "last_updated": "2026-05-08T01:00:00"},
        {"id": "2", "last_updated": "2026-05-08T02:00:00"},
    ]
    page2 = [
        {"id": "3", "last_updated": "2026-05-08T03:00:00"},
    ]
    queue = [_mock_dump_response(page1), _mock_dump_response(page2)]

    def fake_urlopen(url, timeout=None):
        return _FakeResponse(queue.pop(0) if queue else _mock_dump_response([]))

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
        runner.sync_once()

    cursor = runner._conn.execute(
        "SELECT last_cursor FROM _sync_meta WHERE table_name='contracts'"
    ).fetchone()
    assert cursor[0] == "2026-05-08T03:00:00"
    runner.close()


def test_nested_dict_value_is_serialized_to_json(tmp_path):
    """When a row contains a nested object (e.g. JSON field), it's stored as a JSON
    string in the TEXT column — not stringified via Python str()."""
    runner = _make_runner(tmp_path, (TableSync("contracts", "last_updated"),))
    rows = [{"id": "1", "meta": {"a": 1, "b": [2, 3]}, "last_updated": "2026-05-08T01:00:00"}]

    def fake_urlopen(url, timeout=None):
        return _FakeResponse(_mock_dump_response(rows))

    with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
        runner.sync_once()

    stored = runner._conn.execute("SELECT meta FROM contracts WHERE id='1'").fetchone()
    parsed = json.loads(stored[0])
    assert parsed == {"a": 1, "b": [2, 3]}
    runner.close()


def test_sync_runs_from_worker_thread(tmp_path):
    """Regression test for Phase 1.6 first-deploy bug: L3SyncRunner is constructed
    in the main thread but `run_forever` dispatches `sync_once` via
    `asyncio.to_thread`, so the SQLite connection must support cross-thread use.
    Without `check_same_thread=False`, sqlite3 raises ProgrammingError mid-cycle
    and every table fails — silently — for the entire 7-day run.
    """
    import asyncio

    runner = _make_runner(tmp_path, (TableSync("contracts", "last_updated"),))
    rows = [{"id": "1", "address": "0xa", "last_updated": "2026-05-08T01:00:00"}]

    def fake_urlopen(url, timeout=None):
        return _FakeResponse(_mock_dump_response(rows))

    async def _go():
        with unittest.mock.patch("urllib.request.urlopen", fake_urlopen):
            return await asyncio.to_thread(runner.sync_once)

    result = asyncio.run(_go())
    assert result["contracts"]["error"] is None
    assert result["contracts"]["rows"] == 1
    runner.close()


def test_wal_mode_enabled(tmp_path):
    """The synced DB must be in WAL mode so concurrent reads (Layer3Client in
    main thread) don't block sync writes (worker thread) and vice versa."""
    runner = _make_runner(tmp_path, (TableSync("contracts", "last_updated"),))
    mode = runner._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    runner.close()


def test_from_config_raises_on_missing_token():
    """Constructor should refuse to build a runner without auth — saves us a
    confusing 403 cycle at runtime."""
    from layer3_trading_exp.config import Config

    class _Cfg:
        l3_dump_base_url = "http://example/"
        layer3_admin_token = ""
        l3_db_path = Path("/tmp/x.db")

    with pytest.raises(ValueError, match="token"):
        L3SyncRunner.from_config(_Cfg())
