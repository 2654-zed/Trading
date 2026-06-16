"""MEMPOOL_FORENSIC substrate: the real data the loop measures on.

Provides hourly-window discovery, a CHRONOLOGICAL train/holdout split (honest
OOS = later windows held out), and a small REGISTRY of vetted forensic
measurements. The 'Coder' selects + parameterizes a registered measurement
rather than emitting arbitrary code — for a local research tool on our own
read-only data this is the safe scaffold default; the 'write code → Docker
sandbox' path is the production hardening (see README).

Canonical measurement: per-block PRIVATE-INCLUSION SHARE (fraction of a
block's txs we NEVER saw in our mempool capture = the observable shadow of
private orderflow) vs block size. A real, falsifiable microstructure question
computable from exactly the data we hold.
"""

from __future__ import annotations

import glob
import math
import os
import re
from typing import Optional

import duckdb

MEMPOOL_DIR = "engine/data/mempool"
CONF_DIR = "engine/data/confirmations"
_KEY_RE = re.compile(r"_(\d{8}_\d{2})\.jsonl$")


def _keys(dir_, prefix) -> set[str]:
    out = set()
    for fp in glob.glob(f"{dir_}/{prefix}*.jsonl"):
        m = _KEY_RE.search(fp.replace("\\", "/"))
        if m:
            out.add(m.group(1))
    return out


def available_windows() -> list[str]:
    """Hourly window keys ('YYYYMMDD_HH') present in BOTH mempool and
    confirmations — a measurement needs the 'before' and 'after' sides."""
    return sorted(_keys(MEMPOOL_DIR, "mempool_") & _keys(CONF_DIR, "confirmations_"))


def split_windows(windows: list[str], holdout_frac: float = 0.34
                  ) -> tuple[list[str], list[str]]:
    """Chronological split: the LATEST windows become the sealed holdout."""
    ws = sorted(windows)
    k = max(1, round(len(ws) * holdout_frac))
    return ws[:-k], ws[-k:]


def _files(window_keys: list[str]):
    mp = [f"{MEMPOOL_DIR}/mempool_{k}.jsonl" for k in window_keys]
    cf = [f"{CONF_DIR}/confirmations_{k}.jsonl" for k in window_keys]
    mp = [p for p in mp if os.path.exists(p)]
    cf = [p for p in cf if os.path.exists(p)]
    return mp, cf


def _sql_list(paths) -> str:
    return "[" + ",".join("'" + p.replace("\\", "/") + "'" for p in paths) + "]"


def _block_stats(window_keys: list[str]) -> list[tuple[int, int, float]]:
    """Per block: (block_number, total_txs, private_share). private_share =
    1 - (txs we saw in the mempool) / total — the private-orderflow shadow."""
    mp, cf = _files(window_keys)
    if not mp or not cf:
        return []
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone='UTC';")
        rows = con.execute(f"""
            WITH seen AS (
              SELECT DISTINCT lower(hash) AS h
              FROM read_json_auto({_sql_list(mp)}, format='newline_delimited',
                                  union_by_name=true, ignore_errors=true)
              WHERE hash IS NOT NULL),
            conf AS (
              SELECT lower(hash) AS h, block_number
              FROM read_json_auto({_sql_list(cf)}, format='newline_delimited',
                                  union_by_name=true, ignore_errors=true)
              WHERE hash IS NOT NULL AND block_number IS NOT NULL)
            SELECT conf.block_number,
                   count(*) AS total,
                   1.0 - (count(seen.h)::DOUBLE / count(*)) AS private_share
            FROM conf LEFT JOIN seen ON conf.h = seen.h
            GROUP BY conf.block_number
            HAVING count(*) >= 10                      -- ignore partial blocks
            ORDER BY conf.block_number
        """).fetchall()
        return [(int(b), int(t), float(p)) for b, t, p in rows]
    finally:
        con.close()


# ── statistics (no scipy dependency) ────────────────────────────────────────

def _phi(z: float) -> float:                      # standard normal CDF
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _pearson(xs: list[float], ys: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Pearson r + two-sided p via Fisher z (normal approx; valid for n≥~30)."""
    n = len(xs)
    if n < 4:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None, None
    r = sxy / math.sqrt(sxx * syy)
    r = max(-0.999999, min(0.999999, r))
    if n <= 3:
        return r, None
    z = math.atanh(r) * math.sqrt(n - 3)
    p = 2 * (1 - _phi(abs(z)))
    return r, p


def _sign(x: Optional[float], eps: float = 1e-9) -> str:
    if x is None or abs(x) < eps:
        return "0"
    return "+" if x > 0 else "-"


# ── the measurement registry ────────────────────────────────────────────────

def private_share_vs_blocksize(window_keys: list[str]) -> dict:
    """H: per-block private-inclusion share correlates with block size.
    effect = Pearson r(private_share, total_txs) across blocks."""
    stats = _block_stats(window_keys)
    if len(stats) < 4:
        return {"effect": None, "edge_sign": "0", "n_obs": len(stats),
                "p_value": None, "per_regime": [], "coverage_fraction": None,
                "notes": "no_lookahead; insufficient blocks"}
    totals = [s[1] for s in stats]
    shares = [s[2] for s in stats]
    r, p = _pearson([float(t) for t in totals], shares)
    # per-regime: split blocks chronologically in half, require sign-consistency
    half = len(stats) // 2
    per_regime = []
    for label, sub in (("early", stats[:half]), ("late", stats[half:])):
        if len(sub) >= 4:
            rr, pp = _pearson([float(s[1]) for s in sub], [s[2] for s in sub])
            per_regime.append({"regime": label, "effect": rr,
                               "edge_sign": _sign(rr), "n_obs": len(sub)})
    seen_total = sum(t * (1 - sh) for t, sh in zip(totals, shares))
    cov = seen_total / sum(totals) if sum(totals) else None
    return {"effect": r, "edge_sign": _sign(r), "n_obs": len(stats),
            "p_value": p, "per_regime": per_regime, "coverage_fraction": cov,
            "notes": "no_lookahead; private_share from block-time data only"}


MEASUREMENTS = {
    "private_share_vs_blocksize": private_share_vs_blocksize,
}


def run_measurement(measurement_key: str, window_keys: list[str]) -> dict:
    """Run a registered measurement over the given windows. Loud failure if
    the key is unknown (never silently substitute)."""
    fn = MEASUREMENTS.get(measurement_key)
    if fn is None:
        raise KeyError(f"unknown measurement '{measurement_key}'; "
                       f"registered: {sorted(MEASUREMENTS)}")
    return fn(window_keys)


__all__ = ["available_windows", "split_windows", "run_measurement",
           "MEASUREMENTS", "private_share_vs_blocksize"]
