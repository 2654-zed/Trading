"""Tests for realized backrun-profit decoding (pure, no network)."""

from __future__ import annotations

from engine.bloxroute.backrun_profit import (
    decode_transfers, token_delta, gas_cost_wei, realized_profit,
    TRANSFER_TOPIC, WETH,
)

EOA = "0x" + "aa" * 20
BOT = "0x" + "bb" * 20
POOL = "0x" + "ee" * 20
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def _topic_addr(a):
    return "0x" + a[2:].rjust(64, "0")


def _xfer(token, frm, to, value):
    return {"address": token, "data": hex(value),
            "topics": [TRANSFER_TOPIC, _topic_addr(frm), _topic_addr(to)]}


def _receipt(frm, to, logs, gas_used=200_000, gas_price=20 * 10**9):
    return {"from": frm, "to": to, "gasUsed": hex(gas_used),
            "effectiveGasPrice": hex(gas_price), "logs": logs}


def test_decode_transfers_and_delta():
    r = _receipt(EOA, BOT, [
        _xfer(WETH, POOL, BOT, 10**18),        # 1 WETH in to the bot
        _xfer(WETH, BOT, POOL, 5 * 10**17),    # 0.5 WETH out
    ])
    ts = decode_transfers(r)
    assert len(ts) == 2
    # net WETH to the searcher side {EOA, BOT} = +1.0 - 0.5 = 0.5 WETH
    assert token_delta(ts, {EOA, BOT}, WETH) == 5 * 10**17


def test_gas_cost():
    r = _receipt(EOA, BOT, [])
    assert gas_cost_wei(r) == 200_000 * 20 * 10**9      # 0.004 ETH in wei


def test_realized_profit_weth_arb():
    r = _receipt(EOA, BOT, [
        _xfer(WETH, POOL, BOT, 10**18),
        _xfer(WETH, BOT, POOL, 5 * 10**17),
    ])
    p = realized_profit(r, eth_price_usd=2000.0)
    assert p["profit_token"] == WETH
    assert abs(p["net_token_usd"] - 1000.0) < 1e-6       # 0.5 WETH * $2000
    assert abs(p["gas_usd"] - 8.0) < 1e-6                # 0.004 ETH * $2000
    assert abs(p["pnl_usd"] - 992.0) < 1e-6


def test_multitoken_arb_nets_across_tokens():
    # THE ARTIFACT FIX: bot receives 324 WETH but PAYS ~$810k USDC for it.
    # Max-single-token would call this +$810k; correct NET is the small spread.
    r = _receipt(EOA, BOT, [
        _xfer(WETH, POOL, BOT, 324 * 10**18),            # +324 WETH ($810k @ 2500)
        _xfer(USDC, BOT, POOL, 808_000 * 10**6),         # -$808k USDC
    ])
    p = realized_profit(r, eth_price_usd=2500.0)
    # net = 324*2500 - 808000 = 810000 - 808000 = +$2000 (the real spread)
    assert abs(p["net_token_usd"] - 2000.0) < 1.0
    assert p["pnl_usd"] < 2000.0                          # minus gas


def test_loss_leg_nets_negative():
    # bot only SENDS weth (a loss/leg of a bundle) -> negative net
    r = _receipt(EOA, BOT, [_xfer(WETH, BOT, POOL, 10**18)])
    p = realized_profit(r, 2000.0)
    assert p["profit_token"] is None
    assert p["net_token_usd"] < 0
    assert p["pnl_usd"] < 0                               # loss + gas


def test_erc721_transfer_ignored():
    # 4-topic Transfer (ERC-721) carries no fungible value -> skipped
    log = {"address": WETH, "data": "0x",
           "topics": [TRANSFER_TOPIC, _topic_addr(POOL), _topic_addr(BOT),
                      _topic_addr(EOA)]}
    assert decode_transfers(_receipt(EOA, BOT, [log])) == []
