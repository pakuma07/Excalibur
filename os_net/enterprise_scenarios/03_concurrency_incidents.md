# 03 — Concurrency Incidents

> **Audience:** staff/principal on-call. Each scenario: **Symptom → Triage → Root
> cause → Mitigate now → Permanent fix → Prevention.** Theory in
> [Concurrency & Synchronization](../operating_system/04_concurrency_synchronization.md).
> Runnable demos in [`../operating_system/examples/`](../operating_system/examples/README.md)
> (`deadlock_demo.py`, `producer_consumer.py`, `worker_pool.py`, `circuit_breaker.py`).

> Concurrency incidents are the hardest to diagnose because they are **timing-
> dependent and often non-reproducible**. The discipline: capture thread state
> *during* the incident (stacks, lock holders) — a postmortem without a thread dump
> is usually a dead end.

---

## 3.1 Deadlock — everything stops, CPU idle

**Symptom.** A service (or a subset of threads) hangs completely; throughput drops to
zero or a plateau; **CPU is idle** (deadlocked threads aren't spinning, they're
blocked). Requests time out.

**Triage.**
```bash
# Capture stacks of every thread NOW (the single most important artifact):
#   JVM:    jstack <pid>           (look for "Found 1 deadlock" / BLOCKED cycles)
#   Go:     SIGQUIT -> full goroutine dump; or pprof
#   native: gdb -p <pid> 'thread apply all bt'
#   Python: py-spy dump --pid <pid>
cat /proc/<pid>/task/*/stack       # kernel stacks of each thread
# Look for a CYCLE: thread A holds L1 waits L2; thread B holds L2 waits L1.
```

**Root cause.** Two+ threads each hold a lock the other needs → a cycle in the
wait-for graph. Requires all four **Coffman conditions**: mutual exclusion, hold-and-
wait, no preemption, circular wait. Classic trigger: acquiring locks in *different
orders* on different code paths.

**Mitigate now.** Restart the affected process to break the deadlock (it will recur
until fixed). If only some threads are stuck, the pool may drain — fail over.

**Permanent fix.** Break a Coffman condition — almost always **circular wait**:
- **Lock ordering:** establish a global order and always acquire in that order (the
  standard fix; enforce with a lock-ordering checker / static analysis).
- **Lock-free or single-lock:** reduce the number of locks held at once.
- **`try_lock` with timeout + backoff:** detect and back off rather than block forever.

**Prevention.** Lock-hierarchy discipline in code review; deadlock detectors in CI/
tests (TSan, Java's `ThreadMXBean.findDeadlockedThreads`); keep critical sections
small and avoid calling out (RPC, callbacks) while holding a lock. See
[Concurrency §deadlock](../operating_system/04_concurrency_synchronization.md) and
`deadlock_demo.py`.

---

## 3.2 Lock convoy — throughput collapses under load

**Symptom.** Throughput *drops* as concurrency *rises* (more threads = less work
done); CPU shows high `%sys` (context switches); a hot lock is the common factor.
Latency is bimodal.

**Triage.**
```bash
perf record -g -p <pid>; perf report     # time in lock/futex paths (futex_wait, __lll_lock)
pidstat -w 1                              # cswch/s (context switches) spiking
cat /proc/<pid>/status | grep ctxt        # voluntary vs involuntary switches
# eBPF: which lock, how long held:
# bpftrace -e 'tracepoint:syscalls:sys_enter_futex { @[comm] = count(); }'
```

**Root cause.** A **lock convoy**: many threads contend on one hot lock. Each
acquirer holds briefly, but the *handoff* (wake a waiter, context switch, scheduler
latency) costs far more than the work inside the lock. Threads queue up; the system
spends its time in futex/scheduler overhead, not work. Adding threads makes it worse.

**Mitigate now.** Reduce concurrency on the hot path (smaller pool); if a specific
lock, route around it.

**Permanent fix.**
- **Shrink the critical section** — do work outside the lock; lock only the mutation.
- **Shard the lock** — partition the data so contention spreads across many locks
  (e.g. striped locks, per-shard maps).
- **Lock-free / read-optimized** — `RCU`, read-write locks for read-heavy, atomics/CAS
  for counters (mind the **ABA** problem). Per-CPU/thread-local accumulation then
  merge.

**Prevention.** Profile lock contention under load before shipping; design for
sharding from the start; alert on context-switch rate. See
[Concurrency §contention, lock-free](../operating_system/04_concurrency_synchronization.md).

---

## 3.3 False sharing — a "scaling" regression with no contention

**Symptom.** A multithreaded routine doesn't scale (or *regresses*) with more cores,
despite no logical lock contention — threads touch *different* variables. Often
appears after an innocuous struct-layout change.

**Triage.**
```bash
perf c2c record ./app; perf c2c report   # THE tool: detects cache-line contention (HITM)
perf stat -e cache-misses,LLC-load-misses ./app
```
`perf c2c` shows two threads ping-ponging the same cache line ("HITM" — hit-modified).

**Root cause.** Two threads write to *different* variables that happen to live on the
**same 64-byte cache line**. Each write invalidates the other core's cached copy, so
the line ping-pongs across cores over the interconnect — coherence traffic kills
performance even though there's no logical sharing. Classic: an array of per-thread
counters packed tightly.

**Mitigate / fix.** Pad/align hot per-thread data to a cache line:
```c
// Each counter on its own cache line -> no false sharing.
struct alignas(64) Counter { std::atomic<long> v; };   // C++ (Chapter 24)
Counter counters[N];
// Or pad: char pad[64 - sizeof(long)];  // C
```

**Prevention.** Align/pad hot concurrent data structures; use
`std::hardware_destructive_interference_size`; run `perf c2c` on scaling-critical
code. A staff-level "why doesn't this scale?" instinct. See
[Concurrency §false sharing](../operating_system/04_concurrency_synchronization.md)
and [C++ Performance ch24].

---

## 3.4 Thundering herd / cache stampede — synchronized overload

**Symptom.** A spike of load that hits all at once — when a cache key expires, when a
dependency recovers, when many clients reconnect after a blip, or when a popular item
goes hot. The backend gets a synchronized flood and may collapse.

**Triage.**
```bash
# Correlate the spike with: cache TTL expiry, a dependency recovery, a deploy, a
# reconnect storm. Look at request-rate to the backend vs cache hit ratio.
ss -s                    # surge of new connections (reconnect herd)
# App metrics: cache miss rate spiking in lockstep with backend QPS
```

**Root cause.** Many actors do the same expensive thing simultaneously:
- **Cache stampede:** a hot key expires → thousands of concurrent misses recompute it
  at once.
- **Thundering herd (wakeups):** many threads/processes wake on one event and all race
  (historically `accept()`; mostly fixed by `EPOLLEXCLUSIVE`/`SO_REUSEPORT`).
- **Reconnect storm:** a dependency restarts → every client reconnects in the same
  instant.

**Mitigate now.** Add jitter to retries/TTLs; rate-limit/load-shed the backend; serve
stale while recomputing.

**Permanent fix.**
- **Single-flight:** collapse concurrent misses into *one* recompute, others wait for
  the result (see `circuit_breaker.py` and the language books' Production chapters).
- **Probabilistic early expiration / staggered TTLs:** refresh before expiry, with
  jitter, so keys don't expire in lockstep.
- **Exponential backoff with jitter** on all retries/reconnects (full jitter).
- `SO_REUSEPORT` / `EPOLLEXCLUSIVE` for accept herds.

**Prevention.** Never use a fixed TTL without jitter; bake single-flight into the
cache client; jittered backoff is a library default, not a per-site choice. See
[I/O §thundering herd](../operating_system/06_io_models_async.md).

---

## 3.5 Connection / thread-pool exhaustion — the cascading hang

**Symptom.** The service stops accepting work; requests queue then time out;
"connection pool exhausted" / "no available threads" errors. Often triggered by *one*
slow downstream dependency.

**Triage.**
```bash
# Where are the threads/connections stuck?
jstack <pid> | grep -A5 'pool'           # threads blocked in a downstream call
ss -tan state established | wc -l        # established conns; near the pool limit?
ss -tan state close-wait | wc -l         # CLOSE_WAIT pileup = app not closing sockets
# App metrics: pool active == pool max, queue depth climbing
```

**Root cause.** A bounded pool (DB connections, HTTP client, worker threads) drains
because each worker is **blocked on a slow dependency** (no timeout, or too long).
With no free workers, new requests queue and time out — one slow dependency
**cascades** into total unavailability. A `CLOSE_WAIT` pileup means the app isn't
closing sockets (fd/conn leak), exhausting the pool over time.

**Mitigate now.** Restart to reset the pool; cut traffic; bypass/disable the slow
dependency if optional.

**Permanent fix.**
- **Timeouts on every call** — the #1 fix. A blocked worker must time out and free
  itself (tighter than the client's own timeout).
- **Circuit breaker** — stop calling a dead dependency; fail fast and free the pool
  (`circuit_breaker.py`).
- **Bulkheads** — separate pools per dependency so one slow dependency can't drain the
  pool the others need.
- **Fix connection leaks** — always close (try-with-resources / `defer` / context
  managers); cap pool size sanely.

**Prevention.** Mandatory timeouts + circuit breakers as platform defaults; alert on
pool-utilization and `CLOSE_WAIT` count; load-test with an injected-slow dependency
(this is the failure that takes down whole services). See
[Concurrency §pools](../operating_system/04_concurrency_synchronization.md) and the
Production chapters of the language books.

---

## 3.6 GIL / single-writer contention — cores idle, one thread saturated

**Symptom.** A multithreaded program (notably Python) doesn't use multiple cores for
CPU-bound work — one core at 100%, the rest idle; adding threads doesn't help.

**Triage.**
```bash
py-spy top --pid <pid>    # is time spent holding/waiting the GIL?
mpstat -P ALL 1           # one core busy, others idle = serialized execution
```

**Root cause.** The **Global Interpreter Lock** (CPython) serializes bytecode
execution — threads can't run Python in parallel (only one holds the GIL). Threads
help **I/O-bound** work (the GIL is released during blocking I/O) but not **CPU-bound**
work. (The same pattern appears with any single global lock / single-writer design.)

**Mitigate / fix.**
- **CPU-bound:** use **processes** (`multiprocessing`, `ProcessPoolExecutor`) to
  sidestep the GIL, or push the hot loop into native code (NumPy/Cython/Rust) that
  releases the GIL.
- **I/O-bound:** threads or asyncio are fine — the GIL isn't your bottleneck there.
- Consider **free-threaded CPython** (PEP 703, 3.13+) for shared-memory parallelism
  (see the Python book's post-GIL chapter).

**Prevention.** Choose the concurrency model by bottleneck type (I/O vs CPU); don't
throw threads at CPU-bound Python. See
[Concurrency §the GIL](../operating_system/04_concurrency_synchronization.md).

---

## 3.7 Race condition / data corruption — the heisenbug

**Symptom.** Intermittent wrong results, corrupted state, or rare crashes that vanish
when you add logging (timing changes hide them). Worse under load / more cores.

**Triage.**
```bash
# Reproduce under a race detector (the only reliable way):
#   C/C++/Go:  -fsanitize=thread (TSan) / `go test -race`
#   Java:      stress tests + jcstress
# Look for unsynchronized shared mutable state: check-then-act, read-modify-write.
```

**Root cause.** Unsynchronized access to shared mutable state — a `count += 1`
(read-modify-write), a `if not exists: create` (check-then-act), or a lazily-
initialized singleton — interleaves between threads and loses updates or corrupts
invariants. The GIL or coarse locks may have *accidentally* hidden it until a
refactor or a faster machine exposed it.

**Mitigate now.** Roll back the change that exposed it; serialize the hot path
temporarily (a coarse lock) to stop corruption while you fix it properly.

**Permanent fix.** Protect shared mutable state explicitly: a lock around the
compound operation, an **atomic** for counters (CAS — mind ABA), an immutable/
message-passing design, or thread-local accumulation + merge. Make the data
structure's thread-safety contract explicit.

**Prevention.** Run the test suite under **TSan / `-race`** in CI (this is the highest-
ROI concurrency gate); prefer immutability and message passing over shared state;
review every shared-mutable access. See
[Concurrency §races, memory model](../operating_system/04_concurrency_synchronization.md).

---

## Quick-reference: symptom → first command

| Symptom | First look |
|---|---|
| Hang, CPU idle, throughput zero | thread dump (`jstack`/`py-spy dump`) → lock cycle (3.1) |
| Throughput drops as threads rise | `perf` futex time, `pidstat -w` cswch (3.2) |
| Won't scale, no logical contention | `perf c2c` (false sharing) (3.3) |
| Synchronized load spike | correlate TTL/recovery/reconnect (3.4) |
| "Pool exhausted", cascading timeouts | `jstack` blocked-on-downstream, `ss CLOSE_WAIT` (3.5) |
| One core busy, others idle (CPU work) | `py-spy top` GIL (3.6) |
| Intermittent wrong results | reproduce under TSan/`-race` (3.7) |

---

## Key takeaways

1. **Capture thread state during the incident** — a deadlock/convoy/pool-exhaustion
   diagnosis lives or dies on a thread dump (`jstack`/`py-spy dump`/`gdb bt`).
2. **Deadlock = circular wait** — fix with a global **lock ordering**; never hold a
   lock across an RPC/callback.
3. **Lock convoy: throughput falls as threads rise** — shrink critical sections,
   **shard locks**, or go lock-free.
4. **False sharing** is a no-contention scaling regression — pad/align hot per-thread
   data to a cache line; `perf c2c` finds it.
5. **Thundering herd / stampede** — jittered backoff + TTLs and **single-flight** are
   the standard cures; never a fixed TTL.
6. **Pool exhaustion cascades from one slow dependency** — **timeouts + circuit
   breakers + bulkheads** are the fix, and the prevention.
7. **Run TSan/`-race` in CI** — the single highest-ROI guard against the
   non-reproducible race/heisenbug.

> Next: [04 — Network Incidents](04_network_incidents.md) — retransmission storms,
> port exhaustion, accept-queue overflow, and retry-storm metastability.
