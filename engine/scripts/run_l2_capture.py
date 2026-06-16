"""Multi-exchange CEX Level-2 order-book capture (Game 3).

Records the raw L2 stream losslessly from several exchanges concurrently to
hourly JSONL (one file per exchange). Public market data only — no API keys.
Book reconstruction + price-pressure/imbalance features are computed OFFLINE
from these recordings (handoff-ready).

Usage:
    python -m engine.scripts.run_l2_capture --minutes 0
    python -m engine.scripts.run_l2_capture --minutes 0 --exchanges coinbase,kraken,binanceus \
        --pairs BTC-USD,ETH-USD
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..cex.l2_capture import DEFAULT_EXCHANGES, run


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=0.0, help="0 = until Ctrl-C")
    p.add_argument("--exchanges", type=lambda s: [x.strip() for x in s.split(",")],
                   default=DEFAULT_EXCHANGES)
    p.add_argument("--pairs", type=lambda s: [x.strip() for x in s.split(",")],
                   default=["BTC-USD", "ETH-USD"])
    p.add_argument("--depth", type=int, default=25)
    p.add_argument("--out-dir", type=Path, default=Path("engine/data/l2"))
    return p.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run(_parse_args())))
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        sys.exit(0)
