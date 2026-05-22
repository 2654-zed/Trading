# Phase 2 sub-phase 2.8 — Railway deployment image for the cross-chain
# arb research experiment (EXP-002). Inherits the Phase 1.6 architecture
# (sibling service to L3's stellar-embrace) with Phase 2 additions:
#  - multi-chain enumeration (Base + Arbitrum + Optimism)
#  - Across fee verification step at entrypoint
#  - CrossChainDetector wired into detect_dry_run
#
# Runtime expectations:
#   - Volume mounted at /app/data (logs, rollups, run_metadata, L3 sync copy)
#   - Env vars (Phase 1):  BASE_RPC_URL, BASE_WSS_URL, L3_DUMP_BASE_URL, LAYER3_ADMIN_TOKEN
#   - Env vars (Phase 2):  ARB_RPC_URL, ARB_WSS_URL, OP_RPC_URL, OP_WSS_URL
#   - PORT env var unused (no inbound HTTP)
#   - SIGTERM-graceful shutdown (drains logger queue, closes DB)
#   - --minutes timer is now backed by a periodic asyncio watchdog (sub-phase 2.2)
#     so it honors the deadline even if WS subscriptions stall

FROM python:3.13-slim

# Avoid prompts, keep image small, ensure stdout is unbuffered for Railway logs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install only the package's runtime requirements. Tests + matplotlib + scipy
# live in dev requirements; not needed at deploy time. Analysis scripts that
# DO need matplotlib are run on-demand, not in the main process — they can
# pull deps locally when used.
COPY layer3_trading_exp/requirements.txt /app/layer3_trading_exp/requirements.txt
RUN pip install -r /app/layer3_trading_exp/requirements.txt

# Copy the package. .dockerignore excludes tests + local data + dotenv.
COPY layer3_trading_exp /app/layer3_trading_exp

# Note: no Dockerfile `VOLUME` directive — Railway's builder rejects them.
# The persistent volume is attached via Railway's volume config (see railway.json
# / `railway volume add` setup); the mount point /app/data is provisioned by
# Railway at container start, not declared here.

# Self-bootstrapping entrypoint: on first launch with an empty volume, runs
# enumerate_live + sync_l3_db --once before exec-ing detect_dry_run. On
# subsequent launches the sentinel files exist and bootstrap is a no-op.
COPY entrypoint.sh /entrypoint.sh
# Normalize line endings (handles CRLF from Windows edits) + make executable.
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Phase 2 EXP-002 default: 1-day run (1440 min).
# Was 10080 (7d) per D-014; user revised to 1440 (1d) on 2026-05-21 redeploy
# (post sub-phase 2.8.3 RCA fix) to bound the next observation window. The
# WS-stall reconnect failures across the 2026-05-18 → 2026-05-21 window left
# Phase 2 with limited usable data; a tighter window with the architectural
# fix in place is the right cadence for the next checkpoint.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["--minutes", "1440"]
