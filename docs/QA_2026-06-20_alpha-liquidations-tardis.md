# Q&A reference — bloXroute alpha · liquidation predictiveness · Tardis access

> Dated **2026-06-20**. Answers to three questions: Q1–Q2 from a financier, Q3
> from the partner engineer. Kept as a standing reference so we don't re-derive.
> Related: [FEAS_drainer-signal.md](FEAS_drainer-signal.md) · [ARCHIVE.md](ARCHIVE.md) · [DATA.md](DATA.md) · [FOCUS.md](../FOCUS.md)

---

## Q1 (financier) — Did bloXroute generate any alpha?

**Short answer: No tradeable alpha. Not a dollar, and nothing that survived a
test as a strategy.** What it produced was *market-structure measurement* and one
*validated-but-unactionable* detector — not an edge we could trade from our
position. We never put capital behind any of it, because every candidate failed
on the economics, not for lack of looking.

What we actually measured off the paid mempool feed:

| Candidate | Finding | Verdict |
|---|---|---|
| **Backruns** | ~8.7% of public swaps get a verified same-pool backrun (vs 0% null); but **median realized PnL ≈ $0**, p90 ~$1k *before* the builder bribe (which takes most of it); public actors win ≥ as often as private | Real, but **uneconomical** |
| **Triangular / cyclic arb** | **<1%** of txs (0/1500 V2/V3-countable triangular; 0.6% any profitable cyclic; V4 undercounts) | Uneconomical |
| **Private orderflow** | **~41–52%** of mined txs never appear in the public mempool — the high-value flow is invisible to a public feed by construction | Structural blind spot |
| **Approval-drain detection** (this session) | **Gate A passed** — the third-party-`transferFrom` flag surfaces real phishing drains at **~85% precision**, ~10+/day, often *before* public scam lists; but **Gate B failed** (can't act: no victim keys, can't win the block), the **ghost-tx edge is 0.6%** (drains land ~99%), and ~18% of a clean drainer's sweeps went private | Signal real, **no tradeable alpha**; doesn't even justify the paid feed |

**Why no alpha:** every value-capture path off the mempool is a same-block
builder-auction *latency race* (backrun, triangular, liquidation, MEV) that a
laptop + shared public feed + no colocation/builder access structurally loses.
The one thing that *worked* (drain detection) is a detection capability we can't
monetize and that runs **free on confirmed-block data** anyway.

**Standing recommendation:** freeze the 9-day archive, **stop the $300/mo
bloXroute stream + free block-ingest** (see FEAS doc). The measurement value is
extracted; nothing new is worth continuing to capture.

---

## Q2 (financier) — Did we look at predicting liquidations / liquidation risk?

**Short answer: No — we never tested liquidations. Honest gap.** But it's the
**most promising untested direction you've surfaced**, for a specific reason: it
splits into a part that's *dead* for us (same physics that killed the MEV work)
and a part that's a **data edge that fits our exact position and is testable on
the Tardis data we're already buying.**

**Flavor A — on-chain lending liquidations (Aave / Compound / Morpho): DEAD for us.**
- *Prediction* is trivial and free: Health Factor = collateral×threshold ÷ debt,
  computable for every borrower from public state + oracle prices over a free RPC.
  But knowing a position is underwater is **worthless on its own**.
- *Capture* (earning the liquidation bonus) is a saturated latency/gas/auction
  **MEV race** — won by backrunning the oracle update with private orderflow +
  colocation + builder bids. Searchers historically bid ~99% of profit to
  validators and keep ~17%; **Chainlink SVR now internalizes ~99% of that OEV**
  back to protocols. A laptop loses this race even with perfect prediction.
- → **Don't pursue capture.** Keep the HF math only as free situational data.

**Flavor B — perp / CEX liquidation cascades: a real (if shallow) DATA edge — testable on our stack.**
- The mechanism is mechanical: leverage clusters at price levels → a move forces
  market orders → they drain thin book depth → price gaps → more positions trip →
  feedback loop (Oct 10–11 2025's ~$19–31B deleveraging is the textbook case).
- You're **not winning a tx** — you're forecasting forced-flow price impact, to
  *avoid being the liquidated party, size risk, or fade an exhausted cluster.*
- **What's genuinely predictive (all offline-computable from Tardis + free OI/funding):**
  realized **L2 book-depth and how fast it thins** (the underrated input heatmaps
  ignore); **open-interest level + rate-of-change**; **funding extremes** (who's
  crowded); **liquidation-level clusters** (reconstruct yourself from OI + leverage
  buckets — don't trust vendor heatmaps); and the **liquidation print tape** as a
  leading burst signal.
- **Honest caveats:** heatmaps are *reconstructed*, not real positions; the public
  Binance liquidation feed is throttled (1 order/symbol/1000ms) and *under-reports
  during the exact cascades you care about* — lean on OI deltas for true magnitude;
  and the signal is **reflexive/semi-crowded** (clusters are a magnet, levels get
  hunted/faked). So: a real-but-shallow edge, best as a **risk/timing/regime
  filter**, not push-button alpha.

**How we'd test it (if greenlit):** it's a natural extension of Game 3 — same
sealed-holdout gate, same recorded-data discipline. Build a fragility model from
Tardis `incremental_book_L2` + `derivative_ticker` (OI/funding) + `liquidations`,
pre-register "fragility state → short-horizon adverse move," and run it through
the OOS gate. **It needs no live latency race**, which is exactly why it fits us
where the MEV work didn't.

---

## Q3 (engineer) — Where's the "lock" for the Tardis key? Do we code our own doorway into Tardis's books?

**Short answer: (1) The lock is the same pattern as the bloXroute key — an env var
/ gitignored config, never in git or chat. (2) No, you do NOT hand-roll a doorway —
Tardis ships official clients that normalize + replay the books for you.**

### The lock (credential storage) — same invariant as bloXroute
Standing rule: **credentials never live in code or chat.** Store the Tardis key
exactly like `BLOXROUTE_AUTH_HEADER`:
- **Current setup (Jason, 2026-06-20):** the key is in the repo-root **`.env`** as **`TARDIS_DEV`** — and `.env` / `*.env` / `**/.env` are already excluded by `.gitignore` (verified ignored + untracked), so it stays out of git. Load with `python-dotenv` (or export it) and read `os.environ["TARDIS_DEV"]`; never print/echo it. *(The name matches the package; `TARDIS_API_KEY` would read more clearly, but use whatever's set.)*
- **Alternative:** a user-scope env var (`setx TARDIS_DEV "<key>"`, survives reboot) or a gitignored `engine/data/tardis_config.json` (`engine/data/` is also ignored).
- If you run **tardis-machine**, its env var is `TM_API_KEY` (pass `-e TM_API_KEY=$TARDIS_DEV` to Docker, or `--api-key`). Same rule: env/secret, never in a tracked image or compose file.

> Verify before any commit: `git check-ignore .env` prints `.env` (confirmed). The key must never appear in a tracked file or a log.

### The doorway — use the official clients, don't build one
You do **not** parse Tardis's HTTP/CSV or exchange-native frames yourself. Three first-party doorways:
- **`tardis-dev` (Python, pip):** `replay(..., api_key=...)` for historical replay; `download_datasets(..., api_key=...)` for CSVs. Closest to our existing Python stack.
- **`tardis-dev` / tardis-node (Node, npm):** `replay()/replayNormalized()/stream()/streamNormalized()`, `init({ apiKey })`. (Requires Node 24+.)
- **`tardis-machine` (Docker `tardisdev/tardis-machine`):** a local server exposing HTTP :8000 + WS :8001. Its `/ws-replay-normalized` returns the **same normalized message shape as the live `/ws-stream-normalized`** — so your reconstruction code consumes one format and you flip between *historical backtest* and *live* with zero parser changes. **This is the cleanest doorway for a backtest→live pipeline** and the recommended path.

**Datasets to pull** (Game 3 + the Q2 liquidation test): `incremental_book_L2`
(full book) or `book_snapshot_25`, `trades`, `derivative_ticker` (OI / funding /
mark), and — **yes, Tardis has it as a first-class normalized type — `liquidations`.**
Without a key you only get the **first day of each month**, so the key gates everything.

### Note on our existing collector
`handoff/l2_collector.py` records *raw* exchange streams. With Tardis, prefer
pointing the reconstruction at **tardis-machine normalized replay** (one consumer
for backtest + live); the raw collector becomes the live-redundancy complement, or
we switch live to tardis-machine's normalized stream too.

---

## Cross-link: Q2 ↔ Q3
The Tardis `liquidations` dataset + L2 book + OI/funding is **exactly the input set
the Flavor-B liquidation test needs** — so the moment the engineer wires the key
(Q3), the liquidation-predictiveness study (Q2) is testable on recorded data with
no new purchases and no latency race. That's the cheapest high-value next experiment.

---

### Sources
DefiLlama (mempool/MEV economics, our own measurements) · [FEAS_drainer-signal.md](FEAS_drainer-signal.md) ·
Tardis: github.com/tardis-dev/tardis-node, pypi.org/project/tardis-dev, github.com/tardis-dev/tardis-machine, docs.tardis.dev ·
Liquidations: Chainlink SVR docs + analysis (blog.chain.link, chaoslabs.xyz), flashbots aave-liquidations spec, Coinglass LiqMap docs, Binance liquidation-stream docs, Amberdata deleveraging analyses, arXiv "Slippage-at-Risk" / auto-deleveraging.
