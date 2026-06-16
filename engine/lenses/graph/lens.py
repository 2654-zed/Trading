"""Graph lens — Phase 3 sub-phase 3.1 implementation.

Per blueprint § 3.3: "detect coordination + influence" via centrality
metrics + subgraph detection. This sub-phase 3.1 implementation emits
THREE distinct signal types backed by real L3 corpus lookups.

DESIGN PIVOT (UNK-013, 2026-05-27): The original lens design queried
L3's `contracts` / `org_wallets` / `deployers` tables to ask
"is this pool flagged?". An empirical scan showed those tables have
ZERO overlap with our top-TVL monitored set — by construction, L3
indexes suspicious infrastructure (drainers, trap-emitters), not
canonical liquidity. The lens now queries L3's INTERACTION tables to
ask "what graph activity has L3 observed AROUND this pool?":

  1. `cluster_detected` — ≥3 monitored addresses share a common
     `org_id` in L3's `org_transfer_events` (i.e. the same operator
     is funding multiple of our pools). Strength scales with cluster
     size. Confidence high because org_id assignment is deterministic.

  2. `centrality_spike` — a monitored address receives transfers from
     L3-classified org wallets at high volume (in-degree centrality on
     the funding graph). Strength scales log-wise with total transfer
     count. Confidence high because the data is deterministic.

  3. `subgraph_anomaly` — a monitored address shows up in one of L3's
     anomaly subgraphs: (a) `poisoning_events` as poisoner OR target,
     (b) `org_transfer_events` from a high-risk `from_role`
     ("laundry", "unknown"), or (c) `org_wallets` directly (legacy path,
     rarely fires in practice but kept for completeness).

Why these three:
  - Each maps to a distinct graph-math notion (clustering of recipients
    by funder, in-degree centrality, subgraph membership)
  - All three are grounded in L3 data that EMPIRICALLY overlaps with our
    monitored set (smoke-tested against the live SQLite, not just
    against synthetic fixtures)
  - They exercise the Signal schema's full set of dimensions
    (strength + confidence + time_horizon + metadata)

Per I-15: this lens reads ONLY from injected adapters
(MonitoredSetSource + Layer3CorpusSource). It does not import any other
lens, and it does not import directly from `layer3_trading_exp/` — the
adapters (in `engine/adapters/`) provide that bridge.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Protocol

from .._base import Lens
from ...core.event_bus import EventBus


# ----- Adapter protocols (so the lens is testable with mock data) -------

class MonitoredSetSource(Protocol):
    """Returns the current monitored set of pools.

    A concrete impl wraps Phase 1+2's `pool_set.py::PoolSet`. A test
    impl returns canned fixture data.
    """
    def list_pools(self) -> list[dict]: ...
    # Each dict has at least: {"address": str, "chain": str,
    # "protocol": str, "token0": {"address": ..., "symbol": ...},
    # "token1": {"address": ..., "symbol": ...}}.


class Layer3CorpusSource(Protocol):
    """Read-only L3 corpus query interface.

    A concrete impl wraps Phase 1+2's `layer3_client.py::Layer3Client`
    plus raw SQL on the same read-only connection (per UNK-013 pivot:
    we query L3's INTERACTION tables, not just its classification
    tables, to find graph activity around our pools).

    A test impl returns canned fixture data.
    """
    # --- legacy classification probes (kept for completeness; empirically
    #     return None for ~100% of top-TVL monitored pools per UNK-013) ---

    def get_contract_classification(self, address: str) -> Optional[dict]: ...
    # Returns at least: {"deployer_address": str, "confidence_tier": str,
    # "last_updated": str} or None if not in corpus.

    def get_org_wallet_membership(self, address: str) -> Optional[dict]: ...
    # Returns at least: {"org_id": str, "tier": str, "role": str} for the
    # FIRST org_wallet row matching `address`, or None.

    def get_deployer_deploy_count(self, deployer_address: str) -> int: ...
    # Returns total contracts deployed by this deployer per L3, or 0 if
    # the deployer isn't in the corpus.

    # --- interaction probes (UNK-013 path: these EMPIRICALLY overlap with
    #     our monitored set; they drive the actual signals) ---

    def get_org_interactions(self, address: str) -> Optional[dict]: ...
    # Returns aggregated org-transfer stats for an address that receives
    # transfers from L3-classified orgs. None if no interactions.
    # Shape: {
    #     "total_transfers": int,
    #     "distinct_org_ids": list[str],   # may be empty if all NULL
    #     "primary_org_id": Optional[str], # most frequent non-null org_id
    #     "from_roles": dict[str, int],    # role -> count
    #     "last_seen_ts": Optional[float], # epoch seconds; best-effort
    # }

    def get_poisoning_membership(self, address: str) -> Optional[dict]: ...
    # Returns poisoning_events stats for an address. None if not seen.
    # Shape: {
    #     "as_poisoner": int,   # rows where address is poisoner
    #     "as_target": int,     # rows where address is target
    #     "event_types": list[str],
    #     "chains": list[str],
    # }

    def prefetch_for_scan(self, addresses: list[str]) -> None: ...
    # OPTIONAL performance hook. The lens calls this once per scan with
    # the full set of addresses it will query that scan. Concrete impls
    # may use it to do a single bulk SQL query and cache results; the
    # subsequent per-address calls then hit the cache.
    #
    # Impls without this optimization should provide a no-op. The
    # Protocol marks it as required to enforce the contract, but a
    # default no-op base is allowed in mocks.


# ----- The lens ----------------------------------------------------------

class GraphLens(Lens):
    """Graph-math lens. Subclass-defined behavior:
      - lens_label = "graph"
      - emits cluster_detected, centrality_spike, subgraph_anomaly
      - reads monitored set + L3 corpus via injected adapters
    """

    lens_label = "graph"

    # Tunables (subclass-locked at sub-phase 3.1; future sub-phases
    # may move these to a config). All thresholds picked from empirical
    # distributions against the real L3 SQLite (2026-05-27 diagnostic).

    # cluster_detected — fires when ≥N monitored addresses share an org_id.
    CLUSTER_MIN_ADDRESSES = 3

    # centrality_spike — fires when a monitored address receives >N total
    # transfers from L3-classified org wallets. Real distribution shows
    # 39 addresses with >100, 28 with >1000.
    CENTRALITY_TRANSFER_THRESHOLD = 100
    # Strength = log10(n_transfers) / 6.0, capped at 1.0 (≈ 1M transfers).
    CENTRALITY_LOG_NORMALIZER = 6.0

    # subgraph_anomaly — role-based bucketing for the from_role of
    # org_transfer_events. "laundry" + "unknown" are L3's high-risk
    # classifications; anything else is treated as benign for this signal.
    HIGH_RISK_FROM_ROLES: frozenset[str] = frozenset({"laundry", "unknown"})
    # Strength mapping (CONFIRMED > SUSPECTED > MONITORED also retained
    # for the legacy org_wallets path).
    ORG_TIER_STRENGTH: dict[str, float] = {
        "CONFIRMED": 1.0,
        "SUSPECTED": 0.7,
        "MONITORED": 0.4,
        "LAUNDRY": 0.9,   # from_role='laundry' — very strong signal
        "UNKNOWN": 0.5,   # from_role='unknown' — moderate signal
        "POISONER": 1.0,
        "POISON_TARGET": 0.8,
    }
    ORG_TIER_STRENGTH_DEFAULT = 0.3

    # Scan interval (seconds). 30s by default — graph properties don't
    # change every block, so polling at 30s gives plenty of signal
    # density without spamming.
    SCAN_INTERVAL_SECONDS = 30.0

    def __init__(
        self,
        monitored_set: MonitoredSetSource,
        l3_corpus: Layer3CorpusSource,
        *,
        scan_interval_seconds: Optional[float] = None,
        max_scans: Optional[int] = None,
        replay_clock=None,
    ):
        super().__init__()
        self._monitored_set = monitored_set
        self._l3 = l3_corpus
        self._scan_interval = (
            float(scan_interval_seconds)
            if scan_interval_seconds is not None
            else self.SCAN_INTERVAL_SECONDS
        )
        # Bounded run for tests / smoke; None = run forever.
        self._max_scans = max_scans
        self._scan_count = 0
        # Sub-phase 3.3: temporal replay. When set, run() iterates the
        # clock's windows and queries data "as of" each window end.
        self._replay_clock = replay_clock

    @property
    def scan_count(self) -> int:
        return self._scan_count

    async def run(
        self,
        bus: EventBus,
        *,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Loop: scan the monitored set, emit signals, sleep, repeat.

        Shutdown contract: respects both `stop_event` AND
        `asyncio.CancelledError`. The sleep between scans races against
        `stop_event` so a long `scan_interval` doesn't block shutdown.

        Replay mode (sub-phase 3.3): if a ReplayClock was injected,
        iterate its windows instead of looping in wall-clock — query
        data "as of" each window end + stamp signals with simulated time.
        """
        if self._replay_clock is not None:
            await self._run_replay(bus, stop_event=stop_event)
            return
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            if self._max_scans is not None and self._scan_count >= self._max_scans:
                return

            await self._scan_once(bus)
            self._scan_count += 1

            # Race the sleep against stop_event so a long scan_interval
            # doesn't delay shutdown. If stop_event isn't provided, fall
            # back to a plain (cancellable) sleep.
            if stop_event is None:
                await asyncio.sleep(self._scan_interval)
            else:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self._scan_interval,
                    )
                    # stop_event fired during the sleep — exit cleanly.
                    return
                except asyncio.TimeoutError:
                    # Normal path: sleep elapsed, loop continues.
                    pass

    def _candidate_addresses(self, pool: dict) -> list[tuple[str, str]]:
        """Return [(address, kind)] tuples to probe L3 against.

        For each pool we probe its pool contract AND its constituent
        token contracts. Both are valid recipients of L3-org transfers
        (per UNK-013, the pool factory itself is never in L3's corpus
        but the canonical tokens like WETH/USDC are — wait, also no —
        but they ARE recipients of org gas-station transfers).
        """
        candidates: list[tuple[str, str]] = []
        pool_addr = (pool.get("address") or "").lower()
        if pool_addr:
            candidates.append((pool_addr, "pool_address"))
        for tkey in ("token0", "token1"):
            tval = pool.get(tkey)
            taddr = ""
            if isinstance(tval, dict):
                taddr = (tval.get("address") or "").lower()
            elif isinstance(tval, str):
                taddr = tval.lower()
            if taddr:
                candidates.append((taddr, tkey))
        return candidates

    async def _run_replay(self, bus: EventBus, *, stop_event=None) -> None:
        """Iterate the ReplayClock's windows, scanning data as-of each."""
        for window in self._replay_clock.windows():
            if stop_event is not None and stop_event.is_set():
                return
            self._forced_timestamp = window.as_of
            try:
                await self._scan_once(bus, as_of_ts=window.as_of)
            finally:
                self._forced_timestamp = None
            self._scan_count += 1

    async def _scan_once(self, bus: EventBus, *,
                         as_of_ts: Optional[float] = None) -> None:
        """One scan pass over the monitored set.

        Three independent passes, one per signal type, each driven by a
        different L3 interaction-table join. Per UNK-013, the original
        classification-table joins yielded zero overlap with our top-TVL
        set; this design pivots to interaction tables.

        `as_of_ts` (sub-phase 3.3 replay): when set, all L3 queries are
        bounded to events at-or-before this simulated time.
        """
        pools = self._monitored_set.list_pools()
        if not pools:
            return

        # Collect (address, [pools_referring_to_it]) once so the L3 query
        # cost is bounded by the unique-address count.
        addr_to_pool_refs: dict[str, list[tuple[dict, str]]] = {}
        for pool in pools:
            for addr, kind in self._candidate_addresses(pool):
                addr_to_pool_refs.setdefault(addr, []).append((pool, kind))

        # Perf hook: ask the adapter to bulk-fetch everything for this
        # scan in one shot. Without this, 217 addresses × 2 tables × an
        # un-indexed full-table-scan each = ~10+ minutes per scan.
        prefetch = getattr(self._l3, "prefetch_for_scan", None)
        if callable(prefetch):
            try:
                prefetch(list(addr_to_pool_refs.keys()))
            except Exception:
                # Prefetch failures degrade to per-row fallback. Lens
                # must not crash if the adapter's perf hook misbehaves.
                pass

        # ---- collect org_interactions for every candidate address ----
        # Used by BOTH cluster_detected (group by org_id) and
        # centrality_spike (filter by transfer count).
        addr_to_interactions: dict[str, dict] = {}
        for addr in addr_to_pool_refs.keys():
            try:
                interactions = self._l3.get_org_interactions(
                    addr, as_of_ts=as_of_ts,
                )
            except Exception:
                continue
            if interactions:
                addr_to_interactions[addr] = interactions

        # ---- (1) cluster_detected ----
        # Group monitored addresses by their primary_org_id. Fire when a
        # cluster has ≥CLUSTER_MIN_ADDRESSES members.
        org_to_addrs: dict[str, list[str]] = {}
        for addr, interactions in addr_to_interactions.items():
            primary_org = interactions.get("primary_org_id")
            if not primary_org:
                continue
            org_to_addrs.setdefault(primary_org, []).append(addr)

        for org_id, cluster_addrs in org_to_addrs.items():
            if len(cluster_addrs) < self.CLUSTER_MIN_ADDRESSES:
                continue
            strength = min(len(cluster_addrs) / 50.0, 1.0)  # linear up to 50
            chains = set()
            for addr in cluster_addrs:
                for pool, _kind in addr_to_pool_refs.get(addr, []):
                    if pool.get("chain"):
                        chains.add(pool["chain"])
            await self.emit(
                bus, "cluster_detected",
                strength=strength,
                confidence=0.9,
                time_horizon="medium",
                metadata={
                    "org_id": org_id,
                    "cluster_size": len(cluster_addrs),
                    "cluster_addresses": cluster_addrs,
                    "chains": sorted(chains),
                },
            )

        # ---- (2) centrality_spike ----
        # Address receives >N total transfers from L3-orgs = high
        # in-degree on the funding graph. Strength scales log-wise.
        import math
        for addr, interactions in addr_to_interactions.items():
            n_transfers = int(interactions.get("total_transfers") or 0)
            if n_transfers < self.CENTRALITY_TRANSFER_THRESHOLD:
                continue
            strength = min(
                math.log10(max(n_transfers, 1)) / self.CENTRALITY_LOG_NORMALIZER,
                1.0,
            )
            primary_org = interactions.get("primary_org_id")
            await self.emit(
                bus, "centrality_spike",
                strength=strength,
                confidence=0.95,
                time_horizon="long",
                metadata={
                    "address": addr,
                    "total_transfers": n_transfers,
                    "distinct_org_ids": interactions.get("distinct_org_ids", []),
                    "primary_org_id": primary_org,
                    "from_roles": interactions.get("from_roles", {}),
                    "pool_references": [
                        {"pool": p.get("address"), "kind": k}
                        for p, k in addr_to_pool_refs.get(addr, [])
                    ][:5],  # cap metadata size
                },
            )

        # ---- (3) subgraph_anomaly ----
        # Three sub-signals, each emitted with a different `match_kind`:
        #   (a) poisoning_events membership (poisoner OR target)
        #   (b) from_role in HIGH_RISK_FROM_ROLES on the funding graph
        #   (c) legacy org_wallets membership (rarely fires)
        for addr in addr_to_pool_refs.keys():
            # (a) poisoning_events
            try:
                pois = self._l3.get_poisoning_membership(addr, as_of_ts=as_of_ts)
            except Exception:
                pois = None
            if pois:
                if pois.get("as_poisoner", 0) > 0:
                    await self.emit(
                        bus, "subgraph_anomaly",
                        strength=self.ORG_TIER_STRENGTH["POISONER"],
                        confidence=0.97,
                        time_horizon="long",
                        metadata={
                            "match_kind": "poisoning_poisoner",
                            "matched_address": addr,
                            "event_count": pois.get("as_poisoner", 0),
                            "chains": pois.get("chains", []),
                            "event_types": pois.get("event_types", []),
                        },
                    )
                if pois.get("as_target", 0) > 0:
                    await self.emit(
                        bus, "subgraph_anomaly",
                        strength=self.ORG_TIER_STRENGTH["POISON_TARGET"],
                        confidence=0.9,
                        time_horizon="long",
                        metadata={
                            "match_kind": "poisoning_target",
                            "matched_address": addr,
                            "event_count": pois.get("as_target", 0),
                            "chains": pois.get("chains", []),
                            "event_types": pois.get("event_types", []),
                        },
                    )

            # (b) high-risk from_role on the funding graph
            interactions = addr_to_interactions.get(addr)
            if interactions:
                from_roles: dict = interactions.get("from_roles") or {}
                for role, count in from_roles.items():
                    if role in self.HIGH_RISK_FROM_ROLES and count > 0:
                        tier = role.upper()
                        await self.emit(
                            bus, "subgraph_anomaly",
                            strength=self.ORG_TIER_STRENGTH.get(
                                tier, self.ORG_TIER_STRENGTH_DEFAULT,
                            ),
                            confidence=0.85,
                            time_horizon="medium",
                            metadata={
                                "match_kind": "from_role",
                                "matched_address": addr,
                                "from_role": role,
                                "event_count": count,
                                "primary_org_id":
                                    interactions.get("primary_org_id"),
                            },
                        )

            # (c) legacy org_wallets — kept for completeness
            try:
                row = self._l3.get_org_wallet_membership(addr)
            except Exception:
                row = None
            if row:
                tier = (row.get("tier") or "").upper()
                await self.emit(
                    bus, "subgraph_anomaly",
                    strength=self.ORG_TIER_STRENGTH.get(
                        tier, self.ORG_TIER_STRENGTH_DEFAULT,
                    ),
                    confidence=0.95,
                    time_horizon="long",
                    metadata={
                        "match_kind": "org_wallet",
                        "matched_address": addr,
                        "org_id": row.get("org_id", "?"),
                        "tier": tier,
                    },
                )


__all__ = ["GraphLens", "MonitoredSetSource", "Layer3CorpusSource"]
