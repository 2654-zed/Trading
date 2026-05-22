"""Pytest fixtures: a minimal in-memory SQLite DB seeded with known rows for each
consumable-surface table the client touches. The schema mirrors the columns named in
layer3_consumable_intelligence.md §1.1-§1.10; it is NOT a full clone of Layer 3's
production schema — only enough to exercise the client's query patterns.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


TRUST_AMP_CRITICAL_ADDR = "0xd4624228"


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 4, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded_db(tmp_path: Path, now_utc: datetime) -> Path:
    db_path = tmp_path / "surveillance_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    _seed(conn, now_utc)
    conn.commit()
    conn.close()
    return db_path


_SCHEMA = """
CREATE TABLE contracts (
    contract_address TEXT PRIMARY KEY,
    chain TEXT,
    confidence_tier TEXT,
    decayed_at TEXT,
    deployed_code_hash TEXT,
    has_asymmetric_transfer INTEGER,
    has_conditional_revert INTEGER,
    has_unusual_fee_structure INTEGER,
    deployer_address TEXT,
    last_updated TEXT
);
CREATE TABLE deployers (
    deployer_address TEXT PRIMARY KEY,
    chain TEXT,
    first_seen TEXT,
    last_seen TEXT,
    total_contracts_deployed INTEGER,
    mainnet_first_tx TEXT,
    entity_type TEXT,
    funding_trail TEXT
);
CREATE TABLE trap_events (
    trap_contract_address TEXT,
    bot_address TEXT,
    tx_hash TEXT,
    block_number INTEGER,
    timestamp TEXT,
    loss_estimate_usd REAL,
    failure_signature TEXT
);
CREATE TABLE alerts (
    alert_type TEXT,
    address TEXT,
    tx_hash TEXT,
    block_number INTEGER,
    timestamp TEXT,
    payload TEXT,
    false_positive INTEGER
);
CREATE TABLE org_wallets (
    address TEXT,
    chain TEXT,
    org_id TEXT,
    role TEXT,
    added_at TEXT,
    added_by TEXT,
    reason TEXT,
    PRIMARY KEY (address, chain)
);
CREATE TABLE org_candidates (
    candidate_id TEXT PRIMARY KEY,
    cluster_size INTEGER,
    deployer_addresses TEXT,
    shared_funding_source TEXT,
    shared_chain TEXT,
    shared_gas_fingerprint TEXT,
    first_seen TEXT,
    last_seen TEXT,
    detected_at TEXT,
    status TEXT
);
CREATE TABLE infrastructure_registry (
    address TEXT,
    chain TEXT,
    classification TEXT,
    verified_at TEXT,
    notes TEXT,
    PRIMARY KEY (address, chain)
);
CREATE TABLE bytecode_families (
    family_id TEXT PRIMARY KEY,
    family_name TEXT,
    detection_tier TEXT,
    member_count INTEGER,
    unique_deployers INTEGER,
    avg_revert_rate REAL,
    total_victims INTEGER,
    first_seen TEXT,
    last_updated TEXT,
    is_cross_deployer INTEGER
);
CREATE TABLE bytecode_family_members (
    family_id TEXT,
    contract_address TEXT,
    deployer TEXT,
    deployment_timestamp TEXT,
    revert_rate REAL,
    unique_callers INTEGER,
    PRIMARY KEY (family_id, contract_address)
);
CREATE TABLE trust_amplification (
    contract_address TEXT PRIMARY KEY,
    total_callers INTEGER,
    router_callers INTEGER,
    router_percentage REAL,
    amplification_factor REAL,
    revert_rate REAL,
    alert_level TEXT,
    last_updated TEXT
);
CREATE TABLE approval_watchlist (
    victim_address TEXT,
    contract_address TEXT,
    approve_tx_hash TEXT,
    approve_timestamp TEXT,
    contract_tier TEXT,
    drain_detected INTEGER,
    drain_tx_hash TEXT,
    drain_timestamp TEXT,
    drain_caller TEXT
);
CREATE TABLE camouflage_metrics (
    date TEXT PRIMARY KEY,
    chain TEXT
);
CREATE TABLE daily_metrics (
    date TEXT PRIMARY KEY,
    chain TEXT
);
"""


def _seed(conn: sqlite3.Connection, now: datetime) -> None:
    fresh = now.isoformat()
    hour_ago = (now - timedelta(minutes=10)).isoformat()
    stale = (now - timedelta(hours=2)).isoformat()

    # contracts — three kinds of rows so filter logic can be exercised
    conn.executemany(
        "INSERT INTO contracts VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "0xaaaa1111", "base", "confirmed", None, "0xhash1",
                1, 0, 0, "0xdeployer1", fresh,
            ),
            (
                "0xbbbb2222", "base", "suspected", None, "0xhash2",
                0, 1, 0, "0xdeployer2", fresh,
            ),
            (
                "0xcccc3333", "base", "unanalyzed",
                (now - timedelta(days=31)).isoformat(), "0xhash3",
                0, 0, 0, "0xdeployer3", hour_ago,
            ),
        ],
    )

    # deployers
    conn.executemany(
        "INSERT INTO deployers VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                "0xdeployer1", "base", fresh, fresh, 3, "2024-01-01T00:00:00",
                "labeled", json.dumps({"funder": "0xfunder1"}),
            ),
            (
                "0xdeployer2", "base",
                (now - timedelta(days=3)).isoformat(), fresh, 1, "",
                "", json.dumps({"funder": "0xfunder2"}),
            ),
        ],
    )

    # trap_events — two rows for one contract, one row for another
    conn.executemany(
        "INSERT INTO trap_events VALUES (?,?,?,?,?,?,?)",
        [
            ("0xaaaa1111", "0xbot1", "0xtx1", 100, fresh, 1500.0, "revert:selector=deadbeef"),
            ("0xaaaa1111", "0xbot2", "0xtx2", 101, fresh, 750.0, "revert:selector=deadbeef"),
            ("0xbbbb2222", "0xbot1", "0xtx3", 102, fresh, 2200.0, "revert:selector=cafef00d"),
        ],
    )

    # alerts (freshness only)
    conn.execute(
        "INSERT INTO alerts VALUES (?,?,?,?,?,?,?)",
        ("TRAP_CONFIRMED", "0xaaaa1111", "0xtx1", 100, fresh, "{}", 0),
    )

    # org_wallets — org_001 (two rows) and org_002 (one row), per Addendum A #3
    conn.executemany(
        "INSERT INTO org_wallets VALUES (?,?,?,?,?,?,?)",
        [
            ("0xorg001addr1", "base", "org_001", "treasury", fresh, "jason", "seed"),
            ("0xorg001addr2", "base", "org_001", "cashout", fresh, "jason", "seed"),
            ("0xorg002addr1", "base", "org_002", "operator", fresh, "jason", "seed"),
        ],
    )

    # org_candidates
    conn.execute(
        "INSERT INTO org_candidates VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "cand_001", 5, json.dumps(["0xdeployer9", "0xdeployerA", "0xdeployerB"]),
            "0xfunder9", "base", None, fresh, fresh, fresh, "pending",
        ),
    )

    # infrastructure_registry
    conn.execute(
        "INSERT INTO infrastructure_registry VALUES (?,?,?,?,?)",
        ("0xcctp1", "base", "circle_cctp_message_transmitter_v2", fresh, ""),
    )

    # bytecode_families + members
    conn.execute(
        "INSERT INTO bytecode_families VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("fam_001", "T1-test", "T1", 100, 2, 0.5, 3, fresh, fresh, 1),
    )
    conn.execute(
        "INSERT INTO bytecode_family_members VALUES (?,?,?,?,?,?)",
        ("fam_001", "0xaaaa1111", "0xdeployer1", fresh, 0.5, 10),
    )

    # trust_amplification — seed the spec's named address with CRITICAL
    conn.executemany(
        "INSERT INTO trust_amplification VALUES (?,?,?,?,?,?,?,?)",
        [
            (TRUST_AMP_CRITICAL_ADDR, 100, 80, 80.0, 3.5, 0.15, "CRITICAL", fresh),
            ("0xaaaa1111", 50, 10, 20.0, 1.2, 0.3, None, fresh),
        ],
    )

    # approval_watchlist — one drain-detected row targeting 0xbbbb2222
    conn.executemany(
        "INSERT INTO approval_watchlist VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("0xvictim1", "0xbbbb2222", "0xapproveTx1", fresh, "suspected",
             1, "0xdrainTx1", fresh, "0xdrainer1"),
            ("0xvictim2", "0xaaaa1111", "0xapproveTx2", fresh, "confirmed",
             0, None, None, None),
        ],
    )

    # camouflage_metrics, daily_metrics — freshness only
    conn.execute("INSERT INTO camouflage_metrics VALUES (?,?)", (now.date().isoformat(), "base"))
    conn.execute("INSERT INTO daily_metrics VALUES (?,?)", (now.date().isoformat(), "base"))
