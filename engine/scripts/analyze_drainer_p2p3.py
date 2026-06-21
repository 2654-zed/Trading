#!/usr/bin/env python3
"""P2/P3 precision probe on the drainer-candidate cell.

Feasibility test (docs/FEAS_drainer-signal.md), Gate A - extractability.
Takes the P1 residue (low-fanout initiator, one-shot (initiator,victim) pair,
payout to a 3rd address) and tries to MEASURE how many are real drains, free:

  P2 (labels) : overlap vs the Scam Sniffer public drainer blacklist (gold
                positive). Reported for the cell AND the full population.
  P3 (on-chain, free RPC): per candidate -> did it land? (ghost fraction = the
                differentiator); transferFrom amount from receipt logs; is the
                victim emptied now? is the payout address fresh (low nonce)?

Lists are reactive (~days delayed) so list recall on a 9-day archive is a FLOOR.
balanceOf is read at 'latest' (proxy for emptied) so no archive node is needed.
"""
from __future__ import annotations
import collections
import json
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "mempool"
FLAGS = DATA / "third_party_transferfrom_flags.jsonl"
SCAMSNIFFER = "https://raw.githubusercontent.com/scamsniffer/scam-database/main/blacklist/address.json"
ENDPOINTS = ["https://ethereum-rpc.publicnode.com", "https://eth.drpc.org"]  # UA-friendly, batch-capable
UA = "Mozilla/5.0 (research)"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SWEEPER_FANOUT = 20
_rot = 0


def addr32(a):
    return "0x" + "0" * 24 + a.lower().replace("0x", "")


def rpc_batch(reqs, timeout=15, tries=4):
    """reqs: list of (method, params). Returns (ok, id-aligned results).
    ok=False means the whole batch transport failed (=> retry/miss, NOT ghost)."""
    global _rot
    body = json.dumps([{"jsonrpc": "2.0", "id": i, "method": m, "params": p}
                       for i, (m, p) in enumerate(reqs)]).encode()
    for _ in range(tries):
        ep = ENDPOINTS[_rot % len(ENDPOINTS)]
        _rot += 1
        try:
            req = urllib.request.Request(ep, data=body,
                                         headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                arr = json.loads(r.read())
            if not isinstance(arr, list):
                continue
            out = [None] * len(reqs)
            for it in arr:
                if isinstance(it, dict) and "id" in it and "result" in it:
                    out[it["id"]] = it["result"]
            return True, out
        except Exception:
            time.sleep(0.5)
    return False, [None] * len(reqs)


def load_flags():
    rows = []
    for line in FLAGS.open(encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        rows.append((r.get("hash"), (r.get("initiator") or "").lower(),
                     (r.get("owner_debited") or "").lower(), (r.get("to") or "").lower(),
                     (r.get("token") or "").lower()))
    return rows


def main():
    rows = load_flags()
    init_owners = collections.defaultdict(set)
    pair = collections.Counter()
    for _, ini, own, to, tok in rows:
        init_owners[ini].add(own)
        pair[(ini, own)] += 1
    small = {i for i in init_owners if len(init_owners[i]) < SWEEPER_FANOUT}   # exclude sweeper AND kit (matches P1)
    cell = [r for r in rows if r[1] in small and pair[(r[1], r[2])] == 1 and r[3] != r[1]]
    print(f"=== P2/P3 precision probe ===\nflags={len(rows):,}  tight candidate cell (P1 '532')={len(cell):,}")

    # ---- P2: list overlap (free, no RPC) --------------------------------
    try:
        with urllib.request.urlopen(urllib.request.Request(SCAMSNIFFER, headers={"User-Agent": UA}), timeout=25) as r:
            obj = json.loads(r.read())
        scam = {a.lower() for a in (obj.keys() if isinstance(obj, dict) else obj) if isinstance(a, str) and a.startswith("0x")}
    except Exception as e:
        scam = set()
        print(f"  (scam list fetch failed: {e})")
    print(f"\n-- P2: Scam Sniffer drainer list ({len(scam):,} addrs) overlap --")

    def overlap(rs, label):
        hit = [r for r in rs if r[1] in scam or r[3] in scam]
        print(f"  {label:<30} flags_hit={len(hit):>5} ({100*len(hit)/max(1,len(rs)):4.1f}%)  "
              f"distinct flagged addrs={len({r[1] for r in hit} | {r[3] for r in hit})}")
        return hit

    overlap(rows, "full population")
    overlap([r for r in rows if r[3] != r[1]], "all 3rd-addr-payout")
    cell_hits = overlap(cell, "tight candidate cell")

    # ---- P3: on-chain enrichment on the cell (free RPC) -----------------
    print(f"\n-- P3: on-chain enrichment of the {len(cell)}-flag cell (free RPC) --")
    receipts, responded = {}, {}
    B = 20
    for s in range(0, len(cell), B):
        chunk = cell[s:s + B]
        ok, res = rpc_batch([("eth_getTransactionReceipt", [r[0]]) for r in chunk])
        for r, rc in zip(chunk, res):
            receipts[r[0]] = rc
            responded[r[0]] = ok
        time.sleep(0.1)

    def st(r):
        rc = receipts.get(r[0])
        return rc.get("status") if isinstance(rc, dict) else None

    landed = [r for r in cell if responded[r[0]] and st(r) == "0x1"]
    ghost = [r for r in cell if responded[r[0]] and receipts[r[0]] is None]
    reverted = [r for r in cell if responded[r[0]] and st(r) == "0x0"]
    miss = [r for r in cell if not responded[r[0]]]

    def drained_amount(r):
        rc = receipts.get(r[0]) or {}
        for lg in (rc.get("logs") or []):
            tp = lg.get("topics", [])
            if (lg.get("address", "").lower() == r[4] and len(tp) >= 3
                    and tp[0].lower() == TRANSFER_TOPIC and tp[1].lower() == addr32(r[2])):
                try:
                    return int(lg.get("data", "0x0"), 16)
                except ValueError:
                    return None
        return None

    bal, nonce = {}, {}
    for s in range(0, len(landed), B):
        chunk = landed[s:s + B]
        reqs = []
        for r in chunk:
            reqs.append(("eth_call", [{"to": r[4], "data": "0x70a08231" + addr32(r[2])[2:]}, "latest"]))
            reqs.append(("eth_getTransactionCount", [r[3], "latest"]))
        ok, res = rpc_batch(reqs)
        for k, r in enumerate(chunk):
            b, nn = res[2 * k], res[2 * k + 1]
            bal[r[0]] = int(b, 16) if b else None
            nonce[r[0]] = int(nn, 16) if nn else None
        time.sleep(0.1)

    DUST = 1_000_000  # < 1 unit (6-dec stable) == emptied
    emptied = [r for r in landed if bal.get(r[0]) is not None and bal[r[0]] < DUST]
    fresh = [r for r in landed if nonce.get(r[0]) is not None and nonce[r[0]] <= 5]
    silver = [r for r in landed if r in emptied and r in fresh]
    amts = sorted(a / 1e6 for a in (drained_amount(r) for r in landed) if a)

    LH = {r[0] for r in landed}; GH = {r[0] for r in ghost}; RV = {r[0] for r in reverted}
    EM = {r[0] for r in emptied}; FR = {r[0] for r in fresh}; SV = {r[0] for r in silver}
    dump = DATA.parent / "drainer_cell_p3.jsonl"
    with dump.open("w", encoding="utf-8") as f:
        for r in cell:
            h = r[0]
            f.write(json.dumps({"hash": h, "initiator": r[1], "owner": r[2], "to": r[3], "token": r[4],
                                "landed": h in LH, "ghost": h in GH, "reverted": h in RV,
                                "amount_raw": drained_amount(r), "victim_bal_now": bal.get(h),
                                "recip_nonce": nonce.get(h), "emptied": h in EM, "fresh": h in FR,
                                "silver": h in SV}) + "\n")
    print(f"  [dumped {len(cell)} enriched candidates -> {dump.name}]")
    print(f"  landed={len(landed)}  never-mined GHOST={len(ghost)}  reverted={len(reverted)}  rpc_miss={len(miss)}")
    print(f"  GHOST fraction (the differentiator): {100*len(ghost)/max(1,len(cell)-len(miss)):.1f}% of resolved")
    if amts:
        print(f"  drained amount USD (6-dec assumed): median=${amts[len(amts)//2]:,.0f}  "
              f"p90=${amts[min(len(amts)-1,int(len(amts)*0.9))]:,.0f}  max=${amts[-1]:,.0f}  n={len(amts)}")
    print(f"  victim emptied now (<$1 left): {len(emptied)}/{len(landed)}")
    print(f"  payout address fresh (nonce<=5): {len(fresh)}/{len(landed)}")
    print(f"  SILVER-DRAIN (emptied AND fresh): {len(silver)}")
    print(f"  GOLD (list-confirmed in cell): {len(cell_hits)}")
    drain_likely = set(r[0] for r in cell_hits) | set(r[0] for r in silver)
    print(f"\n-- verdict --")
    print(f"  drain-likely (gold OR silver): {len(drain_likely)}/{len(cell)} of cell "
          f"({100*len(drain_likely)/max(1,len(cell)):.1f}%)")


if __name__ == "__main__":
    main()
