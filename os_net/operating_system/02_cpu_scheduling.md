# 02 — CPU Scheduling

> **Audience:** staff/principal. You know `nice` exists and that "the scheduler picks what runs." This doc is about *how* it picks — the algorithms (FCFS through MLFQ), the math that makes one fair and another starve, Linux's CFS/EEVDF internals, real-time guarantees, the priority-inversion bug that nearly lost a Mars mission, and the cgroup knobs you actually turn in production.
>
> **Primary sources:** Tanenbaum & Bos, *Modern Operating Systems* (4th ed.), ch. 2.4; Silberschatz, Galvin & Gagne, *Operating System Concepts* (10th ed.), ch. 5; Arpaci-Dusseau, *Operating Systems: Three Easy Pieces* (OSTEP), ch. 7–10 (the MLFQ/lottery treatment); Love, *Linux Kernel Development* (3rd ed.), ch. 4; the Linux kernel `Documentation/scheduler/` (sched-design-CFS, sched-eevdf, sched-rt-group); Liu & Layland, *Scheduling Algorithms for Multiprogramming in a Hard-Real-Time Environment* (1973, RMS/EDF); the JPL Mars Pathfinder priority-inversion post-mortem (Glenn Reeves, 1997).

---

## 1. Why this matters at scale

The CPU is a single resource time-sliced across far more runnable threads than there are cores. The **scheduler** is the policy that decides who runs next and for how long. That policy directly sets:

1. **Tail latency.** Throughput-optimal scheduling (run the longest jobs uninterrupted) destroys interactive latency. The scheduler is where the throughput-vs-latency war is fought, and your p99 lives or dies by it.
2. **Fairness and isolation.** In a multi-tenant box (containers, serverless, a shared DB host), the scheduler decides whether a noisy neighbor can starve your service. `cpu.shares` / `cpu.max` / nice are the levers, and misusing them causes the classic "one cron job ate the cluster" incident.
3. **Correctness, in real-time systems.** For control loops, media, and trading, *missing a deadline is a bug*, not a slowdown. Priority scheduling plus the priority-**inversion** failure mode (§9) is a genuine source of catastrophic, hard-to-reproduce outages — including a Mars mission.

You don't usually write a scheduler. But you constantly *fight* one: tuning nice, setting affinity, choosing `SCHED_FIFO` for a latency-critical thread, or debugging why a cgroup-throttled container has mysterious 100 ms stalls. That requires knowing what the scheduler is actually doing.

### The decision the scheduler makes

```
        Runnable tasks (the runqueue)
        +-----+ +-----+ +-----+ +-----+
        |  A  | |  B  | |  C  | |  D  |   ... possibly thousands
        +-----+ +-----+ +-----+ +-----+
                     |
                     v   pick_next_task()  (the policy)
                +---------+
                |   CPU   |   one (per core) winner runs for a time slice
                +---------+
                     |
        preempt (timer tick / higher-prio wakeup / yield / block)
                     |
                     v
              context switch  (save regs, switch mm if process, restore)
```

---

## 2. Goals and the fundamental tensions

A scheduler optimizes several metrics that *conflict*:

| Goal | Definition | Who cares |
|---|---|---|
| **Throughput** | jobs completed per unit time | batch/analytics |
| **Turnaround time** | completion − arrival (total time in system) | batch |
| **Waiting time** | time spent runnable but not running | everyone |
| **Response time / latency** | first-run (or completion) − arrival | interactive, RPC servers |
| **Fairness** | each gets a "fair share" of CPU | multi-tenant |
| **Predictability / deadlines** | bounded, guaranteed timing | real-time |

The core tensions:

- **Throughput vs. latency.** Long uninterrupted runs minimize context-switch overhead (good throughput) but make interactive tasks wait (bad latency). Time-slicing trades throughput for responsiveness.
- **Fairness vs. priority.** Strict priority gives important work the CPU first but can *starve* low-priority work forever. Fair-share guarantees everyone progress but won't honor "this is critical."
- **Overhead vs. responsiveness.** Smaller time slices = snappier response but more context switches (each ~1 us, see [01](01_processes_threads.md) §15). The slice length is the central tuning knob.

> **Definition:** A **preemptive** scheduler can forcibly take the CPU from a running task (on a timer tick or a higher-priority wakeup). A **cooperative** (non-preemptive) scheduler only switches when the task *yields* or blocks. Cooperative is simpler and lock-light but one runaway task hangs the system — which is why Windows 3.x / classic Mac OS / early cooperative async runtimes were fragile, and why all modern OS kernels are preemptive.

---

## 3. The classic algorithms, with worked examples

Use this common workload (arrival time, CPU burst), all times in ms:

| Job | Arrival | Burst |
|---|---|---|
| P1 | 0 | 7 |
| P2 | 2 | 4 |
| P3 | 4 | 1 |
| P4 | 5 | 4 |

### 3.1 FCFS (First-Come, First-Served) — non-preemptive

Run in arrival order. Simple, no starvation, but suffers the **convoy effect**: a short job stuck behind a long one waits forever.

```
Gantt:  | P1        | P2     | P3 | P4     |
        0           7        11   12       16
```

- P1: finish 7, turnaround 7, wait 0
- P2: finish 11, turnaround 9, wait 5
- P3: finish 12, turnaround 8, wait 7
- P4: finish 16, turnaround 11, wait 7
- **Avg turnaround = 8.75, avg wait = 4.75**

### 3.2 SJF (Shortest Job First) — non-preemptive

At each scheduling point pick the *shortest* available burst. **Provably optimal for average waiting time** among non-preemptive schedulers — but requires knowing burst lengths (you estimate them) and can **starve** long jobs.

```
At t=0 only P1 ready -> run P1 (0-7). At t=7 ready: P2(4),P3(1),P4(4).
Pick P3(1), then P2(4) (tie with P4, FCFS breaks it), then P4.
Gantt:  | P1        | P3 | P2     | P4     |
        0           7    8        12       16
```

- **Avg turnaround = 8.0, avg wait = 4.0**

### 3.3 SRTF (Shortest Remaining Time First) — preemptive SJF

Preempt the running job if a newly arrived job has a shorter *remaining* time.

```
t=0 P1 starts. t=2 P2(4) arrives, P1 rem=5 -> P1 continues (5>4? no, 5>4 so preempt? rem P1=5 > P2=4 -> preempt to P2).
t=2: run P2. t=4 P3(1) arrives < P2 rem(2) -> preempt to P3.
t=5 P3 done; P4(4) arrives. Ready: P1 rem5, P2 rem2, P4 rem4 -> run P2.
t=7 P2 done. Ready P1 rem5, P4 rem4 -> run P4. t=11 P4 done -> run P1 (5) -> 16.
Gantt: |P1 |P2|P3|P2 |P4    |P1        |
       0   2  4  5  7        11         16
```

- P1: finish 16, turnaround 16, wait 9
- P2: finish 7, turnaround 5, wait 1
- P3: finish 5, turnaround 1, wait 0
- P4: finish 11, turnaround 6, wait 2
- **Avg turnaround = 7.0, avg wait = 3.0** — best of the lot, but maximally unfair to P1.

### 3.4 Round Robin (RR) — preemptive, time-quantum q

Each job runs at most `q` ms, then goes to the back of the queue. Excellent *response time*, no starvation; throughput drops as `q` shrinks (more switches). With **q = 2 ms** on our workload (queueing arrivals as they come):

```
Gantt (q=2):
|P1 |P2 |P1 |P3|P4 |P2 |P1 |P4 |P1 |
0   2   4   6  7  9  11  13  14  16 ...
```

- RR's average turnaround is usually *worse* than SJF, but its **response time** (time to first execution) is far better — every job runs within `(n-1)*q` of arrival. This is the interactive-vs-batch trade in one knob.
- **Rule of thumb:** pick `q` large enough that ≥80% of bursts finish within one quantum, but small enough to keep response snappy — typically 10–100 ms historically; Linux CFS effectively auto-tunes it.

### 3.5 Priority scheduling

Each job has a priority; run the highest. Can be preemptive or not. **Starvation** is the failure mode (a stream of high-prio jobs starves low-prio ones). The fix is **aging**: gradually raise the priority of waiting jobs.

### 3.6 MLFQ (Multi-Level Feedback Queue)

The algorithm that powers practical interactive schedulers (the ancestor of Windows and pre-CFS Linux schedulers). Multiple priority queues; **learn** each job's behavior:

```
   Q0 (highest, quantum 8ms)   <- new jobs start here
   Q1 (quantum 16ms)
   Q2 (lowest, quantum 32ms, RR)

   Rules:
   1. New job enters at the TOP.
   2. If a job uses its WHOLE quantum (CPU-bound) -> DEMOTE one level.
   3. If a job YIELDS/blocks before its quantum (I/O-bound, interactive)
      -> stays at its level (rewards interactivity).
   4. Periodically BOOST everyone to the top (prevents starvation + handles
      a job that changes behavior).
```

MLFQ's genius: it **approximates SJF without knowing burst lengths** by observing behavior — interactive jobs (short bursts) float to the top and get fast response; CPU hogs sink and get long, infrequent slices (good throughput). Rule 4 (periodic boost) is what prevents starvation and was historically the source of gameable behavior (a job that yields just before its quantum stays high — OSTEP discusses these attacks).

### 3.7 Side-by-side

| Algorithm | Preemptive | Optimizes | Starvation? | Needs burst length? |
|---|---|---|---|---|
| FCFS | no | simplicity | no | no |
| SJF | no | avg wait (optimal) | **yes** | yes (estimate) |
| SRTF | yes | avg wait | **yes** | yes |
| RR | yes | response time | no | no |
| Priority | either | importance | **yes** (w/o aging) | no |
| MLFQ | yes | response + throughput | no (w/ boost) | **no** (learns) |

---

## 4. Linux CFS — the Completely Fair Scheduler

From ~2.6.23 (2007) until 6.6 (2023), the default Linux scheduler for normal tasks (`SCHED_OTHER`/`SCHED_NORMAL`) was **CFS**, by Ingo Molnár. Its model is elegant: *emulate an ideal, perfectly multitasking CPU that runs all N runnable tasks simultaneously at 1/N speed.*

### 4.1 vruntime — the core idea

CFS gives no explicit time slices. Instead each task accumulates **virtual runtime (`vruntime`)** — the CPU time it has consumed, **weighted by its nice value**. The scheduler's rule is dead simple:

> **Always run the runnable task with the smallest `vruntime`.**

A task that has run little has low `vruntime` and gets picked; as it runs, its `vruntime` climbs until someone else is now the minimum. This automatically equalizes CPU time — fairness falls out of the invariant.

`vruntime` advances at a rate inversely proportional to the task's **weight** (derived from nice):

```
   delta_vruntime = delta_real_time * (NICE_0_WEIGHT / task_weight)

   nice 0  -> weight 1024 -> vruntime advances at real-time rate
   nice -1 -> weight ~1277 -> vruntime advances SLOWER -> picked more -> more CPU
   nice +1 -> weight ~820  -> vruntime advances FASTER -> picked less -> less CPU
```

Each step of nice is ~**1.25×** CPU weight — so nice −5 vs nice +5 is roughly a 3× CPU ratio. This is *relative*, not absolute: nice only matters when tasks compete.

### 4.2 The red-black tree

"Find the minimum `vruntime`" must be fast with thousands of tasks. CFS keeps runnable tasks in a **red-black tree keyed by `vruntime`**:

```
            [ vruntime ordered red-black tree ]
                         (25)
                        /    \
                     (18)     (40)
                     /  \      /
                  (12) (20)  (33)
                   ^
              leftmost node = smallest vruntime = run NEXT (O(1) cached)
```

- **Insert / remove: O(log n)** (balanced tree).
- **Pick next: O(1)** — the leftmost node is cached. Picking the next task to run is the hottest path; making it O(1) matters.
- A newly woken task is given a `vruntime` near the tree's *minimum* (not 0) so it doesn't unfairly dominate — but slightly less, so interactive tasks waking from I/O get a small responsiveness boost.

### 4.3 Tunables

- `sched_latency_ns` (the target period in which every runnable task runs once) and `sched_min_granularity_ns` (floor on a slice, to cap switch overhead) together set the effective time slice: `slice ≈ sched_latency / nr_running`, clamped to the minimum granularity.
- Group scheduling (cgroups) nests this: fairness is computed hierarchically so a cgroup with 100 threads doesn't out-compete one with 1 thread (§11).

---

## 5. EEVDF — the successor (Linux 6.6+, 2023)

CFS was replaced as the default by **EEVDF (Earliest Eligible Virtual Deadline First)** in Linux 6.6 (late 2023), by Peter Zijlstra, based on a 1995 paper (Stoica & Abdel-Wahab). CFS was excellent at *fairness* but had no first-class notion of *latency* — you couldn't say "this task is fair-share but should run with low latency." EEVDF adds exactly that.

### 5.1 The model

EEVDF keeps the virtual-time fairness idea but adds two concepts:

- **Lag / eligibility.** Each task has a "lag" = how much CPU it *deserves* (by fair share) minus what it *got*. A task is **eligible** to run only when its lag is non-negative (it's owed time). This prevents a task that already ran ahead from running again before others catch up — tightening fairness.
- **Virtual deadline.** Each task is assigned a virtual deadline derived from a per-task **time slice request** (`sched_attr`'s `sched_runtime`, exposed via `nice`/latency-nice). Among *eligible* tasks, EEVDF runs the one with the **earliest virtual deadline**.

```
   request a SHORT slice  -> EARLIER virtual deadline -> scheduled sooner,
                             more often, in smaller chunks  (low latency)
   request a LONG slice    -> LATER deadline -> fewer, bigger chunks
                             (high throughput, fewer switches)
   ... while LAG-based eligibility keeps total CPU shares FAIR.
```

The payoff: a task can be **latency-sensitive without being higher priority**. A media/interactive thread asks for short slices and gets low scheduling latency; a batch thread asks for long slices and gets throughput — and both still receive their *fair share* of CPU. This is the "latency nice" capability CFS lacked. (As of 2026, EEVDF is the production default on current kernels; it reuses CFS's red-black-tree machinery and cgroup integration, so most operational knowledge transfers.)

---

## 6. Real-time scheduling

For tasks where timing is *correctness*, Linux offers POSIX real-time policies that **always preempt** normal (`SCHED_OTHER`/CFS/EEVDF) tasks. RT priorities run 1–99; any runnable RT task beats every normal task.

| Policy | Behavior |
|---|---|
| `SCHED_FIFO` | Run until you block, yield, or a *higher*-priority RT task preempts. **No time-slicing among equal priority** — a `SCHED_FIFO` task can monopolize a core. |
| `SCHED_RR` | Like FIFO but **time-sliced** (round-robin) among equal-priority RT tasks. |
| `SCHED_DEADLINE` | EDF-based (§6.2): specify `(runtime, deadline, period)`; the kernel admits the task only if the set is schedulable, then guarantees it. Outranks FIFO/RR. |

> **Danger:** a buggy `SCHED_FIFO` task at priority 99 that spins will **lock up a core** — it never yields and nothing normal can preempt it, including your SSH session. This is why the kernel has `sched_rt_runtime_us` (default: RT tasks capped at 95% of each period, leaving 5% for normal tasks as a safety valve).

### 6.1 Rate-monotonic scheduling (RMS) — Liu & Layland 1973

A *static*-priority scheme for **periodic** tasks: assign priority **inversely to period** (shorter period = higher priority). RMS is the optimal fixed-priority assignment. The classic schedulability bound for `n` tasks:

```
   sum( C_i / T_i )  <=  n * (2^(1/n) - 1)

   C_i = compute time, T_i = period.
   n=1 -> 100%,  n=2 -> 82.8%,  n=3 -> 78.0%,  n->inf -> ln 2 = 69.3%
```

If total CPU utilization is under that bound, **all deadlines are guaranteed** to be met under RMS. Above it, you may still be schedulable (the bound is sufficient, not necessary) but must check with response-time analysis.

### 6.2 Earliest Deadline First (EDF) — dynamic priority

EDF picks the task with the **nearest absolute deadline**. It is **optimal** for single-core: if *any* schedule meets all deadlines, EDF does, and it achieves up to **100% utilization** (`sum(C_i/T_i) <= 1`). The cost: priorities change dynamically (more overhead) and, crucially, **under overload EDF degrades catastrophically** — a single missed deadline can cascade (domino effect), whereas RMS fails predictably (only the lowest-priority tasks miss). Linux's `SCHED_DEADLINE` implements EDF with admission control to prevent overload.

---

## 7. Priority inversion and inheritance — the Mars Pathfinder case study

This is the single most famous scheduling bug, and a staple of staff interviews.

### 7.1 The mechanism

```
   Priority:  High (H) > Medium (M) > Low (L)
   Shared resource R guarded by a mutex.

   1. L acquires mutex on R.
   2. H wakes, preempts L, wants R -> BLOCKS waiting for L to release.
   3. M (unrelated, needs no lock) wakes. M > L, so M PREEMPTS L.
      -> L never runs -> never releases R -> H stays blocked.

   Net: a MEDIUM task indirectly blocks a HIGH task, indefinitely.
   The high-priority task is "inverted" below medium ones. STARVATION.
```

H is the highest priority but is effectively stuck behind M — *inverted*. If H has a deadline (or, on Pathfinder, a watchdog), the system fails.

### 7.2 Mars Pathfinder (July 1997)

The Pathfinder lander began experiencing **total system resets** on the Martian surface. Root cause (diagnosed by JPL's Glenn Reeves by reproducing it on a ground replica with tracing on):

- A high-priority **bus management** task (`bc_dist`) and a low-priority **meteorological data** task shared an information bus protected by a mutex (VxWorks pipe/mutex).
- A medium-priority **communications** task would preempt the low-priority task while it held the mutex, starving the high-priority bus task.
- A **watchdog timer** noticed the high-priority bus task hadn't run, concluded the system was hung, and **reset the spacecraft** — exactly the priority-inversion scenario above.

The fix was already supported by VxWorks: enable **priority inheritance** on that mutex. JPL patched it *remotely from Earth* by flipping the mutex's inheritance flag via an uploaded change to a global variable.

### 7.3 The fixes

| Protocol | How it works |
|---|---|
| **Priority Inheritance (PIP)** | While L holds a lock that H wants, L **temporarily inherits H's priority** so M can't preempt it. L runs at H's priority until it releases, then drops back. (What fixed Pathfinder.) |
| **Priority Ceiling (PCP)** | Each mutex has a "ceiling" = the highest priority of any task that can lock it. A task holding the mutex runs at the ceiling. Prevents inversion *and* deadlock; common in hard-RT. |

Linux exposes priority inheritance via **PI-futexes** and `pthread_mutexattr_setprotocol(..., PTHREAD_PRIO_INHERIT)`. The lesson: *any time a low-priority task can hold a lock a high-priority task needs, you have a latent inversion — use an inheriting mutex.*

---

## 8. CPU affinity and NUMA-aware scheduling

### 8.1 Affinity

By default the scheduler may migrate a thread between cores for load balance. But migration is costly: the thread's **cache (L1/L2) is cold** on the new core, and on a NUMA box its memory may now be *remote*. **CPU affinity** pins a thread to a set of cores:

- `sched_setaffinity(2)` / `taskset` / `cpuset` cgroups / Python `os.sched_setaffinity`.
- **Cache warmth:** pinning a latency-critical thread to one core keeps its working set hot — common for trading engines, DPDK packet processors, and database I/O threads.
- **Isolation:** `isolcpus=` / `nohz_full=` reserve cores from the general scheduler so a critical RT thread owns a core with no interference (no timer ticks, no other tasks).

### 8.2 NUMA

On a multi-socket server, memory is **Non-Uniform**: a core accesses its local socket's RAM faster (~100 ns) than the other socket's (~150–300 ns, across the interconnect). Drepper's *What Every Programmer Should Know About Memory* is the reference.

```
   Socket 0                         Socket 1
   +--------+   QPI/UPI link   +--------+
   | Cores  |<---------------->| Cores  |
   | L3$    |   (slower path)  | L3$    |
   +---+----+                  +---+----+
       |                           |
   [Local RAM 0]               [Local RAM 1]
   fast (~100ns) for sk0       fast for sk1; REMOTE/slow for sk0
```

The scheduler is **NUMA-aware**: it tries to keep a thread on the node where its memory lives (and Linux's *autonuma* migrates pages toward the threads using them). Staff-level practice:

- Pin both the thread **and** its memory to one node (`numactl --cpunodebind=0 --membind=0`).
- Watch for the "remote memory" tax: a thread scheduled on socket 1 hammering memory allocated on socket 0 can run 1.5–2× slower with no code change — a classic mysterious-regression cause when a VM is rescheduled across sockets.

---

## 9. Load balancing across cores

Each core has its own runqueue (per-CPU runqueues avoid a global lock — a scalability necessity). The scheduler periodically **load-balances**: it moves tasks from busy runqueues to idle ones, organized by **scheduling domains** that mirror the hardware topology (SMT siblings → cores → LLC → NUMA nodes).

- Balancing is **hierarchical and reluctant**: it's cheap/aggressive between hyperthread siblings (shared cache) and expensive/conservative across NUMA nodes (cold cache + remote memory). The cost model deliberately resists migrations that would lose cache/NUMA locality.
- **Idle balancing**: a core going idle pulls work from a busy sibling rather than sit empty.
- The tension: balance *too* eagerly and you thrash caches; balance *too* lazily and cores sit idle while one runqueue is deep. The scheduler walks this line via the domain hierarchy and migration cost estimates.

---

## 10. nice, cgroups, and the knobs you actually turn

### 10.1 nice

`nice` ranges −20 (highest) to +19 (lowest), default 0. Each step ≈ 1.25× CPU weight (§4.1). It's **relative and only matters under contention** — a nice +19 task alone still gets 100% of an idle CPU. Use it for best-effort background work (backups, indexers) so they yield to foreground load.

### 10.2 cgroups v2 — the real isolation mechanism

In containerized / multi-tenant systems, **cgroups** (not nice) are how you allocate CPU. Two distinct controls:

| Control | cgroup v2 file | Semantics |
|---|---|---|
| **Weight (shares)** | `cpu.weight` (1–10000; v1: `cpu.shares`) | *Proportional* share **under contention**. Weight 200 vs 100 = 2:1 split of a busy CPU. No cap — if the CPU is idle you get all of it. |
| **Quota (bandwidth)** | `cpu.max` = `"<quota> <period>"` (v1: `cpu.cfs_quota_us`/`cfs_period_us`) | *Hard cap*. `"50000 100000"` = at most 50 ms CPU per 100 ms = **0.5 cores**, even if the CPU is idle. |

```
   cpu.weight (shares):  "you get THIS FRACTION when contended"
       -> elastic; bursts into idle capacity. Good default.

   cpu.max (quota):      "you get AT MOST this, period."
       -> hard ceiling; the source of CFS THROTTLING stalls.
```

### 10.3 The CFS throttling trap (a real production incident pattern)

`cpu.max` quota is enforced per ~100 ms **period**. A multithreaded app can burn its entire quota in the first few ms of the period (all threads run at once), then get **throttled — frozen — for the rest of the period**. The result: a service with "plenty of CPU headroom" on average shows periodic **tens-of-milliseconds latency spikes** every 100 ms. This is one of the most common Kubernetes latency mysteries:

- Diagnose via `cpu.stat` → `nr_throttled` / `throttled_time` climbing.
- Mitigations: raise the limit, remove the CPU limit entirely (use `requests`/weight only), reduce thread count to fit the quota, or use the kernel's CFS-bandwidth burst feature. The point: **`cpu.max` is a hard wall, not a guideline**, and bursty multithreaded apps hit it hard.

---

## 11. Working code — a scheduling simulator

Compares FCFS, SJF (non-preemptive), and RR on a workload, printing average waiting and turnaround times — the standard way to build intuition for the trade-offs in §3. Pure stdlib, runnable.

```python
#!/usr/bin/env python3
"""
sched_sim.py - simulate FCFS, SJF (non-preemptive), and Round Robin and
compare average waiting / turnaround time on the same workload.

Run: python3 sched_sim.py
"""
from dataclasses import dataclass
from typing import List


@dataclass
class Job:
    name: str
    arrival: int
    burst: int


def fcfs(jobs: List[Job]):
    """First-come, first-served: run in arrival order, non-preemptive."""
    order = sorted(jobs, key=lambda j: (j.arrival, j.name))
    t = 0
    completion = {}
    for j in order:
        start = max(t, j.arrival)        # CPU idle until the job arrives
        finish = start + j.burst
        completion[j.name] = finish
        t = finish
    return completion


def sjf_nonpreemptive(jobs: List[Job]):
    """At each decision point pick the shortest available burst."""
    remaining = list(jobs)
    t = 0
    completion = {}
    while remaining:
        ready = [j for j in remaining if j.arrival <= t]
        if not ready:
            t = min(j.arrival for j in remaining)   # jump to next arrival
            continue
        # shortest burst; ties broken by arrival then name (deterministic)
        j = min(ready, key=lambda x: (x.burst, x.arrival, x.name))
        t += j.burst
        completion[j.name] = t
        remaining.remove(j)
    return completion


def round_robin(jobs: List[Job], quantum: int):
    """Preemptive RR with a fixed time quantum."""
    from collections import deque
    order = sorted(jobs, key=lambda j: (j.arrival, j.name))
    rem = {j.name: j.burst for j in jobs}
    arrival = {j.name: j.arrival for j in jobs}
    completion = {}
    t = 0
    q = deque()
    i = 0  # index into arrival-sorted jobs not yet enqueued

    def enqueue_arrivals(up_to):
        nonlocal i
        while i < len(order) and order[i].arrival <= up_to:
            q.append(order[i].name)
            i += 1

    enqueue_arrivals(t)
    if not q and order:
        t = order[0].arrival
        enqueue_arrivals(t)

    while q:
        name = q.popleft()
        run = min(quantum, rem[name])
        t += run
        rem[name] -= run
        enqueue_arrivals(t)              # admit jobs that arrived while running
        if rem[name] == 0:
            completion[name] = t
        else:
            q.append(name)               # not done -> back of the line
        if not q and i < len(order):     # CPU idle: jump to next arrival
            t = order[i].arrival
            enqueue_arrivals(t)
    return completion


def metrics(jobs: List[Job], completion):
    rows = []
    tot_wait = tot_turn = 0
    for j in jobs:
        turn = completion[j.name] - j.arrival      # turnaround
        wait = turn - j.burst                       # waiting = turnaround - service
        tot_wait += wait
        tot_turn += turn
        rows.append((j.name, j.arrival, j.burst, completion[j.name], turn, wait))
    n = len(jobs)
    return rows, tot_turn / n, tot_wait / n


def main():
    workload = [
        Job("P1", 0, 7),
        Job("P2", 2, 4),
        Job("P3", 4, 1),
        Job("P4", 5, 4),
    ]
    algos = [
        ("FCFS", fcfs(workload)),
        ("SJF (non-preempt)", sjf_nonpreemptive(workload)),
        ("RR (q=2)", round_robin(workload, quantum=2)),
    ]

    print(f"{'Algorithm':<20} {'avg turnaround':>16} {'avg waiting':>14}")
    print("-" * 52)
    for label, comp in algos:
        _, avg_turn, avg_wait = metrics(workload, comp)
        print(f"{label:<20} {avg_turn:>16.2f} {avg_wait:>14.2f}")

    # Detailed per-job table for SJF
    print("\nPer-job detail (SJF):")
    rows, _, _ = metrics(workload, sjf_nonpreemptive(workload))
    print(f"{'job':<5}{'arr':>5}{'burst':>7}{'finish':>8}"
          f"{'turn':>7}{'wait':>7}")
    for name, arr, burst, fin, turn, wait in rows:
        print(f"{name:<5}{arr:>5}{burst:>7}{fin:>8}{turn:>7}{wait:>7}")


if __name__ == "__main__":
    main()
```

Expected output (matches the hand-worked numbers in §3):

```text
Algorithm              avg turnaround    avg waiting
----------------------------------------------------
FCFS                             8.75           4.75
SJF (non-preempt)                8.00           4.00
RR (q=2)                         9.00           5.00
```

SJF wins on average waiting (it's provably optimal for that metric); RR is worse on averages but would win on *response time* (first-run latency), which this simulator doesn't tabulate — the trade-off made concrete.

---

## 12. Working code — CPU affinity (pinning) with sched_setaffinity

Demonstrates reading and setting CPU affinity from Python (`os.sched_setaffinity`, a thin wrapper over the `sched_setaffinity(2)` syscall) and *observing* that a pinned compute loop stays on its assigned core.

```python
#!/usr/bin/env python3
"""
affinity_demo.py - pin this process to a single CPU and verify it stays
there while doing CPU-bound work. Linux only (sched_*affinity).

Run: python3 affinity_demo.py
"""
import os
import time

def busy(ms):
    """Spin for ~ms milliseconds of CPU-bound work."""
    end = time.perf_counter() + ms / 1000.0
    x = 0
    while time.perf_counter() < end:
        x += 1  # keep the CPU busy
    return x

def main():
    if not hasattr(os, "sched_getaffinity"):
        print("sched_*affinity not available on this platform (Linux only)")
        return

    pid = 0  # 0 = this process
    available = sorted(os.sched_getaffinity(pid))
    print(f"CPUs this process may run on: {available}")

    # Pin to the first available CPU only.
    target = available[0]
    os.sched_setaffinity(pid, {target})
    print(f"Pinned to CPU {target}; affinity now: "
          f"{sorted(os.sched_getaffinity(pid))}")

    # Do CPU-bound work and report which CPU we actually ran on.
    busy(200)
    if hasattr(os, "sched_getcpu"):       # Python 3.13+ exposes this
        print(f"Currently executing on CPU: {os.sched_getcpu()}")
    else:
        # Fallback: read it from /proc/self/stat field 39 (processor).
        with open("/proc/self/stat") as f:
            fields = f.read().split()
        print(f"Last-run CPU (from /proc/self/stat): {fields[38]}")

    # Restore the original affinity mask (be a good citizen).
    os.sched_setaffinity(pid, set(available))
    print(f"Restored affinity: {sorted(os.sched_getaffinity(pid))}")

if __name__ == "__main__":
    main()
```

**What this teaches:** pinning a hot thread to a core keeps its L1/L2 cache warm and (on NUMA) its memory local. The same call is what `numactl`, container CPU-set isolation, and DPDK/trading engines use under the hood. Pinning everything is an anti-pattern (you defeat the load balancer); pin only the few latency-critical threads and isolate their cores with `isolcpus`/`cpuset`.

---

## 13. Working code — demonstrating priority inversion

A self-contained simulation of the classic three-task inversion (§7), in pure Python. It models a low task holding a lock, a high task blocking on it, and a medium task that preempts low — showing the high task starving — then shows how priority inheritance fixes it. (We *simulate* the scheduler so the effect is deterministic and observable without root or RT privileges.)

```python
#!/usr/bin/env python3
"""
priority_inversion.py - simulate classic priority inversion and the
priority-inheritance fix, deterministically (no RT privileges needed).

Model: a single CPU, strict priority scheduling. Three tasks share a lock.
We show that WITHOUT inheritance, a medium task indirectly blocks the high
task (inversion); WITH inheritance, the low task is boosted so it finishes
its critical section promptly and the high task proceeds.

Run: python3 priority_inversion.py
"""
from dataclasses import dataclass, field
from typing import Optional, List

HIGH, MEDIUM, LOW = 3, 2, 1
NAMES = {HIGH: "HIGH", MEDIUM: "MEDIUM", LOW: "LOW"}


@dataclass
class Task:
    base_prio: int
    name: str
    script: List[str]          # sequence of 1ms ops: 'cpu','lock','unlock'
    ip: int = 0                # instruction pointer
    eff_prio: int = field(init=False)

    def __post_init__(self):
        self.eff_prio = self.base_prio

    def done(self):
        return self.ip >= len(self.script)

    def next_op(self):
        return self.script[self.ip]


class Sim:
    def __init__(self, use_inheritance: bool):
        self.use_inheritance = use_inheritance
        self.lock_held_by: Optional[Task] = None
        self.blocked_on_lock: List[Task] = []
        self.time = 0
        self.log = []

    def runnable(self, tasks, now):
        # arrived (we model arrival via a 'sleep' prefix omitted for brevity)
        out = []
        for t in tasks:
            if t.done():
                continue
            if t in self.blocked_on_lock:
                continue
            out.append(t)
        return out

    def step(self, tasks):
        runnable = self.runnable(tasks, self.time)
        if not runnable:
            self.time += 1
            return False
        # strict priority: highest effective priority runs (ties: name)
        cur = max(runnable, key=lambda t: (t.eff_prio, -ord(t.name[0])))
        op = cur.next_op()

        if op == "lock":
            if self.lock_held_by is None:
                self.lock_held_by = cur
                cur.ip += 1
            else:
                # block on the lock
                self.blocked_on_lock.append(cur)
                holder = self.lock_held_by
                # PRIORITY INHERITANCE: boost the holder to the waiter's prio
                if self.use_inheritance and cur.eff_prio > holder.eff_prio:
                    holder.eff_prio = cur.eff_prio
                    self.log.append(
                        f"t={self.time:>2}  {cur.name} blocks on lock; "
                        f"{holder.name} INHERITS prio {cur.eff_prio}")
                return True
        elif op == "unlock":
            self.lock_held_by = None
            cur.eff_prio = cur.base_prio       # drop back to base
            cur.ip += 1
            # wake blocked waiters
            woken = self.blocked_on_lock
            self.blocked_on_lock = []
            for w in woken:
                pass  # they become runnable again next step
        else:  # 'cpu'
            cur.ip += 1

        self.log.append(f"t={self.time:>2}  run {cur.name:<6} "
                         f"(eff prio {cur.eff_prio}) op={op}")
        self.time += 1
        return True

    def run(self, tasks, max_t=60):
        while self.time < max_t and not all(t.done() for t in tasks):
            self.step(tasks)
        return self.time


def build_tasks():
    # LOW grabs the lock, does a long critical section.
    low = Task(LOW, "LOW",
               ["cpu", "lock", "cpu", "cpu", "cpu", "cpu", "unlock", "cpu"])
    # HIGH (arrives "later" - we approximate by lower initial activity) wants
    # the lock after LOW holds it.
    high = Task(HIGH, "HIGH", ["lock", "cpu", "unlock"])
    # MEDIUM just burns CPU - it does NOT use the lock, but can preempt LOW.
    med = Task(MEDIUM, "MEDIUM", ["cpu"] * 8)
    return low, high, med


def scenario(use_inheritance):
    low, high, med = build_tasks()
    # We want LOW to acquire first: give it a 1-step head start by running
    # it once before others are 'awake'. Simplest: order matters via priority,
    # so we manually let LOW take the lock first.
    sim = Sim(use_inheritance)
    # Step 1: only LOW runs to grab the lock (simulate HIGH/MED not yet ready).
    low.ip = 0
    sim.lock_held_by = low
    low.ip = 2  # consumed 'cpu','lock'
    print(f"  (LOW has acquired the lock; HIGH and MEDIUM now wake)")
    finish = sim.run([low, high, med])
    for line in sim.log:
        print("   " + line)
    print(f"  -> all tasks done at t={finish}")
    return finish


def main():
    print("=== WITHOUT priority inheritance (inversion expected) ===")
    scenario(use_inheritance=False)
    print("\n=== WITH priority inheritance (fix) ===")
    scenario(use_inheritance=True)
    print("\nObserve: without inheritance, MEDIUM (CPU-only) repeatedly "
          "preempts LOW, delaying the unlock and starving HIGH.\n"
          "With inheritance, LOW is boosted to HIGH's priority, finishes its\n"
          "critical section immediately, and HIGH proceeds without delay.")


if __name__ == "__main__":
    main()
```

**What this teaches:** the *only* difference between the two runs is the `if self.use_inheritance` block that boosts the lock holder to the waiter's priority. Without it, the MEDIUM task (which needs no lock) keeps preempting LOW, the critical section drags out, and HIGH starves — the Pathfinder bug. With inheritance, LOW runs at HIGH's priority just long enough to release the lock. Real systems implement this in the kernel via PI-futexes; you enable it per-mutex with `PTHREAD_PRIO_INHERIT`.

---

## 14. Advanced: measuring scheduler latency, sched_ext, and core scheduling

### Measuring run-queue latency (the number that maps to tail latency)

The scheduler metric that drives p99 is **run-queue latency** — how long a *runnable*
task waits before it gets a CPU. You can't tune what you can't see:

- `/proc/<pid>/schedstat` and `/proc/schedstat` expose cumulative wait time.
- `perf sched record` → `perf sched latency` gives per-task scheduling delay.
- **`runqlat`** (bcc/bpftrace) prints a histogram of run-queue latency — the cleanest
  "is the scheduler the bottleneck?" view; a tail in the ms range under load means
  CPU saturation or throttling ([scenarios 01](../enterprise_scenarios/01_cpu_memory_incidents.md)).
- **PSI cpu** (`/proc/pressure/cpu`) aggregates "time tasks were runnable but stalled
  for CPU" — the best single alerting signal; watch with
  [`examples/psi_watcher.py`](examples/README.md).

### sched_ext — pluggable schedulers in BPF (Linux 6.12+)

`sched_ext` lets you write a **scheduler in BPF** and load it at runtime without
patching the kernel. It enables workload-specialized policies (e.g. `scx_rusty`,
`scx_lavd` for latency/gaming) and safe experimentation — a misbehaving BPF scheduler
is detected and the kernel falls back to EEVDF. This is how hyperscalers now tune
scheduling per-fleet, a shift from "take what the kernel gives you."

### Core scheduling — SMT, side channels, and core-level noisy neighbors

Two hyperthreads share one physical core's execution units and L1/L2, creating two
problems: **security** (L1TF/MDS side channels let one sibling spy on the other, so
untrusted tenants must not share a core) and **performance** (a cache-hungry sibling
steals execution resources — a hidden noisy neighbor at the *core* level).

**Core scheduling** (`prctl(PR_SCHED_CORE)`) ensures only threads from the same trust
group run on sibling hyperthreads at once — the mitigation cloud providers use to keep
SMT on without cross-tenant leakage. Alternatives: disable SMT (simplest, ~20-30%
throughput loss) or isolate a full physical core (both siblings via `isolcpus`) for a
latency-critical thread so no sibling competes.

---

## 15. Trade-offs summary

- **No scheduler optimizes everything.** Throughput, latency, fairness, and predictability conflict; every algorithm picks a corner. SJF/SRTF minimize average wait but starve; RR minimizes response but adds switches; MLFQ approximates SJF by *learning* behavior.
- **Linux normal tasks: fairness via virtual time.** CFS = "run the smallest `vruntime`" in an O(log n) RB-tree; EEVDF (6.6+) adds lag-based eligibility + virtual deadlines so a task can be **low-latency without being high-priority**.
- **Real-time = correctness, not speed.** `SCHED_FIFO`/`RR` always preempt normal tasks (and can lock a core); `SCHED_DEADLINE` is admission-controlled EDF. RMS (static, ≤69% bound) vs EDF (dynamic, 100% but overload-fragile).
- **Priority inversion is a real outage class** (Mars Pathfinder). Any lock shared between priority levels needs **priority inheritance** (`PTHREAD_PRIO_INHERIT` / PI-futexes) or a ceiling protocol.
- **Affinity + NUMA locality** trade load-balancing flexibility for warm caches and local memory — pin only the critical few, isolate their cores.
- **cgroups, not nice, isolate tenants.** `cpu.weight` is elastic proportional share; `cpu.max` is a hard wall — and that wall causes the **CFS throttling** latency-spike incidents that plague Kubernetes.

## 16. Key takeaways

1. The scheduler arbitrates the **throughput-vs-latency-vs-fairness** conflict; the algorithm choice *is* that trade-off made concrete (FCFS/SJF/SRTF/RR/Priority/MLFQ).
2. **SJF is optimal for average wait but starves**; **RR is optimal for response but adds overhead**; **MLFQ learns** behavior to get SJF-like results without knowing burst lengths.
3. **CFS** schedules by smallest **`vruntime`** (nice-weighted) in an **O(log n) red-black tree**, O(1) to pick next; **EEVDF** (Linux 6.6+) adds **eligibility (lag)** and **virtual deadlines** for explicit latency control.
4. **Real-time policies always beat normal tasks**; RMS (static, ~69% bound) and EDF (dynamic, 100% but overload-fragile) are the two foundations; `SCHED_DEADLINE` is admission-controlled EDF.
5. **Priority inversion** (a medium task starving a high task via a lock held by a low task) is a genuine catastrophic bug — Mars Pathfinder — fixed by **priority inheritance**.
6. **Affinity and NUMA-awareness** keep caches warm and memory local; over-pinning defeats the load balancer.
7. **In production you tune cgroups**: `cpu.weight` (elastic share) vs `cpu.max` (hard cap, the source of CFS-throttling stalls), not `nice`.

> Read next: memory management — how the virtual address space from [01](01_processes_threads.md) §2 is backed by physical frames, paging, and the page cache that dominates real-world latency.
