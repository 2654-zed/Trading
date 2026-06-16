"""Adapter shims around Phase 1+2 modules.

Per the Phase 3 spec § "Package layout" + I-15 (lens independence):
adapters provide the bridge between lenses and the existing Phase 1+2
infrastructure. Lenses import from `engine.adapters.*`, NOT from
`layer3_trading_exp.*`. The adapter layer is what gets swapped out
when Phase 4 transitions from Alchemy to bloxroute.

Concrete adapters here:
  - `monitored_set_phase2`: wraps `layer3_trading_exp.pool_set.PoolSet`
  - `l3_corpus_phase2`: wraps `layer3_trading_exp.layer3_client.Layer3Client`

Sub-phase 3.1 ships ONLY the read-only-snapshot adapters needed for
the graph lens. Stream adapters (BlockPoolStates → bus events) land in
sub-phase 3.2.
"""
