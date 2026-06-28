# 08 — Linux Internals & Performance Observability

> **Audience:** staff/principal. You can read a `top` output. This doc is about *how a Linux box boots, what the kernel exposes through `/proc` and `/sys`, and how to systematically find the bottleneck* on a production system using a methodology (USE) instead of guesswork — down to eBPF when the standard tools run out.
>
> **Primary sources:** Brendan Gregg, *Systems Performance* (2nd ed., 2020) and *BPF Performance Tools* (2019); the USE method (Gregg, 2012); the `proc(5)`, `sysctl(8)`, `systemd(1)`, `bootup(7)`, `perf(1)`, and `bpftrace(8)` man pages; the Linux kernel `Documentation/admin-guide/` tree (sysctl, cgroup-v2, mm); the systemd documentation.

---

## 1. Why this matters at scale

When a production system is "slow," the cost of being wrong is measured in incident minutes times the number of engineers staring at dashboards. The difference between a senior and a principal engineer here is **method**: not "let me restart it" or "let me look at CPU," but a *systematic sweep* that finds the bottleneck in minutes and produces evidence, not vibes.

Two ideas anchor this doc:

1. **Linux already instruments itself.** Almost everything you need is exposed through the `/proc` and `/sys` pseudo-filesystems and through tracepoints/kprobes. The standard tools are thin readers over these. Knowing where the numbers come from lets you trust them and go deeper when they lie.
2. **A methodology beats a tool list.** Gregg's **USE method** turns "the server is slow" into a finite checklist over every resource. eBPF/bpftrace then lets you ask *arbitrary* questions of the live kernel with near-zero overhead — the modern superpower for production debugging.

```
   "the server is slow"
        |
   USE method  ──> per-resource checklist (CPU, mem, disk, net, ...)
        |
   standard tools (top/vmstat/iostat/pidstat/ss/sar)  ──> narrow it down
        |
   perf / strace / ftrace / bpftrace  ──> root cause, in the kernel
```

---

## 2. The boot process: firmware → init → your service

```text
  power on
     |
  [1] FIRMWARE: BIOS (legacy) or UEFI (modern)
        - POST, init hardware, find a boot device
        - UEFI: reads the EFI System Partition (ESP), runs a .efi bootloader
        - Secure Boot verifies signatures here
     |
  [2] BOOTLOADER: GRUB2 (or systemd-boot)
        - presents menu, loads the KERNEL (vmlinuz) + INITRAMFS into memory
        - passes the kernel command line (root=, ro, quiet, etc.)
     |
  [3] KERNEL
        - decompresses, sets up memory mgmt, scheduler, drivers
        - mounts the INITRAMFS as a temporary root (has the drivers/modules
          needed to find and mount the *real* root fs, e.g. LVM/encryption)
        - switches to the real root (switch_root) and execs PID 1
     |
  [4] PID 1 = init  (on modern distros: systemd)
        - brings up the system per its unit dependency graph
        - reaches the default target (e.g. multi-user.target / graphical.target)
     |
  [5] your services start (sshd.service, nginx.service, ...)
```

- **BIOS vs UEFI**: BIOS is 16-bit legacy with a 512-byte MBR boot sector and a 2 TiB disk limit; **UEFI** is the modern firmware — GPT partitions, an EFI System Partition holding `.efi` bootloaders, **Secure Boot** (signature verification of the boot chain), and faster init. Almost all modern servers are UEFI.
- **initramfs** exists to solve a chicken-and-egg: the kernel needs drivers/modules to mount the real root (which may be on LVM, RAID, NVMe-over-fabric, or encrypted), but those live *on* the root. The initramfs is a small in-RAM root carrying exactly those modules, used to pivot to the real root.
- **PID 1 is special**: it can never die (kernel panic if it does), and it reaps orphaned zombies. On modern Linux it's **systemd**.

### 2.1 Inspecting the boot

```bash
# How long did boot take, and what was slow?
systemd-analyze                      # total firmware+loader+kernel+userspace time
systemd-analyze blame | head -20     # slowest units to initialize
systemd-analyze critical-chain       # the dependency-ordered critical path
cat /proc/cmdline                    # the kernel command line GRUB passed
dmesg | head -40                     # kernel ring buffer from earliest boot
```

---

## 3. systemd: units and service management

systemd models the system as a **dependency graph of units**. A unit is a typed configuration object:

| Unit type | Purpose |
|---|---|
| `.service` | a daemon/process to manage (start/stop/restart, watchdog) |
| `.socket` | socket-activation: systemd holds the socket, starts the service on first connection |
| `.target` | a grouping/sync point (the systemd analog of SysV runlevels; e.g. `multi-user.target`) |
| `.timer` | cron replacement (calendar or monotonic schedules) |
| `.mount` / `.automount` | filesystem mounts (generated from `/etc/fstab`) |
| `.device`, `.path`, `.slice`, `.scope` | udev devices, path-watch activation, **cgroup slices** for resource control |

The key insight: **systemd places every service in its own cgroup** (a `.scope`/`.slice`), which is how `systemctl status` can show per-service memory/CPU and how you set resource limits declaratively (`MemoryMax=`, `CPUQuota=` in the unit) — it's writing the cgroup files from doc 07 for you.

```bash
# Daily-driver service management:
systemctl status nginx               # state, PID, cgroup, recent logs, memory
systemctl start|stop|restart nginx
systemctl enable --now nginx         # start now + on boot
systemctl list-units --type=service --state=running
systemctl list-dependencies multi-user.target

# Logs (journald is systemd's structured log store):
journalctl -u nginx -f               # follow one unit's logs
journalctl -u nginx --since "10 min ago" -p err   # errors only, time-windowed
journalctl -k -b                     # kernel messages, this boot
journalctl --disk-usage              # how much the journal is using

# Resource control on a running service (writes the cgroup, persists):
systemctl set-property nginx.service MemoryMax=512M CPUQuota=50%
```

A minimal hardened service unit, showing the cgroup/sandbox knobs a principal engineer should reach for:

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My App
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/myapp
Restart=on-failure
RestartSec=2
# --- resource control (cgroup v2) ---
MemoryMax=512M
CPUQuota=50%
TasksMax=256
# --- sandboxing (defense in depth) ---
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
SystemCallFilter=@system-service        # seccomp allowlist
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
```

---

## 4. /proc and /sys: the kernel's API as files

Linux exposes kernel and process state as **pseudo-filesystems** — files that are generated on read, not stored on disk. This is the data source under every observability tool.

- **`/proc`** — per-process and system-wide kernel state. Per-PID directories `/proc/<pid>/` plus global files.
- **`/sys`** (sysfs) — a structured view of the **device/driver model** and kernel objects (kobjects); also where many tunables live.

| Path | What it gives you |
|---|---|
| `/proc/<pid>/status` | human-readable state, memory (`VmRSS`), UIDs, threads, caps |
| `/proc/<pid>/stat` | machine-readable per-process counters (utime, stime, rss, state) |
| `/proc/<pid>/fd/` | open file descriptors (symlinks) — find leaks, what a process has open |
| `/proc/<pid>/maps`, `/smaps` | memory map; `/smaps` adds per-mapping RSS/PSS/swap |
| `/proc/<pid>/io` | bytes read/written (incl. actual disk via `read_bytes`) |
| `/proc/<pid>/limits` | the effective ulimits for that process |
| `/proc/stat` | system CPU time breakdown (user/sys/idle/iowait), ctxt switches, boot time |
| `/proc/meminfo` | total/free/available memory, cache, dirty, swap |
| `/proc/loadavg` | 1/5/15-min load averages + running/total tasks |
| `/proc/diskstats` | per-device I/O counters (what `iostat` reads) |
| `/proc/net/dev`, `/proc/net/tcp` | per-interface counters; socket tables |
| `/proc/pressure/{cpu,memory,io}` | **PSI** — Pressure Stall Information (saturation, see §5) |
| `/sys/block/<dev>/queue/scheduler` | the I/O scheduler for a disk |
| `/sys/fs/cgroup/...` | cgroup v2 controllers (doc 07) |
| `/sys/kernel/debug/tracing/` | ftrace control (§7) |

```bash
# A few you'll actually use under fire:
cat /proc/loadavg                       # 2.15 1.80 1.42 3/512 18734
awk '/VmRSS/{print $2" kB"}' /proc/$(pgrep -n nginx)/status   # resident set
ls -l /proc/$(pgrep -n nginx)/fd | wc -l                       # open FDs (leak check)
cat /proc/pressure/io                   # is the system stalled waiting on I/O?
```

---

## 5. The USE method (Gregg) — the systematic sweep

The **USE method**: for **every resource**, check three things.

| Metric | Definition | Question it answers |
|---|---|---|
| **Utilization** | % of time the resource was busy (or % capacity used) | Is it *busy*? |
| **Saturation** | degree of queued/waiting work it can't service yet | Is work *backing up*? |
| **Errors** | count of error events | Is it *failing*? |

> The procedure: enumerate resources (CPUs, memory, disks, NICs, controllers, buses), and for each, find U, S, E. **Saturation is the most under-watched and most predictive** — a resource at 100% utilization with no queue is fine; one at 70% with a growing queue is your bottleneck. Linux **PSI** (`/proc/pressure/*`) gives saturation directly: it reports the % of time tasks were *stalled* waiting on CPU, memory, or I/O.

The USE checklist mapped to tools:

| Resource | Utilization | Saturation | Errors |
|---|---|---|---|
| **CPU** | `mpstat -P ALL`, `top` (%usr+%sys) | run-queue: `vmstat` `r` col, `/proc/pressure/cpu`, load avg | (rare) `mcelog`, `dmesg` |
| **Memory** | `free`, `/proc/meminfo` | swapping: `vmstat` `si/so`, `/proc/pressure/memory`, **OOM kills** in `dmesg` | ECC errors `dmesg`/`edac` |
| **Disk** | `iostat -xz 1` `%util` | `iostat` `aqu-sz` (queue), `await`, `/proc/pressure/io` | `dmesg`, SMART, `/sys/.../ioerr_cnt` |
| **Network** | `sar -n DEV 1`, `/proc/net/dev` | `ss -s`, drops/overruns in `ip -s link`, retransmits | `ip -s link` errors, `netstat -s` |

**The load average myth:** Linux load average counts tasks in state **R (running/runnable) OR D (uninterruptible sleep — usually disk/IO wait)**, *not* just CPU demand. A load of 50 on a 4-core box might be 4 CPU-bound tasks plus 46 stuck in `D` waiting on a slow NFS mount. Always cross-check load with `vmstat`'s `r` (runnable) vs `b` (blocked) columns and PSI before concluding "CPU is the problem."

---

## 6. The core observability toolset

```text
                       Linux observability tools, by resource
   CPU      : top/htop, mpstat, pidstat, vmstat (r), perf, /proc/pressure/cpu
   Memory   : free, vmstat (si/so), pidstat -r, /proc/meminfo, slabtop, smem
   Disk     : iostat -xz, pidstat -d, biolatency (bpf), /proc/pressure/io
   Network  : ss, sar -n, ip -s link, tcptop/tcpretrans (bpf), nstat
   Per-proc : pidstat (all-in-one), strace/ltrace (syscalls/libcalls), /proc/<pid>/*
   System   : sar (historical!), dstat, vmstat, perf, ftrace, bpftrace
```

Quick reference for the ones that earn their keep:

```bash
# --- CPU ---
mpstat -P ALL 1        # per-CPU %usr/%sys/%iowait/%idle — spot a single hot core
pidstat 1              # per-process CPU each second (better than ps for "who")
vmstat 1               # r=runnable (CPU sat), b=blocked, plus mem/swap/io/cpu

# --- Memory ---
free -h                # used/free/available/buff-cache/swap (watch "available")
vmstat 1               # si/so (swap in/out): NONZERO si/so = memory saturation
pidstat -r 1           # per-process minor/major faults + RSS

# --- Disk ---
iostat -xz 1           # %util, await (latency!), aqu-sz (queue), r/s w/s, rkB/s
pidstat -d 1           # per-process disk read/write bytes

# --- Network ---
ss -tanp               # sockets, states, owning process (the modern netstat)
ss -s                  # summary incl. socket counts by state
sar -n DEV 1           # per-NIC throughput; sar -n TCP,ETCP 1 for retransmits
ip -s link             # per-interface errors/drops/overruns

# --- Historical (sar reads pre-collected data — invaluable post-incident) ---
sar -u -f /var/log/sa/sa$(date +%d)    # CPU history for today
sar -r ; sar -b ; sar -n DEV           # mem / io / net history
```

### 6.1 Tracing tools: strace, ltrace, ftrace, perf

- **`strace`** — traces **syscalls** of one process (via `ptrace`). Indispensable for "why is this hanging / what file/socket is it touching," but **high overhead** (every syscall stops the process twice) — never attach to a hot production process without `-f -e trace=...` filtering. `strace -c` gives a syscall-time summary.
- **`ltrace`** — like strace but for **library calls** (e.g. `malloc`, `libssl`). Useful to see app-level behavior; even higher overhead.
- **`ftrace`** — the kernel's built-in tracer (`/sys/kernel/debug/tracing/`). Function tracing, function-graph (call timing), tracepoints. Low overhead, always present. Often driven by the `trace-cmd` front end.
- **`perf`** — the swiss-army profiler. CPU profiling (sampling the stack), PMU/hardware counters (cache misses, branch mispredicts, IPC), tracepoints. The basis of CPU flame graphs.

```bash
# strace: what syscalls is a stuck process making? (filter to keep overhead sane)
strace -f -tt -e trace=network,file -p <pid>
strace -c -p <pid>            # summary: time spent per syscall (Ctrl-C to print)

# perf: sample on-CPU stacks for 30s, then summarize
perf record -F 99 -a -g -- sleep 30      # 99 Hz, all CPUs, with call graphs
perf report --stdio | head -40

# perf: hardware-counter view — is this workload memory-bound? (low IPC, high misses)
perf stat -d -p <pid> sleep 10
```

### 6.2 Flame graphs

A **flame graph** (Gregg) visualizes sampled stack traces: the x-axis is *population* (how many samples included that stack — i.e., proportional time), **not time order**; the y-axis is stack depth. Wide frames = where the CPU actually spends time. It collapses thousands of stacks into one picture so you find the hot path at a glance.

```bash
# CPU flame graph from perf (using Brendan Gregg's FlameGraph scripts):
perf record -F 99 -a -g -- sleep 30
perf script | ./stackcollapse-perf.pl | ./flamegraph.pl > cpu.svg
# Open cpu.svg: the widest tower from the bottom up is your hot code path.
```

There are also **off-CPU flame graphs** (sampling stacks while a thread is *blocked*, via scheduler tracepoints/bpf) — the right tool when the problem is *waiting* (locks, I/O) rather than burning CPU.

---

## 7. eBPF / bpftrace: arbitrary questions of the live kernel

**eBPF** runs small, verified programs in the kernel attached to **tracepoints, kprobes/uprobes, and perf events** — letting you measure things no static tool exposes, aggregate in-kernel (so you ship summaries, not every event), with overhead low enough for production. **bpftrace** is the high-level language for one-liners; **bcc** is the Python/C toolkit for fuller tools (`biolatency`, `execsnoop`, `tcplife`, etc.).

The mental model: `probe { filter } { action }`. Maps (`@`) aggregate in-kernel; `hist()`/`lhist()` build histograms cheaply.

### 7.1 Production one-liner cookbook

```bash
# 1) Who is exec()ing what? (catch surprise processes, cron jobs, shellouts)
bpftrace -e 'tracepoint:syscalls:sys_enter_execve { printf("%s -> %s\n", comm, str(args->filename)); }'

# 2) Block-I/O latency as a histogram (find tail-latency disk stalls)
bpftrace -e 'tracepoint:block:block_rq_issue { @start[args->dev] = nsecs; }
             tracepoint:block:block_rq_complete /@start[args->dev]/ {
               @usecs = hist((nsecs - @start[args->dev]) / 1000); delete(@start[args->dev]); }'

# 3) Count syscalls by process over 10s (find a syscall-storm)
bpftrace -e 'tracepoint:raw_syscalls:sys_enter { @[comm] = count(); }
             interval:s:10 { exit(); }'

# 4) Distribution of read() sizes per process (spot tiny inefficient I/O)
bpftrace -e 'tracepoint:syscalls:sys_enter_read { @[comm] = hist(args->count); }'

# 5) New TCP connections with latency (who is the app talking to, how slow?)
bpftrace -e 'kprobe:tcp_connect { @conn[tid] = nsecs; }
             kretprobe:tcp_connect /@conn[tid]/ { @us = hist((nsecs-@conn[tid])/1000); delete(@conn[tid]); }'

# 6) Page-cache misses causing disk reads, by file (where is RAM not enough?)
bpftrace -e 'kprobe:vfs_read { @[comm] = count(); } interval:s:5 { print(@); clear(@); }'

# 7) Off-CPU time by stack (why are threads BLOCKED, not running?)
bpftrace -e 'kprobe:finish_task_switch { @[kstack] = count(); }'

# bcc equivalents you should know by name:
#   execsnoop  — trace new processes      biolatency — disk I/O latency histogram
#   tcplife    — TCP session summaries     opensnoop  — files being opened
#   runqlat    — CPU run-queue latency      profile   — CPU profiler -> flame graph
#   cachestat  — page cache hit/miss rate   ext4slower — slow FS ops over a threshold
```

These are the tools that turn "the p99 is bad sometimes" into "block device `nvme0n1` has a bimodal latency distribution with a 40 ms tail every 5s, correlated with a flush" — a root cause, in one command, on a live box.

---

## 8. Bottleneck diagnosis by resource

### 8.1 CPU
- **Utilization**: `mpstat -P ALL 1`. Is it *one* core pinned (single-threaded hot path) or all cores? `%sys` high → kernel/syscall heavy (profile with perf); `%usr` high → app code (flame graph); `%iowait` high → it's really *disk*, not CPU.
- **Saturation**: `vmstat 1` `r` column > number of CPUs means runnable tasks are waiting for a core. `/proc/pressure/cpu` `some avg10` rising confirms it.
- **Root-cause**: `perf record -g` → flame graph to see *which functions*.

### 8.2 Memory
- **Utilization**: `free -h` — watch **`available`**, not `free` (Linux uses free RAM as page cache; `free` looking low is normal and healthy).
- **Saturation**: `vmstat 1` — **nonzero `si`/`so`** (swap in/out) means memory pressure; `/proc/pressure/memory` rising; major faults in `pidstat -r`.
- **Errors/the cliff**: the **OOM killer** (§9). When over-committed and out of swap, the kernel kills the process with the highest `oom_score`.

### 8.3 Disk
- **Utilization**: `iostat -xz 1` `%util` (near 100% = saturated *for a single queue*; for SSDs/NVMe with deep queues, prefer latency).
- **Saturation**: `await` (avg I/O latency, ms) and `aqu-sz` (avg queue depth) rising; `/proc/pressure/io`.
- **Root-cause**: `biolatency` (bpf) for the latency distribution; `pidstat -d` / `iotop` for *which process*; check the I/O scheduler in `/sys/block/<dev>/queue/scheduler`.

### 8.4 Network
- **Utilization**: `sar -n DEV 1` throughput vs link speed.
- **Saturation**: `ss -s`, TCP **retransmits** (`nstat`, `netstat -s | grep -i retrans`), send/recv queue backlog in `ss -tanp` (`Send-Q`/`Recv-Q`), drops/overruns in `ip -s link`.
- **Root-cause**: `tcpretrans`/`tcplife` (bpf), `tcpdump` for the actual packets.

---

## 9. Kernel logs, tunables, ulimits, and the OOM killer

### 9.1 dmesg & kernel logs
`dmesg` reads the kernel **ring buffer** — hardware errors, driver messages, OOM kills, segfaults, network link flaps, filesystem errors. First place to look for anything that smells like the kernel or hardware.

```bash
dmesg -T --level=err,warn        # human timestamps, errors+warnings only
dmesg -w                         # follow (like tail -f) for the kernel log
journalctl -k -b -p err          # kernel msgs this boot, errors, via journald
```

### 9.2 sysctl — kernel tunables
Kernel parameters live under `/proc/sys/` and are read/written via `sysctl` (persisted in `/etc/sysctl.d/*.conf`). The ones principal engineers reach for:

```bash
# Inspect / set at runtime:
sysctl net.core.somaxconn               # max accept-queue backlog (raise for high-conn servers)
sysctl -w vm.swappiness=10              # bias against swapping (DB/latency-sensitive)
sysctl -w net.ipv4.tcp_tw_reuse=1       # reuse TIME-WAIT sockets (high churn)
sysctl -w fs.file-max=2000000           # system-wide open-file ceiling

# Persist (survives reboot):
echo 'net.core.somaxconn = 4096' | sudo tee /etc/sysctl.d/99-tuning.conf
sudo sysctl --system                    # reload all sysctl.d files
```

Common production-relevant knobs: `vm.swappiness`, `vm.dirty_ratio`/`vm.dirty_background_ratio` (writeback aggressiveness), `vm.overcommit_memory`, `net.core.somaxconn`, `net.ipv4.tcp_max_syn_backlog`, `net.ipv4.ip_local_port_range` (ephemeral port exhaustion), `fs.file-max`, `fs.inotify.max_user_watches` (the classic "too many open files" for watchers).

### 9.3 ulimits — per-process resource limits
`ulimit` (the shell front end to `setrlimit(2)`) caps per-process resources. The one that bites everyone: **`nofile`** (max open file descriptors) — a busy server hitting it throws `EMFILE`/"Too many open files" and stalls.

```bash
ulimit -n                       # current open-files soft limit (often 1024 — too low!)
ulimit -a                       # all limits
cat /proc/<pid>/limits          # the EFFECTIVE limits of a running process (trust this)
# Raise for a systemd service (the modern way — /etc/security/limits.conf is ignored by systemd):
#   [Service]
#   LimitNOFILE=1048576
```

### 9.4 The OOM killer
When the kernel cannot satisfy an allocation and cannot reclaim/swap, the **OOM killer** picks a victim by `oom_score` (roughly proportional to memory use, adjustable via `oom_score_adj`) and `SIGKILL`s it. The evidence is always in `dmesg`/journald:

```bash
dmesg -T | grep -i -A1 'killed process'
# Out of memory: Killed process 4821 (java) total-vm:..., anon-rss:..., ...
journalctl -k | grep -i 'out of memory'
```

Per-container/per-cgroup, the **cgroup OOM killer** (doc 07) does the same *locally* when `memory.max` is hit — you'll see `memory.events` `oom_kill` increment and a cgroup-scoped dmesg line. Tuning: protect critical procs with `oom_score_adj=-1000`, set `vm.overcommit_memory` deliberately, and size cgroup limits so one tenant's leak can't take the node.

---

## 10. Working example: a mini-`top` from /proc (Python)

This *runnable* script builds a tiny `top` by reading only `/proc` — demonstrating exactly where `top`/`htop` get their numbers (CPU% from delta of `utime+stime` over an interval against total jiffies; RSS from `statm`). It parses cleanly and runs on any Linux with Python 3.

```python
#!/usr/bin/env python3
"""mini_top.py — a minimal `top` built directly from /proc.

CPU% is computed the same way top does: the change in a process's CPU jiffies
(utime+stime) over the change in *total* system CPU jiffies, across one interval.
Run:  python3 mini_top.py        (Linux only)
"""
import os
import time

CLK_TCK = os.sysconf("SC_CLK_TCK")          # jiffies per second (usually 100)
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024


def total_cpu_jiffies() -> int:
    """Sum of all fields on the aggregate 'cpu' line of /proc/stat."""
    with open("/proc/stat") as f:
        parts = f.readline().split()        # 'cpu', user, nice, system, idle, ...
    return sum(int(x) for x in parts[1:])


def process_cpu_jiffies(pid: int):
    """Return (utime+stime jiffies, comm, rss_kb) for a pid, or None if it's gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # comm may contain spaces/parens; it's wrapped in the first '(' .. last ')'.
        lp, rp = data.index("("), data.rindex(")")
        comm = data[lp + 1:rp]
        fields = data[rp + 2:].split()      # field 4 onward (0-indexed after comm)
        # In /proc/stat fields (after comm) utime=#14, stime=#15 of the man page,
        # which are indices 11 and 12 in this post-comm slice.
        utime, stime = int(fields[11]), int(fields[12])
        with open(f"/proc/{pid}/statm") as f:
            rss_pages = int(f.read().split()[1])   # field 2 = resident set in pages
        return utime + stime, comm, rss_pages * PAGE_KB
    except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
        return None


def snapshot():
    """Map pid -> (cpu_jiffies, comm, rss_kb) for all current processes."""
    procs = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            r = process_cpu_jiffies(int(name))
            if r:
                procs[int(name)] = r
    return procs


def main(interval: float = 1.0, top_n: int = 15):
    ncpu = os.cpu_count() or 1
    prev_total = total_cpu_jiffies()
    prev = snapshot()
    while True:
        time.sleep(interval)
        cur_total = total_cpu_jiffies()
        cur = snapshot()
        total_delta = max(1, cur_total - prev_total)   # avoid div-by-zero

        rows = []
        for pid, (cpu_j, comm, rss_kb) in cur.items():
            if pid in prev:
                cpu_delta = cpu_j - prev[pid][0]
                # top-style: share of TOTAL cpu * number of CPUs => 0..100*ncpu
                cpu_pct = 100.0 * ncpu * cpu_delta / total_delta
                rows.append((cpu_pct, pid, comm, rss_kb))

        rows.sort(reverse=True)
        with open("/proc/loadavg") as f:
            load = f.read().split()[:3]

        os.system("clear")
        print(f"mini-top  load avg: {' '.join(load)}   cpus: {ncpu}   "
              f"(interval {interval}s)")
        print(f"{'PID':>7} {'CPU%':>7} {'RSS(MB)':>9}  COMMAND")
        for cpu_pct, pid, comm, rss_kb in rows[:top_n]:
            print(f"{pid:>7} {cpu_pct:>7.1f} {rss_kb/1024:>9.1f}  {comm}")

        prev_total, prev = cur_total, cur


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
```

What it teaches: `top`/`htop` are not magic — CPU% is a **delta over an interval** (a single `/proc/<pid>/stat` read tells you nothing about *rate*; you need two samples), normalized against total system jiffies and scaled by CPU count. RSS comes from `statm` in pages. Every metric in this doc has an equally concrete source under `/proc` or `/sys`.

---

## 11. The "the server is slow" triage runbook (USE method)

A copy-pasteable first-90-seconds sweep — Gregg's "60-second checklist," organized by USE. Run top-to-bottom; the goal is to localize to one resource, then go deep with §7/§8.

```bash
# 0) Context: how long, how bad, is it the box or one app?
uptime                       # load avg trend (1/5/15) — rising or settling?
dmesg -T | tail -30          # OOM kills? hardware/driver errors? FS errors?

# 1) CPU — utilization + saturation
vmstat 1 5                   # r (runnable > #CPU = CPU sat), b (blocked = I/O), us/sy/wa
mpstat -P ALL 1 3            # one hot core vs all? high %sys? high %iowait (=disk!)?
cat /proc/pressure/cpu       # PSI: are tasks STALLED waiting for CPU?

# 2) Memory — saturation is what matters (swap + OOM)
free -h                      # look at AVAILABLE, not free
vmstat 1 5                   # si/so nonzero => swapping => memory bottleneck
cat /proc/pressure/memory    # PSI memory stall %

# 3) Disk — latency is king
iostat -xz 1 3               # %util, AWAIT (latency!), aqu-sz (queue depth)
cat /proc/pressure/io        # PSI io stall %

# 4) Network
ss -s                        # socket summary; lots of TIME-WAIT/CLOSE-WAIT?
sar -n DEV 1 3               # throughput vs link; ip -s link for drops/errors
nstat -az | grep -i retrans  # TCP retransmits = loss/saturation

# 5) Who? — attribute to a process
pidstat 1 3                  # per-proc CPU
pidstat -d 1 3               # per-proc disk
pidstat -r 1 3               # per-proc faults + RSS

# 6) Go deep on the localized resource:
#    CPU-bound  -> perf record -F 99 -a -g -- sleep 30; flame graph
#    I/O-bound  -> bpftrace biolatency / tcpretrans
#    Stuck/hung -> strace -f -tt -p <pid>   (which syscall is it blocked in?)
```

Decision logic once localized:

```text
   high %iowait + high await        -> DISK bound        -> biolatency, which proc (pidstat -d)
   r > #CPU, high %usr, PSI-cpu up  -> CPU bound          -> perf flame graph (which function)
   si/so > 0, PSI-mem up, OOM kills -> MEMORY bound       -> who leaks (pidstat -r), cgroup limits
   retrans/drops up, Send-Q grows   -> NETWORK bound      -> tcpretrans, tcpdump, check link/QoS
   load high but r low & b high     -> tasks in D (I/O)   -> NOT cpu; chase the I/O / NFS / lock
   nothing saturated but app slow   -> off-CPU / locks    -> off-CPU flame graph, runqlat
```

---

## 12. Advanced: off-CPU analysis, the 60-second checklist, PMU, and continuous profiling

### On-CPU vs off-CPU — the two halves of "where did the time go?"

A normal flame graph ([§6](#6-the-core-observability-toolset)) shows **on-CPU** time —
where the CPU was *busy*. But most latency is spent **off-CPU**: blocked on a lock,
disk, network, or the run queue. An **off-CPU flame graph** profiles where threads
*slept* and for how long (via the scheduler `sched_switch` tracepoint / `offcputime`
from bcc). The two together account for 100% of wall-clock latency:

```
   total latency = on-CPU (busy: hot functions)  +  off-CPU (waiting: lock/IO/runqueue)
   slow but CPU idle?  -> the answer is in the OFF-CPU graph (a lock, a disk, a syscall)
```

This is the staff-level move for "the request is slow but nothing is pegged" — the
off-CPU graph names exactly what it waited on (see
[scenarios 05](../enterprise_scenarios/05_cross_layer_triage.md)).

### Brendan Gregg's 60-second checklist

A disciplined first-minute sweep on any unfamiliar slow box, before deep tools:

```
uptime              # load averages — trend (1/5/15 min)
dmesg | tail        # OOM kills, TCP drops, hardware errors
vmstat 1            # r (runnable) vs b (blocked); si/so (swap); us/sy/wa/id
mpstat -P ALL 1     # per-CPU imbalance; one core pegged? high %soft/%irq?
pidstat 1           # which process is burning CPU, over time
iostat -xz 1        # disk await / saturation (runbook 02)
free -m             # available vs used; swap
sar -n DEV 1        # network throughput per interface
sar -n TCP,ETCP 1   # TCP retransmits, resets (runbook 04)
top / htop          # the synthesis
```

Plus the modern one-liner: `cat /proc/pressure/{cpu,memory,io}` (PSI) tells you in one
read *which resource is causing stalls* — automate it with
[`examples/psi_watcher.py`](examples/README.md).

### PMU & top-down microarchitecture analysis

When the CPU is busy but you don't know *why* it's slow per-instruction, the
**Performance Monitoring Unit** (hardware counters) answers it. `perf stat` exposes
IPC (instructions per cycle), cache-miss rate, and branch-miss rate; **top-down
analysis** (`perf stat --topdown` / `toplev`) attributes every stalled cycle to one of
four buckets:

```
   Retiring (good work) | Bad Speculation (mispredicts) | Frontend-bound (I-cache/decode) | Backend-bound (D-cache/memory)
```

A low IPC that's **backend-bound** = memory/cache stalls → fix data layout
([03 §12 NUMA](03_memory_management.md), C++ ch24). **Bad speculation** → unpredictable
branches. This turns "the CPU is busy" into a specific, fixable cause.

### Continuous profiling — the always-on flame graph

Ad-hoc profiling misses the incident you weren't watching. **Continuous profilers**
(Parca, Pyroscope, Grafana Phlebotomy, the Polar Signals/`perf`+eBPF stack) sample
stacks fleet-wide at ~1% overhead and store them, so you can open a flame graph for
"checkout, p99 hosts, 03:00-03:05 last Tuesday" after the fact — and **diff** two time
windows or releases to localize a regression. This is now standard practice at scale;
it's the difference between "reproduce it and profile" and "look up what already
happened."

---

## 13. Trade-offs & operating principles

- **Measure saturation, not just utilization.** 100% busy with an empty queue is fine; 70% with a growing queue is the bottleneck. PSI (`/proc/pressure/*`) is the cheapest saturation signal Linux gives you — use it.
- **Trust the source, not the dashboard.** Every metric resolves to a file under `/proc`/`/sys` or a tracepoint. When tools disagree, go to the source.
- **Match the tool's overhead to the target.** `strace`/`ltrace` are fine on a dev box, dangerous on a hot production process (2 stops per syscall). `perf` sampling and **eBPF/bpftrace** are production-safe — prefer them.
- **Historical data wins post-incidents.** `sar` (sysstat) records U/S/E over time; without it you're blind to what happened *before* you logged in. Make sure it's enabled on every server.
- **Load average is not CPU.** It includes uninterruptible (D-state) tasks. Cross-check with `vmstat r` vs `b`.
- **The page-cache cliff and the swap cliff dominate real latency.** "Free RAM is low" is usually fine (it's cache); "swap in/out is nonzero" or "OOM kills in dmesg" is the real signal.

## 14. Key Takeaways

1. **Boot is a handoff chain**: firmware (UEFI/Secure Boot) → bootloader (GRUB) → kernel + initramfs → PID 1 (systemd) → your services. `systemd-analyze blame/critical-chain` tells you what's slow.
2. **systemd models the system as a unit dependency graph** and puts every service in a **cgroup** — which is how it does per-service resource limits and accounting.
3. **`/proc` and `/sys` are the kernel's file-based API**; every observability tool is a reader over them. Learn the key files and you can debug without the tools.
4. **The USE method** — Utilization, Saturation, Errors for every resource — converts "it's slow" into a finite checklist. **Saturation (PSI) is the most predictive and most overlooked** signal.
5. **Know your tools by resource**: `mpstat`/`vmstat`/`perf` (CPU), `free`/`vmstat si-so` (mem), `iostat -xz`/`biolatency` (disk), `ss`/`sar`/`tcpretrans` (net); `pidstat` to attribute; `strace`/`ftrace`/`bpftrace` to root-cause.
6. **Flame graphs** (on-CPU and off-CPU) turn thousands of sampled stacks into the one hot/blocked path at a glance.
7. **eBPF/bpftrace** lets you ask arbitrary questions of the live kernel at production-safe overhead — the modern endgame for the bottlenecks the static tools can't see.
8. **Load average ≠ CPU demand** (it includes D-state I/O waiters); **low "free" RAM is normal** (page cache); the real alarms are **swap activity and OOM kills** in `dmesg`.

> Read next: [07 — Virtualization & Containers](07_virtualization_containers.md) for *what* you're observing when a container's cgroup throttles or its kernel boundary is the thing under load.
