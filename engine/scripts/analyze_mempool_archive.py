"""Offline decision-grade analysis of captured mempool archives.

Reads the JSONL archive written by run_mempool_capture and produces the
honest "can we compete?" + drain-fanout report. Re-runnable; $0; no feed.

Usage:
    python -m engine.scripts.analyze_mempool_archive
    python -m engine.scripts.analyze_mempool_archive --glob "engine/data/mempool/mempool_*.jsonl"
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.analysis import analyze_competition, analyze_drains


def _load(globpat: str) -> list:
    txs = []
    files = sorted(glob.glob(globpat))
    for fp in files:
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    txs.append(json.loads(line))
                except ValueError:
                    continue
    return txs, files


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--glob", default="engine/data/mempool/mempool_*.jsonl")
    p.add_argument("--window", type=float, default=2.0)
    args = p.parse_args()

    txs, files = _load(args.glob)
    if not txs:
        print(f"no archived txs matched {args.glob}", file=sys.stderr)
        return 1
    span = (max(t.get("recv_ts", 0) for t in txs)
            - min(t.get("recv_ts", 0) for t in txs))
    print(f"loaded {len(txs):,} txs from {len(files)} file(s), "
          f"span {span/60:.1f} min")

    comp = analyze_competition(txs, window_s=args.window)
    drains = analyze_drains(txs)

    print("\n" + "=" * 66)
    print("CAN WE COMPETE? — contention, organic-noise-corrected")
    print("=" * 66)
    print(f"  top-decile priority-fee floor : "
          f"{comp['fee_threshold_gwei_top_decile']} gwei "
          f"({comp['n_bidders']:,} bidder txs of {comp['n_txs']:,})")
    print(f"  RAW contention (all txs)       : max {comp['raw_max_racers']} racers, "
          f"{comp['raw_contended_events']:,} contended events  "
          f"<- inflated by organic volume")
    print(f"  BIDDER contention (real MEV)   : max {comp['bidder_max_racers']} racers, "
          f"{comp['bidder_contended_events']:,} contended events")
    print(f"  genuinely contested targets    : {comp['n_contested_targets']}")
    print(f"  COST TO COMPETE (gwei p50/p90/max on contested): "
          f"{comp['cost_to_compete_gwei_p50']} / "
          f"{comp['cost_to_compete_gwei_p90']} / "
          f"{comp['cost_to_compete_gwei_max']}")

    print("\n" + "=" * 66)
    print("DRAIN FAN-OUT — one initiator pulling from many owners (candidates)")
    print("=" * 66)
    print(f"  third-party transferFrom txs   : "
          f"{drains['total_third_party_transferFrom']:,} "
          f"({drains['distinct_initiators']:,} distinct initiators)")
    print(f"  fan-out candidates (>=2 owners): {drains['n_fanout_candidates']}")
    for init, n in drains["fanout_candidates"][:10]:
        print(f"    {init}  -> {n} distinct (owner, token)")
    print("\n  NOTE: fan-out candidates include legitimate CEX sweepers. This")
    print("  surfaces candidates pre-execution; it does not convict. The")
    print("  deductive primitive (initiator != owner) is sound; entity")
    print("  labeling to separate sweeper from drainer is the next layer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
