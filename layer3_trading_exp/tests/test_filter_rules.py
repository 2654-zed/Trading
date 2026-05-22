"""Per-rule unit tests for filter_rules.

Each test verifies one rule's fire/no-fire condition with a fake Layer3Client
backed by in-memory dicts. The fake mirrors Layer3Client's read interface
exactly for the methods each rule consumes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from layer3_trading_exp import filter_rules
from layer3_trading_exp.filter_pipeline import (
    AddressContext,
    FilterResult,
    RuleSeverity,
)
from layer3_trading_exp.opportunity_detector import Opportunity, PoolLeg


def _opp(block_timestamp: int = 1_700_000_000) -> Opportunity:
    """Minimal opportunity for rule tests. Specific addresses don't matter
    here because tests pass AddressContext directly."""
    leg1 = PoolLeg(
        pool_address="0x" + "11" * 20, protocol="uniswap_v3", fee_bps=5,
        token_in_addr="0x" + "aa" * 20, token_in_symbol="A",
        token_out_addr="0x" + "bb" * 20, token_out_symbol="B",
        amount_in_raw=1, amount_out_raw=1,
    )
    leg2 = PoolLeg(
        pool_address="0x" + "22" * 20, protocol="aerodrome_volatile", fee_bps=30,
        token_in_addr="0x" + "bb" * 20, token_in_symbol="B",
        token_out_addr="0x" + "aa" * 20, token_out_symbol="A",
        amount_in_raw=1, amount_out_raw=1,
    )
    return Opportunity(
        opportunity_id="opp-1", block_number=100, block_timestamp=block_timestamp,
        detected_at="t", legs=(leg1, leg2),
        borrowed_token_addr="0x" + "aa" * 20, borrowed_token_symbol="A",
        notional_usd=10_000.0, amount_in_raw=1, amount_out_raw=1,
        gross_margin=0.005, gross_gain_usd=50.0,
        gas_cost_usd=0.005, flash_loan_fee_usd=5.0,
        expected_net_gain_usd=45.0,
    )


def _ctx(*addrs: str, deployers: tuple[str, ...] = ()) -> AddressContext:
    return AddressContext(
        contract_addresses=tuple(a.lower() for a in addrs),
        deployer_addresses=tuple(d.lower() for d in deployers),
    )


class FakeL3:
    """In-memory stand-in for Layer3Client. Each test populates only the
    methods/data it cares about. Methods not configured return Layer3Client's
    documented "no row found" sentinel (None or empty list)."""

    def __init__(self) -> None:
        self.contracts: dict[str, dict] = {}
        self.org_wallets: dict[str, list[dict]] = {}
        self.drain_approvals: dict[str, list[dict]] = {}
        self.extraction_events: dict[str, list[dict]] = {}
        self.trust_amp: dict[str, dict] = {}
        self.bytecode_family: dict[str, dict] = {}
        self.org_candidates: dict[str, list[dict]] = {}
        self.trap_events: dict[str, int] = {}
        self.deployer_profile: dict[str, dict] = {}
        self.infrastructure: dict[str, list[dict]] = {}

    # Read-side methods exposed to rules:
    def get_contract_classification(self, addr: str) -> Optional[dict]:
        return self.contracts.get(addr.lower())

    def get_org_wallet_rows(self, addr: str) -> list[dict]:
        return self.org_wallets.get(addr.lower(), [])

    def get_drain_detected_approvals_targeting(self, addr: str) -> list[dict]:
        return self.drain_approvals.get(addr.lower(), [])

    def get_extraction_events_referencing(self, addr: str) -> list[dict]:
        return self.extraction_events.get(addr.lower(), [])

    def get_trust_amplification(self, addr: str) -> Optional[dict]:
        return self.trust_amp.get(addr.lower())

    def get_bytecode_family(self, addr: str) -> Optional[dict]:
        return self.bytecode_family.get(addr.lower())

    def get_org_candidates_referencing(self, deployer: str) -> list[dict]:
        return self.org_candidates.get(deployer.lower(), [])

    def count_trap_events_for(self, addr: str) -> int:
        return self.trap_events.get(addr.lower(), 0)

    def get_deployer_profile(self, addr: str) -> Optional[dict]:
        return self.deployer_profile.get(addr.lower())

    def get_infrastructure_classification(self, addr: str) -> list[dict]:
        return self.infrastructure.get(addr.lower(), [])


# ---------------------------------------------------------------------------
# Rule 1 — confirmed_tier_contact
# ---------------------------------------------------------------------------

def test_rule_1_fires_for_confirmed_not_decayed():
    l3 = FakeL3()
    bad = "0x" + "ee" * 20
    l3.contracts[bad] = {"confidence_tier": "confirmed", "decayed_at": None}
    res = filter_rules.rule_1_confirmed_tier_contact(_opp(), _ctx(bad), l3)
    assert res.fired
    assert res.severity == RuleSeverity.HARD
    assert bad in res.matched_addresses


def test_rule_1_does_not_fire_for_decayed_confirmed():
    """confidence_tier='confirmed' but decayed_at set → no longer confirmed."""
    l3 = FakeL3()
    addr = "0x" + "ee" * 20
    l3.contracts[addr] = {"confidence_tier": "confirmed",
                           "decayed_at": "2026-01-01T00:00:00+00:00"}
    res = filter_rules.rule_1_confirmed_tier_contact(_opp(), _ctx(addr), l3)
    assert not res.fired


def test_rule_1_does_not_fire_for_suspected():
    l3 = FakeL3()
    addr = "0x" + "ee" * 20
    l3.contracts[addr] = {"confidence_tier": "suspected", "decayed_at": None}
    res = filter_rules.rule_1_confirmed_tier_contact(_opp(), _ctx(addr), l3)
    assert not res.fired


def test_rule_1_does_not_fire_when_no_contract_row():
    res = filter_rules.rule_1_confirmed_tier_contact(_opp(), _ctx("0x" + "ee" * 20), FakeL3())
    assert not res.fired
    assert res.matched_addresses == ()


# ---------------------------------------------------------------------------
# Rule 2 — org_wallet_membership
# ---------------------------------------------------------------------------

def test_rule_2_fires_when_pool_address_is_org_wallet():
    l3 = FakeL3()
    addr = "0x" + "11" * 20
    l3.org_wallets[addr] = [{"org_id": "org_001", "role": "router"}]
    res = filter_rules.rule_2_org_wallet_membership(_opp(), _ctx(addr), l3)
    assert res.fired
    assert addr in res.matched_addresses
    assert res.details["per_address"][addr]["rows"][0]["org_id"] == "org_001"


def test_rule_2_does_not_fire_when_no_org_match():
    res = filter_rules.rule_2_org_wallet_membership(_opp(), _ctx("0x" + "ff" * 20), FakeL3())
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 3 — drain_detected_approval
# ---------------------------------------------------------------------------

def test_rule_3_fires_when_token_has_drain_detected_row():
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.drain_approvals[token] = [{"victim_address": "0xv", "drain_tx_hash": "0xt"}]
    res = filter_rules.rule_3_drain_detected_approval(_opp(), _ctx(token), l3)
    assert res.fired
    assert res.details["per_address"][token]["drain_count"] == 1


def test_rule_3_does_not_fire_when_no_drain_rows():
    res = filter_rules.rule_3_drain_detected_approval(_opp(), _ctx("0xaa"), FakeL3())
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 4 — extraction_event_involvement
# ---------------------------------------------------------------------------

def test_rule_4_fires_when_address_referenced_in_extraction_event():
    l3 = FakeL3()
    bad = "0x" + "11" * 20
    l3.extraction_events[bad] = [{"event_id": "EXTRACTION_001"}]
    res = filter_rules.rule_4_extraction_event_involvement(_opp(), _ctx(bad), l3)
    assert res.fired
    assert "EXTRACTION_001" in res.details["per_address"][bad]["event_ids"]
    assert res.details["match_method"] == "prefix_substring"


def test_rule_4_does_not_fire_when_no_referencing_events():
    res = filter_rules.rule_4_extraction_event_involvement(_opp(), _ctx("0xaa"), FakeL3())
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 5 — bytecode_pattern_flags
# ---------------------------------------------------------------------------

def test_rule_5_fires_when_token_has_asymmetric_transfer():
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.contracts[token] = {"has_asymmetric_transfer": 1, "has_conditional_revert": 0}
    res = filter_rules.rule_5_bytecode_pattern_flags(_opp(), _ctx(token), l3)
    assert res.fired
    assert res.details["per_address"][token]["has_asymmetric_transfer"] is True


def test_rule_5_fires_when_token_has_conditional_revert():
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.contracts[token] = {"has_asymmetric_transfer": 0, "has_conditional_revert": 1}
    res = filter_rules.rule_5_bytecode_pattern_flags(_opp(), _ctx(token), l3)
    assert res.fired


def test_rule_5_does_not_fire_for_clean_bytecode():
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.contracts[token] = {"has_asymmetric_transfer": 0, "has_conditional_revert": 0}
    res = filter_rules.rule_5_bytecode_pattern_flags(_opp(), _ctx(token), l3)
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 6 — trust_amp_critical
# ---------------------------------------------------------------------------

def test_rule_6_fires_for_critical_alert():
    l3 = FakeL3()
    addr = "0x" + "11" * 20
    l3.trust_amp[addr] = {"alert_level": "CRITICAL", "amplification_factor": 12.5}
    res = filter_rules.rule_6_trust_amp_critical(_opp(), _ctx(addr), l3)
    assert res.fired
    assert res.severity == RuleSeverity.SOFT


def test_rule_6_does_not_fire_for_non_critical():
    l3 = FakeL3()
    addr = "0x" + "11" * 20
    l3.trust_amp[addr] = {"alert_level": "WARNING"}
    res = filter_rules.rule_6_trust_amp_critical(_opp(), _ctx(addr), l3)
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 7 — concentrated_taas_template
# ---------------------------------------------------------------------------

def test_rule_7_fires_for_cross_deployer_with_concentration():
    """Cross-deployer family with unique_deployers/member_count < 0.01 → fires."""
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.bytecode_family[token] = {
        "family_id": 99, "is_cross_deployer": True,
        "unique_deployers": 5, "member_count": 1000,  # ratio 0.005 < 0.01
    }
    res = filter_rules.rule_7_concentrated_taas_template(_opp(), _ctx(token), l3)
    assert res.fired
    assert res.details["per_address"][token]["concentration_ratio"] == 0.005


def test_rule_7_does_not_fire_when_not_cross_deployer():
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.bytecode_family[token] = {
        "family_id": 99, "is_cross_deployer": False,
        "unique_deployers": 5, "member_count": 1000,
    }
    res = filter_rules.rule_7_concentrated_taas_template(_opp(), _ctx(token), l3)
    assert not res.fired


def test_rule_7_does_not_fire_when_concentration_above_threshold():
    l3 = FakeL3()
    token = "0x" + "aa" * 20
    l3.bytecode_family[token] = {
        "family_id": 99, "is_cross_deployer": True,
        "unique_deployers": 50, "member_count": 100,  # ratio 0.5
    }
    res = filter_rules.rule_7_concentrated_taas_template(_opp(), _ctx(token), l3)
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 8 — pending_org_candidate
# ---------------------------------------------------------------------------

def test_rule_8_fires_when_deployer_in_pending_org_candidate():
    l3 = FakeL3()
    deployer = "0x" + "dd" * 20
    l3.org_candidates[deployer] = [{"candidate_id": "ORGC_42", "status": "pending"}]
    res = filter_rules.rule_8_pending_org_candidate(_opp(), _ctx(deployers=(deployer,)), l3)
    assert res.fired
    assert deployer in res.matched_addresses


def test_rule_8_does_not_fire_for_no_deployers():
    res = filter_rules.rule_8_pending_org_candidate(_opp(), _ctx(), FakeL3())
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 9 — trap_event_history
# ---------------------------------------------------------------------------

def test_rule_9_fires_when_address_has_trap_events():
    l3 = FakeL3()
    addr = "0x" + "11" * 20
    l3.trap_events[addr] = 7
    res = filter_rules.rule_9_trap_event_history(_opp(), _ctx(addr), l3)
    assert res.fired
    assert res.details["per_address"][addr]["trap_event_count"] == 7


def test_rule_9_does_not_fire_for_zero_count():
    res = filter_rules.rule_9_trap_event_history(_opp(), _ctx("0xabc"), FakeL3())
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 10 — suspected_tier_alone (WEAK signal, log-only)
# ---------------------------------------------------------------------------

def test_rule_10_fires_for_suspected_tier():
    l3 = FakeL3()
    addr = "0x" + "11" * 20
    l3.contracts[addr] = {"confidence_tier": "suspected"}
    res = filter_rules.rule_10_suspected_tier_alone(_opp(), _ctx(addr), l3)
    assert res.fired
    assert res.severity == RuleSeverity.WEAK


def test_rule_10_does_not_fire_for_confirmed():
    """confirmed tier is rule 1's territory; rule 10 is suspected-only."""
    l3 = FakeL3()
    addr = "0x" + "11" * 20
    l3.contracts[addr] = {"confidence_tier": "confirmed"}
    res = filter_rules.rule_10_suspected_tier_alone(_opp(), _ctx(addr), l3)
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 11 — new_deployer (< 7 days before opp timestamp)
# ---------------------------------------------------------------------------

def test_rule_11_fires_for_deployer_younger_than_7_days():
    l3 = FakeL3()
    deployer = "0x" + "dd" * 20
    opp_ts = 1_700_000_000
    # first_seen 3 days before opp
    first_seen = datetime.fromtimestamp(opp_ts - 3 * 86400, tz=timezone.utc)
    l3.deployer_profile[deployer] = {"first_seen": first_seen.isoformat()}
    res = filter_rules.rule_11_new_deployer(_opp(opp_ts), _ctx(deployers=(deployer,)), l3)
    assert res.fired
    assert res.severity == RuleSeverity.WEAK
    assert 2.9 < res.details["per_deployer"][deployer]["age_days"] < 3.1


def test_rule_11_does_not_fire_for_older_deployer():
    l3 = FakeL3()
    deployer = "0x" + "dd" * 20
    opp_ts = 1_700_000_000
    first_seen = datetime.fromtimestamp(opp_ts - 30 * 86400, tz=timezone.utc)
    l3.deployer_profile[deployer] = {"first_seen": first_seen.isoformat()}
    res = filter_rules.rule_11_new_deployer(_opp(opp_ts), _ctx(deployers=(deployer,)), l3)
    assert not res.fired


def test_rule_11_does_not_fire_when_deployer_first_seen_after_opp():
    """Defensive: a future first_seen (data error) should not fire the rule."""
    l3 = FakeL3()
    deployer = "0x" + "dd" * 20
    opp_ts = 1_700_000_000
    future = datetime.fromtimestamp(opp_ts + 86400, tz=timezone.utc)
    l3.deployer_profile[deployer] = {"first_seen": future.isoformat()}
    res = filter_rules.rule_11_new_deployer(_opp(opp_ts), _ctx(deployers=(deployer,)), l3)
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 12 — bytecode_family_membership (record-only enrichment)
# ---------------------------------------------------------------------------

def test_rule_12_records_family_for_non_concentrated_member():
    l3 = FakeL3()
    addr = "0x" + "aa" * 20
    l3.bytecode_family[addr] = {
        "family_id": 5, "is_cross_deployer": False,
        "unique_deployers": 3, "member_count": 10,
    }
    res = filter_rules.rule_12_bytecode_family_membership(_opp(), _ctx(addr), l3)
    assert res.fired
    assert res.details["per_address"][addr]["family_id"] == 5


def test_rule_12_skips_concentrated_cross_deployer_family():
    """Rule 7's territory — rule 12 should NOT double-record."""
    l3 = FakeL3()
    addr = "0x" + "aa" * 20
    l3.bytecode_family[addr] = {
        "family_id": 99, "is_cross_deployer": True,
        "unique_deployers": 5, "member_count": 1000,  # 0.005 < 0.01
    }
    res = filter_rules.rule_12_bytecode_family_membership(_opp(), _ctx(addr), l3)
    assert not res.fired


# ---------------------------------------------------------------------------
# Rule 13 — infrastructure_registry_match
# ---------------------------------------------------------------------------

def test_rule_13_fires_for_circle_cctp_classification():
    l3 = FakeL3()
    addr = "0x" + "cc" * 20
    l3.infrastructure[addr] = [{"classification": "circle_cctp_token_messenger"}]
    res = filter_rules.rule_13_infrastructure_registry_match(_opp(), _ctx(addr), l3)
    assert res.fired
    assert res.severity == RuleSeverity.LEGIT


def test_rule_13_does_not_fire_for_unwhitelisted_classification():
    """Spec: registry presence with NON-whitelisted classification is not a
    legit override. Currently only circle_cctp_* is whitelisted."""
    l3 = FakeL3()
    addr = "0x" + "cc" * 20
    l3.infrastructure[addr] = [{"classification": "some_other_classification"}]
    res = filter_rules.rule_13_infrastructure_registry_match(_opp(), _ctx(addr), l3)
    assert not res.fired


def test_rule_13_does_not_fire_for_absence():
    """Spec: registry absence is NOT a signal."""
    res = filter_rules.rule_13_infrastructure_registry_match(_opp(), _ctx("0xff"), FakeL3())
    assert not res.fired
