"""H9 live paper-trading core (network-free, unit-tested).

Implements the validated H9 strategy (D-043/D-044) as a forward paper
trader: on a strong `entropy_drop`-on-roles signal, open a paper LONG of
fixed USD size; after the 48h horizon, close at the real forward price
and book net P&L after fees + constant-product slippage (the cost model
validated in gate #2). NO capital, NO on-chain action — it only records
what it WOULD do, building a live out-of-sample track record.

Survivorship-honest: a token whose price coverage disappears past the
horizon (a death/rug) is closed at a catastrophic loss after a grace
period, NOT silently dropped.

This module is pure logic + an injected price source, so it is fully
unit-testable without network. The live driver (`scripts/paper_trade_h9`)
wires it to fresh L3 dumps + DefiLlama prices and persists state across
runs so it accumulates as data arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional, Protocol


# Strategy constants (from D-044). entropy_drop strength = entropy_delta /
# baseline_entropy (∈[0,1]); the validated edge was the strong cohort.
DEFAULT_STRENGTH_THRESHOLD = 0.5
DEFAULT_HORIZON_SECONDS = 48 * 3600
DEFAULT_POSITION_USD = 10_000.0
DEFAULT_GAS_USD = 0.10
# A position overdue by more than this with no real exit price → the token
# lost coverage (died); close it survivorship-honestly.
DEATH_GRACE_SECONDS = 7 * 86400
DEATH_GROSS_RETURN = -1.0   # assume worthless / unexitable


class PriceSource(Protocol):
    def price_at(self, token: str, ts: float) -> Optional[float]: ...
    def has_data_through(self, token: str, ts: float) -> bool: ...


def cp_net_return(gross: float, fee_bps: float, tvl: float, X: float,
                  gas_usd: float = DEFAULT_GAS_USD) -> float:
    """Validated round-trip net return (D-044 gate #2): entry slippage on
    position X, exit slippage on the post-move bag X·(1+gross), constant
    TVL (conservative), pool fee each leg, fixed gas."""
    fee = fee_bps / 10000.0
    reserve = max(tvl / 2.0, 1.0)
    slip_in = X / (reserve + X)
    x_exit = X * (1 + gross) if gross > -1 else 0.0
    slip_out = x_exit / (reserve + x_exit) if x_exit > 0 else 1.0
    mult = (1 + gross) * (1 - fee) ** 2 * (1 - slip_in) * (1 - slip_out)
    return mult - 1 - gas_usd / max(X, 1.0)


@dataclass
class PaperPosition:
    key: str
    token: str
    signal_ts: float
    strength: float
    entry_price: float
    horizon_end: float


@dataclass
class ClosedTrade:
    key: str
    token: str
    signal_ts: float
    strength: float
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    tvl: float
    position_usd: float
    closed_reason: str   # "matured" | "death"


class H9PaperTrader:
    SIGNAL_TYPE = "entropy_drop"
    SIGNAL_SOURCE = "org_transfer_roles"

    def __init__(
        self,
        price_source: PriceSource,
        token_pool_info: dict,            # token_lower -> (fee_bps, tvl_usd)
        *,
        strength_threshold: float = DEFAULT_STRENGTH_THRESHOLD,
        horizon_seconds: float = DEFAULT_HORIZON_SECONDS,
        position_usd: float = DEFAULT_POSITION_USD,
        gas_usd: float = DEFAULT_GAS_USD,
    ):
        self._px = price_source
        self._pools = {k.lower(): v for k, v in token_pool_info.items()}
        self._thr = strength_threshold
        self._h = horizon_seconds
        self._pos = position_usd
        self._gas = gas_usd
        self.open: dict[str, PaperPosition] = {}
        self.closed: list[ClosedTrade] = []
        self.seen: set[str] = set()          # dedup across runs
        self.skipped_no_price = 0
        self.skipped_no_pool = 0

    @staticmethod
    def trade_key(token: str, signal_ts: float) -> str:
        return f"{token.lower()}@{int(signal_ts)}"

    # --- entry ------------------------------------------------------

    def process_signal(self, *, token: str, signal_ts: float, strength: float,
                       signal_type: str, source: str) -> bool:
        """Consider a signal for entry. Returns True if a position opened.
        Idempotent: a (token, ts) seen before is ignored (safe re-runs)."""
        if signal_type != self.SIGNAL_TYPE or source != self.SIGNAL_SOURCE:
            return False
        if strength < self._thr:
            return False
        key = self.trade_key(token, signal_ts)
        if key in self.seen:
            return False
        tok = token.lower()
        if tok not in self._pools:
            self.skipped_no_pool += 1
            return False
        entry = self._px.price_at(tok, signal_ts)
        if not entry or entry <= 0:
            self.skipped_no_price += 1
            return False
        self.seen.add(key)
        self.open[key] = PaperPosition(
            key=key, token=tok, signal_ts=signal_ts, strength=strength,
            entry_price=entry, horizon_end=signal_ts + self._h,
        )
        return True

    # --- exit -------------------------------------------------------

    def mark_to_market(self, now_ts: float) -> int:
        """Close every open position whose horizon has elapsed and whose
        real exit price is available. Tokens that lost coverage past the
        grace window close as deaths. Returns # closed this call."""
        closed_now = 0
        for key in list(self.open.keys()):
            p = self.open[key]
            if p.horizon_end > now_ts:
                continue  # not matured yet
            has_exit = self._px.has_data_through(p.token, p.horizon_end)
            if has_exit:
                exit_p = self._px.price_at(p.token, p.horizon_end) or 0.0
                gross = ((exit_p - p.entry_price) / p.entry_price
                         if p.entry_price > 0 else DEATH_GROSS_RETURN)
                reason = "matured"
            elif now_ts - p.horizon_end >= DEATH_GRACE_SECONDS:
                exit_p = 0.0
                gross = DEATH_GROSS_RETURN
                reason = "death"
            else:
                continue  # matured but exit price not yet available; wait
            fee_bps, tvl = self._pools[p.token]
            net = cp_net_return(gross, fee_bps, tvl, self._pos, self._gas)
            self.closed.append(ClosedTrade(
                key=p.key, token=p.token, signal_ts=p.signal_ts,
                strength=p.strength, entry_price=p.entry_price,
                exit_price=exit_p, gross_return=gross, net_return=net,
                tvl=tvl, position_usd=self._pos, closed_reason=reason,
            ))
            del self.open[key]
            closed_now += 1
        return closed_now

    # --- reporting --------------------------------------------------

    def track_record(self) -> dict:
        n = len(self.closed)
        if n == 0:
            return {"closed": 0, "open": len(self.open)}
        nets = sorted(t.net_return for t in self.closed)
        wins = sum(1 for x in nets if x > 0)
        total_pnl = sum(t.net_return * t.position_usd for t in self.closed)
        deaths = sum(1 for t in self.closed if t.closed_reason == "death")
        return {
            "closed": n,
            "open": len(self.open),
            "win_rate": wins / n,
            "mean_net": sum(nets) / n,
            "median_net": nets[n // 2],
            "best_net": nets[-1],
            "worst_net": nets[0],
            "cum_pnl_usd": total_pnl,
            "deaths": deaths,
            "position_usd": self._pos,
            "skipped_no_price": self.skipped_no_price,
            "skipped_no_pool": self.skipped_no_pool,
        }

    # --- persistence ------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "config": {"strength_threshold": self._thr, "horizon_seconds": self._h,
                       "position_usd": self._pos, "gas_usd": self._gas},
            "open": [asdict(p) for p in self.open.values()],
            "closed": [asdict(t) for t in self.closed],
            "seen": sorted(self.seen),
            "skipped_no_price": self.skipped_no_price,
            "skipped_no_pool": self.skipped_no_pool,
        }

    def load_state(self, state: dict) -> None:
        self.open = {p["key"]: PaperPosition(**p) for p in state.get("open", [])}
        self.closed = [ClosedTrade(**t) for t in state.get("closed", [])]
        self.seen = set(state.get("seen", []))
        self.skipped_no_price = state.get("skipped_no_price", 0)
        self.skipped_no_pool = state.get("skipped_no_pool", 0)


__all__ = ["H9PaperTrader", "PaperPosition", "ClosedTrade",
           "cp_net_return", "PriceSource", "DEFAULT_STRENGTH_THRESHOLD",
           "DEFAULT_HORIZON_SECONDS", "DEFAULT_POSITION_USD"]
