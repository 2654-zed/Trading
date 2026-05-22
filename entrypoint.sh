#!/bin/bash
# Phase 1.6 — self-bootstrapping container entrypoint.
#
# On first launch (empty volume), runs the one-time setup:
#   1. enumerate_live  → writes monitored_pools.json (frozen pool set)
#   2. sync_l3_db --once → seeds the local L3 SQLite copy
# On subsequent launches, both sentinel files exist and the bootstrap is skipped;
# the container goes straight into detect_dry_run.
#
# This satisfies spec invariant #2 ("frozen at run start") — enumeration happens
# at first container launch and never repeats while monitored_pools.json exists.
# A genuine new run requires `rm` on the sentinel file from `railway ssh`.

set -euo pipefail

DATA="${DATA_DIR:-/app/data}"
META_DIR="${RUN_METADATA_DIR:-$DATA/run_metadata}"
POOLS_JSON="$META_DIR/monitored_pools.json"
L3_DB="${L3_DB_PATH:-$META_DIR/l3_sync.db}"

mkdir -p "$META_DIR" "${LOG_DIR:-$DATA/logs}" "${ROLLUP_DIR:-$DATA/rollups}"

PHASE_2_RPCS_SET=""
if [ -n "${ARB_RPC_URL:-}${ARBITRUM_RPC_URL:-}${OP_RPC_URL:-}${OPTIMISM_RPC_URL:-}" ]; then
    PHASE_2_RPCS_SET=1
fi

# Phase 2 sub-phase 2.8 (D-014): on a service that previously ran Phase 1,
# the volume carries a Base-only monitored_pools.json. Detect that vintage
# and back it up so the multi-chain enumerator can produce a fresh file.
# We check the bag-level `chain` field (Phase 1 wrote "base"; Phase 2
# multi-chain writes "all" or a single non-base chain on --chain X).
if [ -f "$POOLS_JSON" ] && [ -n "$PHASE_2_RPCS_SET" ]; then
    EXISTING_CHAIN=$(python -c "import json; print(json.load(open('$POOLS_JSON')).get('chain', '?'))")
    if [ "$EXISTING_CHAIN" = "base" ]; then
        BACKUP="$POOLS_JSON.phase1.$(date +%Y%m%d-%H%M%S).bak"
        echo "[bootstrap] $POOLS_JSON is Phase 1 vintage (chain=base) but Phase 2 RPCs set."
        echo "[bootstrap]   Backing up to $BACKUP and re-enumerating multi-chain."
        mv "$POOLS_JSON" "$BACKUP"
    fi
fi

if [ ! -f "$POOLS_JSON" ]; then
    # Phase 2 sub-phase 2.1 (D-009): multi-chain enumerator is the new default.
    # For single-chain (Base-only) runs the old `enumerate_live` path is still
    # available; the multi-chain enumerator auto-skips chains with no RPC URL.
    if [ -n "$PHASE_2_RPCS_SET" ]; then
        echo "[bootstrap] $POOLS_JSON missing + Arb/OP RPCs configured → "
        echo "[bootstrap]   running enumerate_pools (multi-chain)"
        python -m layer3_trading_exp.scripts.enumerate_pools
    else
        echo "[bootstrap] $POOLS_JSON missing + no Arb/OP RPCs → "
        echo "[bootstrap]   running enumerate_live (Base only, Phase 1 path)"
        python -m layer3_trading_exp.scripts.enumerate_live
    fi
else
    echo "[bootstrap] $POOLS_JSON exists; skipping enumeration (spec invariant #2)"
fi

if [ -n "${L3_DUMP_BASE_URL:-}" ] && [ -n "${LAYER3_ADMIN_TOKEN:-}" ]; then
    if [ ! -f "$L3_DB" ]; then
        echo "[bootstrap] $L3_DB missing → running sync_l3_db --once"
        python -m layer3_trading_exp.scripts.sync_l3_db --once
    else
        echo "[bootstrap] $L3_DB exists; main process will resume incremental sync"
    fi
else
    echo "[bootstrap] L3_DUMP_BASE_URL or LAYER3_ADMIN_TOKEN unset; sync disabled"
fi

# Phase 2 sub-phase 2.4 (D-009): Across fee verification hard gate. If any
# route drifted > 20 bps from the static D-006 baseline, this exits non-zero
# and `set -e` halts the deploy. Verifier writes <META_DIR>/across_fee_table.json
# on success; the detector loads it at startup. Skip when no Phase 2 chains
# are configured (Base-only legacy runs don't need cross-chain bridge model).
if [ -n "${ARB_RPC_URL:-}${ARBITRUM_RPC_URL:-}${OP_RPC_URL:-}${OPTIMISM_RPC_URL:-}" ]; then
    if [ ! -f "$META_DIR/across_fee_table.json" ]; then
        echo "[bootstrap] across_fee_table.json missing → running verify_across_fees"
        python -m layer3_trading_exp.scripts.verify_across_fees
    else
        echo "[bootstrap] across_fee_table.json exists; skipping re-verification "
        echo "[bootstrap]   (I-13: fees frozen for the run). Delete to force re-verify."
    fi
else
    echo "[bootstrap] no Arb/OP RPCs → skipping Across fee verification (Base-only)"
fi

echo "[bootstrap] starting detect_dry_run (default 7-day run)"
exec python -m layer3_trading_exp.scripts.detect_dry_run "$@"
