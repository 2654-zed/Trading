#!/usr/bin/env python3
"""CLI to pull Tardis datasets in plain terms — the engineer's one-command doorway.

Examples
--------
# liquidation-cascade Phase-0 slice (Oct 2025 stress window, BTC+ETH perps):
python -m engine.scripts.tardis_pull --from 2025-10-08 --to 2025-10-13 \
    --exchange binance-futures --symbols btcusdt ethusdt \
    --types incremental_book_L2 derivative_ticker liquidations trades

# defaults (binance-futures, BTC+ETH, our standard data types) for one day:
python -m engine.scripts.tardis_pull --from 2025-10-10 --to 2025-10-11

Output lands in the gitignored cache (engine/data/tardis/). Needs TARDIS_DEV in
.env (see docs/TARDIS.md). The key is read from env and never printed.
"""
from __future__ import annotations

import argparse

from engine.cex.tardis_io import DEFAULT_DATA_TYPES, DEFAULT_EXCHANGE, DEFAULT_SYMBOLS, pull


def main() -> int:
    p = argparse.ArgumentParser(description="Download Tardis.dev datasets to the local gitignored cache.")
    p.add_argument("--from", dest="from_date", required=True, help="UTC YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="to_date", required=True, help="UTC YYYY-MM-DD (EXCLUSIVE)")
    p.add_argument("--exchange", default=DEFAULT_EXCHANGE, help=f"Tardis exchange id (default {DEFAULT_EXCHANGE})")
    p.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="exchange-native symbols, e.g. btcusdt ethusdt")
    p.add_argument("--types", nargs="+", default=DEFAULT_DATA_TYPES, help="data types (see docs/TARDIS.md)")
    a = p.parse_args()

    print(f"[tardis_pull] {a.exchange} {a.symbols} {a.types}  {a.from_date}..{a.to_date} (to exclusive)")
    out = pull(a.exchange, a.symbols, a.types, a.from_date, a.to_date)
    print(f"[tardis_pull] done -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
