# research_loop — Quant / Critic / Coder, repurposed to forensic mempool data

A LangGraph cyclic-debate loop (your spec), **pointed at the one place we hold
proprietary data and an open question**: the forensic ETH mempool capture's
*private-orderflow shadow* — not arbitraged-away price strategies. It does not
hunt trade alpha; it generates **falsifiable microstructure measurements**,
vets them through a deterministic gate apparatus, and its first-class output is
a documented, multiple-testing-aware **kill-log**. A `CONFIRMED_OOS` verdict is
rare and hard-won; if it fires often, suspect a softened gate, not alpha.

## The load-bearing inversion
> The LLM agents (Quant, Critic) can **only ever route toward rejection**. The
> only path to a positive verdict runs through two **pure-Python gates**
> (`HARD_GATE`, `OOS_GATE`) on a sha-sealed, burn-on-fail holdout. Hard metrics
> override the LLM; the LLM can never override hard metrics.

```
data_steward → quant ⇄ critic_theory → coder → sandbox_exec → HARD_GATE
   (seal      (debate, max 3)    (repair,   (run on TRAIN)   (deterministic;
   holdout)                       max 2)                      multiple-testing
        │                                                     corrected)
        └─VETO→ REJECTED_NO_DATA                                   │ IS passes
                                                                   ▼
   READY ◄─ FINALIZE ◄─ critic_postmortem ◄─ OOS_GATE ◄─ OOS_EXEC (holdout, once)
  (CONFIRMED_OOS)        (may only DOWNGRADE)  (sign must hold IS→OOS)
```
Every other edge leads to a `REJECTED_*` terminal. Two **separate** loop
counters: `iteration` (theory debate, ≤3) and `code_repair_iter` (a script that
won't run is not a hypothesis revision).

## Run it
```bash
pip install -r engine/research_loop/requirements.txt        # langgraph, duckdb, pydantic, anthropic
# No API key needed — runs the REAL measurement, scripts only the LLM turns:
python -m engine.research_loop.run --mock
# Live (needs a standalone key, separate from Claude Code):
setx ANTHROPIC_API_KEY "..."   &&   setx ANTHROPIC_MODEL "claude-sonnet-4-6"
python -m engine.research_loop.run
python -m pytest engine/research_loop/tests/                 # 18 tests, no key required
```

## What's where
| File | Role |
|---|---|
| `state.py` | typed Global State + `promotion_block()` (the asymmetry as code) |
| `gates.py` | `evaluate_hard_gate` / `evaluate_oos_gate` + holdout seal/tripwire — **no LLM** |
| `ledger.py` | process-shared SQLite multiple-testing ledger (Bonferroni vs running N) |
| `forensic_data.py` | window discovery, chronological train/holdout split, **measurement registry** |
| `llm.py` | `AnthropicClient` (live) + `MockLLMClient` (tests) |
| `agents.py` | quant / critic-theory / coder / critic-postmortem nodes + prompt teeth |
| `graph.py` | deterministic nodes + routers + the StateGraph wiring |

## Adding a hypothesis
Hypotheses are realized by **registered measurements** in `forensic_data.py`
(`MEASUREMENTS`). A measurement takes a list of window keys and returns
`{effect, edge_sign, n_obs, p_value, per_regime, coverage_fraction, notes}`.
The shipped one — `private_share_vs_blocksize` — measures per-block
private-inclusion share (the mined txs we never saw pending) vs block size.
Write a new function, register it, and the Quant can select it by key.

## Security note (registry vs. Docker)
The Coder **selects + parameterizes a vetted, read-only registry measurement**
rather than executing arbitrary LLM-written code. For a local tool on our own
read-only data that's the safe default. The spec's "Coder writes arbitrary
Python, run in a `--network none` Docker sandbox" is the production hardening —
add it only if you let the LLM emit free-form code.

## Honest framing (do not lose this)
The public trading edge here is **dead** (six prior OOS nulls + L3 retirement).
This loop's value is (a) a ruthless, credibility-producing **filter**, and (b)
turning the agents on the **private-orderflow footprint** our pipeline uniquely
measures. A clean documented NO-GO is the product, not a failure.
