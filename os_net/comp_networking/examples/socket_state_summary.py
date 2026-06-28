#!/usr/bin/env python3
"""
socket_state_summary.py - summarize TCP socket states from /proc/net/tcp(6),
the fast way to spot TIME_WAIT floods and CLOSE_WAIT pileups during an incident.

What the states tell you (enterprise_scenarios/04_network_incidents.md):
  - many TIME_WAIT  -> connection churn; risk of ephemeral-port exhaustion (4.1)
  - many CLOSE_WAIT -> the APP isn't close()ing sockets -> fd/conn leak (4.4, 03.5)
  - many SYN_RECV   -> SYN flood / accept-queue pressure (4.2, 4.11)
  - rising FIN_WAIT -> peers not completing teardown

Concept docs: comp_networking/04_transport_tcp_udp.md (state machine, TIME_WAIT).

Run (Linux):  python3 socket_state_summary.py
Run (any OS): python3 socket_state_summary.py --selftest
"""
from __future__ import annotations
import sys

# Linux kernel TCP state codes (hex) -> name. See include/net/tcp_states.h.
TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
    "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
    "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING", "0C": "NEW_SYN_RECV",
}

# Thresholds that warrant a flag in a summary.
WARN = {"TIME_WAIT": 10000, "CLOSE_WAIT": 500, "SYN_RECV": 500}

SAMPLE = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid
   0: 0100007F:1538 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0
   1: 0100007F:E1F2 0100007F:1538 01 00000000:00000000 00:00000000 00000000  1000
   2: 0100007F:E1F3 0100007F:1538 06 00000000:00000000 03:00000ABC 00000000     0
   3: 0100007F:E1F4 0100007F:1538 06 00000000:00000000 03:00000ABC 00000000     0
   4: 0100007F:E1F5 0A00020F:01BB 08 00000000:00000000 00:00000000 00000000  1000
"""


def parse_states(text: str) -> dict[str, int]:
    """Count sockets by state name from a /proc/net/tcp body (incl. header line)."""
    counts: dict[str, int] = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if not parts or parts[0] == "sl" or not parts[0].rstrip(":").isdigit():
            continue  # skip header / malformed
        st = parts[3].upper()  # the 'st' column
        name = TCP_STATES.get(st, f"UNKNOWN({st})")
        counts[name] = counts.get(name, 0) + 1
    return counts


def read_proc_states() -> dict[str, int] | None:
    total: dict[str, int] = {}
    found = False
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                found = True
                for k, v in parse_states(f.read()).items():
                    total[k] = total.get(k, 0) + v
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return total if found else None


def format_summary(counts: dict[str, int]) -> str:
    lines = []
    total = sum(counts.values())
    for name in sorted(counts, key=lambda k: -counts[k]):
        n = counts[name]
        flag = "  <== HIGH" if name in WARN and n >= WARN[name] else ""
        lines.append(f"  {name:<14} {n:>8}{flag}")
    lines.append(f"  {'TOTAL':<14} {total:>8}")
    return "\n".join(lines)


def selftest() -> None:
    print("=== socket_state_summary self-test ===")
    counts = parse_states(SAMPLE)
    assert counts["LISTEN"] == 1, counts
    assert counts["ESTABLISHED"] == 1, counts
    assert counts["TIME_WAIT"] == 2, counts
    assert counts["CLOSE_WAIT"] == 1, counts
    assert sum(counts.values()) == 5, counts
    # A CLOSE_WAIT pileup should flag (simulate 600 of them).
    pileup = {"CLOSE_WAIT": 600, "ESTABLISHED": 10}
    assert pileup["CLOSE_WAIT"] >= WARN["CLOSE_WAIT"]
    print(format_summary(counts))
    print("\nAll assertions passed. OK")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    counts = read_proc_states()
    if counts is None:
        print("/proc/net/tcp not available (Linux only). "
              "Showing the self-test on sample data instead.\n")
        selftest()
        return
    print("TCP socket states (from /proc/net/tcp + tcp6):\n")
    print(format_summary(counts))


if __name__ == "__main__":
    main()
