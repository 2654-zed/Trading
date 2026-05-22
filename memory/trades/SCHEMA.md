# trades/ schema — reasoning-annotated opportunity entries

The base data lives in `/app/data/logs/YYYY-MM-DD.jsonl` (one opportunity
per line, schema in `layer3_trading_exp/schema.py`). That format is the
**raw measurement layer**.

This directory's role is the **reasoning layer**: for opportunities we
flag as worth understanding individually (not all 2,420+), annotate them
with the agent's hypothesis attribution, confidence, predicted outcome,
and post-hoc analysis. This enables attribution — "which hypotheses are
generating value" vs "which signals are noise."

Two file types coexist in this directory:

1. **`YYYY-MM-DD_review.md`** — per-run review summaries (Step 1 of LOOP).
2. **`YYYY-MM-DD_<event_id>.json`** — per-opportunity reasoning annotations
   (Phase 4 schema). Created selectively, not for every opp.

---

## Annotation JSON format

```json
{
  "event_id": "<opportunity_id from JSONL — matches a record in /app/data/logs/...>",
  "signal": "<what made this opportunity worth annotating — e.g. 'first opportunity since persistent arb closed' or 'novel pool pair never seen before'>",
  "hypothesis": "<which strategic hypothesis this opportunity speaks to — H1 / H2 / H3 / multiple, by ID from STRATEGY_STATE.md>",
  "reasoning": "<why this opportunity matters for that hypothesis — one paragraph>",
  "confidence": "<float 0.0-1.0: agent's confidence that this opportunity is real and not a measurement artifact>",
  "action": "<what we'd DO about this opportunity in execution mode (which is forbidden in Phase 1) — e.g. 'borrow 10K USDC, execute 2-leg swap, expect $25 net'>",
  "outcome": "<what actually happened post-detection — 'arb persisted N blocks then closed' / 'price moved against us before we could have executed' / etc.>",
  "postmortem": "<learned-from line: did the hypothesis attribution hold up? would we annotate this signal again?>"
}
```

### Field guidance

- **event_id**: must match a real `opportunity_id` in the JSONL log. Cross-reference makes attribution auditable.
- **signal**: a sentence, not a category. "First opp on a Slipstream-only protocol combo" is a signal; "arbitrage detected" is not.
- **hypothesis**: hypothesis IDs from `STRATEGY_STATE.md`. If multiple, list them.
- **reasoning**: must explain why this opp moves the needle on the hypothesis. If you can't write the reasoning, this opp isn't worth annotating.
- **confidence**: agent's calibration. 0.0 = "almost certainly a glitch"; 1.0 = "this is real and reproducible". Use this honestly — over-confident annotations are worse than no annotation.
- **action**: counterfactual — what would the execution-mode version of this system have done? Useful for Phase 2 spec input.
- **outcome**: filled in some time AFTER the event_id was first logged. Track persistence: did the arb close? In how many blocks? Did Layer 3 eventually flag it?
- **postmortem**: this is the learning artifact. Be concrete.

### When to annotate (NOT every opportunity)

- The first opportunity of a new pool pair / token combination
- Opportunities that cross a confidence threshold (high or low)
- Opportunities that contradict a recent loop's hypothesis scoring
- Opportunities where the gross margin > 1.0% (rare; high research value)
- Opportunities that an UNKNOWN's resolution depends on (cross-reference UNK-NNN in `reasoning`)

For run-level reviews, use the Markdown format (see `2026-05-13_pre-index-fix.md`),
not the JSON format. JSON is per-opportunity reasoning.

### Goal of this format

Attribution. Over many annotations, we should be able to query:

- Which hypotheses generated the most high-confidence true-positive opportunities?
- Which signals turned out to be noise more often than substance?
- Are there pool/protocol combinations that consistently surface real opps?
- Did our confidence calibrate correctly (high-confidence annotations → high follow-through rate)?

The schema is overkill at zero annotations and pays off at 20+.

### Seed example

See `2026-05-13_persistent-msusd-usdc.json` for the format applied to the
single arbitrage opportunity observed during the first 2 hours of EXP-001
(opportunity_id chosen from a representative record).
