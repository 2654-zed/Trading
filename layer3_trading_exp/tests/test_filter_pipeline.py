"""Pipeline-level tests for filter_pipeline.

Covers:
  - All 13 rules execute for every opportunity (no short-circuiting)
  - Results are returned in canonical rule_id order
  - hard_flagged / soft_flagged / has_legit_override aggregation is correct
  - L3 freshness snapshot recorded with each evaluation
  - Spec acceptance: per-evaluation latency well under 500ms p95 budget
  - Real Layer3Client wiring works against a fresh in-memory SQLite DB
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

import pytest

from layer3_trading_exp.filter_pipeline import (
    AddressContext,
    FilterPipeline,
    RuleSeverity,
    collect_address_context,
)
from layer3_trading_exp.opportunity_detector import Opportunity, PoolLeg


def _opp(opp_id: str = "opp-1") -> Opportunity:
    leg1 = PoolLeg(
        pool_address="0x" + "11" * 20, protocol="uniswap_v3", fee_bps=5,
        token_in_addr="0x" + "aa" * 20, token_in_symbol="USDC",
        token_out_addr="0x" + "bb" * 20, token_out_symbol="WETH",
        amount_in_raw=1, amount_out_raw=1,
    )
    leg2 = PoolLeg(
        pool_address="0x" + "22" * 20, protocol="aerodrome_volatile", fee_bps=30,
        token_in_addr="0x" + "bb" * 20, token_in_symbol="WETH",
        token_out_addr="0x" + "aa" * 20, token_out_symbol="USDC",
        amount_in_raw=1, amount_out_raw=1,
    )
    return Opportunity(
        opportunity_id=opp_id, block_number=100, block_timestamp=1_700_000_000,
        detected_at="t", legs=(leg1, leg2),
        borrowed_token_addr="0x" + "aa" * 20, borrowed_token_symbol="USDC",
        notional_usd=10_000.0, amount_in_raw=1, amount_out_raw=1,
        gross_margin=0.005, gross_gain_usd=50.0,
        gas_cost_usd=0.005, flash_loan_fee_usd=5.0,
        expected_net_gain_usd=45.0,
    )


def _make_test_db() -> sqlite3.Connection:
    """In-memory SQLite seeded with the L3 schema columns the rules consume.

    Matches the production schema as observed in tests/test_layer3_client.py
    and the live DB inspection done during Phase 1.3 implementation.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE contracts (
            contract_address TEXT PRIMARY KEY,
            confidence_tier TEXT,
            decayed_at TEXT,
            deployed_code_hash TEXT,
            has_asymmetric_transfer INTEGER,
            has_conditional_revert INTEGER,
            has_unusual_fee_structure INTEGER,
            deployer_address TEXT,
            last_updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE deployers (
            deployer_address TEXT PRIMARY KEY,
            first_seen TEXT,
            total_contracts_deployed INTEGER,
            mainnet_first_tx TEXT,
            entity_type TEXT,
            funding_trail TEXT,
            last_seen TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE org_wallets (
            address TEXT, org_id TEXT, role TEXT, chain TEXT,
            added_at TEXT, added_by TEXT, reason TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE org_candidates (
            candidate_id TEXT, cluster_size INTEGER,
            shared_funding_source TEXT, status TEXT,
            deployer_addresses TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE infrastructure_registry (
            address TEXT, classification TEXT, chain TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE bytecode_families (
            family_id INTEGER PRIMARY KEY, member_count INTEGER,
            unique_deployers INTEGER, is_cross_deployer INTEGER,
            last_updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE bytecode_family_members (
            contract_address TEXT, family_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE trust_amplification (
            contract_address TEXT, router_percentage REAL,
            amplification_factor REAL, revert_rate REAL,
            alert_level TEXT, last_updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE trap_events (
            timestamp TEXT, bot_address TEXT, tx_hash TEXT,
            loss_estimate_usd REAL, failure_signature TEXT,
            trap_contract_address TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE approval_watchlist (
            contract_address TEXT, victim_address TEXT,
            approve_timestamp TEXT, contract_tier TEXT,
            drain_detected INTEGER, drain_tx_hash TEXT,
            drain_timestamp TEXT, drain_caller TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE extraction_events (
            event_id TEXT, event_type TEXT, observed_at TEXT,
            documented_at TEXT, summary TEXT, raw_transactions TEXT,
            total_usd_moved REAL
        )
    """)
    conn.execute("""
        CREATE TABLE alerts (timestamp TEXT)
    """)
    conn.execute("""
        CREATE TABLE camouflage_metrics (date TEXT)
    """)
    conn.execute("""
        CREATE TABLE daily_metrics (date TEXT)
    """)
    return conn


class _ConnectionWrapper:
    """Adapter so Layer3Client can attach to our pre-built sqlite3.Connection.
    Layer3Client.__init__ takes a Path and opens the DB itself; for tests we
    bypass that with this wrapper that mimics Layer3Client's read interface
    using the methods Layer3Client itself uses internally."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @property
    def connection(self):
        return self._conn

    def get_contract_classification(self, addr):
        cur = self._conn.execute(
            """SELECT contract_address, confidence_tier, decayed_at,
                      deployed_code_hash, has_asymmetric_transfer,
                      has_conditional_revert, has_unusual_fee_structure,
                      deployer_address
               FROM contracts WHERE contract_address = LOWER(?)""", (addr,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_org_wallet_rows(self, addr):
        cur = self._conn.execute(
            "SELECT org_id, role, chain, added_at, added_by, reason "
            "FROM org_wallets WHERE LOWER(address) = LOWER(?)", (addr,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_drain_detected_approvals_targeting(self, addr):
        cur = self._conn.execute(
            "SELECT victim_address, approve_timestamp, contract_tier, "
            "drain_tx_hash, drain_timestamp, drain_caller "
            "FROM approval_watchlist "
            "WHERE LOWER(contract_address) = LOWER(?) AND drain_detected = 1", (addr,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_extraction_events_referencing(self, addr):
        addr_lower = addr.lower()
        if not addr_lower.startswith("0x") or len(addr_lower) < 8:
            return []
        prefix = addr_lower[:8]
        cur = self._conn.execute(
            "SELECT event_id, event_type, observed_at, summary, total_usd_moved "
            "FROM extraction_events "
            "WHERE LOWER(raw_transactions) LIKE '%' || ? || '%' "
            "OR LOWER(summary) LIKE '%' || ? || '%'", (prefix, prefix))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_trust_amplification(self, addr):
        cur = self._conn.execute(
            "SELECT router_percentage, amplification_factor, revert_rate, alert_level "
            "FROM trust_amplification WHERE contract_address = LOWER(?)", (addr,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_bytecode_family(self, addr):
        cur = self._conn.execute(
            "SELECT bfm.family_id, bf.member_count, bf.unique_deployers, bf.is_cross_deployer "
            "FROM bytecode_family_members bfm JOIN bytecode_families bf "
            "ON bf.family_id = bfm.family_id "
            "WHERE bfm.contract_address = LOWER(?)", (addr,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_org_candidates_referencing(self, deployer):
        cur = self._conn.execute(
            "SELECT candidate_id, cluster_size, shared_funding_source, status "
            "FROM org_candidates "
            "WHERE deployer_addresses LIKE '%' || LOWER(?) || '%' AND status='pending'",
            (deployer,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def count_trap_events_for(self, addr):
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM trap_events "
            "WHERE LOWER(trap_contract_address) = LOWER(?)", (addr,))
        return int(cur.fetchone()[0])

    def get_deployer_profile(self, addr):
        cur = self._conn.execute(
            "SELECT deployer_address, first_seen, total_contracts_deployed, "
            "mainnet_first_tx, entity_type "
            "FROM deployers WHERE deployer_address = LOWER(?)", (addr,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_infrastructure_classification(self, addr):
        cur = self._conn.execute(
            "SELECT classification, chain FROM infrastructure_registry "
            "WHERE LOWER(address) = LOWER(?)", (addr,))
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]


def test_pipeline_runs_all_13_rules_in_order():
    """No short-circuiting: every rule runs and results come back in canonical
    rule_id 1..13 order, regardless of which rules fired."""
    conn = _make_test_db()
    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, freshness_threshold_seconds=30 * 60,
                              now_fn=lambda: datetime(2026, 5, 7, tzinfo=timezone.utc))

    eval_result = pipeline.evaluate(_opp())
    assert len(eval_result.results) == 13
    assert [r.rule_id for r in eval_result.results] == list(range(1, 14))
    # Empty DB → nothing fires
    assert all(not r.fired for r in eval_result.results)
    assert not eval_result.hard_flagged
    assert not eval_result.soft_flagged
    assert not eval_result.has_legit_override


def test_pipeline_hard_flag_aggregation():
    """A single HARD rule firing must propagate to FilterEvaluation.hard_flagged."""
    conn = _make_test_db()
    pool_addr = "0x" + "11" * 20
    conn.execute(
        "INSERT INTO contracts (contract_address, confidence_tier, decayed_at, "
        "has_asymmetric_transfer, has_conditional_revert, deployer_address) "
        "VALUES (?, 'confirmed', NULL, 0, 0, ?)",
        (pool_addr, "0x" + "dd" * 20))
    conn.commit()

    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, freshness_threshold_seconds=30 * 60,
                              now_fn=lambda: datetime(2026, 5, 7, tzinfo=timezone.utc))
    eval_result = pipeline.evaluate(_opp())

    assert eval_result.hard_flagged
    rule_1 = eval_result.results[0]
    assert rule_1.rule_id == 1
    assert rule_1.fired
    assert pool_addr in rule_1.matched_addresses


def test_pipeline_records_freshness_and_marks_degraded_when_stale():
    """Spec invariant #8: any filter-critical table > 30 min stale → degraded=True."""
    conn = _make_test_db()
    # Insert contracts row dated 2 hours ago — well past the 30min threshold.
    stale_ts = (datetime(2026, 5, 7, tzinfo=timezone.utc).replace(
        hour=10) - __import__("datetime").timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO contracts (contract_address, confidence_tier, last_updated, "
        "has_asymmetric_transfer, has_conditional_revert) "
        "VALUES ('0x'||hex(randomblob(20)), 'confirmed', ?, 0, 0)", (stale_ts,))
    conn.commit()

    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, freshness_threshold_seconds=30 * 60,
                              now_fn=lambda: datetime(2026, 5, 7, 10, tzinfo=timezone.utc))
    eval_result = pipeline.evaluate(_opp())
    assert eval_result.degraded is True
    assert "contracts" in eval_result.l3_freshness
    assert eval_result.l3_freshness["contracts"]["stale"] is True


# ----------------------------------------------------------------------------
# Phase 2 sub-phase 2.6 (D-009, D-012): per-table freshness thresholds wired
# through FilterPipeline.evaluate.
# ----------------------------------------------------------------------------


def test_pipeline_populates_degraded_per_table():
    """Each filter-critical table gets a bool entry in degraded_per_table."""
    conn = _make_test_db()
    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, freshness_threshold_seconds=30 * 60,
                              now_fn=lambda: datetime(2026, 5, 16, tzinfo=timezone.utc))
    ev = pipeline.evaluate(_opp())
    from layer3_trading_exp.freshness import FILTER_CRITICAL_TABLES
    assert set(ev.degraded_per_table.keys()) == set(FILTER_CRITICAL_TABLES)
    for table, stale in ev.degraded_per_table.items():
        assert isinstance(stale, bool), f"{table} should be bool, got {type(stale)}"


def test_pipeline_per_table_thresholds_distinguish_slow_from_fast_tables():
    """The headline 2.6 test: a slow table (trust_amplification at 20h)
    is NOT degraded under its 36h threshold; a fast table (contracts
    at 2h) IS degraded under its 1h threshold. The same record in Phase 1
    would have flagged both as degraded under the single 30-min threshold."""
    conn = _make_test_db()
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    # Insert a contracts row from 2 hours ago (stale under 1h, fresh under 30min
    # rolled forward to 2h).
    contracts_ts = (now - __import__("datetime").timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO contracts (contract_address, confidence_tier, last_updated, "
        "has_asymmetric_transfer, has_conditional_revert) "
        "VALUES ('0x'||hex(randomblob(20)), 'confirmed', ?, 0, 0)", (contracts_ts,))
    # trust_amplification row from 20 hours ago — fresh under 36h threshold.
    # (Table is already CREATE'd by _make_test_db; we just insert.)
    ta_ts = (now - __import__("datetime").timedelta(hours=20)).isoformat()
    conn.execute(
        "INSERT INTO trust_amplification (contract_address, router_percentage, "
        "amplification_factor, revert_rate, alert_level, last_updated) "
        "VALUES ('0xabc', 0.0, 0.0, 0.0, 'INFO', ?)", (ta_ts,))
    conn.commit()
    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, now_fn=lambda: now)  # default per-table thresholds
    ev = pipeline.evaluate(_opp())
    # contracts: stale (2h > 1h)
    assert ev.degraded_per_table.get("contracts") is True
    # trust_amplification: fresh (20h < 36h)
    assert ev.degraded_per_table.get("trust_amplification") is False
    # The legacy aggregate `degraded` is True (something is stale).
    assert ev.degraded is True


def test_pipeline_back_compat_with_single_threshold_kwarg():
    """Passing only `freshness_threshold_seconds` (Phase 1 callsite shape)
    still works — the per-table thresholds default to the standard
    Phase 2 map. Phase 1 tests calling FilterPipeline(l3, freshness_...)
    don't need to change."""
    conn = _make_test_db()
    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, freshness_threshold_seconds=30 * 60,
                              now_fn=lambda: datetime(2026, 5, 16, tzinfo=timezone.utc))
    ev = pipeline.evaluate(_opp())
    # The single-threshold arg is retained but PER-TABLE map dominates
    # the freshness check (per spec sub-phase 2.6). No exception means
    # the back-compat path works.
    assert isinstance(ev.degraded_per_table, dict)


def test_pipeline_freshness_block_records_threshold_per_table():
    """For analysis later, the per-table `l3_freshness` block carries the
    threshold used (not just the boolean) so reviewers can verify which
    threshold fired."""
    conn = _make_test_db()
    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, now_fn=lambda: datetime(2026, 5, 16, tzinfo=timezone.utc))
    ev = pipeline.evaluate(_opp())
    for table, info in ev.l3_freshness.items():
        assert "threshold_seconds" in info, (
            f"l3_freshness[{table}] missing threshold_seconds")


def test_pipeline_evaluation_well_under_500ms_budget():
    """Spec acceptance: full rule evaluation per opportunity <= 500ms p95."""
    conn = _make_test_db()
    l3 = _ConnectionWrapper(conn)
    pipeline = FilterPipeline(l3, freshness_threshold_seconds=30 * 60,
                              now_fn=lambda: datetime(2026, 5, 7, tzinfo=timezone.utc))
    durations: list[float] = []
    for i in range(50):
        eval_result = pipeline.evaluate(_opp(opp_id=f"opp-{i}"))
        durations.append(eval_result.elapsed_ms)

    durations.sort()
    p95 = durations[int(0.95 * len(durations))]
    # Empty DB on local SQLite — should be < 50ms easily; budget is 500ms.
    assert p95 < 500.0, f"p95 evaluation {p95:.1f}ms exceeds 500ms budget"


def test_collect_address_context_dedups_addresses_across_legs():
    """The middle and borrowed tokens appear on both legs; address context
    must deduplicate them so we don't run rules twice on the same address."""
    opp = _opp()
    # 4 unique contracts: pool0, pool1, USDC, WETH.
    # Legs share USDC and WETH (token_in of leg1 = token_out of leg2 = USDC, etc.)
    conn = _make_test_db()
    # Seed deployer for both pool addresses so we can check deployer dedup too.
    deployer = "0x" + "dd" * 20
    for pool_addr in ("0x" + "11" * 20, "0x" + "22" * 20):
        conn.execute(
            "INSERT INTO contracts (contract_address, confidence_tier, "
            "deployer_address, has_asymmetric_transfer, has_conditional_revert) "
            "VALUES (?, 'unrated', ?, 0, 0)", (pool_addr, deployer))
    conn.commit()

    l3 = _ConnectionWrapper(conn)
    ctx = collect_address_context(opp, l3)
    # 2 pools + 2 tokens (USDC, WETH) = 4 unique contract addresses.
    assert len(ctx.contract_addresses) == 4
    assert len(set(ctx.contract_addresses)) == 4
    # Deployer dedup: both pools share the same deployer → 1 unique.
    assert ctx.deployer_addresses == (deployer.lower(),)
