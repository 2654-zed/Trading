# FAILURE_LOG.md

Append-only log of failures, surprises, and incidents.
Newest entries at top. Closing a failure → add `STATUS: RESOLVED <date>`
to the same entry rather than deleting.

---

## 2026-05-22

### Failure: 2.8.3 RCA fix had a gap — only WSStallError escalated, ConnectionClosedError silently retried forever

* **Expected behavior**: Per D-016 (sub-phase 2.8.3 RCA), after K consecutive failures with no successful tick, PoolMonitor escalates by raising; ChainMonitor catches, exits the asynccontextmanager (closes WS), backoffs, re-enters (opens fresh WS). Two-level recovery should handle any WS-level failure.
* **Actual behavior**: User reported `layer3-trading-exp` shows "offline" in Railway dashboard at T+~29h after the 2026-05-21 redeploy. CLI investigation showed:
  - Container actually running (L3 sync cycles completing normally)
  - All 3 chain monitors stuck in a reconnect loop with **2,750+ attempts each** (×30s backoff = ~23 hours of being stuck)
  - The exception every chain hit: `ConnectionClosedError: no close frame received or sent` (from web3.py's WebSocketProvider — TCP socket severed without proper close handshake)
  - SSH to container fails with "Could not establish SSH connection to application" (Railway routing-level issue separate from the application stall)
* **Cause**: My 2.8.3 escalation logic was **too narrow**. The code only counted `WSStallError` toward `consecutive_stalls`; `ConnectionClosedError` and other web3.py-raised exceptions hit a SEPARATE `except Exception` branch that just logged + slept + retried forever on the same dead WS. The visibility patch from 2.8.2 made the retries visible (we can see "reconnect attempt 2753" in the buffer) but the underlying recovery still didn't fire because `ConnectionClosedError` was never categorized as "fatal enough to recycle the WS."
* **Timeline reconstructable from logs**:
  - 2026-05-21 ~22:30 UTC: User redeploys after fixing Railway dashboard sleep-when-idle setting
  - Container runs cleanly for ~6h (block 46,284,021 → 46,294,988 on Base = ~5,500 blocks)
  - All 3 chains hit `ConnectionClosedError` ~roughly simultaneously (likely an Alchemy-side TCP RST event)
  - Each chain has been retrying ~30s/attempt for ~23h with zero recovery
* **Impact**: ~23h of detection lost on a 7-day run (current container is still on `--minutes 10080` due to the unresolved dashboard custom-start-command override). Run 1's 12 unique cross-chain opps from 2026-05-17 remain the only useful Phase 2 data.
* **Fix**: Phase 2 sub-phase 2.8.4 patch — `pool_monitor.py` unifies the failure-counter logic. Single `except Exception` clause handles BOTH `WSStallError` AND non-stall exceptions (`ConnectionClosedError`, `OSError`, transport library exceptions, etc). Counter increments on any exception; resets on every successful tick; escalates after `max_consecutive_stalls` (kwarg name preserved for back-compat). The `WSStallError` and `Exception` branches are merged; the print message reads "subscription-level retry K/N after {exc_type}: {msg}" for either kind. Unit test added (`test_pool_monitor_escalates_on_non_stall_exception_too`) using a fake `_AlwaysRaisingTransport` that raises a `ConnectionClosedError`-equivalent on every subscribe attempt; confirms escalation fires at K=2 attempts.
* **Lessons + open questions**:
  1. **My RCA was correct architecturally but the fix had a narrow trigger.** Any unified architectural recovery mechanism should be triggered by ALL exception classes, not a specific one. Pattern: "if it goes wrong K times in a row with no success in between, escalate" — independent of WHY.
  2. **The 23h stuck-retrying behavior cost almost a full day of the run window.** Could be mitigated further by reducing the per-attempt sleep cap (currently 30s) — but the architectural fix is the right primary defense.
  3. **`ConnectionClosedError` happens on a TCP RST.** This is different from the May 18 stall (totally silent WS) and the May 21 stall (silent on TCP). Three distinct stall classes observed so far; the unified handling now covers all three.
* **STATUS**: RESOLVED-IN-CODE 2026-05-22. Tests passing (310/310). Code change is small; awaiting redeploy authorization. Same operational caveat as D-016 — current container needs to be replaced for the fix to take effect; the dashboard's `--minutes 10080` override still needs the user's manual update before the 1-day window can stick.

---

## 2026-05-21

### Failure: EXP-002 chain monitors silently stalled — no log output, no recovery

* **Expected behavior**: After the Railway outage recovery (FAILURE_LOG 2026-05-20), the trading-exp container should resume detection cleanly. The 2026-05-18 WS-stall detector should catch WS-level stalls and raise `WSStallError` → outer except → reconnect.
* **Actual behavior**: At T+17h health check, the Railway log buffer (500 lines) contained ZERO block-tick lines from any chain. Only L3 sync cycles and one daily-rollup line. Chain monitor tasks were silent for an extended window (estimated several hours; precise start unknown because nothing was printed). Meanwhile: PID 1 (`python -m layer3_trading_exp.scripts.detect_dry_run`) was alive; L3 sync ticked every ~5 min with 0 errors and 82K rows/cycle; daily rollup wrote `2026-05-20.csv` on schedule; fresh Alchemy WS connection from inside the container worked instantly for all 3 chains. So Alchemy was reachable, the asyncio event loop was running, but the chain monitor tasks were stuck somewhere with no log output.
* **Cause (HYPOTHESIZED)**: Most likely the Railway outage (2026-05-19 22:29 UTC) killed all 3 chains' WS connections. Our 2026-05-18 WS-stall detector raised `WSStallError`. Outer except clause in `PoolMonitor.run` slept for backoff, then re-entered `subscribe_new_heads()`. The reconnect may have failed (DNS / socket errors during the outage window, then perhaps some other error after recovery). Each reconnect attempt failed silently because the outer except clause did NOT print anything — it just incremented `attempt`, called `asyncio.sleep(delay)`, and looped. After enough attempts the backoff reaches 30s (max) and stays there. Chain monitors are then in an infinite silent-reconnect loop. Why each reconnect fails AFTER Alchemy is verifiably reachable is unknown — could be a stale event loop state, a stuck `WebSocketProvider` __aenter__, or a different exception path I haven't anticipated.
* **Impact**: ~3.5 days (~85h) of total detection downtime spanning Run 2 (post-2026-05-18 redeploy). However: zero opportunities had been logged since 2026-05-17 23:49 UTC (the MSUSD/USDC arb closed at that point), so the detector was producing zero JSONL records throughout Run 2 even when monitors WERE ticking. Total empirical loss: limited (we lost the ability to detect transient opportunities that may have appeared in the window, but couldn't observe any because monitors were stuck). H1' projection for the run is now based on Run 1's 7h window only (12 unique cross-chain opps, ~41/day extrapolated — below 50/day threshold, above 10/day falsification floor).
* **Fix**: Phase 2 sub-phase 2.8.2 patch — `pool_monitor.py` outer except clause now prints exception type, message, backoff delay, and last_block to stderr on every reconnect attempt. Future stalls will be visible in the Railway log buffer:
   ```python
   except Exception as exc:
       ...
       print(f"[pool_monitor:{self._chain_label}] reconnect attempt {attempt+1} "
             f"after {last_error}; sleeping {delay}s (last_block={last_block})",
             file=_sys.stderr, flush=True)
       ...
   ```
* **Open question for future runs**: WHY each reconnect attempt was failing is still unknown. The reconnect-logging patch is for VISIBILITY of failures; it does NOT directly fix whatever was causing reconnects to fail. The patch is necessary for diagnosis when the next stall happens. May need follow-up work:
   1. A per-chain liveness watchdog (separate from time-budget watchdog) that kills the whole container if no block ticks from any chain in >N minutes → restartPolicyType: ON_FAILURE rescues
   2. Bounded retry count with hard exit (so the run terminates rather than stays stuck)
   3. Deeper inspection of `WebSocketProvider`'s internal state on reconnect
* **STATUS**: RESOLVED 2026-05-21 — root cause identified + architecturally fixed:

  **Root cause (sub-phase 2.8.3 RCA)**: The WS connection was opened ONCE per ChainMonitor lifetime via `async with self._transport_factory(...) as transport:` and the subscription was retried on the SAME socket forever. When the underlying TCP socket went silent (Railway/GCP outage broke the connection at the network layer without delivering a FIN/RST), every `subscribe_new_heads()` retry kept getting silence → `WSStallError` → silent sleep → retry on the same dead socket → forever. Re-subscribing on a dead socket never recovers; you have to close + re-open the TCP connection.

  **Architectural fix (sub-phase 2.8.3)**: Two-level recovery.
   1. `PoolMonitor` gains a `max_consecutive_stalls` parameter (default 3). After N consecutive `WSStallError`s with no intervening successful tick, PoolMonitor RAISES instead of swallowing. The counter resets on every successful head arrival.
   2. `ChainMonitor.run()` no longer opens the WS once-and-forever. It wraps the `async with self._transport_factory(...)` in a while-True retry loop with its own backoff. When PoolMonitor escalates, ChainMonitor catches, the async-with cleanly closes the WS at the TCP layer, the backoff sleeps, then a fresh async-with opens a brand-new TCP connection.
   3. Both layers log every transition to stderr so reconnect cascades are visible in `railway logs`.

  Unit tests added (`tests/test_pool_monitor.py` + `tests/test_chain_monitor.py`) covering: escalation after N consecutive stalls, counter reset on successful tick, default `max_consecutive_stalls=3`, validation of input bounds, ChainMonitor re-opens WS via factory when PoolMonitor escalates. **309/309 test suite passing.**

  Code not yet deployed at the time of writing this entry; current Railway container has the 2.8.2 visibility patch but NOT the 2.8.3 architectural fix. Operator decision pending on redeploy timing (would reset the time-budget watchdog clock).

---

## 2026-05-20

### Failure: Railway-wide outage (GCP block) + stellar-embrace HTTP server not auto-recovering

* **Expected behavior**: Railway hosts both `layer3-trading-exp` (our detector) and `stellar-embrace` (L3 production). Outages are rare; on recovery, all services come back up cleanly via `restartPolicyType: ON_FAILURE`.
* **Actual behavior**: 2026-05-19 ~22:29 UTC — Railway-wide outage caused by Google Cloud blocking Railway's account (per Railway status page). Outage resolved by Railway/GCP within ~6h. Our `layer3-trading-exp` container auto-restarted cleanly and resumed detection on all 3 chains (WS-stall detector from 2026-05-18 entry stayed armed across the restart). However, the sibling `stellar-embrace` service came back up with its **HTTP server NOT bound to port 8080**; only the surveillance subprocesses (monitor / selector / alert_engine) were running. Our `l3_sync` cycles failed for ~67 minutes with `Temporary failure in name resolution` (during outage) → `urlopen error timed out` (post-outage; DNS resolved but no listener).
* **Cause**: Railway/GCP outage was external (resolved by Railway). Stellar-embrace's HTTP server not coming back was internal to that service's process manager — possibly the entrypoint subprocess sequencing didn't bring the HTTP server back after a process-routing crash loop (the `routing` subprocess crash-loops continuously because `ONEINCH_API_KEY` env var isn't set; this is pre-existing cosmetic noise but may have masked the HTTP server's startup failure).
* **Impact**: Our detection layer was UNAFFECTED (uses local L3 SQLite cache + monitored_pools.json on volume). L3 sync stalled from 2026-05-19 22:16 UTC to ~2026-05-20 03:00 UTC (~4h 44min gap). Per D-012 per-table thresholds: `contracts` + `deployers` (1h threshold) became stale during the gap; `bytecode_families` (6h) marginal; `trap_events` (24h) + `trust_amplification` (36h) stayed fresh throughout. H2 verdict for the affected window will show progressive `degraded_per_table` rates on `contracts`/`deployers`, which is the correct behavior per D-012 semantics.
* **Fix**: User restarted/redeployed `stellar-embrace`. Post-restart, HTTP server bound to port 8080 normally. Our `l3_sync` cycle picked up automatically on its next interval; first post-recovery cycle synced 82,697 rows with 0 errors. No code change required on our side. Lesson for future operational discipline: when L3 sync starts failing, FIRST check `/health` from inside our container; that distinguishes "L3 down" (`/health` times out / 502s) from "auth/token rotated" (`/health` 200 + `/dump` 403) from "L3 partial recovery" (`/health` 200 + `/dump` times out).
* **Diagnostic command** used to confirm recovery (worth keeping in toolbelt):
  ```python
  python -c "
  import urllib.request, urllib.parse, os
  params = {'token': os.environ['LAYER3_ADMIN_TOKEN'], 'table': 'contracts',
            'cursor_col': 'last_updated', 'offset': '0', 'limit': '1'}
  url = 'http://stellar-embrace.railway.internal:8080/dump?' + urllib.parse.urlencode(params)
  r = urllib.request.urlopen(url, timeout=10)
  print(r.status, r.read()[:100])"
  ```
* **STATUS**: RESOLVED 2026-05-20.

---

## 2026-05-18

### Failure: EXP-002 stalled at T+7h — all 3 chains' WS subscriptions silently degraded, no reconnect

* **Expected behavior**: EXP-002 runs for 7 days at the `--minutes 10080` deadline, producing JSONL records continuously across Base + Arb + OP. PoolMonitor's reconnect loop re-subscribes if the WS connection raises an exception.
* **Actual behavior**: Detector produced 13,967 JSONL records in the first ~7h (16:40 → 23:49 UTC 2026-05-17) — **including 1,238 cross-chain records, 12 unique opp keys**. Then all writes stopped. Container kept running. L3 sync continued cycling cleanly. Block ticks from Arb + OP went silent; Base WS got stuck delivering block `46,136,019` ~10x/s for ~23h (lag grew to 414s at sample time). NO exception ever raised by any transport layer. The PoolMonitor's existing reconnect loop only triggers on exception, so the stall went undetected.
* **Cause**: The PoolMonitor `async for head in self._transport.subscribe_new_heads()` loop assumes the iterator either yields a fresh head or raises. The Alchemy WS connection apparently entered a degraded state where it kept the connection open but stopped sending new messages (Arb/OP) OR resent the last message indefinitely (Base). Neither shape raises an exception — the async-for just sits there iterating, or returns the same stale head over and over. Different failure class than `FAILURE_LOG.md` 2026-05-16 (which was the `on_block`-gated deadline check) but same pattern: "system silently degrades while looking healthy". I-11 T-A (lag > 30s) had been firing for ~23h with no automated intervention since the trigger requires a human/agent sweep.
* **Impact**: ~23h of detection lost (May 17 23:49 → May 18 23:10ish UTC). Continued to consume Alchemy CUs on Base (multicalls fetching state for the stuck block ~10x/s). Cross-chain opp count for the run window is preserved (12 unique in the 7h that worked) but the 7-day H1' projection is undercut by the lost time.
* **Fix**: Phase 2 sub-phase 2.8.1 patch — `pool_monitor.py` extended with a per-chain WS stall detector:
   1. **Silence detector**: `asyncio.wait_for(__anext__(), timeout=stall_threshold)` raises `WSStallError("silent", ...)` when no head arrives within threshold.
   2. **Same-block-repeating detector**: tracks `last_new_block_at` monotonic timestamp. If `block_number == last_block` AND `now_mono - last_new_block_at > threshold`, raises `WSStallError("same_block_repeating", ...)`.
   3. Either raise propagates through the outer `except Exception` → existing reconnect loop fires → fresh `subscribe_new_heads()` → fresh WS connection.
   4. Default threshold: `max(20s, chain_block_time × 10)`. Configurable per `PoolMonitor` instance via `stall_threshold_seconds` kwarg.
   5. Unit tests in `tests/test_pool_monitor.py`: silence detection, same-block-repeating detection, normal advancement doesn't trigger, threshold override, validation.
* **Live regression**: `railway down --yes` + `railway up` 2026-05-18 23:10 UTC. New container booted with patch active; all 3 monitors ticking with healthy lag (Base ~0.5-0.8s, Arb sampled ~1.0s, OP ~0.2-0.3s). Cross-chain detector built 12 scan routes as before.
* **STATUS**: RESOLVED 2026-05-18. Run resumes; original 7-day deadline (`--minutes 10080`) was reset to a fresh ~7-day window by the redeploy. Expected new deadline ~2026-05-25 23:10 UTC.

---

## 2026-05-17

### Failure: First Phase 2 deploy enumerated zero Optimism pools (DefiLlama chain label mismatch)

* **Expected behavior**: `enumerate_pools --chain all` produces a `monitored_pools.json` covering all three chains (Base + Arbitrum + Optimism). Spec sub-phase 2.1 estimated ~95 Optimism pools.
* **Actual behavior**: First Phase 2.8 deploy enumerated 158 pools across only 2 chains (Base 124, Arbitrum 34, **Optimism 0**). Across fee verification succeeded for all 20 OP-touching route tuples, but the cross-chain detector saw 0 cross-chain scan routes because OP had no pools.
* **Cause**: `DEFILLAMA_CHAIN_BY_LABEL["optimism"]` was set to `"Optimism"` in sub-phase 2.1. DefiLlama's `/pools` endpoint actually labels Optimism as `"OP Mainnet"` — verified via in-container query of the live API. Every `tvl_source.get_pools_above_floor("Optimism", ...)` returned an empty list. Enumeration code "succeeded" because zero matches isn't an error; downstream the cross-chain detector silently produced 0 scan routes for the 3-chain triangle.
* **Impact**: First Phase 2.8 deploy taken down within ~5 minutes of detect_dry_run start. The Across fee table from this run is preserved on the volume (D-010 still valid). Filter pipeline and L3 sync paths were observed clean — Base intra-chain detection was already firing `+1 intra` per block (the persistent MSUSD/USDC arb appears to have reopened, reversing D-008's UNK-002 resolution; flagged for the run-end LOOP).
* **Fix**: One-line patch in `pool_set.py::DEFILLAMA_CHAIN_BY_LABEL` — `"optimism": "OP Mainnet"`. While inspecting, also corrected the Velodrome project labels (`velodrome-v2` for current Solidly-style pools, `velodrome-v3` for the Slipstream CL variant — DefiLlama no longer surfaces a `velodrome-v1` project). Updated `tests/test_pool_set.py::test_defillama_chain_mapping_uses_verified_labels` accordingly.
* **STATUS**: RESOLVED 2026-05-17. Re-deploy with patch + clean monitored_pools.json on the volume produced via `entrypoint.sh`'s "Phase 1 vintage → backup + re-enumerate" path.

### Side-note (not a failure, just an observation): MSUSD/USDC arb may have reopened
First Phase 2.8 deploy showed `+1 intra +0 cross` on EVERY Base block tick (a pattern matching Phase 1's first 1.85 hours when the persistent MSUSD/USDC arb was open). D-008 had resolved UNK-002 with "the arb did NOT reopen in ~2.5 days of post-closure observation." That window has now elapsed by ~1 day — the arb appears to have reopened. D-008's reversal criterion #1 has technically fired. Tracked here so the run-end LOOP picks it up; D-008 itself is not yet formally reversed because EXP-002 needs to run long enough to confirm whether this is the same MSUSD/USDC pair or a different arb with similar emission rate. Will resolve at LOOP time.

---

## 2026-05-16

### Failure: `--minutes 1440` timer did not terminate EXP-001
* **Expected behavior**: `detect_dry_run.py --minutes 1440` should call `asyncio.CancelledError("time budget reached")` from `on_block` when `time.monotonic() >= deadline`, propagate through cleanup, write `dry_run_*.json` summary, exit 0.
* **Actual behavior**: Run started 2026-05-13 ~05:33 UTC. Should have ended 2026-05-14 ~05:33 UTC. Container observed still running at 2026-05-16 ~13:54 UTC (~3 days past start, ~2 days past target end). No summary file written. Latest block ingestion `46,033,830` with sub-second lag — process alive and well, just not honoring the deadline.
* **Cause (HYPOTHESIZED — not verified)**: Two candidates:
  1. The deadline check fires only inside `on_block`. If `on_block` stops being called (e.g. WS disconnects, all callers blocked, or asyncio task starvation), the deadline never fires. The last few minutes before `railway down` showed block ticks stale at `46,033,830` — consistent with WS having dropped without a GapMarker being emitted.
  2. The container DID exit on time but Railway's `restartPolicyType=ON_FAILURE` was misinterpreting exit 0 and re-launching. The active deployment ID's timestamp (`2026-05-14 15:21:10 -05:00`) is suspicious — that's ~30 hours past initial deploy, suggesting at least one restart.
* **Impact**: Run consumed ~2 days of unnecessary compute + Alchemy CUs producing zero useful data (detector ran cleanly through 104K blocks with zero opps after the persistent arb closed). User had to manually terminate via `railway down`.
* **Fix**: Two-part follow-up needed:
  1. Add a periodic time-budget check NOT gated on `on_block` — a background asyncio task that wakes every N seconds and triggers shutdown if `time.monotonic() >= deadline`.
  2. Investigate Railway restart-policy behavior on exit 0. May need `restartPolicyType=NEVER` for time-bounded runs, OR a wrapper that prevents restart after planned exit.
* **STATUS**: RESOLVED 2026-05-16. Code fix landed in sub-phase 2.2 (`layer3_trading_exp/time_budget.py` — periodic asyncio watchdog NOT gated on `on_block`, cancels all peer tasks when `time.monotonic() >= deadline`, wired into `detect_dry_run.py`'s `asyncio.gather`). Unit-test regression: `tests/test_time_budget.py::test_watchdog_fires_when_deadline_reached_and_cancels_peers`. **Live regression test (Phase 2.8 hard gate)**: `python -m layer3_trading_exp.scripts.detect_dry_run --minutes 1 --chains base` exited at **67 seconds** elapsed (deadline 60s; budget 90s ceiling), with the watchdog firing within 7s of the deadline. Process terminated cleanly with the expected CancelledError propagating through the logger consumer's `queue.get()` — the clean-shutdown signature. The bug class (timer not honored due to `on_block` stalls) is structurally fixed.

---

## 2026-05-13

### Failure: Synced L3 DB had no indexes → filter eval was 3,200 ms (32× slower than local)
* **Expected behavior**: Filter eval p95 ≤ 500 ms (per `INVARIANTS.md` I-7). Local tests had measured 95 ms p95.
* **Actual behavior**: Live on Railway, per-eval p50 was 3,200 ms. Per-block processing took 2,773 ms vs Base's 2,000 ms block period → lag grew linearly. Reached 1,836 s (30 min behind real-time) before diagnosis.
* **Cause**: `sync_l3_db.py:_create_table_from_row` created tables with TEXT columns + only an `id` primary key. No indexes on the columns Layer3Client's WHERE clauses query (`contract_address`, `deployer_address`, `LOWER(address)`, etc.). Filter rules each did full table scans on 299K-row `contracts`, 70K-row `deployers`. Local tests didn't catch this because they ran against L3's actual production DB which has indexes.
* **Impact**: ~2 hours of slow-processing operation; ~30 min of accumulated lag. Logger consumer task was also starved (filter eval blocking event loop), so JSONL writes stopped early-on. ~2,420 records saved but with structural data quality issues (lag-distorted timestamps).
* **Fix**: Added plain indexes (e.g. `contracts(contract_address)`) AND functional indexes on `LOWER(col)` for queries that wrap the column in `LOWER()`. Live `CREATE INDEX IF NOT EXISTS` via `railway ssh` → filter eval dropped to 239 ms p50. Permanent fix landed in `sync_l3_db.py` via `_INDEX_SPECS_BY_TABLE`. See `decisions/D-005`.
* **STATUS**: RESOLVED 2026-05-13

### Failure: Filter eval blocked the asyncio event loop during heavy sync writes
* **Expected behavior**: Logger consumer task drains queue continuously; JSONL records persist to disk.
* **Actual behavior**: 0 JSONL records written for several minutes after deploy, despite block detection emitting `+1 opps unique=1` to stdout. Lag growing 2-3s per emission instead of staying flat.
* **Cause**: `filter_pipeline.evaluate()` does SQLite reads synchronously. The bg sync task was writing 370 MB of initial state to the same DB. SQLite locks held by the sync writer caused filter eval reads to block for seconds. Filter eval ran on the main asyncio event loop, so the entire loop was blocked — the logger consumer task couldn't dispatch. Records piled up in `asyncio.Queue` but never wrote to disk.
* **Impact**: JSONL log was effectively non-functional. Phase 1.4's whole logging acceptance criterion was being violated silently.
* **Fix**: Wrapped filter eval with `await asyncio.to_thread(filter_pipeline.evaluate, opp)` in `detect_dry_run.py:on_block`. Releases the loop during each SQLite call so the logger consumer (and other asyncio tasks) make progress.
* **STATUS**: RESOLVED 2026-05-13

---

## 2026-05-12

### Failure: SQLite ProgrammingError on background sync — thread-affinity check
* **Expected behavior**: Background sync task runs every 5 min, pulls L3 deltas, no errors.
* **Actual behavior**: Every table in every sync cycle failed with `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`. 11/11 tables failed each cycle. Zero L3 data syncing despite the bootstrap one-shot succeeding.
* **Cause**: `L3SyncRunner.__init__` created the SQLite connection in the main thread. `run_forever()` dispatched `sync_once()` via `asyncio.to_thread`, which runs on a worker thread. Python's `sqlite3` rejects cross-thread connection use unless explicitly opted in. The single-threaded bootstrap subprocess (`sync_l3_db.py --once`) didn't hit this because everything ran in the same thread.
* **Impact**: Background sync silently broken. L3 DB on the volume would have gone progressively stale over the run. Filter rules would have evaluated against frozen data.
* **Fix**: `sqlite3.connect(path, check_same_thread=False)` + enable WAL mode for concurrent read/write across the main process's Layer3Client connection. Added regression test (`test_sync_runs_from_worker_thread`).
* **STATUS**: RESOLVED 2026-05-12

### Failure: `railway variables` dumped all env vars to stdout including secrets
* **Expected behavior**: A way to verify DATA_DIR was set correctly.
* **Actual behavior**: Running `railway variables` without filter printed every key-value pair in cleartext, including `BASE_RPC_URL` (Alchemy URL with API key embedded), `BASE_WSS_URL`, and `LAYER3_ADMIN_TOKEN`.
* **Cause**: Railway CLI default `variables` listing has no redaction. I ran it unfiltered to "verify" the new service config without considering that all secrets would land in chat output.
* **Impact**: Alchemy API key suffix (`****REDACTED****`) and L3 admin token prefix (`****REDACTED****`) entered the chat transcript. User chose not to rotate Alchemy key (same key serves L3 production; rotation breaks L3). L3 admin token rotation deferred. Reduced trust surface for the leaked-secret window. (Original partial values stripped 2026-05-22 before first push to GitHub — per I-9, secrets must not enter version control.)
* **Fix**: Encode the rule: **NEVER** run `railway variables` unfiltered. Use `railway variables | grep -E "^║ KEY_NAME"` for targeted lookups. Codified in `INVARIANTS.md` I-9.
* **STATUS**: PARTIALLY RESOLVED — incident contained but secrets remain in chat transcript and not rotated.

### Failure: Git Bash MSYS path translation corrupted DATA_DIR env var
* **Expected behavior**: `railway variables --set "DATA_DIR=/app/data"` sets the literal string `/app/data` on Railway.
* **Actual behavior**: Railway received `DATA_DIR=C:/Program Files/Git/app/data`. The Linux container then wrote files to that literal path (ephemeral filesystem, NOT the volume mount). On every restart, the volume was empty and bootstrap re-ran from scratch.
* **Cause**: MSYS / Git Bash on Windows auto-translates Unix-style paths in command arguments. Any token starting with `/` gets converted to a Windows path before the binary sees it. Hit this twice in the same session — first when creating the volume (`--mount-path /app/data` rejected as "must start with `/`"), then again for the env var.
* **Impact**: Three deploy cycles lost. The "100% degraded" symptom and the missing JSONL log were both downstream of writes going to ephemeral filesystem.
* **Fix**: Prefix all Railway CLI calls passing Unix paths with `MSYS_NO_PATHCONV=1`. Codified in `INVARIANTS.md` I-9 (operational discipline).
* **STATUS**: RESOLVED 2026-05-12 (with discipline; no code change)

### Failure: First Railway build rejected by Dockerfile `VOLUME` directive
* **Expected behavior**: `railway up` builds the Docker image and deploys.
* **Actual behavior**: Build failed with `dockerfile invalid: docker VOLUME at Line X is not supported, use Railway Volumes`.
* **Cause**: My Dockerfile declared `VOLUME ["/app/data"]`. Railway's builder enforces volumes via their own config (the volume I'd already created with `railway volume add`), not Dockerfile directives.
* **Impact**: One failed build cycle, ~3 min lost.
* **Fix**: Removed `VOLUME` directive. Added explanatory comment. Volume is provisioned + mounted by Railway based on `railway volume add` state.
* **STATUS**: RESOLVED 2026-05-12

---

## Notes on filing failures here

- Operational mishaps caught and fixed in the same session → log them. Future agents need to know what's been hit and won't hit it again.
- Bugs caught by tests pre-deploy → log if the bug taught us something durable (e.g. revealed an architectural issue), otherwise just commit message is enough.
- Surprises that didn't break anything → log them as `unknowns/UNKNOWNS.md` UNK-NNN entries, not failures.
- Strategy choices that turned out to be wrong → log in `decisions/` with a follow-up decision reversing it; failures here are about correctness, not strategy.
