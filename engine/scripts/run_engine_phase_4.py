"""Phase 4 driver — REAL outcomes (D-040). Re-tests H5–H8 with realized
forward price returns instead of the D-034 flow proxy.

Pipeline (reuses the entire Phase 3 engine; only the OUTCOME changes):
  1. Prefetch DefiLlama price series for all monitored tokens ($0 CU).
  2. Full-span replay with RealizedOutcomeAttributor → in-sample H5/H7 +
     observability report.
  3. Out-of-sample H8/H5 replay (train/holdout) with real outcomes.
  4. Print real H5–H8 verdicts → input to UNK-015 resolution + Phase 5
     go/no-go.

$0 CU, read-only, execution disabled.

Usage: python -m engine.scripts.run_engine_phase_4 --target-windows 200
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
from ..adapters.price_history import DefiLlamaPriceHistory
from ..feedback.observability import compute_performance, render_markdown
from ..feedback.phase3_analysis import analyze_h5, analyze_h7
from ..feedback.realized_outcome import RealizedOutcomeAttributor
from ..feedback.replay import (
    h8_holdout_test, replay_to_ledger, score_outcome_correlation,
)


def _tokens(monitored):
    toks = set()
    for p in monitored.list_pools():
        for k in ("token0", "token1"):
            v = p.get(k)
            if isinstance(v, dict) and v.get("address"):
                toks.add(v["address"].lower())
    return list(toks)


def _all_addresses(monitored):
    addrs = set()
    for p in monitored.list_pools():
        if p.get("address"):
            addrs.add(p["address"].lower())
        addrs.update(_tokens_for_pool(p))
    return list(addrs)


def _tokens_for_pool(p):
    out = []
    for k in ("token0", "token1"):
        v = p.get(k)
        if isinstance(v, dict) and v.get("address"):
            out.append(v["address"].lower())
    return out


async def run(args) -> int:
    print(f"[run_engine_phase_4] starting "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    rng = l3.get_data_time_range(_all_addresses(monitored))
    if rng is None:
        print("ERROR: no L3 data", file=sys.stderr); return 2
    start_ts, end_ts = rng
    span = end_ts - start_ts
    slice_seconds = max(span / max(args.target_windows, 1), 1.0)
    horizon = 2 * slice_seconds
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    tokens = _tokens(monitored)

    # 1. Prefetch real prices ($0 CU). Extend past end by the horizon so
    #    forward windows near the data end still resolve.
    print(f"  prefetching DefiLlama prices for {len(tokens)} tokens "
          f"(period={args.price_period}) ...", flush=True)
    px = DefiLlamaPriceHistory(period=args.price_period)
    px.prefetch(tokens, start_ts, end_ts + horizon)
    cov = px.coverage(tokens)
    print(f"  price coverage: {px.resolved_count}/{len(tokens)} "
          f"({100*cov:.0f}%); unresolved={len(px.unresolved)}", flush=True)
    if cov < 0.5:
        print("WARNING: <50% price coverage — outcomes will be sparse.",
              file=sys.stderr)

    attributor = RealizedOutcomeAttributor(
        px, tokens, horizon_seconds=horizon, entity_mode=args.entity_mode)
    print(f"  outcome mode: {'ENTITY-SPECIFIC' if args.entity_mode else 'WHOLE-BASKET'}",
          flush=True)

    # 2. Full-span replay → in-sample analysis + observability.
    print("  full-span replay (real outcomes) ...", flush=True)
    writer = await replay_to_ledger(
        l3, monitored, out / "phase4_ledger.db",
        start_ts=start_ts, end_ts=end_ts, slice_seconds=slice_seconds,
        attributor=attributor)
    joined = writer.outcomes_joined()
    h5_is = analyze_h5(joined)
    h7_is = analyze_h7(joined)
    perf = compute_performance(joined)
    (out / "phase4_performance.md").write_text(render_markdown(perf),
                                               encoding="utf-8")
    from collections import Counter
    gt = Counter(r["ground_truth"] for r in joined)
    vals = [float(r["outcome_value"] or 0) for r in joined]
    writer.close()

    # 3. Out-of-sample H8/H5 with real outcomes.
    print("  out-of-sample H8/H5 (train/holdout, real outcomes) ...",
          flush=True)
    h8 = await h8_holdout_test(
        l3, monitored, start_ts=start_ts, end_ts=end_ts,
        target_windows=args.target_windows, out_dir=out,
        train_frac=0.7, attributor=attributor)
    h5_oos = h8["holdout_h5"]
    l3.close()

    print()
    print("=" * 70)
    print("PHASE 4 — REAL-OUTCOME HYPOTHESIS VERDICTS")
    print("=" * 70)
    print(f"  price coverage        : {100*cov:.0f}%  ($0 CU, DefiLlama)")
    print(f"  outcome mode          : {'ENTITY-SPECIFIC' if args.entity_mode else 'WHOLE-BASKET'} "
          f"(entity windows={attributor.entity_windows}, "
          f"fallback={attributor.fallback_windows})")
    print(f"  outcome ground_truth  : {dict(gt)}")
    if vals:
        print(f"  outcome_value range   : {min(vals):.3f}..{max(vals):.3f} "
              f"(mean {sum(vals)/len(vals):.3f})")
    print()
    print(f"  H5 IN-SAMPLE          : {h5_is.get('verdict')} "
          f"(orch |corr|={h5_is.get('orchestrator_abs_corr',0):.3f} vs "
          f"best {h5_is.get('best_lens')} "
          f"{h5_is.get('best_lens_abs_corr',0):.3f}, "
          f"{h5_is.get('margin_pp')}pp)")
    print(f"  H5 OUT-OF-SAMPLE      : {h5_oos.get('verdict')} "
          f"(orch |corr|={h5_oos.get('orchestrator_abs_corr',0):.3f} vs "
          f"best {h5_oos.get('best_lens')} "
          f"{h5_oos.get('best_lens_abs_corr',0):.3f}, "
          f"{h5_oos.get('margin_pp')}pp; formal bar ≥15pp)")
    print(f"  H7 (conflicts=alpha)  : {h7_is.get('verdict')} "
          f"(conflict mean={h7_is.get('mean_outcome_conflict')} vs "
          f"{h7_is.get('mean_outcome_noconflict')})")
    print(f"  H8 OUT-OF-SAMPLE      : {h8['h8_verdict']} "
          f"(learned {h8['learned_abs']:.3f} vs static {h8['static_abs']:.3f}, "
          f"{h8['improvement_pp']}pp)")
    print()
    print("  EXECUTION: NONE.  CU: $0.  Data: DefiLlama free.")

    (out / "phase4_verdicts.json").write_text(json.dumps({
        "price_coverage": cov, "outcome_ground_truth": dict(gt),
        "h5_in_sample": h5_is, "h5_out_of_sample": h5_oos,
        "h7_in_sample": h7_is, "h8_out_of_sample": h8,
    }, indent=2, default=str), encoding="utf-8")
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--target-windows", type=int, default=200)
    p.add_argument("--price-period", type=str, default="4h")
    p.add_argument("--entity-mode", action="store_true",
                   help="entity-specific outcomes (Phase 4 run-2) instead of "
                        "whole-basket turbulence")
    p.add_argument("--monitored-pools-path", type=Path,
                   default=Path("layer3_trading_exp/data/run_metadata/monitored_pools.json"))
    p.add_argument("--l3-db-path", type=Path,
                   default=Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db"))
    p.add_argument("--out-dir", type=Path, default=Path("engine/data"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(_parse_args())))
