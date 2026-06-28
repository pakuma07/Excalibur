#!/usr/bin/env python3
"""
port_exhaustion_probe.py - estimate how close a host is to EPHEMERAL PORT
exhaustion, the cause of "Cannot assign requested address" (EADDRNOTAVAIL).

Each OUTBOUND connection consumes an ephemeral port from a ~28k pool
(net.ipv4.ip_local_port_range). A high rate of short-lived connections to one
destination piles sockets into TIME_WAIT (held ~60s) and exhausts the pool.
See: enterprise_scenarios/04_network_incidents.md (4.1); comp_networking/
04_transport_tcp_udp.md (TIME_WAIT). The real fix is connection pooling/keep-alive.

This probe reads the ephemeral range and counts ephemeral ports currently in use
(ESTABLISHED + TIME_WAIT etc.), per remote endpoint, and reports utilization.

Run (Linux):  python3 port_exhaustion_probe.py
Run (any OS): python3 port_exhaustion_probe.py --selftest
"""
from __future__ import annotations
import sys

DEFAULT_RANGE = (32768, 60999)  # typical Linux default ip_local_port_range
WARN_UTIL = 0.80                 # flag at 80% of the ephemeral pool used

# Sample /proc/net/tcp body: many TIME_WAIT(06) outbound conns to one peer
# (0A00020F:01BB = 10.0.2.15:443), simulating churn toward an HTTPS endpoint.
_BASE = "   {i}: 0100007F:{lp:04X} 0A00020F:01BB 06 0 0\n"
SAMPLE = "  sl local rem st\n" + "".join(
    _BASE.format(i=i, lp=40000 + i) for i in range(2500)
) + "   x: 0100007F:1538 00000000:0000 0A 0 0\n"  # one LISTEN (ignored)


def read_port_range() -> tuple[int, int]:
    try:
        with open("/proc/sys/net/ipv4/ip_local_port_range") as f:
            lo, hi = f.read().split()
            return int(lo), int(hi)
    except (FileNotFoundError, PermissionError, OSError):
        return DEFAULT_RANGE


def ephemeral_ports_in_use(text: str, lo: int, hi: int) -> tuple[int, dict[str, int]]:
    """Count local ports within [lo,hi] in use, and tally by remote endpoint."""
    used_ports: set[int] = set()
    per_remote: dict[str, int] = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].rstrip(":").isdigit():
            continue
        local, remote, st = parts[1], parts[2], parts[3].upper()
        if st == "0A":  # LISTEN sockets don't consume an ephemeral outbound port
            continue
        try:
            lport = int(local.split(":")[1], 16)
        except (IndexError, ValueError):
            continue
        if lo <= lport <= hi:
            used_ports.add(lport)
            per_remote[remote] = per_remote.get(remote, 0) + 1
    return len(used_ports), per_remote


def report(text: str, lo: int, hi: int) -> None:
    pool = hi - lo + 1
    used, per_remote = ephemeral_ports_in_use(text, lo, hi)
    util = used / pool if pool else 0.0
    flag = "  <== NEAR EXHAUSTION" if util >= WARN_UTIL else ""
    print(f"  ephemeral range : {lo}-{hi}  (pool = {pool} ports)")
    print(f"  ports in use    : {used}  ({util*100:.1f}%){flag}")
    if per_remote:
        top = sorted(per_remote.items(), key=lambda kv: -kv[1])[:3]
        print("  top remote endpoints by connection count (churn suspects):")
        for rem, n in top:
            print(f"    {rem:<22} {n}")
    if util >= WARN_UTIL:
        print("\n  FIX: enable connection pooling/keep-alive to the hot endpoint; "
              "widen\n       ip_local_port_range; set net.ipv4.tcp_tw_reuse=1 "
              "(never tcp_tw_recycle).")


def selftest() -> None:
    print("=== port_exhaustion_probe self-test ===")
    lo, hi = DEFAULT_RANGE
    used, per_remote = ephemeral_ports_in_use(SAMPLE, lo, hi)
    assert used == 2500, used                       # 2500 distinct ephemeral ports
    assert per_remote["0A00020F:01BB"] == 2500      # all churn to one peer
    assert "00000000:0000" not in per_remote        # LISTEN excluded
    # A widened range lowers utilization for the same usage:
    pool_default = hi - lo + 1
    pool_wide = 65535 - 1024 + 1
    assert (used / pool_wide) < (used / pool_default)
    report(SAMPLE, lo, hi)
    print("\nAll assertions passed. OK")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    try:
        with open("/proc/net/tcp") as f:
            body = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        print("/proc/net/tcp not available (Linux only). "
              "Showing the self-test on sample data instead.\n")
        selftest()
        return
    lo, hi = read_port_range()
    print("Ephemeral port utilization (outbound connections):\n")
    report(body, lo, hi)


if __name__ == "__main__":
    main()
