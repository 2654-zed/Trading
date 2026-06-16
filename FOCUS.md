# FOCUS

> Single source of truth for the effort. Keep it under two pages. When an open
> question is answered, fold the answer into the relevant section and move the
> dead thread to [docs/ARCHIVE.md](docs/ARCHIVE.md). Discussions happen in
> **GitHub Issues**, not email — link them below.
>
> _Last updated: 2026-06-14_

## Goal
Find and **rigorously validate** a tradeable signal our team can actually
capture with the resources we have — testing every candidate through a
sealed-holdout, multiple-testing-corrected gate *before* any capital. A
documented **NO-GO is an acceptable result**; a curve-fit "yes" is not.
**Current focus: CEX order-book microstructure ("price pressure") — "Game 3".**

## Resources
- **People:** Jason (lead/eng) · partner engineer (collection + analysis).
- **Data:**
  - **Tardis.dev Pro** — historical CEX L2 order books + trades + funding (primary for Game 3).
  - **`l2_collector`** — live CEX L2 recorder (Coinbase/Kraken/Binance.US/OKX), runs on the engineer's machine.
  - **bloXroute ETH mempool feed** ($300/mo) — on-chain forensic data (Game 2, deprioritized).
  - Free public ETH RPC (block ingest).
- **Code (this repo, `engine/`):** `research_loop` (the sealed-holdout / multiple-testing gate), `cex/l2_capture`, mempool capture + analysis.
- **Compute:** local machines (no colocation / no builder access — a hard constraint that ruled out the latency games).

## High-level roadmap (milestones)
- **M0 ✅ Pick the game.** Chose Game 3 (CEX order-book microstructure) over on-chain MEV (measured uneconomical) and factor alpha (deferred). See [DECIDE](handoff/DECIDE_pick-one-game.md).
- **M1 ◻ Assemble the CEX L2 dataset** — Tardis Pro history + live collector going forward.
- **M2 ◻ Reconstruction + "price-pressure" pipeline** — turn raw L2 into order-book imbalance / depth features.
- **M3 ◻ Pre-register the hypothesis** — imbalance → short-horizon move; define universe, horizon, and a realistic cost model *before* looking at returns.
- **M4 ◻ Test through the gate** — sealed holdout, sign-must-hold, multiple-testing correction → **GO (paper trade)** or **documented NO-GO**.
- **(Phase 2, optional) CEX↔DEX bridge** — relate Tardis to bloXroute for price-discovery/lead-lag, *only if* M4 shows promise.

## Status
- **At M1→M2.** Game 3 chosen; Tardis Pro acquired; `l2_collector` built, smoke-tested (3 exchanges, real depth), and handed to the engineer to run on their machine; the sealed-holdout research harness is built + tested (286 repo tests green).
- **Being worked on now:** the reconstruction + imbalance analyzer (M2) — the bridge from raw L2 to a testable price-pressure signal.

## Open questions / discussions
_(Each becomes a GitHub Issue — discuss there, link here, fold the answer back in.)_
- **Q1 — Universe:** which exchanges / pairs / depth for the study? _(Issue #__)_
- **Q2 — Tardis ↔ bloXroute:** how do they relate? **Answered:** market-level only (same asset, time-aligned); no tx-level join; CEX-DEX bridge is optional Phase 2. _(see [docs/DATA.md](docs/DATA.md) → "Relating venues")_
- **Q3 — Storage:** L2 is GBs/day; where do the raw bytes live (drive/bucket)? _(Issue #__)_
- **Q4 — Success criteria:** what result counts as GO vs. NO-GO for the imbalance study? _(Issue #__)_
- **Q5 — Repo name:** rename `layer3-trading-exp` (reflects the retired project)? _(Issue #__)_

## Archive
History and decided-against directions live in **[docs/ARCHIVE.md](docs/ARCHIVE.md)** — kept out of this doc so FOCUS stays current.
