"""Tests for the backrun-slot contestability measurement."""

from __future__ import annotations

import json

from engine.bloxroute import backrun_analysis as ba

UR = "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad"   # a known router
RAND = "0x" + "99" * 20


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _setup(tmp_path, monkeypatch, mp_rows, conf_rows, key="20260101_00"):
    mpd, cfd = tmp_path / "mp", tmp_path / "cf"
    mpd.mkdir(); cfd.mkdir()
    _write(mpd / f"mempool_{key}.jsonl", mp_rows)
    _write(cfd / f"confirmations_{key}.jsonl", conf_rows)
    monkeypatch.setattr(ba, "MEMPOOL_DIR", str(mpd))
    monkeypatch.setattr(ba, "CONF_DIR", str(cfd))
    return [key]


def _mp(h, to):
    return {"hash": h, "from": "0x1", "to": to, "input": "0x", "recv_ts": 1.0}


def _cf(h, blk, idx):
    return {"hash": h, "block_number": blk, "tx_index": idx,
            "block_timestamp": 1, "block_received_local_ts": 1.0,
            "builder_or_fee_recipient": "0x" + "ab" * 20}


def test_two_proportion_z_basic():
    z, p = ba.two_proportion_z(90, 100, 10, 100)   # 0.9 vs 0.1, huge gap
    assert z is not None and p < 1e-6
    assert ba.two_proportion_z(0, 0, 1, 10) == (None, None)


def test_backrun_slot_captured_privately_is_detected(tmp_path, monkeypatch):
    # 2 blocks: each has a public swap at idx0 followed by a PRIVATE tx (idx1),
    # then two SEEN non-swap txs (idx2,idx3). So the backrun slot is 100%
    # private; the baseline slot (after a seen non-swap) is 100% public.
    seen = [_mp("0xa", UR), _mp("0xc", RAND), _mp("0xd", RAND),
            _mp("0xe", UR), _mp("0x10", RAND), _mp("0x11", RAND)]   # 0xb,0xf NOT seen
    conf = [_cf("0xa", 100, 0), _cf("0xb", 100, 1), _cf("0xc", 100, 2), _cf("0xd", 100, 3),
            _cf("0xe", 101, 0), _cf("0xf", 101, 1), _cf("0x10", 101, 2), _cf("0x11", 101, 3)]
    win = _setup(tmp_path, monkeypatch, seen, conf)
    r = ba.analyze_backrun_contestability(win)
    assert r["n_public_swaps_with_backrun_slot"] == 2
    assert r["backrun_slot_private_rate"] == 1.0
    assert r["baseline_slot_private_rate"] == 0.0
    assert abs(r["excess_private_capture"] - 1.0) < 1e-9
    assert r["edge_sign"] == "+"
    assert r["p_value"] is not None
    assert abs(r["overall_private_rate"] - 0.25) < 1e-9   # 2 private of 8


def test_no_signal_when_backruns_are_public(tmp_path, monkeypatch):
    # public swap followed by a SEEN tx -> backrun slot is public -> no excess
    seen = [_mp("0xa", UR), _mp("0xb", RAND), _mp("0xc", RAND), _mp("0xd", RAND)]
    conf = [_cf("0xa", 100, 0), _cf("0xb", 100, 1), _cf("0xc", 100, 2), _cf("0xd", 100, 3)]
    win = _setup(tmp_path, monkeypatch, seen, conf)
    r = ba.analyze_backrun_contestability(win)
    assert r["backrun_slot_private_rate"] == 0.0
    assert r["edge_sign"] in ("0", "-")


def test_no_data_returns_clean(tmp_path, monkeypatch):
    win = _setup(tmp_path, monkeypatch, [], [])
    r = ba.analyze_backrun_contestability(["nonexistent_99"])
    assert r["n_public_swaps_with_backrun_slot"] == 0
