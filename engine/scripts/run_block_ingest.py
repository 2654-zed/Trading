"""Tier-1 confirmation-side driver (SEPARATE process from the mempool
capture; never touches the bloXroute WebSocket).

Follows the ETH chain head via a free public JSON-RPC endpoint and archives
one confirmation row per transaction to hourly-rotated
confirmations_YYYYMMDD_HH.jsonl — the "after" side that the Phase-4 DuckDB
join matches against the mempool "before" side.

No credentials of any kind: the endpoints are public and read-only.

Usage:
    python -m engine.scripts.run_block_ingest --minutes 60
    python -m engine.scripts.run_block_ingest --minutes 0   # until Ctrl-C
    python -m engine.scripts.run_block_ingest --rpc https://my-node/...
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.block_ingest import DEFAULT_RPCS, BlockPoller
from ..bloxroute.clock_health import query_clock_status
from ..bloxroute.jsonl_io import (
    HealthWriter, RotatingJsonlWriter, acquire_singleton_lock,
)


def _resolve_rpcs(cli_rpcs: list[str] | None) -> list[str]:
    """CLI --rpc flags win; else ETH_RPC_URLS (comma-separated) env; else
    the built-in free public defaults."""
    if cli_rpcs:
        return cli_rpcs
    env = os.environ.get("ETH_RPC_URLS", "").strip()
    if env:
        return [u.strip() for u in env.split(",") if u.strip()]
    return list(DEFAULT_RPCS)


def run(args) -> int:
    # Process-wide socket default: urlopen's per-call timeout covers TCP
    # connect + read, but a global default also bounds anything that slips
    # past it. (Known residual: OS-level DNS resolution has no Python-level
    # timeout; Windows' resolver applies its own ~10s bound. Endpoint
    # rotation is the recovery path either way.)
    socket.setdefaulttimeout(max(args.rpc_timeout, 5.0))
    rpcs = _resolve_rpcs(args.rpc)
    # one ingest per archive dir (no duplicate confirmation writers)
    lock_fh = acquire_singleton_lock(args.out_dir)
    if lock_fh is None:
        print(f"ERROR: another block ingest already holds the lock on "
              f"{args.out_dir}. Exiting.", file=sys.stderr)
        return 3
    latest_clock = {"synced": None, "offset_ms": None}
    # Closeables/poller init to None so the finally + final print can guard
    # them even if an open() below fails (no leaked handle on startup error).
    writer = health = clock_writer = poller = None

    def _probe_clock(reason: str) -> None:
        # READ-ONLY w32tm probe; failure is logged, never fatal.
        try:
            status = query_clock_status(5.0)
        except Exception as e:  # noqa: BLE001 — probe must never kill ingest
            clock_writer.event("clock_status", reason=reason,
                               clock_synced=None, clock_offset_ms=None,
                               error=f"probe raised {type(e).__name__}: {e}")
            return
        latest_clock["synced"] = status.get("clock_synced")
        latest_clock["offset_ms"] = status.get("clock_offset_ms")
        clock_writer.event("clock_status", reason=reason, **status)

    started = time.monotonic()
    deadline = started + args.minutes * 60 if args.minutes > 0 else None
    last_clock = started

    try:
        writer = RotatingJsonlWriter(args.out_dir, "confirmations_")
        health = HealthWriter(args.out_dir / "block_health.jsonl")
        clock_writer = HealthWriter(args.out_dir / "clock_health.jsonl")
        poller = BlockPoller(rpcs=rpcs, health=health.event,
                             timeout=args.rpc_timeout,
                             max_blocks_per_cycle=args.max_blocks_per_cycle)
        print(f"[block_ingest] following ETH head via {rpcs[0]} "
              f"(+{len(rpcs)-1} fallback) "
              f"({datetime.now(timezone.utc).isoformat()})", flush=True)
        print(f"  archive dir: {args.out_dir}", flush=True)
        _probe_clock("startup")
        if latest_clock["synced"] is not True:
            print(f"  WARNING: clock_synced={latest_clock['synced']} "
                  "(NTP not confirmed; rows record this honestly)", flush=True)
        while deadline is None or time.monotonic() < deadline:
            rows = poller.poll(clock_synced=latest_clock["synced"],
                               clock_offset_ms=latest_clock["offset_ms"])
            for row in rows:
                writer.write(row)
            if rows:
                print(f"  through block {poller.last_processed}: "
                      f"+{len(rows)} rows (total {writer.written})", flush=True)
            now = time.monotonic()
            if now - last_clock >= args.clock_interval:
                _probe_clock("periodic")
                last_clock = now
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\ninterrupted.", flush=True)
    finally:
        for _c in (writer, health, clock_writer, lock_fh):
            if _c is not None:
                _c.close()   # closing lock_fh releases the singleton lock

    rows_written = writer.written if writer is not None else 0
    last_block = poller.last_processed if poller is not None else None
    print(f"\n=== FINAL === rows={rows_written} last_block={last_block} "
          f"elapsed={(time.monotonic()-started)/60:.1f} min", flush=True)
    return 0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=60.0,
                   help="run duration; 0 = until Ctrl-C")
    p.add_argument("--poll-interval", type=float, default=6.0,
                   help="seconds between head polls (ETH blocks ~12s)")
    p.add_argument("--rpc", action="append", default=None,
                   help="JSON-RPC endpoint (repeatable; overrides defaults)")
    p.add_argument("--rpc-timeout", type=float, default=10.0)
    p.add_argument("--max-blocks-per-cycle", type=int, default=30)
    p.add_argument("--clock-interval", type=float, default=300.0,
                   help="seconds between read-only w32tm clock-health probes")
    p.add_argument("--out-dir", type=Path,
                   default=Path("engine/data/confirmations"))
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(run(_parse_args()))
