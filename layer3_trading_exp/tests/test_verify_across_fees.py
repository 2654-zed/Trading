"""Unit tests for scripts/verify_across_fees.py (Phase 2 sub-phase 2.4, D-009).

These tests mock the live Across API so the verifier's decision logic
(keep / update / abort) is exercised deterministically without network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from layer3_trading_exp.bridge_model import _DEFAULT_FEE_BPS_BY_TOKEN, BridgeModel
from layer3_trading_exp.scripts.verify_across_fees import (
    DRIFT_BPS_ABORT,
    DRIFT_BPS_NO_OP,
    serialize_table,
    verify,
)
from layer3_trading_exp.token_registry import TokenRegistry


def _two_chain_registry() -> TokenRegistry:
    """Minimal registry: USDC + WETH on Base + Arbitrum only. Keeps the
    bridgeable_pairs() output small (4 tuples: USDC base↔arb, WETH base↔arb)
    so tests assert on exact counts."""
    return TokenRegistry(
        canonical_tokens={
            "USDC": {"base": "0xb1" + "0"*38, "arbitrum": "0xa1" + "0"*38},
            "WETH": {"base": "0xb2" + "0"*38, "arbitrum": "0xa2" + "0"*38},
        },
        decimals={"USDC": 6, "WETH": 18},
    )


def _make_request_returning(pct_str_by_url: dict[str, str]):
    """Build a request_fn that maps URL prefixes to fake `pct` strings."""
    def fake_request(url: str, timeout: float):
        for prefix, pct in pct_str_by_url.items():
            if prefix in url:
                return {"totalRelayFee": {"pct": pct}}
        # Unmatched URL → simulate API error by returning malformed payload.
        return {"unexpected": "no totalRelayFee"}
    return fake_request


def _make_request_constant(pct_str: str):
    """All URLs return the same pct → uniform live value for every tuple."""
    def fake_request(url: str, timeout: float):
        return {"totalRelayFee": {"pct": pct_str}}
    return fake_request


# Helper: convert bps to Across's 1e18-scaled string.
def _bps_to_pct_str(bps: float) -> str:
    """bps × 1e14 = the pct value Across returns."""
    return str(int(bps * 1e14))


def test_verify_all_within_no_op_band_keeps_static_values():
    """Static USDC = 10 bps; live = 11 bps (drift 1 bps ≤ 5). Keep static."""
    registry = _two_chain_registry()
    static = {"USDC": 10.0, "WETH": 8.0}
    fake = _make_request_constant(_bps_to_pct_str(11.0))
    results, final = verify(
        token_registry=registry, static_table=static,
        request_fn=fake,
    )
    # 4 tuples: USDC base→arb, USDC arb→base, WETH base→arb, WETH arb→base.
    assert len(results) == 4
    actions = [r.action for r in results]
    # USDC drift = 1 bps → kept; WETH drift = 3 bps → kept.
    assert all(a == "kept" for a in actions), actions
    # Final table uses static (NOT live) for kept routes.
    for (src, dst, sym), bps in final.items():
        assert bps == static[sym], (src, dst, sym, bps)


def test_verify_in_drift_band_updates_to_live_value():
    """Static USDC = 10; live = 18 bps (drift 8 in the (5, 20] band)."""
    registry = _two_chain_registry()
    static = {"USDC": 10.0, "WETH": 8.0}
    fake = _make_request_constant(_bps_to_pct_str(18.0))
    results, final = verify(
        token_registry=registry, static_table=static,
        request_fn=fake,
    )
    # USDC drift = 8 → updated. WETH static=8, live=18, drift=10 → updated.
    assert all(r.action == "updated" for r in results), [r.action for r in results]
    # Final table uses live (18.0) for updated routes.
    for bps in final.values():
        assert bps == 18.0


def test_verify_above_abort_threshold_aborts():
    """Static USDC = 10; live = 35 bps (drift 25 > 20). Verifier flags
    the tuples as `abort` and the final table EXCLUDES those routes
    (callers exit non-zero)."""
    registry = _two_chain_registry()
    static = {"USDC": 10.0, "WETH": 8.0}
    fake = _make_request_constant(_bps_to_pct_str(35.0))
    results, final = verify(
        token_registry=registry, static_table=static,
        request_fn=fake,
    )
    # Every tuple drifted > 20 bps from static.
    actions = [r.action for r in results]
    assert all(a == "abort" for a in actions), actions
    # `final` contains NO routes for aborted tuples (the verifier doesn't
    # write a partial table — the caller exits non-zero before write).
    assert final == {}


def test_verify_api_error_falls_back_to_static_with_note():
    """Across returns a payload missing `totalRelayFee` → no live value."""
    registry = _two_chain_registry()
    fake = _make_request_returning({})   # every URL → malformed payload
    results, final = verify(
        token_registry=registry,
        static_table={"USDC": 10.0, "WETH": 8.0},
        request_fn=fake,
    )
    assert all(r.action == "api_error" for r in results), [r.action for r in results]
    assert all(r.live_bps is None for r in results)
    # final uses static values despite API errors (graceful degradation).
    assert len(final) == 4


def test_verify_offline_mode_skips_api_entirely():
    """`offline=True` should never call request_fn."""
    registry = _two_chain_registry()
    call_count = {"n": 0}

    def fake(url: str, timeout: float):
        call_count["n"] += 1
        return {"totalRelayFee": {"pct": _bps_to_pct_str(99.0)}}

    results, final = verify(
        token_registry=registry,
        static_table={"USDC": 10.0, "WETH": 8.0},
        offline=True, request_fn=fake,
    )
    assert call_count["n"] == 0
    assert all(r.action == "kept" for r in results)
    assert all(r.live_bps is None for r in results)


def test_serialize_table_round_trips_through_bridge_model_load(tmp_path):
    """The verifier's output JSON must round-trip through
    BridgeModel.load_verified_table → produce a model whose per-route
    fees match what the verifier wrote."""
    registry = _two_chain_registry()
    fake = _make_request_constant(_bps_to_pct_str(15.0))
    _, final = verify(
        token_registry=registry,
        static_table={"USDC": 10.0, "WETH": 8.0},
        request_fn=fake,
    )
    payload = serialize_table(final, notional_usd=10_000.0)
    path = tmp_path / "across_fee_table.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    bm = BridgeModel.load_verified_table(path, token_registry=registry)
    # Every (src, dst, sym) in the verified table must produce that fee.
    for (src, dst, sym), expected_bps in final.items():
        assert bm.fee_bps(src, dst, sym) == expected_bps, (src, dst, sym)


def test_bridge_model_load_rejects_unsupported_schema_version(tmp_path):
    path = tmp_path / "across_fee_table.json"
    path.write_text(json.dumps({"schema_version": 2, "routes_bps": {}}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported across_fee_table schema_version"):
        BridgeModel.load_verified_table(path)


def test_bridge_model_load_rejects_malformed_key(tmp_path):
    """Loader must surface a clear error if a route key is malformed."""
    path = tmp_path / "across_fee_table.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "routes_bps": {"this is not a valid key": 10.0},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="malformed across_fee_table key"):
        BridgeModel.load_verified_table(path)


def test_drift_thresholds_match_spec():
    """Sanity guard: thresholds in the code match the spec sub-phase 2.4
    contract (5 bps no-op, 20 bps abort)."""
    assert DRIFT_BPS_NO_OP == 5.0
    assert DRIFT_BPS_ABORT == 20.0
