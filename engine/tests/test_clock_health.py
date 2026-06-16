"""Tests for the read-only clock-discipline parser (pure, no subprocess)."""

from __future__ import annotations

from engine.bloxroute.clock_health import parse_w32tm_status

SYNCED = """\
Leap Indicator: 0(no warning)
Stratum: 4 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0429153s
Root Dispersion: 7.8769732s
ReferenceId: 0xC0A80001 (source IP:  192.168.0.1)
Last Successful Sync Time: 6/9/2026 1:23:45 PM
Source: time.windows.com,0x8
Poll Interval: 10 (1024s)
Phase Offset: 0.0012340s
"""

LOCAL_CMOS = """\
Leap Indicator: 3(not synchronized)
Stratum: 0 (unspecified)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0000000s
Root Dispersion: 0.0000000s
ReferenceId: 0x00000000 (unspecified)
Last Successful Sync Time: unspecified
Source: Local CMOS Clock
Poll Interval: 10 (1024s)
"""

FREE_RUNNING = """\
Leap Indicator: 3(not synchronized)
Stratum: 0 (unspecified)
Source: Free-running System Clock
"""

SERVICE_DOWN = ("The following error occurred: The service has not been "
                "started. (0x80070426)")


def test_synced_clock_detected():
    r = parse_w32tm_status(SYNCED)
    assert r["clock_synced"] is True
    assert r["stratum"] == 4
    assert r["source"] == "time.windows.com,0x8"
    assert r["leap_indicator"] == 0
    assert abs(r["clock_offset_ms"] - 1.234) < 1e-6   # 0.001234s -> 1.234 ms
    assert r["error"] is None


def test_local_cmos_is_not_synced():
    r = parse_w32tm_status(LOCAL_CMOS)
    assert r["clock_synced"] is False     # undisciplined source
    assert r["clock_offset_ms"] is None   # no Phase Offset line


def test_free_running_is_not_synced():
    assert parse_w32tm_status(FREE_RUNNING)["clock_synced"] is False


def test_service_down_is_unknown_not_false():
    r = parse_w32tm_status(SERVICE_DOWN)
    assert r["clock_synced"] is None      # cannot determine -> honest UNKNOWN
    assert r["error"] is not None


def test_empty_output_is_unknown():
    r = parse_w32tm_status("")
    assert r["clock_synced"] is None
    assert r["error"] == "empty w32tm output"


def test_leap_not_synchronized_overrides_named_source():
    # an NTP source name but leap=3 -> still not synchronized
    txt = "Leap Indicator: 3(not synchronized)\nStratum: 5\nSource: time.nist.gov"
    assert parse_w32tm_status(txt)["clock_synced"] is False


def test_ntp_source_but_missing_leap_is_unknown():
    # regression: NTP source + valid stratum but NO Leap Indicator line is
    # absence of evidence, not proof of sync -> must be UNKNOWN, not True.
    txt = ("Stratum: 2 (secondary reference - syncd by (S)NTP)\n"
           "Source: time.nist.gov\nPoll Interval: 10 (1024s)")
    r = parse_w32tm_status(txt)
    assert r["leap_indicator"] is None
    assert r["clock_synced"] is None
