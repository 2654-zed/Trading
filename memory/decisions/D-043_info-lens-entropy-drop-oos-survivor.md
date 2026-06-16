# D-043: `entropy_drop`-on-transfer-roles is the first OOS-surviving signal — new H9

**Date**: 2026-05-28
**Made by**: agent (info-lens investigation, user "look into the info lens")
**Status**: ACTIVE

## Context

After D-042 killed the multi-lens orchestrator (NO-GO), the silver lining was that the information lens alone carried ~0.29 correlation with real outcomes. This investigation decomposed the info lens by signal type × data source × horizon, with real DefiLlama price outcomes and a train/holdout out-of-sample split. $0 CU.

## Finding

The info lens's edge is NOT the lens broadly — it is concentrated almost entirely in ONE signal type on ONE data source: **`entropy_drop` on the `org_transfer_events.from_role` distribution.** And it is the FIRST signal in the entire project (across 6 prior H5 tests) to survive out-of-sample.

| Horizon | train corr(strength, signed return), n=12 | HOLDOUT corr, n=74 |
|---|---|---|
| 12h | +0.591 | **+0.409** |
| 24h | +0.740 | **+0.480** |
| 48h | +0.562 | **+0.506** |

- Holdout n=74, r≈0.48 → p < 0.0001. Robust across all three horizons. Predicts **direction** (signed return), not just magnitude.
- Mean |return| grows 6.8% / 13.6% / 28% at 12/24/48h (memecoin-scale moves).

Contrast (confirmed noise, OOS):
- `regime_surprise` (the MOST frequent type, 116/220 signals): sign-flips OOS (liquidity 24h holdout −0.558). This is what diluted the lens-level number to 0.29.
- `divergence_spike`, `entropy_drop`-on-liquidity: too rare to validate (0 holdout).

## Interpretation

Economic story: when the DIVERSITY of who transacts with a token collapses (Shannon entropy of the `from_role` mix drops — flow concentrates into one role), a price move follows. Concentration of flow precedes the move. L3's role-labeling is uniquely positioned to see this. The recurring project-wide lesson holds at every level: **the signal lives in one narrow place and every layer of aggregation buries it** (multi-lens buried it under graph noise; full info-lens buried it under regime_surprise noise).

## New hypothesis H9 (replaces the dead multi-lens H5 as the live thread)

> **H9**: An `entropy_drop` in a token's transfer-role distribution predicts its forward return (magnitude and/or direction) with a tradable edge after costs.

Status: **PROMISING — OOS-validated on magnitude+direction (r≈0.48, n=74 holdout), NOT yet cost- or survivorship-validated.**

## Caveats = the next make-or-break tests (filed as UNK-016)

1. **Survivorship bias** — 94% price coverage excludes dead/delisted tokens (which would show ≈−100%). Could inflate the DIRECTIONAL (positive) component. Magnitude correlation is more robust. #1 check.
2. **No cost model** — +0.5 corr on memecoins is meaningless until netted against memecoin-pool slippage/spread. The Phase 2 cost model (D-010/D-016) applies.
3. **Single 49-day window** — one market regime; needs replication on other periods.
4. **Train/holdout imbalance** — signal rare early (n=12), common late (n=74); holdout is the trustworthy figure but a regime shift is possible.

## Consequences

- The multi-lens architecture stays dead (D-042). The productive path is a **single-signal `entropy_drop`-on-roles detector**, validated for survivorship + costs + multi-window robustness.
- If H9 survives those, it — not the orchestrator — is the candidate for a future execution phase (still behind the Phase 5 gates + execution-authorization, which remain unmet/withheld).

## Links

- D-042 (multi-lens NO-GO + info-lens silver lining), D-040 (free price data), D-024 (info lens grounding)
- UNK-016 (H9 validation: survivorship + costs + replication) — opened by this decision
- Code: `engine/scripts/analyze_info_lens.py`; artifacts: `engine/data/info_lens_oos.log`
