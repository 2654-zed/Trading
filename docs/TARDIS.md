# TARDIS — access, credentials, and the team data doorway

> How anyone on the repo gets Tardis.dev order-book / trade / derivative /
> **liquidation** data with near-zero setup. Principle: **the repo ships
> everything except the secret.** Related: [DATA.md](DATA.md) · [QA_2026-06-20](QA_2026-06-20_alpha-liquidations-tardis.md) · [FEAS_liquidation-cascade.md](FEAS_liquidation-cascade.md)

## TL;DR onboarding (3 steps)
```bash
cp .env.example .env            # then paste the real TARDIS_DEV key (shared via the vault, NOT the repo)
pip install -r engine/cex/requirements.txt
python -m engine.scripts.tardis_pull --from 2025-10-08 --to 2025-10-13   # data -> engine/data/tardis/ (gitignored)
```
…or, for a normalized replay/live server that one consumer can use for **both**
backtest and live:
```bash
docker compose -f docker-compose.tardis.yml up      # reads TARDIS_DEV from .env
# replay:  ws://localhost:8001/ws-replay-normalized   ·   live: ws://localhost:8001/ws-stream-normalized
```

## The key — the "lock", and sharing it on a team
- The key lives in **`.env` as `TARDIS_DEV`** — **gitignored** (`.env`/`*.env`/`**/.env`
  are excluded; verified). It must never appear in a tracked file, a Dockerfile, CI
  YAML, a log, or chat.
- **One key, multiple machines.** A Tardis key is a plain HTTP API key — you and the
  engineer use the **same key** on your own machines.
- **Share it out-of-band, not through the repo:** a shared password-manager vault
  (1Password/Bitwarden) or a one-time secure send. Each collaborator pastes it into
  their **own local `.env`**. The committed **`.env.example`** documents the var name
  so onboarding is just "copy + paste".
- **If/when pulls run in CI** (GitHub Actions on the private repo): add `TARDIS_DEV`
  as a **repository secret** and reference `${{ secrets.TARDIS_DEV }}` — collaborators
  can *use* it in workflows but can't read the value. Still never commit the raw key.
- **No key ⇒ only the first day of each month** is accessible — the key gates everything.

## Two doorways (both thin — nobody hand-rolls Tardis)
1. **`tardis-machine` (Docker) — the backbone.** `docker-compose.tardis.yml` runs the
   official server and maps `TARDIS_DEV` → `TM_API_KEY`. It serves **normalized
   replay** (`/ws-replay-normalized`, `POST /replay-normalized`) that is
   **wire-compatible with the live stream** (`/ws-stream-normalized`) — so our
   reconstruction/analysis code consumes **one format** and flips backtest⇄live with
   no changes. This is the recommended path for the Game-3 / liquidation pipeline.
2. **`engine/cex/tardis_io.py` + `engine/scripts/tardis_pull.py` — CSV pulls.** A
   one-command downloader for ad-hoc / offline analysis (pandas, the OOS gate). Reads
   `TARDIS_DEV`, caches to the gitignored `engine/data/tardis/`, carries our default
   universe + data types. The engineer asks in plain terms (`--exchange --symbols
   --types --from --to`) and gets files.

## Our default universe + data types
- **Exchange:** `binance-futures` (start with one venue — different liq engines +
  cross-venue lead-lag double-count events; see the liquidation FEAS).
- **Symbols:** `btcusdt`, `ethusdt` (Tardis ids are exchange-native, **lowercase**).
- **Data types:** `incremental_book_L2` (full book), `book_snapshot_25`, `trades`,
  `derivative_ticker` (OI / funding / mark), **`liquidations`**.

## Where data lands (and the rule)
- CSV cache: **`engine/data/tardis/`** · tardis-machine cache: **`engine/data/tardis-cache/`**
  — both under `engine/data/`, which is **gitignored**. **Data bytes never go in git**
  (the [DATA.md](DATA.md) rule); the repo holds the *code + a description*, not the bytes.

## Gotchas
- `--to` / `to_date` is **exclusive** (per-day files).
- Tardis exchange ids are specific strings — list at `https://api.tardis.dev/v1/exchanges`.
- The live **liquidation feed is throttled** (Binance: 1 order/symbol/1000ms) and
  under-reports during cascades — use **OI deltas** for magnitude (see the FEAS doc).
- `tardis_io.py` is **untested against the live API in-repo** — `pip install tardis-dev`
  and run one small `pull(...)` smoke test before relying on it.

## How this feeds the work
The reconstruction + feature pipeline (Game-3 imbalance, and the liquidation-cascade
fragility model) consumes the **normalized** Tardis format. Targeting tardis-machine's
normalized replay means the *same consumer code* runs the offline backtest and the
live stream. The existing raw `handoff/l2_collector.py` becomes optional live
redundancy.
