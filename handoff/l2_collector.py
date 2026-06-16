#!/usr/bin/env python3
"""Self-contained CEX Level-2 order-book collector (hand-off build).

Records the RAW Level-2 order-book stream LOSSLESSLY from one or more
centralized exchanges concurrently, to hourly JSONL (one file per exchange).
Book reconstruction + features (imbalance / "price pressure") are done OFFLINE
from the recordings — see L2_DATA_SCHEMA.md. Public market data only: NO API
keys, no account access, nothing credential-sensitive.

SETUP (any OS with Python 3.9+):
    pip install websockets
    python l2_collector.py --minutes 0                       # all defaults, until Ctrl-C
    python l2_collector.py --minutes 0 --exchanges coinbase,kraken,binanceus,okx \
        --pairs BTC-USD,ETH-USD --out-dir ./l2_data

OUTPUT: ./l2_data/l2_<exchange>_YYYYMMDD_HH.jsonl  +  l2_feed_health.jsonl
Each row: {exchange, recv_ts (local unix, stamped before decode), seq
(monotonic per exchange), pair, kind (snapshot|update|control|other), msg (the
raw exchange message)}.

WARNING — L2 is HEAVY (gigabytes/day). Watch disk; narrow --pairs/--exchanges
or run bounded sessions (--minutes 60) as needed.

NOTE — `recv_ts` is THIS machine's clock. NTP-sync the machine for trustworthy
timestamps. For precise event ordering use each exchange's own timestamp in
`msg` where present. (Our internal build adds an NTP-state stamp + a
single-instance lock; omitted here to keep this one file dependency-light —
just don't run two copies into the same --out-dir.)
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ── lossless hourly JSONL writer ────────────────────────────────────────────
class RotatingJsonlWriter:
    def __init__(self, out_dir: Path, prefix: str, flush_every: int = 200):
        self._dir = Path(out_dir); self._dir.mkdir(parents=True, exist_ok=True)
        self._prefix = prefix; self._flush_every = flush_every
        self._fh = None; self._hour = None; self.written = 0

    def write(self, row: dict) -> None:
        hk = datetime.now(timezone.utc).strftime("%Y%m%d_%H")
        if hk != self._hour:
            if self._fh:
                self._fh.close()
            self._fh = (self._dir / f"{self._prefix}{hk}.jsonl").open("a", encoding="utf-8")
            self._hour = hk
        self._fh.write(json.dumps(row, default=str) + "\n")
        self.written += 1
        if self.written % self._flush_every == 0:
            self._fh.flush()

    def close(self):
        if self._fh:
            self._fh.flush(); self._fh.close()


class HealthWriter:
    def __init__(self, path: Path):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def event(self, event: str, **detail) -> None:
        self._fh.write(json.dumps({"event": event, "ts": time.time(), **detail}) + "\n")
        self._fh.flush()

    def close(self):
        try:
            self._fh.close()
        except OSError:
            pass


# ── exchange adapters (ws url + subscribe + best-effort normalize) ──────────
class Adapter:
    name = "base"

    def __init__(self, pairs, depth=25):
        self.pairs = pairs; self.depth = depth

    def ws_url(self): raise NotImplementedError
    def subscribe_messages(self): return []
    def normalize(self, msg): return {"pair": None, "kind": "other"}


class CoinbaseAdapter(Adapter):
    name = "coinbase"
    def ws_url(self): return "wss://ws-feed.exchange.coinbase.com"
    def subscribe_messages(self):
        return [{"type": "subscribe", "product_ids": list(self.pairs),
                 "channels": ["level2_batch"]}]
    def normalize(self, msg):
        t = msg.get("type")
        if t == "snapshot": return {"pair": msg.get("product_id"), "kind": "snapshot"}
        if t in ("l2update", "l2update_batch"): return {"pair": msg.get("product_id"), "kind": "update"}
        return {"pair": msg.get("product_id"), "kind": "control"}


class KrakenAdapter(Adapter):
    name = "kraken"
    _MAP = {"BTC-USD": "XBT/USD", "ETH-USD": "ETH/USD", "SOL-USD": "SOL/USD"}
    def ws_url(self): return "wss://ws.kraken.com"
    def subscribe_messages(self):
        return [{"event": "subscribe",
                 "pair": [self._MAP.get(p, p.replace("-", "/")) for p in self.pairs],
                 "subscription": {"name": "book", "depth": min(self.depth, 100)}}]
    def normalize(self, msg):
        if isinstance(msg, list) and len(msg) >= 2 and isinstance(msg[1], dict):
            pair = msg[-1] if isinstance(msg[-1], str) else None
            kind = "snapshot" if ("as" in msg[1] or "bs" in msg[1]) else "update"
            return {"pair": pair, "kind": kind}
        return {"pair": None, "kind": "control"}


class BinanceUSAdapter(Adapter):
    name = "binanceus"
    def _streams(self):
        return [f"{p.split('-')[0].lower()}usdt@depth20@100ms" for p in self.pairs]
    def ws_url(self):
        return "wss://stream.binance.us:9443/stream?streams=" + "/".join(self._streams())
    def normalize(self, msg):
        s = msg.get("stream")
        pair = s.split("@")[0].upper() if isinstance(s, str) else None
        return {"pair": pair, "kind": "snapshot"}


class OKXAdapter(Adapter):
    name = "okx"
    _MAP = {"BTC-USD": "BTC-USDT", "ETH-USD": "ETH-USDT", "SOL-USD": "SOL-USDT"}
    def ws_url(self): return "wss://ws.okx.com:8443/ws/v5/public"
    def subscribe_messages(self):
        return [{"op": "subscribe", "args": [
            {"channel": "books5", "instId": self._MAP.get(p, p)} for p in self.pairs]}]
    def normalize(self, msg):
        arg = msg.get("arg") or {}
        if arg.get("channel") == "books5" and msg.get("data"):
            return {"pair": arg.get("instId"), "kind": "snapshot"}
        return {"pair": arg.get("instId"), "kind": "control"}


ADAPTERS = {a.name: a for a in (CoinbaseAdapter, KrakenAdapter, BinanceUSAdapter, OKXAdapter)}


# ── capture loop (one task per exchange) ────────────────────────────────────
async def capture_exchange(adapter, out_dir, deadline, health):
    import websockets
    writer = RotatingJsonlWriter(out_dir, f"l2_{adapter.name}_")
    seq = itertools.count(); backoff = 1.0; attempt = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            attempt += 1
            if attempt > 1:
                health.event("reconnect", exchange=adapter.name, attempt=attempt)
            try:
                async with websockets.connect(adapter.ws_url(), ping_interval=20,
                                              max_size=None) as ws:
                    health.event("connect", exchange=adapter.name)
                    for sub in adapter.subscribe_messages():
                        await ws.send(json.dumps(sub))
                    health.event("subscribed", exchange=adapter.name)
                    backoff = 1.0
                    while deadline is None or time.monotonic() < deadline:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        recv_ts = time.time()
                        s = next(seq)
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            health.event("parse_error", exchange=adapter.name, seq=s)
                            continue
                        tag = adapter.normalize(msg) if isinstance(msg, (dict, list)) else {}
                        writer.write({"exchange": adapter.name, "recv_ts": recv_ts,
                                      "seq": s, "pair": tag.get("pair"),
                                      "kind": tag.get("kind"), "msg": msg})
            except asyncio.TimeoutError:
                health.event("gap", exchange=adapter.name)
            except Exception as e:  # noqa: BLE001 — log + reconnect, never crash
                health.event("error", exchange=adapter.name, detail=f"{type(e).__name__}: {e}")
                await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)
    finally:
        writer.close()


async def run(args) -> int:
    out_dir = Path(args.out_dir)
    health = HealthWriter(out_dir / "l2_feed_health.jsonl")
    try:
        adapters = [ADAPTERS[n](args.pairs, depth=args.depth)
                    for n in args.exchanges if n in ADAPTERS]
        if not adapters:
            print(f"no valid exchanges in {args.exchanges}; choose from {list(ADAPTERS)}")
            return 2
        deadline = (time.monotonic() + args.minutes * 60) if args.minutes > 0 else None
        print(f"[l2_collector] {[a.name for a in adapters]} pairs={args.pairs} -> {out_dir}",
              flush=True)
        await asyncio.gather(*[capture_exchange(a, out_dir, deadline, health) for a in adapters])
        return 0
    finally:
        health.close()


def _args():
    p = argparse.ArgumentParser(description="Self-contained CEX L2 order-book collector")
    p.add_argument("--minutes", type=float, default=0.0, help="0 = run until Ctrl-C")
    p.add_argument("--exchanges", type=lambda s: [x.strip() for x in s.split(",")],
                   default=["coinbase", "kraken", "binanceus"],
                   help="comma list of: " + ", ".join(ADAPTERS))
    p.add_argument("--pairs", type=lambda s: [x.strip() for x in s.split(",")],
                   default=["BTC-USD", "ETH-USD"])
    p.add_argument("--depth", type=int, default=25)
    p.add_argument("--out-dir", default="./l2_data")
    return p.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run(_args())))
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr); sys.exit(0)
