"""Pure tests for the CEX L2 adapters (URLs, subscribe msgs, normalize). No net."""

from __future__ import annotations

from engine.cex.l2_capture import (
    CoinbaseAdapter, KrakenAdapter, BinanceUSAdapter, OKXAdapter,
    ADAPTERS, DEFAULT_EXCHANGES,
)

PAIRS = ["BTC-USD", "ETH-USD"]


def test_registry_and_defaults():
    assert set(DEFAULT_EXCHANGES) <= set(ADAPTERS)
    assert {"coinbase", "kraken", "binanceus", "okx"} <= set(ADAPTERS)


def test_coinbase_subscribe_and_normalize():
    a = CoinbaseAdapter(PAIRS)
    assert a.ws_url().startswith("wss://")
    sub = a.subscribe_messages()[0]
    assert sub["product_ids"] == PAIRS and "level2_batch" in sub["channels"]
    assert a.normalize({"type": "snapshot", "product_id": "BTC-USD"}) == {
        "pair": "BTC-USD", "kind": "snapshot"}
    assert a.normalize({"type": "l2update", "product_id": "ETH-USD"})["kind"] == "update"


def test_kraken_subscribe_and_normalize():
    a = KrakenAdapter(PAIRS, depth=25)
    sub = a.subscribe_messages()[0]
    assert "XBT/USD" in sub["pair"] and sub["subscription"]["depth"] == 25
    snap = [0, {"as": [["100", "1"]], "bs": [["99", "1"]]}, "book-25", "XBT/USD"]
    assert a.normalize(snap) == {"pair": "XBT/USD", "kind": "snapshot"}
    upd = [0, {"a": [["100", "2"]]}, "book-25", "XBT/USD"]
    assert a.normalize(upd)["kind"] == "update"


def test_binanceus_stream_url_and_normalize():
    a = BinanceUSAdapter(PAIRS)
    url = a.ws_url()
    assert "btcusdt@depth20@100ms" in url and "ethusdt@depth20@100ms" in url
    msg = {"stream": "btcusdt@depth20@100ms", "data": {"bids": [], "asks": []}}
    assert a.normalize(msg) == {"pair": "BTCUSDT", "kind": "snapshot"}


def test_okx_subscribe_and_normalize():
    a = OKXAdapter(PAIRS)
    args = a.subscribe_messages()[0]["args"]
    assert {"channel": "books5", "instId": "BTC-USDT"} in args
    msg = {"arg": {"channel": "books5", "instId": "BTC-USDT"},
           "data": [{"asks": [], "bids": [], "ts": "1"}]}
    assert a.normalize(msg) == {"pair": "BTC-USDT", "kind": "snapshot"}
