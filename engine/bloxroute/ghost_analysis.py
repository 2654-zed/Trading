"""Phase-4 join: mempool "before" vs block "after" — ghost-tx detector.

DuckDB reads the two JSONL archives IN PLACE (no ETL, no copy) and builds:

  mempool        raw pending-tx rows (Tier-0 capture)
  confs          raw confirmation rows (Tier-1 block ingest)
  mp             mempool deduped to first-seen per hash, with slot=(sender,nonce)
  conf_hashes    confirmed hashes deduped to earliest block (reorg-safe)
  conf_blocks    confirmed blocks deduped (block_number -> earliest recv/ts)
  gaps           wall-time windows where the block_number sequence SKIPS
                 (a missing block = a hole we could not see "land" through)
  labeled        each mp hash + did it land?
  resolved_slots (sender,nonce) slots where SOME observed hash landed
  classified     mp rows in the OBSERVABLE window, labeled:
                   landed         — this exact hash was mined
                   replaced_drop  — never landed but its (sender,nonce) slot was
                                    filled by a different hash (cancel/bump)
                   true_ghost     — never landed and slot never resolved (CANDIDATE)
                   unclassifiable — sender or nonce missing, so no slot key exists

OBSERVABILITY DISCIPLINE (this is the whole ballgame):
A mempool tx can only be classified if we were WATCHING CONFIRMATIONS,
CONTINUOUSLY, for long enough after we saw it. So we restrict `classified`
to mp rows whose first_seen_ts is within [conf_start, conf_end - tail_buffer]
AND whose [first_seen_ts, first_seen_ts + tail_buffer] window does NOT
overlap a confirmation-feed GAP (a skipped block_number). Outside that the
"did it land?" question is UNANSWERABLE with our data; such rows are excluded
(counted as `unobservable_window` / `unobservable_gap`). Without this,
non-overlapping or gappy archives would mislabel everything ghost.

INTERPRETATION LIMITS (printed in the report; not hidden):
  * true_ghost is a CANDIDATE, not proof of censorship/drop. It also covers
    private-pool replacement we didn't see, our own mempool sampling misses,
    and txs that simply hadn't landed within tail_buffer. It is an UPPER bound.
  * replaced_drop only fires when we ALSO saw the replacing tx in mempool;
    otherwise a real replacement looks like true_ghost. So true_ghost is
    inflated and replaced_drop is a lower bound.
  * If confirmation GAPS exist, a tx that landed in a missing block would also
    read as ghost — which is exactly why gap-overlapping txs are excluded, and
    why any residual gaps are reported loudly.
  * A single vantage point cannot establish causation (why a tx didn't land).
"""

from __future__ import annotations

import glob as _glob
import os

import duckdb


def _q(path_glob: str) -> str:
    # duckdb is happiest with forward slashes; paths here are local + trusted.
    return path_glob.replace("\\", "/").replace("'", "''")


def _count_raw_lines(files: list[str]) -> int:
    """Fast newline count across files (to reconcile against parsed rows so
    any silently-skipped malformed/truncated line is surfaced, not hidden)."""
    total = 0
    for fp in files:
        try:
            with open(fp, "rb") as fh:
                buf = fh.read(1 << 20)
                while buf:
                    total += buf.count(b"\n")
                    last = buf[-1:]
                    buf = fh.read(1 << 20)
            # count a final line with no trailing newline
            if last not in (b"\n", b""):
                total += 1
        except OSError:
            pass
    return total


def build_views(con, mempool_glob: str, confs_glob: str) -> None:
    con.execute("SET TimeZone='UTC';")
    # ignore_errors=true: a single truncated line (e.g. a capture crash mid
    # write) must NOT make a multi-hundred-MB forensic archive unreadable.
    # Skipped lines are reconciled + reported by analyze() (loud, not hidden).
    con.execute(f"""
        CREATE OR REPLACE VIEW mempool AS
        SELECT * FROM read_json_auto('{_q(mempool_glob)}',
            format='newline_delimited', union_by_name=true, ignore_errors=true);
    """)
    con.execute(f"""
        CREATE OR REPLACE VIEW confs AS
        SELECT * FROM read_json_auto('{_q(confs_glob)}',
            format='newline_delimited', union_by_name=true, ignore_errors=true);
    """)
    con.execute("""
        CREATE OR REPLACE VIEW mp AS
        SELECT lower(hash) AS hash,
               any_value("from") AS sender,
               any_value(nonce)  AS nonce,
               min(recv_ts)      AS first_seen_ts,
               count(*)          AS times_seen
        FROM mempool
        WHERE hash IS NOT NULL AND hash <> ''
        GROUP BY lower(hash);
    """)
    con.execute("""
        CREATE OR REPLACE VIEW conf_hashes AS
        SELECT lower(hash)          AS hash,
               min(block_number)    AS block_number,
               min(block_timestamp) AS block_timestamp
        FROM confs
        WHERE hash IS NOT NULL AND hash <> ''
        GROUP BY lower(hash);
    """)
    # one row per distinct mined block, earliest local-receive time
    con.execute("""
        CREATE OR REPLACE VIEW conf_blocks AS
        SELECT block_number,
               min(block_received_local_ts) AS recv_ts,
               min(block_timestamp)         AS block_ts
        FROM confs
        WHERE block_number IS NOT NULL AND block_received_local_ts IS NOT NULL
        GROUP BY block_number;
    """)
    # gaps: a jump in block_number between consecutive observed blocks means
    # we never saw the blocks in between -> a hole in "did it land?" coverage.
    con.execute("""
        CREATE OR REPLACE VIEW conf_block_seq AS
        SELECT block_number, recv_ts,
               block_number - LAG(block_number) OVER (ORDER BY block_number) AS bn_step,
               LAG(recv_ts) OVER (ORDER BY block_number) AS prev_recv_ts
        FROM conf_blocks;
    """)
    con.execute("""
        CREATE OR REPLACE VIEW gaps AS
        SELECT prev_recv_ts AS win_start, recv_ts AS win_end,
               (bn_step - 1) AS missing_blocks
        FROM conf_block_seq WHERE bn_step > 1;
    """)
    con.execute("""
        CREATE OR REPLACE VIEW labeled AS
        SELECT mp.*,
               (c.hash IS NOT NULL) AS landed,
               c.block_number       AS landed_block,
               c.block_timestamp    AS landed_block_ts
        FROM mp LEFT JOIN conf_hashes c USING (hash);
    """)
    con.execute("""
        CREATE OR REPLACE VIEW resolved_slots AS
        SELECT DISTINCT sender, nonce
        FROM labeled
        WHERE landed AND sender IS NOT NULL AND nonce IS NOT NULL;
    """)


def coverage(con) -> dict:
    """Confirmation-feed temporal SPAN + mempool span (epoch seconds). NOTE:
    these are min/max bounds, NOT proof of continuous block capture — see the
    `gaps` view / detect_gaps() for holes inside the span."""
    c = con.execute("""
        SELECT min(block_received_local_ts), max(block_received_local_ts),
               count(*), count(DISTINCT lower(hash))
        FROM confs WHERE block_received_local_ts IS NOT NULL
    """).fetchone()
    m = con.execute("""
        SELECT min(first_seen_ts), max(first_seen_ts), count(*) FROM mp
    """).fetchone()
    return {
        "conf_start": c[0], "conf_end": c[1],
        "conf_rows": c[2] or 0, "conf_unique_hashes": c[3] or 0,
        "mempool_start": m[0], "mempool_end": m[1], "mempool_hashes": m[2] or 0,
    }


def detect_gaps(con) -> dict:
    rows = con.execute(
        "SELECT win_start, win_end, missing_blocks FROM gaps ORDER BY win_start"
    ).fetchall()
    return {
        "n_gaps": len(rows),
        "missing_blocks": sum(int(r[2]) for r in rows),
        "windows": [{"win_start": r[0], "win_end": r[1],
                     "missing_blocks": int(r[2])} for r in rows[:20]],
    }


def build_classified(con, cov_start: float, cov_end_obs: float,
                     tail_buffer_s: float) -> None:
    # observable = inside the temporal window AND not overlapping any gap's
    # wall-time window during the tx's [first_seen, first_seen+buffer] horizon.
    con.execute(f"""
        CREATE OR REPLACE VIEW observable AS
        SELECT l.* FROM labeled l
        WHERE l.first_seen_ts >= {cov_start!r}
          AND l.first_seen_ts <= {cov_end_obs!r}
          AND NOT EXISTS (
            SELECT 1 FROM gaps g
            WHERE g.win_start <= l.first_seen_ts + {tail_buffer_s!r}
              AND g.win_end   >= l.first_seen_ts);
    """)
    con.execute("""
        CREATE OR REPLACE VIEW classified AS
        SELECT o.*,
               CASE WHEN o.sender IS NULL OR o.nonce IS NULL THEN 'unclassifiable'
                    WHEN o.landed THEN 'landed'
                    WHEN rs.sender IS NOT NULL THEN 'replaced_drop'
                    ELSE 'true_ghost' END AS klass
        FROM observable o
        LEFT JOIN resolved_slots rs
          ON o.sender = rs.sender AND o.nonce = rs.nonce;
    """)


def analyze(mempool_glob: str, confs_glob: str,
            tail_buffer_s: float = 180.0, burst_top: int = 15) -> dict:
    """Run the full join + classification. Returns a structured result with
    coverage diagnostics, confirmation-gap report, classification counts,
    hourly rates, ghost bursts, and an `observable` flag. Never fabricates
    overlap or continuity that isn't there."""
    mp_files = sorted(_glob.glob(mempool_glob))
    cf_files = sorted(_glob.glob(confs_glob))
    out: dict = {
        "mempool_files": len(mp_files), "confs_files": len(cf_files),
        "tail_buffer_s": tail_buffer_s, "observable": False,
        "classification": {}, "hourly": [], "ghost_bursts": [],
        "gaps": {"n_gaps": 0, "missing_blocks": 0, "windows": []},
        "malformed_skipped": {"mempool": 0, "confs": 0},
        "note": None,
    }
    if not mp_files:
        out["note"] = f"no mempool files matched {mempool_glob}"
        return out
    if not cf_files:
        out["note"] = (f"no confirmation files matched {confs_glob} — run "
                       "engine.scripts.run_block_ingest to produce the 'after' side")
        return out

    con = duckdb.connect()
    try:
        build_views(con, mempool_glob, confs_glob)

        # reconcile parsed rows vs raw lines -> surface any skipped lines
        parsed_mp = con.execute("SELECT count(*) FROM mempool").fetchone()[0]
        parsed_cf = con.execute("SELECT count(*) FROM confs").fetchone()[0]
        out["malformed_skipped"] = {
            "mempool": max(0, _count_raw_lines(mp_files) - parsed_mp),
            "confs": max(0, _count_raw_lines(cf_files) - parsed_cf),
        }

        cov = coverage(con)
        out["coverage"] = cov
        out["gaps"] = detect_gaps(con)

        if cov["conf_rows"] == 0 or cov["mempool_hashes"] == 0:
            out["note"] = ("no confirmation rows parsed" if cov["conf_rows"] == 0
                           else "no mempool rows parsed")
            return out
        if cov["conf_start"] is None or cov["mempool_start"] is None:
            # rows exist but timestamps are all NULL — a schema/corruption issue,
            # distinct from "no rows" above.
            out["note"] = ("rows parsed but all recv_ts/block_received_local_ts "
                           "are NULL — check archive schema/integrity")
            return out

        cov_end_obs = cov["conf_end"] - tail_buffer_s
        out["observable_window"] = {"start": cov["conf_start"], "end": cov_end_obs}
        ov_start = max(cov["conf_start"], cov["mempool_start"])
        ov_end = min(cov["conf_end"], cov["mempool_end"])
        out["overlap_seconds"] = max(0.0, ov_end - ov_start)

        n_window = con.execute(
            "SELECT count(*) FROM mp WHERE first_seen_ts >= ? "
            "AND first_seen_ts <= ?", [cov["conf_start"], cov_end_obs]
        ).fetchone()[0]
        out["mempool_txs_in_window"] = n_window
        if n_window == 0:
            out["note"] = (
                "ZERO mempool txs fall inside the confirmation-coverage window "
                "— the mempool 'before' and block 'after' archives do not "
                "overlap in time. Classification is impossible until both "
                "captures run CONCURRENTLY. (Logic verified on synthetic "
                "fixtures; see tests.)")
            return out

        build_classified(con, cov["conf_start"], cov_end_obs, tail_buffer_s)
        cls = {k: n for k, n in con.execute(
            "SELECT klass, count(*) FROM classified GROUP BY klass").fetchall()}
        out["classification"] = cls
        n_classified = sum(cls.values())
        out["unobservable_gap"] = n_window - n_classified   # excluded by a gap
        out["observable"] = n_classified > 0

        out["hourly"] = [
            {"hour": h, "landed": la, "replaced_drop": rd,
             "true_ghost": tg, "unclassifiable": uc}
            for h, la, rd, tg, uc in con.execute("""
                SELECT strftime(to_timestamp(first_seen_ts), '%Y-%m-%d %H:00') AS hr,
                       count(*) FILTER (WHERE klass='landed'),
                       count(*) FILTER (WHERE klass='replaced_drop'),
                       count(*) FILTER (WHERE klass='true_ghost'),
                       count(*) FILTER (WHERE klass='unclassifiable')
                FROM classified GROUP BY hr ORDER BY hr
            """).fetchall()]
        out["ghost_bursts"] = [
            {"minute": mnt, "true_ghosts": g} for mnt, g in con.execute(f"""
                SELECT strftime(to_timestamp(first_seen_ts), '%Y-%m-%d %H:%M') AS mnt,
                       count(*) FILTER (WHERE klass='true_ghost') AS g
                FROM classified GROUP BY mnt HAVING g > 0
                ORDER BY g DESC, mnt LIMIT {int(burst_top)}
            """).fetchall()]
        if not out["observable"]:
            out["note"] = ("all in-window txs were excluded by confirmation "
                           "gaps — coverage too holey to classify")
        return out
    finally:
        con.close()


__all__ = ["build_views", "coverage", "detect_gaps", "build_classified",
           "analyze"]
