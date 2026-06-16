"""Clock-discipline probe for the mempool capture (Tier-0 hardening).

Network-timing forensics are only as trustworthy as the clock stamping
`recv_ts`. This module reads the OS time-sync state (Windows `w32tm`,
READ-ONLY — it never changes the clock) and yields a structured record so
every capture run carries an auditable answer to "was the wall clock
disciplined while these timestamps were taken?".

Honesty rules:
  * Pure parser (`parse_w32tm_status`) is unit-tested across sync states.
  * The subprocess wrapper NEVER raises and NEVER hangs (hard timeout); on
    any failure it returns clock_synced=None plus an `error` string — an
    UNKNOWN is reported as unknown, never silently as "synced".
  * Until the user enables NTP (admin step, deferred per decision #2),
    clock_synced will read False/None and that fact is recorded per-run
    and per-row — not hidden.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

# Sources w32tm reports when the clock is NOT disciplined by NTP.
_UNDISCIPLINED_SOURCES = ("local cmos clock", "free-running system clock")
_STRATUM_RE = re.compile(r"Stratum:\s*(\d+)")
_LEAP_RE = re.compile(r"Leap Indicator:\s*(\d+)")
_SOURCE_RE = re.compile(r"Source:\s*(.+)")
_LASTSYNC_RE = re.compile(r"Last Successful Sync Time:\s*(.+)")
_OFFSET_RE = re.compile(r"Phase Offset:\s*([-+0-9.eE]+)\s*s", re.IGNORECASE)


def parse_w32tm_status(text: str) -> dict:
    """Parse `w32tm /query /status` output into a structured dict.

    Returns keys: clock_synced (bool|None), clock_offset_ms (float|None),
    stratum (int|None), source (str|None), last_sync (str|None),
    leap_indicator (int|None), error (str|None).

    clock_synced is True only when there is positive evidence of NTP
    discipline (a real source, valid stratum 1..15, leap != 3); False when
    there is positive evidence it is undisciplined (local/free-running
    source, leap=3 "not synchronized", or stratum 0/16); None when the
    output is unrecognizable or reports a service error (UNKNOWN, not a
    guess)."""
    out: dict = {
        "clock_synced": None, "clock_offset_ms": None, "stratum": None,
        "source": None, "last_sync": None, "leap_indicator": None,
        "error": None,
    }
    if not text or not text.strip():
        out["error"] = "empty w32tm output"
        return out

    low = text.lower()
    # A stopped W32Time service / access error -> genuinely unknown.
    if "error occurred" in low or "service has not been started" in low \
            or "0x80070426" in low:
        out["error"] = "w32tm reported a service/error state"
        # fall through to still capture whatever fields are present

    m = _STRATUM_RE.search(text)
    if m:
        out["stratum"] = int(m.group(1))
    m = _LEAP_RE.search(text)
    if m:
        out["leap_indicator"] = int(m.group(1))
    m = _SOURCE_RE.search(text)
    if m:
        out["source"] = m.group(1).strip()
    m = _LASTSYNC_RE.search(text)
    if m:
        out["last_sync"] = m.group(1).strip()
    m = _OFFSET_RE.search(text)
    if m:
        try:
            out["clock_offset_ms"] = float(m.group(1)) * 1000.0
        except ValueError:
            out["clock_offset_ms"] = None

    # ---- decide clock_synced from the evidence -------------------------
    src = (out["source"] or "").lower()
    leap = out["leap_indicator"]
    stratum = out["stratum"]

    if src and any(u in src for u in _UNDISCIPLINED_SOURCES):
        out["clock_synced"] = False          # explicitly not disciplined
    elif leap == 3:
        out["clock_synced"] = False          # "not synchronized"
    elif stratum is not None and (stratum == 0 or stratum >= 16):
        out["clock_synced"] = False          # unspecified / unsynchronized
    elif (src and stratum is not None and 1 <= stratum <= 15
          and leap is not None and leap != 3 and not out["error"]):
        # require POSITIVE leap evidence (known and not 3); a missing leap
        # line is absence of information, not proof of sync -> stays UNKNOWN.
        out["clock_synced"] = True           # positive evidence of NTP sync
    else:
        out["clock_synced"] = None           # cannot determine -> UNKNOWN
    return out


def query_clock_status(timeout: float = 5.0) -> dict:
    """Run `w32tm /query /status` READ-ONLY and parse it. Never raises and
    never hangs (hard `timeout`). On any failure returns a dict with
    clock_synced=None and an `error` string (the honest UNKNOWN)."""
    try:
        proc = subprocess.run(
            ["w32tm", "/query", "/status"],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return {"clock_synced": None, "clock_offset_ms": None, "stratum": None,
                "source": None, "last_sync": None, "leap_indicator": None,
                "error": "w32tm not available (non-Windows or missing)"}
    except subprocess.TimeoutExpired:
        return {"clock_synced": None, "clock_offset_ms": None, "stratum": None,
                "source": None, "last_sync": None, "leap_indicator": None,
                "error": f"w32tm timed out after {timeout}s"}
    except OSError as e:
        return {"clock_synced": None, "clock_offset_ms": None, "stratum": None,
                "source": None, "last_sync": None, "leap_indicator": None,
                "error": f"w32tm failed: {type(e).__name__}: {e}"}

    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    parsed = parse_w32tm_status(text)
    if proc.returncode != 0 and not parsed.get("error"):
        parsed["error"] = f"w32tm exit {proc.returncode}"
    return parsed


__all__ = ["parse_w32tm_status", "query_clock_status"]
