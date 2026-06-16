# docs/archive/ — preserved historical documents

These are **frozen snapshots** moved out of the repo root so the root stays a clean
single-source-of-truth (the live hub is [FOCUS.md](../../FOCUS.md); the curated
narrative of what was tried and dropped is [docs/ARCHIVE.md](../ARCHIVE.md)).

> **Reading note:** these documents are preserved **as written**. Any relative
> path *inside* them (e.g. `` `memory/INVARIANTS.md` ``, `` `../../PHASE_2…` ``)
> refers to that doc's **original location at the repo root**, not its new path
> here. Inbound links *to* these files (from live docs/code) were repointed; the
> docs' own internal text was left untouched on purpose.

## Charters & phase specs (superseded by FOCUS.md)
- **[LAYER3_TRADING_EXPERIMENT.md](LAYER3_TRADING_EXPERIMENT.md)** — original Phase 1 charter: detection-and-logging arbitrage research on Base, classified against Layer 3's behavioral corpus (hypotheses, invariants, build plan).
- **[PHASE_1_1_ADDENDUM.md](PHASE_1_1_ADDENDUM.md)** — the Phase 1.1 pool-enumeration pivot (factory event-scan → DefiLlama `/pools` + on-chain `getPool`); 2026-05-06 acceptance run (78 pools, ~$469M TVL).
- **[PHASE_2_CROSS_CHAIN_SPEC.md](PHASE_2_CROSS_CHAIN_SPEC.md)** — Phase 2 cross-chain (Base/Arbitrum/Optimism via Across), detection-only; built + gone live (D-009…D-015).
- **[PHASE_3_MULTI_LENS_ENGINE_SPEC.md](PHASE_3_MULTI_LENS_ENGINE_SPEC.md)** — the multi-lens decision-engine blueprint (signal schema, lenses, orchestrator, sub-phases 3.1–3.5).
- **[PHASE_4_REAL_OUTCOMES_SPEC.md](PHASE_4_REAL_OUTCOMES_SPEC.md)** — draft (never formally "approved") plan to swap proxy outcomes for real realized returns and re-test H5–H8; work since done (D-040/D-041).
- **[PHASE_5_EXECUTION_TRANSITION_SPEC.md](PHASE_5_EXECUTION_TRANSITION_SPEC.md)** — draft plan to move from read-only detection to live capital execution; **never started** (the Phase-4 gate failed, NO-GO — D-041/D-042).

## Lab & evidence reports
- **[LAB_REPORT_multi-lens-engine.md](LAB_REPORT_multi-lens-engine.md)** — completed report (2026-05-28): the synthesis-beats-best-lens thesis (H5) was falsified across six tests; only the narrow H9 `entropy_drop` signal survived OOS.
- **[EVIDENCE_SUMMARY_for-financiers.md](EVIDENCE_SUMMARY_for-financiers.md)** — 2026-06-03 financier-facing scorecard of the six independent OOS tests that each found no tradeable edge.
- **[BRIEFING_why-arbitrage-isnt-simple.md](BRIEFING_why-arbitrage-isnt-simple.md)** — 2026-06-02 partner briefing: naive cross-DEX/cross-chain arbitrage isn't profitable after costs + MEV competition.

## Financier correspondence (drafts — the email history this repo replaces)
- **[REPLY_to-financiers_draft.md](REPLY_to-financiers_draft.md)** — draft defending the OOS methodology and proposing a precisely-specified D1–D8 test.
- **[REPLY_to-financiers_bloxroute_draft.md](REPLY_to-financiers_bloxroute_draft.md)** — draft: bloXroute data is live-only (not backtestable); proposes a zero-capital live "shadow mode" scoring run.
