"""Quant / Critic / Coder research loop — repurposed (Path B) to the forensic
ETH mempool capture, not OHLCV price strategies.

The public trading edge here is dead (six prior OOS nulls + L3 retirement);
the one place we hold proprietary data and an open question is the mempool
capture's PRIVATE-ORDERFLOW SHADOW. So this loop does NOT hunt price alpha:
it generates falsifiable MICROSTRUCTURE MEASUREMENTS, vets them through a
deterministic gate apparatus, and its first-class output is a documented,
multiple-testing-aware KILL-LOG. A `READY_FOR_PAPER_TRADING`-equivalent
verdict is rare and hard-won; if it fires often, suspect a softened gate.

Architecture (load-bearing inversion): the LLM agents can only ever route
toward REJECTION. The ONLY path to a positive verdict runs through two
pure-Python gates (HARD_GATE, OOS_GATE) on a sha-sealed, burn-on-fail
holdout. Hard metrics override the LLM; the LLM can never override metrics.
"""
