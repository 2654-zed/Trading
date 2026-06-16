"""Tests for the H9 live paper-trading core (network-free)."""

from __future__ import annotations

from engine.paper.h9_paper_trader import (
    H9PaperTrader, cp_net_return, DEFAULT_HORIZON_SECONDS, DEATH_GRACE_SECONDS,
)


class FakePrices:
    """Injectable price source. `series` = {token: [(ts, price), ...]} sorted."""
    def __init__(self, series):
        self._s = {k.lower(): v for k, v in series.items()}

    def price_at(self, token, ts):
        s = self._s.get(token.lower())
        if not s:
            return None
        # nearest point
        best = min(s, key=lambda p: abs(p[0] - ts))
        return best[1]

    def has_data_through(self, token, ts):
        s = self._s.get(token.lower())
        return bool(s) and s[-1][0] >= ts


POOLS = {"0xtok": (30.0, 2_000_000.0)}   # fee 30bps, $2M TVL
H = DEFAULT_HORIZON_SECONDS


# ----- cost model --------------------------------------------------------

def test_cp_net_return_positive_gross_liquid_pool():
    # +50% gross, $10k into $2M pool → net should be clearly positive.
    net = cp_net_return(0.50, 30.0, 2_000_000.0, 10_000.0)
    assert 0.40 < net < 0.50


def test_cp_net_return_big_pump_exit_slippage_bites():
    # +300% gross: exit dumps a ~4x bag → exit slippage noticeably reduces net.
    net_small = cp_net_return(3.0, 30.0, 2_000_000.0, 1_000.0)
    net_big = cp_net_return(3.0, 30.0, 2_000_000.0, 100_000.0)
    assert net_big < net_small          # larger position keeps less of the gain
    assert net_small > 2.5


def test_cp_net_return_flat_is_slightly_negative():
    # 0% gross → lose fees+slippage+gas.
    assert cp_net_return(0.0, 30.0, 2_000_000.0, 10_000.0) < 0


# ----- entry logic -------------------------------------------------------

def _trader(prices=None):
    px = prices or FakePrices({"0xtok": [(1000.0, 1.0), (1000.0 + H, 1.5)]})
    return H9PaperTrader(px, POOLS, strength_threshold=0.5, position_usd=10_000.0)


def test_opens_strong_entropy_drop_roles_signal():
    t = _trader()
    ok = t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                          signal_type="entropy_drop", source="org_transfer_roles")
    assert ok
    assert len(t.open) == 1


def test_ignores_weak_signal():
    t = _trader()
    ok = t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.3,
                          signal_type="entropy_drop", source="org_transfer_roles")
    assert not ok and len(t.open) == 0


def test_ignores_wrong_type_or_source():
    t = _trader()
    assert not t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.9,
                                signal_type="regime_surprise", source="org_transfer_roles")
    assert not t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.9,
                                signal_type="entropy_drop", source="liquidity_events")
    assert len(t.open) == 0


def test_dedup_same_signal_not_reopened():
    t = _trader()
    assert t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                            signal_type="entropy_drop", source="org_transfer_roles")
    # Same (token, ts) again → ignored (idempotent re-runs).
    assert not t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                                signal_type="entropy_drop", source="org_transfer_roles")
    assert len(t.open) == 1


def test_skips_token_without_pool():
    t = _trader()
    assert not t.process_signal(token="0xunknown", signal_ts=1000.0, strength=0.9,
                                signal_type="entropy_drop", source="org_transfer_roles")
    assert t.skipped_no_pool == 1


def test_skips_token_without_entry_price():
    t = H9PaperTrader(FakePrices({}), POOLS)  # no prices at all
    assert not t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.9,
                                signal_type="entropy_drop", source="org_transfer_roles")
    assert t.skipped_no_price == 1


# ----- exit logic --------------------------------------------------------

def test_does_not_close_before_horizon():
    t = _trader()
    t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                     signal_type="entropy_drop", source="org_transfer_roles")
    # now = just after entry, before horizon
    assert t.mark_to_market(1000.0 + H / 2) == 0
    assert len(t.open) == 1


def test_closes_matured_position_at_real_exit_price():
    px = FakePrices({"0xtok": [(1000.0, 1.0), (1000.0 + H, 1.5)]})
    t = H9PaperTrader(px, POOLS, strength_threshold=0.5, position_usd=10_000.0)
    t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                     signal_type="entropy_drop", source="org_transfer_roles")
    closed = t.mark_to_market(1000.0 + H + 1)
    assert closed == 1 and len(t.open) == 0
    trade = t.closed[0]
    assert abs(trade.gross_return - 0.5) < 1e-9   # 1.0 -> 1.5
    assert trade.closed_reason == "matured"
    assert trade.net_return > 0.40


def test_waits_when_exit_price_not_yet_available():
    # series ends before horizon, still within grace → don't close yet.
    px = FakePrices({"0xtok": [(1000.0, 1.0), (1000.0 + 3600, 1.2)]})
    t = H9PaperTrader(px, POOLS, strength_threshold=0.5)
    t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                     signal_type="entropy_drop", source="org_transfer_roles")
    # now just past horizon but exit data missing, within grace
    assert t.mark_to_market(1000.0 + H + 100) == 0
    assert len(t.open) == 1


def test_death_close_after_grace_when_no_exit_data():
    px = FakePrices({"0xtok": [(1000.0, 1.0), (1000.0 + 3600, 1.2)]})
    t = H9PaperTrader(px, POOLS, strength_threshold=0.5)
    t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                     signal_type="entropy_drop", source="org_transfer_roles")
    # now well past horizon + grace, still no exit data → death close.
    closed = t.mark_to_market(1000.0 + H + DEATH_GRACE_SECONDS + 1)
    assert closed == 1
    trade = t.closed[0]
    assert trade.closed_reason == "death"
    assert trade.gross_return == -1.0
    assert trade.net_return < -0.9


# ----- track record + persistence ---------------------------------------

def test_track_record_winrate_and_pnl():
    px = FakePrices({
        "0xwin": [(0.0, 1.0), (H, 2.0)],     # +100%
        "0xlose": [(0.0, 1.0), (H, 0.8)],    # -20%
    })
    pools = {"0xwin": (30.0, 5_000_000.0), "0xlose": (30.0, 5_000_000.0)}
    t = H9PaperTrader(px, pools, strength_threshold=0.5, position_usd=10_000.0)
    t.process_signal(token="0xwin", signal_ts=0.0, strength=0.9,
                     signal_type="entropy_drop", source="org_transfer_roles")
    t.process_signal(token="0xlose", signal_ts=0.0, strength=0.9,
                     signal_type="entropy_drop", source="org_transfer_roles")
    t.mark_to_market(H + 1)
    tr = t.track_record()
    assert tr["closed"] == 2
    assert tr["win_rate"] == 0.5
    assert tr["best_net"] > 0.9
    assert tr["worst_net"] < 0


def test_state_round_trips():
    px = FakePrices({"0xtok": [(1000.0, 1.0), (1000.0 + H, 1.5)]})
    t = H9PaperTrader(px, POOLS, strength_threshold=0.5)
    t.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                     signal_type="entropy_drop", source="org_transfer_roles")
    state = t.to_dict()
    t2 = H9PaperTrader(px, POOLS, strength_threshold=0.5)
    t2.load_state(state)
    assert t2.seen == t.seen
    assert len(t2.open) == 1
    # A reload then re-process of the same signal must NOT double-open.
    assert not t2.process_signal(token="0xtok", signal_ts=1000.0, strength=0.8,
                                 signal_type="entropy_drop", source="org_transfer_roles")
    # And it can still close normally.
    assert t2.mark_to_market(1000.0 + H + 1) == 1


def test_persisted_closed_trades_survive_reload():
    px = FakePrices({"0xtok": [(0.0, 1.0), (H, 1.5)]})
    t = H9PaperTrader(px, POOLS, strength_threshold=0.5)
    t.process_signal(token="0xtok", signal_ts=0.0, strength=0.8,
                     signal_type="entropy_drop", source="org_transfer_roles")
    t.mark_to_market(H + 1)
    t2 = H9PaperTrader(px, POOLS)
    t2.load_state(t.to_dict())
    assert t2.track_record()["closed"] == 1
