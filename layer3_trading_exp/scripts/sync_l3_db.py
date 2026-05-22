"""Phase 1.6 — incremental Layer 3 DB sync over Railway private network.

Pulls rows from L3's `/dump?table=X&since_ts=&cursor_col=&offset=&limit=` endpoint
and upserts into a local SQLite copy that `Layer3Client` reads from. Tracks
per-table sync state (last seen timestamp or id) so each sync only pulls deltas.

Used two ways:
  - One-shot bootstrap: `python -m layer3_trading_exp.scripts.sync_l3_db --once`
    Useful on first deploy to seed the local DB before the detector starts.
  - Background loop: `await L3SyncRunner(...).run_forever()` — wired into
    `detect_dry_run.py` as a long-lived asyncio task.

Schema strategy: SQLite is dynamically typed. We CREATE TABLE with TEXT columns
matching the row dict keys returned by /dump (probed via `limit=1` on first sync).
Phase 1.3 filter rules query by column name with TEXT-compatible WHERE clauses,
so type erasure is fine in practice.

Robustness:
  - HTTP 5xx / network errors → retry with backoff, then skip this cycle (next
    interval will catch up).
  - HTTP 400 on cursor mismatch (cursor_col doesn't exist on this table) → fall
    back to full-sync mode for that table, log loudly.
  - HTTP 403 (token rejected) → loud failure, raise — the deployment is broken.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

# Force UTF-8 stdout for log lines that may contain Unicode.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..config import DEFAULT, Config


@dataclass(frozen=True)
class TableSync:
    """Per-table sync configuration.

    - name: L3 table name (must be in L3's /dump valid-tables list)
    - cursor_col: column name to use as incremental cursor. None = full-sync.
    - cursor_is_timestamp: True → /dump's since_ts param; False → since_id.
    """
    name: str
    cursor_col: Optional[str]
    cursor_is_timestamp: bool = True


# Index specifications per table — created at schema bootstrap.
# Mirror the Layer3Client query WHERE clauses verbatim:
#   - bare column reference → plain index on that column
#   - LOWER(column) wrapper → functional index on LOWER(column)
# Functional indexes are required because SQLite can't use a plain index when
# the query wraps the column in LOWER() (both sides must match).
_INDEX_SPECS_BY_TABLE: dict[str, tuple[tuple[str, str], ...]] = {
    "contracts": (
        ("idx_contracts_contract_address", "[contract_address]"),
        ("idx_contracts_confidence_tier", "[confidence_tier]"),
    ),
    "deployers": (
        ("idx_deployers_deployer_address", "[deployer_address]"),
    ),
    "trust_amplification": (
        ("idx_trust_amp_contract", "[contract_address]"),
    ),
    "bytecode_family_members": (
        ("idx_bfm_contract_address", "[contract_address]"),
    ),
    "approval_watchlist": (
        ("idx_approval_drain", "[drain_detected]"),
        ("idx_approval_contract_lower", "LOWER([contract_address])"),
    ),
    "trap_events": (
        ("idx_trap_contract_lower", "LOWER([trap_contract_address])"),
    ),
    "org_wallets": (
        ("idx_org_wallets_addr_lower", "LOWER([address])"),
    ),
    "infrastructure_registry": (
        ("idx_infra_addr_lower", "LOWER([address])"),
    ),
}


# Tables the Phase 1.3 filter pipeline reads, with their sync strategies.
# The 5 filter-critical tables (per `freshness.py`) use timestamp cursors.
# The rest are smaller or schemaless-by-our-needs and get full-sync each cycle.
SYNC_TABLES: tuple[TableSync, ...] = (
    TableSync("contracts",            "last_updated"),
    TableSync("deployers",            "last_seen"),
    TableSync("trap_events",          "timestamp"),
    TableSync("bytecode_families",    "last_updated"),
    TableSync("trust_amplification",  "last_updated"),
    TableSync("bytecode_family_members", None),
    TableSync("approval_watchlist",   None),
    TableSync("extraction_events",    None),
    TableSync("org_wallets",          None),
    TableSync("org_candidates",       None),
    TableSync("infrastructure_registry", None),
)


_DUMP_PAGE_LIMIT = 5_000
_HTTP_TIMEOUT = 60
_MAX_RETRIES_PER_TABLE = 3


class L3SyncRunner:
    """Pulls L3 tables to a local SQLite copy. Stateful (tracks per-table cursors)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        local_db_path: Path,
        tables: tuple[TableSync, ...] = SYNC_TABLES,
        page_limit: int = _DUMP_PAGE_LIMIT,
    ) -> None:
        if not base_url:
            raise ValueError("base_url required (set L3_DUMP_BASE_URL)")
        if not token:
            raise ValueError("token required (set LAYER3_ADMIN_TOKEN)")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._db_path = local_db_path
        self._tables = tables
        self._page_limit = page_limit

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because run_forever() dispatches sync_once()
        # to a worker via asyncio.to_thread. We serialize all access via the
        # asyncio task (one cycle at a time, never concurrent), so the SQLite
        # wrapper's thread-affinity check is safe to disable.
        # WAL mode lets the read-only Layer3Client connection in the main process
        # see consistent snapshots without blocking sync writes.
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS _sync_meta ("
            "table_name TEXT PRIMARY KEY, "
            "last_cursor TEXT, "
            "last_sync_at TEXT, "
            "rows_seen INTEGER DEFAULT 0)"
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    @classmethod
    def from_config(cls, cfg: Config = DEFAULT) -> "L3SyncRunner":
        """Construct from env-backed Config. Raises if URL/token unset."""
        return cls(
            base_url=cfg.l3_dump_base_url,
            token=cfg.layer3_admin_token,
            local_db_path=cfg.l3_db_path,
        )

    # ---- public ----

    async def run_forever(self, interval_seconds: int) -> None:
        """Sync every `interval_seconds` until cancelled. Surfaces a one-line
        summary per cycle to stdout. Tolerates transient errors per table."""
        while True:
            t0 = time.perf_counter()
            try:
                summary = await asyncio.to_thread(self.sync_once)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[l3_sync] cycle FAILED: {type(e).__name__}: {e}", flush=True)
                summary = {}
            elapsed = time.perf_counter() - t0
            total_rows = sum(summary.get(t.name, {}).get("rows", 0) for t in self._tables)
            errors = [n for n, info in summary.items() if info.get("error")]
            print(
                f"[l3_sync] cycle done in {elapsed:.1f}s; +{total_rows:,} rows; "
                f"errors={len(errors)}{(' (' + ','.join(errors) + ')') if errors else ''}",
                flush=True,
            )
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                raise

    def sync_once(self) -> dict[str, dict]:
        """Sync all tables once. Returns per-table summary dict."""
        results: dict[str, dict] = {}
        for ts in self._tables:
            try:
                rows = self._sync_table(ts)
                results[ts.name] = {"rows": rows, "error": None}
            except Exception as e:
                # Don't kill the whole cycle on one table failure.
                print(f"[l3_sync] {ts.name} FAILED: {type(e).__name__}: {e}", flush=True)
                results[ts.name] = {"rows": 0, "error": f"{type(e).__name__}: {e}"}
        return results

    # ---- internals ----

    def _sync_table(self, ts: TableSync) -> int:
        """Sync one table. Returns rows inserted/updated in this cycle.

        Schema bootstrap is folded into the first page fetch — no separate probe
        call. If the local table doesn't exist, we infer its columns from the
        first non-empty page and CREATE TABLE before upserting.
        """
        # Full-sync path (no cursor) — clear & rewrite each cycle.
        if ts.cursor_col is None:
            return self._full_sync(ts)

        last_cursor = self._get_last_cursor(ts.name) or ""
        total_rows = 0
        max_seen = last_cursor
        offset = 0
        while True:
            page = self._fetch_page(
                ts.name,
                since_ts=last_cursor if ts.cursor_is_timestamp else None,
                since_id=last_cursor if not ts.cursor_is_timestamp else None,
                cursor_col=ts.cursor_col,
                offset=offset,
                limit=self._page_limit,
            )
            rows = page.get("rows") or []
            if not rows:
                break
            # Lazy schema bootstrap from the first non-empty page we see.
            if not self._table_exists(ts.name):
                self._create_table_from_row(ts.name, rows[0])
            self._upsert_rows(ts.name, rows)
            total_rows += len(rows)
            for r in rows:
                v = r.get(ts.cursor_col)
                if v is None:
                    continue
                v_str = str(v)
                if v_str > max_seen:
                    max_seen = v_str
            if len(rows) < self._page_limit:
                break
            offset += self._page_limit

        if total_rows > 0 and max_seen != last_cursor:
            self._update_meta(ts.name, max_seen, total_rows)
        return total_rows

    def _full_sync(self, ts: TableSync) -> int:
        """Pull entire table from L3 in pages. Replace local copy atomically."""
        # Collect all rows into memory (acceptable for small tables only — the
        # SYNC_TABLES list constrains which tables get this treatment).
        all_rows: list[dict] = []
        offset = 0
        while True:
            page = self._fetch_page(
                ts.name,
                since_ts=None, since_id=None, cursor_col=None,
                offset=offset, limit=self._page_limit,
            )
            rows = page.get("rows") or []
            all_rows.extend(rows)
            if len(rows) < self._page_limit:
                break
            offset += self._page_limit
        # Lazy schema bootstrap if needed.
        if all_rows and not self._table_exists(ts.name):
            self._create_table_from_row(ts.name, all_rows[0])
        # Replace local copy. DELETE+INSERT in one transaction.
        if self._table_exists(ts.name):
            with self._conn:
                self._conn.execute(f"DELETE FROM [{ts.name}]")
                if all_rows:
                    self._upsert_rows(ts.name, all_rows)
        self._update_meta(ts.name, "<full>", len(all_rows))
        return len(all_rows)

    def _create_table_from_row(self, table: str, sample_row: dict) -> None:
        """CREATE TABLE locally with TEXT columns inferred from one sample row.
        Prefer `id` as the primary key if present so INSERT OR REPLACE upserts work.
        Also create indexes targeting the Layer3Client query patterns — without
        these, filter eval falls back to full-table scans on 299K-row tables
        and per-block processing time blows past Base's 2s block period.
        """
        cols = list(sample_row.keys())
        col_defs = ",".join(f"[{c}] TEXT" for c in cols)
        pk = "PRIMARY KEY ([id])" if "id" in cols else ""
        sql = f"CREATE TABLE IF NOT EXISTS [{table}] ({col_defs}{(', ' + pk) if pk else ''})"
        self._conn.execute(sql)
        # Create indexes matching Layer3Client's actual WHERE clauses. Plain
        # equality columns use plain indexes; LOWER(col) queries need
        # functional indexes (SQLite won't use a plain index when both sides
        # of the WHERE are wrapped in LOWER()).
        index_specs = _INDEX_SPECS_BY_TABLE.get(table, ())
        for name, expr in index_specs:
            try:
                self._conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON [{table}]({expr})")
            except sqlite3.OperationalError:
                # Column doesn't exist in this sample row — skip silently.
                # Most likely cause: this table is empty on the remote so we
                # can't yet infer its schema. The next sync cycle will retry.
                pass
        self._conn.commit()

    def _upsert_rows(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        # Use the first row's keys as the column set. If subsequent rows
        # have additional keys, we ignore them (defensive — schema drift).
        cols = list(rows[0].keys())
        # Ensure local schema has all the columns we're about to write.
        local_cols = self._local_columns(table)
        missing = [c for c in cols if c not in local_cols]
        for c in missing:
            try:
                self._conn.execute(f"ALTER TABLE [{table}] ADD COLUMN [{c}] TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists from a concurrent path

        col_list = ",".join(f"[{c}]" for c in cols)
        placeholders = ",".join("?" * len(cols))
        sql = f"INSERT OR REPLACE INTO [{table}] ({col_list}) VALUES ({placeholders})"
        with self._conn:
            for r in rows:
                values = [self._coerce(r.get(c)) for c in cols]
                try:
                    self._conn.execute(sql, values)
                except sqlite3.IntegrityError:
                    # Tables without `id` PK won't deduplicate via INSERT OR REPLACE;
                    # fall back to a no-op insert. Trade-off: full-sync semantics
                    # depend on the prior DELETE pass.
                    continue

    @staticmethod
    def _coerce(v) -> Optional[str]:
        """Convert any JSON-decoded value to a string-or-None for TEXT storage."""
        if v is None:
            return None
        if isinstance(v, (str, int, float, bool)):
            return str(v) if not isinstance(v, str) else v
        return json.dumps(v, sort_keys=True)

    def _local_columns(self, table: str) -> set[str]:
        try:
            cur = self._conn.execute(f"PRAGMA table_info([{table}])")
            return {row[1] for row in cur.fetchall()}
        except sqlite3.OperationalError:
            return set()

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    def _get_last_cursor(self, table: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT last_cursor FROM _sync_meta WHERE table_name=?", (table,)
        ).fetchone()
        return row[0] if row else None

    def _update_meta(self, table: str, cursor: str, rows: int) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO _sync_meta (table_name, last_cursor, last_sync_at, rows_seen) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(table_name) DO UPDATE SET "
                "last_cursor=excluded.last_cursor, last_sync_at=excluded.last_sync_at, "
                "rows_seen=rows_seen+excluded.rows_seen",
                (table, cursor, now, rows),
            )

    def _fetch_page(
        self,
        table: str,
        *,
        since_ts: Optional[str],
        since_id: Optional[str],
        cursor_col: Optional[str],
        offset: int,
        limit: int,
    ) -> dict:
        params: dict[str, str] = {
            "token": self._token,
            "table": table,
            "offset": str(offset),
            "limit": str(limit),
        }
        if cursor_col:
            params["cursor_col"] = cursor_col
        if since_ts:
            params["since_ts"] = since_ts
        if since_id:
            params["since_id"] = since_id
        url = f"{self._base_url}/dump?{urllib.parse.urlencode(params)}"

        last_err: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES_PER_TABLE):
            try:
                with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    raise RuntimeError(f"L3 /dump rejected token (403); check LAYER3_ADMIN_TOKEN") from e
                if e.code == 400:
                    # Bad request — usually cursor_col mismatch. Surface as RuntimeError;
                    # caller logs and skips this table for the cycle.
                    raise RuntimeError(f"L3 /dump 400 on {table}: {e.read()[:200]!r}") from e
                last_err = e
            except Exception as e:
                last_err = e
            # Backoff before retry.
            time.sleep(2 ** attempt)
        raise RuntimeError(f"L3 /dump failed after {_MAX_RETRIES_PER_TABLE} retries: {last_err}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="run one sync cycle and exit")
    p.add_argument("--interval", type=int, default=None,
                   help="seconds between cycles (default: SYNC_INTERVAL_SECONDS env, 300)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = DEFAULT
    runner = L3SyncRunner.from_config(cfg)
    print(f"l3_sync: base_url={cfg.l3_dump_base_url}, local_db={cfg.l3_db_path}", flush=True)
    print(f"l3_sync: tables={[t.name for t in SYNC_TABLES]}", flush=True)
    try:
        if args.once:
            summary = runner.sync_once()
            for tbl, info in summary.items():
                print(f"  {tbl:<30} rows={info['rows']:>8,}  error={info['error']}")
        else:
            interval = args.interval or cfg.sync_interval_seconds
            print(f"l3_sync: interval={interval}s (Ctrl-C to stop)", flush=True)
            asyncio.run(runner.run_forever(interval))
    finally:
        runner.close()


if __name__ == "__main__":
    main()
