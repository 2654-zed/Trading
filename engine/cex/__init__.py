"""Centralized-exchange Level-2 order-book capture (Game 3: order-book
microstructure / "price pressure").

Records the RAW L2 stream losslessly from one or more exchanges concurrently —
initial snapshot + every diff/update, each stamped with a local receive time
and a monotonic sequence. Book RECONSTRUCTION + feature computation (imbalance,
depth, "price pressure") happen OFFLINE from the recording, so a live
reconstruction bug can never silently corrupt the captured data.

Same forensic discipline as the mempool pipeline: append-only, lossless,
recv_ts stamped before decode, loud failure, structured health log, NTP-aware
clock stamp, single-instance lock. Public market data only — no API keys.
"""
