"""bloXroute ETH mempool capture driver.

Connects to the bloXroute Cloud-API WebSocket, subscribes to the pending-tx
stream, ARCHIVES every tx to JSONL (the durable proprietary dataset), and
runs the three honest analyzers live (competition density, deductive
third-party-transferFrom flag, microstructure stats).

CREDENTIALS ARE NEVER IN CODE OR CHAT. Set them locally before running:

    setx BLOXROUTE_AUTH_HEADER "<your bloXroute Authorization header>"
    # optional, defaults to wss://api.blxrbdn.com/ws
    setx BLOXROUTE_WS_URL "wss://<regional-endpoint>/ws"

...or create engine/data/bloxroute_config.json (gitignored):
    { "auth_header": "...", "ws_url": "wss://api.blxrbdn.com/ws" }

Usage:
    python -m engine.scripts.run_mempool_capture --minutes 60
    python -m engine.scripts.run_mempool_capture --minutes 0   # run until Ctrl-C
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bloxroute.clock_health import query_clock_status
from ..bloxroute.jsonl_io import (
    HealthWriter, RotatingJsonlWriter, acquire_singleton_lock,
)
from ..bloxroute.mempool_capture import (
    DEFAULT_SOURCE, CompetitionTracker, MicrostructureStats,
    is_third_party_transfer_from, parse_bloxroute_tx,
)

DEFAULT_WS = "wss://api.blxrbdn.com/ws"
SUBSCRIBE = {
    "id": 1, "method": "subscribe",
    "params": ["newTxs", {"include": ["tx_hash", "tx_contents"]}],
}


def _load_credentials(cfg_path: Path) -> tuple[str, str]:
    auth = os.environ.get("BLOXROUTE_AUTH_HEADER")
    url = os.environ.get("BLOXROUTE_WS_URL")
    if (not auth or not url) and cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            auth = auth or cfg.get("auth_header")
            url = url or cfg.get("ws_url")
        except (ValueError, OSError):
            pass
    return auth, (url or DEFAULT_WS)


# Archive + health writers live in ..bloxroute.jsonl_io (shared with the
# Tier-1 confirmation ingest so both processes have identical forensic
# write semantics: append-only rotation; always-flushed health events).


async def run(args) -> int:
    import websockets
    auth, url = _load_credentials(args.config)
    if not auth:
        print("ERROR: no bloXroute auth header found.\n"
              "  Set BLOXROUTE_AUTH_HEADER (env) or create "
              f"{args.config} with {{'auth_header': '...'}}.\n"
              "  Credentials must NOT be pasted into chat.", file=sys.stderr)
        return 2

    # SINGLE-STREAM GUARD: the $300 bloXroute tier allows ONE concurrent
    # stream. Refuse to start if another capture already holds the lock —
    # this makes a second stream impossible regardless of how it's launched
    # (auto-start, manual double-click, leftover process).
    lock_fh = acquire_singleton_lock(args.out_dir)
    if lock_fh is None:
        print("ERROR: another mempool capture already holds the lock on "
              f"{args.out_dir}.\n  Refusing to open a SECOND bloXroute stream "
              "(the $300 tier allows exactly one). Exiting.", file=sys.stderr)
        return 3

    comp = CompetitionTracker(window_s=args.competition_window)
    stats = MicrostructureStats()
    seq_counter = itertools.count()                 # Tier-0: monotonic per-event seq
    last_seq = -1
    # Latest clock-discipline sample, stamped onto every captured row.
    latest_clock = {"synced": None, "offset_ms": None}
    # Closeables init to None so the finally can guard them even if an open()
    # below fails — no leaked handle on a startup error.
    archive = flag_fh = health = clock_writer = None

    async def _probe_clock(reason: str) -> None:
        # READ-ONLY w32tm probe, run OFF the event loop; must never block,
        # hang, or crash the capture — a probe failure is logged, not fatal.
        try:
            status = await asyncio.to_thread(query_clock_status, 5.0)
        except Exception as e:  # noqa: BLE001 — clock probe must never kill capture
            clock_writer.event("clock_status", reason=reason,
                               clock_synced=None, clock_offset_ms=None,
                               error=f"probe raised {type(e).__name__}: {e}")
            return
        latest_clock["synced"] = status.get("clock_synced")
        latest_clock["offset_ms"] = status.get("clock_offset_ms")
        clock_writer.event("clock_status", reason=reason, **status)

    host = urlsplit(url).hostname or url
    started = time.monotonic()
    deadline = started + args.minutes * 60 if args.minutes > 0 else None
    last_stats = started
    last_clock = started
    backoff = 1.0
    attempt = 0
    try:
        archive = RotatingJsonlWriter(args.out_dir, "mempool_")
        flag_fh = (args.out_dir / "third_party_transferfrom_flags.jsonl").open(
            "a", encoding="utf-8")
        health = HealthWriter(args.out_dir / "feed_health.jsonl")
        clock_writer = HealthWriter(args.out_dir / "clock_health.jsonl")
        print(f"[mempool_capture] connecting to {url} "
              f"({datetime.now(timezone.utc).isoformat()})", flush=True)
        print(f"  archive dir: {args.out_dir}", flush=True)
        await _probe_clock("startup")
        if latest_clock["synced"] is not True:
            print(f"  WARNING: clock_synced={latest_clock['synced']} "
                  "(NTP not confirmed; rows record this honestly)", flush=True)
        while deadline is None or time.monotonic() < deadline:
            attempt += 1
            if attempt > 1:
                health.event("reconnect", attempt=attempt, last_seq=last_seq,
                             backoff_s=round(backoff, 1))
            try:
                async with websockets.connect(
                    url, additional_headers={"Authorization": auth},
                    ping_interval=20, max_size=None,
                ) as ws:
                    health.event("connect", attempt=attempt, host=host)
                    try:
                        await ws.send(json.dumps(SUBSCRIBE))
                    except Exception as e:  # noqa: BLE001 — re-raised to reconnect handler
                        health.event("subscribe_error", attempt=attempt,
                                     detail=f"{type(e).__name__}: {e}")
                        raise
                    health.event("subscribed", attempt=attempt)
                    print("  subscribed to newTxs. capturing...", flush=True)
                    backoff = 1.0
                    await _probe_clock("connect")
                    while deadline is None or time.monotonic() < deadline:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        recv_ts = time.time()   # Tier-0: stamp at socket receipt, BEFORE decode
                        seq = next(seq_counter)
                        last_seq = seq
                        try:
                            msg = json.loads(raw)
                        except ValueError as e:
                            # Loud failure over silent drop: a malformed frame is
                            # logged (flushed) with its seq, then we skip ONLY it.
                            health.event("parse_error", seq=seq, recv_ts=recv_ts,
                                         detail=f"json decode failed: {type(e).__name__}")
                            continue
                        tx = parse_bloxroute_tx(
                            msg, recv_ts=recv_ts, seq=seq, source=DEFAULT_SOURCE,
                            clock_synced=latest_clock["synced"],
                            clock_offset_ms=latest_clock["offset_ms"])
                        if not tx:
                            continue        # non-tx control frame (e.g. subscription ack)
                        archive.write(tx)
                        stats.observe(tx)
                        comp.observe(tx)
                        flag = is_third_party_transfer_from(tx["from"], tx["input"])
                        if flag:
                            stats.third_party_transfer_from += 1
                            flag_fh.write(json.dumps(
                                {"hash": tx["hash"], "initiator": flag["initiator"],
                                 "owner_debited": flag["from"], "to": flag["to"],
                                 "token": tx["to"], "recv_ts": tx["recv_ts"]},
                                default=str) + "\n")
                            flag_fh.flush()
                        now = time.monotonic()
                        if now - last_stats >= args.stats_interval:
                            _print_stats(archive, stats, comp, now - started)
                            last_stats = now
                        if now - last_clock >= args.clock_interval:
                            await _probe_clock("periodic")
                            last_clock = now
            except asyncio.TimeoutError:
                # A 60s silence forces a reconnect (stall). Record the gap.
                health.event("gap", last_seq=last_seq,
                             detail="no messages in 60s; reconnecting")
                print("  (no messages 60s — reconnecting)", flush=True)
            except websockets.ConnectionClosed as e:
                # extract close code without the deprecated ConnectionClosed.code
                _rcvd = getattr(e, "rcvd", None)
                _code = getattr(_rcvd, "code", None) if _rcvd is not None else None
                health.event("disconnect", last_seq=last_seq, code=_code,
                             detail=f"connection closed: {type(e).__name__}")
                print(f"  disconnected: {type(e).__name__}; "
                      f"reconnect in {backoff:.0f}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as e:  # noqa: BLE001 — log + reconnect, don't crash capture
                health.event("error", last_seq=last_seq,
                             detail=f"{type(e).__name__}: {e}")
                print(f"  stream error: {type(e).__name__}: {e}; "
                      f"reconnect in {backoff:.0f}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
    finally:
        # Guarantees close+flush even on Ctrl-C/cancellation (the normal way a
        # --minutes 0 run ends) AND even if a writer failed to open above. The
        # archive's buffered tail and always-flushed logs survive any exit path.
        for _c in (archive, flag_fh, health, clock_writer, lock_fh):
            if _c is not None:
                _c.close()   # closing lock_fh releases the single-stream lock

    print("\n=== FINAL ===", flush=True)
    _print_stats(archive, stats, comp, time.monotonic() - started)
    return 0


def _print_stats(archive, stats, comp, elapsed):
    s = stats.snapshot()
    print(f"\n[{elapsed/60:.1f} min] archived={archive.written}  "
          f"txs={s['total_txs']}  rate={s['total_txs']/max(elapsed,1):.0f}/s")
    print(f"  third-party transferFrom flags : {s['third_party_transferFrom']}")
    print(f"  competition: max racers on one opportunity = {comp.max_racers_seen}, "
          f"contended events = {comp.contended_events}")
    print(f"  priority fee gwei p50/p90      : {s['priority_fee_gwei_p50']} / "
          f"{s['priority_fee_gwei_p90']}")
    print(f"  top methods   : {s['top_methods'][:4]}")
    print(f"  top dest      : {[d for d,_ in s['top_destinations'][:4]]}")


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=60.0,
                   help="run duration; 0 = until Ctrl-C")
    p.add_argument("--stats-interval", type=float, default=60.0)
    p.add_argument("--clock-interval", type=float, default=300.0,
                   help="seconds between read-only w32tm clock-health probes")
    p.add_argument("--competition-window", type=float, default=2.0)
    p.add_argument("--out-dir", type=Path, default=Path("engine/data/mempool"))
    p.add_argument("--config", type=Path,
                   default=Path("engine/data/bloxroute_config.json"))
    return p.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run(_parse_args())))
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        sys.exit(0)
