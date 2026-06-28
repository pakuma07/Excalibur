#!/usr/bin/env python3
"""
tcp_retrans_monitor.py - compute the TCP retransmission rate over an interval
from /proc/net/snmp, the host-wide signal for packet loss.

Retransmits cost a round trip (fast retransmit) or ~200ms+ (RTO timeout) and are
the usual cause of latency spikes + throughput collapse on a bad path. See:
  - comp_networking/04_transport_tcp_udp.md  (reliable delivery, congestion)
  - comp_networking/08_network_performance_tuning.md  (diagnosing loss)
  - enterprise_scenarios/04_network_incidents.md  (4.3 retransmission storms)

Retransmit RATE = RetransSegs / OutSegs over an interval. A healthy LAN is well
under 0.1%; sustained >1% means a lossy path (find the hop with `mtr`).

/proc/net/snmp has paired header/value lines, e.g.:
    Tcp: ... OutSegs RetransSegs ...
    Tcp: ... 1000000 1500 ...

Run (Linux):  python3 tcp_retrans_monitor.py [--interval 5 --count 6]
Run (any OS): python3 tcp_retrans_monitor.py --selftest
"""
from __future__ import annotations
import sys
import time

WARN_RATE = 0.01  # 1% retransmit rate over the interval is a problem

SAMPLE_T0 = ("Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens "
             "AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts\n"
             "Tcp: 1 200 120000 -1 50 40 0 5 12 9000000 8000000 8000 0 100\n")
# 5s later: +500000 OutSegs, +25000 RetransSegs -> 5% interval rate (bad).
SAMPLE_T1 = ("Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens "
             "AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts\n"
             "Tcp: 1 200 120000 -1 52 41 0 5 14 9400000 8500000 33000 0 100\n")


def parse_snmp_tcp(text: str) -> dict[str, int]:
    """Return the Tcp: counters as a dict from a /proc/net/snmp body."""
    lines = [ln for ln in text.splitlines() if ln.startswith("Tcp:")]
    if len(lines) < 2:
        return {}
    headers = lines[0].split()[1:]
    values = lines[1].split()[1:]
    out: dict[str, int] = {}
    for h, v in zip(headers, values):
        try:
            out[h] = int(v)
        except ValueError:
            pass
    return out


def retrans_rate(prev: dict[str, int], cur: dict[str, int]) -> tuple[int, int, float]:
    d_out = cur.get("OutSegs", 0) - prev.get("OutSegs", 0)
    d_re = cur.get("RetransSegs", 0) - prev.get("RetransSegs", 0)
    rate = d_re / d_out if d_out > 0 else 0.0
    return d_re, d_out, rate


def read_snmp() -> dict[str, int] | None:
    try:
        with open("/proc/net/snmp") as f:
            return parse_snmp_tcp(f.read())
    except (FileNotFoundError, PermissionError, OSError):
        return None


def monitor(interval: float, count: int) -> None:
    prev = read_snmp()
    if not prev:
        print("/proc/net/snmp unavailable; running self-test.\n")
        selftest()
        return
    print(f"TCP retransmit rate every {interval}s (warn > {WARN_RATE*100:g}%):\n")
    for _ in range(count):
        time.sleep(interval)
        cur = read_snmp() or {}
        d_re, d_out, rate = retrans_rate(prev, cur)
        flag = "  <== LOSSY PATH" if rate >= WARN_RATE else ""
        print(f"  [{time.strftime('%H:%M:%S')}] retrans {d_re}/{d_out} segs "
              f"= {rate*100:5.2f}%{flag}")
        prev = cur


def selftest() -> None:
    print("=== tcp_retrans_monitor self-test ===")
    t0 = parse_snmp_tcp(SAMPLE_T0)
    t1 = parse_snmp_tcp(SAMPLE_T1)
    assert t0["OutSegs"] == 8000000 and t0["RetransSegs"] == 8000, t0
    d_re, d_out, rate = retrans_rate(t0, t1)
    assert d_out == 500000 and d_re == 25000, (d_out, d_re)
    assert abs(rate - 0.05) < 1e-9, rate           # 5% interval retransmit rate
    assert rate >= WARN_RATE                         # would flag a lossy path
    # A healthy interval: tiny retransmits.
    healthy_next = dict(t0)
    healthy_next["OutSegs"] += 1000000
    healthy_next["RetransSegs"] += 200              # 0.02%
    _, _, hr = retrans_rate(t0, healthy_next)
    assert hr < WARN_RATE
    print(f"  interval retransmit rate = {rate*100:.2f}%  -> ALERT (lossy)")
    print(f"  healthy interval rate    = {hr*100:.3f}% -> ok")
    print("\nAll assertions passed. OK")


def main() -> None:
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return
    interval = 5.0
    count = 6
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])
    if "--count" in args:
        count = int(args[args.index("--count") + 1])
    if read_snmp() is None:
        print("/proc/net/snmp not available (Linux only). "
              "Showing the self-test on sample data instead.\n")
        selftest()
        return
    monitor(interval, count)


if __name__ == "__main__":
    main()
