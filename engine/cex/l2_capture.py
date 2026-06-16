"""Exchange adapters + the multi-exchange L2 capture loop.

Each adapter is thin on purpose: a websocket URL, the subscribe message(s) to
send, and a best-effort `normalize` that tags (pair, kind) on a raw message.
The capture NEVER depends on normalize being perfect — it records the full raw
message regardless, so nothing is lost even if an exchange changes its schema.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from typing import Optional

from ..bloxroute.clock_health import query_clock_status
from ..bloxroute.jsonl_io import (
    HealthWriter, RotatingJsonlWriter, acquire_singleton_lock,
)


# ── per-exchange adapters ───────────────────────────────────────────────────
# A pair is normalized as e.g. "BTC-USD"; each adapter maps it to the exchange's
# own symbol. `depth` = how many levels to request where the API allows it.

class Adapter:
    name = "base"

    def __init__(self, pairs: list[str], depth: int = 20):
        self.pairs = pairs
        self.depth = depth

    def ws_url(self) -> str:
        raise NotImplementedError

    def subscribe_messages(self) -> list[dict]:
        return []                                  # url-stream exchanges send none

    def normalize(self, msg: dict) -> dict:
        """Best-effort (pair, kind). kind: snapshot|update|control|other."""
        return {"pair": None, "kind": "other"}


class CoinbaseAdapter(Adapter):
    name = "coinbase"

    def ws_url(self):
        return "wss://ws-feed.exchange.coinbase.com"

    def subscribe_messages(self):
        return [{"type": "subscribe", "product_ids": list(self.pairs),
                 "channels": ["level2_batch"]}]

    def normalize(self, msg):
        t = msg.get("type")
        if t == "snapshot":
            return {"pair": msg.get("product_id"), "kind": "snapshot"}
        if t in ("l2update", "l2update_batch"):
            return {"pair": msg.get("product_id"), "kind": "update"}
        return {"pair": msg.get("product_id"), "kind": "control"}


class KrakenAdapter(Adapter):
    name = "kraken"
    _MAP = {"BTC-USD": "XBT/USD", "ETH-USD": "ETH/USD", "SOL-USD": "SOL/USD"}

    def ws_url(self):
        return "wss://ws.kraken.com"

    def subscribe_messages(self):
        return [{"event": "subscribe",
                 "pair": [self._MAP.get(p, p.replace("-", "/")) for p in self.pairs],
                 "subscription": {"name": "book", "depth": min(self.depth, 100)}}]

    def normalize(self, msg):
        # book updates arrive as arrays: [channelID, {as/bs|a/b}, "book-N", pair]
        if isinstance(msg, list) and len(msg) >= 2 and isinstance(msg[1], dict):
            pair = msg[-1] if isinstance(msg[-1], str) else None
            kind = "snapshot" if ("as" in msg[1] or "bs" in msg[1]) else "update"
            return {"pair": pair, "kind": kind}
        return {"pair": None, "kind": "control"}


class BinanceUSAdapter(Adapter):
    name = "binanceus"

    def _streams(self):
        # Binance liquidity is in USDT pairs: "BTC-USD" -> "btcusdt"
        return [f"{p.split('-')[0].lower()}usdt@depth20@100ms" for p in self.pairs]

    def ws_url(self):
        return "wss://stream.binance.us:9443/stream?streams=" + "/".join(self._streams())

    def normalize(self, msg):
        # combined-stream: {"stream":"btcusdt@depth20@100ms","data":{bids,asks}}
        s = msg.get("stream")
        pair = s.split("@")[0].upper() if isinstance(s, str) else None
        return {"pair": pair, "kind": "snapshot"}   # depth20 = full top-20 each push


class OKXAdapter(Adapter):
    name = "okx"
    _MAP = {"BTC-USD": "BTC-USDT", "ETH-USD": "ETH-USDT", "SOL-USD": "SOL-USDT"}

    def ws_url(self):
        return "wss://ws.okx.com:8443/ws/v5/public"

    def subscribe_messages(self):
        return [{"op": "subscribe", "args": [
            {"channel": "books5", "instId": self._MAP.get(p, p)} for p in self.pairs]}]

    def normalize(self, msg):
        arg = msg.get("arg") or {}
        if arg.get("channel") == "books5" and msg.get("data"):
            return {"pair": arg.get("instId"), "kind": "snapshot"}
        return {"pair": arg.get("instId"), "kind": "control"}


ADAPTERS = {a.name: a for a in (CoinbaseAdapter, KrakenAdapter,
                                BinanceUSAdapter, OKXAdapter)}
DEFAULT_EXCHANGES = ["coinbase", "kraken", "binanceus"]   # US-clean trio


# ── the capture loop (one task per exchange) ────────────────────────────────

async def capture_exchange(adapter: Adapter, out_dir, deadline,
                           health: HealthWriter, latest_clock: dict) -> None:
    import websockets
    writer = RotatingJsonlWriter(out_dir, f"l2_{adapter.name}_")
    seq = itertools.count()
    backoff = 1.0
    attempt = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            attempt += 1
            if attempt > 1:
                health.event("reconnect", exchange=adapter.name, attempt=attempt,
                             backoff_s=round(backoff, 1))
            try:
                async with websockets.connect(adapter.ws_url(), ping_interval=20,
                                              max_size=None) as ws:
                    health.event("connect", exchange=adapter.name, attempt=attempt)
                    for sub in adapter.subscribe_messages():
                        await ws.send(json.dumps(sub))
                    health.event("subscribed", exchange=adapter.name)
                    backoff = 1.0
                    while deadline is None or time.monotonic() < deadline:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        recv_ts = time.time()        # stamp BEFORE decode (Tier-0)
                        s = next(seq)
                        try:
                            msg = json.loads(raw)
                        except ValueError as e:
                            health.event("parse_error", exchange=adapter.name,
                                         seq=s, detail=type(e).__name__)
                            continue
                        tag = adapter.normalize(msg) if isinstance(msg, (dict, list)) else {}
                        writer.write({"exchange": adapter.name, "recv_ts": recv_ts,
                                      "seq": s, "pair": tag.get("pair"),
                                      "kind": tag.get("kind"),
                                      "clock_synced": latest_clock["synced"],
                                      "msg": msg})
            except asyncio.TimeoutError:
                health.event("gap", exchange=adapter.name, detail="no msg 60s")
            except websockets.ConnectionClosed as e:
                health.event("disconnect", exchange=adapter.name,
                             detail=type(e).__name__)
                await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)
            except Exception as e:  # noqa: BLE001 — log + reconnect, never crash
                health.event("error", exchange=adapter.name,
                             detail=f"{type(e).__name__}: {e}")
                await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)
    finally:
        writer.close()


async def run(args) -> int:
    out_dir = args.out_dir
    lock = acquire_singleton_lock(out_dir, name=".l2_capture.lock")
    if lock is None:
        print(f"ERROR: another L2 capture holds the lock on {out_dir}. Exiting.")
        return 3
    health = clock_writer = None
    try:
        health = HealthWriter(out_dir / "l2_feed_health.jsonl")
        clock_writer = HealthWriter(out_dir / "clock_health.jsonl")
        latest_clock = {"synced": None}
        try:
            st = await asyncio.to_thread(query_clock_status, 5.0)
            latest_clock["synced"] = st.get("clock_synced")
            clock_writer.event("clock_status", reason="startup", **st)
        except Exception as e:  # noqa: BLE001
            clock_writer.event("clock_status", reason="startup", error=str(e))

        adapters = [ADAPTERS[name](args.pairs, depth=args.depth)
                    for name in args.exchanges if name in ADAPTERS]
        if not adapters:
            print(f"no valid exchanges in {args.exchanges}", flush=True)
            return 2
        deadline = (time.monotonic() + args.minutes * 60) if args.minutes > 0 else None
        print(f"[l2_capture] {[a.name for a in adapters]} pairs={args.pairs} "
              f"-> {out_dir}", flush=True)
        await asyncio.gather(*[
            capture_exchange(a, out_dir, deadline, health, latest_clock)
            for a in adapters])
        return 0
    finally:
        for c in (health, clock_writer, lock):
            if c is not None:
                c.close()


__all__ = ["Adapter", "CoinbaseAdapter", "KrakenAdapter", "BinanceUSAdapter",
           "OKXAdapter", "ADAPTERS", "DEFAULT_EXCHANGES", "capture_exchange", "run"]
