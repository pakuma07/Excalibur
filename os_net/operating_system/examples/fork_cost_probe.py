#!/usr/bin/env python3
"""
fork_cost_probe.py - measure how fork() latency grows with a process's resident
memory, because fork() must copy the PAGE TABLES synchronously even though COW
defers the data copy.

This is the root of "Redis BGSAVE/fork latency spikes": a large-RSS process
stalls on fork() while the kernel duplicates page tables (~proportional to mapped
memory). See:
  - operating_system/01_processes_threads.md  (§6 COW, §16 fork hazards at scale)
  - operating_system/03_memory_management.md   (page tables, huge pages)

The probe allocates increasing amounts of memory, TOUCHES it (so pages are really
mapped, not lazily reserved), then times fork() at each size and shows the trend.

Run (Linux/macOS): python3 fork_cost_probe.py
Run (Windows/any): python3 fork_cost_probe.py --selftest   (no os.fork on Windows)
"""
from __future__ import annotations
import os
import sys
import time


def time_fork_with_rss(mb: int) -> float:
    """Allocate+touch `mb` MiB, then time a single fork(); child exits at once.
    Returns fork() latency in milliseconds (parent side)."""
    # Allocate and TOUCH every page (bytearray is contiguous; write one byte per 4K).
    buf = bytearray(mb * 1024 * 1024)
    for i in range(0, len(buf), 4096):
        buf[i] = 1  # fault the page in -> it counts toward RSS and page tables
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child: do nothing, exit immediately (async-signal-safe)
    dt = (time.perf_counter() - t0) * 1000.0  # parent measures fork() return latency
    os.waitpid(pid, 0)  # reap (avoid a zombie, see §7)
    del buf
    return dt


def run_probe() -> None:
    print("Measuring fork() latency vs resident memory (touched pages).")
    print("Expect latency to rise with RSS: fork copies page tables synchronously.\n")
    print(f"  {'RSS (MB)':>9} {'fork() latency (ms)':>22}")
    print("  " + "-" * 33)
    results = []
    for mb in (1, 16, 64, 256, 512):
        # median of a few runs to reduce noise
        samples = sorted(time_fork_with_rss(mb) for _ in range(3))
        med = samples[1]
        results.append((mb, med))
        print(f"  {mb:>9} {med:>22.3f}")
    if len(results) >= 2 and results[0][1] > 0:
        growth = results[-1][1] / results[0][1]
        print(f"\n  fork() latency grew ~{growth:.1f}x from {results[0][0]}MB "
              f"to {results[-1][0]}MB of touched memory.")
    print("\nMitigations for large-heap fork stalls: huge pages (smaller page "
          "tables),\nmadvise(MADV_DONTFORK) for regions the child won't need, or "
          "avoid fork-based\nsnapshots entirely for very large in-memory stores.")


def selftest() -> None:
    print("=== fork_cost_probe self-test (logic only; no fork) ===")
    # Verify the page-touch loop maps the expected number of pages.
    mb = 2
    buf = bytearray(mb * 1024 * 1024)
    touched = 0
    for i in range(0, len(buf), 4096):
        buf[i] = 1
        touched += 1
    assert touched == (mb * 1024 * 1024) // 4096 == 512, touched
    # Model check: page-table bytes scale ~linearly with mapped pages (8 bytes/PTE
    # at the leaf level), so fork's PTE-copy work is ~O(RSS). Assert the model.
    def pte_bytes(mb_):
        pages = mb_ * 1024 * 1024 // 4096
        return pages * 8  # 8 bytes per leaf PTE (x86-64), ignoring upper levels
    assert pte_bytes(512) == 512 * pte_bytes(1), "PTE work is linear in RSS"
    print(f"  touched {touched} pages for {mb}MB (4KiB pages) OK")
    print(f"  leaf PTE bytes: 1MB -> {pte_bytes(1)}, 512MB -> {pte_bytes(512)} "
          f"({pte_bytes(512)//pte_bytes(1)}x) OK")
    print("\nAll assertions passed. OK  (Run on Linux/macOS without --selftest to "
          "time real fork().)")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    if not hasattr(os, "fork"):
        print("os.fork() is unavailable on this platform (Windows). "
              "Showing the self-test instead.\n")
        selftest()
        return
    run_probe()


if __name__ == "__main__":
    main()
