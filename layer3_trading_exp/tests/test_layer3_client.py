"""Phase 1.0 acceptance tests for layer3_client.Layer3Client.

Covers the four gated acceptance criteria plus negative-case coverage:
- get_contract_classification returns correct §1.1 fields
- is_in_org_wallets returns boolean correctly for org_001 and org_002 addresses
  (org_003/org_004 dropped per Addendum A #3)
- get_trap_events_for row count matches direct SQL
- get_trust_amplification returns CRITICAL for 0xd4624228
"""

from __future__ import annotations

import sqlite3

import pytest

from layer3_trading_exp.layer3_client import Layer3Client
from .conftest import TRUST_AMP_CRITICAL_ADDR


def test_get_contract_classification_returns_all_consumable_fields(seeded_db):
    with Layer3Client(seeded_db) as c:
        row = c.get_contract_classification("0xaaaa1111")
    assert row is not None
    expected = {
        "contract_address", "confidence_tier", "decayed_at", "deployed_code_hash",
        "has_asymmetric_transfer", "has_conditional_revert",
        "has_unusual_fee_structure", "deployer_address",
    }
    assert expected.issubset(row.keys())
    assert row["confidence_tier"] == "confirmed"
    assert row["has_asymmetric_transfer"] == 1
    assert row["deployer_address"] == "0xdeployer1"


def test_get_contract_classification_missing_address_returns_none(seeded_db):
    with Layer3Client(seeded_db) as c:
        assert c.get_contract_classification("0xnotpresent") is None


def test_is_in_org_wallets_true_for_seeded_org_001(seeded_db):
    with Layer3Client(seeded_db) as c:
        assert c.is_in_org_wallets("0xorg001addr1") is True
        assert c.is_in_org_wallets("0xorg001addr2") is True


def test_is_in_org_wallets_true_for_seeded_org_002(seeded_db):
    with Layer3Client(seeded_db) as c:
        assert c.is_in_org_wallets("0xorg002addr1") is True


def test_is_in_org_wallets_false_for_non_member(seeded_db):
    with Layer3Client(seeded_db) as c:
        assert c.is_in_org_wallets("0xaaaa1111") is False


def test_is_in_org_wallets_case_insensitive(seeded_db):
    with Layer3Client(seeded_db) as c:
        assert c.is_in_org_wallets("0xORG001addr1") is True


def test_get_trap_events_for_matches_direct_sql(seeded_db):
    with Layer3Client(seeded_db) as c:
        rows = c.get_trap_events_for("0xaaaa1111")
        count = c.count_trap_events_for("0xaaaa1111")

    direct = sqlite3.connect(str(seeded_db))
    try:
        (direct_count,) = direct.execute(
            "SELECT COUNT(*) FROM trap_events WHERE LOWER(trap_contract_address) = LOWER(?)",
            ("0xaaaa1111",),
        ).fetchone()
    finally:
        direct.close()

    assert len(rows) == direct_count
    assert count == direct_count
    assert direct_count == 2


def test_get_trap_events_ordered_desc(seeded_db):
    with Layer3Client(seeded_db) as c:
        rows = c.get_trap_events_for("0xaaaa1111")
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_get_trust_amplification_returns_critical_for_d4624228(seeded_db):
    with Layer3Client(seeded_db) as c:
        row = c.get_trust_amplification(TRUST_AMP_CRITICAL_ADDR)
    assert row is not None
    assert row["alert_level"] == "CRITICAL"


def test_get_trust_amplification_none_for_unflagged(seeded_db):
    with Layer3Client(seeded_db) as c:
        row = c.get_trust_amplification("0xaaaa1111")
    assert row is not None
    assert row["alert_level"] is None


def test_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Layer3Client(tmp_path / "does_not_exist.db")


def test_get_drain_detected_approvals_only_returns_drained(seeded_db):
    with Layer3Client(seeded_db) as c:
        drained = c.get_drain_detected_approvals_targeting("0xbbbb2222")
        unrelated = c.get_drain_detected_approvals_targeting("0xaaaa1111")
    assert len(drained) == 1
    assert drained[0]["victim_address"] == "0xvictim1"
    assert unrelated == []


def test_bytecode_family_lookup(seeded_db):
    with Layer3Client(seeded_db) as c:
        fam = c.get_bytecode_family("0xaaaa1111")
    assert fam is not None
    assert fam["family_id"] == "fam_001"
    assert fam["is_cross_deployer"] == 1


def test_infrastructure_registry_hit(seeded_db):
    with Layer3Client(seeded_db) as c:
        rows = c.get_infrastructure_classification("0xcctp1")
    assert len(rows) == 1
    assert rows[0]["classification"] == "circle_cctp_message_transmitter_v2"


def test_org_candidates_referencing_deployer(seeded_db):
    with Layer3Client(seeded_db) as c:
        rows = c.get_org_candidates_referencing("0xdeployerA")
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
