"""Centralized configuration for the Layer 3 trading research experiment (Phase 1).

Values here are the frozen run configuration per spec §Invariants #2. Changing a value
mid-run corrupts the experimental record.

Path defaults are local-development-friendly. In Phase 1.6 Railway deployment, all
filesystem paths and the L3 sync endpoint are overridden via environment variables —
see DOCKERFILE / railway.json / PHASE_1_6_ADDENDUM.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parent
L3_DB_DEFAULT = Path(r"C:\Users\jason\Desktop\ai lang\surveillance\data\surveillance.db")


def _load_env_file(path: Path = EXPERIMENT_ROOT / ".env") -> None:
    """Lightweight .env loader. Stdlib-only; does not overwrite existing env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file()


def _env_path(key: str, default: Path) -> Path:
    """Return env var as Path if set, else the default."""
    v = os.environ.get(key)
    return Path(v) if v else default


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    chain: str = "base"

    # All filesystem paths env-overridable so the same config works locally and
    # in the Railway container. On Railway, DATA_DIR=/app/data and the rest
    # default off that root.
    data_dir: Path = field(default_factory=lambda: _env_path(
        "DATA_DIR", EXPERIMENT_ROOT / "data"))

    @property
    def log_dir(self) -> Path:
        return _env_path("LOG_DIR", self.data_dir / "logs")

    @property
    def rollup_dir(self) -> Path:
        return _env_path("ROLLUP_DIR", self.data_dir / "rollups")

    @property
    def run_metadata_dir(self) -> Path:
        return _env_path("RUN_METADATA_DIR", self.data_dir / "run_metadata")

    @property
    def kill_switch_path(self) -> Path:
        return _env_path("KILL_SWITCH_PATH", self.data_dir / "KILL_SWITCH")

    # L3 DB path resolution at instance-creation time:
    #   1. Explicit L3_DB_PATH env var (most specific)
    #   2. {DATA_DIR}/run_metadata/l3_sync.db when DATA_DIR is set (container mode)
    #   3. Hardcoded Windows dev-machine path (local mode)
    # Phase 1.6 first-deploy bug: setting DATA_DIR alone didn't propagate
    # into l3_db_path because its old default was a constant. The default
    # factory now reads the env at construction so DATA_DIR is enough.
    l3_db_path: Path = field(default_factory=lambda: (
        Path(os.environ["L3_DB_PATH"]) if "L3_DB_PATH" in os.environ
        else (Path(os.environ["DATA_DIR"]) / "run_metadata" / "l3_sync.db"
              if "DATA_DIR" in os.environ
              else L3_DB_DEFAULT)
    ))

    # L3 /dump endpoint for incremental sync. Empty string disables sync (dev mode
    # against a local-copy surveillance.db). Set via env in Railway:
    #   L3_DUMP_BASE_URL=http://stellar-embrace.railway.internal:$PORT
    #   LAYER3_ADMIN_TOKEN=<set via railway variables>
    l3_dump_base_url: str = field(default_factory=lambda: os.environ.get("L3_DUMP_BASE_URL", ""))
    layer3_admin_token: str = field(default_factory=lambda: os.environ.get("LAYER3_ADMIN_TOKEN", ""))
    sync_interval_seconds: int = field(default_factory=lambda: _env_int("SYNC_INTERVAL_SECONDS", 300))

    freshness_threshold_seconds: int = 30 * 60

    gross_margin_bps_floor: int = 30
    min_depth_notional_usd: float = 10_000.0
    # Floors halved from spec defaults ($1M / $500K) after Phase 1.2 dry-run
    # showed degenerate variety (one persistent arb across all 60 minutes).
    # Wider candidate set increases the chance of catching transient
    # price-drift opportunities. Recorded in PHASE_1_2_ADDENDUM.md.
    uniswap_v3_tvl_floor_usd: float = field(default_factory=lambda: _env_float(
        "UNISWAP_V3_TVL_FLOOR_USD", 500_000.0))
    aerodrome_tvl_floor_usd: float = field(default_factory=lambda: _env_float(
        "AERODROME_TVL_FLOOR_USD", 250_000.0))

    base_rpc_url: str = field(default_factory=lambda: os.environ.get("BASE_RPC_URL", ""))
    base_wss_url: str = field(default_factory=lambda: os.environ.get("BASE_WSS_URL", ""))

    # Phase 2 (sub-phase 2.1, D-009): cross-chain RPC + WS URLs. Accept both
    # ARB_* and ARBITRUM_* / OP_* and OPTIMISM_* spellings to keep .env files
    # forgiving. Empty string disables that chain in scripts that consult
    # these (e.g. enumerate_pools.py raises SystemExit when missing).
    arbitrum_rpc_url: str = field(default_factory=lambda: (
        os.environ.get("ARB_RPC_URL") or os.environ.get("ARBITRUM_RPC_URL") or ""
    ))
    arbitrum_wss_url: str = field(default_factory=lambda: (
        os.environ.get("ARB_WSS_URL") or os.environ.get("ARBITRUM_WSS_URL") or ""
    ))
    optimism_rpc_url: str = field(default_factory=lambda: (
        os.environ.get("OP_RPC_URL") or os.environ.get("OPTIMISM_RPC_URL") or ""
    ))
    optimism_wss_url: str = field(default_factory=lambda: (
        os.environ.get("OP_WSS_URL") or os.environ.get("OPTIMISM_WSS_URL") or ""
    ))


DEFAULT = Config()


def ensure_dirs(cfg: Config = DEFAULT) -> None:
    for d in (cfg.data_dir, cfg.log_dir, cfg.rollup_dir, cfg.run_metadata_dir):
        d.mkdir(parents=True, exist_ok=True)
