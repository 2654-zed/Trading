"""Tests for the Phase-4 ghost-tx join. Synthetic JSONL fixtures with KNOWN
ground truth prove the landed / replaced_drop / true_ghost classification
and the observability-window discipline. No network; DuckDB reads tmp files."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("duckdb")

from engine.bloxroute.ghost_analysis import analyze

# A clock base inside a single UTC hour so hourly buckets are deterministic.
BASE = 1_750_000_000.0   # 2025-06-15 ~ 13:06 UTC


def _mp_row(h, sender, nonce, ts):
    return {"hash": h, "from": sender, "to": "0xpool", "input": "0x",
            "value": None, "gas": None, "gas_price": None,
            "max_priority_fee": None, "nonce": nonce, "recv_ts": ts,
            "seq": 0, "source": "blxr-cloud-ws-eth",
            "clock_synced": None, "clock_offset_ms": None}


def _conf_row(h, block, ts, recv):
    return {"hash": h, "block_number": block, "block_timestamp": ts,
            "block_received_local_ts": recv,
            "builder_or_fee_recipient": "0x" + "ab" * 20, "tx_index": 0,
            "source": "public-eth-rpc:test", "clock_synced": None,
            "clock_offset_ms": None}


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")


def _setup(tmp_path, mp_rows, cf_rows):
    md = tmp_path / "mempool"; md.mkdir()
    cd = tmp_path / "confs"; cd.mkdir()
    _write(md / "mempool_x.jsonl", mp_rows)
    _write(cd / "confirmations_x.jsonl", cf_rows)
    return str(md / "mempool_*.jsonl"), str(cd / "confirmations_*.jsonl")


def test_classifies_landed_replaced_and_ghost(tmp_path):
    # All mempool txs seen at BASE; confirmations cover BASE-10 .. BASE+600
    # so with tail_buffer=60 the observable window is BASE-10 .. BASE+540,
    # i.e. all four txs (seen at BASE) are observable.
    mp = [
        _mp_row("0xAAA", "0xalice", "0x1", BASE + 0),   # lands as itself
        _mp_row("0xBBB", "0xbob",   "0x7", BASE + 1),   # never lands, slot replaced
        _mp_row("0xBBB2","0xbob",   "0x7", BASE + 2),   # the replacement (lands)
        _mp_row("0xCCC", "0xcarol", "0x3", BASE + 3),   # never lands, slot never filled
    ]
    cf = [
        # coverage must START before the txs (else a pre-coverage landing
        # would be missed and mislabeled ghost) and extend well past them.
        _conf_row("0xEARLY", 99, int(BASE) - 12, BASE - 30),
        _conf_row("0xAAA",  100, int(BASE), BASE + 30),
        _conf_row("0xBBB2", 101, int(BASE) + 12, BASE + 45),
        _conf_row("0xZZZ",  102, int(BASE) + 24, BASE + 600),
    ]
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["observable"] is True
    c = r["classification"]
    assert c.get("landed") == 2          # 0xAAA and 0xBBB2
    assert c.get("replaced_drop") == 1   # 0xBBB (slot 0xbob/0x7 filled by 0xBBB2)
    assert c.get("true_ghost") == 1      # 0xCCC


def test_non_overlapping_windows_classify_nothing(tmp_path):
    # mempool seen long BEFORE any confirmation coverage -> 0 observable,
    # and crucially NOT mislabeled as ghosts.
    mp = [_mp_row("0xAAA", "0xalice", "0x1", BASE),
          _mp_row("0xCCC", "0xcarol", "0x3", BASE + 1)]
    cf = [_conf_row("0xZZZ", 100, int(BASE) + 40000, BASE + 40000)]  # ~11h later
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=180.0)
    assert r["observable"] is False
    assert r["mempool_txs_in_window"] == 0
    assert r["classification"] == {}
    assert "do not overlap" in r["note"]


def test_tail_buffer_excludes_txs_seen_too_close_to_coverage_end(tmp_path):
    # A tx seen 30s before coverage ends, with a 180s buffer, is NOT yet
    # answerable -> excluded (not a ghost), even though it never landed.
    mp = [_mp_row("0xLATE", "0xdan", "0x9", BASE + 570)]
    cf = [_conf_row("0xAAA", 100, int(BASE), BASE + 0),
          _conf_row("0xBBB", 101, int(BASE) + 12, BASE + 600)]  # cov ends BASE+600
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=180.0)
    # observable window ends at BASE+420; the tx at BASE+570 is past it
    assert r["mempool_txs_in_window"] == 0
    assert r["observable"] is False


def test_dedup_first_seen_per_hash(tmp_path):
    # same hash seen 3 times -> one mp row at the earliest recv_ts
    mp = [_mp_row("0xAAA", "0xalice", "0x1", BASE + 5),
          _mp_row("0xAAA", "0xalice", "0x1", BASE + 1),
          _mp_row("0xAAA", "0xalice", "0x1", BASE + 9)]
    cf = [_conf_row("0xEARLY", 99, int(BASE) - 12, BASE - 30),
          _conf_row("0xAAA", 100, int(BASE), BASE + 30),
          _conf_row("0xZZZ", 101, int(BASE) + 12, BASE + 600)]
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["coverage"]["mempool_hashes"] == 1
    assert r["classification"].get("landed") == 1


def test_missing_confirmation_files_reports_cleanly(tmp_path):
    md = tmp_path / "mempool"; md.mkdir()
    _write(md / "mempool_x.jsonl", [_mp_row("0xAAA", "0xa", "0x1", BASE)])
    r = analyze(str(md / "mempool_*.jsonl"),
                str(tmp_path / "confs" / "confirmations_*.jsonl"))
    assert r["observable"] is False
    assert "no confirmation files" in r["note"]


def test_confirmation_gap_excludes_affected_txs(tmp_path):
    # A tx seen during a hole in the block sequence must be EXCLUDED (gap),
    # NOT mislabeled true_ghost — even though its hash never lands.
    mp = [_mp_row("0xGHOST", "0xeve", "0x2", BASE + 100)]   # falls in the gap
    cf = [
        _conf_row("0xA", 100, int(BASE), BASE - 10),
        _conf_row("0xB", 101, int(BASE) + 12, BASE + 50),   # block 101 @ recv BASE+50
        # block 102..149 MISSING -> gap window [BASE+50, BASE+700]
        _conf_row("0xC", 150, int(BASE) + 600, BASE + 700),
        _conf_row("0xD", 151, int(BASE) + 612, BASE + 900),  # extends coverage
    ]
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["gaps"]["n_gaps"] == 1
    assert r["gaps"]["missing_blocks"] == 48                 # 149 - 101
    # the tx at BASE+100 has horizon [BASE+100, BASE+160] inside the gap window
    assert r["unobservable_gap"] >= 1
    assert r["classification"].get("true_ghost", 0) == 0     # NOT a false ghost


def test_reorg_same_hash_two_blocks_uses_earliest(tmp_path):
    mp = [_mp_row("0xAAA", "0xalice", "0x1", BASE + 5)]
    cf = [_conf_row("0xEARLY", 99, int(BASE) - 12, BASE - 30),
          _conf_row("0xAAA", 100, int(BASE), BASE + 30),       # lands block 100
          _conf_row("0xAAA", 101, int(BASE) + 12, BASE + 45),  # reorg re-include
          _conf_row("0xZZZ", 102, int(BASE) + 24, BASE + 600)]
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["classification"].get("landed") == 1            # counted ONCE
    assert sum(r["classification"].values()) == 1


def test_null_sender_or_nonce_is_unclassifiable_not_ghost(tmp_path):
    mp = [_mp_row("0xN1", None, "0x1", BASE + 1),    # null sender
          _mp_row("0xN2", "0xalice", None, BASE + 2)]  # null nonce
    cf = [_conf_row("0xEARLY", 99, int(BASE) - 12, BASE - 30),
          _conf_row("0xZZZ", 100, int(BASE), BASE + 600)]
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["classification"].get("unclassifiable") == 2
    assert r["classification"].get("true_ghost", 0) == 0     # not inflated


def test_no_gaps_on_continuous_sequence(tmp_path):
    mp = [_mp_row("0xAAA", "0xalice", "0x1", BASE + 5)]
    cf = [_conf_row("0xE", 99, int(BASE) - 12, BASE - 30),
          _conf_row("0xAAA", 100, int(BASE), BASE + 30),
          _conf_row("0xF", 101, int(BASE) + 12, BASE + 600)]
    mg, cg = _setup(tmp_path, mp, cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["gaps"]["n_gaps"] == 0
    assert r["classification"].get("landed") == 1


def test_old_schema_mempool_rows_without_new_fields(tmp_path):
    # pre-hardening rows lack seq/source/clock — must still join on hash.
    old = {"hash": "0xAAA", "from": "0xalice", "to": "0xp", "input": "0x",
           "value": None, "gas": None, "gas_price": None,
           "max_priority_fee": None, "nonce": "0x1", "recv_ts": BASE}
    cf = [_conf_row("0xEARLY", 99, int(BASE) - 12, BASE - 30),
          _conf_row("0xAAA", 100, int(BASE), BASE + 30),
          _conf_row("0xZZZ", 101, int(BASE) + 12, BASE + 600)]
    mg, cg = _setup(tmp_path, [old], cf)
    r = analyze(mg, cg, tail_buffer_s=60.0)
    assert r["classification"].get("landed") == 1
