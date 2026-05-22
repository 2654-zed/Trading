#!/usr/bin/env bash
# Phase 1.6 — pull JSONL logs, rollup CSVs, and analysis reports from the
# Railway-deployed trading-exp service to a local directory.
#
# Usage:
#   ./scripts/fetch_run_artifacts.sh                    # pulls into ./run_artifacts/
#   ./scripts/fetch_run_artifacts.sh /path/to/dest      # custom destination
#
# Requires:
#   - Railway CLI installed and logged in (`railway login`)
#   - Linked to the `blockchain` project with `layer3-trading-exp` service
#   - The service is running (or at minimum, the volume is reachable via SSH)

set -euo pipefail

DEST="${1:-./run_artifacts}"
mkdir -p "$DEST/logs" "$DEST/rollups" "$DEST/analysis"

echo "Pulling logs..."
railway ssh "cd /app/data && tar -czf - logs rollups analysis run_metadata 2>/dev/null" \
    | tar -xzf - -C "$DEST/"

echo "Done. Artifacts in $DEST/"
echo
echo "Summary:"
echo "  logs:     $(ls -1 "$DEST/logs/" 2>/dev/null | wc -l) JSONL files"
echo "  rollups:  $(ls -1 "$DEST/rollups/" 2>/dev/null | wc -l) CSV files"
echo "  analysis: $(ls -1 "$DEST/analysis/" 2>/dev/null | wc -l) report files"
echo
echo "Run the Phase 1.5 analysis locally with:"
echo "  python -m layer3_trading_exp.scripts.run_analysis \\"
echo "    --start <YYYY-MM-DD> --end <YYYY-MM-DD>"
echo "  (override LOG_DIR=$DEST/logs / ROLLUP_DIR=$DEST/rollups via env if needed)"
