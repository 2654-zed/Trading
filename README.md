# layer3-trading-exp

> **The repo name is historical.** The active work is **CEX order-book
> microstructure research ("Game 3")** — rigorously testing whether order-book
> imbalance predicts short-horizon moves, through a sealed-holdout gate. The
> original "Layer 3" trading project is **retired** (renaming the repo is open
> question [#4](https://github.com/2654-zed/layer3-trading-exp/issues/4)).
>
> ## 👉 Start at **[FOCUS.md](FOCUS.md)** — the single source of truth.

This repo is the team's coordination hub *and* the home of the research code.
It spans **two eras**: a small set of **active** forensic/research tools, and a
larger body of **retired** code kept for reference and replay. The map below
marks which is which — don't mistake the legacy deployment for the current product.

---

## Start here

| You want… | Go to |
|---|---|
| The current goal, roadmap, and status | **[FOCUS.md](FOCUS.md)** |
| What data exists and where the bytes live | [docs/DATA.md](docs/DATA.md) |
| What was tried and set aside (and why) | [docs/ARCHIVE.md](docs/ARCHIVE.md) |
| Open decisions / discussion | GitHub Issues [#1](https://github.com/2654-zed/layer3-trading-exp/issues/1)–[#4](https://github.com/2654-zed/layer3-trading-exp/issues/4) (we discuss here, **not email**) |
| Hand-off deliverables for the partner engineer | [handoff/](handoff/) |

**Automated / agent readers:** read `FOCUS.md` then `docs/DATA.md`. Treat anything
tagged **Legacy** below as reference only — it is not the current direction and is
not maintained. The current entry points are the few **Active** items.

---

## Repository map

### ✅ Active — the current effort
| Path | What it is |
|---|---|
| [`FOCUS.md`](FOCUS.md) | Single source of truth: goal, resources, roadmap (M0–M4), status, open questions. |
| [`docs/`](docs/) | `DATA.md` (datasets + the "bytes never in git" rule), `ARCHIVE.md` (curated history), `archive/` (frozen source docs). |
| [`engine/bloxroute/`](engine/bloxroute/) | bloXroute ETH mempool capture (Tier-0) + confirmation block-ingest (Tier-1) + pure analyzers (ghost-tx, backrun, triangular). |
| [`engine/cex/`](engine/cex/) | Centralized-exchange Level-2 order-book lossless capture — the Game-3 "price-pressure" data. |
| [`engine/research_loop/`](engine/research_loop/) | Quant/Critic/Coder loop with veto-only LLMs and a deterministic **sealed-holdout / multiple-testing gate**. A documented NO-GO is the product. |
| [`engine/scripts/`](engine/scripts/) | Runnable CLI drivers (`python -m engine.scripts.*`). Mixed era — the capture/analysis scripts are active; the `run_engine_phase_*` ones are legacy. |
| [`handoff/`](handoff/) | Self-contained deliverables for the partner engineer: `l2_collector.py`, status report, data dictionary, schemas, decision docs. |

### 🗄️ Legacy — retired, kept for reference & replay
| Path | What it is |
|---|---|
| `engine/` lens stack | `lenses/ orchestrator/ synthesis/ feedback/ execution/ paper/ consumers/ core/ adapters/` — the retired multi-lens decision engine (six OOS nulls; public edge is dead). |
| [`layer3_trading_exp/`](layer3_trading_exp/) | The original Phase-1/2 detection-only DEX-arbitrage codebase (Base → cross-chain via Across). Was the Railway-deployed worker; **detector halted 2026-05-24** after a compute-unit spike (logs end there). |
| Root deploy infra | `Dockerfile`, `railway.json`, `entrypoint.sh`, `scripts/`, `.dockerignore`, `.railwayignore` — builds & runs **`layer3_trading_exp`** (the retired EXP-002) on Railway. See [Deployment](#deployment-legacy). |

### 🧠 Record — the reasoning / audit layer
| Path | What it is |
|---|---|
| [`memory/`](memory/) | Persistent decision/failure/audit trail (**not** code docs): `decisions/` (D-001…D-047), `failures/`, `unknowns/`, `loop/`, `trades/` + four state docs. Read order in [`memory/README.md`](memory/README.md). (`trades/` is a misnomer — per invariant I-1 the system never trades.) |

---

## Quick start (active pipelines)

All run locally; no colocation. Raw outputs land in `engine/data/` (gitignored).

```bash
# Forensic ETH mempool — "before" (pending) vs "after" (mined)
python -m engine.scripts.run_mempool_capture   # Tier-0; needs BLOXROUTE_AUTH_HEADER env var
python -m engine.scripts.run_block_ingest      # Tier-1; free public RPC, no creds, safe alongside
python -m engine.scripts.analyze_ghost_tx      # DuckDB join → landed / replaced / true-ghost
python -m engine.scripts.verify_backruns       # verified-backrun rate (read-only RPC sample)
python -m engine.scripts.analyze_triangular_arb

# CEX Level-2 order-book capture (Game 3) — public market data only
python -m engine.scripts.run_l2_capture
# …or the self-contained single-file build for the engineer:
python handoff/l2_collector.py --minutes 0     # pip install websockets

# Sealed-holdout research loop (NO-GO is an acceptable result)
python -m engine.research_loop.run --mock      # live mode needs ANTHROPIC_API_KEY
```

`.bat` wrappers exist for the long-running captures (`run_mempool_capture.bat`,
`run_l2_capture.bat`, `run_block_ingest.bat`). Tests: `pytest engine/`.

---

## Conventions

- **Data bytes never go in git** ([docs/DATA.md](docs/DATA.md)). `engine/data/` and
  `l2_data/` are gitignored — the repo holds *descriptions + a link*, not the bytes.
  `engine/data/bloxroute_config.json` holds the bloXroute auth header and must never be committed.
- **`docs/archive/`** = frozen historical docs; relative paths *inside* them refer to
  each doc's original repo-root location (see [docs/archive/README.md](docs/archive/README.md)).
- **`memory/`** is the reasoning/audit layer, not documentation — start at its README.
- **Coordination** happens in `FOCUS.md` + GitHub Issues, not email. When a question
  is answered, fold it into FOCUS and move dead threads to `docs/ARCHIVE.md`.

---

## Deployment (legacy)

The root `Dockerfile` / `railway.json` / `entrypoint.sh` build and run
`python -m layer3_trading_exp.scripts.detect_dry_run` — the **retired EXP-002
cross-chain detector** — as a Railway background worker. It was **killed** after a
compute-unit spike and is not the current product. The active `engine/` work has
**no Railway wiring**; it runs on local machines. Treat this stack as reference.
