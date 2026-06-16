"""Tests for the Tier-1 confirmation-side ingest (pure, network-free)."""

from __future__ import annotations

import sys

import pytest

from engine.bloxroute.block_ingest import (
    BlockPoller, RpcError, parse_block,
)
from engine.bloxroute.jsonl_io import RotatingJsonlWriter, acquire_singleton_lock

MINER = "0x" + "ab" * 20


def _block(number, tx_hashes, timestamp=1_750_000_000, miner=MINER):
    return {"number": hex(number), "timestamp": hex(timestamp),
            "miner": miner, "transactions": list(tx_hashes)}


# ----- parse_block ---------------------------------------------------------

def test_parse_block_hash_list():
    rows = parse_block(_block(100, ["0xAA", "0xBB"]), 123.5, "https://rpc.x",
                       clock_synced=False, clock_offset_ms=None)
    assert len(rows) == 2
    r = rows[0]
    assert r["hash"] == "0xaa"
    assert r["block_number"] == 100
    assert r["block_timestamp"] == 1_750_000_000
    assert r["block_received_local_ts"] == 123.5
    assert r["builder_or_fee_recipient"] == MINER.lower()
    assert r["tx_index"] == 0
    assert r["source"] == "public-eth-rpc:https://rpc.x"
    assert r["clock_synced"] is False
    assert rows[1]["tx_index"] == 1


def test_parse_block_full_tx_objects():
    blk = _block(7, [])
    blk["transactions"] = [{"hash": "0xCC", "from": "0x1"},
                           {"hash": "0xDD", "from": "0x2"}]
    rows = parse_block(blk, 1.0, "u")
    assert [r["hash"] for r in rows] == ["0xcc", "0xdd"]


def test_parse_block_empty_block():
    assert parse_block(_block(5, []), 1.0, "u") == []


def test_parse_block_invalid_structure_raises():
    with pytest.raises(RpcError):
        parse_block({"timestamp": "0x1"}, 1.0, "u")      # missing number
    with pytest.raises(RpcError):
        parse_block({"number": None, "timestamp": "0x1"}, 1.0, "u")


def test_parse_block_unexpected_tx_type_raises():
    blk = _block(5, [])
    blk["transactions"] = [42]
    with pytest.raises(RpcError):
        parse_block(blk, 1.0, "u")


def test_parse_block_missing_miner_raises():
    # loud failure: a block header without `miner` is structurally broken;
    # never emit rows with an empty builder_or_fee_recipient.
    blk = _block(5, ["0x1"])
    del blk["miner"]
    with pytest.raises(RpcError):
        parse_block(blk, 1.0, "u")
    blk["miner"] = None
    with pytest.raises(RpcError):
        parse_block(blk, 1.0, "u")


def test_parse_block_tx_object_missing_hash_raises():
    blk = _block(5, [])
    blk["transactions"] = [{"from": "0x1"}]    # dict without hash
    with pytest.raises(RpcError):
        parse_block(blk, 1.0, "u")


# ----- BlockPoller ---------------------------------------------------------

class FakeRpc:
    """Scriptable fetch: maps (method, key) -> value or exception. Records
    calls. Blocks are keyed by int number; head by 'head'."""

    def __init__(self, head, blocks, fail=None):
        self.head = head            # int or callable -> int
        self.blocks = blocks        # {int: block dict | None | RpcError}
        self.fail = fail or {}      # method -> RpcError to raise
        self.calls = []

    def __call__(self, url, method, params, timeout=10.0):
        self.calls.append((url, method, tuple(params)))
        if method in self.fail:
            raise self.fail[method]
        if method == "eth_blockNumber":
            h = self.head() if callable(self.head) else self.head
            return hex(h)
        if method == "eth_getBlockByNumber":
            n = int(params[0], 16)
            v = self.blocks.get(n, None)
            if isinstance(v, Exception):
                raise v
            return v
        raise AssertionError(f"unexpected method {method}")


def _events_collector():
    events = []
    def emit(event, **detail):
        events.append({"event": event, **detail})
    return events, emit


def test_first_poll_starts_at_tip():
    rpc = FakeRpc(head=100, blocks={100: _block(100, ["0x1", "0x2"])})
    p = BlockPoller(rpcs=["u1"], fetch=rpc)
    rows = p.poll()
    assert p.last_processed == 100
    assert [r["block_number"] for r in rows] == [100, 100]
    # it did NOT backfill 99 and below
    assert all(c[1] != "eth_getBlockByNumber" or int(c[2][0], 16) == 100
               for c in rpc.calls)


def test_advances_and_fills_gaps_in_order():
    rpc = FakeRpc(head=100, blocks={
        100: _block(100, ["0xa"]), 101: _block(101, ["0xb"]),
        102: _block(102, []), 103: _block(103, ["0xc", "0xd"])})
    p = BlockPoller(rpcs=["u1"], fetch=rpc)
    p.poll()                                  # processes 100
    rpc.head = 103                            # head jumps +3
    rows = p.poll()
    assert p.last_processed == 103
    assert [r["block_number"] for r in rows] == [101, 103, 103]  # 102 empty
    nums = [int(c[2][0], 16) for c in rpc.calls
            if c[1] == "eth_getBlockByNumber"]
    assert nums == [100, 101, 102, 103]       # strictly in order


def test_null_block_stops_cycle_and_retries_same_number():
    # lagging load-balanced backend: head says 101 exists, block fetch -> None
    rpc = FakeRpc(head=100, blocks={100: _block(100, ["0xa"]), 101: None})
    events, emit = _events_collector()
    p = BlockPoller(rpcs=["u1"], fetch=rpc, health=emit)
    p.poll()                                  # tip-start: processes 100
    rpc.head = 101                            # head advances; block 101 lags
    assert p.poll() == []
    assert p.last_processed == 100            # 101 NOT advanced past
    assert any(e["event"] == "block_not_yet_available" and e["block"] == 101
               for e in events)
    rpc.blocks[101] = _block(101, ["0xb"])    # backend catches up
    rows = p.poll()
    assert p.last_processed == 101            # retried, not skipped
    assert rows and rows[0]["hash"] == "0xb"


def test_rpc_error_rotates_endpoint_without_advancing():
    rpc = FakeRpc(head=100, blocks={100: RpcError("boom")})
    events, emit = _events_collector()
    p = BlockPoller(rpcs=["u1", "u2"], fetch=rpc, health=emit)
    rows = p.poll()
    assert rows == []
    assert p.last_processed is None           # nothing advanced
    assert p.current_rpc == "u2"              # rotated
    assert any(e["event"] == "rpc_error" for e in events)
    assert any(e["event"] == "rpc_rotate" for e in events)


def test_head_fetch_error_rotates():
    rpc = FakeRpc(head=100, blocks={}, fail={"eth_blockNumber": RpcError("down")})
    events, emit = _events_collector()
    p = BlockPoller(rpcs=["u1", "u2"], fetch=rpc, health=emit)
    assert p.poll() == []
    assert p.current_rpc == "u2"


def test_head_below_processed_waits_never_goes_backwards():
    rpc = FakeRpc(head=100, blocks={100: _block(100, ["0xa"])})
    events, emit = _events_collector()
    p = BlockPoller(rpcs=["u1"], fetch=rpc, health=emit)
    p.poll()
    rpc.head = 98                             # lagging node / reorg below height
    assert p.poll() == []
    assert p.last_processed == 100            # unchanged
    assert any(e["event"] == "head_below_processed" for e in events)


def test_cycle_cap_bounds_blocks_and_is_lossless():
    blocks = {n: _block(n, [f"0x{n:x}"]) for n in range(100, 161)}
    rpc = FakeRpc(head=160, blocks=blocks)
    events, emit = _events_collector()
    p = BlockPoller(rpcs=["u1"], fetch=rpc, health=emit, max_blocks_per_cycle=30)
    p.poll()                                  # tip-start: just 160... reset
    # force a long catch-up: pretend we had processed 99 long ago
    p.last_processed = 99
    rows = p.poll()
    assert len(rows) == 30                    # capped
    assert p.last_processed == 129
    assert any(e["event"] == "cycle_capped" for e in events)
    rows2 = p.poll()                          # next cycle picks up remainder
    assert p.last_processed == 159
    rows3 = p.poll()
    assert p.last_processed == 160            # nothing skipped
    got = {r["block_number"] for r in rows + rows2 + rows3}
    assert got == set(range(100, 161))        # lossless


def test_requires_at_least_one_rpc():
    with pytest.raises(ValueError):
        BlockPoller(rpcs=[])


def test_single_endpoint_reports_no_failover_not_fake_rotation():
    rpc = FakeRpc(head=100, blocks={}, fail={"eth_blockNumber": RpcError("down")})
    events, emit = _events_collector()
    p = BlockPoller(rpcs=["only"], fetch=rpc, health=emit)
    assert p.poll() == []
    assert p.current_rpc == "only"            # nothing to rotate to
    assert any(e["event"] == "no_failover_available" for e in events)
    assert not any(e["event"] == "rpc_rotate" for e in events)


# ----- RotatingJsonlWriter prefix (shared writer used by both tiers) ------

def test_rotating_writer_uses_prefix(tmp_path):
    w = RotatingJsonlWriter(tmp_path, "confirmations_")
    w.write({"a": 1}); w.close()
    files = list(tmp_path.glob("confirmations_*.jsonl"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").strip() == '{"a": 1}'


@pytest.mark.skipif(sys.platform != "win32",
                    reason="advisory lock is a no-op off Windows")
def test_singleton_lock_blocks_second_holder(tmp_path):
    a = acquire_singleton_lock(tmp_path)
    assert a is not None                       # first acquires
    b = acquire_singleton_lock(tmp_path)
    assert b is None                           # second is refused while held
    a.close()                                  # release
    c = acquire_singleton_lock(tmp_path)
    assert c is not None                       # acquirable again after release
    c.close()
