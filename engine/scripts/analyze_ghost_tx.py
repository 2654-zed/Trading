"""Phase-4 report runner: ghost-tx detector over the captured archives.

Joins the mempool "before" archive against the block "after" archive and
prints the landed / replaced_drop / true_ghost breakdown with the
observability + interpretation discipline made explicit. Re-runnable; $0;
reads JSONL in place via DuckDB.

Usage:
    python -m engine.scripts.analyze_ghost_tx
    python -m engine.scripts.analyze_ghost_tx --tail-buffer 180
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.ghost_analysis import analyze


def _ts(epoch):
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mempool-glob",
                   default="engine/data/mempool/mempool_*.jsonl")
    p.add_argument("--confs-glob",
                   default="engine/data/confirmations/confirmations_*.jsonl")
    p.add_argument("--tail-buffer", type=float, default=180.0,
                   help="seconds of confirmation coverage required AFTER a "
                        "mempool tx before it can be called a ghost")
    p.add_argument("--burst-top", type=int, default=15)
    args = p.parse_args()

    r = analyze(args.mempool_glob, args.confs_glob,
                tail_buffer_s=args.tail_buffer, burst_top=args.burst_top)

    print("=" * 70)
    print("PHASE-4 GHOST-TX JOIN  (mempool 'before' vs block 'after')")
    print("=" * 70)
    print(f"  mempool files: {r['mempool_files']}   "
          f"confirmation files: {r['confs_files']}")
    cov = r.get("coverage")
    if cov:
        print(f"  mempool span      : {_ts(cov['mempool_start'])} -> "
              f"{_ts(cov['mempool_end'])}  ({cov['mempool_hashes']:,} hashes)")
        print(f"  confirmation span : {_ts(cov['conf_start'])} -> "
              f"{_ts(cov['conf_end'])}  ({cov['conf_unique_hashes']:,} mined hashes)")
        print(f"  archive overlap   : {r.get('overlap_seconds', 0)/60:.1f} min")
        ow = r.get("observable_window", {})
        print(f"  observable window : {_ts(ow.get('start'))} -> "
              f"{_ts(ow.get('end'))}  (tail buffer {r['tail_buffer_s']:.0f}s)")
        print(f"  mempool txs in window : {r.get('mempool_txs_in_window', 0):,}")

    # data-integrity surfacing (loud, not hidden)
    ms = r.get("malformed_skipped", {})
    if ms.get("mempool") or ms.get("confs"):
        print(f"  ! malformed/skipped lines: mempool={ms.get('mempool',0):,} "
              f"confs={ms.get('confs',0):,}  (ignored to keep archive readable)")
    g = r.get("gaps", {})
    if g.get("n_gaps"):
        print(f"  ! CONFIRMATION GAPS: {g['n_gaps']} gap(s), "
              f"{g['missing_blocks']:,} missing block(s) — txs whose landing "
              f"window overlaps a gap are EXCLUDED (not called ghost)")
    else:
        print("  confirmation gaps : none detected (block sequence continuous)")
    if r.get("unobservable_gap"):
        print(f"  gap-excluded txs  : {r['unobservable_gap']:,}")

    if not r["observable"]:
        print("\n  >>> NOT CLASSIFIABLE <<<")
        print(f"  {r['note']}")
        _print_discipline()
        return 0

    total = sum(r["classification"].values())
    print("\n  CLASSIFICATION (observable window only):")
    for k in ("landed", "replaced_drop", "true_ghost", "unclassifiable"):
        n = r["classification"].get(k, 0)
        pct = 100.0 * n / total if total else 0.0
        print(f"    {k:<14}: {n:>8,}  ({pct:5.1f}%)")
    print(f"    {'TOTAL':<14}: {total:>8,}")

    if r["hourly"]:
        print("\n  HOURLY RATES (UTC):")
        print(f"    {'hour':<16} {'landed':>9} {'replaced':>9} {'ghost':>9}")
        for h in r["hourly"]:
            print(f"    {h['hour']:<16} {h['landed']:>9,} "
                  f"{h['replaced_drop']:>9,} {h['true_ghost']:>9,}")

    if r["ghost_bursts"]:
        print(f"\n  TOP GHOST-BURST MINUTES (UTC):")
        for b in r["ghost_bursts"]:
            print(f"    {b['minute']}   true_ghosts={b['true_ghosts']:,}")

    _print_discipline()
    return 0


def _print_discipline():
    print("\n  " + "-" * 66)
    print("  INTERPRETATION DISCIPLINE (read before drawing conclusions):")
    print("  * true_ghost is a CANDIDATE, not proof. It includes private-pool")
    print("    replacements we never saw, our own mempool sampling misses, and")
    print("    txs that simply hadn't landed within the tail buffer. UPPER bound.")
    print("  * replaced_drop only fires when we ALSO captured the replacing tx;")
    print("    otherwise a real replacement reads as true_ghost. LOWER bound.")
    print("  * txs that landed in blocks we FAILED to fetch (confirmation gaps)")
    print("    would also read as ghost — such txs are excluded above, and any")
    print("    residual gaps are reported loudly; a gappy feed is untrustworthy.")
    print("  * A single vantage point cannot establish CAUSATION (why a tx")
    print("    didn't land — drop vs censor vs private inclusion are not")
    print("    distinguishable from this data alone).")
    print("  * Any price/profit question is only meaningful against a matched")
    print("    random-minute control — not run here (no price feed wired).")
    print("  " + "-" * 66)


if __name__ == "__main__":
    sys.exit(main())
