# 15 — User-Space Debugging: gdb, Core Dumps & /proc

> **Audience:** Staff/principal engineers who must triage a crashed, hung, or
> misbehaving **user-space process** on a production Linux box — often a binary
> you didn't write, with no debugger experience required. This is operational
> craft: get the evidence, find the broken frame, hand back a root cause.
> System-wide observability (perf, eBPF, the USE method) lives in a sibling
> reference — see [../../os_net/operating_system/08_linux_internals_observability.md](../../os_net/operating_system/08_linux_internals_observability.md).
> **This** chapter is about one specific broken process.

---

## 1. The toolbox map — symptom → tool

Pick the tool by what you can observe and what you're allowed to do to the
process. Attaching a debugger **stops** the process; `/proc` does not.

| Symptom / question | First reach for | Why |
|---|---|---|
| Process crashed, already dead | **core dump** + `gdb` | Post-mortem; no live process needed |
| Process crashed, dmesg shows `segfault` | `gdb bin core`, `bt full` | Find the faulting frame |
| Hung / not responding | `/proc/PID/stack`, `/proc/PID/wchan`, then `gdb -p` + `thread apply all bt` | Where is it blocked? |
| "What is it *doing* to the OS?" | `strace -f -p PID` | Live syscall trace |
| "What library calls is it making?" | `ltrace -p PID` | Library-call trace |
| Spinning at 100% CPU | `perf top -p PID`, or `gdb -p` sampled `bt` | Find the hot loop |
| Leaking memory / fds | `/proc/PID/smaps`, `/proc/PID/fd`, `valgrind` (dev) | Growth over time |
| "What's it got open / its limits / env?" | `/proc/PID/{fd,limits,environ,maps}` | Live introspection, no debugger |
| System-wide, not one process | **→ os_net 08** (perf/bpftrace/USE) | Wrong reference |

Rules of thumb:

- **Prefer a core dump over a live attach.** `gdb -p` SIGSTOPs the target —
  fine for a worker, a stall for a latency-critical service.
- **`/proc` is free and non-intrusive.** Try it before the debugger.
- For script-level (`set -x`, `trap`) debugging see
  [08 — Error Handling, Strict Mode & Debugging](08_error_handling_debugging.md).

---

## 2. `/proc/PID` — live introspection, no debugger, works in prod

Reading `/proc` does not stop the process. It's the first thing to run.

```bash
PID=$(pgrep -x myservice | head -1)

# State, threads, capabilities, RSS — the one-glance summary
grep -E '^(State|Threads|VmRSS|VmSize|Cap)' /proc/$PID/status
# State:  S (sleeping)          # R running, D uninterruptible, Z zombie, T stopped
# Threads:        14
# VmRSS:    482176 kB           # resident set — the "real" memory
# CapEff:   00000000a80425fb    # effective capabilities (see ch 12)

# What binary is this, and what was it launched with?
readlink /proc/$PID/exe        # /usr/bin/myservice  (the real binary on disk)
tr '\0' ' ' < /proc/$PID/cmdline; echo
readlink /proc/$PID/cwd        # working dir — relative paths resolve here
tr '\0' '\n' < /proc/$PID/environ | grep -iE 'PATH|HOME|LD_'  # env it inherited

# Limits — the file/fd/core ceilings actually in force
grep -E 'open files|core file size' /proc/$PID/limits
# Max open files            1024   4096   files
# Max core file size        0      unlimited   bytes   <-- core size 0 = NO CORE
```

### Diagnosing a hang (where is it stuck in the kernel?)

```bash
# wchan = the kernel function it's sleeping in. Needs CAP_SYS_ADMIN/root.
cat /proc/$PID/wchan; echo        # e.g. futex_wait_queue_me  -> blocked on a lock
cat /proc/$PID/stack              # kernel-side stack of the task
# [<0>] futex_wait+0x...          -> waiting on a futex (mutex/condvar)
# [<0>] do_sys_poll+0x...         -> blocked in poll(), waiting for IO

# Per-thread: which thread is stuck where?
for t in /proc/$PID/task/*; do echo -n "$(basename $t): "; cat $t/wchan; echo; done
```

`State: D` (uninterruptible sleep) + a wchan in a filesystem/IO path → the
process is wedged on slow/dead storage, not a software bug.

### Diagnosing an fd leak ("what's it got open?")

```bash
ls -l /proc/$PID/fd | head
# lrwx------ 0 -> /dev/pts/3
# l-wx------ 3 -> /var/log/app.log
# lrwx------ 7 -> socket:[884412]      # a socket — resolve with ss below
# lr-x------ 9 -> /etc/myservice.conf

# Count fds over time — climbing monotonically = leak
ls /proc/$PID/fd | wc -l            # 1023  ... about to hit the 1024 limit -> EMFILE

# Which file/type dominates? (the leak signature)
ls -l /proc/$PID/fd | grep -oE '\-> .*' | sort | uniq -c | sort -rn | head
#  900 -> /tmp/cache.db             # 900 handles to one file = not closing it
```

Resolve a `socket:[INODE]` with `ss -tanp | grep INODE` (which peer? which port?).

### Finding a memory region / leak with maps & smaps

```bash
# Coarse map of memory regions (heap, stacks, mmap'd files, libs)
grep -E 'heap|stack|\.so' /proc/$PID/maps | head
# 5612a1c00000-5612a3400000 rw-p ... [heap]      # anonymous heap growth
# 7f9c2a000000-...           r-xp ... /usr/lib/libssl.so.3

# smaps = per-region accounting. Sum private dirty anon pages = real heap use.
awk '/Rss:/{r+=$2} END{print r" kB RSS (smaps total)"}' /proc/$PID/smaps
grep -E '^(Rss|Pss|Private_Dirty)' /proc/$PID/smaps_rollup   # kernel >=4.14, one-shot
```

A steadily growing `[heap]` region or `Private_Dirty` in `smaps_rollup` across
samples is your leak signal — confirm the *cause* with valgrind/ASan/heaptrack
in dev (§8).

---

## 3. `strace` — "what is it doing / why is it stuck?"

`strace` traces **system calls**: the boundary where the process talks to the
OS. When something fails or blocks, the failing syscall (and its `errno`) is
usually the whole answer.

```bash
# Attach to a running process: follow children, timestamps, syscall durations
strace -f -T -tt -p $PID
# 14:02:11.337 openat(AT_FDCWD, "/etc/myservice.conf", O_RDONLY) = -1 ENOENT (No such file)
#                                                                  ^^^^^^^^^ the smoking gun
# 14:02:11.339 connect(7, {sa_family=AF_INET, sin_port=htons(5432)...}) = -1 ECONNREFUSED <2.0s>
#                                                                          ^^ DB down; <2.0s> = it blocked here

# Narrow the noise: only file-open or only network syscalls
strace -f -e trace=openat,stat -p $PID        # "what file can't it open?"
strace -f -e trace=network -p $PID            # connects/sends/recvs

# Profile: which syscalls dominate time/count? (no per-call spam)
strace -f -c -p $PID
# % time   seconds   calls   errors  syscall
# 71.2   12.40       9001     9000   futex      <-- 9000 futex EAGAIN = lock contention spin
# 18.0    3.13      48000        0   read

# Capture a user-space stack at each matching syscall (find WHO calls it)
strace -k -e trace=openat -p $PID
```

Reading common errnos:

- **`ENOENT`** — file/path missing. The path printed *is* the bug (typo, wrong cwd, missing config).
- **`EACCES`** / **`EPERM`** — permission / capability denied. Cross-check `/proc/PID/status` `Cap*` and the file's mode (ch 12).
- **`EAGAIN`/`EWOULDBLOCK`** — non-blocking resource not ready (normal in event loops; a *flood* of them = a spin).
- **`ECONNREFUSED`/`ETIMEDOUT`** — the dependency is down/unreachable, not your process's fault.

> **Perf-overhead warning:** `strace` traps **every** syscall via `ptrace` —
> it can slow a busy process 10–100×. Use `-e` to scope, `-c` for a summary,
> and detach (`Ctrl-C` / kill the strace) the moment you have the answer. For
> low-overhead, system-wide syscall tracing in steady state, use `bpftrace`/perf
> — see [../../os_net/operating_system/08_linux_internals_observability.md](../../os_net/operating_system/08_linux_internals_observability.md).

### `ltrace` — library calls

```bash
ltrace -f -p $PID                 # malloc/free, getenv, libssl calls, etc.
ltrace -e 'malloc+free' -p $PID   # spot alloc/free imbalance at the library layer
```

os_net 08 covers `strace`/`ltrace` for *observability*; keep this usage
**debug-focused** — you're chasing one failure, not building a baseline.

---

## 4. Core dumps — the post-mortem snapshot

A core dump is the process's memory + register state frozen at the crash.
It's the single best artifact for a crash you can't reproduce live.

### Enabling cores

```bash
# Per-shell / per-service: the soft limit MUST be > 0 or no core is written
ulimit -c unlimited
ulimit -c                          # unlimited

# Where do cores go? core_pattern decides.
cat /proc/sys/kernel/core_pattern
# Case A: a plain pattern -> file on disk relative to the process cwd
#   core.%e.%p.%t      (%e=exe %p=pid %t=epoch)
# Case B: starts with "|" -> piped to a handler program (ulimit -c is IGNORED for the pipe!)
#   |/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h

# Set a sane on-disk pattern (transient; persist via sysctl.d)
echo '/var/crash/core.%e.%p.%t' | sudo tee /proc/sys/kernel/core_pattern
```

### systemd-coredump — how most modern distros capture cores

When `core_pattern` pipes to `systemd-coredump`, cores are captured, compressed,
and indexed in the journal regardless of `ulimit` — query them with `coredumpctl`:

```bash
coredumpctl list                       # all recent dumps, newest last
# TIME            PID  UID  SIG     COREFILE  EXE
# Mon 13:55 ...  4821 1000  SIGSEGV present   /usr/bin/myservice

coredumpctl info myservice             # signal, faulting cmdline, truncated backtrace
coredumpctl dump myservice -o core     # extract the raw core to ./core
coredumpctl debug myservice            # opens gdb on the core WITH symbols, in one step
```

Storage and retention live in `/etc/systemd/coredump.conf`
(`Storage=`, `MaxUse=`, `ProcessSizeMax=`).

### Ensuring cores in prod (and for a systemd service)

A service's core size is governed by the unit's `LimitCORE=`, not your login
shell — see [11 — systemd: Service Authoring & Operations](11_systemd_services.md):

```ini
# /etc/systemd/system/myservice.service.d/core.conf
[Service]
LimitCORE=infinity
```

**Symptom → Cause → Fix: "no core dump produced"**

- **Symptom:** Process segfaults (dmesg confirms) but no core file appears.
- **Cause:** `ulimit -c` is `0` (the default on many distros) → no core written.
  - **Fix:** `ulimit -c unlimited` / unit `LimitCORE=infinity`.
- **Cause:** `core_pattern` pipes to a handler (`|...`) — file never lands where you looked; it's in `coredumpctl` / the handler's dir.
  - **Fix:** `coredumpctl list`, or read the pattern.
- **Cause:** Container — the *host* `core_pattern` applies and may point at a path not visible in the container; or `RLIMIT_CORE` is 0 in the image.
  - **Fix:** Set host `core_pattern` to an on-disk path, mount it in, raise the limit.
- **Cause:** Setuid/securebits binary — kernel suppresses cores unless `fs.suid_dumpable=2` (and the path is root-owned).
  - **Fix:** `sysctl fs.suid_dumpable=2` (understand the disclosure risk first).

---

## 5. `gdb` essentials for the non-C-developer triager

You don't need to read C fluently. You need the **backtrace** — it names the
function that crashed and the chain that called it.

### Load a core and get the backtrace

```bash
gdb /usr/bin/myservice /var/crash/core.myservice.4821.171...   # binary THEN core
#   ... or just:  coredumpctl debug myservice
```

```gdb
(gdb) bt                       # the backtrace — THE single most useful command
#0  0x... in parse_header (h=0x0) at parser.c:88       <-- crashed here
#1  0x... in handle_request () at server.c:204         <-- called from here
#2  0x... in worker_loop ()   at server.c:151

(gdb) bt full                  # backtrace + local variables in every frame
#0  parse_header (h=0x0) at parser.c:88
        len = <optimized out>
        p   = 0x0                                       <-- null pointer -> the segfault

(gdb) frame 1                  # move to frame #1 to inspect its context
(gdb) info locals              # locals in the current frame
(gdb) print req->path          # print a specific value (follows pointers)
(gdb) up / down                # walk the call chain
(gdb) info registers           # rip/rsp/... — for stripped/asm-level work
```

### All threads — find the crashed / deadlocked one

```gdb
(gdb) info threads             # list threads; * marks the one that faulted
(gdb) thread apply all bt      # backtrace EVERY thread — essential for deadlocks
#   Thread 3 ... pthread_mutex_lock () ... __lll_lock_wait   <-- waiting for a lock
#   Thread 7 ... pthread_mutex_lock () ... __lll_lock_wait   <-- waiting for the SAME lock
#                                          -> both blocked = deadlock; find who holds it
(gdb) thread 3                 # switch to a specific thread, then bt/up/down
```

### Attaching to a live process

> `gdb -p PID` **STOPS** the process while you're attached. `detach`/quit
> resumes it. Keep it brief on anything latency-sensitive.

```gdb
(gdb) attach 4821              # or: gdb -p 4821
(gdb) break parser.c:88        # breakpoint
(gdb) watch counter            # stop when 'counter' changes
(gdb) continue                 # resume until the next stop
(gdb) finish                   # run until current function returns
(gdb) detach                   # let it run again — DON'T just quit and kill it
```

---

## 6. Symbols & debuginfo — why your backtrace is `??`

```gdb
(gdb) bt
#0  0x00007f9c2a41b3d1 in ?? ()        <-- stripped: no symbol names, useless
#1  0x00007f9c2a41a002 in ?? ()
```

`??` means the binary/library is **stripped** — production builds usually are.
Symbols live in separate debuginfo. Fixes, easiest first:

```bash
# 1) debuginfod — gdb auto-downloads matching debug symbols by build-id. Best option.
export DEBUGINFOD_URLS="https://debuginfod.<distro>.org/"
gdb /usr/bin/myservice core            # symbols fetched on demand

# 2) Install the matching -dbg / -debuginfo package (must match the exact build)
sudo apt install myservice-dbgsym      # Debian/Ubuntu
sudo dnf debuginfo-install myservice   # RHEL/Fedora

# 3) Map a raw address -> file:line directly (when you only have an address)
addr2line   -e /usr/bin/myservice -f 0x401a3d     # uses .debug if present
eu-addr2line -e /usr/bin/myservice 0x401a3d       # elfutils variant

# Confirm a binary even HAS symbols / a build-id for debuginfod
file /usr/bin/myservice                # "... not stripped" vs "stripped"
readelf -n /usr/bin/myservice | grep -i build-id
```

Separate debug files (`/usr/lib/debug/.build-id/...`) are matched to the binary
by build-id. For how builds strip symbols and split debuginfo, see
[14 — Packages, Builds & Dynamic Linking](14_packages_linking.md).

---

## 7. Recipes — Symptom → exact commands → root cause

### Segfault

```bash
dmesg | tail
# myservice[4821]: segfault at 0 ip 00005612a1c0... sp ... error 4 in myservice[...]
#                  ^ faulting addr 0 = null-pointer deref
coredumpctl debug myservice            # or: gdb bin core
```
```gdb
(gdb) bt full          # top frame names the function; a 0x0 local = the null deref
```
**Root cause:** the frame `#0` function dereferenced a pointer that `bt full`
shows as `0x0` (or `error 4`/`addr 0` in dmesg). Trace back to where it should
have been set.

### Hang / deadlock

```bash
cat /proc/$PID/task/*/wchan            # futex_wait on multiple threads = lock wait
gdb -p $PID
```
```gdb
(gdb) thread apply all bt              # two+ threads in __lll_lock_wait -> deadlock
(gdb) detach
```
**Root cause:** threads acquiring two locks in opposite order (classic ABBA), or
one thread holding a lock while blocked on IO.

### 100% CPU spin

```bash
top -H -p $PID                         # -H: which THREAD is hot (TID)
perf top -p $PID                       # sample the hot function -> os_net 08
# Low-tech alternative: attach and sample the stack a few times
for i in 1 2 3; do gdb -p $PID -batch -ex 'bt' 2>/dev/null; done | sort | uniq -c
```
**Root cause:** the function that appears in every sample is the hot loop —
often a busy-wait, a retry-without-backoff, or a runaway `EAGAIN` poll.

### Memory leak

```bash
# Trend RSS / smaps over time; growing = leak
while sleep 60; do grep VmRSS /proc/$PID/status; done
grep Private_Dirty /proc/$PID/smaps_rollup
```
Confirm the *allocation site* in dev with valgrind/ASan/heaptrack (§8). In prod,
`/proc` only shows the growth, not the culprit line.

---

## 8. Deeper tools (mostly dev-time)

- **`valgrind --tool=memcheck`** — exact leak + invalid-access reports with
  stacks. ~20–50× slowdown; **dev/staging, never hot prod**.
- **AddressSanitizer (`-fsanitize=address`)** — compile-time instrumentation;
  catches use-after-free/overflow with low overhead. Needs a rebuild → bake into
  CI/test binaries.
- **`heaptrack`** — lighter heap profiler; good for "where does the memory go?".
- **`rr`** (record-replay) — record a failing run once, then replay it
  deterministically under gdb *backwards* (`reverse-continue`). Gold for
  Heisenbugs you can reproduce but not catch.

---

## 9. Production debugging discipline

- **Capture, then debug offline.** Copy the **core + the exact binary** (same
  build-id) + `/proc/PID/maps` to a workstation. Debug there, not on the box.
- **A live `gdb -p` STOPS the process.** On a latency-critical service that's an
  outage. Prefer a core dump; if you must attach, scope it and `detach` fast.
- **Match the binary exactly.** A core from build N + binary build N+1 = garbage
  backtraces. Pin the build-id.
- **`/proc` first, debugger last.** Most hangs and fd/leak questions are answered
  by `/proc/PID/{stack,wchan,fd,smaps}` without ever stopping anything.
- **One process vs. the whole system.** If the problem isn't isolated to this
  binary — noisy neighbor, IO saturation, scheduler — switch to the system-wide
  tools in [../../os_net/operating_system/08_linux_internals_observability.md](../../os_net/operating_system/08_linux_internals_observability.md),
  and for cross-layer incidents (app ↔ kernel ↔ network ↔ storage) follow
  [../../os_net/enterprise_scenarios/05_cross_layer_triage.md](../../os_net/enterprise_scenarios/05_cross_layer_triage.md).

---

> Next: [16 — Fleet & Configuration Management at Scale](16_fleet_config_management.md) —
> zoom out from debugging one host to safely changing ten thousand of them.

> Related: [../../os_net/](../../os_net/README.md) — OS internals, observability
> & incident runbooks (perf, eBPF, the USE method, cross-layer triage) ·
> [10 — Advanced & Enterprise](10_advanced_enterprise.md) · [README](README.md).
