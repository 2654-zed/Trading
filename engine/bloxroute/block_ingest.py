"""Tier-1 confirmation-side ingest (the "after" half of the mempool join).

Polls a FREE public ETH JSON-RPC endpoint (decision #1) with
eth_blockNumber / eth_getBlockByNumber and emits one confirmation row per
transaction in each new block:

    {hash, block_number, block_timestamp, block_received_local_ts,
     builder_or_fee_recipient, tx_index, source,
     clock_synced, clock_offset_ms}

HARD RULES:
  * This process NEVER touches the bloXroute WebSocket (the $300 tier
    allows ONE concurrent stream; the confirmation side is HTTP JSON-RPC
    only, read-only, no credentials, no signing).
  * Append-only, lossless: rows are never deduped, dropped, or edited. A
    restart may re-emit rows for a block already archived — duplicates are
    resolved at query time (Phase 4 DuckDB), never at write time.
  * Loud failure: every RPC error is logged via the health callback and
    triggers endpoint rotation; nothing is silently swallowed.
  * A lagging load-balanced node (block not yet available) STOPS the cycle
    so the same block is retried next cycle — a block is never skipped.

HONEST LIMITATION (recorded, not hidden): `builder_or_fee_recipient` is
the block header's `miner` field (the fee recipient). The TRUE builder /
relay identity is only available from out-of-protocol relay-data APIs and
is flagged as a later add — standard RPC cannot provide it.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable, Optional

# Free public ETH mainnet JSON-RPC endpoints (no key, read-only). The
# poller rotates to the next on any failure.
DEFAULT_RPCS = [
    "https://ethereum-rpc.publicnode.com",
    "https://eth.llamarpc.com",
    "https://eth.drpc.org",
    "https://cloudflare-eth.com",
]

# Vantage tag prefix for confirmation rows (the rpc URL is appended per
# row via `source` so multi-endpoint provenance is auditable).
SOURCE_KIND = "public-eth-rpc"


class RpcError(Exception):
    """Any JSON-RPC transport/protocol failure (HTTP, JSON, error object)."""


def rpc_call(url: str, method: str, params: list, timeout: float = 10.0):
    """Single JSON-RPC 2.0 call over HTTPS. Returns the `result` value
    (which may legitimately be None, e.g. a not-yet-available block).
    Raises RpcError on transport/JSON/protocol failure — never returns a
    silently-wrong value."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "trading-research-block-ingest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception as e:  # noqa: BLE001 — normalized to RpcError for rotation
        raise RpcError(f"{method} transport failure: {type(e).__name__}: {e}") from e
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise RpcError(f"{method} returned non-JSON ({len(raw)} bytes)") from e
    if not isinstance(msg, dict):
        raise RpcError(f"{method} returned non-object JSON: {type(msg).__name__}")
    if msg.get("error"):
        raise RpcError(f"{method} rpc error: {msg['error']}")
    if "result" not in msg:
        raise RpcError(f"{method} response missing 'result'")
    return msg["result"]


def parse_block(block: dict, received_ts: float, source: str,
                clock_synced: Optional[bool] = None,
                clock_offset_ms: Optional[float] = None) -> list[dict]:
    """Flatten one eth_getBlockByNumber result (full=False -> tx hashes)
    into confirmation rows. Tolerates full=True (tx objects) by extracting
    each object's hash. Raises RpcError on a structurally-invalid block —
    loud failure, never a silently-wrong row."""
    try:
        number = int(block["number"], 16)
        timestamp = int(block["timestamp"], 16)
    except (KeyError, TypeError, ValueError) as e:
        raise RpcError(f"invalid block structure: {type(e).__name__}: {e}") from e
    miner = block.get("miner")
    if not miner or not isinstance(miner, str):
        # every valid ETH block header carries `miner`; its absence means a
        # structurally-broken response — fail loud, never an empty row field.
        raise RpcError(f"block {number}: missing/invalid miner field")
    miner = miner.lower()
    txs = block.get("transactions") or []
    rows = []
    for idx, t in enumerate(txs):
        if isinstance(t, str):
            tx_hash = t.lower()
        elif isinstance(t, dict):
            tx_hash = t.get("hash")
            if not tx_hash or not isinstance(tx_hash, str):
                raise RpcError(f"block {number}: tx object without hash "
                               f"at index {idx}")
            tx_hash = tx_hash.lower()
        else:
            raise RpcError(f"block {number}: unexpected tx entry type "
                           f"{type(t).__name__} at index {idx}")
        rows.append({
            "hash": tx_hash,
            "block_number": number,
            "block_timestamp": timestamp,
            "block_received_local_ts": received_ts,
            "builder_or_fee_recipient": miner,   # header `miner` = fee recipient;
                                                 # true builder/relay needs relay APIs (later add)
            "tx_index": idx,
            "source": f"{SOURCE_KIND}:{source}",
            "clock_synced": clock_synced,
            "clock_offset_ms": clock_offset_ms,
        })
    return rows


class BlockPoller:
    """Sequential, lossless block follower over rotating public RPCs.

    One `poll()` call = one cycle: read the chain head, then fetch every
    not-yet-processed block in order. Any failure (transport error, block
    not yet available on a lagging node) stops the cycle WITHOUT advancing
    `last_processed`, so the same block is retried next cycle. Blocks per
    cycle are capped to bound memory after a long stall; the remainder is
    picked up by subsequent cycles (nothing skipped)."""

    def __init__(self, rpcs: Optional[list[str]] = None,
                 fetch: Callable = rpc_call,
                 health: Optional[Callable] = None,
                 timeout: float = 10.0,
                 max_blocks_per_cycle: int = 30):
        # None -> built-in defaults; an EXPLICIT empty list is a config
        # error and must fail loud (not silently fall back).
        self._rpcs = list(DEFAULT_RPCS) if rpcs is None else list(rpcs)
        if not self._rpcs:
            raise ValueError("BlockPoller needs at least one RPC endpoint")
        self._i = 0
        self._fetch = fetch
        self._health = health
        self._timeout = timeout
        self._max_per_cycle = max_blocks_per_cycle
        self.last_processed: Optional[int] = None

    @property
    def current_rpc(self) -> str:
        return self._rpcs[self._i]

    def _emit(self, event: str, **detail) -> None:
        if self._health:
            self._health(event, **detail)

    def _rotate(self, reason: str) -> None:
        if len(self._rpcs) == 1:
            # rotating to the same endpoint would be a misleading log line;
            # say honestly that there is no failover and we will just retry.
            self._emit("no_failover_available", rpc=self.current_rpc,
                       detail=reason)
            return
        old = self.current_rpc
        self._i = (self._i + 1) % len(self._rpcs)
        self._emit("rpc_rotate", from_rpc=old, to_rpc=self.current_rpc,
                   detail=reason)

    def poll(self, clock_synced: Optional[bool] = None,
             clock_offset_ms: Optional[float] = None) -> list[dict]:
        """One poll cycle; returns new confirmation rows (possibly [])."""
        url = self.current_rpc
        try:
            latest_hex = self._fetch(url, "eth_blockNumber", [],
                                     timeout=self._timeout)
            latest = int(latest_hex, 16)
        except (RpcError, TypeError, ValueError) as e:
            self._emit("rpc_error", rpc=url, method="eth_blockNumber",
                       detail=str(e))
            self._rotate(f"eth_blockNumber failed: {e}")
            return []

        if self.last_processed is None:
            start = latest                    # start at tip; no implicit backfill
        elif latest < self.last_processed:
            # load-balanced lagging node or (rare) reorg below our height —
            # record it and wait; never go backwards, never drop.
            self._emit("head_below_processed", rpc=url, head=latest,
                       last_processed=self.last_processed)
            return []
        else:
            start = self.last_processed + 1

        end = min(latest, start + self._max_per_cycle - 1)
        if end < latest:
            self._emit("cycle_capped", start=start, end=end, head=latest,
                       detail="remainder picked up next cycle (lossless)")

        rows: list[dict] = []
        for n in range(start, end + 1):
            try:
                block = self._fetch(url, "eth_getBlockByNumber",
                                    [hex(n), False], timeout=self._timeout)
            except RpcError as e:
                self._emit("rpc_error", rpc=url, method="eth_getBlockByNumber",
                           block=n, detail=str(e))
                self._rotate(f"eth_getBlockByNumber({n}) failed: {e}")
                break                          # retry block n next cycle
            recv = time.time()
            if block is None:
                # head said n exists but this backend hasn't seen it yet
                self._emit("block_not_yet_available", rpc=url, block=n)
                break                          # retry block n next cycle
            try:
                block_rows = parse_block(block, recv, url,
                                         clock_synced=clock_synced,
                                         clock_offset_ms=clock_offset_ms)
            except RpcError as e:
                self._emit("rpc_error", rpc=url, method="parse_block",
                           block=n, detail=str(e))
                self._rotate(f"parse_block({n}) failed: {e}")
                break                          # retry block n next cycle
            rows.extend(block_rows)
            self.last_processed = n
            self._emit("block", number=n, txs=len(block_rows),
                       fee_recipient=(block.get("miner") or "").lower())
        return rows


__all__ = ["DEFAULT_RPCS", "SOURCE_KIND", "RpcError", "rpc_call",
           "parse_block", "BlockPoller"]
