# 02 — I/O & Storage Incidents

> **Audience:** staff/principal on-call. Each scenario: **Symptom → Triage → Root
> cause → Mitigate now → Permanent fix → Prevention.** Theory in
> [File Systems & Storage](../operating_system/05_file_systems_storage.md) and
> [I/O Models](../operating_system/06_io_models_async.md).

> **The golden rule of I/O incidents:** a high load average with *idle CPU* is an
> I/O problem. Threads stuck in **D state** (uninterruptible sleep) are blocked in
> the kernel on I/O and count toward load while doing nothing.

---

## 2.1 Disk saturation — the storage device is the bottleneck

**Symptom.** Service latency tracks disk activity; database queries slow; load
average high but CPU mostly idle (`%wa` high).

**Triage.**
```bash
iostat -xz 1             # the one command. Watch:
#   %util   -> ~100% = device saturated (caveat: misleading for SSD/NVMe, see below)
#   aqu-sz  -> average queue depth; deep queue = backlog
#   r_await/w_await -> per-I/O latency in ms; rising = device struggling
#   r/s w/s rkB/s wkB/s -> IOPS and throughput
iotop -oPa              # which PROCESS is doing the I/O
cat /proc/pressure/io  # PSI: time stalled on I/O (the cleanest saturation signal)
```
> **`%util` lies on SSD/NVMe.** It means "the queue was non-empty," but these
> devices serve many I/Os in parallel — 100% `%util` on an NVMe can still have
> headroom. Trust **`await` (latency)** and **PSI io** over `%util` on modern disks.

**Root cause.** Demand (IOPS or bandwidth) exceeds the device's capacity, or
latency per I/O has risen (a failing disk, a throttled cloud volume hitting its IOPS
cap, or a noisy neighbor on shared storage).

**Mitigate now.** Shed I/O load; throttle the heavy process (`ionice`, cgroup
`io.max`); fail over reads to a replica; in cloud, the volume may have hit its
provisioned IOPS — bump it.

**Permanent fix.** Add IOPS/bandwidth (faster/bigger volume, NVMe), cache hot reads
(page cache / app cache), batch writes, or reduce write amplification. Right-size
cloud volume IOPS to peak, not average.

**Prevention.** Alert on **`await`** and **PSI io**, not `%util`; track cloud-volume
IOPS/throughput against the provisioned cap. See
[Storage §I/O schedulers](../operating_system/05_file_systems_storage.md).

---

## 2.2 fsync stall — the durability tax (the "fsyncgate" class)

**Symptom.** A write-heavy service (database, queue, WAL-based system) shows
periodic latency cliffs; commits/transactions stall in bursts; throughput is fine
between stalls.

**Triage.**
```bash
strace -f -T -e trace=fsync,fdatasync -p <pid>   # see fsync calls and their DURATION (<...>)
biolatency-bpfcc                                  # histogram of block I/O latency
# Are stalls aligned with write-back flushes?
cat /proc/meminfo | grep -E 'Dirty|Writeback'
```
`fsync` durations of tens-to-hundreds of ms in the strace output are the cause.

**Root cause.** `fsync()`/`fdatasync()` forces buffered data to **durable** storage
and blocks until the device confirms — it bypasses the page cache's speed. Under a
burst of dirty pages, or on slow/contended storage, fsync latency spikes and every
committing thread waits. (The "**fsyncgate**" lesson: on some kernels/filesystems an
fsync *error* even marked pages clean, silently losing data — durability handling is
subtle; Postgres changed its fsync error handling because of it.)

**Mitigate now.** Reduce fsync frequency where the durability contract allows
(group commit / batch commits); move WAL/journal to faster, dedicated storage.

**Permanent fix.**
- **Group commit:** batch many transactions into one fsync (databases do this;
  tune `commit_delay`/`group commit` settings).
- **Separate the WAL** onto its own fast device so journal fsyncs don't contend with
  data I/O.
- Use a device with low fsync latency (NVMe with power-loss protection); for the
  durability contract, understand `O_DSYNC` vs `fsync` vs `fdatasync`.

**Prevention.** Track fsync latency as a first-class metric; load-test durability
paths under write bursts. See
[Storage §fsync & fsyncgate](../operating_system/05_file_systems_storage.md).

---

## 2.3 Write-back stall — dirty pages flooding the device

**Symptom.** A burst of writes makes *all* I/O (including reads) stall for hundreds
of ms; the system "hiccups" after a large write.

**Triage.**
```bash
cat /proc/meminfo | grep -E 'Dirty|Writeback'    # large Dirty -> backlog to flush
vmstat 1                                           # 'bo' (blocks out) spikes; 'wa' high
sar -d 1                                            # device queue/util during the spike
```

**Root cause.** The kernel buffers writes in the page cache and flushes them
("write-back") in the background. When dirty pages exceed `vm.dirty_ratio`, the
kernel forces **synchronous** write-back — the writing process is blocked until
enough pages flush, and the device is saturated, stalling everyone. A classic
"copy a huge file → the whole box hiccups."

**Mitigate now.** Lower the dirty thresholds so flushing starts earlier and in
smaller increments:
```bash
sysctl -w vm.dirty_background_ratio=5   # start background flush at 5% (default 10)
sysctl -w vm.dirty_ratio=10             # force sync write-back at 10% (default 20)
# Or the byte-based variants on big-RAM boxes: vm.dirty_background_bytes / vm.dirty_bytes
```

**Permanent fix.** Tune dirty ratios for the workload + RAM size (on a 256 GB box,
20% dirty = 51 GB of buffered writes — far too much before a flush); faster storage;
for bulk writes use `O_DIRECT` or paced/throttled writers.

**Prevention.** Set dirty *bytes* (not ratio) on large-memory hosts; test bulk-write
paths. See [Storage §write-back](../operating_system/05_file_systems_storage.md).

---

## 2.4 Disk space & inode exhaustion — the silent killer

**Symptom.** Writes suddenly fail with `ENOSPC` ("No space left on device") — even
though `df -h` shows free space (the inode case). Services crash or corrupt state;
logs stop.

**Triage.**
```bash
df -h                    # block space per filesystem
df -i                    # INODES — can be exhausted with disk space FREE (many tiny files)
du -sh /* 2>/dev/null | sort -h        # where did the space go
lsof | grep deleted      # DELETED-but-open files holding space (log rotated but fd open)
```

**Root cause.** Three classic variants:
- **Disk full** — runaway logs, a core dump, an unbounded cache/temp dir.
- **Inodes exhausted** — millions of tiny files (e.g. a session/cache dir) — `df -h`
  shows free space but you can't create a file.
- **Deleted-but-open** — a process holds an fd to a rotated/deleted file; space isn't
  reclaimed until the process closes it or restarts (`lsof | grep deleted`).

**Mitigate now.** Free space (truncate/rotate logs, clear temp); for deleted-but-open,
restart the holding process or `truncate` via `/proc/<pid>/fd/<n>`.

**Permanent fix.** Log rotation with size caps; bounded caches/temp with TTL eviction;
separate volume for logs/data so a log flood can't take down the data partition;
monitor inodes, not just bytes.

**Prevention.** Alert at 80% on **both** `df -h` **and** `df -i`; cap log/core sizes
(`logrotate`, `ulimit -c`); enforce per-service disk quotas.

---

## 2.5 Slow-disk tail latency — one bad device drags the fleet

**Symptom.** Most requests are fast; a small percentage (p99/p999) are very slow, and
the slow ones cluster on specific hosts/disks. A degrading-but-not-dead disk.

**Triage.**
```bash
biolatency-bpfcc                # bimodal histogram: most fast, a tail at ms+
biosnoop-bpfcc                  # per-I/O: which device/PID has the slow ones
smartctl -a /dev/nvme0n1        # SMART: reallocated sectors, media errors, wear
iostat -xz 1                    # compare await across devices/hosts
```

**Root cause.** A failing/aging disk with rising per-I/O latency, a SMR drive doing
background reorganization, or a cloud volume being throttled — a *latency* problem,
not a *throughput* one. Fan-out queries (Chapter on tail latency) wait on the slowest
device, so one bad disk poisons p99 across many requests.

**Mitigate now.** Drain/replace the bad host; route reads around it (replica reads);
**hedged requests** (send a duplicate to another replica after a short delay, take the
first to return) hide single-disk tails.

**Permanent fix.** Proactive disk health monitoring + auto-drain; hedged reads for
latency-critical fan-out; spread data so no single disk is on every request's path.

**Prevention.** SMART monitoring with auto-remediation; alert on per-device `await`
outliers; design for tail tolerance (hedging, quorum reads).

---

## 2.6 NFS / network filesystem hang — D-state everywhere

**Symptom.** Processes touching a mounted network filesystem hang **uninterruptibly**
(can't even `kill -9`); load average climbs as threads pile into D state; the NFS
server or network had a blip.

**Triage.**
```bash
ps -eo pid,stat,wchan,cmd | grep ' D'    # D-state procs; wchan shows nfs_* = NFS wait
mount | grep nfs                          # check mount options (hard vs soft)
nfsstat -c                                # client RPC retransmits/timeouts
dmesg | grep -i 'nfs.*not responding'
```

**Root cause.** A **hard** NFS mount (the safe default for data integrity) blocks I/O
**forever** when the server is unreachable, and those blocked threads are
uninterruptible (D state) — you can't kill them, and they accumulate until the box is
unusable. The network/server problem becomes a host problem.

**Mitigate now.** Restore the NFS server / network; the hung threads resume. If the
server is gone for good, a lazy unmount (`umount -l`) + reboot may be required (D-state
threads can't be killed).

**Permanent fix.** Choose mount semantics deliberately: `hard,intr` (older) or modern
timeouts (`soft,timeo=,retrans=`) trade integrity for liveness — `soft` can cause data
loss/corruption, so use it only for read-only/cache mounts. Better: avoid NFS on the
critical path for latency-SLO services; use object storage with app-level timeouts.

**Prevention.** Monitor D-state thread count and NFS RPC retransmits; isolate NFS
dependencies; set client timeouts so a server blip degrades gracefully instead of
hanging the host. See [Storage §NFS](../operating_system/05_file_systems_storage.md).

---

## 2.7 Read amplification / cold cache — "fast in staging, slow in prod"

**Symptom.** A query/endpoint is fast in testing but slow in production, or slow right
after a deploy/restart, then speeds up.

**Triage.**
```bash
# Is it serving from page cache or hitting disk?
vmstat 1                 # high 'bi' (blocks in) during the slow period = disk reads
cachestat-bpfcc          # page-cache hit ratio over time
free -m                  # was the cache cold (just restarted / evicted)?
```

**Root cause.** **Cold page cache.** In prod the working set may not fit in RAM, or a
restart/deploy emptied the cache, so reads hit disk (~100–1000× slower than cache)
until it warms. Staging is fast because its small dataset fits in cache. Or genuine
**read amplification** (an index miss forcing a full scan, a wrong access pattern).

**Mitigate now.** Warm the cache (pre-read hot data, `vmtouch`); roll deploys slowly
so caches warm; add a readiness gate until the cache is warm.

**Permanent fix.** Size RAM for the hot working set; fix the access pattern (the right
index — see [Data-Intensive Systems in the language books]); cache hot reads at the
app layer; for restarts, cache-warming on startup.

**Prevention.** Track page-cache hit ratio; capacity-plan RAM to working set; canary
deploys watch latency as caches warm. See
[Memory §page cache](../operating_system/03_memory_management.md).

---

## Quick-reference: symptom → first command

| Symptom | First look |
|---|---|
| High load, idle CPU, `%wa` high | `iostat -xz 1` (await), PSI io (2.1) |
| Periodic commit/latency cliffs (write-heavy) | `strace -T -e fsync` (2.2) |
| Whole box hiccups after a big write | `/proc/meminfo` Dirty (2.3) |
| `ENOSPC` with free space showing | `df -i` (inodes), `lsof | grep deleted` (2.4) |
| Only p99/p999 slow, clustered on hosts | `biolatency`, `smartctl` (2.5) |
| Procs hung, unkillable (D state) | `ps -eo stat,wchan`, NFS (2.6) |
| Fast in staging, slow in prod | page-cache hit ratio, `vmstat bi` (2.7) |

---

## Key takeaways

1. **`iostat` `await` and PSI io beat `%util`** on SSD/NVMe — measure latency and
   stall time, not a queue-non-empty flag.
2. **`fsync` is the durability tax** — periodic write-heavy stalls are fsync; fix with
   group commit and a dedicated fast WAL device (and respect the fsyncgate lesson on
   error handling).
3. **Write-back stalls** come from too-high dirty ratios on big-RAM boxes — tune
   `vm.dirty_bytes` so flushing is early and incremental.
4. **`ENOSPC` with free space = inodes or deleted-but-open files** — monitor `df -i`
   and `lsof | grep deleted`, not just `df -h`.
5. **One degrading disk poisons p99** via fan-out — monitor SMART, auto-drain, and use
   **hedged requests** to tolerate single-device tails.
6. **Hard NFS mounts turn a server blip into unkillable D-state threads** — keep
   network filesystems off the latency-critical path.
7. **"Fast in staging, slow in prod" is usually a cold/too-small page cache** — size
   RAM to the working set and warm caches on deploy.

> Next: [03 — Concurrency Incidents](03_concurrency_incidents.md) — deadlocks, lock
> convoys, thundering herds, and pool exhaustion.
