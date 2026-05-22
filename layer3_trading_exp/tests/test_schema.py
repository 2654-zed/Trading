"""Unit tests for schema.py."""

from __future__ import annotations

import json

import pytest

from layer3_trading_exp.filter_pipeline import FilterEvaluation, FilterResult, RuleSeverity
from layer3_trading_exp.opportunity_detector import Opportunity, PoolLeg
from layer3_trading_exp.bridge_model import BridgeLeg
from layer3_trading_exp.schema import (
    LOG_RECORD_REQUIRED_FIELDS,
    ROLLUP_CSV_COLUMNS,
    RULE_TIER,
    SCHEMA_VERSION,
    V2_ADDITIONAL_FIELDS,
    build_log_record,
    is_cross_chain_record,
    record_schema_version,
    validate_record,
)


def _leg(pool_addr: str, t_in: str, t_out: str, protocol: str, fee_bps: int = 5) -> PoolLeg:
    return PoolLeg(
        pool_address=pool_addr,
        protocol=protocol,
        fee_bps=fee_bps,
        token_in_addr=t_in,
        token_in_symbol="A",
        token_out_addr=t_out,
        token_out_symbol="B",
        amount_in_raw=10_000_000_000,
        amount_out_raw=10_031_000_000,
    )


def _make_opp() -> Opportunity:
    return Opportunity(
        opportunity_id="opp-1",
        block_number=45_700_000,
        block_timestamp=1_715_000_000,
        detected_at="2026-05-08T03:00:00+00:00",
        legs=(
            _leg("0x" + "aa" * 20, "0x" + "11" * 20, "0x" + "22" * 20, "uniswap_v3", 5),
            _leg("0x" + "bb" * 20, "0x" + "22" * 20, "0x" + "11" * 20, "aerodrome_slipstream", 5),
        ),
        borrowed_token_addr="0x" + "11" * 20,
        borrowed_token_symbol="USDC",
        notional_usd=10_000.0,
        amount_in_raw=10_000_000_000,
        amount_out_raw=10_031_000_000,
        gross_margin=0.0031,
        gross_gain_usd=31.0,
        gas_cost_usd=0.005,
        flash_loan_fee_usd=5.0,
        expected_net_gain_usd=26.0,
    )


def _make_eval(*, fired_rule_ids: tuple[int, ...] = (), degraded: bool = False) -> FilterEvaluation:
    severity_by_id = {
        1: RuleSeverity.HARD, 2: RuleSeverity.HARD, 3: RuleSeverity.HARD,
        4: RuleSeverity.HARD, 5: RuleSeverity.HARD,
        6: RuleSeverity.SOFT, 7: RuleSeverity.SOFT, 8: RuleSeverity.SOFT, 9: RuleSeverity.SOFT,
        10: RuleSeverity.WEAK, 11: RuleSeverity.WEAK, 12: RuleSeverity.WEAK,
        13: RuleSeverity.LEGIT,
    }
    name_by_id = {i: f"rule_{i}_name" for i in range(1, 14)}
    results = []
    for rid in range(1, 14):
        results.append(FilterResult(
            rule_id=rid,
            rule_name=name_by_id[rid],
            severity=severity_by_id[rid],
            fired=(rid in fired_rule_ids),
            matched_addresses=("0x" + "ee" * 20,) if rid in fired_rule_ids else (),
            details={"sample_extra": "value"} if rid == 1 else {},
        ))
    hard = any(r.fired and r.severity == RuleSeverity.HARD for r in results)
    soft = any(r.fired and r.severity == RuleSeverity.SOFT for r in results)
    legit = any(r.fired and r.severity == RuleSeverity.LEGIT for r in results)
    return FilterEvaluation(
        opportunity_id="opp-1",
        evaluated_at="2026-05-08T03:00:01+00:00",
        results=tuple(results),
        l3_freshness={
            "contracts": {"latest_ts": "2026-05-08T02:30:00+00:00", "age_seconds": 1800.0, "stale": False},
            "trap_events": {"latest_ts": "2026-04-28T00:00:00+00:00", "age_seconds": 870_000, "stale": True},
        },
        degraded=degraded,
        hard_flagged=hard,
        soft_flagged=soft,
        has_legit_override=legit,
        elapsed_ms=42.0,
    )


def test_build_log_record_has_all_required_fields():
    record = build_log_record(_make_opp(), _make_eval())
    for field in LOG_RECORD_REQUIRED_FIELDS:
        assert field in record, f"missing {field}"
        assert record[field] is not None, f"null in required {field}"


def test_build_log_record_path_dedups_tokens_in_order():
    record = build_log_record(_make_opp(), _make_eval())
    tokens = record["path"]["tokens"]
    # Two unique tokens in the round-trip path; preserved insertion order.
    assert tokens == ["0x" + "11" * 20, "0x" + "22" * 20]
    assert record["path"]["pools"] == ["0x" + "aa" * 20, "0x" + "bb" * 20]


def test_build_log_record_protocols_in_leg_order():
    record = build_log_record(_make_opp(), _make_eval())
    assert record["pool_protocols"] == ["uniswap_v3", "aerodrome_slipstream"]


def test_build_log_record_margin_in_bps():
    record = build_log_record(_make_opp(), _make_eval())
    # 0.0031 (decimal) -> 31 bps
    assert record["margin_gross_bps"] == 31


def test_build_log_record_filter_results_keyed_by_rule_id_and_name():
    record = build_log_record(_make_opp(), _make_eval(fired_rule_ids=(1,)))
    assert "rule_1_rule_1_name" in record["filter_results"]
    rule_1 = record["filter_results"]["rule_1_rule_1_name"]
    assert rule_1["fired"] is True
    assert rule_1["severity"] == "hard"
    assert rule_1["tier"] == "A"
    assert rule_1["matches"] == ["0x" + "ee" * 20]
    # rule-specific details are spread alongside canonical keys
    assert rule_1.get("sample_extra") == "value"


def test_build_log_record_flag_summary_lists_fired_rule_names_only():
    record = build_log_record(_make_opp(), _make_eval(fired_rule_ids=(1, 6)))
    # rule_1 is HARD, rule_6 is SOFT. Both should appear in flag_summary.
    assert "rule_1_name" in record["flag_summary"]
    assert "rule_6_name" in record["flag_summary"]
    # Rules 2-5, 7-13 didn't fire — should NOT be in flag_summary.
    assert "rule_3_name" not in record["flag_summary"]


def test_build_log_record_layer3_freshness_flattens_table_timestamps():
    record = build_log_record(_make_opp(), _make_eval())
    fr = record["layer3_freshness"]
    assert fr["contracts_last_updated"] == "2026-05-08T02:30:00+00:00"
    assert fr["trap_events_last_updated"] == "2026-04-28T00:00:00+00:00"
    # `trap_events` has stale=True in the fixture; `contracts` does not.
    assert fr["stale_tables"] == ["trap_events"]


def test_build_log_record_layer3_stale_mirrors_eval_degraded():
    rec_clean = build_log_record(_make_opp(), _make_eval(degraded=False))
    rec_degraded = build_log_record(_make_opp(), _make_eval(degraded=True))
    assert rec_clean["layer3_stale"] is False
    assert rec_degraded["layer3_stale"] is True


def test_record_is_json_serializable_with_default_str():
    record = build_log_record(_make_opp(), _make_eval())
    s = json.dumps(record, default=str)
    parsed = json.loads(s)
    assert parsed["opportunity_id"] == "opp-1"
    assert parsed["block_number"] == 45_700_000


def test_validate_record_passes_for_well_formed_record():
    record = build_log_record(_make_opp(), _make_eval())
    validate_record(record)  # should not raise


def test_validate_record_raises_on_missing_field():
    record = build_log_record(_make_opp(), _make_eval())
    del record["block_number"]
    with pytest.raises(ValueError, match="missing required field: block_number"):
        validate_record(record)


def test_validate_record_raises_on_null_field():
    record = build_log_record(_make_opp(), _make_eval())
    record["chain"] = None
    with pytest.raises(ValueError, match="null"):
        validate_record(record)


def test_validate_record_accepts_falsy_but_present():
    record = build_log_record(_make_opp(), _make_eval())
    # `flag_summary` is an empty list when nothing fires — empty != null.
    assert record["flag_summary"] == []
    validate_record(record)


def test_rollup_csv_column_count():
    # 7 spec scalars + 13 rule columns + 3 medians + 1 distribution + 2 degradation = 26
    assert len(ROLLUP_CSV_COLUMNS) == 26
    assert ROLLUP_CSV_COLUMNS[0] == "date"
    assert "rule_1_fire_count" in ROLLUP_CSV_COLUMNS
    assert "rule_13_fire_count" in ROLLUP_CSV_COLUMNS
    assert "pool_type_distribution" in ROLLUP_CSV_COLUMNS


def test_rule_tier_mapping_complete_for_all_13():
    for i in range(1, 14):
        assert i in RULE_TIER


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.5 (D-009): JSONL schema v2 additive migration.
# ----------------------------------------------------------------------------


def _make_cross_chain_opp() -> Opportunity:
    """Construct an Opportunity that's been through the cross-chain detector
    (variant 2: one pool per chain, two bridge legs)."""
    base_leg = _leg("0xb1" + "00" * 19, "0xu1" + "00" * 19, "0xw1" + "00" * 19,
                    "uniswap_v3", 5)
    arb_leg = _leg("0xa1" + "00" * 19, "0xw2" + "00" * 19, "0xu2" + "00" * 19,
                   "uniswap_v3", 5)
    bridge_out = BridgeLeg(
        src_chain="base", dst_chain="arbitrum", token="WETH",
        token_address_src="0xw1" + "00" * 19, token_address_dst="0xw2" + "00" * 19,
        fee_bps=8.0, latency_seconds=30.0,
    )
    bridge_return = BridgeLeg(
        src_chain="arbitrum", dst_chain="base", token="USDC",
        token_address_src="0xu2" + "00" * 19, token_address_dst="0xu1" + "00" * 19,
        fee_bps=10.0, latency_seconds=30.0,
    )
    return Opportunity(
        opportunity_id="cc-opp-1",
        block_number=46_000_000, block_timestamp=1_715_500_000,
        detected_at="2026-05-16T12:00:00+00:00",
        legs=(base_leg, arb_leg),
        borrowed_token_addr="0xu1" + "00" * 19,
        borrowed_token_symbol="USDC",
        notional_usd=10_000.0,
        amount_in_raw=10_000_000_000, amount_out_raw=10_060_000_000,
        gross_margin=0.006, gross_gain_usd=60.0,
        gas_cost_usd=0.01, flash_loan_fee_usd=5.0,
        expected_net_gain_usd=54.99,
        # cross-chain fields
        bridge_legs=(bridge_out, bridge_return),
        path_chains=("base", "arbitrum"),
        gross_margin_raw=0.0075,                # pre-haircut
        latency_drift_haircut_bps=15.0,         # WETH haircut
        borrow_chain="base",
        bridge_fee_bps_total=18.0,
    )


def test_v2_record_includes_schema_version():
    opp = _make_opp()
    ev = _make_eval()
    record = build_log_record(opp, ev)
    assert record["schema_version"] == SCHEMA_VERSION == 2


def test_v2_record_includes_all_v2_additional_fields():
    opp = _make_opp()
    record = build_log_record(opp, _make_eval())
    for field in V2_ADDITIONAL_FIELDS:
        assert field in record, f"v2 field {field!r} missing from record"


def test_v2_intra_chain_record_has_empty_bridge_legs():
    """Phase 1-shape opportunities (no bridge_legs populated) must produce
    a record with bridge_legs=[] — preserves v1 semantics."""
    opp = _make_opp()
    record = build_log_record(opp, _make_eval())
    assert record["bridge_legs"] == []
    assert record["latency_drift_haircut_bps"] == 0.0
    assert record["chains"] == ["base"]
    assert record["path_chains"] == ["base", "base"]


def test_v2_cross_chain_record_populates_bridge_legs():
    opp = _make_cross_chain_opp()
    record = build_log_record(opp, _make_eval())
    assert is_cross_chain_record(record)
    assert len(record["bridge_legs"]) == 2
    assert record["bridge_legs"][0]["src_chain"] == "base"
    assert record["bridge_legs"][0]["dst_chain"] == "arbitrum"
    assert record["bridge_legs"][0]["token"] == "WETH"
    assert record["bridge_legs"][1]["src_chain"] == "arbitrum"
    assert record["bridge_legs"][1]["dst_chain"] == "base"
    assert record["chains"] == ["arbitrum", "base"]
    assert record["path_chains"] == ["base", "arbitrum"]
    assert record["latency_drift_haircut_bps"] == 15.0
    assert record["borrow_chain"] == "base"


def test_v2_cost_breakdown_separates_bridge_and_swap():
    opp = _make_cross_chain_opp()
    record = build_log_record(opp, _make_eval())
    cb = record["cost_breakdown"]
    assert cb["bridge_fees_bps"] == 18.0  # 8 + 10
    assert cb["latency_drift_haircut_bps"] == 15.0
    assert cb["gas_usd"] == 0.01
    assert cb["flash_loan_fee_usd"] == 5.0
    # Swap fees: each leg fee_bps=5 → total 10
    assert cb["swap_fees_bps_total"] == 10.0


def test_record_schema_version_returns_1_for_legacy_records():
    """v1 records (Phase 1, no `schema_version` field) report as version 1."""
    legacy = {"opportunity_id": "x", "chain": "base"}
    assert record_schema_version(legacy) == 1


def test_record_schema_version_returns_2_for_v2_records():
    opp = _make_opp()
    record = build_log_record(opp, _make_eval())
    assert record_schema_version(record) == 2


def test_is_cross_chain_record_false_for_v1_and_intra_v2():
    """v1 records have no bridge_legs key; intra-chain v2 has empty list."""
    legacy = {"opportunity_id": "x"}
    assert not is_cross_chain_record(legacy)
    opp_intra = _make_opp()
    record = build_log_record(opp_intra, _make_eval())
    assert not is_cross_chain_record(record)


def test_v2_record_validates_with_existing_validate_record():
    """v2 records must pass `validate_record` — all v1 required fields still
    present. (v2 additional fields are not in the required set; their absence
    is allowed for v1-shaped records.)"""
    opp = _make_cross_chain_opp()
    record = build_log_record(opp, _make_eval())
    validate_record(record)  # should not raise


def test_phase_1_legacy_record_still_validates():
    """Hand-craft a v1-shape record (no v2 fields) and verify it still
    passes validate_record. This guards against the v2 migration accidentally
    making `schema_version` required, which would break Phase 1 replay."""
    legacy = {
        "opportunity_id": "legacy-1",
        "timestamp": "2026-05-13T05:00:00+00:00",
        "block_number": 45_928_573,
        "chain": "base",
        "path": {"tokens": ["0x" + "11" * 20, "0x" + "22" * 20],
                 "pools": ["0x" + "aa" * 20, "0x" + "bb" * 20]},
        "pool_protocols": ["uniswap_v3", "aerodrome_slipstream"],
        "margin_gross_bps": 30,
        "gas_estimate_usd": 0.005,
        "expected_gain_usd": 26.0,
        "depth_notional_usd": 10_000.0,
        "filter_results": {},
        "layer3_freshness": {"stale_tables": [], "degraded": False},
        "hard_flagged": False,
        "soft_flagged": False,
        "flag_summary": [],
        "layer3_stale": False,
    }
    validate_record(legacy)  # must not raise
    assert record_schema_version(legacy) == 1
    assert not is_cross_chain_record(legacy)
