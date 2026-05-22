"""Phase 1.3 — Layer 3 filter evaluation orchestrator.

Each detected `Opportunity` runs through all 13 filter rules from the spec
(§Phase 1.3). No short-circuiting: every rule executes for every opportunity
so the JSONL log captures the full intelligence trace, regardless of which
rule fires first.

Per spec invariant #8 ("freshness awareness"): if any filter-critical L3 table
is older than 30 minutes, the evaluation is marked `degraded=True`. Phase 1.3
records this flag; the actual pause-new-evaluation behavior wires up in
Phase 1.4 (logging/measurement).

Per spec acceptance criterion: full evaluation per opportunity completes in
<= 500ms p95. With Layer3Client backed by local SQLite and ~5-8 unique
addresses per opportunity, expected per-evaluation time is well under that
budget (sub-millisecond per query × ~30 queries ≈ <50ms typical).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from .freshness import (
    DEFAULT_FRESHNESS_THRESHOLD_SECONDS,
    FILTER_CRITICAL_TABLES,
    PER_TABLE_THRESHOLDS_SECONDS,
    check_table,
)

if TYPE_CHECKING:
    from .layer3_client import Layer3Client
    from .opportunity_detector import Opportunity


class RuleSeverity(str, Enum):
    HARD = "hard"   # would block in Phase 2 execution
    SOFT = "soft"   # would modify size in Phase 2 execution
    WEAK = "weak"   # log-only signal
    LEGIT = "legit" # known-legit override


@dataclass(frozen=True)
class FilterResult:
    """One rule's outcome for one opportunity."""
    rule_id: int
    rule_name: str
    severity: RuleSeverity
    fired: bool
    matched_addresses: tuple[str, ...]
    details: dict

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["matched_addresses"] = list(self.matched_addresses)
        return d


@dataclass(frozen=True)
class FilterEvaluation:
    """Aggregate result of running all 13 rules against one opportunity.

    Phase 2 sub-phase 2.6 (D-009, D-012): `degraded_per_table` captures
    per-table staleness so H2 can distinguish "this table is stale but
    the rest are fresh" from "everything is stale" (Phase 1's collapsed
    bit). Default empty dict for v1 back-compat with code that hasn't
    been updated. `degraded` (legacy aggregate) now means "ANY filter-
    critical table stale per its per-table threshold".
    """
    opportunity_id: str
    evaluated_at: str  # ISO 8601 UTC
    results: tuple[FilterResult, ...]   # always 13 entries, ordered by rule_id
    l3_freshness: dict
    degraded: bool          # legacy aggregate; True iff any per-table stale
    hard_flagged: bool
    soft_flagged: bool
    has_legit_override: bool
    elapsed_ms: float
    degraded_per_table: dict = field(default_factory=dict)  # table -> bool

    def to_dict(self) -> dict:
        return {
            "opportunity_id": self.opportunity_id,
            "evaluated_at": self.evaluated_at,
            "results": [r.to_dict() for r in self.results],
            "l3_freshness": self.l3_freshness,
            "degraded": self.degraded,
            "degraded_per_table": dict(self.degraded_per_table),
            "hard_flagged": self.hard_flagged,
            "soft_flagged": self.soft_flagged,
            "has_legit_override": self.has_legit_override,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class AddressContext:
    """Addresses extracted from one opportunity for L3 querying.

    `contract_addresses` are the on-chain contracts directly involved in the
    arbitrage path (pool contracts, token contracts).

    `deployer_addresses` are resolved from L3's contracts table via lookup of
    each contract address; rules 8 and 11 query against deployers specifically.

    Both lists are deduplicated, lowercased, and order-preserving (insertion order).
    """
    contract_addresses: tuple[str, ...]
    deployer_addresses: tuple[str, ...]


def collect_address_context(opp: "Opportunity", l3: "Layer3Client") -> AddressContext:
    """Walk an Opportunity's legs and collect every contract address that the
    filter rules need to evaluate against L3.

    Address sources:
      - Each leg's pool contract address (2 unique pools)
      - Each leg's token_in / token_out (deduplicated; typically 2 unique tokens
        across both legs since they share the middle token and the borrow token)
      - Deployer of each contract above (looked up via L3's contracts table)
    """
    seen_contracts: list[str] = []
    seen_set: set[str] = set()
    for leg in opp.legs:
        for addr in (leg.pool_address, leg.token_in_addr, leg.token_out_addr):
            lower = addr.lower()
            if lower not in seen_set:
                seen_set.add(lower)
                seen_contracts.append(lower)

    seen_deployers: list[str] = []
    deployer_set: set[str] = set()
    for contract_addr in seen_contracts:
        row = l3.get_contract_classification(contract_addr)
        if row is None:
            continue
        deployer = row.get("deployer_address")
        if deployer is None:
            continue
        lower = deployer.lower()
        if lower not in deployer_set:
            deployer_set.add(lower)
            seen_deployers.append(lower)

    return AddressContext(
        contract_addresses=tuple(seen_contracts),
        deployer_addresses=tuple(seen_deployers),
    )


class FilterPipeline:
    """Orchestrates per-opportunity rule evaluation against Layer 3.

    Construct once with a Layer3Client and call `evaluate(opp)` per opportunity.
    Stateless beyond the client handle.
    """

    def __init__(
        self,
        l3: "Layer3Client",
        freshness_threshold_seconds: int = 30 * 60,
        now_fn=None,
        *,
        per_table_thresholds_seconds: dict[str, int] | None = None,
        default_threshold_seconds: int | None = None,
    ) -> None:
        # Phase 2 sub-phase 2.6 (D-012): per-table freshness thresholds.
        # `freshness_threshold_seconds` is retained as a Phase 1 back-compat
        # knob — when used without the per-table kwargs, the pipeline
        # behaves Phase 1-equivalently (single threshold for all tables).
        # When `per_table_thresholds_seconds` is provided (or defaults are
        # used), Phase 2 per-table semantics kick in.
        self._l3 = l3
        self._freshness_threshold = freshness_threshold_seconds
        self._per_table_thresholds = (
            dict(per_table_thresholds_seconds)
            if per_table_thresholds_seconds is not None
            else dict(PER_TABLE_THRESHOLDS_SECONDS)
        )
        self._default_table_threshold = (
            default_threshold_seconds
            if default_threshold_seconds is not None
            else DEFAULT_FRESHNESS_THRESHOLD_SECONDS
        )
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def evaluate(self, opp: "Opportunity") -> FilterEvaluation:
        from . import filter_rules

        t0 = time.perf_counter()
        ctx = collect_address_context(opp, self._l3)
        results: list[FilterResult] = []
        for rule in filter_rules.ALL_RULES:
            results.append(rule(opp, ctx, self._l3))
        results.sort(key=lambda r: r.rule_id)

        # Freshness snapshot for rules' tables. Phase 2 sub-phase 2.6
        # (D-012): per-table thresholds. Each filter-critical table is
        # checked against its own threshold; `degraded_per_table` captures
        # per-table results so H2 analysis can distinguish "specific table
        # stale" from "everything stale" (Phase 1's collapsed bit). The
        # legacy `degraded` aggregate is True iff ANY per-table is stale.
        freshness = {}
        degraded_per_table: dict[str, bool] = {}
        degraded = False
        now = self._now_fn()
        for table in FILTER_CRITICAL_TABLES:
            table_threshold = self._per_table_thresholds.get(
                table, self._default_table_threshold,
            )
            tf = check_table(self._l3.connection, table, table_threshold, now=now)
            freshness[table] = {
                "latest_ts": tf.latest_ts,
                "age_seconds": tf.age_seconds,
                "stale": tf.stale,
                "threshold_seconds": table_threshold,
            }
            degraded_per_table[table] = tf.stale
            if tf.stale:
                degraded = True

        hard = any(r.fired and r.severity == RuleSeverity.HARD for r in results)
        soft = any(r.fired and r.severity == RuleSeverity.SOFT for r in results)
        legit = any(r.fired and r.severity == RuleSeverity.LEGIT for r in results)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return FilterEvaluation(
            opportunity_id=opp.opportunity_id,
            evaluated_at=now.isoformat(),
            results=tuple(results),
            l3_freshness=freshness,
            degraded=degraded,
            degraded_per_table=degraded_per_table,
            hard_flagged=hard,
            soft_flagged=soft,
            has_legit_override=legit,
            elapsed_ms=elapsed_ms,
        )


__all__ = [
    "FilterPipeline",
    "FilterEvaluation",
    "FilterResult",
    "RuleSeverity",
    "AddressContext",
    "collect_address_context",
]
