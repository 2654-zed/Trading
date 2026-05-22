"""Read-only wrapper around the local Layer 3 SQLite copy.

Each public method corresponds to one query pattern documented in
layer3_consumable_intelligence.md sections 1.1 through 1.10. Methods return plain
dicts or lists of dicts; callers are responsible for interpretation and tier-aware
filtering.

Invariants:
- Read-only connection (sqlite3 URI mode=ro) — spec invariant #5.
- No schema writes, no ALTER TABLE, no derived tables materialized here.
- Missing rows return None (single-row lookups) or empty list (multi-row lookups).
  A connection or schema error raises sqlite3.Error rather than being swallowed —
  spec invariant #10 (loud logging, no silent failures).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


def _row_to_dict(cur: sqlite3.Cursor, row: Optional[tuple]) -> Optional[dict]:
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _rows_to_dicts(cur: sqlite3.Cursor, rows: list[tuple]) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


class Layer3Client:
    """Thin query interface over the local Layer 3 consumable surface."""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        if not self._db_path.exists():
            raise FileNotFoundError(f"Layer 3 SQLite copy not found at {self._db_path}")
        uri = f"file:{self._db_path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = None

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Layer3Client":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # §1.1 — contract-level classification
    def get_contract_classification(self, address: str) -> Optional[dict]:
        cur = self._conn.execute(
            """
            SELECT contract_address, confidence_tier, decayed_at, deployed_code_hash,
                   has_asymmetric_transfer, has_conditional_revert,
                   has_unusual_fee_structure, deployer_address
            FROM contracts
            WHERE contract_address = LOWER(?)
            """,
            (address,),
        )
        return _row_to_dict(cur, cur.fetchone())

    # §1.2 — deployer-level classification
    def get_deployer_profile(self, address: str) -> Optional[dict]:
        cur = self._conn.execute(
            """
            SELECT deployer_address, first_seen, total_contracts_deployed,
                   mainnet_first_tx, entity_type,
                   json_extract(funding_trail, '$.funder') AS funder
            FROM deployers
            WHERE deployer_address = LOWER(?)
            """,
            (address,),
        )
        return _row_to_dict(cur, cur.fetchone())

    # §1.3 — observed harm events
    def get_trap_events_for(self, address: str) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT timestamp, bot_address, tx_hash, loss_estimate_usd, failure_signature
            FROM trap_events
            WHERE LOWER(trap_contract_address) = LOWER(?)
            ORDER BY timestamp DESC
            """,
            (address,),
        )
        return _rows_to_dicts(cur, cur.fetchall())

    def count_trap_events_for(self, address: str) -> int:
        cur = self._conn.execute(
            """
            SELECT COUNT(*) FROM trap_events
            WHERE LOWER(trap_contract_address) = LOWER(?)
            """,
            (address,),
        )
        return int(cur.fetchone()[0])

    # §1.6 — organizational classification
    def is_in_org_wallets(self, address: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM org_wallets WHERE LOWER(address) = LOWER(?) LIMIT 1",
            (address,),
        )
        return cur.fetchone() is not None

    def get_org_wallet_rows(self, address: str) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT org_id, role, chain, added_at, added_by, reason
            FROM org_wallets
            WHERE LOWER(address) = LOWER(?)
            """,
            (address,),
        )
        return _rows_to_dicts(cur, cur.fetchall())

    def get_org_candidates_referencing(self, deployer_address: str) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT candidate_id, cluster_size, shared_funding_source, status
            FROM org_candidates
            WHERE deployer_addresses LIKE '%' || LOWER(?) || '%'
              AND status = 'pending'
            """,
            (deployer_address,),
        )
        return _rows_to_dicts(cur, cur.fetchall())

    # §1.7 — infrastructure registry
    def get_infrastructure_classification(self, address: str) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT classification, chain
            FROM infrastructure_registry
            WHERE LOWER(address) = LOWER(?)
            """,
            (address,),
        )
        return _rows_to_dicts(cur, cur.fetchall())

    # §1.8 — bytecode-family membership
    def get_bytecode_family(self, contract_address: str) -> Optional[dict]:
        cur = self._conn.execute(
            """
            SELECT bfm.family_id, bf.member_count, bf.unique_deployers,
                   bf.is_cross_deployer
            FROM bytecode_family_members bfm
            JOIN bytecode_families bf ON bf.family_id = bfm.family_id
            WHERE bfm.contract_address = LOWER(?)
            """,
            (contract_address,),
        )
        return _row_to_dict(cur, cur.fetchone())

    # §1.9 — trust amplification
    def get_trust_amplification(self, address: str) -> Optional[dict]:
        cur = self._conn.execute(
            """
            SELECT router_percentage, amplification_factor, revert_rate, alert_level
            FROM trust_amplification
            WHERE contract_address = LOWER(?)
            """,
            (address,),
        )
        return _row_to_dict(cur, cur.fetchone())

    # §1.10 — approval exposure
    # The doc's query pattern filters by victim_address. Rule 3 of the filter
    # pipeline needs the inverse: "does this spender have any drain-detected row?"
    def get_drain_detected_approvals_targeting(self, contract_address: str) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT victim_address, approve_timestamp, contract_tier,
                   drain_tx_hash, drain_timestamp, drain_caller
            FROM approval_watchlist
            WHERE LOWER(contract_address) = LOWER(?)
              AND drain_detected = 1
            """,
            (contract_address,),
        )
        return _rows_to_dicts(cur, cur.fetchall())

    # Phase 1.3 Rule 4 — extraction_events involvement
    def get_extraction_events_referencing(self, address: str) -> list[dict]:
        """Return extraction_events rows that reference the given contract
        address in their raw_transactions JSON or summary text.

        Schema caveat: L3's extraction_events stores abbreviated address
        prefixes (e.g. '0x51c728' — '0x' + 6 hex chars) inside the
        raw_transactions JSON blob. Full-address equality matching is therefore
        impossible against the current schema. We match on the 8-char prefix
        as a substring and accept the false-positive risk: any two addresses
        sharing the first 6 hex chars will collide. The collision probability
        for a single comparison is ~1/16M; for a typical opportunity (5-8
        addresses checked against ~7 events) the expected false-positive rate
        is well under 0.001%. Acceptable for Phase 1 detection; Phase 2 should
        revisit if L3's schema gains full-address columns.
        """
        addr_lower = address.lower()
        if not addr_lower.startswith("0x") or len(addr_lower) < 8:
            return []
        prefix = addr_lower[:8]  # '0x' + 6 hex chars
        cur = self._conn.execute(
            """
            SELECT event_id, event_type, observed_at, summary, total_usd_moved
            FROM extraction_events
            WHERE LOWER(raw_transactions) LIKE '%' || ? || '%'
               OR LOWER(summary) LIKE '%' || ? || '%'
            """,
            (prefix, prefix),
        )
        return _rows_to_dicts(cur, cur.fetchall())
