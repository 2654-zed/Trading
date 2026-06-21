#!/usr/bin/env python3
"""Thin, shared wrapper over the official `tardis-dev` Python client.

Goal: one tiny doorway the whole team uses, so nobody hand-rolls Tardis access.
- Reads the key from `TARDIS_DEV` (env / `.env`) — NEVER hardcoded or logged.
- Downloads normalized CSV datasets to a gitignored local cache (re-pulls are
  free; the client skips already-downloaded day files).
- Carries our default universe + the data types the Game-3 / liquidation-cascade
  work needs.

Setup:  pip install -r engine/cex/requirements.txt   (tardis-dev + python-dotenv)
        put TARDIS_DEV=<key> in .env   (see docs/TARDIS.md / .env.example)

NOTE: untested against the live API in this repo — `pip install tardis-dev` and
do one small `pull(...)` smoke run before relying on it. The signature below is
the canonical `tardis_dev.datasets.download` API.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()  # load .env from cwd/parents if present; harmless if absent
except ImportError:  # python-dotenv optional — env vars still work
    pass

# gitignored cache (engine/data/ is excluded by .gitignore — bytes never hit git)
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "tardis"

# Default data types for the order-book microstructure + liquidation-cascade work.
DEFAULT_DATA_TYPES = [
    "incremental_book_L2",   # full L2 book (snapshots + diffs) — reconstruct offline
    "book_snapshot_25",      # top-25 normalized snapshots (lighter)
    "trades",                # tick-by-tick prints
    "derivative_ticker",     # open interest, funding, mark/index price
    "liquidations",          # the liquidation feed (Flavor-B input)
]

# Tardis exchange ids are specific strings — see https://api.tardis.dev/v1/exchanges
DEFAULT_EXCHANGE = "binance-futures"
DEFAULT_SYMBOLS = ["btcusdt", "ethusdt"]  # exchange-native, lowercase


def _api_key() -> str:
    key = os.environ.get("TARDIS_DEV")
    if not key:
        raise RuntimeError(
            "TARDIS_DEV is not set. Put it in .env (TARDIS_DEV=<key>) or export it; "
            "never hardcode the key. See docs/TARDIS.md."
        )
    return key


def pull(
    exchange: str = DEFAULT_EXCHANGE,
    symbols=DEFAULT_SYMBOLS,
    data_types=None,
    from_date: str | None = None,
    to_date: str | None = None,
    download_dir: str | Path | None = None,
) -> Path:
    """Download Tardis CSV datasets to the local gitignored cache.

    Dates are ISO 'YYYY-MM-DD' (UTC); `to_date` is EXCLUSIVE (per-day files).
    Re-pulls skip files already on disk. Returns the download directory.
    The API key is read from env and never printed.
    """
    from tardis_dev import datasets  # imported lazily so the module loads without the dep

    out = Path(download_dir or CACHE_DIR)
    out.mkdir(parents=True, exist_ok=True)
    datasets.download(
        exchange=exchange,
        data_types=list(data_types or DEFAULT_DATA_TYPES),
        from_date=from_date,
        to_date=to_date,
        symbols=[s.lower() for s in symbols],
        api_key=_api_key(),
        download_dir=str(out),
    )
    return out


if __name__ == "__main__":  # tiny self-check (no network): confirms key + paths
    print("cache dir :", CACHE_DIR)
    print("key set?  :", bool(os.environ.get("TARDIS_DEV")))
    print("defaults  :", DEFAULT_EXCHANGE, DEFAULT_SYMBOLS, DEFAULT_DATA_TYPES)
