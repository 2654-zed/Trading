# /memory/

**Not documentation.** This directory is a persistent reasoning + audit
layer for the Layer 3 trading experiment. It exists so that:

- decisions are traceable
- failures are learnable
- strategy evolution is explicit
- risk is controlled
- a future autonomous agent can pick up the system without context loss

For codebase documentation, see `Desktop/Trading/LAYER3_TRADING_EXPERIMENT.md`
(spec) and `layer3_trading_exp/README.md` (developer overview).

---

## How a fresh agent should read this directory

In this order:

1. **`SYSTEM_STATE.md`** — what's running RIGHT NOW. Concrete facts about
   the active deployment, monitored set, pipeline.
2. **`STRATEGY_STATE.md`** — what we're trying to accomplish and why. The
   hypotheses being measured.
3. **`INVARIANTS.md`** — non-negotiable rules. Violating any of these halts.
4. **`unknowns/UNKNOWNS.md`** — what we don't know yet and what hinges on it.
5. **`failures/FAILURE_LOG.md`** — what's broken before, sorted newest-first.
6. **`decisions/`** — every decision that shaped current state, with rationale + reversal criteria.
7. **`trades/`** — per-run opportunity review snapshots (named "trades" for symmetry; this codebase doesn't actually trade — see `INVARIANTS.md` I-1).
8. **`loop/LOOP.md`** — the reflection loop the agent MUST run after operational events.

---

## How to update

| Action | Files to update |
|---|---|
| Live deployment state changed (new run, new redeploy, kill switch hit) | `SYSTEM_STATE.md` health snapshot |
| Strategy intent changed (new hypothesis, success criterion shift) | `STRATEGY_STATE.md` |
| Code change to runtime modules | Run the LOOP, log a decision if architecturally meaningful |
| Failure happened in deployment / sync / detection | Append to `failures/FAILURE_LOG.md` |
| Surprise / question that can't be answered with current data | Add UNK to `unknowns/UNKNOWNS.md` |
| Operational or strategy decision made | Create `decisions/D-NNN_<name>.md` |
| Detection run completes | Run the LOOP — produces files in trades/, possibly failures/, unknowns/, decisions/ |

---

## What this directory is NOT

- Not a status dashboard
- Not a code documentation system
- Not a personal journal (memory of past sessions lives at `~/.claude/projects/.../memory/`)
- Not optional — `INVARIANTS.md` I-10 makes loop execution required behavior

---

## Quick verification (CI-style)

```bash
# All required files exist + non-empty:
for f in SYSTEM_STATE.md STRATEGY_STATE.md INVARIANTS.md \
         unknowns/UNKNOWNS.md failures/FAILURE_LOG.md loop/LOOP.md \
         trades/README.md decisions/README.md; do
  [ -s "memory/$f" ] || echo "MISSING OR EMPTY: $f"
done
```
