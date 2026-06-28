# Examples — OS concepts solved at enterprise scale

> **Audience:** staff/principal. These are small, **standalone, runnable** programs that take a single OS concept from the companion docs and show the *production* version of it — the worker pool behind Gunicorn, the rate limiter every API gateway needs, the circuit breaker that stops a retry storm, the supervisor that keeps a fleet alive. Each file is heavily commented, prints clear output, and **self-verifies with `assert`s** so you can run it and trust it.
>
> **Environment:** written for Windows with Python 3.11 (`py` launcher / PowerShell), but every cross-platform file runs unmodified on Linux/macOS too. Files that demonstrate an inherently POSIX-only mechanism are marked in their header.

---

## How to run

Each file is independent — no third-party packages, stdlib only.

```powershell
py worker_pool.py            # or: python worker_pool.py
py producer_consumer.py
py rate_limiter.py
py circuit_breaker.py
py lru_cache.py
py deadlock_demo.py
py graceful_shutdown.py
py supervisor.py
```

Each prints what it is doing and ends with `All ... assertions passed.` A non-zero exit code or an `AssertionError` means something regressed.

> On Windows, the Bash tool's `python3` hits the Microsoft Store stub. Use PowerShell `python` or the `py` launcher.

---

## Index

| File | Enterprise problem it solves | Real-world analogues | OS concept doc |
|------|------------------------------|----------------------|----------------|
| [`worker_pool.py`](worker_pool.py) | Bounded concurrency with **backpressure** and graceful shutdown — never spawn unbounded threads under load. | Gunicorn/uWSGI workers, Celery concurrency, Java `ThreadPoolExecutor`, Go worker pools. | [01 — Processes & Threads](../01_processes_threads.md), [04 — Concurrency](../04_concurrency_synchronization.md) |
| [`producer_consumer.py`](producer_consumer.py) | Correct **bounded-buffer** producer/consumer with condition variables — no lost or duplicated items under contention. | Ingestion buffers, log flush threads, Kafka consumer → worker hand-off. | [04 — Concurrency](../04_concurrency_synchronization.md), [01 — Processes & Threads](../01_processes_threads.md) |
| [`rate_limiter.py`](rate_limiter.py) | Thread-safe **token-bucket** + **sliding-window** throttling — admission control / fairness. | nginx `limit_req`, AWS API Gateway, Stripe, Envoy. | [04 — Concurrency](../04_concurrency_synchronization.md), [02 — CPU Scheduling](../02_cpu_scheduling.md) |
| [`circuit_breaker.py`](circuit_breaker.py) | **CLOSED/OPEN/HALF_OPEN** breaker — fail fast on a flaky downstream and stop retry storms / cascading failure. | Netflix Hystrix, resilience4j, Polly, Istio/Envoy outlier detection. | [04 — Concurrency](../04_concurrency_synchronization.md), [06 — I/O Models & Async](../06_io_models_async.md) |
| [`lru_cache.py`](lru_cache.py) | **O(1) thread-safe LRU** cache (hashmap + doubly linked list) with eviction and hit/miss stats. | `functools.lru_cache`, Guava/Caffeine, the OS page cache (clock ≈ LRU). | [03 — Memory Management](../03_memory_management.md), [04 — Concurrency](../04_concurrency_synchronization.md) |
| [`deadlock_demo.py`](deadlock_demo.py) | Reproduces a **lock-ordering deadlock** and shows both fixes (global lock ordering; try-lock + backoff). | Money transfers, multi-row DB locks, any two-mutex code path. | [04 — Concurrency](../04_concurrency_synchronization.md) |
| [`graceful_shutdown.py`](graceful_shutdown.py) | Catch **SIGINT/SIGTERM**, stop accepting work, **drain** in-flight work, exit 0 — the 12-factor / k8s preStop pattern. | Kubernetes `terminationGracePeriodSeconds`, systemd stop, rolling deploys. | [01 — Processes & Threads](../01_processes_threads.md) (signals), [06 — I/O Models & Async](../06_io_models_async.md) |
| [`supervisor.py`](supervisor.py) | Spawn child **processes**, monitor, **restart crashed ones with exponential backoff** (crash-loop guard). | systemd, runit, supervisord, kubelet, Erlang/OTP supervisors. | [01 — Processes & Threads](../01_processes_threads.md) (fork/exec, reaping, crash isolation) |

---

## Diagnostic scripts (accompany the incident runbooks)

These read live kernel state on **Linux** (`/proc`, `/sys/fs/cgroup`) to diagnose
the failure modes in [`../../enterprise_scenarios/`](../../enterprise_scenarios/README.md).
On non-Linux they fall back to embedded **sample data** and run a `--selftest` that
asserts the parsing/decision logic, so they execute everywhere.

```powershell
py psi_watcher.py --selftest            # then, on Linux: py psi_watcher.py
py cgroup_throttle_watch.py --selftest
py rss_leak_detector.py --selftest      # on Linux: py rss_leak_detector.py <pid>
py fork_cost_probe.py --selftest        # on Linux/macOS: py fork_cost_probe.py
```

| File | What it diagnoses | Runbook |
|------|-------------------|---------|
| [`psi_watcher.py`](psi_watcher.py) | **PSI** pressure stalls on CPU/memory/IO — the sharpest "which resource is the bottleneck?" signal; alerts on `full avg10`. | [01](../../enterprise_scenarios/01_cpu_memory_incidents.md), [02](../../enterprise_scenarios/02_io_storage_incidents.md), [05](../../enterprise_scenarios/05_cross_layer_triage.md) |
| [`cgroup_throttle_watch.py`](cgroup_throttle_watch.py) | **CFS throttling** (`cpu.stat` `nr_throttled` ratio) and **cgroup OOM** (`memory.events`) — the "low avg CPU, periodic p99 spikes" mystery. | [01.1, 01.5](../../enterprise_scenarios/01_cpu_memory_incidents.md) |
| [`rss_leak_detector.py`](rss_leak_detector.py) | Classifies an RSS trend as **leak vs growth-then-plateau vs stable** by slope — leak-or-not before OOMKilled. | [01.5, 01.6](../../enterprise_scenarios/01_cpu_memory_incidents.md) |
| [`fork_cost_probe.py`](fork_cost_probe.py) | Measures **fork() latency vs RSS** (page-table copy cost) — the Redis `BGSAVE`/fork-stall class. | [01 §16](../01_processes_threads.md) |

---

## Cross-platform notes

- **All eight files run on Windows.** `supervisor.py` uses `multiprocessing` with the **`spawn`** start method (the Windows/macOS default), so its worker entry points are module-level functions and all spawning is guarded by `if __name__ == "__main__":` — required to avoid re-running the launcher in each child.
- **`graceful_shutdown.py`** registers only the signals that exist on the current platform (`SIGINT`/`SIGTERM` everywhere; `SIGHUP`/`SIGQUIT` guarded with `hasattr` for POSIX). It uses `signal.raise_signal()` to drive the handler deterministically on both Windows and POSIX. In a real terminal you can also press **Ctrl-C** to trigger the same drain.
- **`deadlock_demo.py`** deliberately *creates* a deadlock to show it, but every lock acquisition uses a **timeout**, so the program detects the stuck state and reports it instead of hanging — safe to run in CI.

---

## Verification status

All eight scripts run green on Windows 11 / Python 3.11 with every embedded `assert` passing. The cross-platform structure means they also run unmodified on Linux/macOS.
