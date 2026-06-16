"""Fit diagnostic for Polymarket + Kalshi as information sources.

Before building any adapter (the UNK-013 lesson: verify data overlaps with
what we predict BEFORE coding), this answers:
  - Polymarket: of the most-liquid active markets, how many are CRYPTO /
    crypto-PRICE markets? Total volume? Any memecoin (our-universe) coverage?
  - Kalshi: crypto vs macro-regime coverage + liquidity.

$0 Alchemy — public REST APIs, read-only.
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

CRYPTO = ("bitcoin", "btc", "ethereum", " eth", "ether", "solana", " sol",
          "crypto", "dogecoin", "doge", "xrp", "ripple", "bnb", "cardano",
          "memecoin", "altcoin", "stablecoin", "satoshi", "nakamoto")
PRICE = ("$", "above", "below", "reach", "hit", "price", "all-time high",
         "ath", "k by", "trade at")
MACRO = ("fed", "rate cut", "rate hike", "cpi", "inflation", "gdp", "jobs",
         "unemployment", "recession", "powell", "fomc", "treasury", "interest rate")


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "fit-diag/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def has(text, kws):
    t = (text or "").lower()
    return any(k in t for k in kws)


def diag_polymarket():
    print("=" * 70)
    print("POLYMARKET — active markets by volume (top ~750)")
    print("=" * 70)
    markets = []
    for offset in range(0, 750, 250):
        url = (f"https://gamma-api.polymarket.com/markets?closed=false"
               f"&limit=250&offset={offset}&order=volumeNum&ascending=false")
        try:
            batch = get(url)
        except Exception as e:
            print(f"  page offset={offset} error: {e}"); break
        if not batch:
            break
        markets.extend(batch)
    print(f"  active markets pulled: {len(markets)}")

    def vol(m):
        try:
            return float(m.get("volumeNum") or m.get("volume") or 0)
        except (TypeError, ValueError):
            return 0.0

    total_vol = sum(vol(m) for m in markets)
    crypto = [m for m in markets if has(m.get("question"), CRYPTO)
              or has(m.get("description"), CRYPTO)]
    crypto_price = [m for m in crypto if has(m.get("question"), PRICE)]
    crypto_vol = sum(vol(m) for m in crypto)
    print(f"  total volume (sampled):   ${total_vol:,.0f}")
    print(f"  crypto markets:           {len(crypto)} / {len(markets)} "
          f"({100*len(crypto)/max(len(markets),1):.0f}%)")
    print(f"  of which crypto-PRICE:    {len(crypto_price)}")
    print(f"  crypto share of volume:   {100*crypto_vol/max(total_vol,1):.1f}%")
    print(f"\n  top crypto markets by volume:")
    for m in sorted(crypto, key=vol, reverse=True)[:10]:
        print(f"    ${vol(m):>12,.0f}  {(m.get('question') or '')[:64]}")
    # memecoin / our-universe overlap
    memes = ("brett", "toshi", "bald", "doginme", "clanker", "mog", "pepe",
             "wif", "bonk", "memecoin")
    meme_hits = [m for m in markets if has(m.get("question"), memes)]
    print(f"\n  memecoin-style market hits: {len(meme_hits)} "
          f"(our monitored universe is Base memecoins)")
    for m in meme_hits[:5]:
        print(f"    {(m.get('question') or '')[:64]}")


def diag_kalshi():
    print()
    print("=" * 70)
    print("KALSHI — open markets, categorized")
    print("=" * 70)
    host = "https://api.elections.kalshi.com"
    markets, cursor, pages = [], None, 0
    while pages < 6:
        url = f"{host}/trade-api/v2/markets?limit=1000&status=open"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            d = get(url)
        except Exception as e:
            print(f"  page {pages} error: {e}"); break
        ms = d.get("markets", [])
        markets.extend(ms)
        cursor = d.get("cursor")
        pages += 1
        if not cursor or not ms:
            break
    print(f"  open markets pulled: {len(markets)}")

    def kvol(m):
        for k in ("volume", "volume_24h", "liquidity_dollars", "notional_value_dollars"):
            v = m.get(k)
            if v:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 0.0

    def title(m):
        return f"{m.get('title','')} {m.get('subtitle','') or m.get('yes_sub_title','')}"

    crypto = [m for m in markets if has(title(m), CRYPTO)]
    crypto_price = [m for m in crypto if has(title(m), PRICE)]
    macro = [m for m in markets if has(title(m), MACRO)]
    print(f"  crypto markets:  {len(crypto)} (of which price-threshold: {len(crypto_price)})")
    print(f"  macro markets:   {len(macro)}")
    print(f"\n  sample crypto markets:")
    for m in sorted(crypto, key=kvol, reverse=True)[:8]:
        print(f"    {title(m)[:70]}")
    print(f"\n  sample macro markets:")
    for m in macro[:6]:
        print(f"    {title(m)[:70]}")


if __name__ == "__main__":
    try:
        diag_polymarket()
    except Exception as e:
        print(f"Polymarket diag failed: {type(e).__name__}: {e}")
    try:
        diag_kalshi()
    except Exception as e:
        print(f"Kalshi diag failed: {type(e).__name__}: {e}")
