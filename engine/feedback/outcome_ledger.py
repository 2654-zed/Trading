"""Outcome ledger SCHEMA (sub-phase 3.3 — design only, no live writes).

Per the spec, sub-phase 3.3 *designs + reviews* the outcome-ledger schema;
the ledger only becomes ACTIVE (written) in sub-phase 3.4 when the
execution layer produces decisions whose outcomes can be attributed, and
fully drives weight updates in sub-phase 3.5 (the closed feedback loop,
invariants I-19 + I-20).

What the ledger must capture (so 3.4/3.5 can close the loop):

  1. decisions   — each orchestrator WindowAggregate (the adjudicated
                   output) with its regime + score + contributing signals.
  2. conflicts   — every ConflictSignal, keyed by
                   (timestamp, lenses_involved, contradicting_types) per
                   I-18 ("conflicts must NOT be silently resolved … every
                   conflict gets a row in the outcome ledger").
  3. composites  — every CompositeSignal (multi-lens convergence events).
  4. outcomes    — the realized result attributed back to a decision
                   (populated in 3.4+; the feedback signal for I-19/I-20).

Storage choice: a dedicated SQLite file (separate from the read-only L3
copy — we never write to L3 per I-3). The DDL is defined here but NOT
executed against any live DB in 3.3. `create_schema(conn)` is provided
for 3.4 to call.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


# ---- DDL (reviewed at sub-phase 3.3; executed starting 3.4) -------------

SCHEMA_DDL = """
-- One row per adjudicated orchestrator window (the "decision").
CREATE TABLE IF NOT EXISTS decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    window_start        REAL NOT NULL,
    window_end          REAL NOT NULL,
    regime_label        TEXT NOT NULL,
    regime_confidence   REAL NOT NULL,
    base_score          REAL NOT NULL,
    weighted_aggregate  REAL NOT NULL,
    conflict_count      INTEGER NOT NULL DEFAULT 0,
    conflict_boost      REAL NOT NULL DEFAULT 0.0,
    weights_json        TEXT NOT NULL,       -- the regime weight table used
    contributing_ids    TEXT NOT NULL,       -- JSON list of lens names
    per_lens_strength   TEXT NOT NULL DEFAULT '{}',  -- JSON {lens: strength}
    entities            TEXT NOT NULL DEFAULT '[]',  -- JSON list of addresses
    created_at          REAL NOT NULL        -- wall-clock insert time
);
CREATE INDEX IF NOT EXISTS idx_decisions_window
    ON decisions(window_start);

-- One row per ConflictSignal. Keyed per I-18.
CREATE TABLE IF NOT EXISTS conflicts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    window_start         REAL NOT NULL,
    window_end           REAL NOT NULL,
    conflict_type        TEXT NOT NULL,
    lenses_involved      TEXT NOT NULL,      -- JSON sorted list
    contradicting_types  TEXT NOT NULL,      -- JSON sorted list
    shared_entity        TEXT,
    strength             REAL NOT NULL,
    component_ids        TEXT NOT NULL,      -- JSON list of signal ids
    created_at           REAL NOT NULL
);
-- I-18 key: (timestamp, lenses_involved, contradicting_types).
CREATE INDEX IF NOT EXISTS idx_conflicts_key
    ON conflicts(window_start, lenses_involved, contradicting_types);

-- One row per CompositeSignal (multi-lens convergence).
CREATE TABLE IF NOT EXISTS composites (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    window_start        REAL NOT NULL,
    window_end          REAL NOT NULL,
    lenses_involved     TEXT NOT NULL,
    signal_types        TEXT NOT NULL,
    shared_entity       TEXT,
    convergence_score   REAL NOT NULL,
    component_ids       TEXT NOT NULL,
    component_count     INTEGER NOT NULL,
    created_at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_composites_window
    ON composites(window_start);

-- Realized outcomes attributed back to a decision. Populated in 3.4+.
-- This is the feedback signal that closes the loop (I-19 / I-20).
CREATE TABLE IF NOT EXISTS outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER NOT NULL REFERENCES decisions(id),
    outcome_type    TEXT NOT NULL,    -- e.g. 'price_move', 'exploit_confirmed'
    outcome_value   REAL,             -- signed magnitude where applicable
    ground_truth    TEXT,             -- free-form attestation / label
    realized_at     REAL NOT NULL,    -- when the outcome became known
    lag_seconds     REAL              -- realized_at - decision.window_end
);
CREATE INDEX IF NOT EXISTS idx_outcomes_decision
    ON outcomes(decision_id);

-- Per-lens running performance, updated by the 3.5 feedback loop. The
-- weight-update process (I-19) reads/writes this to learn which lenses
-- earn their weight under which regime.
CREATE TABLE IF NOT EXISTS lens_performance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lens                TEXT NOT NULL,
    regime_label        TEXT NOT NULL,
    n_decisions         INTEGER NOT NULL DEFAULT 0,
    n_correct           INTEGER NOT NULL DEFAULT 0,
    cumulative_outcome  REAL NOT NULL DEFAULT 0.0,
    updated_at          REAL NOT NULL,
    UNIQUE(lens, regime_label)
);
"""


# ---- Typed row mirrors (for 3.4 writers) --------------------------------

@dataclass(frozen=True)
class DecisionRow:
    window_start: float
    window_end: float
    regime_label: str
    regime_confidence: float
    base_score: float
    weighted_aggregate: float
    conflict_count: int
    conflict_boost: float
    weights_json: str
    contributing_ids: str
    created_at: float


@dataclass(frozen=True)
class ConflictRow:
    window_start: float
    window_end: float
    conflict_type: str
    lenses_involved: str
    contradicting_types: str
    shared_entity: Optional[str]
    strength: float
    component_ids: str
    created_at: float


@dataclass(frozen=True)
class OutcomeRow:
    decision_id: int
    outcome_type: str
    outcome_value: Optional[float]
    ground_truth: Optional[str]
    realized_at: float
    lag_seconds: Optional[float]


def create_schema(conn: sqlite3.Connection) -> None:
    """Execute the DDL against a writable SQLite connection.

    Activated in sub-phase 3.4 (via LedgerWriter) against a dedicated
    ledger DB — NEVER the read-only L3 copy (I-3).
    """
    conn.executescript(SCHEMA_DDL)
    conn.commit()


class LedgerWriter:
    """Live writer for the outcome ledger (sub-phase 3.4).

    Opens (or creates) a dedicated SQLite file separate from the L3 copy,
    creates the schema, and provides typed insert helpers. Per I-3 this is
    a SEPARATE writable DB; the L3 corpus is never written.
    """

    def __init__(self, db_path):
        import json as _json
        from pathlib import Path
        self._json = _json
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        create_schema(self._conn)

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    # --- inserts -----------------------------------------------------

    def write_decision(self, decision, created_at: float) -> int:
        cur = self._conn.execute(
            "INSERT INTO decisions (window_start, window_end, regime_label, "
            "regime_confidence, base_score, weighted_aggregate, "
            "conflict_count, conflict_boost, weights_json, contributing_ids, "
            "per_lens_strength, entities, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision.window_start, decision.window_end,
                decision.regime_label,
                decision.rationale.get("regime_confidence", 0.0),
                decision.rationale.get("base_score", 0.0),
                decision.aggregate_score,
                decision.rationale.get("conflict_count", 0),
                decision.rationale.get("conflict_boost", 0.0),
                self._json.dumps(decision.rationale.get("weights_used", {})),
                self._json.dumps(
                    [tl.get("lens") for tl in decision.rationale.get("top_lenses", [])]
                ),
                self._json.dumps(decision.rationale.get("per_lens_strength", {})),
                self._json.dumps(decision.rationale.get("entities", [])),
                created_at,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def write_conflict(self, conflict, created_at: float) -> int:
        cur = self._conn.execute(
            "INSERT INTO conflicts (window_start, window_end, conflict_type, "
            "lenses_involved, contradicting_types, shared_entity, strength, "
            "component_ids, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                conflict.window_start, conflict.window_end,
                conflict.conflict_type,
                self._json.dumps(sorted(conflict.lenses_involved)),
                self._json.dumps(sorted(conflict.contradicting_types)),
                conflict.shared_entity, conflict.strength,
                self._json.dumps(conflict.component_signal_ids),
                created_at,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def write_outcome(self, decision_id: int, outcome_type: str,
                      outcome_value, ground_truth, realized_at: float,
                      lag_seconds) -> int:
        cur = self._conn.execute(
            "INSERT INTO outcomes (decision_id, outcome_type, outcome_value, "
            "ground_truth, realized_at, lag_seconds) VALUES (?,?,?,?,?,?)",
            (decision_id, outcome_type, outcome_value, ground_truth,
             realized_at, lag_seconds),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # --- queries -----------------------------------------------------

    def all_decisions(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, window_start, window_end, regime_label, "
            "weighted_aggregate, conflict_count, entities FROM decisions "
            "ORDER BY window_start"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def count(self, table: str) -> int:
        if table not in LEDGER_TABLES:
            raise ValueError(f"unknown table {table}")
        return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def outcomes_joined(self) -> list[dict]:
        """Outcomes joined to their decisions — for the learner (3.4b)."""
        cur = self._conn.execute(
            "SELECT d.window_start, d.window_end, d.regime_label, "
            "d.weighted_aggregate, d.conflict_count, d.weights_json, "
            "d.per_lens_strength, o.outcome_value, o.ground_truth "
            "FROM outcomes o JOIN decisions d ON o.decision_id = d.id "
            "ORDER BY d.window_start"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# Tables a reviewer / 3.4 implementer can enumerate.
LEDGER_TABLES = (
    "decisions", "conflicts", "composites", "outcomes", "lens_performance",
)


__all__ = [
    "SCHEMA_DDL", "create_schema", "LEDGER_TABLES", "LedgerWriter",
    "DecisionRow", "ConflictRow", "OutcomeRow",
]
