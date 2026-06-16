# D-047: L3 retirement revelation — the capstone, and trading-software wind-down

**Date**: 2026-06-08
**Made by**: user (shared L3 `WHAT_WE_LEARNED.md`) + agent (impact analysis)
**Status**: ACTIVE — capstone of the trading research arc

## The revelation

Layer 3 (the upstream surveillance system this trading project consumes) was **retired 2026-06-08** with an honest, adversarially-reviewed retrospective (`Desktop/ai lang/reports/WHAT_WE_LEARNED.md`). Its central finding, against its own prior claims:

> **Adversarial-ness is a property of CONTROL, not appearance. The system inferred intent from on-chain SHAPE, and that is structurally broken.**

And — load-bearing for us — **L3 retired its own behavioral labels as unreliable.** Its own framing: *"observations sound, labels suspect."* Specifics from the retrospective:
- Org mapping collapsed: 1 drained contract, 0 draining deployers across the entire layer.
- "Criminal orgs" were fabrications (the $285M "org_001" was a DPRK Solana hack swept in by a SQL `LIKE` bug, on a chain L3 never monitored).
- "1,650 confirmed adversarial" included Chainlink oracles as false positives; ≥7% verified-legit.
- "3,437 → briefly 44,540 drains" → actually **266 confirmed** real drains.
- `bytecode_families` table empty; "org roles" (gas_station/laundry/etc.) part of the collapsed org-mapping layer.
- The ONE thing that worked: a **deductive control-fact** test (`tx.from ≠ victim`) → 266 real drains, Tier-A. Everything shape-based failed.

## Why this is the root cause of our six nulls

**Our entire trading signal was built on L3's behavioral LABELS — exactly the layer now declared unreliable:**
- **H9** = `entropy_drop` on the `from_role` distribution (gas_station/laundry/unknown). Those role labels are part of the org-wallet classification that **collapsed**. H9 computed entropy over largely-mislabeled noise.
- The **multi-lens engine** and **froth→ETH** tests consumed the same suspect classifications.

A signal built on noise labels fits in-sample and fails out-of-sample — the textbook signature, and exactly what H9 did (live OOS negative, D-044/UNK-016). We diagnosed this *empirically* (the OOS collapse); the L3 retrospective diagnoses it *causally* (the labels were never real).

## The unifying lesson

The two projects failed the same way:
- **L3**: you cannot infer **intent** from on-chain **shape**.
- **Trading**: you cannot predict **price** from on-chain behavioral **labels**.

We built a price-prediction layer on top of an intent-detection layer **that didn't detect intent** — a bet stacked on a bet that had already lost. Deepest shared finding: **on-chain shape tells you neither intent nor price; control does, and control is granted/stolen off-chain — where neither system could see.**

## Consequences

1. **The H9 paper trade's input is contaminated.** It runs on `from_role` labels now known suspect; even a positive result would be uninterpretable. → wound down (this decision; scheduled task disabled).
2. **The "unique proprietary-data edge" premise is gone.** That was the entire justification for the project. With L3's behavioral data unreliable by its own makers' assessment, the only remaining path the financiers offer is the pure speed/MEV arms race with **no data advantage** — which a late, under-capitalized entrant loses (D-046).
3. **The surveillance-product reframe is also undermined.** L3 failed at intent-detection too; the intelligence was mostly false positives. Only the narrow deductive drain detector survives — a forensic tool, not a signal or a product.

## What survives (real, Tier-A)

Raw transfer observations (sound; labels suspect); independent price data (DefiLlama/Hyperliquid); the out-of-sample testing + correction discipline (the actual asset, on both projects); the 266-drain deductive forensic set.

## Decision

**Wind down the trading software alongside L3.** Its premise is falsified twice over: (a) the L3 labels it consumed are unreliable, and (b) even reliable behavioral data doesn't predict price (six OOS nulls). The H9 paper trade is stopped (input contaminated). No capital is or will be deployed (I-1/I-3, D-037 stand). The corpus + code + decision record are retained read-only as the honest record.

Not yet done (user deferred): updating the financier-facing summary to reflect that the proprietary-data edge no longer exists.

## Links

- `Desktop/ai lang/reports/WHAT_WE_LEARNED.md` (L3 retrospective)
- D-044 (H9 validated-then-live-negative), D-045 (six-way negative), D-046 (LevelX/bloXroute), UNK-016
- `LAB_REPORT_multi-lens-engine.md`, `EVIDENCE_SUMMARY_for-financiers.md`
