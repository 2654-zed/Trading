# Tardis data setup — quickstart

Hey — this gets you pulling Tardis.dev order-book / trades / **liquidations** /
funding data from the repo in a few minutes. Everything you need is already in the
repo except the API key, which I'll send you separately (don't paste it into git,
Slack, or chat).

---

## What you get
- One key → **all exchanges, all data types** (order books, trades, derivative
  ticker / OI / funding, liquidations).
- A one-command CSV puller, and (optionally) a local server that replays history in
  the **same format as the live stream**, so backtest and live use one code path.
- **Heads-up on the key's window:** it covers **2026‑02‑14 → ~now** only. Anything
  before mid‑Feb 2026 returns a 401 — don't waste time on older dates (e.g. the 2025
  cascades aren't reachable with this key).

---

## 1. Repo + key
```bash
git clone https://github.com/2654-zed/layer3-trading-exp.git
cd layer3-trading-exp
cp .env.example .env
```
Open `.env` and paste the key I send you:
```
TARDIS_DEV=<the-key-I-send-you>
```
`.env` is gitignored — it will **not** be committed. Keep the key out of any tracked
file, Dockerfile, or CI config.

## 2. Python env (use a venv — important)
Tardis-dev is picky about which interpreter it's installed in, so isolate it:
```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# mac/linux: source .venv/bin/activate
pip install -r engine/cex/requirements.txt
```
(That installs `tardis-dev` + `python-dotenv`. Run all the Tardis commands below with
this venv activated.)

## 3. Pull data
```bash
# one day of BTC liquidations + funding/OI, into the gitignored cache:
python -m engine.scripts.tardis_pull --from 2026-06-10 --to 2026-06-11 \
    --exchange binance-futures --symbols btcusdt --types liquidations derivative_ticker

# defaults (binance-futures, BTC+ETH, full book + trades + OI + liquidations):
python -m engine.scripts.tardis_pull --from 2026-06-10 --to 2026-06-11
```
- `--to` is **exclusive** (so `--from X --to X+1` = one day).
- Files land in **`engine/data/tardis/`** (gitignored — data bytes never go in git).
- Symbols are exchange-native, **lowercase** (`btcusdt`, `ethusdt`).
- ⚠️ The book data (`incremental_book_L2`) is **big** — start with `liquidations` +
  `derivative_ticker` (tiny) before pulling full books.
- Exchange ids are specific strings — list at `https://api.tardis.dev/v1/exchanges`
  (e.g. `binance-futures`, `bybit`, `okex-swap`, `hyperliquid`, `deribit`).

## 4. (Optional) tardis-machine — normalized replay + live in one format
If you want historical replay that's wire-compatible with the live stream (so the
same consumer code does backtest and live):
```bash
docker compose -f docker-compose.tardis.yml up   # reads TARDIS_DEV from .env
# HTTP :8000 · WS :8001
#   replay: ws://localhost:8001/ws-replay-normalized
#   live:   ws://localhost:8001/ws-stream-normalized
```

---

## You're set up when…
```bash
python -m engine.scripts.tardis_pull --from 2026-06-10 --to 2026-06-11 \
    --symbols btcusdt --types liquidations
ls engine/data/tardis/    # -> binance-futures_liquidations_2026-06-10_BTCUSDT.csv.gz
```
That file has real liquidation prints (`exchange,symbol,timestamp,...,side,price,amount`).

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `HTTP Error 401 … does not allow access` | The **date is outside the key window** (2026‑02‑14 → ~now), or a wrong exchange id. The 401 body lists the key's full access scope. |
| `ModuleNotFoundError: tardis_dev` | You're not in the venv (or installed into a different Python). Activate `.venv` and `pip install -r engine/cex/requirements.txt`. |
| `TARDIS_DEV is not set` | The key isn't in a **repo-root** `.env` (or the venv shell didn't load it). Put it in `./.env`. |
| `cannot import name 'datasets'` | Old code path — pull latest; the puller uses tardis-dev v4's `download_datasets`. |

Full reference (key scope, data types, design): **`docs/TARDIS.md`**. Ping me if
anything snags.
