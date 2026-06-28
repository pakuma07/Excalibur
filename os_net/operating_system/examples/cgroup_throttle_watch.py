#!/usr/bin/env python3
"""
cgroup_throttle_watch.py - detect CFS CPU throttling and cgroup OOM kills, the
two most common "looks healthy but isn't" container incidents.

CFS throttling (cpu.max quota) freezes ALL threads for the rest of a ~100ms
period once the quota is spent, producing tens-of-ms p99 spikes while AVERAGE
CPU looks low. The smoking gun is cpu.stat: nr_throttled / nr_periods. See:
  - operating_system/02_cpu_scheduling.md  (§10.3 the CFS throttling trap)
  - enterprise_scenarios/01_cpu_memory_incidents.md  (1.1, 1.5)

cgroup v2 files:
  cpu.stat        -> usage_usec / nr_periods / nr_throttled / throttled_usec
  memory.events   -> low / high / max / oom / oom_kill (counters)
  memory.current  -> current bytes ;  memory.max -> limit (or "max")

Run (Linux, in a cgroup): python3 cgroup_throttle_watch.py
Run (any OS):             python3 cgroup_throttle_watch.py --selftest
"""
from __future__ import annotations
import sys
import os
import time

CGV2 = "/sys/fs/cgroup"  # cgroup v2 unified mount
THROTTLE_ALERT_RATIO = 0.01  # >1% of periods throttled is worth investigating

SAMPLE_CPU_STAT = (
    "usage_usec 5123456\n"
    "user_usec 4000000\n"
    "system_usec 1123456\n"
    "nr_periods 12000\n"
    "nr_throttled 4200\n"        # 35% of periods throttled -> BAD
    "throttled_usec 870000000\n"
)
SAMPLE_MEM_EVENTS = "low 0\nhigh 1500\nmax 12\noom 2\noom_kill 2\n"


def parse_kv(text: str) -> dict[str, int]:
    """Parse 'key value' lines (cpu.stat, memory.events) into ints."""
    out: dict[str, int] = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def throttle_ratio(cpu_stat: dict[str, int]) -> float:
    periods = cpu_stat.get("nr_periods", 0)
    return cpu_stat.get("nr_throttled", 0) / periods if periods else 0.0


def read_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def report_once(prev_cpu: dict[str, int] | None) -> dict[str, int]:
    cpu_raw = read_file(os.path.join(CGV2, "cpu.stat"))
    cpu = parse_kv(cpu_raw) if cpu_raw else {}
    if cpu:
        ratio = throttle_ratio(cpu)
        flag = "  <== THROTTLED" if ratio > THROTTLE_ALERT_RATIO else ""
        # Delta since last sample, if available.
        if prev_cpu and prev_cpu.get("nr_periods"):
            d_periods = cpu.get("nr_periods", 0) - prev_cpu["nr_periods"]
            d_throttled = cpu.get("nr_throttled", 0) - prev_cpu["nr_throttled"]
            d_ratio = d_throttled / d_periods if d_periods else 0.0
            print(f"  cpu: throttled {ratio*100:5.1f}% lifetime, "
                  f"{d_ratio*100:5.1f}% last interval{flag}")
        else:
            print(f"  cpu: throttled {ratio*100:5.1f}% of periods "
                  f"({cpu.get('nr_throttled')}/{cpu.get('nr_periods')}){flag}")
    else:
        print("  cpu: cpu.stat unavailable")

    mem_raw = read_file(os.path.join(CGV2, "memory.events"))
    mem = parse_kv(mem_raw) if mem_raw else {}
    if mem:
        oom = mem.get("oom_kill", 0)
        high = mem.get("high", 0)
        flag = "  <== OOM-KILLED" if oom else ("  <== mem.high throttling" if high else "")
        print(f"  mem: oom_kill={oom} high_events={high}{flag}")
    return cpu


def watch(interval: float = 2.0) -> None:
    print("Watching cgroup throttling/OOM (Ctrl-C to stop).\n")
    prev = None
    try:
        while True:
            print(f"[{time.strftime('%H:%M:%S')}]")
            prev = report_once(prev)
            print()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped.")


def selftest() -> None:
    print("=== cgroup_throttle_watch self-test ===")
    cpu = parse_kv(SAMPLE_CPU_STAT)
    assert cpu["nr_periods"] == 12000 and cpu["nr_throttled"] == 4200
    r = throttle_ratio(cpu)
    assert abs(r - 0.35) < 1e-9, r
    assert r > THROTTLE_ALERT_RATIO  # would alert

    mem = parse_kv(SAMPLE_MEM_EVENTS)
    assert mem["oom_kill"] == 2 and mem["high"] == 1500

    # A healthy cgroup: no throttling, no OOM.
    healthy = parse_kv("nr_periods 1000\nnr_throttled 0\n")
    assert throttle_ratio(healthy) == 0.0
    assert throttle_ratio(healthy) <= THROTTLE_ALERT_RATIO  # would NOT alert

    print(f"  sample throttle ratio = {r*100:.1f}%  -> ALERT (> {THROTTLE_ALERT_RATIO*100:g}%)")
    print(f"  sample oom_kill        = {mem['oom_kill']}  -> ALERT")
    print("\nAll assertions passed. OK")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    if not sys.platform.startswith("linux") or not os.path.exists(os.path.join(CGV2, "cpu.stat")):
        print("cgroup v2 cpu.stat not found (Linux + cgroup v2 only). "
              "Showing the self-test on sample data instead.\n")
        selftest()
        return
    watch()


if __name__ == "__main__":
    main()
