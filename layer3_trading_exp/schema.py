"""Phase 1.4 + Phase 2 sub-phase 2.5 — JSONL log record schema and rollup CSV.

`build_log_record(opportunity, evaluation)` produces the JSONL line written
by `OpportunityLogger`. `validate_record(record)` raises if any required
field is missing or null.

Spec reference: LAYER3_TRADING_EXPERIMENT.md §Phase 1.4 + PHASE_2_CROSS_CHAIN_SPEC.md
§sub-phase 2.5. The schema is fixed at run start per spec invariant #2.

Schema versioning (Phase 2 sub-phase 2.5, D-009):
  - **v1** (Phase 1): `chain` was hardcoded to "base"; no cross-chain fields.
    Records without a `schema_version` field default to v1 on read.
  - **v2** (Phase 2): adds `schema_version: 2`, `chains: list[str]` summary,
    `bridge_legs: list[dict]`, `path_chains: list[str]`,
    `latency_drift_haircut_bps`, `gross_margin_raw_bps`,
    `cost_breakdown: dict`. `chain` becomes dynamic. Empty `bridge_legs`
    for intra-chain records preserves v1 back-compat.

ADDITIVE migration: no v1 field is removed or renamed. Phase 1 analysis
scripts read v2 records correctly. Phase 1 JSONL replays through v2
analysis without changes.

Schema deviation note (one, carried from Phase 1.4):
The spec's `path` block contains only `tokens` and `pools`. The daily
rollup needs `pool_type_distribution`, which requires per-leg protocol
attribution. We add a sibling top-level field `pool_protocols: [proto_a,
proto_b]` rather than nesting under `path`, so the literal `path` shape
stays spec-conformant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .filter_pipeline import FilterEvaluation
    from .opportunity_detector import Opportunity


# Tier labels per spec §Phase 1.3. Rules 1-5 are Tier A (deterministic /
# documented harm); 6-10 are Tier B (inferential); 11-13 are weak signals
# without a tier label in the spec.
RULE_TIER: dict[int, str | None] = {
    1: "A", 2: "A", 3: "A", 4: "A", 5: "A",
    6: "B", 7: "B", 8: "B", 9: "B", 10: "B",
    11: None, 12: None, 13: None,
}


# Schema version. v1 = Phase 1 (hardcoded chain), v2 = Phase 2 sub-phase 2.5.
SCHEMA_VERSION: int = 2

# Required top-level fields per spec §Phase 1.4 schema. validate_record()
# raises if any of these is absent or None. These are the v1-compatible set
# — v2 ADDS fields (see V2_ADDITIONAL_FIELDS) but does not remove any.
LOG_RECORD_REQUIRED_FIELDS: tuple[str, ...] = (
    "opportunity_id",
    "timestamp",
    "block_number",
    "chain",
    "path",
    "pool_protocols",   # schema deviation; see module docstring
    "margin_gross_bps",
    "gas_estimate_usd",
    "expected_gain_usd",
    "depth_notional_usd",
    "filter_results",
    "layer3_freshness",
    "hard_flagged",
    "soft_flagged",
    "flag_summary",
    "layer3_stale",
)


# Phase 2 sub-phase 2.5 v2 additive fields. validate_record() does NOT
# require these (so v1 records still validate). For v2 records the writer
# always populates them; the reader treats absence as v1 defaults.
V2_ADDITIONAL_FIELDS: tuple[str, ...] = (
    "schema_version",
    "chains",                       # ordered list of chains in the path
    "path_chains",                  # per-leg chain (matches `path.pools` order)
    "bridge_legs",                  # list[dict] — empty for intra-chain
    "latency_drift_haircut_bps",    # float — 0.0 for intra-chain
    "gross_margin_raw_bps",         # pre-haircut margin in bps
    "borrow_chain",                 # where the flash loan happens
    "cost_breakdown",               # dict separating bridge_fees / swap / gas
    "degraded_per_table",           # sub-phase 2.6 (D-012): per-table stale bools
)


# CSV column order for daily rollup. Sorted: spec-fixed fields first, then
# rule_1..rule_13, then margin medians, then distributions, then degradation
# counters. Idempotency requires this ordering be stable across reruns.
ROLLUP_CSV_COLUMNS: tuple[str, ...] = (
    "date",
    "total_opportunities",
    "hard_flagged_count",
    "soft_flagged_count",
    "unflagged_count",
    "hard_flag_rate",
    "soft_flag_rate",
    *(f"rule_{i}_fire_count" for i in range(1, 14)),
    "median_gross_margin_bps_all",
    "median_gross_margin_bps_unflagged",
    "median_gross_margin_bps_hard_flagged",
    "pool_type_distribution",
    "degraded_opportunities_count",
    "freshness_violations_count",
)


def build_log_record(opp: "Opportunity", evaluation: "FilterEvaluation") -> dict:
    """Produce the JSONL record for one opportunity + its filter evaluation.

    The returned dict is JSON-serializable (only primitives, lists, dicts).
    Field order in the dict follows insertion order — `json.dumps` will preserve
    it, which keeps log files diff-friendly across runs.
    """
    # Path: dedup tokens preserving order, list pools in leg order.
    legs = opp.legs
    seen: set[str] = set()
    tokens: list[str] = []
    for addr in (legs[0].token_in_addr, legs[0].token_out_addr,
                 legs[1].token_in_addr, legs[1].token_out_addr):
        a = addr.lower()
        if a not in seen:
            seen.add(a)
            tokens.append(a)
    pools = [legs[0].pool_address, legs[1].pool_address]
    protocols = [legs[0].protocol, legs[1].protocol]

    # Filter results: keyed by `rule_{id}_{name}`. Each value carries fired,
    # severity, tier (from RULE_TIER), matches, and rule-specific details.
    filter_results: dict = {}
    flag_summary: list[str] = []
    for r in evaluation.results:
        key = f"rule_{r.rule_id}_{r.rule_name}"
        # Spread rule-specific details in but never let them shadow the
        # canonical structural keys.
        value: dict = {}
        # Start with details, then overwrite with canonical fields so
        # canonical fields always win.
        if isinstance(r.details, dict):
            value.update(r.details)
        value["fired"] = bool(r.fired)
        value["severity"] = r.severity.value
        value["tier"] = RULE_TIER.get(r.rule_id)
        value["matches"] = list(r.matched_addresses)
        filter_results[key] = value
        if r.fired:
            flag_summary.append(r.rule_name)

    # Freshness block: flatten l3_freshness dict to spec's
    # `{table}_last_updated` keys, plus stale_tables list and degraded bool.
    freshness_block: dict = {}
    stale_tables: list[str] = []
    for table, info in (evaluation.l3_freshness or {}).items():
        latest = info.get("latest_ts") if isinstance(info, dict) else None
        # Normalize datetime / non-string types to ISO strings.
        if hasattr(latest, "isoformat"):
            latest = latest.isoformat()
        elif latest is not None and not isinstance(latest, str):
            latest = str(latest)
        freshness_block[f"{table}_last_updated"] = latest
        if isinstance(info, dict) and info.get("stale"):
            stale_tables.append(table)
    freshness_block["stale_tables"] = sorted(stale_tables)
    freshness_block["degraded"] = bool(evaluation.degraded)

    # Phase 2 sub-phase 2.5: derive v2 fields from Opportunity (cross-chain
    # attributes are defaulted to intra-chain-safe values on Phase 1 opps).
    bridge_legs_list = []
    for bl in (opp.bridge_legs or ()):
        # bridge_model.BridgeLeg has a `.to_dict()`; defensive isinstance.
        if hasattr(bl, "to_dict"):
            bridge_legs_list.append(bl.to_dict())
        elif isinstance(bl, dict):
            bridge_legs_list.append(dict(bl))
    is_cross_chain = bool(bridge_legs_list)
    # `path_chains` per pool hop. For intra-chain on chain X: (X, X).
    # For cross-chain (variant 2 — one pool per chain): (src, dst).
    if opp.path_chains:
        path_chains_list = list(opp.path_chains)
    elif is_cross_chain:
        # Fallback if Opportunity didn't populate path_chains but bridge_legs
        # are present (shouldn't happen normally; defensive).
        path_chains_list = [opp.bridge_legs[0].src_chain, opp.bridge_legs[0].dst_chain]
    else:
        path_chains_list = [opp.borrow_chain, opp.borrow_chain]
    # `chains` is the unique-sorted set of chains touched by the opportunity.
    chains_list = sorted(set(path_chains_list))
    # The legacy top-level `chain` field: borrow_chain (where the flash
    # loan executes). For Phase 1 intra-chain opps this is "base" — same
    # as the v1 hardcoded value. For cross-chain opps this is the src_chain.
    chain_top = opp.borrow_chain or "base"

    # Cost breakdown (v2): separate bridge_fees from swap_fees + gas + flash.
    # Swap fees per leg come from pool.fee_bps * notional; we approximate by
    # using leg-level fees from PoolLeg if present. For intra-chain Phase 1
    # opps, bridge_fees_bps = 0.
    leg_swap_fee_bps_total = 0.0
    for lg in (legs[0], legs[1]):
        if getattr(lg, "fee_bps", None) is not None:
            leg_swap_fee_bps_total += float(lg.fee_bps)
    cost_breakdown: dict = {
        "gas_usd": float(opp.gas_cost_usd),
        "flash_loan_fee_usd": float(opp.flash_loan_fee_usd),
        "bridge_fees_bps": float(opp.bridge_fee_bps_total),
        "swap_fees_bps_total": leg_swap_fee_bps_total,
        "latency_drift_haircut_bps": float(opp.latency_drift_haircut_bps),
    }

    record: dict = {
        # --- v1 / unchanged fields (Phase 1 back-compat) ---
        "opportunity_id": opp.opportunity_id,
        "timestamp": evaluation.evaluated_at,
        "block_number": int(opp.block_number),
        # `chain` was hardcoded "base" in v1; now sourced from the opportunity.
        # Phase 1 intra-chain opps default borrow_chain="base" so unchanged
        # value when those Opportunity instances are passed through.
        "chain": chain_top,
        "path": {
            "tokens": tokens,
            "pools": pools,
        },
        "pool_protocols": protocols,
        "margin_gross_bps": int(round(opp.gross_margin * 10_000)),
        "gas_estimate_usd": float(opp.gas_cost_usd),
        "expected_gain_usd": float(opp.expected_net_gain_usd),
        "depth_notional_usd": float(opp.notional_usd),
        "filter_results": filter_results,
        "layer3_freshness": freshness_block,
        "hard_flagged": bool(evaluation.hard_flagged),
        "soft_flagged": bool(evaluation.soft_flagged),
        "flag_summary": flag_summary,
        "layer3_stale": bool(evaluation.degraded),
        # --- v2 additive fields ---
        "schema_version": SCHEMA_VERSION,
        "chains": chains_list,
        "path_chains": path_chains_list,
        "bridge_legs": bridge_legs_list,
        "latency_drift_haircut_bps": float(opp.latency_drift_haircut_bps),
        "gross_margin_raw_bps": int(round(opp.gross_margin_raw * 10_000))
            if opp.gross_margin_raw else int(round(opp.gross_margin * 10_000)),
        "borrow_chain": chain_top,
        "cost_breakdown": cost_breakdown,
        # Sub-phase 2.6 (D-012): per-table degraded flags. Empty dict if
        # the evaluation didn't compute them (pre-2.6 callers); v2.6+
        # evaluations populate one bool per filter-critical table.
        "degraded_per_table": dict(getattr(evaluation, "degraded_per_table", {})),
    }
    return record


def validate_record(record: dict) -> None:
    """Raise ValueError if any spec-required top-level field is missing or null.

    Booleans, zeros, and empty lists are valid (present but falsy). Only
    None / missing keys fail.
    """
    if not isinstance(record, dict):
        raise ValueError(f"record must be dict, got {type(record).__name__}")
    for field in LOG_RECORD_REQUIRED_FIELDS:
        if field not in record:
            raise ValueError(f"missing required field: {field}")
        if record[field] is None:
            raise ValueError(f"required field {field!r} is null")

    # Structural checks on nested objects.
    path = record["path"]
    if not isinstance(path, dict) or "tokens" not in path or "pools" not in path:
        raise ValueError("path must be a dict with `tokens` and `pools`")
    if not isinstance(record["filter_results"], dict):
        raise ValueError("filter_results must be a dict")
    if not isinstance(record["layer3_freshness"], dict):
        raise ValueError("layer3_freshness must be a dict")


def record_schema_version(record: dict) -> int:
    """Return the schema version of a record. v1 records (no
    `schema_version` field) report as 1. v2 records report 2."""
    return int(record.get("schema_version", 1))


def is_cross_chain_record(record: dict) -> bool:
    """True for v2 records that carry non-empty `bridge_legs`. False
    for intra-chain v2 records or any v1 record."""
    bl = record.get("bridge_legs")
    return bool(bl)


__all__ = [
    "build_log_record",
    "validate_record",
    "record_schema_version",
    "is_cross_chain_record",
    "LOG_RECORD_REQUIRED_FIELDS",
    "V2_ADDITIONAL_FIELDS",
    "SCHEMA_VERSION",
    "ROLLUP_CSV_COLUMNS",
    "RULE_TIER",
]
