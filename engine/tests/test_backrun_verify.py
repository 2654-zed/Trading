"""Tests for the backrun-verification pure logic (no network)."""

from __future__ import annotations

from engine.bloxroute import backrun_verify as bv

WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
POOL = "0x" + "ee" * 20         # a specific pool (not ubiquitous)
POOL2 = "0x" + "cc" * 20
TOKEN = "0x" + "dd" * 20


def _rcpt(addrs):
    return {"logs": [{"address": a} for a in addrs]}


def test_extract_touched_contracts():
    t = bv.extract_touched_contracts(_rcpt([WETH.upper(), POOL]))
    assert t == {WETH, POOL}                     # lowercased
    assert bv.extract_touched_contracts(None) == set()
    assert bv.extract_touched_contracts({"logs": []}) == set()


def test_shared_specific_finds_pool_not_weth():
    swap = {WETH, POOL, TOKEN}
    backrun = {WETH, POOL, "0x" + "11" * 20}     # shares WETH + the POOL
    shared = bv.shared_specific(swap, backrun)
    assert shared == {POOL}                       # WETH excluded; the pool is the signal


def test_shared_specific_weth_only_is_not_a_backrun():
    # two unrelated swaps that both touch WETH but DIFFERENT pools
    a = {WETH, POOL}
    b = {WETH, POOL2}
    assert bv.shared_specific(a, b) == set()      # only WETH shared -> not flagged


def test_summarize_splits_by_seen():
    rows = [
        {"next_seen": False, "shared": True},     # private, verified
        {"next_seen": False, "shared": False},    # private, not
        {"next_seen": True, "shared": True},      # public, verified
        {"next_seen": True, "shared": True},
    ]
    s = bv.summarize(rows)
    assert s["n_pairs"] == 4
    assert abs(s["verified_backrun_rate"] - 0.75) < 1e-9
    assert s["n_private_backrunner"] == 2 and abs(s["verified_rate_private"] - 0.5) < 1e-9
    assert s["n_public_backrunner"] == 2 and s["verified_rate_public"] == 1.0
