"""Phase 2 sub-phase 2.2 (D-009) — multi-chain dry-run driver.

Subscribes to newHeads on Base, Arbitrum, and Optimism concurrently inside
one asyncio event loop. Each chain runs its own PoolMonitor (per-chain
pool subset, per-chain sampling cadence). Base retains the full Phase 1
pipeline (OpportunityDetector → FilterPipeline → JSONL logger). Arb and OP
emit block ticks for visibility but do NOT run intra-chain detection in
2.2 — token_registry + multi-chain detector + cross-chain orchestrator
land in sub-phase 2.3 (per spec).

Hardened against the open `--minutes` timer-didn't-terminate failure
(FAILURE_LOG 2026-05-16) by routing the deadline through a background
time-budget watchdog that doesn't depend on `on_block` firing.

Run:
    python -m layer3_trading_exp.scripts.detect_dry_run --minutes 60
    python -m layer3_trading_exp.scripts.detect_dry_run --minutes 5      # smoke
    python -m layer3_trading_exp.scripts.detect_dry_run --blocks 30      # block-bounded
    python -m layer3_trading_exp.scripts.detect_dry_run --chains base    # single chain
    python -m layer3_trading_exp.scripts.detect_dry_run --chains base,arbitrum
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from web3 import AsyncWeb3, Web3
from web3.providers.persistent import WebSocketProvider

# Force UTF-8 stdout for the same reason enumerate_pools does (Windows cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bridge_model import DEFAULT as DEFAULT_BRIDGE, BridgeModel
from ..chain_monitor import (
    DEFAULT_SAMPLING_BY_CHAIN,
    ChainEndpoints,
    ChainMonitor,
    filter_pool_set_by_chain,
)
from ..config import DEFAULT, Config
from ..cross_chain_detector import CrossChainDetector
from ..daily_rollup import write_rollup_csv
from ..filter_pipeline import FilterEvaluation, FilterPipeline
from ..layer3_client import Layer3Client
from ..logger import OpportunityLogger
from ..opportunity_aggregator import OpportunityAggregator
from ..opportunity_detector import (
    GROSS_MARGIN_FLOOR,
    GROSS_MARGIN_FLOOR_CROSS_CHAIN,
    NOTIONAL_USD_FLOOR,
    Opportunity,
    OpportunityDetector,
)
from ..pool_monitor import (
    MULTICALL3_ADDRESS,
    BlockPoolStates,
    GapMarker,
)
from ..pool_set import PoolSet, SUPPORTED_CHAINS
from ..schema import build_log_record
from ..time_budget import time_budget_watchdog
from ..token_registry import DEFAULT as DEFAULT_TOKEN_REGISTRY
from .sync_l3_db import L3SyncRunner


# Per-chain RPC + WS env vars. enumerate_pools.py uses the same mapping;
# kept here independently so this script doesn't depend on the enumerator's
# module-level constants.
ENV_VARS_BY_CHAIN: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "base":     (("BASE_WSS_URL",), ("BASE_RPC_URL",)),
    "arbitrum": (("ARB_WSS_URL", "ARBITRUM_WSS_URL"), ("ARB_RPC_URL", "ARBITRUM_RPC_URL")),
    "optimism": (("OP_WSS_URL", "OPTIMISM_WSS_URL"), ("OP_RPC_URL", "OPTIMISM_RPC_URL")),
}


def _resolve_endpoints(chain_label: str) -> ChainEndpoints:
    import os
    wss_keys, rpc_keys = ENV_VARS_BY_CHAIN[chain_label]
    wss_url = ""
    for k in wss_keys:
        wss_url = os.environ.get(k) or wss_url
        if wss_url:
            break
    rpc_url = ""
    for k in rpc_keys:
        rpc_url = os.environ.get(k) or rpc_url
        if rpc_url:
            break
    return ChainEndpoints(chain_label=chain_label, wss_url=wss_url, rpc_url=rpc_url)


class _AlchemyTransport:
    """WS for newHeads + HTTP for multicalls. Per-chain instance."""

    def __init__(self, ws_w3: AsyncWeb3, http_w3: Web3):
        self._w3 = ws_w3
        self._http_w3 = http_w3
        # Track active newHeads subscription so we can tear it down on
        # re-subscribe. See subscribe_new_heads() comment for full context.
        self._active_sub_id: object | None = None

    async def subscribe_new_heads(self):
        # 2026-05-25 spike RCA: pool_monitor.py inner reconnect loop
        # (line ~480, "subscription-level retry" branch) catches stall
        # errors and re-calls this method on the SAME ws_w3 instance,
        # WITHOUT tearing down the WS connection. Each eth_subscribe
        # call opens a NEW subscription_id; Alchemy keeps delivering
        # newHeads to every subscription until the underlying TCP
        # connection closes OR the client explicitly unsubscribes.
        #
        # Empirical confirmation: 445M newHeads CU/day on Base from
        # 2026-05-22 onward (Alchemy "WebSocket usage by Network" chart),
        # ~129-156 active subscriptions per the math, against an intended
        # baseline of 1 active newHeads subscription per chain.
        #
        # Fix: track the subscription_id and call eth_unsubscribe before
        # re-subscribing. Also unsubscribe on iterator exit (CancelledError,
        # escalation, etc.) so the next reconnect cycle starts clean.
        # All unsubscribe calls are best-effort because the WS may be
        # dead by the time we try.
        if self._active_sub_id is not None:
            try:
                await self._w3.eth.unsubscribe(self._active_sub_id)
            except Exception as e:
                print(f"[_AlchemyTransport] best-effort unsubscribe failed "
                      f"(continuing): {e}", file=sys.stderr, flush=True)
            self._active_sub_id = None

        self._active_sub_id = await self._w3.eth.subscribe("newHeads")
        try:
            async for msg in self._w3.socket.process_subscriptions():
                res = msg["result"]
                num = res["number"]
                ts = res["timestamp"]
                yield {
                    "number": int(num, 16) if isinstance(num, str) else int(num),
                    "timestamp": int(ts, 16) if isinstance(ts, str) else int(ts),
                }
        finally:
            # On any exit path (cancel, escalation, generator close),
            # tear down the active subscription. Failures are expected
            # if the WS is already dead — that's fine, the WS-close
            # will reap the sub server-side too.
            if self._active_sub_id is not None:
                sub_to_close = self._active_sub_id
                self._active_sub_id = None
                try:
                    await self._w3.eth.unsubscribe(sub_to_close)
                except Exception:
                    pass

    async def call_multicall3(self, call_data: bytes, block: int) -> bytes:
        raw = self._http_w3.eth.call({
            "to": self._http_w3.to_checksum_address(MULTICALL3_ADDRESS),
            "data": "0x" + call_data.hex(),
        }, block_identifier=block)
        return bytes(raw)


@asynccontextmanager
async def _alchemy_transport_factory(chain_label: str, wss_url: str, rpc_url: str):
    """Open WS + HTTP web3 instances for one chain. Used by ChainMonitor.run
    as the `transport_factory` argument."""
    if not wss_url:
        raise SystemExit(f"WSS URL is empty for chain {chain_label!r}. "
                         f"Set one of: {ENV_VARS_BY_CHAIN[chain_label][0]}.")
    if not rpc_url:
        raise SystemExit(f"RPC URL is empty for chain {chain_label!r}. "
                         f"Set one of: {ENV_VARS_BY_CHAIN[chain_label][1]}.")
    http_w3 = Web3(Web3.HTTPProvider(rpc_url))
    async with AsyncWeb3(WebSocketProvider(wss_url)) as ws_w3:
        # Per-method Alchemy CU telemetry. Best-effort: never raises into
        # the hot path. Writes batched aggregates to
        # /app/data/run_metadata/rpc_telemetry.db. Surveillance counterpart
        # is in `ai lang/surveillance/rpc_telemetry.py` (commit 7195ea5).
        # Closes the visibility gap for the 2026-05-22/24 CU spikes.
        try:
            from ..rpc_telemetry import wrap_async_web3, wrap_sync_web3
            wrap_async_web3(ws_w3, component=f"chain_monitor_{chain_label}_ws",
                            chain=chain_label)
            wrap_sync_web3(http_w3, component=f"chain_monitor_{chain_label}_http",
                           chain=chain_label)
        except Exception as e:
            print(f"[detect_dry_run] rpc_telemetry wrap failed for "
                  f"{chain_label} (continuing without telemetry): {e}",
                  file=sys.stderr, flush=True)

        yield _AlchemyTransport(ws_w3, http_w3)


def _install_sigterm_handler(loop: asyncio.AbstractEventLoop) -> None:
    """SIGTERM cancels every task. Railway sends SIGTERM on redeploy
    with a 30-second grace window."""
    import signal

    def _handler(signum, frame):
        print(f"[detect_dry_run] received signal {signum}; cancelling all tasks", flush=True)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, AttributeError, OSError):
        pass


async def _daily_rollup_task(cfg) -> None:
    """Background task: at 00:05 UTC each day, write the prior day's rollup CSV.
    On startup, immediately backfill any missing rollups."""
    from datetime import datetime, timedelta, timezone

    def _backfill_missing() -> None:
        log_dir = cfg.log_dir
        rollup_dir = cfg.rollup_dir
        if not log_dir.exists():
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for log_file in sorted(log_dir.glob("*.jsonl")):
            date = log_file.stem
            if date == today:
                continue
            if (rollup_dir / f"{date}.csv").exists():
                continue
            try:
                write_rollup_csv(date, log_dir, rollup_dir)
                print(f"[rollup] backfilled {date}.csv", flush=True)
            except Exception as e:
                print(f"[rollup] backfill {date} FAILED: {type(e).__name__}: {e}", flush=True)

    _backfill_missing()
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=0, minute=5, second=0, microsecond=0) + timedelta(days=1)
        try:
            await asyncio.sleep((next_run - now).total_seconds())
        except asyncio.CancelledError:
            raise
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            out = write_rollup_csv(yesterday, cfg.log_dir, cfg.rollup_dir)
            print(f"[rollup] wrote {out}", flush=True)
        except Exception as e:
            print(f"[rollup] {yesterday} FAILED: {type(e).__name__}: {e}", flush=True)


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs_sorted = sorted(xs)
    idx = max(0, min(len(xs_sorted) - 1, int(q * len(xs_sorted))))
    return xs_sorted[idx]


def _count_by(opps: list[Opportunity], key) -> dict[str, int]:
    out: dict[str, int] = {}
    for o in opps:
        out[key(o)] = out.get(key(o), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _summarize(opportunities: list[Opportunity], wall_seconds: float, blocks_observed: int) -> dict:
    total = len(opportunities)
    if total == 0:
        return {
            "total_opportunities": 0,
            "blocks_observed": blocks_observed,
            "wall_seconds": wall_seconds,
            "opportunities_per_hour_extrapolated": 0.0,
        }
    margins = [o.gross_margin for o in opportunities]
    net_gains = [o.expected_net_gain_usd for o in opportunities]
    return {
        "total_opportunities": total,
        "blocks_observed": blocks_observed,
        "wall_seconds": wall_seconds,
        "opportunities_per_hour_extrapolated": total * 3600.0 / max(wall_seconds, 1.0),
        "margin_min_pct": min(margins) * 100,
        "margin_p50_pct": statistics.median(margins) * 100,
        "margin_p95_pct": _quantile(margins, 0.95) * 100,
        "margin_max_pct": max(margins) * 100,
        "net_gain_usd_min": min(net_gains),
        "net_gain_usd_p50": statistics.median(net_gains),
        "net_gain_usd_max": max(net_gains),
        "by_borrowed_token": _count_by(opportunities, lambda o: o.borrowed_token_symbol),
        "by_protocol_combo": _count_by(opportunities, lambda o: " + ".join(sorted({l.protocol for l in o.legs}))),
    }


async def run(
    *,
    minutes: int | None,
    blocks: int | None,
    chains: tuple[str, ...],
) -> None:
    cfg: Config = DEFAULT

    monitored_path = cfg.run_metadata_dir / "monitored_pools.json"
    if not monitored_path.exists():
        raise SystemExit(
            f"{monitored_path} not found. Run "
            "`python -m layer3_trading_exp.scripts.enumerate_pools` first."
        )
    pool_set = PoolSet.read_from(monitored_path)

    # Per-chain pool counts in the monitored set (after the per-chain filter
    # done inside ChainMonitor). Surface up front so the operator can spot
    # zero-pool chains before any WS work.
    pool_counts_by_chain = {
        c: sum(1 for p in pool_set.pools if p.chain == c)
        for c in chains
    }
    print(f"loaded monitored pool set: {len(pool_set)} pools across "
          f"{len({p.chain for p in pool_set.pools})} chains")
    for c in chains:
        print(f"  {c:10s}: {pool_counts_by_chain[c]} pools  "
              f"(sampling 1/{DEFAULT_SAMPLING_BY_CHAIN.get(c, 1)} blocks)")
    if any(pool_counts_by_chain[c] == 0 for c in chains):
        print("[WARN] one or more chains has zero pools in monitored set", file=sys.stderr)

    # Phase 2 sub-phase 2.5 (D-009): per-chain OpportunityDetector + the
    # CrossChainDetector orchestrator. Each chain in --chains with non-zero
    # monitored pools gets its own intra-chain detector. When 2+ chains
    # are active, a CrossChainDetector wraps them to find inter-chain
    # price-drift opportunities. Filter pipeline + JSONL logger are shared
    # across all sources (intra-chain on each chain + cross-chain).
    per_chain_detectors: dict[str, OpportunityDetector] = {}
    cross_chain_detector: Optional[CrossChainDetector] = None
    filter_pipeline_obj: Optional[FilterPipeline] = None
    l3_client: Optional[Layer3Client] = None
    opp_logger: Optional[OpportunityLogger] = None
    aggregator: Optional[OpportunityAggregator] = None

    # Optionally load the verified Across fee table written by
    # `scripts/verify_across_fees.py` at deploy time. Per I-13 the table is
    # frozen for the run. Falls back to D-006 static defaults if absent
    # (e.g. local dev without internet).
    across_table_path = cfg.run_metadata_dir / "across_fee_table.json"
    if across_table_path.exists():
        try:
            bridge_model_obj: BridgeModel = BridgeModel.load_verified_table(
                across_table_path,
                token_registry=DEFAULT_TOKEN_REGISTRY,
            )
            print(f"bridge_model: loaded verified table from {across_table_path}")
        except Exception as e:
            print(f"[WARN] failed to load {across_table_path} "
                  f"({type(e).__name__}: {e}); falling back to D-006 defaults",
                  file=sys.stderr)
            bridge_model_obj = DEFAULT_BRIDGE
    else:
        bridge_model_obj = DEFAULT_BRIDGE
        print(f"bridge_model: using D-006 static defaults "
              f"({across_table_path} not present)")

    # Build a per-chain OpportunityDetector for every chain with pools.
    for c in chains:
        if pool_counts_by_chain.get(c, 0) == 0:
            continue
        chain_pool_set = filter_pool_set_by_chain(pool_set, c)
        try:
            per_chain_detectors[c] = OpportunityDetector(
                chain_pool_set, chain=c,
                token_registry=DEFAULT_TOKEN_REGISTRY,
            )
        except ValueError as e:
            # Chain registry missing canonical WETH/USDC for this chain —
            # log and skip; intra-chain detection won't run on this chain.
            print(f"[WARN] skipping detector for {c}: {e}", file=sys.stderr)
            continue
        print(f"detector built for {c}:")
        print(f"  pairs:               {per_chain_detectors[c].pair_count}")
        print(f"  arbitrageable pairs: "
              f"{per_chain_detectors[c].arbitrageable_pair_count}")

    # Shared L3 + filter pipeline + logger (any chain's opportunities
    # flow through the same filter rules).
    if per_chain_detectors:
        aggregator = OpportunityAggregator()
        l3_client = Layer3Client(cfg.l3_db_path)
        filter_pipeline_obj = FilterPipeline(
            l3_client, freshness_threshold_seconds=cfg.freshness_threshold_seconds,
        )
        opp_logger = OpportunityLogger(cfg.log_dir, queue_size=10_000)
        print(f"filter pipeline + logger built (shared across chains)")
        print(f"  L3 db: {cfg.l3_db_path}")
        print(f"  log dir: {cfg.log_dir}")
        print(f"  gross margin floor (intra): {GROSS_MARGIN_FLOOR * 100:.2f}%")
        print(f"  gross margin floor (cross): {GROSS_MARGIN_FLOOR_CROSS_CHAIN * 100:.2f}%")
        print(f"  notional per opp:    ${NOTIONAL_USD_FLOOR:,.0f}")
        print(f"  kill switch path: {cfg.kill_switch_path}")

    # Build CrossChainDetector when 2+ chains are active. Single-chain runs
    # (--chains base) skip cross-chain detection entirely.
    if len(per_chain_detectors) >= 2:
        cross_chain_detector = CrossChainDetector(
            per_chain_detectors=per_chain_detectors,
            bridge_model=bridge_model_obj,
            token_registry=DEFAULT_TOKEN_REGISTRY,
        )
        print(f"cross-chain detector built ({cross_chain_detector.route_count} "
              f"scan routes across {len(per_chain_detectors)} chains)")
    else:
        print(f"cross-chain detector DISABLED (only {len(per_chain_detectors)} "
              f"chain(s) with non-zero pools)")
    print()

    all_opps: list[Opportunity] = []
    all_evaluations: list[FilterEvaluation] = []
    detect_times_ms: list[float] = []
    cross_chain_detect_times_ms: list[float] = []
    eval_times_ms: list[float] = []
    blocks_by_chain: dict[str, int] = {c: 0 for c in chains}
    gaps_by_chain: dict[str, int] = {c: 0 for c in chains}
    last_lag_by_chain: dict[str, float] = {c: 0.0 for c in chains}
    cross_chain_opp_count: int = 0
    intra_chain_opp_count_by_chain: dict[str, int] = {c: 0 for c in chains}
    classification_counts = {
        "hard_flagged": 0,
        "soft_flagged_only": 0,
        "weak_only": 0,
        "no_flags": 0,
        "legit_override_seen": 0,
        "degraded": 0,
    }
    # Phase 2 sub-phase 2.5: cache the latest BlockPoolStates per chain so
    # CrossChainDetector.detect() can see all three chains' freshest data
    # whenever any one of them ticks.
    latest_states_by_chain: dict[str, BlockPoolStates] = {}

    async def _process_opportunity(opp: Opportunity) -> None:
        """Run an opportunity through the shared filter pipeline + logger.
        Used by both intra-chain detection (per-chain detector) and
        cross-chain detection (CrossChainDetector)."""
        if filter_pipeline_obj is None or opp_logger is None:
            return
        t1 = time.perf_counter()
        ev = await asyncio.to_thread(filter_pipeline_obj.evaluate, opp)
        eval_times_ms.append((time.perf_counter() - t1) * 1000.0)
        all_evaluations.append(ev)
        if ev.degraded:
            classification_counts["degraded"] += 1
        if ev.has_legit_override:
            classification_counts["legit_override_seen"] += 1
        if ev.hard_flagged:
            classification_counts["hard_flagged"] += 1
        elif ev.soft_flagged:
            classification_counts["soft_flagged_only"] += 1
        elif any(r.fired for r in ev.results):
            classification_counts["weak_only"] += 1
        else:
            classification_counts["no_flags"] += 1
        opp_logger.submit(build_log_record(opp, ev))

    def _make_on_block(chain_label: str):
        async def on_block(states: BlockPoolStates) -> None:
            nonlocal cross_chain_opp_count
            if cfg.kill_switch_path.exists():
                print(f"  KILL SWITCH detected at {cfg.kill_switch_path}; cancelling all tasks")
                raise asyncio.CancelledError("kill switch triggered")
            blocks_by_chain[chain_label] += 1
            last_lag_by_chain[chain_label] = states.ingest_lag_seconds
            latest_states_by_chain[chain_label] = states

            # Intra-chain detection on this chain (if a detector exists).
            chain_opps: list[Opportunity] = []
            chain_detector = per_chain_detectors.get(chain_label)
            if chain_detector is not None:
                t0 = time.perf_counter()
                chain_opps = chain_detector.detect(states)
                detect_times_ms.append((time.perf_counter() - t0) * 1000.0)
                if chain_opps:
                    all_opps.extend(chain_opps)
                    intra_chain_opp_count_by_chain[chain_label] += len(chain_opps)
                    if chain_label == "base" and aggregator is not None:
                        # Phase 1 dedup aggregator is Base-specific; keep it
                        # populated for back-compat with existing analysis.
                        aggregator.record_block(states.block_number, chain_opps)
                    for opp in chain_opps:
                        await _process_opportunity(opp)

            # Cross-chain detection: only fires when 2+ chains have cached
            # states. Avoids re-running cross-chain N times per tick (once
            # per chain) by checking at the end of each chain's block — the
            # latest state set is freshest right after this chain just ticked.
            cross_opps: list[Opportunity] = []
            if cross_chain_detector is not None and len(latest_states_by_chain) >= 2:
                t0 = time.perf_counter()
                cross_opps = cross_chain_detector.detect(latest_states_by_chain)
                cross_chain_detect_times_ms.append((time.perf_counter() - t0) * 1000.0)
                if cross_opps:
                    all_opps.extend(cross_opps)
                    cross_chain_opp_count += len(cross_opps)
                    for opp in cross_opps:
                        await _process_opportunity(opp)

            # Per-tick log line.
            total_new = len(chain_opps) + len(cross_opps)
            if total_new:
                print(f"  [{chain_label}] block {states.block_number:,}  "
                      f"uni={len(states.uniswap_v3)} slip={len(states.slipstream)} "
                      f"aero={len(states.aerodrome)}  "
                      f"lag={states.ingest_lag_seconds:.2f}s  "
                      f"+{len(chain_opps)} intra +{len(cross_opps)} cross")
            else:
                msg_suffix = ""
                if detect_times_ms and chain_detector is not None:
                    msg_suffix = f"  detect_ms={detect_times_ms[-1]:.1f}"
                print(f"  [{chain_label}] block {states.block_number:,}  "
                      f"uni={len(states.uniswap_v3)} slip={len(states.slipstream)} "
                      f"aero={len(states.aerodrome)}  "
                      f"lag={states.ingest_lag_seconds:.2f}s{msg_suffix}")

            if blocks is not None and sum(blocks_by_chain.values()) >= blocks:
                raise asyncio.CancelledError("block budget reached")

        return on_block

    def _make_on_gap(chain_label: str):
        async def on_gap(g: GapMarker) -> None:
            gaps_by_chain[chain_label] += 1
            print(f"  [{chain_label}] GAP: missed {g.missed_blocks} blocks "
                  f"({g.last_block} -> {g.first_block_after_reconnect}): {g.error}")
        return on_gap

    # Build ChainMonitors (no I/O yet).
    chain_monitors: list[ChainMonitor] = []
    for c in chains:
        if pool_counts_by_chain.get(c, 0) == 0:
            print(f"[WARN] skipping chain {c!r}: zero pools in monitored set",
                  file=sys.stderr)
            continue
        endpoints = _resolve_endpoints(c)
        cm = ChainMonitor(
            endpoints=endpoints,
            pool_set=pool_set,
            on_block=_make_on_block(c),
            on_gap=_make_on_gap(c),
            transport_factory=_alchemy_transport_factory,
        )
        chain_monitors.append(cm)
        print(f"chain monitor built: {c}  "
              f"endpoint={endpoints.wss_url[:60]}…  "
              f"pools={cm.pool_count}  sampling=1/{cm.sampling_n}")
    print()

    started_at = time.monotonic()
    started_iso = datetime.now(timezone.utc).isoformat()
    _install_sigterm_handler(asyncio.get_event_loop())

    # Background tasks: L3 sync, daily rollup, time-budget watchdog.
    background_tasks: list[asyncio.Task] = []

    sync_runner: L3SyncRunner | None = None
    if cfg.l3_dump_base_url and cfg.layer3_admin_token:
        try:
            sync_runner = L3SyncRunner.from_config(cfg)
            print(f"l3_sync: enabled; interval={cfg.sync_interval_seconds}s, "
                  f"local_db={cfg.l3_db_path}", flush=True)
            background_tasks.append(asyncio.create_task(
                sync_runner.run_forever(cfg.sync_interval_seconds),
                name="l3_sync",
            ))
        except Exception as e:
            print(f"l3_sync: DISABLED ({type(e).__name__}: {e}); "
                  f"running against existing local DB only", flush=True)
    else:
        print("l3_sync: disabled (L3_DUMP_BASE_URL/LAYER3_ADMIN_TOKEN not set); "
              "reading existing local L3 copy", flush=True)

    background_tasks.append(asyncio.create_task(
        _daily_rollup_task(cfg), name="daily_rollup",
    ))

    # The time-budget watchdog — fixes FAILURE_LOG 2026-05-16. Wakes every 5s,
    # cancels everything when deadline arrives, NOT gated on `on_block`.
    deadline = time.monotonic() + minutes * 60 if minutes else None
    if deadline is not None:
        background_tasks.append(asyncio.create_task(
            time_budget_watchdog(deadline),
            name="time_budget_watchdog",
        ))
        print(f"time_budget_watchdog: ARMED for {minutes} min "
              f"(deadline at monotonic {deadline:.0f})", flush=True)
    else:
        print(f"time_budget_watchdog: not armed (no --minutes)", flush=True)
    print()

    if opp_logger is not None:
        await opp_logger.start()

    chain_tasks = [
        asyncio.create_task(cm.run(), name=f"chain:{cm.chain_label}")
        for cm in chain_monitors
    ]

    try:
        # `return_exceptions=True` so a crash on one chain doesn't kill the others;
        # we collect any exceptions for the summary instead.
        results = await asyncio.gather(*chain_tasks, return_exceptions=True)
        for task, result in zip(chain_tasks, results):
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                print(f"[ERROR] chain task {task.get_name()} crashed: "
                      f"{type(result).__name__}: {result}", file=sys.stderr)
    except asyncio.CancelledError:
        pass
    finally:
        for task in (*background_tasks, *chain_tasks):
            if not task.done():
                task.cancel()
        for task in (*background_tasks, *chain_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if sync_runner is not None:
            sync_runner.close()
        if opp_logger is not None:
            await opp_logger.stop()

    wall = time.monotonic() - started_at
    summary: dict = {
        "started_at": started_iso,
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": wall,
        "chains_active": list(chains),
        "blocks_by_chain": blocks_by_chain,
        "gaps_by_chain": gaps_by_chain,
        "last_lag_seconds_by_chain": last_lag_by_chain,
        "intra_chain_opp_count_by_chain": intra_chain_opp_count_by_chain,
        "cross_chain_opp_count": cross_chain_opp_count,
    }

    if per_chain_detectors and opp_logger is not None:
        total_summary = _summarize(all_opps, wall, sum(blocks_by_chain.values()))
        summary["totals"] = total_summary
        if aggregator is not None:
            aggregator.finalize()
            summary["base_dedup"] = aggregator.summary()
        if detect_times_ms:
            summary["intra_detect_ms_p50"] = statistics.median(detect_times_ms)
            summary["intra_detect_ms_max"] = max(detect_times_ms)
        if cross_chain_detect_times_ms:
            summary["cross_detect_ms_p50"] = statistics.median(cross_chain_detect_times_ms)
            summary["cross_detect_ms_max"] = max(cross_chain_detect_times_ms)
        if all_evaluations:
            n = len(all_evaluations)
            classification_rate = {
                "evaluations_total": n,
                "hard_flagged_pct": classification_counts["hard_flagged"] * 100 / n,
                "soft_flagged_only_pct": classification_counts["soft_flagged_only"] * 100 / n,
                "weak_only_pct": classification_counts["weak_only"] * 100 / n,
                "no_flags_pct": classification_counts["no_flags"] * 100 / n,
                "legit_override_seen_pct": classification_counts["legit_override_seen"] * 100 / n,
                "degraded_pct": classification_counts["degraded"] * 100 / n,
            }
            if eval_times_ms:
                eval_sorted = sorted(eval_times_ms)
                classification_rate["eval_ms_p50"] = statistics.median(eval_sorted)
                classification_rate["eval_ms_p95"] = eval_sorted[int(0.95 * len(eval_sorted))]
                classification_rate["eval_ms_max"] = max(eval_sorted)
            summary["classification"] = classification_rate
        summary["log_volume"] = {
            "records_written": opp_logger.records_written,
            "bytes_written": opp_logger.bytes_written,
            "overflow_count": opp_logger.overflow_count,
        }
        if l3_client is not None:
            l3_client.close()

    print()
    print("=" * 60)
    print("dry-run summary")
    print("=" * 60)
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"  {k:42}  {v:.3f}")
        elif isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk:38}  {vv}")
        else:
            print(f"  {k:42}  {v}")

    out_path = cfg.run_metadata_dir / f"dry_run_{int(started_at)}.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    grp = p.add_mutually_exclusive_group(required=False)
    grp.add_argument("--minutes", type=int, default=10_080,
                     help="run for N minutes (default 10080 = 7 days)")
    grp.add_argument("--blocks", type=int,
                     help="run until N blocks observed across all chains "
                          "(overrides --minutes)")
    p.add_argument("--chains", type=str, default="base,arbitrum,optimism",
                   help="comma-separated chain labels to monitor. "
                        f"Choices: {','.join(SUPPORTED_CHAINS)}. Default: all 3.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    chains = tuple(c.strip() for c in args.chains.split(",") if c.strip())
    bad = [c for c in chains if c not in SUPPORTED_CHAINS]
    if bad:
        raise SystemExit(
            f"unsupported chain(s): {bad}. Allowed: {SUPPORTED_CHAINS}"
        )
    asyncio.run(run(
        minutes=None if args.blocks is not None else args.minutes,
        blocks=args.blocks,
        chains=chains,
    ))
