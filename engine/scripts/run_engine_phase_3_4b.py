"""Sub-phase 3.4b smoke driver — learner + dynamic weights + dry-run
router + Phase-3 hypothesis analysis.

Pipeline:
  1. Replay → decisions + conflicts + outcomes in the live ledger
     (same as 3.4a, reused).
  2. Learner pulls outcomes, updates per-regime per-lens weights (I-19),
     persists to learned_weights.json.
  3. Re-replay with the LEARNED weights (WeightingEngine) → second ledger,
     to show weights moved + their effect.
  4. Analyze H5 / H7 from the ledger; report H6 (regime spot-check) +
     H8 (weights changed measurably + persist) verdicts.
  5. Route decisions through the DRY-RUN strategy router → intended
     orders file. EXECUTION IS DISABLED.

All verdicts are PROXY-validated per D-034.

Usage: python -m engine.scripts.run_engine_phase_3_4b --target-windows 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..adapters.l3_corpus_phase2 import Phase2L3CorpusAdapter
from ..adapters.monitored_set_phase2 import Phase2MonitoredSetAdapter
from ..core.event_bus import EventBus
from ..core.replay_clock import ReplayClock
from ..feedback.learner import Learner
from ..feedback.outcome_attributor import OutcomeAttributor
from ..feedback.outcome_ledger import LedgerWriter
from ..feedback.phase3_analysis import analyze_h5, analyze_h7
from ..lenses.graph.lens import GraphLens
from ..lenses.information.lens import InformationLens
from ..lenses.stochastic.lens import StochasticLens
from ..orchestrator.conflict_engine import ConflictEngine, ConflictSignal
from ..orchestrator.decision_engine import Decision, DecisionEngine
from ..orchestrator.orchestrator import Orchestrator
from ..orchestrator.regime_engine import RegimeEngine
from ..orchestrator.weighting_engine import WeightingEngine
from ..synthesis.cross_lens_engine import CrossLensEngine
from ..execution.strategy_router import StrategyRouter, write_dry_run_orders


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


async def _replay_to_ledger(l3, monitored, clock, win, ledger_path,
                            *, weighting_engine=None):
    """One full replay → write decisions+conflicts+outcomes to a fresh
    ledger. Returns (LedgerWriter, decisions_list, action_counts)."""
    if ledger_path.exists():
        ledger_path.unlink()
    writer = LedgerWriter(ledger_path)
    bus = EventBus(default_queue_size=50_000)
    lenses = [
        GraphLens(monitored_set=monitored, l3_corpus=l3),
        StochasticLens(monitored_set=monitored, data_source=l3),
        InformationLens(monitored_set=monitored, data_source=l3),
    ]
    cross = CrossLensEngine(window_seconds=win)
    regime = RegimeEngine(window_seconds=win)
    conflict = ConflictEngine(window_seconds=win)
    orch = Orchestrator(window_seconds=win, regime_aware=True,
                        weighting_engine=weighting_engine)
    decider = DecisionEngine()

    decisions: list[Decision] = []
    stop_event = asyncio.Event()
    consumer_stop = asyncio.Event()

    async def _decision_tap():
        sub = await bus.subscribe(DecisionEngine.OUTPUT_TOPIC, name="dtap")
        while True:
            try:
                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if consumer_stop.is_set():
                    empty = 0
                    while empty < 3:
                        try:
                            msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                            if isinstance(msg, Decision):
                                writer.write_decision(msg, time.time())
                                decisions.append(msg)
                            empty = 0
                        except asyncio.TimeoutError:
                            empty += 1
                    return
                continue
            if isinstance(msg, Decision):
                writer.write_decision(msg, time.time())
                decisions.append(msg)

    async def _conflict_tap():
        sub = await bus.subscribe(ConflictEngine.OUTPUT_TOPIC, name="ctap")
        while True:
            try:
                msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if consumer_stop.is_set():
                    empty = 0
                    while empty < 3:
                        try:
                            msg = await asyncio.wait_for(sub.queue.get(), timeout=0.5)
                            if isinstance(msg, ConflictSignal):
                                writer.write_conflict(msg, time.time())
                            empty = 0
                        except asyncio.TimeoutError:
                            empty += 1
                    return
                continue
            if isinstance(msg, ConflictSignal):
                writer.write_conflict(msg, time.time())

    consumer_tasks = [asyncio.create_task(_decision_tap()),
                      asyncio.create_task(_conflict_tap())]
    engine_tasks = [
        asyncio.create_task(cross.run(bus, stop_event=stop_event)),
        asyncio.create_task(regime.run(bus, stop_event=stop_event)),
        asyncio.create_task(conflict.run(bus, stop_event=stop_event)),
        asyncio.create_task(orch.run(bus, stop_event=stop_event)),
        asyncio.create_task(decider.run(bus, stop_event=stop_event)),
    ]
    await asyncio.sleep(0.2)
    try:
        for window in clock.windows():
            for lens in lenses:
                await lens.scan_replay_window(bus, window)
            await asyncio.sleep(0)
    finally:
        await asyncio.sleep(0.5)
        stop_event.set()
        try:
            await asyncio.wait_for(asyncio.gather(*engine_tasks, return_exceptions=True), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.5)
        consumer_stop.set()
        try:
            await asyncio.wait_for(asyncio.gather(*consumer_tasks, return_exceptions=True), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        for t in engine_tasks + consumer_tasks:
            if not t.done():
                t.cancel()
        for t in engine_tasks + consumer_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    # Attribute outcomes (forward-looking).
    attr = OutcomeAttributor(l3, horizon_seconds=2 * win)
    attr.attribute_all(writer)
    return writer, decisions, dict(decider.action_counts)


async def run(args) -> int:
    print(f"[run_engine_phase_3_4b] starting "
          f"{datetime.now(timezone.utc).isoformat()}", flush=True)
    monitored = Phase2MonitoredSetAdapter.from_path(args.monitored_pools_path)
    l3 = Phase2L3CorpusAdapter.from_path(args.l3_db_path)
    rng = l3.get_data_time_range(_all_addresses(monitored))
    if rng is None:
        print("ERROR: no L3 data", file=sys.stderr); return 2
    start_ts, end_ts = rng
    slice_seconds = max((end_ts - start_ts) / max(args.target_windows, 1), 1.0)
    win = slice_seconds
    out = args.out_dir

    def _clock():
        return ReplayClock(start_ts, end_ts, slice_seconds=slice_seconds,
                           lookback_seconds=slice_seconds)

    # --- Pass 1: static weights → ledger ---
    print("  pass 1: replay with STATIC weights ...", flush=True)
    writer1, decisions1, actions1 = await _replay_to_ledger(
        l3, monitored, _clock(), win, out / "outcome_ledger.db")
    joined1 = writer1.outcomes_joined()

    # --- Learn ---
    learner = Learner(blend_alpha=0.6)
    learned = learner.learn(writer1)
    weights_path = out / "learned_weights.json"
    learner.persist(learned, weights_path)
    we = WeightingEngine(weights_path)
    print(f"  learner: {learner.last_summary}; using_learned={we.using_learned}",
          flush=True)

    # --- Pass 2: re-replay with LEARNED weights (demonstrates dynamic
    #     weighting is wired end-to-end; H8 supporting evidence) ---
    print("  pass 2: replay with LEARNED weights ...", flush=True)
    writer2, decisions2, actions2 = await _replay_to_ledger(
        l3, monitored, _clock(), win, out / "outcome_ledger_learned.db",
        weighting_engine=we)
    writer2.close()

    # Measure weight change vs static prior.
    from ..orchestrator.regime_weights import REGIME_WEIGHTS
    changes = {}
    for regime in REGIME_WEIGHTS:
        prior = REGIME_WEIGHTS[regime]
        post = learned[regime]
        delta = sum(abs(post[l] - prior[l]) for l in prior)
        changes[regime] = round(delta, 4)

    # --- Analysis (H5, H7) ---
    h5 = analyze_h5(joined1)
    h7 = analyze_h7(joined1)

    # --- Dry-run router over pass-1 decisions ---
    router = StrategyRouter()  # execution disabled
    orders = [router.route(d) for d in decisions1]
    n_orders = write_dry_run_orders(orders, out / "sub_phase_3_4b_dry_run_orders.jsonl")

    elapsed = 0.0
    print()
    print("=" * 70)
    print("sub-phase 3.4b smoke summary (learner + dynamic weights + router)")
    print("=" * 70)
    print(f"  decisions (pass 1)    : {len(decisions1)}")
    print(f"  outcomes attributed   : {writer1.count('outcomes')}")
    print(f"  conflicts logged      : {writer1.count('conflicts')}")
    print(f"  action mix STATIC     : {actions1}")
    print(f"  action mix LEARNED    : {actions2}")
    print()
    print(f"  LEARNER weight change (L1 delta from static prior, per regime):")
    for r, d in changes.items():
        print(f"    {r:14s} Δ={d}")
    print(f"  learned weights persisted: {weights_path}")
    print(f"  weighting engine source  : {we.status()['source']} "
          f"(stale={we.status()['stale']})")
    print()
    print(f"  H5 (orch > best lens) : {h5['verdict']}")
    if "orchestrator_abs_corr" in h5:
        print(f"     orch |corr|={h5['orchestrator_abs_corr']:.3f}  "
              f"best lens={h5['best_lens']} |corr|={h5['best_lens_abs_corr']:.3f}  "
              f"margin={h5['margin_pp']}pp")
    print(f"  H6 (regime real)      : SUPPORTED-PROXY (regime spot-check "
          f"20/20 intuitive — D-031)")
    print(f"  H7 (conflicts=alpha)  : {h7['verdict']}")
    if "mean_outcome_conflict" in h7:
        print(f"     conflict mean outcome={h7['mean_outcome_conflict']}  "
              f"no-conflict={h7['mean_outcome_noconflict']}  "
              f"rank_effect={h7['rank_effect']}")
    weights_moved = any(v > 0.001 for v in changes.values())
    print(f"  H8 (system learns)    : "
          f"{'SUPPORTED-PROXY' if weights_moved else 'NOT-SUPPORTED'} "
          f"(weights moved measurably + persist; rigorous learned-vs-initial "
          f"replay deferred to 3.5)")
    print()
    print(f"  DRY-RUN orders written: {n_orders} "
          f"(holds dropped {router.dropped_holds}); EXECUTION DISABLED")
    print()

    # Persist the analysis for the LOOP writeup.
    (out / "sub_phase_3_4b_analysis.json").write_text(json.dumps({
        "h5": h5, "h7": h7, "weight_changes": changes,
        "n_decisions": len(decisions1),
        "learner_summary": learner.last_summary,
    }, indent=2, default=str), encoding="utf-8")

    writer1.close()
    l3.close()
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
