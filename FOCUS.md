# FOCUS

> Single source of truth for the effort. Keep it under two pages. When an open
> question is answered, fold the answer into the relevant section and move the
> dead thread to [docs/ARCHIVE.md](docs/ARCHIVE.md). Discussions happen in
> **GitHub Issues**, not email — link them below.
>
> _Last updated: 2026-06-16_

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
- **M2 ✅ Reconstruction + "price-pressure" pipeline** — OBI/depth features from Tardis `book_snapshot_25` (`engine/scripts/game3_imbalance.py`).
- **M3 ✅ Pre-registered** — imbalance → signed short-horizon move, with a realistic **taker cost model** (the decisive term).
- **M4 ✅ Tested → documented NO-GO (2026-06-20).** Day-blocked IS/OOS: OBI predicts direction and the **sign holds OOS at every horizon**, but the edge (~0.3–0.8 bps) is **~10–25× below taker fees (~8 bps round-trip)** → real but **maker-only**, untradeable from our (taker) seat. See [FEAS_game3-imbalance.md](docs/FEAS_game3-imbalance.md).
- **(Phase 2, optional) CEX↔DEX bridge** — relate Tardis to bloXroute for price-discovery/lead-lag, *only if* M4 shows promise.

## Status
- **Game 3 core question ANSWERED (2026-06-20): documented NO-GO for our position.** Order-book imbalance is a real, OOS-stable signal but lives **inside taker fees** (maker-only). The liquidation-cascade extension is also a NO-GO ([FEAS_liquidation-cascade.md](docs/FEAS_liquidation-cascade.md) — vol nowcast, no acceleration lead). Tardis Pro wired (`TARDIS_DEV`); puller + engineer onboarding shipped; ~660 MB book/liq/ticker cached.
- **Through-line across ALL branches** (MEV, drains, liquidations, imbalance): every edge we find is **positional** — real but requiring a seat (maker / colocation / builder access) we don't have. Our position can *see* the edges but can't *capture* them.
- **Factor pivot — also NO-GO (2026-06-20, $0 K0 kill).** The one direction *not* gated by our
  seat fails on **statistics**: a realistic ~0.5-Sharpe crypto factor is unconfirmable on a
  buyable history (~16yr needed single-test; worse with multiple-testing), and the existing
  harness false-GOs pure noise 100% of the time. See [FEAS_factor-pivot.md](docs/FEAS_factor-pivot.md).
- **Every branch is now a documented NO-GO** (L3 labels, multi-lens, MEV/backruns, drains,
  liquidations, order-book imbalance, factor): microstructure edges fail on **position**, the
  factor edge fails on **confirmability**. **From this seat + history + budget, there is no
  demonstrated, capturable, confirmable edge.** → **Decision: wind down** (capstone pending).

## Open questions / discussions
_(Each becomes a GitHub Issue — discuss there, link here, fold the answer back in.)_
- **Q1 — Universe:** which exchanges / pairs / depth for the study? _([#1](https://github.com/2654-zed/layer3-trading-exp/issues/1))_
- **Q2 — Tardis ↔ bloXroute:** how do they relate? **Answered:** market-level only (same asset, time-aligned); no tx-level join; CEX-DEX bridge is optional Phase 2. _(see [docs/DATA.md](docs/DATA.md) → "Relating venues")_
- **Q3 — Storage:** L2 is GBs/day; where do the raw bytes live (drive/bucket)? _([#2](https://github.com/2654-zed/layer3-trading-exp/issues/2))_
- **Q4 — Success criteria:** what result counts as GO vs. NO-GO for the imbalance study? _([#3](https://github.com/2654-zed/layer3-trading-exp/issues/3))_
- **Q5 — Repo name:** rename `layer3-trading-exp` (reflects the retired project)? _([#4](https://github.com/2654-zed/layer3-trading-exp/issues/4))_

## Archive
History and decided-against directions live in **[docs/ARCHIVE.md](docs/ARCHIVE.md)** — kept out of this doc so FOCUS stays current.
