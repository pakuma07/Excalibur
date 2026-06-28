# Operating Systems 🖥️

A from-scratch-to-deep-internals operating-systems reference for **staff/principal engineers**. Every doc starts from first principles and ends in the kind of detail you need to reason about production behavior — page-fault costs, scheduler latency, lock contention, I/O models, container internals — not vibes.

The focus is **Linux** (the OS that actually runs production), with cross-references to POSIX, the kernel, and the seminal texts.

---

## 📚 Concept docs

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Processes & Threads](01_processes_threads.md) | Address space, `task_struct`/PCB, states, fork/exec/wait, copy-on-write, zombies/orphans, threads vs processes, 1:1/N:1/M:N, TLS, signals, daemons |
| 02 | [CPU Scheduling](02_cpu_scheduling.md) | FCFS/SJF/SRTF/RR/priority/MLFQ (worked Gantt), Linux **CFS** & **EEVDF**, real-time (FIFO/RR/DEADLINE, RMS/EDF), priority inversion + inheritance (Mars Pathfinder), affinity/NUMA, cgroup `cpu.weight`/`cpu.max` |
| 03 | [Memory Management](03_memory_management.md) | Virtual memory, multi-level paging, MMU/TLB & shootdowns, page faults, demand paging, replacement (FIFO/LRU/Clock/OPT, Belady), working set, allocators (ptmalloc/tcmalloc/jemalloc), huge pages, NUMA, page cache, OOM killer |
| 04 | [Concurrency & Synchronization](04_concurrency_synchronization.md) | Races/critical sections, mutex vs spinlock, semaphores, condition variables, classic problems, deadlock (Coffman, Banker's), the C11 memory model, false sharing, lock-free/CAS/ABA, futexes, RCU, the GIL |
| 05 | [File Systems & Storage](05_file_systems_storage.md) | fd/OFD/inode tables, VFS, journaling (ext4), CoW (ZFS/Btrfs), page cache & write-back, **fsync & fsyncgate**, O_DIRECT, I/O schedulers, RAID, LVM, HDD/SSD/NVMe, NFS/object storage |
| 06 | [I/O, Interrupts & Async I/O](06_io_models_async.md) | Syscall mechanics & cost, interrupts vs polling, DMA, blocking/non-blocking/async, the **C10K problem**, select/poll/**epoll**/kqueue (edge vs level), **io_uring**, zero-copy (sendfile/splice) |
| 07 | [Virtualization & Containers](07_virtualization_containers.md) | Popek-Goldberg, full vs para vs HW-assisted virt (VT-x/EPT), type-1/2 hypervisors, **namespaces + cgroups + overlayfs** (build a container from scratch), OCI/runc/containerd, microVMs (Firecracker), gVisor, seccomp |
| 08 | [Linux Internals & Observability](08_linux_internals_observability.md) | Boot chain + systemd, `/proc` & `/sys`, the **USE method**, the full toolset (perf/strace/ftrace/**bpftrace**), flame graphs, bottleneck diagnosis, sysctl/ulimits, a "the server is slow" triage runbook |

---

## 🛠️ Working enterprise examples

Runnable, self-verifying programs that solve real production problems — see [`examples/`](examples/README.md). Each runs on Windows (Python 3.11) and prints clear output with `assert`-based self-checks:

`worker_pool.py` · `producer_consumer.py` · `rate_limiter.py` · `circuit_breaker.py` · `lru_cache.py` · `deadlock_demo.py` · `graceful_shutdown.py` · `supervisor.py`

```bash
cd examples
py worker_pool.py
```

---

## 🎯 The recurring OS trade-offs

- **Throughput vs latency** — batching and buffering raise throughput but add latency (scheduling, Nagle, write-back).
- **Concurrency model** — thread-per-connection (simple, costly) vs event loop (scalable, complex) vs async I/O (io_uring).
- **Amplification** — every layer (page cache, journaling, COW, SSD FTL) trades read/write/space cost.
- **Isolation vs density** — VMs (strong isolation, heavy) vs containers (light, shared kernel) vs microVMs (the middle).
- **Mechanism vs policy** — the kernel provides mechanisms (paging, scheduling classes); you set policy (cgroup limits, niceness, affinity).

> The OS is the layer everything else stands on. At staff/principal level you are expected to explain *why* a service is slow from first principles — and the answer is almost always here: a scheduler decision, a page fault, lock contention, or an I/O stall.

When one of these goes wrong in production, see [`../enterprise_scenarios/`](../enterprise_scenarios/README.md) for the incident runbooks — CFS throttling, OOMKilled, NUMA/THP regressions ([01](../enterprise_scenarios/01_cpu_memory_incidents.md)), fsync/disk stalls ([02](../enterprise_scenarios/02_io_storage_incidents.md)), and deadlock/convoy/pool exhaustion ([03](../enterprise_scenarios/03_concurrency_incidents.md)) — each as `symptom → triage → root cause → fix → prevention`.

Related: see [`../comp_networking/`](../comp_networking/README.md) for the network stack and [`../../system_design/`](../../system_design/README.md) for distributed-systems architecture.
