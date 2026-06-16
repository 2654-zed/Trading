"""Process-shared multiple-testing ledger (the credibility artifact).

Every hypothesis ever run to the hard gate — passed, failed, AND each
revision (a peek is a peek) — gets a row. The significance bar tightens with
N, so the 30th look-alike faces a stricter threshold than the 1st. This is
what lets an outside party believe "we found nothing" or "we found this one
thing." Kept in SQLite (not graph state) so concurrent hypothesis threads
all increment ONE shared count.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class Ledger:
    def __init__(self, db_path: str = "engine/data/research_loop/ledger.db"):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(p), isolation_level=None)  # autocommit
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id TEXT, family_tag TEXT, segment TEXT,
                p_value REAL, effect REAL, edge_sign TEXT,
                passed_hard_gate INTEGER, ts TEXT)
        """)

    def record(self, hypothesis_id: str, family_tag: str, segment: str,
               ts: str, p_value: Optional[float] = None,
               effect: Optional[float] = None, edge_sign: Optional[str] = None,
               passed_hard_gate: Optional[bool] = None) -> int:
        """Record one trip to the gate; returns the new running count N."""
        self._db.execute(
            "INSERT INTO hypotheses(hypothesis_id,family_tag,segment,p_value,"
            "effect,edge_sign,passed_hard_gate,ts) VALUES(?,?,?,?,?,?,?,?)",
            (hypothesis_id, family_tag, segment, p_value, effect, edge_sign,
             None if passed_hard_gate is None else int(passed_hard_gate), ts))
        return self.count()

    def count(self, family_tag: Optional[str] = None) -> int:
        if family_tag is None:
            row = self._db.execute("SELECT count(*) FROM hypotheses").fetchone()
        else:
            row = self._db.execute(
                "SELECT count(*) FROM hypotheses WHERE family_tag=?",
                (family_tag,)).fetchone()
        return int(row[0])

    def bonferroni_threshold(self, alpha: float, n: int) -> float:
        """The single-test p-value a hypothesis must beat after correcting for
        N total tests. Conservative (Bonferroni); BH-FDR is less strict but
        needs the full p-vector — Bonferroni is the honest floor for a live,
        one-at-a-time loop."""
        return alpha / max(1, n)

    def passes_correction(self, p_value: Optional[float], n: int,
                          alpha: float = 0.05) -> bool:
        if p_value is None:
            return False
        return p_value <= self.bonferroni_threshold(alpha, n)

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass


__all__ = ["Ledger"]
