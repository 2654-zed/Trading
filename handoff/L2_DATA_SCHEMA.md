# CEX Level-2 Order-Book Recordings — Schema & Usage

Raw, lossless recordings of the Level-2 order book from multiple centralized
exchanges, captured concurrently. **The collector records the raw stream; book
reconstruction + features happen offline** (so a live-reconstruction bug can't
silently corrupt the data). This is the data for an order-book / "price-pressure"
study.

## Files
- One file per exchange, hourly: `l2_<exchange>_YYYYMMDD_HH.jsonl`
  (`coinbase`, `kraken`, `binanceus`).
- Plus `l2_feed_health.jsonl` (connect/subscribe/disconnect/gap events) and
  `clock_health.jsonl` (NTP state).

## Row schema (one JSON object per line)
| field | meaning |
|---|---|
| `exchange` | coinbase / kraken / binanceus |
| `recv_ts` | LOCAL unix time the message was received (stamped before decode; NTP-synced) |
| `seq` | monotonic per-exchange capture sequence (gaps ⇒ a skipped/garbled message) |
| `pair` | best-effort normalized pair tag (e.g. ETH-USD / ETH/USD / ETHUSDT) |
| `kind` | `snapshot` (full book) / `update` (diff) / `control` / `other` |
| `clock_synced` | was the local clock NTP-disciplined when stamped |
| `msg` | the **raw, unmodified exchange message** (the source of truth) |

## Per-exchange `msg` shapes & how to reconstruct the book
- **binanceus** — `msg.data = {bids:[[price,size],…], asks:[…]}` is a **complete
  top-20 snapshot on every message.** No reconstruction needed — each row is a
  usable book. *Start here.*
- **coinbase** — a `snapshot` (`{bids,asks}`) then `l2update` diffs
  (`{changes:[[side,price,size],…]}`). Reconstruct: load the snapshot, apply each
  diff in `seq`/time order (size 0 ⇒ remove level).
- **kraken** — arrays `[channelID, {as,bs}|{a,b}, "book-N", pair]`: `as/bs` =
  snapshot, `a/b` = updates. Reconstruct the same way; Kraken levels carry their
  own timestamp.

## The "price-pressure" feature (order-book imbalance)
The canonical signal: `imbalance = (bid_depth − ask_depth) / (bid_depth + ask_depth)`
over the top-N levels (depth = Σ size, optionally × price). >0 = more bid support
(upward pressure), <0 = more ask. Compute it **directly from binanceus rows now**;
for coinbase/kraken, reconstruct the book first.

## Honest caveats
- **US-clean exchanges only** (Coinbase/Kraken/Binance.US) — shallower books than
  Binance.com / Bybit / OKX (which geo-restrict or need non-US access). Fine for a
  methodology study; for the deepest books you'd add those venues.
- **`recv_ts` is local capture time** (NTP-synced), not exchange matching-engine
  time. Use the exchange's own timestamp where present for precise ordering; treat
  cross-exchange alignment as ~10–100ms fuzzy.
- **Single public vantage, retail latency.** Irrelevant for a *research* question
  (does imbalance predict short-horizon moves?), but fatal for *live* execution —
  order-book signals decay in milliseconds against colocated market makers. Build
  the study to answer "is there predictive signal," not "can we trade it live."
- **Volume is heavy** — see the run note. L2 is GBs/day; bound the run or scope.

## Reconstruction is a small offline job
Not built here (kept the collector lossless + simple). A reconstruction +
imbalance script is a straightforward add when whoever does the study is ready —
binanceus needs none to start.
