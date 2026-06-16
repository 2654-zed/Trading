"""Historical price source for Phase 4 real outcomes (D-040).

Backed by DefiLlama's free coins API (no key, $0 CU). Prefetches a price
series per token over the replay window via the /chart endpoint (one
batched call covers many tokens), caches it, and serves interpolated
point-in-time prices for forward-return computation.

Per I-1/I-3: read-only HTTP GET to a public API; no on-chain calls, no
keys, no L3 writes. The DefiLlama dependency is isolated here so the rest
of the engine stays venue/source-agnostic (a `PriceHistorySource` mock is
used in tests).
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from bisect import bisect_left
from typing import Optional, Protocol


class PriceHistorySource(Protocol):
    def price_at(self, token: str, ts: float) -> Optional[float]: ...


class DefiLlamaPriceHistory:
    BASE = "https://coins.llama.fi"
    CHAINS = ("base", "arbitrum", "optimism")

    def __init__(self, *, period: str = "4h", batch: int = 30,
                 timeout: int = 40, user_agent: str = "engine-phase4/1.0"):
        self._period = period
        self._batch = batch
        self._timeout = timeout
        self._ua = user_agent
        # token_lower -> sorted list of (ts, price)
        self._series: dict[str, list[tuple[float, float]]] = {}
        # token_lower -> resolved chain (or None if unresolved)
        self._chain: dict[str, Optional[str]] = {}
        self.resolved_count = 0
        self.unresolved: list[str] = []

    # --- network ----------------------------------------------------

    def _chart(self, coins: list[str], start: int, span: int) -> dict:
        url = (f"{self.BASE}/chart/{','.join(coins)}"
               f"?start={start}&span={span}&period={self._period}")
        req = urllib.request.Request(url, headers={"User-Agent": self._ua})
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read()).get("coins", {})

    # --- prefetch ---------------------------------------------------

    def prefetch(self, tokens: list[str], start_ts: float, end_ts: float,
                 ) -> None:
        """Resolve each token's chain + cache its price series over
        [start_ts, end_ts]. Tries base → arbitrum → optimism. Idempotent."""
        toks = [t.lower() for t in tokens if t]
        span = self._span_for(start_ts, end_ts)
        start = int(start_ts)
        # DefiLlama /chart caps total data points at 500 (coins × span).
        # Size the per-call batch so coins × span ≤ 500.
        max_by_points = max(1, 500 // max(span, 1))
        eff_batch = max(1, min(self._batch, max_by_points))
        # Resolve per chain in batches; tokens that return a non-empty
        # series on a chain are bound to that chain.
        pending = [t for t in toks if t not in self._series]
        for chain in self.CHAINS:
            if not pending:
                break
            still: list[str] = []
            for i in range(0, len(pending), eff_batch):
                chunk = pending[i:i + eff_batch]
                coins = [f"{chain}:{t}" for t in chunk]
                try:
                    got = self._chart(coins, start, span)
                except (urllib.error.URLError, Exception):
                    got = {}
                for t in chunk:
                    key = f"{chain}:{t}"
                    pts = (got.get(key) or {}).get("prices") or []
                    if pts:
                        self._series[t] = sorted(
                            (float(p["timestamp"]), float(p["price"]))
                            for p in pts if p.get("price") is not None
                        )
                        self._chain[t] = chain
                    else:
                        still.append(t)
            pending = still
        self.resolved_count = len(self._series)
        self.unresolved = pending

    def _span_for(self, start_ts: float, end_ts: float) -> int:
        # number of `period` buckets spanning the window (+ a margin).
        per_seconds = {"1h": 3600, "4h": 14400, "6h": 21600,
                       "12h": 43200, "1d": 86400}.get(self._period, 14400)
        n = int((end_ts - start_ts) / per_seconds) + 4
        return max(n, 4)

    # --- query ------------------------------------------------------

    def price_at(self, token: str, ts: float) -> Optional[float]:
        """Nearest-point price (the series is dense relative to the
        forward horizon, so nearest ≈ interpolated for our purposes)."""
        s = self._series.get(token.lower())
        if not s:
            return None
        times = [t for t, _ in s]
        i = bisect_left(times, ts)
        if i <= 0:
            return s[0][1]
        if i >= len(s):
            return s[-1][1]
        # pick the closer of s[i-1], s[i]
        before_t, before_p = s[i - 1]
        after_t, after_p = s[i]
        return before_p if (ts - before_t) <= (after_t - ts) else after_p

    def coverage(self, tokens: list[str]) -> float:
        toks = [t.lower() for t in tokens if t]
        if not toks:
            return 0.0
        return sum(1 for t in toks if t in self._series) / len(toks)

    def series_end(self, token: str) -> Optional[float]:
        """Latest timestamp with a real price point for `token`, or None."""
        s = self._series.get(token.lower())
        return s[-1][0] if s else None

    def has_data_through(self, token: str, ts: float) -> bool:
        """True iff a real price point exists at or after `ts` — so an
        exit price at `ts` is genuine, not a clamped last-known value.
        Used by the paper trader to avoid closing on stale data."""
        end = self.series_end(token)
        return end is not None and end >= ts


__all__ = ["PriceHistorySource", "DefiLlamaPriceHistory"]
