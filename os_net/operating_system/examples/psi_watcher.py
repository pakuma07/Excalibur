#!/usr/bin/env python3
"""
psi_watcher.py - watch Linux Pressure Stall Information (PSI) and alert when a
resource is causing stalls.

PSI (/proc/pressure/{cpu,memory,io}) is the single best "which resource is the
bottleneck?" signal: it reports the % of time tasks were STALLED waiting on a
resource, not just utilization. A box can be 100% CPU-busy with zero pressure
(healthy) or 60% busy with high pressure (saturated). See:
  - operating_system/08_linux_internals_observability.md  (USE method, PSI)
  - operating_system/03_memory_management.md  (memory.pressure)
  - enterprise_scenarios/01_cpu_memory_incidents.md / 02_io_storage_incidents.md

Each PSI line looks like:
    some avg10=1.23 avg60=0.50 avg300=0.10 total=123456789
    full avg10=0.80 avg60=0.30 avg300=0.05 total=98765432
"some" = at least one task stalled; "full" = ALL non-idle tasks stalled (the
sharpest "I am losing time on this resource" signal). CPU has no meaningful
"full" line.

Run (Linux):     python3 psi_watcher.py            # watch live, 2s interval
Run (any OS):    python3 psi_watcher.py --selftest # parse sample data + asserts
"""
from __future__ import annotations
import sys
import time
import os

# Alert thresholds on the 10-second average (% of wall-clock time stalled).
THRESHOLDS = {"cpu": 20.0, "memory": 10.0, "io": 20.0}

# Sample data used on non-Linux / --selftest so the script always runs.
SAMPLE = {
    "cpu": "some avg10=35.20 avg60=12.10 avg300=4.30 total=987654321\n",
    "memory": (
        "some avg10=8.00 avg60=3.00 avg300=1.00 total=12345\n"
        "full avg10=6.50 avg60=2.00 avg300=0.50 total=9876\n"
    ),
    "io": (
        "some avg10=42.00 avg60=18.00 avg300=5.00 total=555\n"
        "full avg10=30.00 avg60=10.00 avg300=2.00 total=333\n"
    ),
}


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    """Parse one /proc/pressure/<res> file body into {'some': {...}, 'full': {...}}."""
    out: dict[str, dict[str, float]] = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]  # 'some' or 'full'
        fields = {}
        for kv in parts[1:]:
            k, _, v = kv.partition("=")
            fields[k] = float(v) if "." in v or k.startswith("avg") else int(v)
        out[kind] = fields
    return out


def read_psi(resource: str) -> dict[str, dict[str, float]] | None:
    path = f"/proc/pressure/{resource}"
    try:
        with open(path) as f:
            return parse_psi(f.read())
    except (FileNotFoundError, PermissionError, OSError):
        return None


def worst_avg10(parsed: dict[str, dict[str, float]]) -> float:
    """The 'full' avg10 if present (sharper), else 'some' avg10."""
    if "full" in parsed:
        return parsed["full"].get("avg10", 0.0)
    return parsed.get("some", {}).get("avg10", 0.0)


def format_line(resource: str, parsed: dict[str, dict[str, float]]) -> str:
    metric = worst_avg10(parsed)
    threshold = THRESHOLDS[resource]
    flag = "  <== PRESSURE" if metric >= threshold else ""
    some = parsed.get("some", {}).get("avg10", 0.0)
    full = parsed.get("full", {}).get("avg10")
    full_s = f" full={full:5.1f}" if full is not None else "            "
    return f"  {resource:<6} some={some:5.1f}{full_s}  (alert>{threshold:g}){flag}"


def watch(interval: float = 2.0) -> None:
    print("Watching PSI (Ctrl-C to stop). avg10 = % of last 10s spent stalled.\n")
    try:
        while True:
            print(f"[{time.strftime('%H:%M:%S')}]")
            for res in ("cpu", "memory", "io"):
                parsed = read_psi(res)
                if parsed is None:
                    print(f"  {res:<6} (unavailable)")
                else:
                    print(format_line(res, parsed))
            print()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped.")


def selftest() -> None:
    print("=== psi_watcher self-test (parsing on sample data) ===")
    cpu = parse_psi(SAMPLE["cpu"])
    assert cpu["some"]["avg10"] == 35.20, cpu
    assert "full" not in cpu, "cpu has no 'full' line"
    assert worst_avg10(cpu) == 35.20

    mem = parse_psi(SAMPLE["memory"])
    assert mem["full"]["avg10"] == 6.50, mem
    assert worst_avg10(mem) == 6.50  # uses 'full' when present

    io = parse_psi(SAMPLE["io"])
    assert worst_avg10(io) == 30.00
    # io 'full' avg10 (30) exceeds threshold (20) -> would alert
    assert worst_avg10(io) >= THRESHOLDS["io"]
    # memory 'full' avg10 (6.5) is below threshold (10) -> would NOT alert
    assert worst_avg10(mem) < THRESHOLDS["memory"]

    for res in ("cpu", "memory", "io"):
        print(format_line(res, parse_psi(SAMPLE[res])))
    print("\nAll assertions passed. OK")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    if not sys.platform.startswith("linux") or not os.path.exists("/proc/pressure"):
        print("PSI is Linux-only (kernel >= 4.20 with CONFIG_PSI). "
              "Showing the self-test on sample data instead.\n")
        selftest()
        return
    watch()


if __name__ == "__main__":
    main()
