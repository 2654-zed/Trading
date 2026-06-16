"""Tests for triangular-arb detection (pure logic, no network)."""

from __future__ import annotations

from engine.bloxroute import triangular_analysis as ta
from engine.bloxroute.backrun_profit import TRANSFER_TOPIC, WETH

EOA = "0x" + "aa" * 20
BOT = "0x" + "bb" * 20
P1 = "0x" + "11" * 20
P2 = "0x" + "22" * 20
P3 = "0x" + "33" * 20


def _swap(pool, topic=ta.V3_SWAP):
    return {"address": pool, "topics": [topic], "data": "0x"}


def _topic_addr(a):
    return "0x" + a[2:].rjust(64, "0")


def _xfer(token, frm, to, value):
    return {"address": token, "data": hex(value),
            "topics": [TRANSFER_TOPIC, _topic_addr(frm), _topic_addr(to)]}


def _receipt(logs, frm=EOA, to=BOT, gas_used=300_000, gas_price=10**9):
    return {"from": frm, "to": to, "gasUsed": hex(gas_used),
            "effectiveGasPrice": hex(gas_price), "logs": logs}


def _profit_logs(weth=10**18):
    # net +weth to the bot -> a profitable cycle
    return [_xfer(WETH, P1, BOT, weth)]


def test_swap_pools_counts_distinct_v2v3():
    r = _receipt([_swap(P1), _swap(P2, ta.V2_SWAP), _swap(P2)])  # P2 twice
    assert ta.swap_pools(r) == {P1, P2}                          # distinct only


def test_triangular_arb_detected():
    r = _receipt([_swap(P1), _swap(P2), _swap(P3)] + _profit_logs())
    c = ta.classify_tx(r, eth_price_usd=2500.0)
    assert c["hops"] == 3 and c["profitable"] and c["kind"] == "triangular_plus_arb"


def test_two_pool_arb_detected():
    r = _receipt([_swap(P1), _swap(P2)] + _profit_logs())
    assert ta.classify_tx(r)["kind"] == "two_pool_arb"


def test_multihop_user_swap_not_arb():
    # 3 pools but NO net profit (a user routing a trade, not a cycle)
    r = _receipt([_swap(P1), _swap(P2), _swap(P3)])              # no profit logs
    c = ta.classify_tx(r)
    assert c["hops"] == 3 and not c["profitable"]
    assert c["kind"] == "multihop_not_arb"


def test_single_swap_and_non_swap():
    assert ta.classify_tx(_receipt([_swap(P1)]))["kind"] == "single_swap"
    assert ta.classify_tx(_receipt([]))["kind"] == "non_swap"


def test_v4_flagged_when_poolmanager_touched():
    r = _receipt([{"address": ta.V4_POOLMANAGER, "topics": ["0xabc"], "data": "0x"}]
                 + _profit_logs())
    c = ta.classify_tx(r)
    assert c["v4"] is True and c["kind"] == "v4_arb_hops_uncounted"
