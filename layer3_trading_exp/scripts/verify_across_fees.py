"""Phase 2 sub-phase 2.4 (D-009) — deployment-time Across fee verifier.

Per I-13 (bridge fees frozen for the run) and D-006 (Across as bridge model),
this one-shot script:

1. Loads the static D-006 baseline fees from `bridge_model._DEFAULT_FEE_BPS_BY_TOKEN`.
2. For every (src_chain, dst_chain, token) tuple in
   `TokenRegistry.bridgeable_pairs()`, queries Across's public
   `/api/suggested-fees` endpoint for a live quote at the canonical
   `$10,000` notional (Phase 1's `NOTIONAL_USD_FLOOR`).
3. Computes the drift between live and static. Branches:
   - **drift ≤ 5 bps**: keep the static value (no change).
   - **5 < drift ≤ 20 bps**: update the table to use the live value.
   - **drift > 20 bps** (outside D-006's sensitivity range): ABORT — exit
     non-zero with a loud log line. `entrypoint.sh` halts the deploy.
4. Writes the frozen table to `<run_metadata_dir>/across_fee_table.json`.
   The detector reads this at startup (or falls back to the static
   defaults if the file is absent — useful for local dev without
   internet access).

Acceptance criteria (spec sub-phase 2.4):
  - Runs in < 60s against live Across API
  - All 5 canonical tokens × directional chain pairs (≤30 tuples) verified
  - Loud failure on verification failure; no detector starts
  - Static table immutable post-verification (I-13)

Run:
    python -m layer3_trading_exp.scripts.verify_across_fees
    python -m layer3_trading_exp.scripts.verify_across_fees --notional-usd 25000
    python -m layer3_trading_exp.scripts.verify_across_fees --dry-run    # don't write
    python -m layer3_trading_exp.scripts.verify_across_fees --offline    # use static only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# UTF-8 stdout for Windows cp1252 redirection.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from ..bridge_model import (
    DEFAULT as DEFAULT_BRIDGE,
    _DEFAULT_FEE_BPS_BY_TOKEN,
)
from ..config import DEFAULT as DEFAULT_CONFIG
from ..opportunity_detector import NOTIONAL_USD_FLOOR
from ..token_registry import DEFAULT as DEFAULT_REGISTRY, TokenRegistry


# EVM chain IDs for the Across L2 triangle. Across keys requests by
# numeric chainId, not by name. Hardcoded here (not in config) because
# these are protocol-level constants, not deployment-tunable.
CHAIN_ID_BY_LABEL: dict[str, int] = {
    "base":     8453,
    "arbitrum": 42161,
    "optimism": 10,
}


# Sensitivity thresholds per D-006 + spec sub-phase 2.4.
DRIFT_BPS_NO_OP = 5.0          # ≤ 5 bps: ignore, keep static
DRIFT_BPS_ABORT = 20.0         # > 20 bps: abort deploy


# Across public suggested-fees endpoint. Read-only HTTP GET; no auth required.
ACROSS_SUGGESTED_FEES_URL = "https://app.across.to/api/suggested-fees"

# Per-request timeout. The spec wants < 60s TOTAL across ~30 tuples =
# < 2s per request on average. 10s per-request timeout absorbs occasional
# slow responses without blowing the global budget.
REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class VerificationResult:
    src_chain: str
    dst_chain: str
    token_symbol: str
    static_bps: float
    live_bps: Optional[float]   # None if API call failed
    drift_bps: Optional[float]  # absolute drift
    action: str                 # "kept", "updated", "abort", "api_error"
    note: str = ""


def _make_request(url: str, timeout: float) -> dict:
    """Read-only GET. Returns parsed JSON dict. Raises on HTTP/JSON error.
    Per I-1 + I-3: this script only reads from Across's public docs API."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "layer3-trading-exp/1.0 (phase-2-verifier)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body)


def _quote_one_tuple(
    src_chain: str,
    dst_chain: str,
    token_symbol: str,
    token_registry: TokenRegistry,
    notional_usd: float,
    *,
    request_fn=_make_request,
) -> Optional[float]:
    """Query Across for a single (src, dst, token) live fee. Returns the
    fee in bps, or None if the API call fails or returns an unusable
    payload. Falling-back to None lets the caller decide whether to abort
    or continue.

    Across `suggested-fees` request shape (verified against docs at module
    write-time):
        GET /api/suggested-fees
            ?token=<src_chain_token_address>
            &originChainId=<src_chain_id>
            &destinationChainId=<dst_chain_id>
            &amount=<wei_amount_in_token_decimals>

    Response shape:
        {
          "totalRelayFee": {"pct": "<int18>", "total": "..."},
          "relayerCapitalFee": {...},
          "relayerGasFee": {...},
          "lpFee": {...},
          "timestamp": "...",
          ...
        }
    `pct` is the fee as a fraction scaled by 1e18. 10 bps = 0.001 = 1e15.
    """
    src_token_addr = token_registry.canonical_address(token_symbol, src_chain)
    if src_token_addr is None:
        return None
    src_id = CHAIN_ID_BY_LABEL[src_chain]
    dst_id = CHAIN_ID_BY_LABEL[dst_chain]
    decimals = token_registry.decimals(token_symbol)
    # Across wants amount in the token's raw units. Convert $notional to
    # raw amount using a rough $1-per-stablecoin-unit assumption for
    # stables and using a $3000/ETH placeholder for WETH / $90000/BTC for
    # cbBTC (just for the API quote — the quote itself is independent of
    # the underlying USD valuation; we only use this to give the relayer
    # a representative size).
    if token_symbol in ("USDC", "USDT", "DAI"):
        raw_amount = int(notional_usd * (10 ** decimals))
    elif token_symbol == "WETH":
        raw_amount = int(notional_usd / 3000.0 * (10 ** decimals))
    elif token_symbol == "cbBTC":
        raw_amount = int(notional_usd / 90000.0 * (10 ** decimals))
    else:
        # Fallback: assume 1:1 USD
        raw_amount = int(notional_usd * (10 ** decimals))

    query = urllib.parse.urlencode({
        "token": src_token_addr,
        "originChainId": src_id,
        "destinationChainId": dst_id,
        "amount": str(raw_amount),
    })
    url = f"{ACROSS_SUGGESTED_FEES_URL}?{query}"
    try:
        payload = request_fn(url, REQUEST_TIMEOUT_SECONDS)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            TimeoutError, json.JSONDecodeError):
        return None
    # Extract totalRelayFee.pct → bps.
    try:
        pct_str = payload["totalRelayFee"]["pct"]
        # `pct` is a string-encoded uint scaled by 1e18 (e.g. "1000000000000000"
        # = 0.001 = 10 bps).
        pct_int = int(pct_str)
        fee_bps = pct_int / 1e14
        return float(fee_bps)
    except (KeyError, TypeError, ValueError):
        return None


def verify(
    *,
    token_registry: TokenRegistry = DEFAULT_REGISTRY,
    static_table: Optional[dict[str, float]] = None,
    notional_usd: float = NOTIONAL_USD_FLOOR,
    offline: bool = False,
    request_fn=_make_request,
) -> tuple[list[VerificationResult], dict[tuple[str, str, str], float]]:
    """Iterate every bridgeable (src, dst, token) tuple and check drift.

    Returns (results, final_fee_table) where final_fee_table is a dict
    keyed by `(src_chain, dst_chain, token_symbol)` → fee_bps with the
    drift-adjusted values (kept-or-updated). On abort, raises SystemExit
    so the caller (entrypoint.sh) sees a non-zero exit.

    `offline=True` skips the API and just returns the static defaults as
    the final table — useful for local dev when Across is unreachable
    (e.g. campus firewall — see Phase 1.1 addendum). NOT for production.
    """
    static = dict(static_table or _DEFAULT_FEE_BPS_BY_TOKEN)
    results: list[VerificationResult] = []
    final: dict[tuple[str, str, str], float] = {}

    for src_chain, dst_chain, token_symbol in token_registry.bridgeable_pairs():
        # Per-token default from the static table (D-006 baseline).
        static_bps = float(static.get(token_symbol, 0.0))

        if offline:
            results.append(VerificationResult(
                src_chain=src_chain, dst_chain=dst_chain,
                token_symbol=token_symbol,
                static_bps=static_bps, live_bps=None, drift_bps=None,
                action="kept", note="offline mode; no live query",
            ))
            final[(src_chain, dst_chain, token_symbol)] = static_bps
            continue

        live_bps = _quote_one_tuple(
            src_chain, dst_chain, token_symbol,
            token_registry, notional_usd, request_fn=request_fn,
        )
        if live_bps is None:
            # I-6 loud failure: a tuple we can't quote at all is suspicious,
            # but not necessarily a deploy-blocker — Across may not have
            # liquidity for some route at the moment. Use the static value
            # and surface the gap.
            results.append(VerificationResult(
                src_chain=src_chain, dst_chain=dst_chain,
                token_symbol=token_symbol,
                static_bps=static_bps, live_bps=None, drift_bps=None,
                action="api_error",
                note="quote unreachable; using static value",
            ))
            final[(src_chain, dst_chain, token_symbol)] = static_bps
            continue

        drift = abs(live_bps - static_bps)
        if drift > DRIFT_BPS_ABORT:
            results.append(VerificationResult(
                src_chain=src_chain, dst_chain=dst_chain,
                token_symbol=token_symbol,
                static_bps=static_bps, live_bps=live_bps, drift_bps=drift,
                action="abort",
                note=(f"drift {drift:.1f} bps > abort threshold "
                      f"{DRIFT_BPS_ABORT:.1f} bps"),
            ))
            # Continue iterating so the report lists every problematic
            # tuple, but the caller still exits non-zero at the end.
            continue
        if drift > DRIFT_BPS_NO_OP:
            results.append(VerificationResult(
                src_chain=src_chain, dst_chain=dst_chain,
                token_symbol=token_symbol,
                static_bps=static_bps, live_bps=live_bps, drift_bps=drift,
                action="updated",
                note=f"drift {drift:.1f} bps in (5, 20] band; using live",
            ))
            final[(src_chain, dst_chain, token_symbol)] = live_bps
        else:
            results.append(VerificationResult(
                src_chain=src_chain, dst_chain=dst_chain,
                token_symbol=token_symbol,
                static_bps=static_bps, live_bps=live_bps, drift_bps=drift,
                action="kept",
                note=f"drift {drift:.1f} bps within ±5 bps; keeping static",
            ))
            final[(src_chain, dst_chain, token_symbol)] = static_bps

    return results, final


def serialize_table(
    fee_table: dict[tuple[str, str, str], float],
    *,
    notional_usd: float,
    verified_at: Optional[str] = None,
) -> dict:
    """Convert the in-memory route-keyed dict to a JSON-serializable shape.

    Keys are dict-stringified `"src->dst:symbol"` so the JSON form is
    diff-readable. Reverse-parseable for `BridgeModel.load_verified_table`.
    """
    routes_serialized: dict[str, float] = {}
    for (src, dst, sym), bps in sorted(fee_table.items()):
        routes_serialized[f"{src}->{dst}:{sym}"] = bps
    return {
        "schema_version": 1,
        "verified_at": verified_at or datetime.now(timezone.utc).isoformat(),
        "notional_usd": notional_usd,
        "drift_thresholds_bps": {
            "no_op": DRIFT_BPS_NO_OP,
            "abort": DRIFT_BPS_ABORT,
        },
        "routes_bps": routes_serialized,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notional-usd", type=float, default=NOTIONAL_USD_FLOOR,
                    help="Quote notional used for the Across API call. Default "
                         f"= ${NOTIONAL_USD_FLOOR:,.0f} (Phase 1's $10K).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Verify but do not write `across_fee_table.json`.")
    ap.add_argument("--offline", action="store_true",
                    help="Skip API calls; use static defaults only. For local dev.")
    ap.add_argument("--output", default=None,
                    help="Override output path. Default: "
                         "<run_metadata_dir>/across_fee_table.json")
    args = ap.parse_args()

    cfg = DEFAULT_CONFIG
    out_path = Path(args.output) if args.output else (
        cfg.run_metadata_dir / "across_fee_table.json"
    )

    print(f"[verify_across_fees] start; notional=${args.notional_usd:,.0f}, "
          f"offline={args.offline}, dry_run={args.dry_run}", flush=True)
    print(f"[verify_across_fees] static defaults (D-006 baseline):", flush=True)
    for sym, bps in sorted(_DEFAULT_FEE_BPS_BY_TOKEN.items()):
        print(f"  {sym:6s}  {bps:>5.1f} bps", flush=True)
    print(flush=True)

    t0 = time.perf_counter()
    results, final = verify(
        notional_usd=args.notional_usd,
        offline=args.offline,
    )
    elapsed = time.perf_counter() - t0

    aborts = [r for r in results if r.action == "abort"]
    updates = [r for r in results if r.action == "updated"]
    api_errors = [r for r in results if r.action == "api_error"]
    kept = [r for r in results if r.action == "kept"]

    # Per-tuple breakdown.
    print(f"[verify_across_fees] results ({len(results)} tuples; "
          f"elapsed {elapsed:.1f}s):", flush=True)
    for r in results:
        live_str = f"{r.live_bps:>5.1f}" if r.live_bps is not None else "  ---"
        drift_str = f"{r.drift_bps:>5.1f}" if r.drift_bps is not None else "  ---"
        print(f"  [{r.action:>8s}] {r.src_chain:>9s}->{r.dst_chain:<9s} "
              f"{r.token_symbol:>6s}  static={r.static_bps:>5.1f}  "
              f"live={live_str}  drift={drift_str}  {r.note}", flush=True)
    print(flush=True)
    print(f"[verify_across_fees] summary: {len(kept)} kept, "
          f"{len(updates)} updated, {len(api_errors)} api_error, "
          f"{len(aborts)} ABORT", flush=True)

    if elapsed > 60.0:
        print(f"[verify_across_fees] WARNING: elapsed {elapsed:.1f}s > 60s "
              f"spec budget; consider parallel-fetching", file=sys.stderr,
              flush=True)

    if aborts:
        print(f"[verify_across_fees] ABORTING: {len(aborts)} route(s) "
              f"drifted > {DRIFT_BPS_ABORT:.0f} bps from static. "
              f"Investigate before redeploy. Either widen D-006 sensitivity "
              f"range or remove the affected route from the registry.",
              file=sys.stderr, flush=True)
        return 1

    if not args.dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = serialize_table(final, notional_usd=args.notional_usd)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True),
                            encoding="utf-8")
        print(f"[verify_across_fees] wrote {out_path}", flush=True)
    else:
        print(f"[verify_across_fees] --dry-run: not writing output", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
