"""Tests for the refined (decision-grade) mempool analyzers."""

from __future__ import annotations

from engine.bloxroute.analysis import (
    analyze_competition, analyze_drains, priority_fee_gwei,
)
from engine.bloxroute.mempool_capture import SEL_TRANSFER_FROM

TOKEN = "0x" + "dd" * 20
POOL = "0x" + "ee" * 20


def _tx(frm, to, ts, fee_gwei, sel="0xa9059cbb", inp=None):
    return {
        "from": frm, "to": to, "recv_ts": ts,
        "max_priority_fee": hex(int(fee_gwei * 1e9)),
        "input": inp if inp is not None else sel,
    }


def test_priority_fee_parse():
    assert priority_fee_gwei({"max_priority_fee": hex(int(2e9))}) == 2.0
    assert priority_fee_gwei({"max_priority_fee": None}) is None


def test_competition_separates_organic_from_bidders():
    # 20 organic low-fee senders to TOKEN/transfer (USDT-like volume)
    txs = [_tx(f"0xorg{i}", TOKEN, 100.0 + i * 0.05, 0.005) for i in range(20)]
    # 3 high-fee searchers racing POOL/swap in a tight window
    txs += [_tx(f"0xbot{i}", POOL, 100.1 + i * 0.01, 50.0, sel="0x12345678")
            for i in range(3)]
    r = analyze_competition(txs, window_s=2.0, top_pct=0.90)
    # raw contention is inflated by the 20 organic USDT senders
    assert r["raw_max_racers"] >= 20
    # bidders are the high-fee ones; the contested target is the pool
    assert r["bidder_max_racers"] == 3
    assert r["n_contested_targets"] == 1
    assert r["cost_to_compete_gwei_p50"] >= 50.0 - 1e-6


def test_competition_empty():
    r = analyze_competition([], window_s=2.0)
    assert r["n_txs"] == 0 and r["raw_max_racers"] == 0


def _transferfrom(frm_param):
    f = frm_param.lower().replace("0x", "").rjust(64, "0")
    t = ("0x" + "aa" * 20).replace("0x", "").rjust(64, "0")
    a = "1".rjust(64, "0")
    return SEL_TRANSFER_FROM + f + t + a


def test_drain_fanout_detects_one_to_many():
    # one initiator (drainer) pulling from 3 distinct owners
    drainer = "0x" + "bb" * 20
    txs = [
        _tx(drainer, TOKEN, 10.0 + i, 1.0, inp=_transferfrom(f"0x{i:040x}"))
        for i in range(3)
    ]
    r = analyze_drains(txs, min_owners=2)
    assert r["total_third_party_transferFrom"] == 3
    assert r["n_fanout_candidates"] == 1
    assert r["fanout_candidates"][0][0] == drainer
    assert r["fanout_candidates"][0][1] == 3


def test_drain_self_transfer_not_counted():
    owner = "0x" + "cc" * 20
    # initiator == owner -> ordinary, not third-party
    txs = [_tx(owner, TOKEN, 1.0, 1.0, inp=_transferfrom(owner))]
    r = analyze_drains(txs)
    assert r["total_third_party_transferFrom"] == 0
    assert r["n_fanout_candidates"] == 0


def test_drain_single_owner_below_threshold():
    spender = "0x" + "ab" * 20
    owner = "0x" + "cd" * 20
    txs = [_tx(spender, TOKEN, 1.0, 1.0, inp=_transferfrom(owner))]
    r = analyze_drains(txs, min_owners=2)
    # one owner only -> not a fan-out candidate
    assert r["n_fanout_candidates"] == 0
