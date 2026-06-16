"""Q1 first-pass: does Base-memecoin-ecosystem behavioral 'froth' lead ETH?

Builds candidate daily-ish froth features from L3 org_transfer_events across
the monitored ecosystem, and tests FORWARD predictive correlation with ETH
returns (with an OOS split), plus a beta/lag control (does froth merely
track RECENT ETH rather than lead future ETH?).

$0 Alchemy — L3 SQLite + free DefiLlama WETH price. Read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..adapters.price_history import DefiLlamaPriceHistory

WETH = "0x4200000000000000000000000000000000000006"
BUCKET = 12 * 3600
MIN_EVENTS = 20


def _pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pts)
    if n < 8:
        return None, n
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    cov = sum((p[0]-mx)*(p[1]-my) for p in pts)
    vx = sum((p[0]-mx)**2 for p in pts)
    vy = sum((p[1]-my)**2 for p in pts)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov/math.sqrt(vx*vy), n


def _iso_to_epoch(ts):
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def shannon(counts):
    tot = sum(counts.values())
    if tot <= 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c > 0:
            p = c/tot
            h -= p*math.log2(p)
    return h


async def run(args) -> int:
    import json
    conn = sqlite3.connect(f"file:{args.l3_db_path}?mode=ro", uri=True)
    data = json.load(open(args.monitored_pools_path))
    pools = data if isinstance(data, list) else data.get("pools", [])
    toks = list({(v['address'].lower()) for p in pools
                 for v in (p.get('token0'), p.get('token1'))
                 if isinstance(v, dict) and v.get('address')})
    ph = ",".join("?"*len(toks))

    rows = conn.execute(
        f"SELECT to_address, from_role, org_id, value_eth, timestamp "
        f"FROM org_transfer_events WHERE LOWER(to_address) IN ({ph})", toks
    ).fetchall()
    conn.close()

    # Bucket into 12h windows.
    buckets = defaultdict(lambda: {"n": 0, "vol": 0.0, "roles": defaultdict(int),
                                   "orgs": set(), "toks": set()})
    tmin, tmax = 1e20, 0
    for to_addr, role, org, val, ts in rows:
        t = _iso_to_epoch(ts)
        if t is None:
            continue
        tmin, tmax = min(tmin, t), max(tmax, t)
        b = int(t // BUCKET)
        d = buckets[b]
        d["n"] += 1
        d["vol"] += float(val or 0)
        d["roles"][role or "null"] += 1
        if org:
            d["orgs"].add(org)
        d["toks"].add((to_addr or "").lower())

    # ETH prices.
    px = DefiLlamaPriceHistory(period="4h")
    px.prefetch([WETH], tmin - BUCKET, tmax + 3*86400)

    def eth(t):
        return px.price_at(WETH, t)

    # Per bucket: features at bucket end + forward ETH returns + prior ETH return.
    recs = []
    for b in sorted(buckets):
        d = buckets[b]
        if d["n"] < MIN_EVENTS:
            continue
        t_end = (b + 1) * BUCKET
        p0 = eth(t_end)
        if not p0 or p0 <= 0:
            continue
        feats = {
            "activity_n": d["n"],
            "value_flow": d["vol"],
            "role_entropy": shannon(d["roles"]),
            "distinct_orgs": len(d["orgs"]),
            "breadth_tokens": len(d["toks"]),
        }
        def fwd(h):
            if not px.has_data_through(WETH, t_end + h):
                return None
            p1 = eth(t_end + h)
            return (p1 - p0)/p0 if p1 else None
        p_prev = eth(t_end - 24*3600)
        prior_ret = (p0 - p_prev)/p_prev if p_prev and p_prev > 0 else None
        recs.append({"t": t_end, "f": feats,
                     "r24": fwd(24*3600), "r48": fwd(48*3600),
                     "prior": prior_ret})

    print(f"usable 12h buckets: {len(recs)}  "
          f"(span {datetime.fromtimestamp(tmin,timezone.utc).date()} .. "
          f"{datetime.fromtimestamp(tmax,timezone.utc).date()})")
    if len(recs) < 16:
        print("too few buckets for a split."); return 1

    split = recs[int(0.7*len(recs))]["t"]
    feat_names = list(recs[0]["f"].keys())

    def corr(feat, target_key, lo, hi):
        xs = [r["f"][feat] for r in recs if lo <= r["t"] < hi]
        ys = [r[target_key] for r in recs if lo <= r["t"] < hi]
        return _pearson(xs, ys)

    print()
    print("="*78)
    print("Q1 — froth feature vs FORWARD ETH return (lead test)  [+OOS split]")
    print("="*78)
    print(f"{'feature':16s} {'h':>4s} {'IS corr (n)':>16s} {'OOS corr (n)':>16s} "
          f"{'BETA: vs prior24h':>18s}")
    for feat in feat_names:
        for tgt, h in (("r24", "24h"), ("r48", "48h")):
            c_is, n_is = corr(feat, tgt, 0, split)
            c_oos, n_oos = corr(feat, tgt, split, 1e20)
            c_beta, _ = corr(feat, "prior", 0, 1e20)
            fmt = lambda c, n: (f"{c:+.3f} (n={n})" if c is not None else f"n/a (n={n})")
            print(f"{feat:16s} {h:>4s} {fmt(c_is,n_is):>16s} {fmt(c_oos,n_oos):>16s} "
                  f"{(f'{c_beta:+.3f}' if c_beta is not None else 'n/a'):>18s}")
    print()
    print("Read: a FORWARD corr that holds IS->OOS with the SAME sign = lead signal.")
    print("If 'BETA vs prior24h' is large, the feature mostly TRACKS recent ETH (lag),")
    print("not leads it. Want: forward corr survives OOS AND exceeds the beta column.")
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
