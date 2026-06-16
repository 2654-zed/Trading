"""The LLM-backed nodes (quant, critic-theory, coder, critic-postmortem).

These can ONLY ever route toward rejection. None of them can promote a
result — that is the deterministic gates' job. The prompts carry the teeth
the design demands; the JSON schemas force structured output."""

from __future__ import annotations

from .forensic_data import MEASUREMENTS
from .state import GlobalState

PASS_BAR = 80          # advisory: only decides whether to spend a backtest
MAX_ITERS = 3          # theory-debate cap (the user's hard 3)
MAX_CODE_REPAIRS = 2   # SEPARATE budget for "the script won't run"

_KEYS = sorted(MEASUREMENTS)

# ── system prompts (teeth) ──────────────────────────────────────────────────

QUANT_SYS = (
    "You are the Quant. You propose FALSIFIABLE microstructure measurements on a "
    "forensic ETH mempool capture (NOT price strategies — the public price edge is "
    "dead here, six OOS nulls). Rules: (1) every proposal must name the empirical "
    "result that KILLS it (falsifier) or you return a low-effort dud; (2) pre-register "
    "the predicted SIGN before any measurement — a post-hoc sign flip is fatal; "
    "(3) state the ECONOMIC MECHANISM, not 'it correlates'; (4) NEVER tune to the "
    "Critic's score — that is Goodhart and invalidates the loop; (5) most ideas are "
    "NULL and a clean 'this won't work' is a success. Pick a measurement_key from the "
    "registry that realizes your hypothesis. Declare family_tag honestly (variants of "
    "one idea share one multiple-testing budget).")

CRITIC_THEORY_SYS = (
    "You are the Critic (theory phase). YOU ARE NOT THE FINAL JUDGE — your score is "
    "an advisory pre-filter that only decides whether to spend compute on a backtest. "
    "The real gate is a deterministic OOS check on sealed data; if your verdict and the "
    "hard metrics ever disagree, the hard metrics win, always. Sycophancy is failure: "
    "emit at least one concrete named flaw. Score 0-100 against: OOS-splittability, "
    "sign pre-registration, multiple-testing exposure, mechanism (no mechanism caps you "
    "at 60), data-honesty (an edge living in the >50%% private-flow blind spot is "
    "untestable here = FAIL), and catastrophic-risk. verdict FAIL = fatal flaw (kill it); "
    "PASS = worth spending a backtest. A bare 'looks good' is forbidden.")

CODER_SYS = (
    "You are the Coder — an honest instrument. You do NOT improve the hypothesis or "
    "negotiate rules. Map the approved hypothesis to exactly one registered measurement "
    "and its params. The measurement is run on TRAIN windows only; the harness touches "
    "the sealed holdout exactly once, later. If the hypothesis cannot be realized by any "
    "registered measurement, pick the closest and let the metrics reject it honestly — "
    "never fabricate a metric.")

CRITIC_PM_SYS = (
    "You are the Critic (post-mortem phase). You see the EMPIRICAL in-sample and "
    "out-of-sample metrics. You may only DOWNGRADE a result, never upgrade one. Hunt "
    "hidden traps: an effect driven by one sub-window, a suspiciously clean number, an "
    "effect that only exists in the private-flow blind spot. verdict PASS only if you "
    "find no fatal empirical flaw; otherwise FAIL with the specific reason.")

# ── output schemas (force structured JSON) ──────────────────────────────────

QUANT_SCHEMA = {
    "type": "object", "properties": {
        "hypothesis": {"type": "string"},
        "math_definition": {"type": "string"},
        "variables": {"type": "object"},
        "falsifier": {"type": "string"},
        "predicted_sign": {"type": "string", "enum": ["+", "-"]},
        "family_tag": {"type": "string"},
        "measurement_key": {"type": "string", "enum": _KEYS},
    },
    "required": ["hypothesis", "math_definition", "falsifier",
                 "predicted_sign", "family_tag", "measurement_key"]}

CRITIC_SCHEMA = {
    "type": "object", "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "flaws": {"type": "array", "items": {"type": "string"}},
        "demands": {"type": "array", "items": {"type": "string"}}},
    "required": ["score", "verdict", "flaws"]}

CODER_SCHEMA = {
    "type": "object", "properties": {
        "measurement_key": {"type": "string", "enum": _KEYS},
        "params": {"type": "object"}},
    "required": ["measurement_key"]}

PM_SCHEMA = {
    "type": "object", "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
        "flaws": {"type": "array", "items": {"type": "string"}}},
    "required": ["verdict", "flaws"]}


# ── nodes ───────────────────────────────────────────────────────────────────

def quant_node(state: GlobalState, *, llm) -> dict:
    it = state.get("iteration", 0)
    prior = state.get("critiques") or []
    proposals = state.get("proposals") or []
    parts = [f"SEED BRIEF: {state.get('seed_brief')}",
             f"REGISTERED MEASUREMENTS: {_KEYS}"]
    if it > 0 and prior:
        last = prior[-1]
        parts.append(f"PRIOR PROPOSAL: {proposals[-1]['hypothesis'] if proposals else ''}")
        parts.append(f"CRITIC FLAWS TO RESOLVE: {last.get('flaws')}")
        parts.append(f"CRITIC DEMANDS: {last.get('demands')}")
        parts.append("Quote each flaw and state what changed, or concede it fatal. "
                     "Do NOT cosmetically re-skin the same idea.")
    p = llm.complete_json(QUANT_SYS, "\n".join(parts), QUANT_SCHEMA, role="quant")
    proposal = {
        "hypothesis": p["hypothesis"], "math_definition": p.get("math_definition", ""),
        "variables": p.get("variables", {}), "falsifier": p["falsifier"],
        "predicted_sign": p["predicted_sign"], "family_tag": p["family_tag"],
        "measurement_key": p["measurement_key"],
        "revision_of": proposals[-1]["hypothesis"] if proposals else None,
    }
    return {"proposals": [proposal], "iteration": it + 1}


def critic_theory_node(state: GlobalState, *, llm) -> dict:
    prop = (state.get("proposals") or [{}])[-1]
    user = (f"PROPOSAL: {prop.get('hypothesis')}\nMATH: {prop.get('math_definition')}\n"
            f"FALSIFIER: {prop.get('falsifier')}\nPREDICTED SIGN: {prop.get('predicted_sign')}\n"
            f"MEASUREMENT: {prop.get('measurement_key')}\nDATA: forensic mempool, single "
            f"vantage, ~41-52% of mined txs visible.")
    c = llm.complete_json(CRITIC_THEORY_SYS, user, CRITIC_SCHEMA, role="critic_theory")
    return {"critiques": [{"phase": "theory", "score": int(c["score"]),
                           "verdict": c["verdict"], "flaws": c.get("flaws", []),
                           "demands": c.get("demands", []), "author": "critic"}]}


def coder_node(state: GlobalState, *, llm) -> dict:
    cr = state.get("code_repair_iter", 0)
    is_repair = state.get("measurement_plan") is not None
    prop = (state.get("proposals") or [{}])[-1]
    user = (f"APPROVED HYPOTHESIS: {prop.get('hypothesis')}\n"
            f"SUGGESTED MEASUREMENT: {prop.get('measurement_key')}\n"
            f"REGISTERED: {_KEYS}")
    if is_repair:
        user += f"\nPRIOR RUN FAILED: {(state.get('exec_log') or [{}])[-1]}"
    plan = llm.complete_json(CODER_SYS, user, CODER_SCHEMA, role="coder")
    return {"measurement_plan": {"measurement_key": plan["measurement_key"],
                                 "params": plan.get("params", {})},
            "code_repair_iter": cr + (1 if is_repair else 0)}


def critic_postmortem_node(state: GlobalState, *, llm) -> dict:
    is_m, oos = state.get("is_metrics"), state.get("oos_metrics")
    user = (f"IN-SAMPLE: effect={is_m.effect} sign={is_m.edge_sign} n={is_m.n_obs} "
            f"p={is_m.p_value} per_regime={is_m.per_regime}\n"
            f"OUT-OF-SAMPLE: effect={oos.effect} sign={oos.edge_sign} n={oos.n_obs}\n"
            f"coverage_fraction={is_m.coverage_fraction} (effect is an UPPER bound).")
    c = llm.complete_json(CRITIC_PM_SYS, user, PM_SCHEMA, role="critic_postmortem")
    return {"critiques": [{"phase": "postmortem", "score": None,
                           "verdict": c["verdict"], "flaws": c.get("flaws", []),
                           "author": "critic"}]}


__all__ = ["PASS_BAR", "MAX_ITERS", "MAX_CODE_REPAIRS", "quant_node",
           "critic_theory_node", "coder_node", "critic_postmortem_node"]
