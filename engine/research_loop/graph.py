"""LangGraph wiring. The topology IS the safety argument: every terminal that
says 'go' (finalize → CONFIRMED_OOS) sits downstream of two pure-Python gates
(hard_gate, oos_gate); the LLM nodes only ever route toward rejection."""

from __future__ import annotations

import time
from functools import partial

from langgraph.graph import StateGraph, START, END

from .agents import (
    PASS_BAR, MAX_ITERS, MAX_CODE_REPAIRS,
    quant_node, critic_theory_node, coder_node, critic_postmortem_node,
)
from .forensic_data import available_windows, split_windows, run_measurement
from .gates import (
    seal_holdout, verify_holdout, evaluate_hard_gate, evaluate_oos_gate,
)
from .state import CostModel, MetricBlock, GlobalState, promotion_block


# ── deterministic nodes (no LLM) ────────────────────────────────────────────

def data_steward_node(state: GlobalState) -> dict:
    """Seals the data substrate for the WHOLE hypothesis: train + sha-locked
    holdout. The honest VETO node — refuses to let the loop run on data we
    don't have."""
    windows = available_windows()
    if len(windows) < 4:
        return {"status": "REJECTED_NO_DATA",
                "reject_reason": f"only {len(windows)} overlapping capture windows; "
                                 "need ≥4 for a train/holdout split"}
    train, hold = split_windows(windows)
    contract = {"data_regime": "MEMPOOL_FORENSIC",
                "source_uri": "duckdb:engine/data/mempool/*.jsonl",
                "train_windows": train, "holdout_windows": hold,
                "holdout_sha256": seal_holdout(hold), "coverage_fraction": None}
    return {"data_contract": contract, "cost_model": CostModel()}


def _metricblock(res: dict, segment: str) -> MetricBlock:
    return MetricBlock(segment_label=segment, effect=res.get("effect"),
                       edge_sign=res.get("edge_sign"), n_obs=res.get("n_obs"),
                       p_value=res.get("p_value"), per_regime=res.get("per_regime", []),
                       coverage_fraction=res.get("coverage_fraction"),
                       notes=res.get("notes"))


def sandbox_exec_node(state: GlobalState) -> dict:
    """Run the chosen measurement on TRAIN only. (Subprocess/Docker hardening
    is the production upgrade — see README; here measurements are vetted,
    read-only registry functions over our own data.)"""
    plan, contract = state["measurement_plan"], state["data_contract"]
    try:
        res = run_measurement(plan["measurement_key"], contract["train_windows"])
    except Exception as e:  # noqa: BLE001 — a failed run is a repair signal, logged loud
        return {"is_metrics": None,
                "exec_log": [{"stage": "sandbox", "error": f"{type(e).__name__}: {e}"}]}
    return {"is_metrics": _metricblock(res, "IN_SAMPLE"),
            "exec_log": [{"stage": "sandbox", "ok": True, "n_obs": res.get("n_obs")}]}


def hard_gate_node(state: GlobalState, *, ledger) -> dict:
    is_m, contract = state["is_metrics"], state["data_contract"]
    prop = state["proposals"][-1]
    n = ledger.record(state["hypothesis_id"], prop["family_tag"], "IS",
                      ts=str(time.time()), p_value=is_m.p_value,
                      effect=is_m.effect, edge_sign=is_m.edge_sign)
    g = evaluate_hard_gate(is_m, contract["data_regime"], n)
    return {"hard_gate": g,
            "exec_log": [{"stage": "hard_gate", "ledger_n": n,
                          "is_stage_passed": g.is_stage_passed, "reasons": g.reasons}]}


def oos_exec_node(state: GlobalState) -> dict:
    """FIRST and ONLY touch of the sealed holdout for this hypothesis."""
    if state.get("holdout_consumed"):
        raise RuntimeError("holdout already consumed — refusing to double-dip (anti-overfit)")
    contract = state["data_contract"]
    verify_holdout(contract)
    res = run_measurement(state["measurement_plan"]["measurement_key"],
                          contract["holdout_windows"])
    return {"oos_metrics": _metricblock(res, "OUT_OF_SAMPLE"), "holdout_consumed": True}


def oos_gate_node(state: GlobalState) -> dict:
    ok, reason = evaluate_oos_gate(state["is_metrics"], state["oos_metrics"])
    g = state["hard_gate"]
    g.sign_holds_is_to_oos = ok
    g.reasons = g.reasons + [f"OOS_GATE: {reason}"]
    return {"hard_gate": g, "exec_log": [{"stage": "oos_gate", "ok": ok, "reason": reason}]}


def finalize_node(state: GlobalState) -> dict:
    block = promotion_block(state)        # belt-and-suspenders; must be None here
    if block:
        return {"status": "REJECTED_POSTMORTEM", "reject_reason": f"promotion blocked: {block}"}
    return {"status": "CONFIRMED_OOS"}


def _reject(status, reason):
    def node(state: GlobalState) -> dict:
        return {"status": status, "reject_reason": reason(state)}
    return node


# ── routers ─────────────────────────────────────────────────────────────────

def route_after_steward(s): return "END" if s.get("status") else "quant"

def route_after_theory(s):
    c = s["critiques"][-1]
    if c["verdict"] == "FAIL":
        return "reject_theory"
    if c["score"] >= PASS_BAR:
        return "coder"
    if s.get("iteration", 0) >= MAX_ITERS:
        return "reject_exhausted"
    return "quant"

def route_after_exec(s):
    if s.get("is_metrics") is not None:
        return "hard_gate"
    if s.get("code_repair_iter", 0) >= MAX_CODE_REPAIRS:
        return "reject_code_failed"
    return "coder"

def route_after_hard_gate(s):
    return "oos_exec" if s["hard_gate"].is_stage_passed else "reject_weak_is"

def route_after_oos_gate(s):
    return "critic_postmortem" if s["hard_gate"].sign_holds_is_to_oos else "reject_oos"

def route_after_postmortem(s):
    return "finalize" if s["critiques"][-1]["verdict"] == "PASS" else "reject_postmortem"


def build_graph(llm, ledger):
    g = StateGraph(GlobalState)
    g.add_node("data_steward", data_steward_node)
    g.add_node("quant", partial(quant_node, llm=llm))
    g.add_node("critic_theory", partial(critic_theory_node, llm=llm))
    g.add_node("coder", partial(coder_node, llm=llm))
    g.add_node("sandbox_exec", sandbox_exec_node)
    g.add_node("hard_gate", partial(hard_gate_node, ledger=ledger))
    g.add_node("oos_exec", oos_exec_node)
    g.add_node("oos_gate", oos_gate_node)
    g.add_node("critic_postmortem", partial(critic_postmortem_node, llm=llm))
    g.add_node("finalize", finalize_node)
    # terminal rejection nodes
    g.add_node("reject_theory", _reject("REJECTED_THEORY",
               lambda s: f"theory FAIL: {(s['critiques'][-1].get('flaws') or ['fatal flaw'])[0]}"))
    g.add_node("reject_exhausted", _reject("REJECTED_EXHAUSTED",
               lambda s: f"{MAX_ITERS} revisions, still below bar {PASS_BAR}"))
    g.add_node("reject_code_failed", _reject("REJECTED_CODE_FAILED",
               lambda s: f"measurement failed {MAX_CODE_REPAIRS}x: {(s.get('exec_log') or [{}])[-1]}"))
    g.add_node("reject_weak_is", _reject("REJECTED_WEAK_IS",
               lambda s: f"in-sample gate failed: {s['hard_gate'].reasons}"))
    g.add_node("reject_oos", _reject("REJECTED_OOS",
               lambda s: f"OOS gate failed: {s['hard_gate'].reasons[-1] if s['hard_gate'].reasons else ''}"))
    g.add_node("reject_postmortem", _reject("REJECTED_POSTMORTEM",
               lambda s: f"post-mortem FAIL: {(s['critiques'][-1].get('flaws') or ['empirical trap'])[0]}"))

    g.add_edge(START, "data_steward")
    g.add_conditional_edges("data_steward", route_after_steward, {"quant": "quant", "END": END})
    g.add_edge("quant", "critic_theory")
    g.add_conditional_edges("critic_theory", route_after_theory, {
        "quant": "quant", "coder": "coder",
        "reject_theory": "reject_theory", "reject_exhausted": "reject_exhausted"})
    g.add_edge("coder", "sandbox_exec")
    g.add_conditional_edges("sandbox_exec", route_after_exec, {
        "coder": "coder", "hard_gate": "hard_gate", "reject_code_failed": "reject_code_failed"})
    g.add_conditional_edges("hard_gate", route_after_hard_gate, {
        "oos_exec": "oos_exec", "reject_weak_is": "reject_weak_is"})
    g.add_edge("oos_exec", "oos_gate")
    g.add_conditional_edges("oos_gate", route_after_oos_gate, {
        "critic_postmortem": "critic_postmortem", "reject_oos": "reject_oos"})
    g.add_conditional_edges("critic_postmortem", route_after_postmortem, {
        "finalize": "finalize", "reject_postmortem": "reject_postmortem"})
    for term in ("finalize", "reject_theory", "reject_exhausted", "reject_code_failed",
                 "reject_weak_is", "reject_oos", "reject_postmortem"):
        g.add_edge(term, END)
    return g.compile()


__all__ = ["build_graph"]
