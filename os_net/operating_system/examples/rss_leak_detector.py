#!/usr/bin/env python3
"""
rss_leak_detector.py - sample a process's RSS over time and classify the trend
as LEAK (monotonic growth), GROWTH-then-PLATEAU (legit working set), or STABLE.

The staff-level question behind OOMKilled (enterprise_scenarios/01 §1.5-1.6) is:
is RSS rising forever (a leak) or climbing to a plateau (a normal working set)?
The discriminator is the SLOPE and whether it flattens. This script fits a simple
linear trend to RSS samples and reports MB/min, plus an R^2-ish flatness check.

  - operating_system/03_memory_management.md  (RSS/PSS, allocators, OOM)
  - enterprise_scenarios/01_cpu_memory_incidents.md  (1.5 OOMKilled, 1.6 leak vs growth)

Run (Linux):  python3 rss_leak_detector.py <pid> [--interval 5 --count 12]
Run (any OS): python3 rss_leak_detector.py --selftest
"""
from __future__ import annotations
import sys
import time


def read_rss_kb(pid: int) -> int | None:
    """RSS in KiB from /proc/<pid>/status (VmRSS). Linux only."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None
    return None


def linfit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares slope, intercept for y = slope*x + intercept."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs) or 1e-9
    slope = num / den
    return slope, my - slope * mx


def classify(times_s: list[float], rss_mb: list[float]) -> dict:
    """Classify the RSS trend. Compares the slope of the first vs second half:
    a true leak keeps the same (or rising) slope; a plateau's slope -> ~0."""
    slope_all, _ = linfit(times_s, rss_mb)          # MB per second
    mb_per_min = slope_all * 60.0
    half = len(times_s) // 2
    slope_first, _ = linfit(times_s[:half] or times_s, rss_mb[:half] or rss_mb)
    slope_second, _ = linfit(times_s[half:] or times_s, rss_mb[half:] or rss_mb)

    rng = max(rss_mb) - min(rss_mb)
    if rng < 1.0:
        verdict = "STABLE (flat)"
    elif slope_second <= slope_first * 0.3:
        verdict = "GROWTH then PLATEAU (likely legit working set)"
    else:
        verdict = "LEAK SUSPECTED (sustained growth)"
    return {
        "mb_per_min": mb_per_min,
        "slope_first_half": slope_first * 60.0,
        "slope_second_half": slope_second * 60.0,
        "range_mb": rng,
        "verdict": verdict,
    }


def monitor(pid: int, interval: float, count: int) -> None:
    print(f"Sampling RSS of pid {pid} every {interval}s x{count} ...\n")
    t0 = time.perf_counter()
    times_s, rss_mb = [], []
    for _ in range(count):
        kb = read_rss_kb(pid)
        if kb is None:
            print(f"  pid {pid} not readable (gone, or not Linux).")
            return
        t = time.perf_counter() - t0
        times_s.append(t)
        rss_mb.append(kb / 1024.0)
        print(f"  t={t:6.1f}s  RSS={kb/1024.0:8.1f} MB")
        time.sleep(interval)
    print()
    _print_verdict(classify(times_s, rss_mb))


def _print_verdict(r: dict) -> None:
    print(f"  overall trend : {r['mb_per_min']:+.2f} MB/min")
    print(f"  first half    : {r['slope_first_half']:+.2f} MB/min")
    print(f"  second half   : {r['slope_second_half']:+.2f} MB/min")
    print(f"  RSS range     : {r['range_mb']:.1f} MB")
    print(f"  VERDICT       : {r['verdict']}")


def selftest() -> None:
    print("=== rss_leak_detector self-test ===")
    t = [float(i) for i in range(12)]  # 0..11 (treat as seconds)

    # A leak: linear growth that never flattens.
    leak = [100 + 5 * i for i in range(12)]
    rl = classify(t, leak)
    assert "LEAK" in rl["verdict"], rl
    assert rl["mb_per_min"] > 0

    # Growth then plateau: rises, then flattens in the second half.
    plat = [100 + 10 * i for i in range(6)] + [160] * 6
    rp = classify(t, plat)
    assert "PLATEAU" in rp["verdict"], rp

    # Stable: noise around a constant.
    stable = [200.0, 200.2, 199.9, 200.1, 200.0, 199.8,
              200.1, 200.0, 199.9, 200.2, 200.0, 199.9]
    rs = classify(t, stable)
    assert "STABLE" in rs["verdict"], rs

    print("  leak   sample ->", rl["verdict"])
    print("  plateau sample ->", rp["verdict"])
    print("  stable sample ->", rs["verdict"])
    print("\nAll assertions passed. OK")


def main() -> None:
    args = sys.argv[1:]
    if "--selftest" in args or not args:
        if not args:
            print("(no pid given) running self-test; pass a PID on Linux to "
                  "monitor a real process.\n")
        selftest()
        return
    pid = int(args[0])
    interval = 5.0
    count = 12
    if "--interval" in args:
        interval = float(args[args.index("--interval") + 1])
    if "--count" in args:
        count = int(args[args.index("--count") + 1])
    monitor(pid, interval, count)


if __name__ == "__main__":
    main()
