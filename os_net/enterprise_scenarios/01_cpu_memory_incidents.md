# 01 — CPU & Memory Incidents

> **Audience:** staff/principal on-call. Each scenario: **Symptom → Triage (exact
> commands) → Root cause → Mitigate now → Permanent fix → Prevention.** The theory
> behind each lives in [CPU Scheduling](../operating_system/02_cpu_scheduling.md) and
> [Memory Management](../operating_system/03_memory_management.md).

---

## 1.1 CFS throttling — periodic latency spikes with "spare" CPU

**Symptom.** A containerized service shows p99 latency spikes of tens of
milliseconds on a regular cadence (roughly every 100 ms), while average CPU usage
looks *low* (e.g. 40%). Dashboards say "plenty of headroom"; users see jank.

**Blast radius.** Per-pod; worst on multithreaded, latency-sensitive services
(JVM, Go with high `GOMAXPROCS`, thread pools) — the single most common Kubernetes
latency mystery.

**Triage.**
```bash
# The smoking gun: throttling counters climbing.
cat /sys/fs/cgroup/cpu.stat            # cgroup v2
#   nr_periods 12000
#   nr_throttled 4200       <-- ~35% of periods throttled = BAD
#   throttled_usec 870000000
# In k8s: container_cpu_cfs_throttled_periods_total / ..._periods_total
```
If `nr_throttled / nr_periods` is non-trivial (>1%), you are CPU-throttled.

**Root cause.** `cpu.max` quota is enforced per ~100 ms **period**. A multithreaded
app with N threads can burn its entire quota in the first few ms (all threads run at
once), then the kernel **freezes every thread for the rest of the period**. Average
CPU looks low because the freeze time isn't "usage." A 1-core limit on an 8-thread
app means it spends ~12 ms running and ~88 ms throttled each period.

**Mitigate now.** Raise the limit, or remove the CPU *limit* and keep only the
*request* (weight). Reduce thread/GOMAXPROCS count to match the quota.
```bash
# Quick relief: widen the quota (or set it to max = unlimited, keep the request).
echo "max 100000" > /sys/fs/cgroup/cpu.max
```

**Permanent fix.**
- Set container **CPU `requests` but not `limits`** for latency-sensitive services
  (rely on `cpu.weight` proportional share for fairness instead of a hard wall), OR
- Right-size threads to the quota (`GOMAXPROCS=ceil(quota)`, bounded thread pools),
  OR enable the kernel **CFS bandwidth burst** so brief bursts don't get clipped.

**Prevention.** Alert on `cfs_throttled_periods` ratio, not just CPU%. Make
"CPU limit on a latency-critical, multithreaded pod" a design-review red flag.
See [CPU Scheduling §10.3](../operating_system/02_cpu_scheduling.md).

---

## 1.2 Noisy neighbor — one tenant starves the box

**Symptom.** A service degrades with no change to *its* code or traffic; a co-located
batch job, cron, or another tenant deployed around the same time.

**Triage.**
```bash
mpstat -P ALL 1          # which cores are pegged; is it %usr, %sys, %steal?
pidstat 1                # which PID is burning CPU
top -H                   # per-THREAD; find the offending thread
# In a VM: high %steal means the HYPERVISOR is giving your vCPU to someone else.
```

**Root cause.** Either a co-tenant on the same host consuming shared CPU (weight
mis-set, no limits) or, in a cloud VM, **CPU steal** — the hypervisor scheduling
other guests on your physical core.

**Mitigate now.** Throttle/evict the offender (`cpu.max` on the batch cgroup);
in cloud, move the victim to a dedicated/larger instance.

**Permanent fix.** Set `cpu.weight` so the latency-critical tenant wins contention;
isolate critical cores with `cpuset`/`isolcpus`; use dedicated (non-burstable)
instance types for latency-SLO services. Don't co-locate batch and online without
weights.

**Prevention.** Alert on `%steal > 5%`. Enforce resource requests/weights cluster-
wide; separate batch and online node pools. See
[CPU Scheduling §8, §10](../operating_system/02_cpu_scheduling.md).

---

## 1.3 Run-queue saturation — load average ≫ cores

**Symptom.** Everything on the host is slow; `uptime` load average is many multiples
of core count.

**Triage.**
```bash
uptime                   # load avg: 60.0 on an 8-core box = 60 runnable/uninterruptible
vmstat 1                 # 'r' column = runnable threads; 'b' = blocked on I/O
mpstat -P ALL 1          # all cores ~100%? CPU-bound. Low %usr but high load? I/O (see 02)
```
Linux load average counts **runnable AND uninterruptible (D-state, usually I/O)**
threads — a high load with idle CPU means an **I/O** problem, not CPU (jump to
[runbook 02](02_io_storage_incidents.md)).

**Root cause.** More runnable threads than cores → every thread waits in the
runqueue → context-switch overhead and scheduling latency dominate. Often a thread-
pool sized to "infinity," a retry storm, or a fan-out amplification.

**Mitigate now.** Shed load (rate-limit, load-shed), scale out, cut thread/worker
counts to ≈ cores for CPU-bound work.

**Permanent fix.** Bound concurrency (pools sized to cores for CPU work; semaphores).
A queue that grows without bound is the disease; backpressure is the cure
([Concurrency runbook 03](03_concurrency_incidents.md)).

**Prevention.** Alert on `load / nproc` and on run-queue latency (`schedstat`,
`runqlat` from bcc). Capacity-plan to keep utilization < ~70% for tail latency.

---

## 1.4 IRQ / softirq storm — a core eaten by interrupts

**Symptom.** One core shows high `%irq`/`%soft` (or `%sys`) and is effectively
unavailable to applications; tail latency spikes correlate with network or device
load.

**Triage.**
```bash
mpstat -P ALL 1          # a core with high %soft/%irq, low %usr
cat /proc/interrupts     # which IRQ, which CPU is absorbing them (NIC queues?)
cat /proc/softirqs       # NET_RX/NET_TX dominating one CPU
```

**Root cause.** All NIC interrupts (or all softirq processing) pinned to one core —
common with a single RX queue or bad IRQ affinity — so packet processing starves
that core under load.

**Mitigate now.** Spread interrupts: enable RSS/RPS, set IRQ affinity across cores.
```bash
# Spread NIC IRQs across CPUs (or use the irqbalance daemon / the NIC's set_irq_affinity).
# Enable RPS to fan softirq RX processing across cores:
echo ff > /sys/class/net/eth0/queues/rx-0/rps_cpus
```

**Permanent fix.** Multi-queue NIC with RSS, IRQ affinity matched to NUMA topology,
keep app threads off the IRQ cores (`isolcpus`). For very high PPS, kernel-bypass
(DPDK/XDP, [Net 08](../comp_networking/08_network_performance_tuning.md)).

**Prevention.** Monitor per-core `%soft`/`%irq`; bake IRQ-affinity tuning into the
host image. See [I/O §interrupts](../operating_system/06_io_models_async.md).

---

## 1.5 OOMKilled — container killed at the memory limit

**Symptom.** A process or container dies abruptly (exit 137 / `OOMKilled`), often
under load or after hours/days of uptime. No application stack trace.

**Triage.**
```bash
dmesg | grep -i -A3 'killed process'     # the OOM killer's verdict + scores
journalctl -k | grep -i oom
cat /sys/fs/cgroup/memory.events         # 'oom_kill' counter (cgroup v2)
# Trend RSS over time (is it a leak or a legit spike?):
cat /proc/<pid>/status | grep -E 'VmRSS|VmSwap'
```

**Root cause.** RSS (resident memory) exceeded the cgroup `memory.max` limit and the
kernel **OOM-killed** the process — this is *not* a Java `OutOfMemoryError` (that's
heap; OOMKilled is the kernel killing the whole process for total RSS, including
off-heap, thread stacks, page cache pinned by the cgroup, native buffers).

**Mitigate now.** Raise the memory limit to restore service; restart.

**Permanent fix.** Distinguish the two cases:
- **Legitimate working set > limit** → raise the limit (right-size from real RSS
  percentiles, not guesses).
- **A leak** → RSS climbs monotonically forever. Profile it (heap profiler;
  `jemalloc` profiling; for native, ASan/Valgrind/`bpftrace` malloc tracking). For
  the JVM, remember non-heap (metaspace, direct buffers, thread stacks) counts —
  size the *container* limit above `-Xmx` + non-heap headroom.

**Prevention.** Alert on `memory working-set / limit > 80%` and on RSS slope (a
rising slope = leak). Set limits from measured percentiles + headroom. See
[Memory §OOM killer](../operating_system/03_memory_management.md).

---

## 1.6 Memory leak vs legitimate growth — telling them apart

**Symptom.** RSS grows over hours/days, ending in OOMKilled (1.5) or swap death
(1.10). The key staff question: **leak, fragmentation, or real working set?**

**Triage.**
```bash
# Slope over time is the discriminator: leak = monotonic, never plateaus.
# Sample RSS periodically; plot it. Then attribute:
pmap -x <pid> | tail -1         # total mapped; growing anon = heap/leak
cat /proc/<pid>/smaps_rollup    # Rss / Pss / anon vs file-backed
jemalloc: MALLOC_CONF=prof:true # native heap profiles -> jeprof flame graph
```

**Root cause & fix.**
- **Heap leak** (unfreed allocations / lingering references): monotonic anon growth
  → fix the code; profile to the allocation site.
- **Fragmentation** (RSS ≫ live objects, common with glibc `ptmalloc` and many
  arenas): switch to **jemalloc/tcmalloc**, tune `MALLOC_ARENA_MAX`, or
  `malloc_trim`. RSS high but live set stable.
- **Page-cache attribution**: file-backed pages charged to the cgroup can inflate
  "memory usage" — reclaimable, not a leak (check `memory.stat` `file` vs `anon`).

**Prevention.** Continuous memory profiling; alert on RSS slope; load-test long
enough to surface slow leaks (a 0.1%/hour leak only shows after days). See
[Memory §allocators](../operating_system/03_memory_management.md).

---

## 1.7 Page-cache thrash & write-back stalls

**Symptom.** Latency spikes correlate with memory pressure; `free` shows little
"available"; the app stalls intermittently though it isn't allocating.

**Triage.**
```bash
free -m                  # 'available' is the number that matters, not 'free'
vmstat 1                 # 'si'/'so' (swap in/out) > 0 = swapping; 'bi'/'bo' = block I/O
cat /proc/meminfo        # Dirty / Writeback (pages awaiting flush)
sar -B 1                 # pgscan/pgsteal = reclaim activity (pressure)
cat /proc/pressure/memory # PSI: 'some'/'full' avg10 -> time stalled on memory
```

**Root cause.** Either reclaim pressure (the kernel evicting page cache / scanning
under low free memory) or a flood of **dirty pages** hitting write-back limits,
stalling writers. PSI (`/proc/pressure/memory`) quantifies the stall directly.

**Mitigate now.** Reduce memory pressure (scale, cut working set); tune
`vm.dirty_background_ratio`/`vm.dirty_ratio` so write-back starts earlier and in
smaller batches.

**Permanent fix.** Right-size memory to keep the hot working set + page cache
resident; for write-heavy workloads, smooth write-back (lower dirty ratios, faster
storage). See [Memory §page cache](../operating_system/03_memory_management.md) and
[Storage §write-back](../operating_system/05_file_systems_storage.md).

**Prevention.** Alert on **PSI memory `full avg10`** (the cleanest "am I stalling on
memory" signal) rather than on `free`.

---

## 1.8 NUMA regression — a "mysterious" 1.5–2× slowdown

**Symptom.** A service runs 1.5–2× slower with no code change — often after a
restart, a VM migration, or a scale-up to a multi-socket instance.

**Triage.**
```bash
numastat -p <pid>        # per-node memory; is memory on node 0 but threads on node 1?
numactl --hardware       # node topology + inter-node distances
perf stat -e node-loads,node-load-misses ./app   # remote-node memory access rate
```

**Root cause.** The thread is scheduled on socket 1 but its memory was allocated on
socket 0 → every access crosses the interconnect (~1.5–2× the latency of local RAM,
[CPU §8.2](../operating_system/02_cpu_scheduling.md)). Classic after a reschedule
across sockets or a large multi-socket instance.

**Mitigate now.**
```bash
numactl --cpunodebind=0 --membind=0 ./app   # pin threads AND memory to one node
```

**Permanent fix.** NUMA-aware placement: pin worker + its memory to the same node;
size workers per-node; enable/trust autonuma or pin explicitly for latency-critical
services. Prefer single-socket instances for latency SLOs if the app isn't
NUMA-aware.

**Prevention.** Track remote-memory ratio; make NUMA topology part of capacity
planning; alert on cross-node access spikes after deploys/migrations.

---

## 1.9 Transparent Huge Pages (THP) — stalls and bloat

**Symptom.** Latency-sensitive service (often a database — Redis, Mongo, Oracle all
warn about this) shows sporadic multi-ms stalls and inflated RSS.

**Triage.**
```bash
cat /sys/kernel/mm/transparent_hugepage/enabled   # [always] madvise never
grep -i AnonHugePages /proc/meminfo
grep thp /proc/vmstat    # thp_fault_alloc, thp_collapse_alloc, compact_stall
```

**Root cause.** THP's background `khugepaged` compaction and on-fault huge-page
allocation cause latency jitter and memory bloat for workloads with sparse/random
memory access — the opposite of THP's intended benefit.

**Mitigate / fix.** Set THP to `madvise` (or `never` for databases that recommend
it) so only code that asks for huge pages gets them:
```bash
echo madvise > /sys/kernel/mm/transparent_hugepage/enabled
echo defer+madvise > /sys/kernel/mm/transparent_hugepage/defrag
```

**Prevention.** Bake the database vendor's THP recommendation into the host image;
test latency with THP `always` vs `madvise`. (Explicit huge pages via `hugetlbfs`
are different and *do* help TLB-bound apps — see
[Memory §huge pages](../operating_system/03_memory_management.md).)

---

## 1.10 Swap death — the host that won't die but won't work

**Symptom.** The host becomes unresponsive but doesn't crash; everything is glacial;
SSH barely connects. CPU shows high `%wa`/`%sys`.

**Triage.**
```bash
vmstat 1                 # si/so (swap in/out) sustained and high = thrashing
free -m                  # Swap used climbing; available near zero
cat /proc/pressure/memory
```

**Root cause.** Overcommitted memory → the kernel swaps hot pages to disk → every
access faults to disk (~1000× RAM) → more reclaim → **thrashing**. The system spends
all its time paging, not working. (Without the OOM killer firing, it can limp
indefinitely.)

**Mitigate now.** Kill the memory hog (or let/trigger the OOM killer); add memory;
reduce working set.

**Permanent fix.** For latency-SLO services, **disable or minimize swap** (or use
`zram`/`zswap` for compressed swap as a softer landing); set `vm.swappiness` low;
right-size memory so the working set is resident. (Note: modern guidance with cgroup
v2 + PSI is nuanced — a little swap can be healthier than hard OOM for some
workloads — but uncontrolled swap on a latency service is a classic outage.)

**Prevention.** Alert on sustained `si/so` and PSI memory pressure *before* the
death spiral; capacity-plan memory with headroom. See
[Memory §swapping](../operating_system/03_memory_management.md).

---

## Quick-reference: symptom → first command

| Symptom | First look |
|---|---|
| Periodic p99 spikes, low avg CPU | `cat cpu.stat` → `nr_throttled` (1.1) |
| Slow with no code change | `mpstat -P ALL 1` `%steal`/noisy neighbor (1.2) |
| Load avg ≫ cores | `vmstat 1` `r` vs `b` (CPU vs I/O) (1.3) |
| One core at 100% `%soft` | `/proc/interrupts`, `/proc/softirqs` (1.4) |
| Exit 137 / OOMKilled | `dmesg | grep -i oom`; RSS vs limit (1.5) |
| RSS grows forever | RSS slope; heap profile (1.6) |
| Stalls under mem pressure | `/proc/pressure/memory` PSI (1.7, 1.10) |
| 1.5–2× slower, multi-socket | `numastat -p <pid>` (1.8) |

---

## Key takeaways

1. **"Low average CPU" hides CFS throttling** — always check `nr_throttled`, and
   prefer requests/weight over hard CPU limits for latency-critical pods.
2. **Load average counts I/O-blocked threads** — high load + idle CPU = an I/O
   problem, not a CPU problem.
3. **OOMKilled ≠ application OutOfMemory** — it's total RSS vs the cgroup limit; size
   the container above heap + non-heap, and tell a leak from a working set by the
   *slope*.
4. **PSI (`/proc/pressure/*`) is the modern saturation signal** — alert on it for
   CPU, memory, and I/O stalls rather than on raw utilization.
5. **NUMA and THP cause "mysterious" regressions** with no code change — pin
   memory+threads, and set THP to `madvise` for latency-sensitive services.
6. Every fix has a **prevention**: the right alert (throttle ratio, RSS slope, PSI,
   `%steal`) turns a 3am page into a dashboard you watched a week earlier.

> Next: [02 — I/O & Storage Incidents](02_io_storage_incidents.md) — fsync stalls,
> disk saturation, and the tail latency that lives in the storage layer.
