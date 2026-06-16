"""Integration test: reconnect + feed-health events against a LOCAL mock
WebSocket server (decision #4). Never touches bloXroute. Verifies the
hardened capture loop: structured connect/subscribed/disconnect/reconnect
events, monotonic seq, constant source, and the clock sample landing on
every archived row.
"""

from __future__ import annotations

import asyncio
import json
from argparse import Namespace

import pytest

websockets = pytest.importorskip("websockets")

from engine.scripts import run_mempool_capture as run_mod


def _stub_clock(timeout: float = 5.0) -> dict:
    # deterministic, instant stand-in for the read-only w32tm probe
    return {"clock_synced": True, "clock_offset_ms": 1.5, "stratum": 3,
            "source": "stub-ntp", "last_sync": "x", "leap_indicator": 0,
            "error": None}


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def test_reconnect_and_feed_health_against_mock_ws(tmp_path, monkeypatch):
    asyncio.run(_scenario(tmp_path, monkeypatch))

    health = _read_jsonl(tmp_path / "feed_health.jsonl")
    events = [e["event"] for e in health]
    # the mock drops the connection after each short burst -> the loop must
    # connect, subscribe, observe a disconnect, and reconnect repeatedly.
    assert events.count("connect") >= 2, events
    assert events.count("subscribed") >= 2, events
    assert events.count("reconnect") >= 1, events
    assert events.count("disconnect") >= 1, events

    rows = _load_archive(tmp_path)
    assert len(rows) >= 2, f"expected captured rows, got {len(rows)}"
    seqs = [r["seq"] for r in rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)   # strictly monotonic
    assert {r["source"] for r in rows} == {"blxr-cloud-ws-eth"}
    assert all(r["clock_synced"] is True for r in rows)           # sample landed
    assert all(r["clock_offset_ms"] == 1.5 for r in rows)

    # clock_health.jsonl carries the startup + per-connect probes
    clock = _read_jsonl(tmp_path / "clock_health.jsonl")
    assert clock and all(c["event"] == "clock_status" for c in clock)
    assert any(c.get("reason") == "startup" for c in clock)


def _load_archive(tmp_path):
    rows = []
    for p in sorted(tmp_path.glob("mempool_*.jsonl")):
        rows += _read_jsonl(p)
    return rows


async def _scenario(tmp_path, monkeypatch):
    conn = {"n": 0}

    async def handler(ws, *_):
        conn["n"] += 1
        n = conn["n"]
        try:
            for i in range(2):
                await ws.send(json.dumps({"params": {"result": {
                    "txHash": f"0x{n:03d}{i:03d}",
                    "txContents": {"from": f"0xsender{n}{i}",
                                   "to": "0xpool", "input": "0x"}}}}))
            await asyncio.sleep(0.1)   # let the client recv before we drop it
        except Exception:
            pass
        # returning closes the connection -> client sees ConnectionClosed

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("BLOXROUTE_AUTH_HEADER", "test-token")
        monkeypatch.setenv("BLOXROUTE_WS_URL", f"ws://localhost:{port}")
        monkeypatch.setattr(run_mod, "query_clock_status", _stub_clock)
        args = Namespace(
            minutes=0.06, stats_interval=999.0, clock_interval=999.0,
            competition_window=2.0, out_dir=tmp_path,
            config=tmp_path / "nonexistent.json",
        )
        rc = await run_mod.run(args)
        assert rc == 0
