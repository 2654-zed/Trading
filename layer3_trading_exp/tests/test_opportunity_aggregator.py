"""Unit tests for OpportunityAggregator.

Confirms the dedup logic: per-block emissions of the same opportunity collapse
into a single episode; a key disappearing for >=1 block then reappearing
counts as a new episode; duplicate block events from the WS subscription do
not spuriously close active episodes.
"""

from __future__ import annotations

from layer3_trading_exp.opportunity_aggregator import (
    Episode,
    OpportunityAggregator,
    OpportunityKey,
)
from layer3_trading_exp.opportunity_detector import Opportunity, PoolLeg


def _opp(
    pool_x: str = "0xaa",
    pool_y: str = "0xbb",
    borrow: str = "0xcc",
    margin: float = 0.005,
    net_gain: float = 25.0,
    block_number: int = 100,
) -> Opportunity:
    """Construct a minimal Opportunity for aggregator testing."""
    leg1 = PoolLeg(
        pool_address=pool_x, protocol="uniswap_v3", fee_bps=5,
        token_in_addr=borrow, token_in_symbol="USDC",
        token_out_addr="0xdd", token_out_symbol="WETH",
        amount_in_raw=10**10, amount_out_raw=5 * 10**15,
    )
    leg2 = PoolLeg(
        pool_address=pool_y, protocol="aerodrome_volatile", fee_bps=30,
        token_in_addr="0xdd", token_in_symbol="WETH",
        token_out_addr=borrow, token_out_symbol="USDC",
        amount_in_raw=5 * 10**15, amount_out_raw=int(10**10 * (1 + margin)),
    )
    return Opportunity(
        opportunity_id="id",
        block_number=block_number, block_timestamp=0,
        detected_at="t",
        legs=(leg1, leg2),
        borrowed_token_addr=borrow, borrowed_token_symbol="USDC",
        notional_usd=10_000.0,
        amount_in_raw=10**10, amount_out_raw=int(10**10 * (1 + margin)),
        gross_margin=margin,
        gross_gain_usd=10_000.0 * margin,
        gas_cost_usd=0.005, flash_loan_fee_usd=5.0,
        expected_net_gain_usd=net_gain,
    )


def test_single_persistent_arb_collapses_to_one_episode():
    """The Phase 1.2 dry-run case: one arb persists for many consecutive
    blocks. Emissions = N (one per block); unique_keys = 1; episodes = 1."""
    agg = OpportunityAggregator()
    for block in range(100, 110):
        agg.record_block(block, [_opp(block_number=block)])
    agg.finalize()

    s = agg.summary()
    assert s["total_emissions"] == 10
    assert s["unique_keys"] == 1
    assert s["total_episodes"] == 1
    assert s["max_episode_length_blocks"] == 10


def test_arb_disappearing_then_reappearing_is_two_episodes():
    """Same key emits at blocks 100-103, gap at 104-105, then 106-108. Should
    count as 2 episodes of the same key."""
    agg = OpportunityAggregator()
    key_opp = lambda b: _opp(block_number=b)
    for block in range(100, 104):
        agg.record_block(block, [key_opp(block)])
    # Gap: blocks 104, 105 emit nothing for this key.
    agg.record_block(104, [])
    agg.record_block(105, [])
    for block in range(106, 109):
        agg.record_block(block, [key_opp(block)])
    agg.finalize()

    s = agg.summary()
    assert s["total_emissions"] == 7
    assert s["unique_keys"] == 1
    assert s["total_episodes"] == 2


def test_duplicate_block_events_dont_close_active_episodes():
    """The WebSocket subscription occasionally emits the same block twice (we
    saw this in the Phase 1.2 dry run). Duplicate block events must not break
    an episode in half."""
    agg = OpportunityAggregator()
    # Block 100 fires twice (duplicate WS event), then continues normally.
    agg.record_block(100, [_opp(block_number=100)])
    agg.record_block(100, [_opp(block_number=100)])  # duplicate
    agg.record_block(101, [_opp(block_number=101)])
    agg.record_block(102, [_opp(block_number=102)])
    agg.finalize()

    s = agg.summary()
    assert s["total_emissions"] == 4
    assert s["unique_keys"] == 1
    assert s["total_episodes"] == 1


def test_two_distinct_arbs_track_separately():
    """Two unrelated opportunities (different pool combos) each get their own
    episode. Unique key count = 2."""
    agg = OpportunityAggregator()
    o1 = lambda b: _opp(pool_x="0x11", pool_y="0x22", block_number=b)
    o2 = lambda b: _opp(pool_x="0x33", pool_y="0x44", block_number=b)
    for block in range(100, 105):
        agg.record_block(block, [o1(block), o2(block)])
    agg.finalize()

    s = agg.summary()
    assert s["total_emissions"] == 10
    assert s["unique_keys"] == 2
    assert s["total_episodes"] == 2


def test_unique_margin_distribution_uses_one_sample_per_key():
    """If one persistent arb emits 1000 times at 0.31% and a transient one
    emits 5 times at 1.50%, the UNIQUE margin distribution should see two
    samples (0.31% and 1.50%), not 1005."""
    agg = OpportunityAggregator()
    persistent = lambda b: _opp(pool_x="0xaa", pool_y="0xbb", margin=0.0031, block_number=b)
    transient = lambda b: _opp(pool_x="0xcc", pool_y="0xdd", margin=0.015, block_number=b)
    for block in range(100, 200):
        ops = [persistent(block)]
        if 150 <= block < 155:
            ops.append(transient(block))
        agg.record_block(block, ops)
    agg.finalize()

    s = agg.summary()
    # Persistent: 100 emissions, 1 episode. Transient: 5 emissions, 1 episode.
    # Unique keys: 2.
    assert s["total_emissions"] == 105
    assert s["unique_keys"] == 2
    assert s["total_episodes"] == 2
    # Unique-margin median: with 2 unique keys, p50 = either the higher or
    # lower. statistics.median on 2 values returns the mean.
    assert s["unique_margin_min_pct"] == 0.31
    assert s["unique_margin_max_pct"] == 1.5


def test_finalize_promotes_active_episodes_to_completed():
    """Episodes still active when the run ends must be reported in the summary
    (not silently lost). Equivalent to the dry-run script ending mid-arb."""
    agg = OpportunityAggregator()
    for block in range(100, 105):
        agg.record_block(block, [_opp(block_number=block)])
    # No finalize() yet — confirm active episode IS counted (already in unique_keys)
    s_before = agg.summary()
    assert s_before["unique_keys"] == 1
    assert s_before["total_episodes"] == 1
    # After finalize, same.
    agg.finalize()
    s_after = agg.summary()
    assert s_after["unique_keys"] == 1
    assert s_after["total_episodes"] == 1


def test_top_active_keys_sorted_by_emission_count():
    """The top-active list should rank by total emissions, descending."""
    agg = OpportunityAggregator()
    quiet = lambda b: _opp(pool_x="0xaa", pool_y="0xbb", margin=0.004, block_number=b)
    loud = lambda b: _opp(pool_x="0xcc", pool_y="0xdd", margin=0.005, block_number=b)
    # Loud emits every block, quiet only on every 5th
    for block in range(100, 200):
        ops = [loud(block)]
        if block % 5 == 0:
            ops.append(quiet(block))
        agg.record_block(block, ops)
    agg.finalize()

    s = agg.summary()
    top = s["top_5_most_active_keys"]
    assert len(top) == 2  # only 2 unique keys
    assert top[0]["emissions"] > top[1]["emissions"]
    assert top[0]["pool_x"] == "0xcc"  # loud
    assert top[1]["pool_x"] == "0xaa"  # quiet
