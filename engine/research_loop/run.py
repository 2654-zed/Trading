"""Entrypoint: run ONE hypothesis through the loop to a terminal verdict.

    python -m engine.research_loop.run --mock     # no API key; real measurement
    python -m engine.research_loop.run            # live (needs ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import argparse
import sys

from .graph import build_graph
from .ledger import Ledger
from .state import new_state

_DEFAULT_SEED = ("Propose a falsifiable measurement of the PRIVATE-ORDERFLOW "
                 "footprint visible in our forensic mempool capture (the mined "
                 "txs we never saw pending). NOT a price strategy.")


def _mock_llm():
    """A scripted happy-path mock: quant proposes the registered measurement,
    critic PASSes theory, coder selects it. The hard/OOS gates then run the
    REAL measurement on the REAL archives, so the final verdict is honest."""
    from .llm import MockLLMClient
    return MockLLMClient({
        "quant": [{"hypothesis": "Per-block private-inclusion share rises with block size",
                   "math_definition": "r(private_share_b, n_tx_b) over blocks b",
                   "falsifier": "sign of r does not hold out-of-sample",
                   "predicted_sign": "+", "family_tag": "private_footprint",
                   "measurement_key": "private_share_vs_blocksize"}],
        "critic_theory": [{"score": 85, "verdict": "PASS",
                           "flaws": ["coverage is a single vantage; effect is an upper bound"],
                           "demands": ["confirm sign holds OOS"]}],
        "coder": [{"measurement_key": "private_share_vs_blocksize", "params": {}}],
        "critic_postmortem": [{"verdict": "PASS", "flaws": []}],
    })


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", default=_DEFAULT_SEED)
    p.add_argument("--hypothesis-id", default="H1")
    p.add_argument("--ledger", default="engine/data/research_loop/ledger.db")
    p.add_argument("--mock", action="store_true")
    args = p.parse_args()

    llm = _mock_llm() if args.mock else __import__(
        "engine.research_loop.llm", fromlist=["AnthropicClient"]).AnthropicClient()
    ledger = Ledger(args.ledger)
    graph = build_graph(llm, ledger)
    final = graph.invoke(new_state(args.hypothesis_id, args.seed),
                         {"recursion_limit": 50})

    print("=" * 64)
    print(f"VERDICT : {final.get('status')}")
    if final.get("reject_reason"):
        print(f"REASON  : {final['reject_reason']}")
    ism, oos = final.get("is_metrics"), final.get("oos_metrics")
    if ism:
        print(f"IS      : effect={ism.effect} sign={ism.edge_sign} n={ism.n_obs} p={ism.p_value}")
    if oos:
        print(f"OOS     : effect={oos.effect} sign={oos.edge_sign} n={oos.n_obs}")
    print(f"ledger N: {ledger.count()}")
    print("NOTE    : a documented NO-GO is a first-class result, not a failure.")
    ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
