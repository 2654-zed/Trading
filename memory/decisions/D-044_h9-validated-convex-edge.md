# D-044: H9 validated — `entropy_drop` is a real convex edge (UNK-016 resolved)

**Date**: 2026-05-28
**Made by**: agent (H9 gate battery, user-approved each step)
**Status**: ACTIVE — resolves UNK-016

## Context

D-043 isolated `entropy_drop`-on-transfer-roles as the first OOS-surviving signal and opened UNK-016 with three validation gates. All three are now run. $0 CU throughout (the offered 77M Alchemy CU was deliberately NOT spent — see below).

## Verdict: H9 VALIDATED (with a convex-profile + single-window caveat)

| Gate | Result |
|---|---|
| #1 Survivorship | PASSED — 0 of 86 signals fired on a token that died mid-window; death-aware corr == survivor-only. |
| #3 Beta-vs-alpha | PASSED — market mean only ~+1% (not a bull run); corr(strength, EXCESS return) = +0.46 @24h / **+0.49–0.50 @48h** (≈ raw corr) → real alpha. 48h regime-robust (+0.50 up / +0.58 down); 24h inverts in down-markets (use 48h). |
| #2 Costs | PASSED at tradeable size — strong cohort (top-half strength, n=43) mean GROSS 48h +44.9% vs weak +1.2% (spread +43.7%). After fees + realistic round-trip slippage (entry on X, exit on the post-move bag X·(1+gross), constant-TVL conservative): mean_net **+37.3% @ $10k, +16.1% @ $50k, −2.1% @ $100k**. Survives outlier removal: **mean ex-top-3 = +18.8%**. |

**The edge is real and cost-surviving because the signal self-selects LIQUID pools** (strong-cohort TVL min $1.06M / median $2.28M) — it needs transfer volume to fire, so it can't land on thin traps. Slippage only dominates above ~$100k/trade.

## Honest caveats (why this is "validated research direction," not "trade now")

1. **Convex / low-win-rate profile**: 42% win rate, negative median (−2%), fat right tail (p90 +190%, top winner +299%). Positive EV is carried by a minority of large winners (though it survives dropping the top 3). High variance; realizing the mean needs many trades + bankroll. NOT a steady edge.
2. **Single window, n=43**: one 49-day period. The +37% point estimate has a wide confidence interval — the SIGN is robust across gates, the MAGNITUDE is uncertain.
3. **No second out-of-sample window possible without resuming L3 sync** — the signal is L3-bound (role labels are L3-proprietary, end 2026-05-18). Alchemy CU cannot reconstruct it. This is the #1 thing that would strengthen/kill H9.

## Why the 77M CU was NOT spent

The one place CU could help (exact pool depth for exit slippage) was rendered moot by a $0 model correction (sizing exit slippage on the post-pump position) which showed the exit-cost concern was overblown — the liquid pools absorb it. CU cannot address the real remaining limitation (a second time-window, which is L3-bound). CU stays banked for a genuine high-fidelity need later (e.g. a paper-trading stage). This continues the project's free-first / CU-only-if-borderline discipline.

## Consequences + recommended next step

- **The multi-lens engine stays dead (D-042).** The productive artifact from all of Phase 3/4 is this ONE signal, studied standalone.
- **Phase 5 (execution) remains NO-GO** under the formal gate (H5 ≥15pp multi-lens). H9 is a *different*, single-signal thesis; it is promising but not yet multi-window-validated, so it does not by itself open execution.
- **Highest-value next step (≈$0 Alchemy CU): resume L3 sync** (the surveillance system at `Desktop/ai lang`) to extend role-labeled data past 2026-05-18, then replicate H9 on the fresh out-of-sample window. If it holds on a second window, H9 becomes a genuine candidate for a focused single-signal execution study (still behind the I-1 amendment + execution-authorization gates).

## Reversal triggers

- Second-window replication fails → H9 was period-specific; downgrade to NOT-SUPPORTED.
- Realistic backtest with proper position-sizing/variance modeling shows risk-of-ruin too high → not tradable despite positive EV.

## Links

- D-043 (H9 + the OOS finding), UNK-016 (resolved here), D-042 (multi-lens NO-GO), D-040 (free price data)
- D-037 (execution gate, stays closed), PHASE_5 §1 (unmet)
- Artifacts: `engine/data/info_lens_{analysis,oos,survivorship,regime,costs3}.log`
- Code: `engine/scripts/check_info_lens_{survivorship,regime,costs}.py`, `analyze_info_lens.py`
