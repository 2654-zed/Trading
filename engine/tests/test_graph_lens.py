"""Tests for engine.lenses.graph.lens.GraphLens — sub-phase 3.1.

Per UNK-013 / D-021 (path 1 resolution): the lens was re-grounded on
L3's INTERACTION tables (`org_transfer_events`, `poisoning_events`)
instead of its classification tables. These tests verify the new
contract.

Acceptance criteria from PHASE_3_MULTI_LENS_ENGINE_SPEC.md:
  - Graph lens emits ≥3 distinct signal types
    (cluster_detected, centrality_spike, subgraph_anomaly).
  - All emitted Signals pass validate_signal cleanly.
  - Lens reads ONLY from injected adapters (I-15) — verified here by
    using duck-typed mocks; no real Phase 2 import is touched.
  - L3 query failures must not crash the lens.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from engine.core.event_bus import EventBus
from engine.core.signal_schema import validate_signal
from engine.lenses.graph.lens import GraphLens


# ----- Mock adapter implementations (duck-typed Protocol satisfiers) ------

class FakeMonitoredSet:
    """Returns a fixed list of pool dicts."""
    def __init__(self, pools: list[dict]):
        self._pools = pools

    def list_pools(self) -> list[dict]:
        return list(self._pools)


class FakeL3Corpus:
    """In-memory L3 corpus for tests.

    Construct with:
      - contracts: legacy {addr_lower: {"deployer_address": ..., ...}}
      - org_wallets: legacy {addr_lower: {"org_id": ..., "tier": ..., ...}}
      - deployer_counts: legacy {addr_lower: int}
      - org_interactions: {addr_lower: {
            "total_transfers": int,
            "distinct_org_ids": list[str],
            "primary_org_id": Optional[str],
            "from_roles": dict[str, int],
            "last_seen_ts": Optional[float],
        }}
      - poisoning: {addr_lower: {
            "as_poisoner": int, "as_target": int,
            "event_types": list[str], "chains": list[str],
        }}

    Optional raise_on_* toggles exercise the lens's resilience path.
    """
    def __init__(
        self,
        *,
        contracts: Optional[dict] = None,
        org_wallets: Optional[dict] = None,
        deployer_counts: Optional[dict] = None,
        org_interactions: Optional[dict] = None,
        poisoning: Optional[dict] = None,
        raise_on_org: bool = False,
        raise_on_deployer: bool = False,
        raise_on_interactions: bool = False,
        raise_on_poisoning: bool = False,
    ):
        self._contracts = {k.lower(): v for k, v in (contracts or {}).items()}
        self._org_wallets = {
            k.lower(): v for k, v in (org_wallets or {}).items()
        }
        self._deployer_counts = {
            k.lower(): v for k, v in (deployer_counts or {}).items()
        }
        self._org_interactions = {
            k.lower(): v for k, v in (org_interactions or {}).items()
        }
        self._poisoning = {
            k.lower(): v for k, v in (poisoning or {}).items()
        }
        self._raise_on_org = raise_on_org
        self._raise_on_deployer = raise_on_deployer
        self._raise_on_interactions = raise_on_interactions
        self._raise_on_poisoning = raise_on_poisoning

    # ----- legacy probes -----
    def get_contract_classification(self, address: str) -> Optional[dict]:
        return self._contracts.get(address.lower())

    def get_org_wallet_membership(self, address: str) -> Optional[dict]:
        if self._raise_on_org:
            raise RuntimeError("simulated L3 org_wallet failure")
        return self._org_wallets.get(address.lower())

    def get_deployer_deploy_count(self, deployer_address: str) -> int:
        if self._raise_on_deployer:
            raise RuntimeError("simulated L3 deployer failure")
        return self._deployer_counts.get(deployer_address.lower(), 0)

    # ----- new interaction probes -----
    def get_org_interactions(self, address: str, *,
                             as_of_ts=None) -> Optional[dict]:
        if self._raise_on_interactions:
            raise RuntimeError("simulated L3 interactions failure")
        return self._org_interactions.get(address.lower())

    def get_poisoning_membership(self, address: str, *,
                                 as_of_ts=None) -> Optional[dict]:
        if self._raise_on_poisoning:
            raise RuntimeError("simulated L3 poisoning failure")
        return self._poisoning.get(address.lower())

    # ----- perf hook (no-op in tests; real adapter bulk-fetches) -----
    def prefetch_for_scan(self, addresses: list[str]) -> None:
        return None


# ----- Helpers ------------------------------------------------------------

def _pool(addr: str, chain: str = "base", protocol: str = "uniswap-v3",
          t0: str = "0xaaa", t1: str = "0xbbb") -> dict:
    return {
        "address": addr,
        "chain": chain,
        "protocol": protocol,
        "token0": {"address": t0, "symbol": "T0"},
        "token1": {"address": t1, "symbol": "T1"},
    }


async def _drain_signals(bus: EventBus, sub) -> list:
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


async def _run_one_scan(lens: GraphLens, bus: EventBus, sub) -> list:
    await lens._scan_once(bus)
    await asyncio.sleep(0)
    return await _drain_signals(bus, sub)


# ----- Construction guards ------------------------------------------------

def test_graph_lens_has_correct_label():
    lens = GraphLens(FakeMonitoredSet([]), FakeL3Corpus())
    assert lens.lens_label == "graph"


# ----- cluster_detected (re-grounded: shared org_id) ---------------------

def test_cluster_detected_fires_when_three_addresses_share_org():
    async def go():
        org = "org_001"
        pools = [
            _pool("0xpool1" + "0" * 34),
            _pool("0xpool2" + "0" * 34),
            _pool("0xpool3" + "0" * 34),
        ]
        org_interactions = {
            p["address"]: {
                "total_transfers": 50,
                "distinct_org_ids": [org],
                "primary_org_id": org,
                "from_roles": {"gas_station": 50},
                "last_seen_ts": None,
            }
            for p in pools
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.cluster_detected")
        sigs = await _run_one_scan(lens, bus, sub)

        assert len(sigs) == 1
        s = sigs[0]
        assert s.lens == "graph"
        assert s.type == "cluster_detected"
        assert s.metadata["org_id"] == org
        assert s.metadata["cluster_size"] == 3
        assert len(s.metadata["cluster_addresses"]) >= 3
        validate_signal(s)
    asyncio.run(go())


def test_cluster_detected_does_not_fire_below_threshold():
    async def go():
        org = "org_001"
        pools = [
            _pool("0xpool1" + "0" * 34),
            _pool("0xpool2" + "0" * 34),
        ]
        org_interactions = {
            p["address"]: {
                "total_transfers": 50,
                "distinct_org_ids": [org],
                "primary_org_id": org,
                "from_roles": {"gas_station": 50},
                "last_seen_ts": None,
            }
            for p in pools
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.cluster_detected")
        sigs = await _run_one_scan(lens, bus, sub)
        # Only 2 pools share the org; threshold is 3.
        assert sigs == []
    asyncio.run(go())


def test_cluster_detected_ignores_addresses_without_primary_org():
    async def go():
        pools = [
            _pool("0xpool1" + "0" * 34),
            _pool("0xpool2" + "0" * 34),
            _pool("0xpool3" + "0" * 34),
        ]
        # All addresses have transfer activity but NULL primary_org_id.
        org_interactions = {
            p["address"]: {
                "total_transfers": 50,
                "distinct_org_ids": [],
                "primary_org_id": None,
                "from_roles": {"null": 50},
                "last_seen_ts": None,
            }
            for p in pools
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.cluster_detected")
        sigs = await _run_one_scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


# ----- centrality_spike (re-grounded: transfer volume) -------------------

def test_centrality_spike_fires_above_transfer_threshold():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        org_interactions = {
            addr: {
                "total_transfers": 1000,  # > 100 threshold
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 1000},
                "last_seen_ts": None,
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.centrality_spike")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.type == "centrality_spike"
        assert s.metadata["address"] == addr
        assert s.metadata["total_transfers"] == 1000
        # Strength = log10(1000) / 6.0 = 3/6 = 0.5
        assert abs(s.strength - 0.5) < 1e-6
        validate_signal(s)
    asyncio.run(go())


def test_centrality_spike_does_not_fire_below_threshold():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        org_interactions = {
            addr: {
                "total_transfers": 50,  # < 100 threshold
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 50},
                "last_seen_ts": None,
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.centrality_spike")
        sigs = await _run_one_scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


def test_centrality_spike_strength_capped_at_one():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        # log10(10^7) = 7; / 6.0 = 1.17 → capped at 1.0
        org_interactions = {
            addr: {
                "total_transfers": 10_000_000,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 10_000_000},
                "last_seen_ts": None,
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.centrality_spike")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        assert sigs[0].strength == 1.0
        validate_signal(sigs[0])
    asyncio.run(go())


# ----- subgraph_anomaly (poisoning + high-risk roles + legacy) -----------

def test_subgraph_anomaly_fires_when_address_is_poisoner():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        poisoning = {
            addr: {
                "as_poisoner": 5,
                "as_target": 0,
                "event_types": ["zero_value_transfer"],
                "chains": ["base"],
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools), FakeL3Corpus(poisoning=poisoning),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.subgraph_anomaly")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.type == "subgraph_anomaly"
        assert s.metadata["match_kind"] == "poisoning_poisoner"
        # POISONER tier maps to strength 1.0
        assert s.strength == 1.0
        validate_signal(s)
    asyncio.run(go())


def test_subgraph_anomaly_fires_when_address_is_poison_target():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        poisoning = {
            addr: {
                "as_poisoner": 0,
                "as_target": 3,
                "event_types": ["address_poisoning"],
                "chains": ["arbitrum"],
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools), FakeL3Corpus(poisoning=poisoning),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.subgraph_anomaly")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        assert sigs[0].metadata["match_kind"] == "poisoning_target"
        # POISON_TARGET tier maps to strength 0.8
        assert abs(sigs[0].strength - 0.8) < 1e-9
        validate_signal(sigs[0])
    asyncio.run(go())


def test_subgraph_anomaly_fires_on_high_risk_from_role():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        org_interactions = {
            addr: {
                "total_transfers": 5,  # below centrality threshold
                "distinct_org_ids": ["org_002"],
                "primary_org_id": "org_002",
                "from_roles": {"laundry": 5},
                "last_seen_ts": None,
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.subgraph_anomaly")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        s = sigs[0]
        assert s.metadata["match_kind"] == "from_role"
        assert s.metadata["from_role"] == "laundry"
        # LAUNDRY tier = 0.9
        assert abs(s.strength - 0.9) < 1e-9
        validate_signal(s)
    asyncio.run(go())


def test_subgraph_anomaly_ignores_benign_gas_station_role():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        org_interactions = {
            addr: {
                "total_transfers": 5,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 5},
                "last_seen_ts": None,
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.subgraph_anomaly")
        sigs = await _run_one_scan(lens, bus, sub)
        # gas_station is NOT in HIGH_RISK_FROM_ROLES.
        assert sigs == []
    asyncio.run(go())


def test_subgraph_anomaly_legacy_org_wallets_path_still_fires():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        org_wallets = {
            addr: {"org_id": "org-x", "tier": "CONFIRMED", "role": "?"},
        }
        lens = GraphLens(
            FakeMonitoredSet(pools), FakeL3Corpus(org_wallets=org_wallets),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.subgraph_anomaly")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        assert sigs[0].metadata["match_kind"] == "org_wallet"
        assert sigs[0].strength == 1.0  # CONFIRMED
        validate_signal(sigs[0])
    asyncio.run(go())


# ----- ACCEPTANCE: all three signal types fire in one scan ---------------

def test_acceptance_all_three_signal_types_fire_in_one_scan():
    """The spec's acceptance bar: ≥3 distinct signal types from this lens
    in a single scan against representative data."""
    async def go():
        # Setup: 3 pools share org_001 (cluster trigger).
        # One pool has 5000 transfers (centrality trigger).
        # One pool appears as a poisoner (subgraph trigger).
        pool_a = "0xa1" + "0" * 38
        pool_b = "0xb2" + "0" * 38
        pool_c = "0xc3" + "0" * 38
        pools = [_pool(pool_a), _pool(pool_b), _pool(pool_c)]
        org_interactions = {
            pool_a: {
                "total_transfers": 5000,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 5000},
                "last_seen_ts": None,
            },
            pool_b: {
                "total_transfers": 50,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 50},
                "last_seen_ts": None,
            },
            pool_c: {
                "total_transfers": 50,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 50},
                "last_seen_ts": None,
            },
        }
        poisoning = {
            pool_a: {
                "as_poisoner": 1,
                "as_target": 0,
                "event_types": ["zero_value_transfer"],
                "chains": ["base"],
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(
                org_interactions=org_interactions, poisoning=poisoning,
            ),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sigs = await _run_one_scan(lens, bus, sub)
        types_seen = {s.type for s in sigs}
        assert "cluster_detected" in types_seen
        assert "centrality_spike" in types_seen
        assert "subgraph_anomaly" in types_seen
        assert len(types_seen) >= 3, (
            f"Expected ≥3 distinct signal types, got {types_seen}"
        )
        for s in sigs:
            validate_signal(s)
    asyncio.run(go())


# ----- Resilience: L3 failures don't crash the lens ----------------------

def test_l3_interactions_query_failure_does_not_crash_lens():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(raise_on_interactions=True),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sigs = await _run_one_scan(lens, bus, sub)
        # No interaction-driven signals, but scan completed cleanly.
        assert all(s.type not in {"cluster_detected", "centrality_spike"}
                   for s in sigs)
    asyncio.run(go())


def test_l3_poisoning_query_failure_does_not_crash_lens():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(raise_on_poisoning=True),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sigs = await _run_one_scan(lens, bus, sub)
        # No poisoning-driven subgraph_anomaly, but scan completed cleanly.
        assert all(not (s.type == "subgraph_anomaly"
                        and "poisoning" in s.metadata.get("match_kind", ""))
                   for s in sigs)
    asyncio.run(go())


def test_l3_org_wallet_query_failure_does_not_crash_lens():
    async def go():
        addr = "0xpool1" + "0" * 34
        pools = [_pool(addr)]
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(raise_on_org=True),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sigs = await _run_one_scan(lens, bus, sub)
        # No legacy-org-wallet signals, scan still completed.
        assert all(not (s.type == "subgraph_anomaly"
                        and s.metadata.get("match_kind") == "org_wallet")
                   for s in sigs)
    asyncio.run(go())


def test_empty_monitored_set_emits_nothing():
    async def go():
        lens = GraphLens(FakeMonitoredSet([]), FakeL3Corpus())
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sigs = await _run_one_scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


def test_pool_without_address_skipped():
    async def go():
        pools = [{"address": "", "chain": "base"}]
        lens = GraphLens(FakeMonitoredSet(pools), FakeL3Corpus())
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        sigs = await _run_one_scan(lens, bus, sub)
        assert sigs == []
    asyncio.run(go())


# ----- Token-level candidate probing -------------------------------------

def test_lens_also_probes_token_addresses():
    """The lens probes both pool address AND token addresses against L3.
    Verify a token-level org_interaction match produces a signal."""
    async def go():
        pool_addr = "0xpool1" + "0" * 34
        t0_addr = "0xt0" + "0" * 38
        pool = _pool(pool_addr, t0=t0_addr)
        pools = [pool]
        # No org_interactions for the pool itself — only the token.
        # 5000 transfers > 100 threshold so centrality_spike fires
        # against the token address.
        org_interactions = {
            t0_addr: {
                "total_transfers": 5000,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 5000},
                "last_seen_ts": None,
            }
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(org_interactions=org_interactions),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.centrality_spike")
        sigs = await _run_one_scan(lens, bus, sub)
        assert len(sigs) == 1
        assert sigs[0].metadata["address"] == t0_addr
        validate_signal(sigs[0])
    asyncio.run(go())


# ----- run() loop semantics ----------------------------------------------

def test_max_scans_terminates_run_loop():
    async def go():
        pools = [_pool("0xpool1" + "0" * 34)]
        lens = GraphLens(
            FakeMonitoredSet(pools), FakeL3Corpus(),
            scan_interval_seconds=0.01,
            max_scans=3,
        )
        bus = EventBus()
        await lens.run(bus)
        assert lens.scan_count == 3
    asyncio.run(go())


def test_stop_event_terminates_run_loop():
    async def go():
        pools = [_pool("0xpool1" + "0" * 34)]
        lens = GraphLens(
            FakeMonitoredSet(pools), FakeL3Corpus(),
            scan_interval_seconds=10.0,  # long sleep — but stop_event fires
        )
        bus = EventBus()
        stop_event = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.05)
            stop_event.set()

        task = asyncio.create_task(lens.run(bus, stop_event=stop_event))
        stopper = asyncio.create_task(stop_soon())
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            pytest.fail("lens did not honor stop_event")
        await stopper
        assert lens.scan_count >= 0
    asyncio.run(go())


# ----- I-15 / I-16 invariant checks --------------------------------------

def test_lens_does_not_import_other_lenses():
    """I-15: no lens-to-lens imports."""
    import engine.lenses.graph.lens as mod
    src = open(mod.__file__).read()
    for forbidden in (
        "engine.lenses.stochastic",
        "engine.lenses.topology",
        "engine.lenses.game",
        "engine.lenses.information",
    ):
        assert forbidden not in src, (
            f"GraphLens imports forbidden lens module: {forbidden}"
        )


def test_emitted_signals_all_pass_validation_under_stress():
    """I-16: every emitted Signal validates cleanly, no exceptions."""
    async def go():
        pools = []
        org_interactions = {}
        poisoning = {}
        # Build a rich fixture: 5 addresses share org_001, plus one with
        # high transfer count, plus one poisoner, plus one laundry.
        for i in range(5):
            addr = f"0xa{i}" + "0" * 38
            pools.append(_pool(addr, chain="base"))
            org_interactions[addr] = {
                "total_transfers": 1000 + i * 200,
                "distinct_org_ids": ["org_001"],
                "primary_org_id": "org_001",
                "from_roles": {"gas_station": 1000 + i * 200},
                "last_seen_ts": None,
            }
        # Add a poisoner.
        addr_p = "0xpoisoner" + "0" * 31
        pools.append(_pool(addr_p, chain="arbitrum"))
        poisoning[addr_p] = {
            "as_poisoner": 1, "as_target": 0,
            "event_types": ["zero_value_transfer"], "chains": ["arbitrum"],
        }
        # Add a laundry-funded address.
        addr_l = "0xlaundry" + "0" * 32
        pools.append(_pool(addr_l, chain="optimism"))
        org_interactions[addr_l] = {
            "total_transfers": 17,
            "distinct_org_ids": ["org_002"],
            "primary_org_id": "org_002",
            "from_roles": {"laundry": 17},
            "last_seen_ts": None,
        }
        lens = GraphLens(
            FakeMonitoredSet(pools),
            FakeL3Corpus(
                org_interactions=org_interactions, poisoning=poisoning,
            ),
        )
        bus = EventBus()
        sub = await bus.subscribe("signal.graph.*")
        for _ in range(20):
            await lens._scan_once(bus)
            await asyncio.sleep(0)
        n = 0
        while not sub.queue.empty():
            sig = sub.queue.get_nowait()
            validate_signal(sig)
            n += 1
        assert n > 0
        assert lens.emitted_count == n
        assert lens.emit_failures == 0
        assert bus.validation_failure_count == 0
    asyncio.run(go())
