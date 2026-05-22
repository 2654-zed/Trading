"""Phase 1.3 — the 13 filter rules from spec §Phase 1.3.

Each rule is a callable `(opp, ctx, l3) -> FilterResult`. Each runs every
opportunity, no short-circuiting. Rules are intentionally narrow — one
intelligence signal per rule — so the JSONL log captures the full intelligence
trace for downstream H2/H3 analysis.

Rules 1–5 (HARD): would block in Phase 2 execution.
Rules 6–9 (SOFT): would modify size in Phase 2 execution.
Rules 10–12 (WEAK): record-only signals.
Rule 13 (LEGIT): known-legit override (presence is informative; absence is NOT).

All rules expect addresses in `ctx` to already be lowercased. They produce
`matched_addresses` lists with the addresses that triggered the rule, plus
a `details` dict with rule-specific context (e.g. confidence_tier value,
family_id, deployer age in days).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .filter_pipeline import AddressContext, FilterResult, RuleSeverity

if TYPE_CHECKING:
    from .layer3_client import Layer3Client
    from .opportunity_detector import Opportunity


# ---------------------------------------------------------------------------
# HARD filters (rules 1-5) — would block execution in Phase 2.
# ---------------------------------------------------------------------------

def rule_1_confirmed_tier_contact(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Fire if any contract in the path is in the contracts table with
    confidence_tier='confirmed' and decayed_at IS NULL. (Tier A: 83.2%
    observable harm rate per April 19 audit.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        row = l3.get_contract_classification(addr)
        if row is None:
            continue
        tier = row.get("confidence_tier")
        decayed = row.get("decayed_at")
        if tier == "confirmed" and decayed is None:
            matched.append(addr)
            details["per_address"][addr] = {
                "confidence_tier": tier,
                "decayed_at": decayed,
            }
    return FilterResult(
        rule_id=1, rule_name="confirmed_tier_contact",
        severity=RuleSeverity.HARD,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_2_org_wallet_membership(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Fire if any contract in the path is in org_wallets. (Tier A: manually curated.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        rows = l3.get_org_wallet_rows(addr)
        if rows:
            matched.append(addr)
            details["per_address"][addr] = {"rows": rows}
    return FilterResult(
        rule_id=2, rule_name="org_wallet_membership",
        severity=RuleSeverity.HARD,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_3_drain_detected_approval(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Fire if any token contract in the path has approval_watchlist row with
    drain_detected=1. (Tier A: on-chain event.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        rows = l3.get_drain_detected_approvals_targeting(addr)
        if rows:
            matched.append(addr)
            details["per_address"][addr] = {"drain_count": len(rows)}
    return FilterResult(
        rule_id=3, rule_name="drain_detected_approval",
        severity=RuleSeverity.HARD,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_4_extraction_event_involvement(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Fire if any contract in the path is referenced in extraction_events
    (raw_transactions JSON or summary text). Schema caveat: matches on 8-char
    address prefix — see Layer3Client.get_extraction_events_referencing for the
    false-positive risk discussion. (Tier A: documented event.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}, "match_method": "prefix_substring"}
    for addr in ctx.contract_addresses:
        events = l3.get_extraction_events_referencing(addr)
        if events:
            matched.append(addr)
            details["per_address"][addr] = {
                "event_ids": [e.get("event_id") for e in events],
            }
    return FilterResult(
        rule_id=4, rule_name="extraction_event_involvement",
        severity=RuleSeverity.HARD,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_5_bytecode_pattern_flags(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Fire if any token contract has has_asymmetric_transfer=1 OR
    has_conditional_revert=1. (Tier A: deterministic bytecode analysis;
    direct honeypot risk.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        row = l3.get_contract_classification(addr)
        if row is None:
            continue
        asym = bool(row.get("has_asymmetric_transfer"))
        cond = bool(row.get("has_conditional_revert"))
        if asym or cond:
            matched.append(addr)
            details["per_address"][addr] = {
                "has_asymmetric_transfer": asym,
                "has_conditional_revert": cond,
            }
    return FilterResult(
        rule_id=5, rule_name="bytecode_pattern_flags",
        severity=RuleSeverity.HARD,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


# ---------------------------------------------------------------------------
# SOFT flags (rules 6-9) — would modify size in Phase 2.
# ---------------------------------------------------------------------------

def rule_6_trust_amp_critical(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Tag if any contract has trust_amplification.alert_level='CRITICAL'.
    (Tier B: inferential.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        row = l3.get_trust_amplification(addr)
        if row is None:
            continue
        if row.get("alert_level") == "CRITICAL":
            matched.append(addr)
            details["per_address"][addr] = {
                "router_percentage": row.get("router_percentage"),
                "amplification_factor": row.get("amplification_factor"),
            }
    return FilterResult(
        rule_id=6, rule_name="trust_amp_critical",
        severity=RuleSeverity.SOFT,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


# Threshold per spec rule 7: "concentration asymmetry"
# = unique_deployers / member_count < 0.01
_CONCENTRATION_THRESHOLD = 0.01


def rule_7_concentrated_taas_template(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Tag if any token belongs to a bytecode_family with is_cross_deployer=true
    AND unique_deployers/member_count < 0.01. (Tier B.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        row = l3.get_bytecode_family(addr)
        if row is None:
            continue
        is_cross = bool(row.get("is_cross_deployer"))
        unique = row.get("unique_deployers") or 0
        member_count = row.get("member_count") or 0
        if not is_cross or member_count <= 0:
            continue
        ratio = unique / member_count
        if ratio < _CONCENTRATION_THRESHOLD:
            matched.append(addr)
            details["per_address"][addr] = {
                "family_id": row.get("family_id"),
                "unique_deployers": unique,
                "member_count": member_count,
                "concentration_ratio": ratio,
            }
    return FilterResult(
        rule_id=7, rule_name="concentrated_taas_template",
        severity=RuleSeverity.SOFT,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_8_pending_org_candidate(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Tag if any deployer of a contract in the path has an org_candidates row
    with status='pending'. (Tier B.)"""
    matched: list[str] = []
    details: dict = {"per_deployer": {}}
    for deployer in ctx.deployer_addresses:
        rows = l3.get_org_candidates_referencing(deployer)
        if rows:
            matched.append(deployer)
            details["per_deployer"][deployer] = {
                "candidate_ids": [r.get("candidate_id") for r in rows],
            }
    return FilterResult(
        rule_id=8, rule_name="pending_org_candidate",
        severity=RuleSeverity.SOFT,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_9_trap_event_history(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Tag if any contract has >=1 row in trap_events. Records the count.
    (Tier A arithmetic, Tier B interpretation for arbitrage context.)"""
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        count = l3.count_trap_events_for(addr)
        if count > 0:
            matched.append(addr)
            details["per_address"][addr] = {"trap_event_count": count}
    return FilterResult(
        rule_id=9, rule_name="trap_event_history",
        severity=RuleSeverity.SOFT,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


# ---------------------------------------------------------------------------
# WEAK signals (rules 10-12) — log-only, no flag.
# ---------------------------------------------------------------------------

def rule_10_suspected_tier_alone(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Record if any contract has confidence_tier='suspected' but DO NOT flag.
    (Tier B; April 19 audit showed 0% PPV at 30-day horizon — not actionable.)
    """
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        row = l3.get_contract_classification(addr)
        if row is None:
            continue
        if row.get("confidence_tier") == "suspected":
            matched.append(addr)
            details["per_address"][addr] = {"confidence_tier": "suspected"}
    return FilterResult(
        rule_id=10, rule_name="suspected_tier_alone",
        severity=RuleSeverity.WEAK,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


_NEW_DEPLOYER_THRESHOLD = timedelta(days=7)


def rule_11_new_deployer(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Record if any deployer's first_seen is < 7 days before the opportunity
    timestamp. Do not flag."""
    matched: list[str] = []
    details: dict = {"per_deployer": {}}
    opp_ts = datetime.fromtimestamp(opp.block_timestamp, tz=timezone.utc)
    for deployer in ctx.deployer_addresses:
        profile = l3.get_deployer_profile(deployer)
        if profile is None:
            continue
        first_seen_str = profile.get("first_seen")
        if not first_seen_str:
            continue
        try:
            first_seen = datetime.fromisoformat(str(first_seen_str).replace("Z", "+00:00"))
        except ValueError:
            continue
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        age = opp_ts - first_seen
        if age < _NEW_DEPLOYER_THRESHOLD and age >= timedelta(0):
            matched.append(deployer)
            details["per_deployer"][deployer] = {
                "first_seen": first_seen.isoformat(),
                "age_days": age.total_seconds() / 86400.0,
            }
    return FilterResult(
        rule_id=11, rule_name="new_deployer",
        severity=RuleSeverity.WEAK,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


def rule_12_bytecode_family_membership(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Record family_id and basic properties for each contract that's in any
    bytecode_family. Do not flag — pure log-only enrichment.

    Excludes families that already triggered Rule 7 (concentrated cross-deployer
    families) so we don't double-count them.
    """
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        row = l3.get_bytecode_family(addr)
        if row is None:
            continue
        is_cross = bool(row.get("is_cross_deployer"))
        unique = row.get("unique_deployers") or 0
        member_count = row.get("member_count") or 0
        ratio = (unique / member_count) if member_count > 0 else None
        # Skip the rule-7 case (record-only complement, not a duplicate).
        if is_cross and ratio is not None and ratio < _CONCENTRATION_THRESHOLD:
            continue
        matched.append(addr)
        details["per_address"][addr] = {
            "family_id": row.get("family_id"),
            "is_cross_deployer": is_cross,
            "member_count": member_count,
            "unique_deployers": unique,
            "concentration_ratio": ratio,
        }
    return FilterResult(
        rule_id=12, rule_name="bytecode_family_membership",
        severity=RuleSeverity.WEAK,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


# ---------------------------------------------------------------------------
# Known-legit override (rule 13).
# ---------------------------------------------------------------------------

# Initially only the circle_cctp_* family (per spec). Future additions
# require updating this whitelist explicitly — no implicit registry trust.
_INFRASTRUCTURE_WHITELIST_PREFIXES = ("circle_cctp_",)


def rule_13_infrastructure_registry_match(
    opp: "Opportunity", ctx: AddressContext, l3: "Layer3Client",
) -> FilterResult:
    """Record if any contract is in infrastructure_registry with a whitelisted
    classification. Spec note: registry ABSENCE is NOT a signal — most
    legitimate infrastructure isn't registered. Do not use absence as a flag.
    """
    matched: list[str] = []
    details: dict = {"per_address": {}}
    for addr in ctx.contract_addresses:
        rows = l3.get_infrastructure_classification(addr)
        if not rows:
            continue
        whitelisted = [
            r for r in rows
            if any(str(r.get("classification", "")).startswith(p)
                   for p in _INFRASTRUCTURE_WHITELIST_PREFIXES)
        ]
        if whitelisted:
            matched.append(addr)
            details["per_address"][addr] = {
                "classifications": [r.get("classification") for r in whitelisted],
            }
    return FilterResult(
        rule_id=13, rule_name="infrastructure_registry_match",
        severity=RuleSeverity.LEGIT,
        fired=bool(matched),
        matched_addresses=tuple(matched),
        details=details,
    )


# Ordered execution list — must match spec rule numbering.
ALL_RULES = (
    rule_1_confirmed_tier_contact,
    rule_2_org_wallet_membership,
    rule_3_drain_detected_approval,
    rule_4_extraction_event_involvement,
    rule_5_bytecode_pattern_flags,
    rule_6_trust_amp_critical,
    rule_7_concentrated_taas_template,
    rule_8_pending_org_candidate,
    rule_9_trap_event_history,
    rule_10_suspected_tier_alone,
    rule_11_new_deployer,
    rule_12_bytecode_family_membership,
    rule_13_infrastructure_registry_match,
)


__all__ = ["ALL_RULES"] + [r.__name__ for r in ALL_RULES]
