"""Sub-phase 3.5 driver — feedback-loop hardening + H8 falsification.

Runs:
  1. The H8 out-of-sample test (train/holdout split; learned vs static
     weights on held-out windows) — the rigorous H8 verdict + an H5
     out-of-sample read (sharpens UNK-015).
  2. A full-span replay → ledger, then the per-lens-per-regime
     observability report (Markdown) + a ledger admin dump.

All $0 CU, proxy outcomes (D-034), execution disabled.

Usage: python -m engine.scripts.run_engine_phase_3_5 --target-windows 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..adapters.l3_corpus_phase2 import Phase2L3CorpusAdapter
from ..adapters.monitored_set_phase2 import Phase2MonitoredSetAdapter
from ..feedback.ledger_admin import LedgerAdmin
from ..feedback.observability import compute_performance, render_markdown
from ..feedback.replay import h8_holdout_test, replay_to_ledger


def _all_addresses(monitored):
    addrs = set()
    for p in monitored.list_pools():
        if p.get("address"):
            addrs.add(p["address"].lower())
        for k in ("token0", "token1"):
            v = p.get(k)
            if isinstance(v, dict) and v.get("address"):
                addrs.add(v["address"].lower())
    return list(addrs)


async def run(args) -> int:
    print(f"[run_engine_phase_3_5] starting "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    rng = l3.get_data_time_range(_all_addresses(monitored))
    if rng is None:
        print("ERROR: no L3 data", file=sys.stderr); return 2
    start_ts, end_ts = rng
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    # --- H8 out-of-sample falsification ---
    print("  running H8 out-of-sample test (train/holdout) ...", flush=True)
    h8 = await h8_holdout_test(
        l3, monitored, start_ts=start_ts, end_ts=end_ts,
        target_windows=args.target_windows, out_dir=out, train_frac=0.7)

    # --- Full-span replay → observability + ledger dump ---
    print("  running full-span replay for observability ...", flush=True)
    span = end_ts - start_ts
    slice_seconds = max(span / max(args.target_windows, 1), 1.0)
    full_ledger_path = out / "sub_phase_3_5_ledger.db"
    writer = await replay_to_ledger(
        l3, monitored, full_ledger_path,
        start_ts=start_ts, end_ts=end_ts, slice_seconds=slice_seconds)
    joined = writer.outcomes_joined()
    perf = compute_performance(joined)
    md = render_markdown(perf)
    (out / "sub_phase_3_5_performance.md").write_text(md, encoding="utf-8")
    writer.close()

    admin = LedgerAdmin(full_ledger_path)
    dump = admin.dump(recent=3)
    admin.close()
    l3.close()

    print()
    print("=" * 70)
    print("sub-phase 3.5 summary (feedback hardening + H8 falsification)")
    print("=" * 70)
    print(f"  H8 OUT-OF-SAMPLE (train 70% → holdout 30%):")
    print(f"    holdout windows     : {h8['holdout_n']}")
    print(f"    static  |corr|      : {h8['static_abs']:.3f}")
    print(f"    learned |corr|      : {h8['learned_abs']:.3f}")
    print(f"    improvement         : {h8['improvement_pp']}pp")
    print(f"    H8 verdict          : {h8['h8_verdict']}")
    h5 = h8["holdout_h5"]
    print()
    print(f"  H5 OUT-OF-SAMPLE (holdout set):")
    if "orchestrator_abs_corr" in h5:
        print(f"    orch |corr|         : {h5['orchestrator_abs_corr']:.3f}")
        print(f"    best lens           : {h5['best_lens']} "
              f"|corr|={h5['best_lens_abs_corr']:.3f}")
        print(f"    margin              : {h5['margin_pp']}pp "
              f"(formal bar ≥15pp)")
        print(f"    verdict             : {h5['verdict']}")
    print()
    print(f"  LEDGER (full span): {dump['counts']}")
    print(f"  by_regime         : {dump['by_regime']}")
    print(f"  missing_outcomes  : {dump['missing_outcomes']}")
    print(f"  observability report → {out / 'sub_phase_3_5_performance.md'}")
    print()
    print("  --- per-lens-per-regime performance (Markdown) ---")
    print(md)
    print()

    (out / "sub_phase_3_5_h8.json").write_text(
        json.dumps(h8, indent=2, default=str), encoding="utf-8")
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-windows", type=int, default=200)
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    p.add_argument("--out-dir", type=Path, default=Path("engine/data"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
