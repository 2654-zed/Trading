#!/usr/bin/env python3
"""P1 archive-only drainer pre-filter over third_party_transferfrom_flags.jsonl.

Feasibility test (docs/FEAS_drainer-signal.md), Gate A — extractability.
Question: does the benign CEX-deposit-sweeper bulk concentrate into an
identifiable region (high fanout + recurring victims), leaving a smaller,
qualitatively different candidate residue worth enriching with RPC/labels?

ARCHIVE-ONLY. No RPC, no external labels, $0. Reads the flag file in place.
Each flag row: {hash, initiator (tx sender/spender), owner_debited (pulled
from), to (recipient), token, recv_ts}.
"""
from __future__ import annotations
import collections
import json
from pathlib import Path

FLAGS = Path(__file__).resolve().parents[1] / "data" / "mempool" / "third_party_transferfrom_flags.jsonl"
KNOWN = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x000000000022d473030f116ddee9f6b43ac78ba3": "Permit2",
}
SWEEPER_FANOUT = 20    # >= this many distinct victims => infra-scale spender
SWEEPER_RECUR = 1.5    # avg sweeps/victim >= this => recurring relationship (deposit sweeper)


def pct(a, b):
    return f"{100*a/b:5.1f}%" if b else "  n/a"


def main():
    rows = []
    bad = 0
    for line in FLAGS.open(encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        rows.append((r.get("initiator"), r.get("owner_debited"), r.get("to"), r.get("token")))

    N = len(rows)
    init_owners = collections.defaultdict(set)
    init_flags = collections.Counter()
    init_self = collections.Counter()
    pair_count = collections.Counter()
    recip_count = collections.Counter()
    for ini, own, to, tok in rows:
        init_owners[ini].add(own)
        init_flags[ini] += 1
        if to == ini:
            init_self[ini] += 1
        pair_count[(ini, own)] += 1
        recip_count[to] += 1

    def fanout(i):
        return len(init_owners[i])

    def recur(i):
        return init_flags[i] / max(1, fanout(i))

    # --- classify initiators into three regions ---------------------------
    sweeper, kit, small = set(), set(), set()
    for i in init_flags:
        if fanout(i) >= SWEEPER_FANOUT and recur(i) >= SWEEPER_RECUR:
            sweeper.add(i)            # benign bulk: high fanout, recurring victims
        elif fanout(i) >= SWEEPER_FANOUT:
            kit.add(i)                # AMBIGUOUS: high fanout, each victim ~once (drainer-kit OR one-time sweeper)
        else:
            small.add(i)             # low fanout: targeted-drain hiding place

    def flags_of(s):
        return sum(init_flags[i] for i in s)

    print(f"=== P1 archive-only pre-filter — {FLAGS.name} ===")
    print(f"flags={N:,}  bad_lines={bad}  distinct initiators={len(init_flags):,}  "
          f"distinct victims={len(set(o for _, o, _, _ in rows)):,}")
    print(f"thresholds: SWEEPER if fanout>={SWEEPER_FANOUT} AND avg sweeps/victim>={SWEEPER_RECUR}")

    print("\n-- (initiator,victim) pair recurrence --")
    rc = collections.Counter(pair_count.values())
    one = sum(c for p, c in pair_count.items() if c == 1)
    print(f"  distinct pairs={len(pair_count):,}  one-shot pairs={rc[1]:,} "
          f"({pct(rc[1], len(pair_count))} of pairs)  flags in 1x pairs={one:,} ({pct(one, N)} of flags)")
    print(f"  pairs seen 2x={rc[2]:,}  3-9x={sum(v for k,v in rc.items() if 3<=k<10):,}  "
          f"10x+={sum(v for k,v in rc.items() if k>=10):,}")

    print("\n-- initiator regions (share of all flags) --")
    print(f"  SWEEPER (benign bulk) : inits={len(sweeper):>6,}  flags={flags_of(sweeper):>8,} ({pct(flags_of(sweeper), N)})")
    print(f"  KIT? (hi-fanout 1x)   : inits={len(kit):>6,}  flags={flags_of(kit):>8,} ({pct(flags_of(kit), N)})  <- ambiguous, needs labels/RPC")
    print(f"  SMALL (lo-fanout)     : inits={len(small):>6,}  flags={flags_of(small):>8,} ({pct(flags_of(small), N)})")

    # --- candidate funnel -------------------------------------------------
    small_flags = [(ini, own, to, tok) for ini, own, to, tok in rows if ini in small]
    cand = [(ini, own, to, tok) for ini, own, to, tok in small_flags if pair_count[(ini, own)] == 1]
    cand_3rdparty = [r for r in cand if r[2] != r[0]]  # to != initiator (cash-out to a third address)
    print("\n-- candidate funnel (drainer-shaped residue) --")
    print(f"  start (all flags)                         : {N:>8,}")
    print(f"  drop SWEEPER bulk                         : {N-flags_of(sweeper):>8,}  ({pct(N-flags_of(sweeper), N)} remain)")
    print(f"  small-initiator only (drop KIT?)          : {len(small_flags):>8,}  ({pct(len(small_flags), N)})")
    print(f"  + one-shot (initiator,victim) pair        : {len(cand):>8,}  ({pct(len(cand), N)})")
    print(f"  + recipient != initiator (3rd-addr payout): {len(cand_3rdparty):>8,}  ({pct(len(cand_3rdparty), N)})")

    # --- characterize the residue ----------------------------------------
    c_init = set(r[0] for r in cand)
    c_own = set(r[1] for r in cand)
    c_recip = set(r[2] for r in cand)
    c_tok = collections.Counter(r[3] for r in cand)
    fresh_recip = sum(1 for r in c_recip if recip_count[r] == 1)
    print("\n-- candidate residue characterization (one-shot, small-initiator set) --")
    print(f"  flags={len(cand):,}  distinct initiators={len(c_init):,}  victims={len(c_own):,}  recipients={len(c_recip):,}")
    print(f"  recipients seen exactly once across ALL flags: {fresh_recip:,} ({pct(fresh_recip, len(c_recip))} of cand recipients)")
    print(f"  token mix: " + ", ".join(f"{KNOWN.get(t, t[:10])}={pct(c, len(cand))}" for t, c in c_tok.most_common(4)))

    print("\n-- top 8 SWEEPER initiators (should be CEX hot keys — sanity) --")
    for i in sorted(sweeper, key=lambda x: init_flags[x], reverse=True)[:8]:
        print(f"  {init_flags[i]:>6,} flags  fanout={fanout(i):>5,}  recur={recur(i):>5.1f}  "
              f"self={pct(init_self[i], init_flags[i])}  {i}")


if __name__ == "__main__":
    main()
