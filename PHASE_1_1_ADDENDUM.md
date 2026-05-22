# Phase 1.1 Architecture Addendum — Pool Set Enumeration

**Status:** accepted, implemented, run executed.
**Spec section affected:** §Phase 1.1 ("Frozen monitored pool set on Base").
**Authored:** 2026-05-06, after Phase 1.1 acceptance run.

This addendum records a substantive pivot in how the Phase 1.1 monitored pool set
is built. The spec's *what* (a frozen set of UniV3 + Aerodrome pools above per-protocol
TVL floors) is unchanged. The *how* changed materially during implementation when
the originally-spec'd approach hit walls that made it untenable.

## Original architecture (spec)

1. Scan Uniswap V3 + Aerodrome v1 factories on Base for every `PoolCreated` event
   from factory deploy block to head, paginated via `eth_getLogs`.
2. For each decoded event: read token metadata on-chain (`symbol`, `decimals`).
3. Fetch USD prices for every unique token via CoinGecko `/simple/token_price`.
4. For each pool: read both token balances on-chain, compute USD TVL.
5. Filter by floor, write `monitored_pools.json`.

## What broke

| Wall | Detail |
|---|---|
| **Event volume** | UniV3 factory on Base has emitted **1,864,284** `PoolCreated` events as of block 45,571,185 (mostly mid-2024 meme-coin frenzy spam). The full historical scan took ~1 hour against an Alchemy free-tier endpoint. |
| **Process fragility** | The first run died at 63% when the terminal Claude Code session was closed mid-scan. Even after adding JSONL checkpointing for resumable scans, the scan still represented an hour of network I/O per acceptance run. |
| **CoinGecko free tier** | Querying `/simple/token_price` for 500k+ unique tokens at the unauthenticated rate limit projected to **~8 hours minimum**, and in practice yielded **<2% price coverage** before the IP got soft-banned by the upstream WAF. The split-and-retry-on-400 logic was making the rate-limit problem worse, not better. |
| **University network** | TLS connections to `api.coingecko.com`, `yields.llama.fi`, and `api.thegraph.com` were all reset at the SNI level by the campus firewall. General internet (google, github) worked fine. Path-blocked at the network edge, not at the API. |
| **`balanceOf` cost** | Even if prices had worked, the post-decode loop calls `_get_balance` twice per surviving pool. With ~1.8M pools surviving the lenient pre-filter, that's ~3.7M sequential RPC calls — projected **~100 hours** at 10 calls/sec without multicall3 batching. |

These weren't independent issues; they compounded. Solving any one in isolation
left the others as walls.

## New architecture

```
┌─────────────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│ DefiLlama /pools    │ ──> │ filter chain+project │ ──> │ for each entry:  │
│ (single ~13 MB GET) │     │ + tvlUsd >= floor    │     │   factory.getPool│
└─────────────────────┘     └──────────────────────┘     │   _get_token_info│
                                                          │   build PoolInfo │
                                                          └──────────────────┘
                                                                  │
                                                                  v
                                                          monitored_pools.json
```

1. **`DefiLlamaTvlSource`** — one unauthenticated GET to `https://yields.llama.fi/pools`
   returns ~13 MB of pool entries across every chain and protocol DefiLlama indexes.
   Filter to `chain="Base"`, target project, and `tvlUsd >= floor`.
2. For each surviving entry, derive the on-chain pool address by calling the appropriate
   factory's `getPool(...)` with parameters parsed from DefiLlama's `poolMeta`:
   - **UniV3:** `getPool(token0, token1, fee_uint24)` — fee parsed from `'0.3%'` etc.
   - **Aerodrome v1:** `getPool(token0, token1, stable_bool)` — DefiLlama doesn't
     disambiguate stable vs volatile, so we query both. If both pools exist, pick
     the one with the larger token0 `balanceOf` (the active pool carrying DefiLlama's
     reported TVL; the other is dormant or empty).
   - **Aerodrome Slipstream:** `getPool(token0, token1, tickSpacing)` — tickSpacing
     parsed from `'CL{n} - {fee%}%'`.
3. Enrich each pool with on-chain `symbol()`/`decimals()` (cached) and DefiLlama's
   `tvlUsd`, build `PoolInfo`, write `PoolSet`.

**No factory event scan. No CoinGecko. No per-pool `balanceOf` loop.**

## Scope additions

| Additional protocol | Spec status | TVL floor | Rationale |
|---|---|---|---|
| **Aerodrome Slipstream** | Not in original spec (post-dated). | $1,000,000 | Slipstream is Aerodrome's UniV3-style concentrated-liquidity AMM. As of acceptance run it represents 27 pools and ~$147M of monitored TVL on Base — comparable to UniV3's $208M. Excluding it would create a coverage gap that biases later phases toward UniV3-only liquidity. Uses the UniV3 floor ($1M) since it's structurally concentrated liquidity, not classic AMM. |

## What we trade off vs. the spec'd architecture

| Dimension | Spec'd (event-scan + CoinGecko) | New (DefiLlama + getPool) |
|---|---|---|
| Wall time | ~100+ hours (with sequential balanceOf) or ~1–2 hours (with multicall3) | **~33 seconds** (51 pools) / **~42 seconds** (78 with Slipstream) |
| Third-party deps | CoinGecko + Alchemy | DefiLlama + Alchemy |
| TVL precision | On-chain reserves × point-in-time CoinGecko prices | DefiLlama's reported `tvlUsd` (their own reserve reads + price feeds, refreshed regularly) |
| Failure modes | Multi-step pipeline, many failure points | Two HTTP requests + ~180 RPC calls; either works or doesn't |
| Pool address derivation | From `PoolCreated` event log | From live `factory.getPool` call (canonical) |

DefiLlama becomes a trusted third party for TVL data. This is a substantive change
from the spec's "separate Alchemy budget, no LLM in runtime pipeline" stance —
DefiLlama is none of those things, but it IS a third-party dependency. The
mitigations:

1. **Read-only, one-shot.** Phase 1.1 enumeration is a single bulk GET at run
   start. Once `monitored_pools.json` is frozen, DefiLlama is never queried again
   during the 30-day Phase 1 run. No runtime dependency.
2. **Verifiable.** Every DefiLlama pool entry is cross-checked against the
   on-chain factory via `getPool(...)`. If DefiLlama claims a pool exists that
   doesn't actually exist on-chain, we drop it (logged as `no on-chain pool`).
3. **Reproducible.** A second run on the same head block would produce the same
   pool addresses. The `tvlUsd` numbers would differ by however much DefiLlama's
   underlying data has refreshed — but the pool *set* is deterministic.

## Spec invariants honored

| Invariant | Status |
|---|---|
| Read-only against Layer 3 | ✓ unchanged |
| Separate Alchemy budget | ✓ — DefiLlama is its own thing, not on the Alchemy budget |
| No LLM in runtime pipeline | ✓ unchanged |
| No adaptive rules mid-run | ✓ — pool set frozen at run start |
| No on-chain writes | ✓ unchanged |
| Loud logging | ✓ — every getPool call logs success/failure with symbol+address |
| §Phase 1.1 frozen at run start | ✓ — `monitored_pools.json` write refuses to overwrite |

## Acceptance run result (2026-05-06, head_block 45,658,483)

```
total pools: 78
  uniswap_v3:           21 pools, $207,888,936 TVL
  aerodrome_volatile:   26 pools, $ 88,464,299 TVL
  aerodrome_stable:      4 pools, $ 25,976,862 TVL
  aerodrome_slipstream: 27 pools, $146,636,050 TVL

Total monitored TVL: $468,966,147
Wall time:           42 seconds
```

## Code that this retired

The following modules and helpers were removed from `pool_set.py` once the new
path was validated:

- `class PriceSource(Protocol)` and `class CoinGeckoPriceSource`
- `_decode_pool_created_uniswap_v3`, `_decode_pool_created_aerodrome`
- `_scan_factory_events` and `_checkpoint_paths` (JSONL checkpoint resume logic)
- `_compute_tvl_usd` (per-pool TVL math from reserves × prices)
- All `COINGECKO_*` constants and `GETLOGS_MAX_RANGE`
- The 370 MB `data/run_metadata/scan_checkpoints/univ3_events.{jsonl,meta.json}`
  artifact from the abandoned full-history scan

The corresponding tests (`test_checkpoint.py`, several CoinGecko tests in
`test_pool_set.py`, the `_compute_tvl_usd` tests) were removed in the same pass.

`_get_token_info`, `_get_balance`, `ERC20_ABI`, and the factory ABIs are retained
— they're used by the new path (`getPool` calls + Aerodrome v1 stable/volatile
disambiguation via `balanceOf`).

## Why this addendum exists

A future reader of `LAYER3_TRADING_EXPERIMENT.md` reading the spec's "scan
factory events for `PoolCreated`" instruction would not find that code in
`pool_set.py`. This document explains why the implementation diverged, what
guardrails preserve the spec's intent, and what the resulting monitored set
looks like.

If Phase 1.1 is ever re-run and DefiLlama is unavailable, the fallback is the
on-chain anchor-pegged approach described as "Path C" during the implementation
discussion: hardcoded WETH/USDC/USDT/DAI/cbETH/wstETH prices, multicall3-batched
`balanceOf`, no third-party API. That code does not exist yet but is documented
here as the contingency.
