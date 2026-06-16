"""Decision-grade offline analysis of captured mempool archives.

The live capture's metrics are a rough liveness pulse. These are the
honest, refined versions that separate genuine signal from organic noise:

  - COMPETITION: raw "distinct senders on the same (contract, method)"
    over-counts popular tokens (96 people transferring USDT != 96 bots
    racing one opportunity). The refined metric conditions on PRIORITY FEE
    — only top-fee-decile txs are plausible searchers bidding to win — and
    reports both, so the inflation is visible. The "cost to compete" is the
    fee being paid on genuinely contested targets.

  - DRAINS: a single third-party `transferFrom` is mostly legitimate
    (CEX sweeps, approved bots). The refined signal is FAN-OUT: one
    initiator (spender) pulling tokens from MANY distinct owners — the
    "one drainer, many victims" pattern L3's 266-drain set confirmed, now
    visible PRE-execution. (Still has FPs — large CEX sweepers fan out too;
    this surfaces candidates, it does not convict.)

Pure functions; unit-tested; no network.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from .mempool_capture import _selector, decode_transfer_from


def priority_fee_gwei(tx: dict) -> Optional[float]:
    mpf = tx.get("max_priority_fee")
    if isinstance(mpf, str) and mpf.startswith("0x"):
        try:
            return int(mpf, 16) / 1e9
        except ValueError:
            return None
    return None


def _fee_threshold(txs: list, pct: float) -> Optional[float]:
    fees = sorted(f for f in (priority_fee_gwei(t) for t in txs) if f is not None)
    if not fees:
        return None
    return fees[min(len(fees) - 1, int(pct * len(fees)))]


def _peak_contention(txs_sorted: list, window_s: float):
    """Per (to, selector): peak distinct senders within any `window_s`, and
    the total count of observations that were contended (>=2 racers)."""
    buckets: dict = {}
    peak: dict = {}
    contended = 0
    for tx in txs_sorted:
        key = (tx.get("to", ""), _selector(tx.get("input", "")))
        ts = tx.get("recv_ts", 0.0)
        dq = buckets.setdefault(key, deque())
        dq.append((ts, tx.get("from", "")))
        cutoff = ts - window_s
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        racers = len({s for _, s in dq})
        if racers >= 2:
            contended += 1
        if racers > peak.get(key, 0):
            peak[key] = racers
    return peak, contended


def analyze_competition(txs: list, *, window_s: float = 2.0,
                        top_pct: float = 0.90) -> dict:
    """Raw vs bidder-only contention. The gap shows how much of the raw
    competition number is organic volume vs real MEV racing."""
    txs_sorted = sorted(txs, key=lambda t: t.get("recv_ts", 0.0))
    fee_floor = _fee_threshold(txs_sorted, top_pct)
    bidders = ([t for t in txs_sorted if (priority_fee_gwei(t) or 0) >= (fee_floor or 0)]
               if fee_floor is not None else [])

    all_peak, all_contended = _peak_contention(txs_sorted, window_s)
    bid_peak, bid_contended = _peak_contention(bidders, window_s)

    # Fees being paid on genuinely contested (bidder) targets.
    contested_targets = {k for k, v in bid_peak.items() if v >= 2}
    contest_fees = sorted(
        f for f in (priority_fee_gwei(t) for t in bidders
                    if (t.get("to", ""), _selector(t.get("input", ""))) in contested_targets)
        if f is not None
    )
    def pct(arr, p):
        return arr[min(len(arr) - 1, int(p * len(arr)))] if arr else None

    return {
        "n_txs": len(txs_sorted),
        "fee_threshold_gwei_top_decile": fee_floor,
        "n_bidders": len(bidders),
        "raw_max_racers": max(all_peak.values()) if all_peak else 0,
        "raw_contended_events": all_contended,
        "bidder_max_racers": max(bid_peak.values()) if bid_peak else 0,
        "bidder_contended_events": bid_contended,
        "n_contested_targets": len(contested_targets),
        "cost_to_compete_gwei_p50": pct(contest_fees, 0.5),
        "cost_to_compete_gwei_p90": pct(contest_fees, 0.9),
        "cost_to_compete_gwei_max": contest_fees[-1] if contest_fees else None,
    }


def analyze_drains(txs: list, *, min_owners: int = 2) -> dict:
    """Fan-out drain candidates: one initiator pulling from many owners."""
    by_initiator: dict = defaultdict(set)       # initiator -> {owners}
    total_third_party = 0
    for tx in txs:
        dec = decode_transfer_from(tx.get("input", ""))
        if dec is None:
            continue
        initiator = (tx.get("from") or "").lower()
        owner = dec["from"].lower()
        if not initiator or initiator == owner:
            continue
        total_third_party += 1
        by_initiator[initiator].add((owner, tx.get("to", "")))  # owner+token
    fanout = sorted(
        ((init, len(pairs)) for init, pairs in by_initiator.items()
         if len(pairs) >= min_owners),
        key=lambda x: -x[1],
    )
    return {
        "total_third_party_transferFrom": total_third_party,
        "distinct_initiators": len(by_initiator),
        "fanout_candidates": fanout[:20],   # (initiator, distinct owner-tokens)
        "n_fanout_candidates": len(fanout),
    }


__all__ = ["analyze_competition", "analyze_drains", "priority_fee_gwei"]
