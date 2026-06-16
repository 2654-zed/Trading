"""Adapter: wrap Phase 1+2's `Layer3Client` as the data source for the
Phase 3 lenses (graph / stochastic / information).

Per I-3 (read-only against Layer 3): adapters here are strictly query
wrappers; they never write to L3. The underlying Layer3Client is
already read-only by construction (sqlite3 URI mode=ro). Raw-SQL methods
use the SAME read-only connection (via `Layer3Client.connection`).

Caching model (sub-phase 3.3 / D-029 refactor):
  - `prefetch_for_scan(addresses)` pulls RAW timestamped rows for the
    address set in ONE batched query per table and caches them.
  - All accessors derive their aggregates from the cached raw rows.
  - Accessors take an optional `as_of_ts`:
      * `as_of_ts is None`  → aggregate over ALL cached rows (static mode;
        preserves sub-phase 3.1 / 3.2 behavior).
      * `as_of_ts` set       → aggregate only over rows with timestamp
        <= as_of_ts (temporal-replay mode). This lets the lens slide a
        cursor through history without re-querying SQL each tick.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from layer3_trading_exp.layer3_client import Layer3Client


def _iso_to_epoch(ts: Optional[str]) -> Optional[float]:
    """Convert L3's ISO-8601 timestamp strings to Unix epoch seconds.

    L3 stores timestamps as strings like '2026-03-29T21:45:38+00:00'.
    Returns None for None / unparseable input.
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


class Phase2L3CorpusAdapter:
    """Bridges Layer3Client to the Phase 3 lens data protocols."""

    def __init__(self, client: Layer3Client):
        self._client = client
        # Raw-row caches, keyed by lowercased address. Populated by
        # prefetch_for_scan; None means "no prefetch yet" → SQL fallback.
        # transfers[addr] = list[(value_eth: float, from_role: str|None,
        #                         org_id: str|None, ts: float)]
        self._raw_transfers: Optional[dict[str, list[tuple]]] = None
        # poisoning[addr] = list[(role: 'poisoner'|'target',
        #                         event_type: str|None, chain: str|None,
        #                         ts: float|None)]
        self._raw_poisoning: Optional[dict[str, list[tuple]]] = None
        # liquidity[addr] = list[(event_type: str, ts: float)]
        self._raw_liquidity: Optional[dict[str, list[tuple]]] = None
        self._cache_scope: Optional[frozenset[str]] = None

    @classmethod
    def from_path(cls, db_path: Path) -> "Phase2L3CorpusAdapter":
        return cls(Layer3Client(Path(db_path)))

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Legacy classification probes (unchanged; empirically ~always None
    # for top-TVL pools per UNK-013, but kept for completeness).
    # ------------------------------------------------------------------

    def get_contract_classification(self, address: str) -> Optional[dict]:
        return self._client.get_contract_classification(address)

    def get_org_wallet_membership(self, address: str) -> Optional[dict]:
        rows = self._client.get_org_wallet_rows(address)
        if not rows:
            return None
        row = rows[0]
        out = {
            "org_id": row.get("org_id"),
            "role": row.get("role"),
            "chain": row.get("chain"),
            "tier": "UNKNOWN",
        }
        profile = self._client.get_deployer_profile(address)
        if profile and profile.get("confidence_tier"):
            out["tier"] = str(profile["confidence_tier"]).upper()
        else:
            cls = self._client.get_contract_classification(address)
            if cls and cls.get("confidence_tier"):
                out["tier"] = str(cls["confidence_tier"]).upper()
        return out

    def get_deployer_deploy_count(self, deployer_address: str) -> int:
        profile = self._client.get_deployer_profile(deployer_address)
        if not profile:
            return 0
        try:
            return int(profile.get("total_contracts_deployed") or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------
    # Prefetch: load raw rows once per address set.
    # ------------------------------------------------------------------

    def prefetch_for_scan(self, addresses: list[str]) -> None:
        addrs_lower = [a.lower() for a in addresses if a]
        scope = frozenset(addrs_lower)
        if scope == self._cache_scope:
            return
        self._cache_scope = scope
        self._raw_transfers = self._load_raw_transfers(addrs_lower)
        self._raw_poisoning = self._load_raw_poisoning(addrs_lower)
        self._raw_liquidity = self._load_raw_liquidity(addrs_lower)

    def _load_raw_transfers(self, addresses: list[str]) -> dict[str, list[tuple]]:
        if not addresses:
            return {}
        conn = self._client.connection
        ph = ",".join("?" * len(addresses))
        cur = conn.execute(
            f"SELECT to_address, value_eth, from_role, org_id, timestamp "
            f"FROM org_transfer_events WHERE LOWER(to_address) IN ({ph})",
            addresses,
        )
        out: dict[str, list[tuple]] = {}
        for to_addr, value_eth, from_role, org_id, ts in cur:
            t = _iso_to_epoch(ts)
            if t is None:
                continue
            out.setdefault((to_addr or "").lower(), []).append(
                (float(value_eth or 0.0), from_role, org_id, t)
            )
        # Sort each address's rows by time for efficient as_of slicing.
        for rows in out.values():
            rows.sort(key=lambda r: r[3])
        return out

    def _load_raw_poisoning(self, addresses: list[str]) -> dict[str, list[tuple]]:
        if not addresses:
            return {}
        conn = self._client.connection
        ph = ",".join("?" * len(addresses))
        cur = conn.execute(
            f"SELECT poisoner_address, target_address, event_type, chain, "
            f"detected_at FROM poisoning_events "
            f"WHERE LOWER(poisoner_address) IN ({ph}) "
            f"   OR LOWER(target_address) IN ({ph})",
            addresses + addresses,
        )
        scope = set(addresses)
        out: dict[str, list[tuple]] = {}
        for poisoner, target, etype, chain, detected_at in cur:
            t = _iso_to_epoch(detected_at)
            p = (poisoner or "").lower()
            tg = (target or "").lower()
            if p in scope:
                out.setdefault(p, []).append(("poisoner", etype, chain, t))
            if tg in scope:
                out.setdefault(tg, []).append(("target", etype, chain, t))
        return out

    def _load_raw_liquidity(self, addresses: list[str]) -> dict[str, list[tuple]]:
        if not addresses:
            return {}
        conn = self._client.connection
        ph = ",".join("?" * len(addresses))
        cur = conn.execute(
            f"SELECT token_address, event_type, timestamp "
            f"FROM liquidity_events WHERE LOWER(token_address) IN ({ph})",
            addresses,
        )
        out: dict[str, list[tuple]] = {}
        for tok, etype, ts in cur:
            t = _iso_to_epoch(ts)
            if t is None:
                continue
            out.setdefault((tok or "").lower(), []).append(
                (etype or "unknown", t)
            )
        for rows in out.values():
            rows.sort(key=lambda r: r[1])
        return out

    # ------------------------------------------------------------------
    # Data range (for building a ReplayClock).
    # ------------------------------------------------------------------

    def get_data_time_range(
        self, addresses: list[str],
    ) -> Optional[tuple[float, float]]:
        """Return (min_ts, max_ts) across org_transfer_events +
        liquidity_events for the address set. Used to bound a ReplayClock.
        Triggers a prefetch if not already cached for this scope."""
        self.prefetch_for_scan(addresses)
        mins: list[float] = []
        maxs: list[float] = []
        for cache in (self._raw_transfers, self._raw_liquidity):
            if not cache:
                continue
            for rows in cache.values():
                if not rows:
                    continue
                # transfers: ts at index 3; liquidity: ts at index 1.
                ts_idx = 3 if cache is self._raw_transfers else 1
                ts_vals = [r[ts_idx] for r in rows if r[ts_idx] is not None]
                if ts_vals:
                    mins.append(min(ts_vals))
                    maxs.append(max(ts_vals))
        if not mins:
            return None
        return (min(mins), max(maxs))

    # ------------------------------------------------------------------
    # Graph-lens accessors.
    # ------------------------------------------------------------------

    def get_org_interactions(
        self, address: str, *, as_of_ts: Optional[float] = None,
    ) -> Optional[dict]:
        addr = (address or "").lower()
        if not addr:
            return None
        rows = self._transfers_for(addr)
        if rows is None:
            return None
        if as_of_ts is not None:
            rows = [r for r in rows if r[3] <= as_of_ts]
        if not rows:
            return None
        distinct_orgs: dict[str, int] = {}
        from_roles: dict[str, int] = {}
        last_ts: Optional[float] = None
        for value_eth, from_role, org_id, t in rows:
            if org_id:
                distinct_orgs[org_id] = distinct_orgs.get(org_id, 0) + 1
            role_key = from_role if from_role else "null"
            from_roles[role_key] = from_roles.get(role_key, 0) + 1
            if last_ts is None or t > last_ts:
                last_ts = t
        primary_org_id = (
            max(distinct_orgs.items(), key=lambda kv: kv[1])[0]
            if distinct_orgs else None
        )
        return {
            "total_transfers": len(rows),
            "distinct_org_ids": sorted(distinct_orgs.keys()),
            "primary_org_id": primary_org_id,
            "from_roles": from_roles,
            "last_seen_ts": last_ts,
        }

    def get_poisoning_membership(
        self, address: str, *, as_of_ts: Optional[float] = None,
    ) -> Optional[dict]:
        addr = (address or "").lower()
        if not addr:
            return None
        rows = self._poisoning_for(addr)
        if rows is None:
            return None
        if as_of_ts is not None:
            rows = [r for r in rows if r[3] is None or r[3] <= as_of_ts]
        if not rows:
            return None
        as_poisoner = 0
        as_target = 0
        event_types: set[str] = set()
        chains: set[str] = set()
        for role, etype, chain, t in rows:
            if role == "poisoner":
                as_poisoner += 1
            else:
                as_target += 1
            if etype:
                event_types.add(etype)
            if chain:
                chains.add(chain)
        return {
            "as_poisoner": as_poisoner,
            "as_target": as_target,
            "event_types": sorted(event_types),
            "chains": sorted(chains),
        }

    # ------------------------------------------------------------------
    # Stochastic-lens accessor.
    # ------------------------------------------------------------------

    def get_flow_buckets(
        self, address: str, *, bucket_seconds: int = 60,
        as_of_ts: Optional[float] = None,
    ) -> Optional[list[tuple[float, float, int]]]:
        addr = (address or "").lower()
        if not addr:
            return None
        rows = self._transfers_for(addr)
        if rows is None:
            return None
        if as_of_ts is not None:
            rows = [r for r in rows if r[3] <= as_of_ts]
        if not rows:
            return None
        am: dict[int, tuple[float, int]] = {}
        for value_eth, _role, _org, t in rows:
            idx = int(t // bucket_seconds)
            prev_v, prev_c = am.get(idx, (0.0, 0))
            am[idx] = (prev_v + value_eth, prev_c + 1)
        return [
            (idx * bucket_seconds, am[idx][0], am[idx][1])
            for idx in sorted(am.keys())
        ]

    # ------------------------------------------------------------------
    # Information-lens accessors.
    # ------------------------------------------------------------------

    def get_liquidity_event_distributions(
        self, address: str, *, days_current: int = 7, days_baseline: int = 7,
        as_of_ts: Optional[float] = None,
    ) -> Optional[dict]:
        addr = (address or "").lower()
        if not addr:
            return None
        rows = self._liquidity_for(addr)
        if rows is None or not rows:
            return None
        return self._windowed_distribution(
            [(etype, t) for etype, t in rows],
            days_current, days_baseline, as_of_ts,
        )

    def get_role_distributions(
        self, address: str, *, days_current: int = 7, days_baseline: int = 7,
        as_of_ts: Optional[float] = None,
    ) -> Optional[dict]:
        addr = (address or "").lower()
        if not addr:
            return None
        rows = self._transfers_for(addr)
        if rows is None or not rows:
            return None
        # Project transfers to (from_role-or-null, ts).
        proj = [(r[1] if r[1] else "null", r[3]) for r in rows]
        return self._windowed_distribution(
            proj, days_current, days_baseline, as_of_ts,
        )

    @staticmethod
    def _windowed_distribution(
        events: list[tuple[str, float]],
        days_current: int, days_baseline: int,
        as_of_ts: Optional[float],
    ) -> Optional[dict]:
        """Split (category, ts) events into current + baseline windows.

        Anchor = as_of_ts if given, else MAX(ts) in the events. The
        current window is [anchor - days_current, anchor]; baseline is
        the days_baseline span immediately before that.
        """
        if not events:
            return None
        anchor = as_of_ts if as_of_ts is not None else max(t for _, t in events)
        cur_start = anchor - days_current * 86400
        base_end = cur_start
        base_start = base_end - days_baseline * 86400
        current: dict[str, int] = {}
        baseline: dict[str, int] = {}
        for cat, t in events:
            if cur_start <= t <= anchor:
                current[cat] = current.get(cat, 0) + 1
            elif base_start <= t < base_end:
                baseline[cat] = baseline.get(cat, 0) + 1
        if not current and not baseline:
            return None
        return {
            "current": current,
            "baseline": baseline,
            "current_total": sum(current.values()),
            "baseline_total": sum(baseline.values()),
        }

    # ------------------------------------------------------------------
    # Windowed forward-activity accessors (sub-phase 3.4 / D-034).
    # Aggregate across the whole prefetched address set within an explicit
    # [start, end) window — used for forward-looking outcome attribution.
    # ------------------------------------------------------------------

    def get_total_flow_in_window(self, start_ts: float, end_ts: float) -> float:
        """Sum org-transfer value_eth across ALL prefetched addresses with
        timestamp in [start_ts, end_ts)."""
        total = 0.0
        if not self._raw_transfers:
            return 0.0
        for rows in self._raw_transfers.values():
            for value_eth, _role, _org, t in rows:
                if start_ts <= t < end_ts:
                    total += value_eth
        return total

    def get_poisoning_count_in_window(self, start_ts: float,
                                      end_ts: float) -> int:
        """Count poisoning events across ALL prefetched addresses with
        detected_at in [start_ts, end_ts)."""
        n = 0
        if not self._raw_poisoning:
            return 0
        for rows in self._raw_poisoning.values():
            for _role, _etype, _chain, t in rows:
                if t is not None and start_ts <= t < end_ts:
                    n += 1
        return n

    # ------------------------------------------------------------------
    # Raw-row access with SQL fallback (for ad-hoc calls without prefetch).
    # ------------------------------------------------------------------

    def _transfers_for(self, addr: str) -> Optional[list[tuple]]:
        if self._raw_transfers is not None and \
                self._cache_scope is not None and addr in self._cache_scope:
            return self._raw_transfers.get(addr, [])
        loaded = self._load_raw_transfers([addr])
        return loaded.get(addr)

    def _poisoning_for(self, addr: str) -> Optional[list[tuple]]:
        if self._raw_poisoning is not None and \
                self._cache_scope is not None and addr in self._cache_scope:
            return self._raw_poisoning.get(addr, [])
        loaded = self._load_raw_poisoning([addr])
        return loaded.get(addr)

    def _liquidity_for(self, addr: str) -> Optional[list[tuple]]:
        if self._raw_liquidity is not None and \
                self._cache_scope is not None and addr in self._cache_scope:
            return self._raw_liquidity.get(addr, [])
        loaded = self._load_raw_liquidity([addr])
        return loaded.get(addr)


__all__ = ["Phase2L3CorpusAdapter"]
