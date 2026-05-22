# INVARIANTS.md

**Non-negotiable rules.** Violating any of these is a STOP-AND-REPORT event,
not a tradeoff. If an action would violate an invariant, halt and surface
the conflict — do not proceed.

Source of truth: `LAYER3_TRADING_EXPERIMENT.md` §Invariants and §"What NOT to build".

---

## I-1: Read-only on-chain

The system **MUST NOT** construct, sign, or submit any transaction.
**MUST NOT** generate, hold, or reference any private key.
**MUST NOT** call `approve()`, `swap()`, `flashLoan()`, or any state-mutating
function on any contract.

Enforcement: code review at every PR. Any module that touches transaction
construction is a violation.

**Verification command:**
```bash
grep -rn "eth_sendTransaction\|signTransaction\|account.signTransaction\|privateKey\|sign_typed_data" layer3_trading_exp/
# Expected: zero matches (excluding tests / comments explicitly forbidding these)
```

---

## I-2: Frozen pool set during a run

Per spec §Phase 1.1: the monitored pool set is enumerated **once** at run
start and never modified during the run. `monitored_pools.json` is the
canonical artifact. The script refuses to overwrite it without manual
deletion.

Enforcement: `PoolSet.write_to` raises `FileExistsError` if the target file
exists. Phase 1.1 acceptance test verifies this.

**Verification command:**
```bash
ls -la /app/data/run_metadata/monitored_pools.json
# Should show a single immutable file from run start; mtime should not advance.
```

---

## I-3: Read-only against Layer 3

The trading exp pulls data from Layer 3 via `/dump` (HTTP GET only).
**MUST NOT** write to Layer 3's database. **MUST NOT** call any L3 admin
endpoint that mutates state. **MUST NOT** propose schema changes to Layer 3
from inside this codebase (raise via Layer 3 maintainer channel instead).

Enforcement: only the `sync_l3_db.py` module touches L3. It uses
`urllib.request.urlopen` with the `/dump` endpoint. Any other L3 HTTP call
is a violation.

**Verification command:**
```bash
grep -rn "stellar-embrace\|spypy\|surveillance.up\|layer3" layer3_trading_exp/ \
  | grep -v "dump\|GET\|read"
# Expected: only references to the dump endpoint and read-only docs
```

---

## I-4: No LLM in runtime

Per spec §"What NOT to build": no LLM inference in the runtime pipeline.
Not for filter decisions, not for opportunity ranking, not for anything.

Claude Code writes the code; the code runs deterministically.

**Verification command:**
```bash
grep -rn "openai\|anthropic\|llm\|completion\|chat" layer3_trading_exp/
# Expected: zero matches in runtime modules (tests/comments OK)
```

---

## I-5: No adaptive rules

Per spec §Invariants #2: filter thresholds, pool monitoring sets, and
opportunity detection logic do not change until the run ends. Improvements
discovered mid-run are documented in `decisions/` and applied in a
subsequent run.

If a value MUST change mid-run (operational fix, not strategy change):
log a decision entry first.

---

## I-6: Loud failure over silent wrong output

Every error path produces a visible signal. No silent retries that swallow
exceptions, no fallbacks that fabricate plausible-but-wrong data.

| Failure mode | Required behavior |
|---|---|
| Layer 3 DB unreachable | Pause evaluation; log every opp with `layer3_unavailable=true` |
| Base RPC unreachable | Pause monitor; reconnect with backoff; emit `GapMarker` |
| Disk fills | Halt with loud alert. Never drop log entries silently |
| One filter rule raises | Record `{"error": ..., "fired": null}` for that rule, continue others |
| All filter rules raise | Log opp with `filter_evaluation_failed=true`, pause new evaluation |
| Synced DB missing a queried table | Surface as runtime error, halt detection until resynced |

---

## I-7: Performance budgets

| Component | Budget | Action if exceeded |
|---|---|---|
| Filter pipeline p95 latency | ≤ 500 ms | Investigate; add indexes; flag in `failures/` |
| Per-block compute (detector) | ≤ 1 block period (Base 2s) | If exceeded persistently → ingest backlog grows |
| Alchemy CU usage | < 80% of free-tier quota (300M CU/month) | Reduce monitoring frequency or rotate plan |
| Logger queue overflow | Surfaced loudly (counter + stderr) | Acceptable if logged; silent drop is a violation |

---

## I-8: Kill switch authority

Only Jason can halt the live run.
The kill switch is `/app/data/KILL_SWITCH`.
Touching this file causes detection to exit cleanly within one block period.

Claude Code is **not** in the loop during the run.
Claude Code does **not** have unilateral authority to redeploy or restart
the running container while it's collecting data.
Container redeployment requires user authorization.

---

## I-9: Token / secret handling

- Tokens never appear in chat output, code commits, or memory files.
- Tokens live as Railway env vars (`LAYER3_ADMIN_TOKEN`) and in local `.env`
  files that are git-ignored.
- The `railway variables` command is **NEVER** run unfiltered — it dumps
  cleartext for every env var. Use targeted greps for specific keys only.
  Past incident: see `failures/FAILURE_LOG.md` 2026-05-12 secret leak.

---

## I-10: Reflection loop completion

After any of:
- a completed detection run
- a code change that touches detector / filter / monitor / sync
- a deployment
- a discovered surprise during operation

…the agent **must** run the full LOOP defined in `loop/LOOP.md`. Skipping
the loop on the basis of "things look fine" is a violation. The loop is
the only mechanism that ensures unknowns and failures don't pile up
silently.

---

## I-11: Intervention triggers (system MUST react automatically)

This invariant converts the agent from passive observer to active responder.
When ANY of the following conditions fires, the listed actions are NOT
optional.

### Trigger conditions

| # | Trigger | Detection method |
|---|---|---|
| T-A | **Processing lag > 30s** for any block tick | `railway logs --deployment | grep "lag=" | awk '$N>30'` (or equivalent inline in loop) — sampled at LOOP cadence or on each manual check |
| T-B | **A hypothesis is INVALIDATED** by Step 6 of LOOP | Loop Step 6 explicitly assigns INVALIDATED |
| T-C | **The same failure occurs >2 times** | Same `### Failure: <name>` heading present 3+ times in `failures/FAILURE_LOG.md` |
| T-D | **An UNKNOWN blocks execution** | A code path raises an exception that explicitly references an UNK-NNN, OR an UNKNOWN's "Linked Decision" is required to proceed and is still "none yet" |

### Required actions on trigger

Every trigger above produces **all three** of these actions:

1. **Log a decision** — create `decisions/D-NNN_intervention-<trigger>-<date>.md` capturing what was observed, what's being changed, and reversal criteria.
2. **Pause or adjust the system** as appropriate to the trigger:
   - T-A (lag > 30s): if lag exceeds 30s persistently (3 consecutive sample points), the agent should propose chunking the bg sync OR scaling sync interval. Pausing is OPTIONAL — research data continues to be useful even with lag, but lag growth is a structural signal that must be addressed.
   - T-B (hypothesis invalidated): pause the related experiment if it's still running. Don't continue collecting data for a question we've already answered "no" on.
   - T-C (repeat failure): halt the affected subsystem until root-cause is documented. Three repeats means the prior fix(es) didn't work; continuing is reckless.
   - T-D (UNKNOWN blocking): halt the affected subsystem; the gate is by design.
3. **Record event in `failures/FAILURE_LOG.md`** — even for T-B (hypothesis invalidation), which isn't a "failure" in the bug sense but IS a structural surprise that must be tracked alongside operational failures. Format: same as other failure entries, with `Cause: hypothesis invalidation` or similar.

### What does NOT count as a trigger

- Single transient lag spike <30s (the per-block lag noise floor)
- A SOFT hypothesis weakening (use LOOP Step 6's WEAKENED state, not this invariant)
- A previously-resolved failure recurring once (resolved means we learned; recurrence at <3 count is normal in distributed systems)
- An UNKNOWN that DOESN'T block execution (the deadline/trigger field handles those)

### Why this invariant exists

Without explicit intervention rules, the system slides toward "everything is fine because nothing is screaming." This invariant forces the failure modes that DO occur in production (slow degradation, structural blockers, repeat-bug compounding) to surface as visible state changes rather than silent ones.

### Verification

A current check of all triggers:

```bash
# T-A: any block tick with lag > 30s in the last 200 logged lines
railway logs --deployment 2>/dev/null | grep -E "lag=[3-9][0-9]\.|lag=[0-9]{3,}\." | tail -5

# T-C: any failure heading appearing 3+ times
grep -E "^### Failure:" memory/failures/FAILURE_LOG.md | sort | uniq -c | awk '$1 >= 3'

# T-D: any UNKNOWN marked "blocks execution" with no linked decision
grep -B1 -A1 "blocks execution" memory/unknowns/UNKNOWNS.md | grep -B2 "Linked Decision.*none yet"
```

The absence of T-B is a state, not a query; it's checked at Loop Step 6.

---

## How to verify ALL invariants at once

```bash
# 1. Code-grep checks (I-1, I-3, I-4):
cd C:/Users/jason/Desktop/Trading
grep -rn "eth_sendTransaction\|signTransaction\|privateKey" layer3_trading_exp/
grep -rn "openai\|anthropic\|llm" layer3_trading_exp/

# 2. Test suite (catches most invariant regressions):
python -m pytest layer3_trading_exp/tests/   # expect 204 passing

# 3. Live deployment health (I-6, I-7):
railway service layer3-trading-exp
railway logs --deployment | grep -iE "GAP|FAILED|OVERFLOW|Traceback" | tail -5
```
