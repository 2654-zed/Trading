"""bloXroute ETH mempool capture + honest analysis (network core + pure
analyzers).

Purpose (per D-046/D-047 context): the trading-edge thesis is dead, but a
PAID live mempool feed can still produce three honest, $0-capital outputs:
  1. A captured ARCHIVE of pre-execution order flow — a proprietary dataset
     that doesn't otherwise exist (mempool data is normally un-backtestable
     precisely because nobody stores it). This is the durable asset.
  2. COMPETITION density — how many distinct senders race the same
     opportunity, and how tightly clustered in time. The empirical
     "can we even compete?" measurement, with no edge of our own required.
  3. A DEDUCTIVE pre-execution detector — third-party `transferFrom`
     (initiator != token owner), the ONE approach that worked across both
     projects (L3's control-fact test), now applied BEFORE the tx lands.

The analyzers are pure + unit-tested. The network loop is isolated so the
whole thing is testable without a feed. Credentials are NEVER hardcoded or
logged — the driver reads them from the environment / a local config file.
"""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

# Method selectors (first 4 bytes of calldata).
SEL_TRANSFER_FROM = "0x23b872dd"
SEL_TRANSFER = "0xa9059cbb"
SEL_APPROVE = "0x095ea7b3"

# Tier-0 vantage tag stamped on every captured row (a second vantage later
# is then a schema-compatible add, distinguished by this value).
DEFAULT_SOURCE = "blxr-cloud-ws-eth"


# ----- pure decoders ------------------------------------------------------

def _addr_from_word(calldata_no0x: str, word_index: int) -> Optional[str]:
    """Extract an address from the Nth 32-byte ABI word (after the selector).
    calldata_no0x excludes the leading '0x' AND the 8-char selector."""
    start = word_index * 64
    word = calldata_no0x[start:start + 64]
    if len(word) < 64:
        return None
    return "0x" + word[24:]  # last 20 bytes of the 32-byte word


def decode_transfer_from(input_hex: str) -> Optional[dict]:
    """Decode an ERC-20 transferFrom(from,to,amount) calldata.
    Returns {"from","to","amount"} or None if not a transferFrom."""
    if not input_hex:
        return None
    h = input_hex.lower()
    if not h.startswith("0x"):
        h = "0x" + h
    if not h.startswith(SEL_TRANSFER_FROM):
        return None
    body = h[10:]  # strip '0x' + 8-char selector
    frm = _addr_from_word(body, 0)
    to = _addr_from_word(body, 1)
    if frm is None or to is None:
        return None
    amount_hex = body[128:192]
    try:
        amount = int(amount_hex, 16) if amount_hex else 0
    except ValueError:
        amount = 0
    return {"from": frm, "to": to, "amount": amount}


def is_third_party_transfer_from(tx_from: str, input_hex: str) -> Optional[dict]:
    """The L3-validated DEDUCTIVE control-fact, applied pre-execution:
    a transferFrom whose INITIATOR (tx.from) is NOT the token owner being
    debited (the `from` param). Returns the decoded detail dict (with
    `initiator`) if it's a third-party move, else None.

    NOTE: most third-party transferFroms are legitimate (DEX routers,
    Permit2, etc.). This is the right PRIMITIVE; suppressing known-legit
    spenders is a refinement, not part of the raw flag (mirrors L3's
    finding that the raw control-fact has FPs but is the correct test)."""
    if not tx_from:
        return None
    dec = decode_transfer_from(input_hex)
    if dec is None:
        return None
    if dec["from"].lower() != tx_from.lower():
        return {**dec, "initiator": tx_from.lower()}
    return None


def _tx_type(contents: dict) -> Optional[int]:
    """EIP-2718 transaction type as an int: 0 legacy, 1 access-list (2930),
    2 EIP-1559, 3 blob (4844), 4 EIP-7702 set-code. None if the feed omits
    the field (older capture rows, or a stream that doesn't include it)."""
    raw = contents.get("type")
    if raw is None:
        raw = contents.get("txType")
    if isinstance(raw, bool):           # bool is an int subclass — reject
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
        except ValueError:
            return None
    return None


def _auth_count(contents: dict) -> Optional[int]:
    """Number of EIP-7702 authorizations (delegations) on the tx. Present
    only on type-4 set-code txs. None when the field is absent; 0 when it is
    present but empty (key-presence, not truthiness, so [] -> 0 not None)."""
    for k in ("authorizationList", "authorization_list", "authorizations"):
        if k in contents:
            al = contents[k]
            return len(al) if isinstance(al, list) else None
    return None


def parse_bloxroute_tx(msg: dict, *, recv_ts: Optional[float] = None,
                       seq: Optional[int] = None,
                       source: str = DEFAULT_SOURCE,
                       clock_synced: Optional[bool] = None,
                       clock_offset_ms: Optional[float] = None) -> Optional[dict]:
    """Normalize a bloXroute newTxs/pendingTxs message to a flat tx dict.
    Defensive about camelCase vs snake_case. Returns None if not a tx msg.

    Tier-0 hardening: `recv_ts` is the socket-receipt time stamped by the
    caller immediately after ws.recv() (BEFORE json decode); the production
    capture loop always supplies it. If omitted it falls back to time.time()
    for backward compatibility (tests only). `seq` = monotonic per-event
    sequence; `source` = vantage tag. `clock_synced`/`clock_offset_ms` are
    the latest clock-discipline sample at receive time (None = unknown /
    NTP not yet enabled). All of these are ADDITIVE — every pre-existing
    field name/type/meaning below is unchanged."""
    params = msg.get("params") or {}
    result = params.get("result") or {}
    if not result:
        return None
    contents = (result.get("txContents") or result.get("tx_contents")
                or result.get("contents") or {})
    tx_hash = (result.get("txHash") or result.get("tx_hash")
               or contents.get("hash"))
    if not contents and not tx_hash:
        return None
    return {
        "hash": (tx_hash or "").lower(),
        "from": (contents.get("from") or "").lower(),
        "to": (contents.get("to") or "").lower(),
        "input": contents.get("input") or contents.get("data") or "",
        "value": contents.get("value"),
        "gas": contents.get("gas"),
        "gas_price": contents.get("gasPrice") or contents.get("gas_price"),
        "max_priority_fee": (contents.get("maxPriorityFeePerGas")
                             or contents.get("max_priority_fee_per_gas")),
        "nonce": contents.get("nonce"),
        # --- Tier-0 additive fields (all names/types above are frozen) ---
        "recv_ts": recv_ts if recv_ts is not None else time.time(),
        "seq": seq,
        "source": source,
        "clock_synced": clock_synced,
        "clock_offset_ms": clock_offset_ms,
        # EIP-2718 type + EIP-7702 delegation count (additive; None on feeds
        # / older rows that don't carry them). tx_type==4 == 7702 set-code.
        "tx_type": _tx_type(contents),
        "auth_count": _auth_count(contents),
    }


def _selector(input_hex: str) -> str:
    h = (input_hex or "").lower()
    if not h.startswith("0x"):
        h = "0x" + h
    return h[:10] if len(h) >= 10 else "0x"


# ----- competition density (the "can we compete?" measure) ----------------

class CompetitionTracker:
    """Sliding-window count of DISTINCT senders racing the same
    (destination, method) within `window_s`. High, tight clusters = an
    arena we can't win on speed."""

    def __init__(self, window_s: float = 2.0):
        self._w = window_s
        # key -> deque[(ts, sender)]
        self._buckets: dict[tuple, deque] = {}
        self.max_racers_seen = 0
        self.contended_events = 0          # events where >=2 distinct senders raced

    def observe(self, tx: dict) -> int:
        key = (tx.get("to", ""), _selector(tx.get("input", "")))
        ts = tx.get("recv_ts", time.time())
        dq = self._buckets.setdefault(key, deque())
        dq.append((ts, tx.get("from", "")))
        cutoff = ts - self._w
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        racers = len({s for _, s in dq})
        if racers >= 2:
            self.contended_events += 1
        self.max_racers_seen = max(self.max_racers_seen, racers)
        return racers


# ----- microstructure stats ----------------------------------------------

@dataclass
class MicrostructureStats:
    total: int = 0
    by_selector: Counter = field(default_factory=Counter)
    by_to: Counter = field(default_factory=Counter)
    by_sender: Counter = field(default_factory=Counter)
    third_party_transfer_from: int = 0
    priority_fees_gwei: list = field(default_factory=list)

    def observe(self, tx: dict) -> None:
        self.total += 1
        self.by_selector[_selector(tx.get("input", ""))] += 1
        if tx.get("to"):
            self.by_to[tx["to"]] += 1
        if tx.get("from"):
            self.by_sender[tx["from"]] += 1
        mpf = tx.get("max_priority_fee")
        if isinstance(mpf, str) and mpf.startswith("0x"):
            try:
                self.priority_fees_gwei.append(int(mpf, 16) / 1e9)
            except ValueError:
                pass

    def snapshot(self) -> dict:
        pf = sorted(self.priority_fees_gwei)
        def pct(p):
            if not pf:
                return None
            return pf[min(len(pf) - 1, int(p * len(pf)))]
        return {
            "total_txs": self.total,
            "third_party_transferFrom": self.third_party_transfer_from,
            "top_methods": self.by_selector.most_common(6),
            "top_destinations": self.by_to.most_common(8),
            "top_senders": self.by_sender.most_common(8),
            "priority_fee_gwei_p50": pct(0.5),
            "priority_fee_gwei_p90": pct(0.9),
        }


__all__ = [
    "decode_transfer_from", "is_third_party_transfer_from",
    "parse_bloxroute_tx", "CompetitionTracker", "MicrostructureStats",
    "SEL_TRANSFER_FROM", "SEL_TRANSFER", "SEL_APPROVE",
]
