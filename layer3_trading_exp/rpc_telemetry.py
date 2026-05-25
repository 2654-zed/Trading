"""Layer 3 trading-exp — per-method Alchemy CU instrumentation.

Mirror of `surveillance/rpc_telemetry.py` from the Layer 3 surveillance
repo (commit 7195ea5), with a `wrap_sync_web3` added because
trading-exp uses BOTH:
  - `AsyncWeb3 + WebSocketProvider` for newHeads subscription
  - `Web3 + HTTPProvider` for sync Multicall3 eth_call (via
    `_AlchemyTransport.call_multicall3`)

Why this exists: 2026-05-22/23 + 2026-05-24/25 Alchemy CU spikes
(1.1B → 1.6B in 36h; 1.9B → 2.3B another day). The surveillance
service was instrumented in commit 7195ea5 and confirmed at ~12M CU/day
visible. Trading-exp's budget was estimated at ~5M/day max but never
measured. This module closes the visibility gap.

Pool count grew 128 → 159 on 2026-05-17 (per
data/run_metadata/monitored_pools.json mtime). If `pool_monitor` does
one Multicall3 per pool per block (instead of one Multicall3 per block
batching all pools), the per-block fan-out × pool count × 3 chains
× block rate matches the observed Alchemy spike. This telemetry will
confirm or refute that hypothesis directly.

CLI:
    python -m layer3_trading_exp.rpc_telemetry --summary
    python -m layer3_trading_exp.rpc_telemetry --by method --top 30
"""
from __future__ import annotations
import argparse
import contextvars
import datetime as _dt
import logging
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_in_manager_wrap: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "rpc_telemetry_in_manager_wrap", default=False
)

logger = logging.getLogger("layer3_trading_exp.rpc_telemetry")

# Alchemy CU cost table (matches surveillance/rpc_telemetry.py).
ALCHEMY_CU_COSTS: dict[str, int] = {
    "eth_chainId": 0,
    "eth_blockNumber": 10,
    "eth_call": 26,
    "eth_estimateGas": 87,
    "eth_gasPrice": 19,
    "eth_getBalance": 19,
    "eth_getBlockByHash": 21,
    "eth_getBlockByNumber": 16,
    "eth_getCode": 19,
    "eth_getLogs": 75,
    "eth_getStorageAt": 17,
    "eth_getTransactionByHash": 17,
    "eth_getTransactionCount": 26,
    "eth_getTransactionReceipt": 15,
    "eth_sendRawTransaction": 250,
    "eth_subscribe": 20,
    "eth_unsubscribe": 10,
    "eth_syncing": 0,
    "alchemy_getAssetTransfers": 150,
    "alchemy_getTokenBalances": 26,
    "alchemy_getTokenMetadata": 100,
    "alchemy_getTransactionReceipts": 250,
    "alchemy_getTokenAllowance": 19,
    "alchemy_pendingTransactions": 20,
    "_default": 25,
}


def cu_estimate(method: str) -> int:
    return ALCHEMY_CU_COSTS.get(method, ALCHEMY_CU_COSTS["_default"])


_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS rpc_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    component TEXT NOT NULL,
    chain TEXT,
    method TEXT NOT NULL,
    n_calls INTEGER NOT NULL,
    cu_estimate INTEGER NOT NULL,
    n_errors INTEGER NOT NULL DEFAULT 0,
    avg_duration_ms INTEGER NOT NULL DEFAULT 0
)
"""

_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_rpc_log_ts ON rpc_call_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_rpc_log_method ON rpc_call_log(method)",
    "CREATE INDEX IF NOT EXISTS idx_rpc_log_component ON rpc_call_log(component)",
]


def ensure_rpc_log_table(conn: sqlite3.Connection) -> None:
    conn.execute(_TABLE_DDL)
    for idx in _INDEX_DDL:
        conn.execute(idx)
    conn.commit()


class RpcTelemetry:
    """Buffer + flush per-method RPC counts to SQLite. Thread-safe."""

    def __init__(self, db_path: str | Path, flush_interval: float = 30.0):
        self.db_path = str(db_path)
        self.flush_interval = flush_interval
        self._buf: dict[tuple[str, str, str], dict] = {}
        self._lock = threading.Lock()
        self._last_flush = time.time()
        self._enabled = True
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            ensure_rpc_log_table(conn)
            conn.close()
        except Exception as e:
            logger.warning("rpc_telemetry init failed (continuing disabled): %s", e)
            self._enabled = False

    def record(self, component: str, chain: str | None, method: str,
               ok: bool, duration_ms: int = 0) -> None:
        if not self._enabled:
            return
        try:
            key = (component, chain or "", method)
            with self._lock:
                d = self._buf.setdefault(key, {"ok": 0, "error": 0, "total_ms": 0})
                if ok:
                    d["ok"] += 1
                else:
                    d["error"] += 1
                d["total_ms"] += duration_ms
                if time.time() - self._last_flush > self.flush_interval:
                    self._flush_locked()
                    self._last_flush = time.time()
        except Exception as e:
            logger.warning("rpc_telemetry.record failed (continuing): %s", e)

    def flush(self) -> int:
        if not self._enabled:
            return 0
        with self._lock:
            return self._flush_locked()

    def _flush_locked(self) -> int:
        if not self._buf:
            return 0
        rows = []
        ts = datetime.now(timezone.utc).isoformat()
        for (component, chain, method), counts in self._buf.items():
            n_calls = counts["ok"] + counts["error"]
            cu = cu_estimate(method) * n_calls
            avg_ms = counts["total_ms"] // n_calls if n_calls else 0
            rows.append((ts, component, chain or None, method, n_calls, cu,
                         counts["error"], avg_ms))
        self._buf.clear()
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.executemany(
                "INSERT INTO rpc_call_log (ts, component, chain, method, n_calls, "
                "cu_estimate, n_errors, avg_duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            conn.close()
            return len(rows)
        except Exception as e:
            logger.warning("rpc_telemetry flush failed (%d rows lost): %s", len(rows), e)
            return 0


_GLOBAL: RpcTelemetry | None = None


def get_telemetry(db_path: str | Path | None = None) -> RpcTelemetry:
    """Return the process-wide telemetry instance. Default DB:
    /app/data/run_metadata/rpc_telemetry.db (writable in Railway container)."""
    global _GLOBAL
    if _GLOBAL is None:
        if db_path is None:
            db_path = Path("/app/data/run_metadata/rpc_telemetry.db")
            if not db_path.parent.exists():
                # Local-dev fallback: write next to this module
                db_path = Path(__file__).resolve().parent.parent / "data" / "rpc_telemetry.db"
        _GLOBAL = RpcTelemetry(db_path)
    return _GLOBAL


# ==== ASYNC WRAP (newHeads WebSocketProvider) ====

def wrap_async_provider(provider: Any, component: str, chain: str | None,
                        telemetry: RpcTelemetry | None = None) -> None:
    """Monkey-patch async provider.make_request. Idempotent."""
    if getattr(provider, "_rpc_telemetry_wrapped", False):
        return
    if telemetry is None:
        telemetry = get_telemetry()

    original_make_request = provider.make_request

    async def wrapped_make_request(method, params):
        if _in_manager_wrap.get():
            return await original_make_request(method, params)
        t0 = time.monotonic()
        ok = False
        try:
            result = await original_make_request(method, params)
            ok = not (isinstance(result, dict) and "error" in result and result["error"])
            return result
        except Exception:
            ok = False
            raise
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            telemetry.record(component, chain, method, ok=ok, duration_ms=ms)

    provider.make_request = wrapped_make_request
    provider._rpc_telemetry_wrapped = True
    logger.info("rpc_telemetry: wrapped ASYNC provider component=%s chain=%s",
                component, chain)


def wrap_async_web3(w3: Any, component: str, chain: str | None,
                    telemetry: RpcTelemetry | None = None) -> None:
    """Wrap every async-Web3 RPC entry point. Multi-entry so it captures
    helpers that bypass `coro_request` (web3.py 7.x async)."""
    if telemetry is None:
        telemetry = get_telemetry()

    provider = getattr(w3, "provider", None)
    if provider is not None:
        wrap_async_provider(provider, component, chain, telemetry)

    manager = getattr(w3, "manager", None)
    if manager is None:
        return
    if getattr(manager, "_rpc_telemetry_wrapped", False):
        return

    def _wrap_manager_method(attr_name: str) -> bool:
        method_fn = getattr(manager, attr_name, None)
        if method_fn is None or not callable(method_fn):
            return False

        async def wrapped(method, params, *args, **kwargs):
            if _in_manager_wrap.get():
                return await method_fn(method, params, *args, **kwargs)
            t0 = time.monotonic()
            ok = False
            token = _in_manager_wrap.set(True)
            try:
                result = await method_fn(method, params, *args, **kwargs)
                ok = True
                return result
            except Exception:
                ok = False
                raise
            finally:
                _in_manager_wrap.reset(token)
                ms = int((time.monotonic() - t0) * 1000)
                telemetry.record(component, chain, str(method),
                                 ok=ok, duration_ms=ms)
        setattr(manager, attr_name, wrapped)
        return True

    wrapped_any = False
    for name in ("coro_request", "_coro_make_request", "socket_request"):
        if _wrap_manager_method(name):
            wrapped_any = True
    if wrapped_any:
        manager._rpc_telemetry_wrapped = True
        logger.info("rpc_telemetry: wrapped ASYNC web3 manager component=%s chain=%s",
                    component, chain)


# ==== SYNC WRAP (HTTPProvider for Multicall3) ====
# trading-exp's _AlchemyTransport.call_multicall3 uses sync `http_w3.eth.call(...)`
# which routes through `manager.request_blocking` → `provider.make_request`.

def wrap_sync_provider(provider: Any, component: str, chain: str | None,
                       telemetry: RpcTelemetry | None = None) -> None:
    """Monkey-patch sync provider.make_request. Idempotent."""
    if getattr(provider, "_rpc_telemetry_wrapped", False):
        return
    if telemetry is None:
        telemetry = get_telemetry()

    original = provider.make_request

    def wrapped(method, params):
        if _in_manager_wrap.get():
            return original(method, params)
        t0 = time.monotonic()
        ok = False
        try:
            result = original(method, params)
            ok = not (isinstance(result, dict) and "error" in result and result["error"])
            return result
        except Exception:
            ok = False
            raise
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            telemetry.record(component, chain, method, ok=ok, duration_ms=ms)

    provider.make_request = wrapped
    provider._rpc_telemetry_wrapped = True
    logger.info("rpc_telemetry: wrapped SYNC provider component=%s chain=%s",
                component, chain)


def wrap_sync_web3(w3: Any, component: str, chain: str | None,
                   telemetry: RpcTelemetry | None = None) -> None:
    """Wrap every sync-Web3 RPC entry point.

    Sync Web3 has fewer entry points than async:
      - `manager.request_blocking(method, params)` — public API
      - `provider.make_request(method, params)` — provider direct

    `w3.eth.call(...)` → Method.__call__ → manager.request_blocking →
    provider.make_request. Wrapping provider.make_request catches all
    paths through the provider; the contextvar-guarded
    request_blocking wrap above adds belt-and-suspenders.
    """
    if telemetry is None:
        telemetry = get_telemetry()

    provider = getattr(w3, "provider", None)
    if provider is not None:
        wrap_sync_provider(provider, component, chain, telemetry)

    manager = getattr(w3, "manager", None)
    if manager is None:
        return
    if getattr(manager, "_rpc_telemetry_wrapped_sync", False):
        return

    request_blocking = getattr(manager, "request_blocking", None)
    if request_blocking is None or not callable(request_blocking):
        return

    def wrapped(method, params, *args, **kwargs):
        if _in_manager_wrap.get():
            return request_blocking(method, params, *args, **kwargs)
        t0 = time.monotonic()
        ok = False
        token = _in_manager_wrap.set(True)
        try:
            result = request_blocking(method, params, *args, **kwargs)
            ok = True
            return result
        except Exception:
            ok = False
            raise
        finally:
            _in_manager_wrap.reset(token)
            ms = int((time.monotonic() - t0) * 1000)
            telemetry.record(component, chain, str(method),
                             ok=ok, duration_ms=ms)

    manager.request_blocking = wrapped
    manager._rpc_telemetry_wrapped_sync = True
    logger.info("rpc_telemetry: wrapped SYNC web3 manager component=%s chain=%s",
                component, chain)


# ==== Query helpers ====

def summarize(conn: sqlite3.Connection, since: str | None = None,
              until: str | None = None, group_by: str = "method") -> list[dict]:
    valid = {"method", "component", "chain"}
    if group_by not in valid:
        raise ValueError(f"group_by must be one of {valid}")
    where = []
    args = []
    if since:
        where.append("ts >= ?"); args.append(since)
    if until:
        where.append("ts <= ?"); args.append(until)
    sql = (
        f"SELECT {group_by}, SUM(n_calls) calls, SUM(cu_estimate) cu, "
        f"SUM(n_errors) errors, MAX(avg_duration_ms) max_avg_ms "
        f"FROM rpc_call_log "
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" GROUP BY {group_by} ORDER BY cu DESC"
    )
    out = []
    for r in conn.execute(sql, args):
        out.append({
            group_by: r[0],
            "calls": r[1] or 0,
            "cu": r[2] or 0,
            "errors": r[3] or 0,
            "max_avg_ms": r[4] or 0,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="/app/data/run_metadata/rpc_telemetry.db")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--by", choices=["method", "component", "chain"], default="method")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        # local-dev fallback
        alt = Path(__file__).resolve().parent.parent / "data" / "rpc_telemetry.db"
        if alt.exists():
            db_path = alt
        else:
            print(f"DB not found at {args.db} or {alt}"); return 1

    conn = sqlite3.connect(db_path)
    ensure_rpc_log_table(conn)

    since = args.since
    if args.summary and not since:
        since = (datetime.now(timezone.utc) - _dt.timedelta(days=1)).isoformat()

    rows = summarize(conn, since=since, until=args.until, group_by=args.by)
    if not rows:
        print("No rpc_call_log rows in the requested window.")
        return 0
    total_calls = sum(r["calls"] for r in rows)
    total_cu = sum(r["cu"] for r in rows)
    print(f"=== RPC usage by {args.by} (since={since or 'all-time'} until={args.until or 'now'}) ===")
    print(f"  Total: {total_calls:,} calls / {total_cu:,} CUs")
    print()
    print(f"  {'group':40s}  {'calls':>12s}  {'CUs':>14s}  {'err':>5s}  {'max_avg_ms':>10s}")
    print("  " + "-" * 90)
    for r in rows[:args.top]:
        g = (str(r[args.by]) or "")[:40]
        print(f"  {g:40s}  {r['calls']:>12,}  {r['cu']:>14,}  {r['errors']:>5,}  {r['max_avg_ms']:>10,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
