"""Tests for the bloXroute mempool analyzers (pure, network-free)."""

from __future__ import annotations

from engine.bloxroute.mempool_capture import (
    decode_transfer_from, is_third_party_transfer_from, parse_bloxroute_tx,
    CompetitionTracker, MicrostructureStats, SEL_TRANSFER_FROM,
)

OWNER = "0x" + "11" * 20
SPENDER = "0x" + "22" * 20
DEST = "0x" + "33" * 20


def _transferfrom_calldata(frm, to, amount):
    f = frm.lower().replace("0x", "").rjust(64, "0")
    t = to.lower().replace("0x", "").rjust(64, "0")
    a = hex(amount)[2:].rjust(64, "0")
    return SEL_TRANSFER_FROM + f + t + a


# ----- decode -------------------------------------------------------------

def test_decode_transfer_from_basic():
    cd = _transferfrom_calldata(OWNER, DEST, 1000)
    d = decode_transfer_from(cd)
    assert d["from"] == OWNER.lower()
    assert d["to"] == DEST.lower()
    assert d["amount"] == 1000


def test_decode_non_transferfrom_returns_none():
    assert decode_transfer_from("0xa9059cbb" + "00" * 64) is None
    assert decode_transfer_from("") is None
    assert decode_transfer_from("0x") is None


def test_decode_handles_missing_0x_prefix():
    cd = _transferfrom_calldata(OWNER, DEST, 5)[2:]  # strip 0x
    d = decode_transfer_from(cd)
    assert d is not None and d["amount"] == 5


# ----- the deductive control-fact flag ------------------------------------

def test_third_party_transfer_from_flagged():
    # initiator (tx.from) = SPENDER, but the owner being debited = OWNER
    cd = _transferfrom_calldata(OWNER, DEST, 1)
    flag = is_third_party_transfer_from(SPENDER, cd)
    assert flag is not None
    assert flag["initiator"] == SPENDER.lower()
    assert flag["from"] == OWNER.lower()


def test_self_transfer_from_not_flagged():
    # initiator == owner → ordinary, not a third-party move
    cd = _transferfrom_calldata(OWNER, DEST, 1)
    assert is_third_party_transfer_from(OWNER, cd) is None


def test_non_transferfrom_not_flagged():
    assert is_third_party_transfer_from(SPENDER, "0xa9059cbb" + "00"*64) is None


# ----- bloXroute message parsing -----------------------------------------

def test_parse_bloxroute_camelcase():
    msg = {"params": {"result": {"txHash": "0xABC", "txContents": {
        "from": "0xDeAd", "to": "0xBeEf", "input": "0x23b872dd",
        "maxPriorityFeePerGas": "0x77359400"}}}}
    tx = parse_bloxroute_tx(msg)
    assert tx["hash"] == "0xabc"
    assert tx["from"] == "0xdead"
    assert tx["max_priority_fee"] == "0x77359400"


def test_parse_non_tx_message_returns_none():
    assert parse_bloxroute_tx({"id": 1, "result": "subscription_id"}) is None
    assert parse_bloxroute_tx({}) is None


# ----- Tier-0 hardening: caller-supplied recv_ts + seq + source -----------

_EXISTING_FIELDS = {"hash", "from", "to", "input", "value", "gas",
                    "gas_price", "max_priority_fee", "nonce", "recv_ts"}


def test_parse_passes_through_recv_ts_seq_source():
    msg = {"params": {"result": {"txHash": "0xABC", "txContents": {
        "from": "0xDeAd", "to": "0xBeEf", "input": "0x"}}}}
    tx = parse_bloxroute_tx(msg, recv_ts=1234.5, seq=42, source="blxr-cloud-ws-eth",
                            clock_synced=True, clock_offset_ms=2.5)
    # the caller's socket-receipt time is used verbatim (not re-stamped)
    assert tx["recv_ts"] == 1234.5
    assert tx["seq"] == 42
    assert tx["source"] == "blxr-cloud-ws-eth"
    assert tx["clock_synced"] is True
    assert tx["clock_offset_ms"] == 2.5


def test_parse_recv_ts_defaults_when_omitted():
    # backward compat (tests / legacy callers): falls back to a real time,
    # seq/clock are None, source is the default vantage tag.
    msg = {"params": {"result": {"txHash": "0x1", "txContents": {"from": "0x2"}}}}
    tx = parse_bloxroute_tx(msg)
    assert isinstance(tx["recv_ts"], float) and tx["recv_ts"] > 0
    assert tx["seq"] is None
    assert tx["source"] == "blxr-cloud-ws-eth"
    assert tx["clock_synced"] is None
    assert tx["clock_offset_ms"] is None


def test_parse_existing_fields_unchanged_and_additive():
    # the pre-Tier-0 field set is preserved byte-identically; only the
    # Tier-0 metadata fields are added (nothing renamed, retyped, or dropped).
    msg = {"params": {"result": {"txHash": "0xABC", "txContents": {
        "from": "0xDeAd", "to": "0xBeEf", "input": "0x23b872dd",
        "maxPriorityFeePerGas": "0x77359400"}}}}
    tx = parse_bloxroute_tx(msg, recv_ts=9.0, seq=1)
    assert _EXISTING_FIELDS <= set(tx)                 # every old field present
    assert set(tx) - _EXISTING_FIELDS == {
        "seq", "source", "clock_synced", "clock_offset_ms",
        "tx_type", "auth_count"}                        # only these added
    assert tx["max_priority_fee"] == "0x77359400"      # unchanged value/type


def test_parse_tx_type_and_eip7702():
    # EIP-1559 (type 2), no authorization list
    m2 = {"params": {"result": {"txHash": "0x1", "txContents": {
        "from": "0xa", "type": "0x2", "maxPriorityFeePerGas": "0x1"}}}}
    t2 = parse_bloxroute_tx(m2)
    assert t2["tx_type"] == 2 and t2["auth_count"] is None

    # EIP-7702 (type 4) with two delegations
    m7702 = {"params": {"result": {"txHash": "0x2", "txContents": {
        "from": "0xb", "type": "0x4",
        "authorizationList": [{"address": "0xc"}, {"address": "0xd"}]}}}}
    t7702 = parse_bloxroute_tx(m7702)
    assert t7702["tx_type"] == 4 and t7702["auth_count"] == 2

    # legacy / feed omits type -> None (not guessed)
    mleg = {"params": {"result": {"txHash": "0x3", "txContents": {"from": "0xe"}}}}
    tleg = parse_bloxroute_tx(mleg)
    assert tleg["tx_type"] is None and tleg["auth_count"] is None

    # type-4 present but empty list -> auth_count 0 (key-presence, not truthiness)
    mempty = {"params": {"result": {"txHash": "0x4", "txContents": {
        "from": "0xf", "type": "0x04", "authorizationList": []}}}}
    tempty = parse_bloxroute_tx(mempty)
    assert tempty["tx_type"] == 4 and tempty["auth_count"] == 0


# ----- competition tracker ------------------------------------------------

def test_competition_counts_distinct_racers():
    c = CompetitionTracker(window_s=10.0)
    base = {"to": DEST, "input": SEL_TRANSFER_FROM}
    assert c.observe({**base, "from": "0xa", "recv_ts": 100.0}) == 1
    assert c.observe({**base, "from": "0xb", "recv_ts": 100.5}) == 2
    assert c.observe({**base, "from": "0xc", "recv_ts": 101.0}) == 3
    assert c.max_racers_seen == 3
    assert c.contended_events == 2  # the 2nd and 3rd observations had >=2


def test_competition_window_expires_old():
    c = CompetitionTracker(window_s=2.0)
    base = {"to": DEST, "input": SEL_TRANSFER_FROM}
    c.observe({**base, "from": "0xa", "recv_ts": 100.0})
    # 5s later, the old racer has expired → back to 1
    assert c.observe({**base, "from": "0xb", "recv_ts": 105.0}) == 1


def test_competition_separates_different_opportunities():
    c = CompetitionTracker(window_s=10.0)
    assert c.observe({"to": DEST, "input": SEL_TRANSFER_FROM,
                      "from": "0xa", "recv_ts": 1.0}) == 1
    # different destination → separate bucket, still 1 racer
    assert c.observe({"to": "0x"+"99"*20, "input": SEL_TRANSFER_FROM,
                      "from": "0xb", "recv_ts": 1.1}) == 1


# ----- microstructure stats ----------------------------------------------

def test_microstructure_snapshot():
    s = MicrostructureStats()
    for i in range(3):
        s.observe({"to": DEST, "from": f"0x{i}", "input": SEL_TRANSFER_FROM,
                   "max_priority_fee": "0x77359400"})  # 2 gwei
    snap = s.snapshot()
    assert snap["total_txs"] == 3
    assert snap["top_methods"][0][0] == SEL_TRANSFER_FROM
    assert snap["priority_fee_gwei_p50"] == 2.0
