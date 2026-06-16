# D-010: Across static fee model — locked + first-run drift findings

**Date**: 2026-05-16
**Made by**: agent (per `PHASE_2_CROSS_CHAIN_SPEC.md` sub-phase 2.4 boundary — files the pre-staged `D-NNN_across-static-fee-model` decision)
**Status**: ACTIVE

## Context

Sub-phase 2.4 lands the Across fee verification mechanism: a static fee
table per (src_chain, dst_chain, token) derived from D-006's point
estimates, plus a one-shot deployment-time verifier
(`scripts/verify_across_fees.py`) that queries Across's public
`/api/suggested-fees` endpoint for every bridgeable tuple in the curated
token registry and rewrites the table if live fees drift > 5 bps.

The spec's I-13 invariant requires the table be frozen for the duration
of a run; this decision locks the mechanism + records what the first
live verification run produced.

## Options considered

1. **Static fees only, no verification** — cheap, but introduces silent
   bias if D-006 estimates are materially wrong. Rejected.
2. **Live API queries per opportunity** — most accurate, but adds an
   HTTP dependency in the hot path and creates a runtime coupling with
   Across's uptime. Rejected (violates I-13's intent).
3. **Static defaults + one-shot deployment-time verification** ← **CHOSEN**.
   Honors I-13 (frozen for the run), keeps detection deterministic, but
   forces a sanity-check against live values at deploy time. Sub-phase
   2.4's drift bands (5 bps no-op / 5–20 bps update / >20 bps abort)
   give a calibration mechanism for when D-006's estimates need
   revisiting.

## Decision

**Lock the static fee model + verification mechanism as implemented in
sub-phase 2.4.** D-006's point estimates are the *initial* values; the
verifier's drift-band logic adapts them at deploy time within the
spec's accepted sensitivity envelope.

Mechanism summary:

| Component | Behavior |
|---|---|
| Source-of-truth defaults | `bridge_model._DEFAULT_FEE_BPS_BY_TOKEN` (USDC 10, WETH 8, USDT 12, DAI 10, cbBTC 15 — all per D-006) |
| Latency haircut (I-14) | Per-token: stables 10 bps, WETH 15 bps, cbBTC 20 bps |
| Verification trigger | Run start (entrypoint.sh, gated on Phase 2 RPCs being set) |
| Drift ≤ 5 bps | Keep static value (no action) |
| Drift in (5, 20] bps | Rewrite table entry with live value, log info |
| Drift > 20 bps | ABORT deploy (exit non-zero, set -e in entrypoint.sh halts) |
| Persistent record | `<run_metadata_dir>/across_fee_table.json` (frozen post-write per I-13) |
| Frozen reload | `BridgeModel.load_verified_table(path)` at detector startup |

## First-run live verification results (2026-05-16, --dry-run)

20 tuples verified in 5.0s (well within the 60s spec budget). cbBTC is
Base-only in the registry so doesn't bridge; USDT isn't on Base in the
registry so only contributes the Arb↔OP pair.

| Token | Static (D-006) | Live (avg) | Drift | Action |
|---|---|---|---|---|
| USDC | 10.0 bps | 1.4 bps | 8.6 bps | updated |
| WETH | 8.0 bps | 1.2–1.3 bps | 6.7–6.8 bps | updated |
| USDT | 12.0 bps | 6.7 bps | 5.3 bps | updated |
| DAI | 10.0 bps | 6.7–13.1 bps | 3.1–3.3 bps | kept |

Summary: 14 updated, 6 kept (all DAI routes), 0 api_error, 0 aborts.

## Material finding: live fees ~5–10× lower than D-006 estimates

USDC at 1.4 bps live (vs 10 bps D-006 point estimate) is a structural
discovery, not noise — every USDC route on the Base/Arb/OP triangle
returned a sub-2-bps live quote. WETH at ~1.2 bps shows the same pattern.

Implications:

1. **H1' becomes more likely to be supported.** A 0.50% cross-chain
   margin floor with ~30 bps round-trip cost (10+10+10 from D-006) was
   the original calibration. The verified cost is ~13 bps (1.4 + 1.4 +
   10 USDC haircut) — about half. More inter-chain price-drift events
   will clear the floor at this lower cost basis.
2. **D-006's "fee sensitivity range: 5–20 bps"** floor is now below the
   live values. Not an immediate problem (live fees being LOWER than
   predicted is good for arbitrage profitability) but the range
   in D-006 should be revisited if it ever feeds H1/H3 sensitivity
   analysis. Filed as a follow-up consideration; D-006 not formally
   reversed because the chosen bridge model is still Across.
3. **The drift in the (5, 20] update band fires for 14/20 tuples.**
   That's a large fraction in the "needs adjustment" zone. If on a
   future re-verification the live fees move closer to the D-006
   defaults (i.e. Across's fee structure changes), the next deploy's
   verifier output will reflect that — no manual reconciliation needed.

## Rationale

- Static + verify is the only model that satisfies I-13 (frozen for
  the run) without giving up empirical calibration. Pure-static is
  brittle; pure-live violates the freeze.
- The drift bands map cleanly to D-006's sensitivity range: ≤5 bps is
  noise, 5–20 bps is reality-vs-estimate drift the spec already
  anticipated, >20 bps is a regime change requiring fresh judgment.
- One-shot verification at run start (not per-opportunity) means the
  hot path stays HTTP-dependency-free. The verifier itself runs against
  Across's documented public API (no auth), so it doesn't consume any
  protected resource.

## Consequences expected

- Deployment in sub-phase 2.8 will write `<run_metadata_dir>/across_fee_table.json`
  on cold start. Subsequent restarts skip re-verification (entrypoint.sh
  checks file existence) per I-13.
- Cross-chain detector's `CrossChainDetector` in sub-phase 2.5 will pick
  up the verified table via `BridgeModel.load_verified_table(...)` —
  load wiring lands when CrossChainDetector is integrated into
  `detect_dry_run.py` (sub-phase 2.5 work).
- Phase 2 H1' opportunity counting will benefit from realistic (lower)
  bridge costs.

## Reversal criteria

- **Across's public API surface changes shape** — `totalRelayFee.pct`
  no longer 18-decimal scaled, or the endpoint moves. Detected at next
  verification run via `api_error` count surge or aborts.
- **Live fees abort > 1 tuple at next verification** — would mean
  D-006's sensitivity range itself is stale. Action on reversal:
  re-spec D-006 with a wider range OR remove the affected routes from
  the registry.
- **Verifier wall time exceeds 60s consistently** — the spec budget
  cited per acceptance criterion. Mitigation: parallel fetching via
  `asyncio.gather` (currently sequential).
- **The Phase 2.8 deploy itself fails** on the verifier's abort path
  for any non-obvious reason — file a failure entry and pause Phase 2
  deployment until the abort cause is understood.

## Consequences for the memory system

- `decisions/README.md` active table — add row for D-010
- `unknowns/UNKNOWNS.md` — no change (UNK-007 already RESOLVED via D-006)
- `STRATEGY_STATE.md` EXP-002 — note "verified fees materially lower
  than D-006 baseline; H1' more likely supported"
- `INVARIANTS.md` — I-13 invariant carries unchanged (table-frozen
  semantics already specified)
- `failures/FAILURE_LOG.md` — no entries (verifier ran cleanly first try)

## Links

- Approving spec: `../../docs/archive/PHASE_2_CROSS_CHAIN_SPEC.md` sub-phase 2.4
- Bridge model premise: `D-006_bridge-model-across.md`
- Phase 2 umbrella: `D-009_phase-2-cross-chain-spec-approved.md`
- I-13 source: `../INVARIANTS.md`
- Verifier script: `../../layer3_trading_exp/scripts/verify_across_fees.py`
- Live-call dry-run output (2026-05-16): inlined above
