"""Ledger admin (sub-phase 3.5): query / dump / repair operations over a
persisted outcome ledger.

Operational tooling so the feedback loop is maintainable, not a black box:
  - dump():            summary counts + recent rows
  - decisions_by_regime(): regime → count
  - find_missing_outcomes(): decisions with no attributed outcome
  - repair_missing_outcomes(): attribute outcomes for any decision lacking
                               one (idempotent backfill)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class LedgerAdmin:
    def __init__(self, db_path):
        self._path = Path(db_path)
        if not self._path.exists():
            raise FileNotFoundError(f"ledger not found: {self._path}")
        self._conn = sqlite3.connect(str(self._path))

    def close(self) -> None:
        self._conn.close()

    def counts(self) -> dict:
        out = {}
        for t in ("decisions", "conflicts", "composites", "outcomes",
                  "lens_performance"):
            try:
                out[t] = self._conn.execute(
                    f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                out[t] = None
        return out

    def decisions_by_regime(self) -> dict:
        cur = self._conn.execute(
            "SELECT regime_label, COUNT(*) FROM decisions GROUP BY regime_label")
        return {r[0]: r[1] for r in cur.fetchall()}

    def find_missing_outcomes(self) -> list[int]:
        cur = self._conn.execute(
            "SELECT d.id FROM decisions d "
            "LEFT JOIN outcomes o ON o.decision_id = d.id "
            "WHERE o.id IS NULL")
        return [r[0] for r in cur.fetchall()]

    def repair_missing_outcomes(self, attributor, *, writer) -> int:
        """Backfill outcomes for any decision lacking one. `attributor`
        computes the proxy; `writer` is a LedgerWriter on the same DB."""
        missing = self.find_missing_outcomes()
        n = 0
        for did in missing:
            row = self._conn.execute(
                "SELECT window_end FROM decisions WHERE id=?", (did,)
            ).fetchone()
            if not row:
                continue
            value, gt, _ = attributor.compute_outcome(row[0])
            import time as _t
            writer.write_outcome(did, "forward_activity_proxy", value, gt,
                                 realized_at=_t.time(),
                                 lag_seconds=attributor._h)
            n += 1
        return n

    def dump(self, *, recent: int = 5) -> dict:
        d = {"path": str(self._path), "counts": self.counts(),
             "by_regime": self.decisions_by_regime(),
             "missing_outcomes": len(self.find_missing_outcomes())}
        cur = self._conn.execute(
            "SELECT window_start, regime_label, weighted_aggregate "
            "FROM decisions ORDER BY window_start DESC LIMIT ?", (recent,))
        d["recent_decisions"] = [
            {"window_start": r[0], "regime": r[1], "score": r[2]}
            for r in cur.fetchall()
        ]
        return d


__all__ = ["LedgerAdmin"]
