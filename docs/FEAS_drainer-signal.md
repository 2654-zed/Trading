# FEAS — Drainer-signal feasibility

> Decision scope: is the `third_party_transferfrom` flag worth turning into
> something, and does it justify keeping the paid bloXroute stream alive?
> Runs **offline on the existing 9-day archive, $0, with the live stream OFF.**
> _Dated 2026-06-17. Not a committed effort — a go/no-go gate before one._

## The question, and two hard gates
The flag fires on pending txs where an EOA calls `transferFrom` to move *someone
else's* tokens — the allowance-spend footprint. 138,957 flags over 9 days, 91%
USDT/USDC, but **dominated by benign CEX deposit-sweepers** (top-12 initiators =
52.7%). To be worth anything it must clear **both** gates; either failing = NO-GO:

- **Gate A — Extractability.** Can we separate real drains from the benign
  sweeper majority at useful precision, offline, $0?
- **Gate B — Actionability + novelty.** Even if we can, is there something we can
  *do* from our position (laptop, public feed, no victim keys) that isn't already
  owned by an incumbent or physically impossible?

## Bottom line up front
**Gate A is feasible. Gate B is mostly already failed for the as-stated thesis —
and neither surviving path needs the paid stream.** Specifics:

- **Extractability ✅ feasible, $0.** A credible discriminator exists from
  archive-only features (initiator→owner fanout, (initiator,owner) recurrence,
  `to ≠ initiator`, non-canonical spender) as a cheap pre-filter, then free-RPC
  enrichment (drain-ratio vs balance, EOA check, destination freshness) only on
  the residue, confirmed via the existing ghost-tx join. *Caveat: `amount` isn't
  in the flag file — re-join to the raw mempool `input` first.*
- **Actionability ⛔ every value-capture path is dead for us.** Rescue front-run,
  approval-revocation race, and backrun/MEV all require (a) the victim's private
  key (we don't have it) and/or (b) winning a same-block builder auction from a
  shared public feed with reconnect gaps (we can't). Don't build anything whose
  value depends on beating a block.
- **Novelty 🟥 crowded, and the comparable died.** The dominant defense layer is
  **pre-sign simulation** (Blockaid powers MetaMask/Coinbase Wallet) — strictly
  earlier and better than reacting to the mempool. **Webacy** already ships the
  exact mempool-alert + one-click-evacuate product. **Harpie** built almost
  exactly the front-run-the-drainer firewall and **shut down (Mar 2025) for lack
  of a sustainable business model.** And bloXroute's *own* Protect RPC keeps txs
  *out* of the public mempool — so the highest-value (private/Flashbots) drains
  are invisible to a public-feed approach by construction.
- **The only survivors are detection/intel, not execution:** a forensics /
  attribution dataset (and a feed downstream of it). Its lone real edge is the
  **"ghost transaction" angle** — drains seen pending but never mined — which
  post-hoc-only competitors can't see. **But forensics is retrospective and runs
  on free confirmed-block data — it does NOT need the live $300/mo feed.**

→ **So the feasibility test is cheap and worth running, but reframe it: it tests a
forensics dataset, and it's decoupled from the paid stream. Even a GO does not, by
itself, justify keeping bloXroute on.**

## Measured results (RAN 2026-06-17 — P1 + P2/P3 + on-chain spot-check)
Ran the whole test on the existing archive, $0, stream untouched.
**Outcome: Gate A PASSES (the signal really works); Gate B still FAILS — overall
NO-GO as a product, and it does NOT justify the paid stream.**

- **P1 (archive-only pre-filter):** benign CEX-sweeper bulk concentrates hard —
  **68 addresses = 51%** of all 139,236 flags, removable for free. Tight
  drainer-shaped residue (low-fanout · one-shot pair · 3rd-address payout) = **532**.
- **P2 (scam lists):** **0** of 139,236 flags overlap the Scam Sniffer drainer
  list — but this is the reactive-list recall floor, NOT absence of drains (below).
- **P3 + spot-check:** the stable-≥$10 / victim-emptied / fresh-payout cell = **91**.
  On-chain spot-check of 8 (spread $10→$175k): **7 of 8 were real approval-phishing
  drains** (median ~$500). Signatures: disposable spender EOAs, **victim-funded gas**
  (drainer-as-a-service), harvest-`permit()`-then-sweep, recipients funnelling to
  vanity-sibling clusters + homoglyph fake-USDC/USDT **address-poisoning**, an
  EIP-7702 delegated sweeper, CEX cash-out. 1 was a benign recurring pull-payment
  collector. → **precision ≈0.85** (small sample, wide CI, but clearly > 0.7).
  We even caught drains the public lists haven't tagged yet — we are *earlier*.
- **Ghost-tx differentiator: DEAD (0.6%).** 523/532 candidates mined normally; the
  drains **land**, so the pending-but-never-mined edge I hoped would justify the
  live feed isn't there.

**Honest reversal:** the first read ("22.6% is inflated noise") was wrong — the
drain-shaped cell is ~85% real drains; the scam-list zero was recall, not signal.
Extractability genuinely works. **But that doesn't rescue the project:** Gate B is
unchanged (can't act — no keys, can't win the block; incumbents own it; Harpie died),
AND because the drains LAND (~99%), the identical detection runs **free on
confirmed-block data — no $300/mo mempool feed required.** The live feed's only
unique value would have been pre-confirmation (ghost) capture, which is 0.6%.
**→ signal real, product still NO-GO, paid stream still unjustified.**
Repro: `engine/scripts/analyze_drainer_prefilter.py` (P1) +
`engine/scripts/analyze_drainer_p2p3.py` (P2/P3 → `engine/data/drainer_cell_p3.jsonl`).

## The test (offline, $0, stream can be OFF the whole time)
Run entirely on the existing `engine/data/{mempool,confirmations}` archive + free RPC.

- **P0 — Assemble.** Join `third_party_transferfrom_flags.jsonl` → raw
  `mempool_*.jsonl` on `hash` to recover the `amount`; attach landed/ghost via the
  existing `ghost_analysis.py` join. *(reuse existing code)*
- **P1 — Archive-only pre-filter (cheap).** Compute fanout, (initiator,owner)
  recurrence, `to==initiator`, victim-under-one-sweeper. **Metric:** what % of the
  138,957 benign bulk does this remove, and how big is the candidate residue?
- **P2 — Silver labels (3-class, $0).** *Positive:* Scam Sniffer blacklist +
  scraped Etherscan `phish-hack` (initiator/recipient ∈ list → DRAIN). *Negative:*
  Dune `labels.addresses` CEX/router/Permit2 (recipient labeled → BENIGN).
  *Heuristic tier (kept SEPARATE so it's not both detector and ground truth):*
  drained-to-zero + no-prior-relationship + fresh-funnel.
- **P3 — Enrich the residue (free RPC).** drain-ratio (amount/balance at block),
  `eth_getCode` EOA check, destination nonce/freshness — only on P1 survivors.
- **P4 — Score + the differentiator.** Precision vs list-confirmed DRAIN;
  false-positive rate vs the CEX/router BENIGN set; **disclose UNKNOWN coverage**.
  Separately: what fraction of drain candidates were **ghost/replaced** (the
  pending-but-never-mined edge) — is it real and non-trivial?

### GO / NO-GO
- **GO (toward a forensics dataset, not a product):** P1 removes the benign bulk
  AND P3-scored candidates hit list-confirmed drains at **precision ≥ ~0.7 on a
  non-trivial candidate set**, AND the ghost-tx fraction is materially > 0.
- **NO-GO:** flags don't preferentially overlap the drainer lists / don't avoid
  the CEX-router set (signal is just benign transfers), or the residue is tiny, or
  ghost-tx adds nothing. → archive the data, stop capture, done.

**Cost/effort:** ~1–3 focused days, **$0**, no purchases, runs with bloXroute off.
*(Recall caveat: reactive scam tags lag ~days–weeks, so list recall on a 9-day
archive is a floor — re-scrape later and re-label, like the Drift re-measurements.)*

## What NOT to build
- ❌ Any **rescue / front-run / approval-revocation / backrun** — dead on physics
  (no keys + can't win the block from a public feed).
- ❌ A **wallet pre-sign / consumer extension** — incumbent-owned, consolidating
  (Wallet Guard→MetaMask, Pocket Universe→Kerberus), and it's a *consumer* of a
  dataset, not a thing a small team ships standalone.
- ❌ **Paying for more bloXroute to run this** — the test uses the archive you
  have; the surviving direction runs on free confirmed-block backfill.
- ❌ **Positive-labeling from heuristics alone** — keeps the validation circular.

## Decision routing
1. **Gate A fails** → NO-GO. Freeze the archive, stop the paid stream, close it out.
2. **Gate A passes but ghost-tx edge is thin** → the signal is real but
   undifferentiated vs incumbents → still NO-GO as a product; keep as internal data.
3. **Gate A passes AND ghost-tx edge is real** → a *narrow forensics/attribution*
   dataset may be worth a deeper scope — **and you can still stop the paid stream**
   (forensics = free backfill). Decide product/B2B separately, eyes open to Harpie.

**MEASURED OUTCOME (2026-06-17):** Gate A **passed** (~85% precision) but the
ghost-tx edge was **absent** (0.6%) → this is case **(2)**, sharpened: the signal is
real but undifferentiated vs incumbents, and since the drains land it needs no live
feed. **Recommendation: freeze the archive, stop the $300/mo stream, close it out.**
The validated detector, if ever wanted (self-wallet guard / $0 public-good feed),
runs on free confirmed-block data — not bloXroute.
