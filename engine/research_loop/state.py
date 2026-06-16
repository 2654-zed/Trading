"""The Global State object + the promotion arithmetic (the asymmetry as code).

Most fields exist to make a single thing checkable: a positive verdict is a
deterministic function of the metrics, NOT of any LLM's confidence.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

# ── terminal / enum vocabulary ──────────────────────────────────────────────
Verdict = Literal["PASS", "FAIL"]
DataRegime = Literal[
    "MEMPOOL_FORENSIC",        # measurement against our DuckDB landed/replaced/ghost data
    "MEMPOOL_PARTIAL_VANTAGE",  # same, but the edge could live in the >50% we can't see
    "PRICE_SERIES",            # needs a real declared OHLCV source (we don't have one)
    "UNTESTABLE",
]
Terminal = Literal[
    "CONFIRMED_OOS",           # the Path-B analogue of READY_FOR_PAPER_TRADING (rare)
    "REJECTED_NO_DATA", "REJECTED_THEORY", "REJECTED_EXHAUSTED",
    "REJECTED_CODE_FAILED", "REJECTED_WEAK_IS", "REJECTED_OOS",
    "REJECTED_POSTMORTEM",
]


class CostModel(BaseModel):
    """Frozen per hypothesis. For PRICE_SERIES it bakes the realistic costs
    that killed the dead edge; for MEMPOOL_FORENSIC measurements it is N/A
    but the coverage caveat below stands in for 'cost of being wrong'."""
    taker_fee_bps: float = 0.0
    slippage_model: Literal["constant_product", "linear_bps", "none"] = "none"
    assumed_notional_usd: float = 0.0
    gas_cost_usd: float = 0.0
    frozen: bool = True


class MetricBlock(BaseModel):
    """IS / OOS kept physically distinct so 'sign must hold IS→OOS' is a real
    comparison, not a vibe. For forensic measurements `effect`/`edge_sign`/
    `n_obs`/`p_value` are the load-bearing fields; the trading fields stay
    None and the gate skips them when data_regime != PRICE_SERIES."""
    segment_label: Literal["IN_SAMPLE", "OUT_OF_SAMPLE"]
    # universal (used for BOTH measurements and strategies)
    effect: Optional[float] = None          # the measured statistic (corr, mean diff, …)
    edge_sign: Optional[Literal["+", "-", "0"]] = None
    n_obs: Optional[int] = None             # blocks / trades / observations
    p_value: Optional[float] = None         # single-test, BEFORE multiple-testing correction
    per_regime: list[dict] = Field(default_factory=list)  # effect within each sub-window
    coverage_fraction: Optional[float] = None  # ~0.41–0.52 → effect is an UPPER bound
    # trading-only (PRICE_SERIES); None for forensic measurements
    net_sharpe_after_costs: Optional[float] = None
    max_drawdown: Optional[float] = None
    excess_return_vs_bench: Optional[float] = None
    notes: Optional[str] = None


class HardGate(BaseModel):
    """The non-LLM judge. There is no field by which an LLM overrides it."""
    sign_holds_is_to_oos: Optional[bool] = None      # DECISIVE
    mc_corrected_significant: Optional[bool] = None   # survives BH vs ledger N
    min_obs_met: Optional[bool] = None
    regime_robust: Optional[bool] = None
    no_lookahead_verified: Optional[bool] = None
    survives_costs: Optional[bool] = None             # PRICE_SERIES only; auto-True for forensic
    market_relative_ok: Optional[bool] = None         # PRICE_SERIES only; auto-True for forensic
    # pre-registered, immutable once the measurement runs
    min_obs_floor: int = 30
    reasons: list[str] = Field(default_factory=list)

    @property
    def is_stage_passed(self) -> bool:
        """The IS-checkable subset — decides whether to spend the holdout.
        EXCLUDES sign_holds_is_to_oos, which is only knowable after OOS."""
        checks = [self.mc_corrected_significant, self.min_obs_met,
                  self.regime_robust, self.no_lookahead_verified,
                  self.survives_costs, self.market_relative_ok]
        return all(c is True for c in checks)

    @property
    def passed(self) -> bool:
        """Full gate incl. the OOS sign-holds law — only this clears promotion."""
        return self.is_stage_passed and self.sign_holds_is_to_oos is True


class AlphaProposal(TypedDict):
    hypothesis: str
    math_definition: str       # deterministic f(info ≤ t)
    variables: dict
    falsifier: str             # what observation KILLS it (unfalsifiable ⇒ auto-reject)
    predicted_sign: str        # pre-registered BEFORE the measurement
    family_tag: str            # variants of one idea share ONE multiple-testing budget
    measurement_key: Optional[str]   # which registered forensic measurement realizes it
    revision_of: Optional[str]


class DataContract(TypedDict):
    data_regime: DataRegime
    source_uri: str            # 'duckdb:engine/data/...' — 'synthetic'/'TODO' ⇒ auto-VETO
    train_windows: list[str]   # hourly window keys, e.g. '20260610_11'
    holdout_windows: list[str]  # SEALED; read once, by oos_exec only
    holdout_sha256: str
    coverage_fraction: Optional[float]


class GlobalState(TypedDict, total=False):
    # identity / counters / tripwires
    hypothesis_id: str
    seed_brief: str
    iteration: int             # theory-debate counter (MAX_ITERS guard)
    code_repair_iter: int      # SEPARATE sandbox-failure counter
    holdout_consumed: bool
    # append-only audit artifacts
    proposals: Annotated[list[AlphaProposal], operator.add]
    critiques: Annotated[list[dict], operator.add]
    exec_log: Annotated[list[dict], operator.add]
    # data + results
    data_contract: Optional[DataContract]
    cost_model: Optional[CostModel]
    measurement_plan: Optional[dict]    # {measurement_key, params}
    is_metrics: Optional[MetricBlock]
    oos_metrics: Optional[MetricBlock]
    hard_gate: HardGate
    # terminal
    status: Optional[Terminal]
    reject_reason: Optional[str]


def promotion_block(s: GlobalState) -> Optional[str]:
    """Returns a BLOCK reason, or None if a positive verdict is allowed.
    The math wins — this is the only function that may clear the way to
    CONFIRMED_OOS, and no LLM field appears in it except a required PASS."""
    dc = s.get("data_contract")
    if dc is None or dc["source_uri"].startswith(("synthetic", "TODO")):
        return "Data substrate unspecified/synthetic — refuse to promote an ungrounded result."
    hg = s.get("hard_gate")
    if hg is None or not hg.passed:
        return "Hard OOS/regime/multiple-testing gate not satisfied — LLM confidence cannot override."
    crits = s.get("critiques") or []
    if not crits or crits[-1].get("verdict") != "PASS":
        return "Critic has not issued PASS on the empirical post-mortem."
    return None


def new_state(hypothesis_id: str, seed_brief: str) -> GlobalState:
    return GlobalState(
        hypothesis_id=hypothesis_id, seed_brief=seed_brief,
        iteration=0, code_repair_iter=0, holdout_consumed=False,
        proposals=[], critiques=[], exec_log=[],
        data_contract=None, cost_model=None, measurement_plan=None,
        is_metrics=None, oos_metrics=None, hard_gate=HardGate(),
        status=None, reject_reason=None,
    )


__all__ = ["Verdict", "DataRegime", "Terminal", "CostModel", "MetricBlock",
           "HardGate", "AlphaProposal", "DataContract", "GlobalState",
           "promotion_block", "new_state"]
