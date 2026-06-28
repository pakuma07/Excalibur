# 01 — Processes & Threads

> **Audience:** staff/principal. You know how to call `fork()` and start a thread pool. This doc is about *what the kernel actually does* when you do — the address space, the `task_struct`, the lifecycle state machine, copy-on-write, and the failure modes (zombies, orphans, signal races, thundering herds) that turn a "simple" supervisor or prefork server into a 3 a.m. incident.
>
> **Primary sources:** Tanenbaum & Bos, *Modern Operating Systems* (4th ed.), ch. 2; Silberschatz, Galvin & Gagne, *Operating System Concepts* (10th ed.), ch. 3–4; Kerrisk, *The Linux Programming Interface* (TLPI), ch. 24–34; Love, *Linux Kernel Development* (3rd ed.), ch. 3–4; Bovet & Cesati, *Understanding the Linux Kernel* (3rd ed.), ch. 3; the `clone(2)`, `fork(2)`, `signal(7)`, `credentials(7)`, `pthreads(7)` man pages; the Linux kernel `Documentation/scheduler/` and `include/linux/sched.h`.

---

## 1. Why this matters at scale

A "process" is the kernel's unit of *resource ownership* and a "thread" is its unit of *scheduling*. Almost every production reliability problem that isn't a logic bug lives in the gap between those two sentences:

1. **Concurrency model is a one-way door.** Choosing prefork processes (Postgres, classic Apache, Gunicorn sync workers) vs. threads (Java, Go runtime over OS threads) vs. M:N green threads (Go goroutines, old Erlang) sets your memory ceiling, your crash-isolation story, and whether one slow request can stall a whole worker. You cannot cheaply retrofit this later.
2. **The lifecycle has sharp edges.** A child that exits but is never `wait()`ed becomes a **zombie** holding a PID slot. A parent that dies first orphans its children onto `init`/`systemd`. A `SIGCHLD` handler that calls a non-async-signal-safe function deadlocks. These are not exotic — they are the *default* failure modes of any code that spawns subprocesses.
3. **fork() cost and copy-on-write semantics decide your fan-out budget.** A 4 GiB Python parent that forks 32 workers does *not* use 128 GiB — until the GC touches every page and COW unshares it. Understanding COW is the difference between a prefork server that scales and one that OOMs under load.

Everything in the rest of this book — scheduling ([02](02_cpu_scheduling.md)), memory, I/O — sits on top of this process/thread model. Get the model wrong and no amount of tuning saves you.

### The big picture

```
                     A PROCESS  (unit of resource ownership)
   +-------------------------------------------------------------+
   |  Address space (virtual memory)        Open file table       |
   |  +----------+ +------+ +-------+        fd 0 -> stdin         |
   |  |  text    | | data | | heap->|        fd 1 -> stdout        |
   |  +----------+ +------+ +-------+        fd 2 -> stderr        |
   |        ...  <-stack(s)         |        Signal dispositions    |
   |                                |        Credentials (uid/gid)  |
   |   +--------+  +--------+  +--------+     PID, PGID, SID         |
   |   |Thread 1|  |Thread 2|  |Thread 3|  <- units of SCHEDULING   |
   |   | regs   |  | regs   |  | regs   |     each: own stack, regs, |
   |   | stack  |  | stack  |  | stack  |     TLS, kernel sched ent. |
   |   +--------+  +--------+  +--------+                            |
   +-------------------------------------------------------------+
   Threads SHARE: address space, fds, signal handlers, cwd, uid.
   Threads have OWN: stack, registers, TLS, errno, sched state, sigmask.
```

---

## 2. The process model and the virtual address space

A process is the abstraction of *a program in execution* plus everything the OS associates with it. The most important part is the **virtual address space** — each process believes it owns a flat, private range of addresses (on x86-64 Linux, 0 to 2^47-1 of user space), and the MMU + page tables map those to physical frames.

### 2.1 Address-space layout (Linux, x86-64, typical)

```
  0x7fff_ffff_ffff  +-----------------------+  high addresses
                    |   [stack]  grows down |  <- main thread stack (8 MiB default)
                    |          |            |
                    |          v            |
                    |   (guard gap)         |
                    +-----------------------+
                    |   mmap region:        |  <- shared libs (libc.so), mmap(),
                    |   libs, thread stacks |     thread stacks, large malloc
                    +-----------------------+
                    |          ^            |
                    |          |            |
                    |   [heap]  grows up    |  <- brk()/sbrk(); small malloc
                    +-----------------------+
                    |   .bss  (zero-init)   |  <- uninitialized globals
                    |   .data (init globals)|  <- initialized globals
                    |   .text (code, RO+X)  |  <- the program itself
  0x0000_0000_0000  +-----------------------+  (NULL page unmapped -> SIGSEGV)
```

Key properties staff engineers must internalize:

- **`.text` is read-only and shared** across all processes running the same binary (and across forks). One physical copy of libc backs every process — this is why dynamic linking saves enormous RAM.
- **The heap (`brk`) and `mmap` arenas** are where `malloc` lives. glibc's allocator uses `brk` for small allocations and `mmap` for large ones (>128 KiB by default, `M_MMAP_THRESHOLD`).
- **Each thread gets its own stack** carved out of the mmap region (default 8 MiB on Linux, see `ulimit -s` and `pthread_attr_setstacksize`). A 1000-thread server reserves ~8 GiB of *virtual* stack — usually fine because it's lazily faulted in, but a real ceiling.
- **The NULL page is deliberately unmapped** so a `*NULL` dereference faults loudly instead of corrupting data.

You can inspect any live process's real map:

```bash
cat /proc/<pid>/maps      # every VMA: range, perms, backing file
cat /proc/<pid>/smaps     # + RSS, PSS, shared/private, swap per region
```

`PSS` (Proportional Set Size) in `smaps` is the honest memory metric for forked workers: shared pages are divided by the number of sharers, so summing PSS across a prefork pool gives true RAM usage — unlike RSS, which double-counts COW-shared pages.

---

## 3. The Process Control Block: `task_struct`

The kernel represents every schedulable entity with a **Process Control Block (PCB)**. On Linux this is `struct task_struct` (defined in `include/linux/sched.h`), and it is one of the largest, most central structures in the kernel — on the order of a few KiB. Crucially, **Linux does not distinguish processes from threads at the kernel level**: both are `task_struct`s. A "thread" is just a `task_struct` that *shares* certain resources with others.

### 3.1 What's in it (the fields that matter)

| Field (conceptual) | Linux `task_struct` member | Purpose |
|---|---|---|
| Process ID | `pid` (kernel TID), `tgid` (POSIX PID) | `getpid()` returns `tgid`; `gettid()` returns `pid` |
| State | `__state` / `state` | RUNNING, INTERRUPTIBLE, etc. (§4) |
| Scheduling | `se` (sched_entity), `prio`, `policy` | vruntime, nice, RT priority ([02](02_cpu_scheduling.md)) |
| Address space | `mm`, `active_mm` | pointer to `struct mm_struct` (page tables, VMAs) |
| Open files | `files` (`struct files_struct`) | the fd table |
| Signals | `signal`, `sighand`, `blocked`, `pending` | dispositions + per-task masks |
| Credentials | `cred` | uid/gid/euid, capabilities |
| Parent/child | `real_parent`, `parent`, `children`, `sibling` | the process tree |
| Namespaces | `nsproxy` | PID/net/mount/etc. namespaces (containers) |
| Exit | `exit_state`, `exit_code` | ZOMBIE/DEAD; status for `wait()` |

### 3.2 The unification: threads share, processes copy

The single most important Linux design decision here: `fork()`, `pthread_create()`, and container creation are all the same syscall — **`clone(2)`** — differing only in *which `CLONE_*` flags* say "share this resource" vs. "copy it."

```
                       clone() flag        fork()      pthread_create()
   Address space (mm)  CLONE_VM            copy (COW)  SHARE
   File descriptors    CLONE_FILES         copy        SHARE
   Signal handlers     CLONE_SIGHAND       copy        SHARE
   Same thread group   CLONE_THREAD        no          YES (same tgid)
   Filesystem info     CLONE_FS            copy        SHARE
```

So "thread vs. process" is not a binary in the kernel — it's a *point on a sharing spectrum*. This is why Linux threads are cheap (`clone` with sharing flags copies almost nothing) and why namespaces (containers) are "just more flags."

---

## 4. Process states and the lifecycle state machine

A task moves through a small set of states. The textbook (Tanenbaum) reduces it to three; Linux adds important refinements.

### 4.1 The canonical state diagram

```
                     scheduler picks it
        +---------+  ----------------->  +---------+
        | READY   |                      | RUNNING |
        |(runnable)| <-----------------  | (on CPU)|
        +---------+  preempted/yield     +---------+
            ^                                 |
            | event/resource                  | blocks on I/O,
            | now available                   | lock, sleep, etc.
            |                                  v
        +-----------+   <------------------ +---------+
        | BLOCKED   |  wakeup (event done)  | (waiting)|
        | (sleeping)|                       +---------+
        +-----------+

        RUNNING --exit()--> ZOMBIE --parent wait()--> (reaped, gone)
```

### 4.2 Linux's actual states

| Linux state | `ps` code | Meaning |
|---|---|---|
| `TASK_RUNNING` | R | On a CPU **or** on a runqueue ready to run |
| `TASK_INTERRUPTIBLE` | S | Sleeping; **can** be woken by a signal (normal blocking I/O wait) |
| `TASK_UNINTERRUPTIBLE` | D | Sleeping; **cannot** be interrupted by signals (mid-disk-I/O, some NFS) |
| `__TASK_STOPPED` | T | Stopped by `SIGSTOP`/`SIGTSTP` (Ctrl-Z) |
| `EXIT_ZOMBIE` | Z | Exited; awaiting parent `wait()` to collect status |
| `EXIT_DEAD` | X | Being removed; transient |

> **The dreaded `D` state.** A process in `TASK_UNINTERRUPTIBLE` (`D`) cannot be killed — not even by `SIGKILL` — because it's blocked inside the kernel waiting on something (usually I/O) that holds locks it can't safely abandon. A pile of `D`-state processes is the signature of a stuck storage backend (a hung NFS mount, a saturated disk). You cannot `kill -9` your way out; you must fix the underlying I/O.

---

## 5. fork(), exec(), wait(), exit() — the lifecycle syscalls

These four calls are the entire Unix process-creation philosophy: **separate the act of duplicating a process (`fork`) from the act of running a new program (`exec`).** This separation — odd to anyone coming from `CreateProcess` on Windows or `posix_spawn` — is what makes the shell's job (redirect fds, set up pipes, drop privileges, *then* exec) trivially composable.

### 5.1 The four calls

| Call | What it does | Returns |
|---|---|---|
| `fork()` | Duplicate the calling process (COW address space, copied fd table) | **0 in child, child PID in parent**, -1 on error |
| `execve(path, argv, envp)` | Replace the current address space with a new program. Same PID. fds survive (unless `O_CLOEXEC`). | Does **not return** on success |
| `waitpid(pid, &status, opts)` | Block until a child changes state; collect its exit status; reap the zombie | reaped PID, or 0 (WNOHANG), or -1 |
| `_exit(code)` / `exit(code)` | Terminate; become a zombie until reaped | never returns |

The famous idiom:

```c
pid_t pid = fork();
if (pid < 0) {
    perror("fork");                    /* resource limit / ENOMEM */
} else if (pid == 0) {
    /* ---- CHILD ---- */
    execve("/bin/ls", (char *[]){"ls", "-l", NULL}, environ);
    _exit(127);                        /* exec failed: child MUST _exit, not return */
} else {
    /* ---- PARENT ---- */
    int status;
    waitpid(pid, &status, 0);          /* reap, else zombie */
}
```

> **`_exit` vs `exit` in the child.** After `fork`, the child shares the parent's buffered `stdio` state. Calling `exit()` (which flushes stdio buffers and runs `atexit` handlers) in a child that won't `exec` can **double-flush** the parent's buffers (duplicated output) and re-run cleanup. Use `_exit()` in the child when not exec'ing. This bites people constantly.

### 5.2 Status decoding

`waitpid`'s `status` is a packed int. Never compare it directly — use the macros:

```c
int status;
waitpid(pid, &status, 0);
if (WIFEXITED(status))   printf("exited, code=%d\n", WEXITSTATUS(status));
if (WIFSIGNALED(status)) printf("killed by signal %d\n", WTERMSIG(status));
if (WIFSTOPPED(status))  printf("stopped by signal %d\n", WSTOPSIG(status));
```

---

## 6. Copy-on-write: how fork() stays cheap

A naïve `fork()` would copy the entire address space — gigabytes for a large process — only for the child to immediately `exec` and throw it all away. **Copy-on-write (COW)** avoids this.

### 6.1 Mechanism

On `fork()`, the kernel does **not** copy data pages. Instead:

1. It copies the *page tables* (cheap relative to data), pointing parent and child at the **same physical frames**.
2. It marks every writable page **read-only** in both processes' page tables and bumps each frame's reference count.
3. When either process **writes** to such a page, the MMU raises a **page fault**. The fault handler sees a COW page, allocates a fresh frame, copies the 4 KiB, remaps it writable in the faulting process, and resumes. Only *touched* pages are ever copied.

```
   Before any write (right after fork):
     parent PTE --\                /-- child PTE     both RO, COW flag
                   >-- [phys frame P]  --<           refcount = 2
   Parent writes to the page  -> fault -> copy:
     parent PTE --> [new frame P'] (RW)
     child  PTE --> [phys frame P]  (now RW, refcount = 1)
```

### 6.2 The enterprise consequence

COW is why a prefork server (§9) sharing a large read-only dataset across workers is memory-efficient — *as long as the pages stay untouched*. The classic trap:

- **Python/Ruby GC defeats COW.** CPython's reference counting writes to the refcount field *inside every object header* whenever an object is touched — even for a read. That write triggers COW on the page holding the object. So a Python prefork worker that merely traverses a large shared dict gradually unshares all of it; RSS climbs toward N×. (This is why `gc.freeze()` exists, and why Instagram famously patched CPython to move refcounts out of the COW path.)
- **The JVM/Go** don't refcount, so they preserve COW better, but a moving/compacting GC rewrites object locations and unshares pages too.

You can *watch* COW happen by diffing `/proc/<pid>/smaps` `Private_Dirty` before and after the child starts working.

---

## 7. Zombies and orphans — the two reaping failures

```
   Normal:    parent fork()s child ... child exit()s -> ZOMBIE
              parent wait()s        -> zombie reaped, PID freed.   OK.

   ZOMBIE leak: parent never wait()s. Child stays ZOMBIE forever,
                holding a PID + a task_struct. Enough of these
                exhaust the PID space (~32k default) -> fork() fails
                with EAGAIN -> "cannot fork" outage.

   ORPHAN:      parent exits BEFORE child. Child is re-parented to
                PID 1 (init/systemd), which wait()s for it automatically.
                Harmless IF init reaps. (A daemon-style double-fork
                deliberately creates orphans so init does the reaping.)
```

### 7.1 Why zombies exist at all

A zombie isn't a bug in the kernel — it's a *feature*. After a child exits, the kernel must keep its exit status (return code, signal, resource usage) around so the parent can read it via `wait()`. The `task_struct` is mostly freed; only the slot holding the status lingers. The zombie is reaped the instant the parent calls `wait()`/`waitpid()`.

### 7.2 The robust patterns

- **Reap explicitly** in a `SIGCHLD` handler with a `waitpid(-1, ..., WNOHANG)` loop (because multiple children can die between two signal deliveries — signals don't queue counts).
- **Or ignore `SIGCHLD`** explicitly (`signal(SIGCHLD, SIG_IGN)` or `SA_NOCLDWAIT`): the kernel then auto-reaps and never creates zombies. Use this when you genuinely don't care about exit status.
- **In containers**, PID 1 must reap orphans. A naïve app as PID 1 that doesn't reap leaves zombies forever — hence `tini`/`dumb-init`/`--init` in Docker.

---

## 8. Threads vs. processes — the model comparison

| Dimension | Process (fork) | Thread (within a process) |
|---|---|---|
| Address space | Private (COW) | **Shared** |
| Crash isolation | **Strong** — one crash can't corrupt siblings | None — a wild write or `abort()` takes down all threads |
| Communication | IPC: pipes, sockets, shm, mmap | Shared memory directly (need locks) |
| Creation cost | Higher (page tables, fd copy) | Lower (`clone` with sharing flags) |
| Context-switch cost | Higher (TLB/page-table switch, see §10) | Lower (same `mm`, no TLB flush) |
| Memory overhead | One address space each | One shared + per-thread stack (~8 MiB virt) |
| Failure blast radius | One worker | Whole process |
| Used by | Postgres, classic Apache `prefork`, Gunicorn sync, nginx workers | Java, .NET, Go runtime, most C++ servers |

**The staff-level summary:** *processes trade memory and context-switch cost for crash isolation and simplicity (no shared-mutable-state bugs); threads trade isolation for cheaper communication and switching.* A request handler that calls into a flaky C library you don't trust belongs in a process; a CPU-bound numeric pipeline sharing a big read-only model belongs in threads.

---

## 9. Kernel vs. user threads, and M:N threading

How do user-level threads map onto kernel scheduling entities? Three classic models:

```
   1:1  (one user thread = one kernel thread)        N:1  (many user, one kernel)
     UT  UT  UT                                         UT  UT  UT
      |   |   |                                          \  |  /
     KT  KT  KT   <- kernel schedules each               (user-space scheduler)
      |   |   |                                              |
    [CPU CPU CPU]                                            KT   <- ONE kernel thread
                                                             |
   Linux NPTL, Windows, modern default.                    [CPU]
   Pro: true parallelism, blocking syscall                 Pro: ultra-cheap switches.
   blocks only that thread.                                Con: one blocking syscall
   Con: kernel thread per thread (heavier).                stalls ALL; no SMP parallelism.

   M:N  (M user threads multiplexed over N kernel threads)
     goroutines:  g g g g g g g g  ...
                   \ \ | / / /
                    [G G G]  <- Go runtime scheduler (M:N), work-stealing
                     | | |
                    KT KT KT  (GOMAXPROCS kernel threads)
                     | | |
                   [CPU CPU CPU]
     Pro: cheap (KiB stacks, grow on demand) + SMP parallelism.
     Con: runtime must handle blocking syscalls (Go hands off the KT).
```

### 9.1 The history and where it landed

- **1:1 won for OS threads.** Linux's original LinuxThreads was a hack; **NPTL** (Native POSIX Thread Library, Drepper & Molnar, 2003) made `pthread_create` a `clone()` producing a 1:1 kernel-scheduled thread. Windows and macOS are 1:1 too. The kernel scheduler is good enough that 1:1's simplicity won.
- **M:N moved into language runtimes.** Pure-OS M:N (old Solaris, NGPT) was abandoned as too complex (the "two schedulers fighting" problem). But the *idea* thrives in **Go (goroutines)**, **Erlang/BEAM processes**, and **Java 21+ virtual threads (Project Loom)** — a userspace scheduler multiplexes millions of cheap green threads over a small pool of OS threads, handling the blocking-syscall handoff itself.

### 9.2 Why M:N is back

The driver is the **C10K → C10M** problem: you want to handle a million concurrent connections, each ideally written in straight-line blocking style. A million OS threads costs ~8 TiB of stack and crushes the scheduler. A million goroutines/virtual threads (KiB stacks, grow on demand, parked on I/O) is feasible. M:N green threading is the answer to "blocking code ergonomics at async-level scale."

---

## 10. Thread-local storage and the GIL contrast

### 10.1 Thread-local storage (TLS)

Some state must be *per-thread* even though threads share the address space — the canonical example is `errno`. If `errno` were a single global, two threads making syscalls would clobber each other's error codes. So `errno` is thread-local (`__thread int errno` conceptually). Mechanisms:

- **C/C++**: `__thread` / `_Thread_local` (C11) / `thread_local` (C++11). The compiler + linker allocate a per-thread TLS block; access goes through the `%fs` segment register on x86-64.
- **POSIX**: `pthread_key_create` + `pthread_getspecific`/`setspecific` for dynamic keys.
- **Python**: `threading.local()`.

TLS is how you make per-thread caches, scratch buffers, and request contexts without locks.

### 10.2 The GIL contrast

CPython has a **Global Interpreter Lock**: only one thread executes Python bytecode at a time. This is *not* a property of threads in general — it's a CPython implementation choice (simplifies the C API and refcounting). Consequences staff engineers must know:

| | CPython threads (GIL) | Processes / true threads |
|---|---|---|
| CPU-bound parallelism | **None** — GIL serializes bytecode | Real, scales with cores |
| I/O-bound concurrency | **Fine** — GIL released during blocking I/O | Fine |
| Best tool for CPU work | `multiprocessing` (fork!) or C extensions that release the GIL | threads |

This is *the* reason Python web servers (Gunicorn, uWSGI) are **prefork** (§11): they scale across cores with *processes*, sidestepping the GIL entirely. (Python 3.13's experimental free-threaded "no-GIL" build aims to change this, but prefork remains the production default in 2026.)

---

## 11. Signals and signal handling

Signals are the Unix mechanism for asynchronous notification — the kernel (or another process) interrupts a process to deliver a small integer. They are also a minefield.

### 11.1 The essentials

| Signal | Default action | Catchable? | Typical use |
|---|---|---|---|
| `SIGTERM` (15) | terminate | yes | **Polite shutdown request** — the one orchestrators send first |
| `SIGKILL` (9) | terminate | **NO** | Unconditional kill; cannot be caught or ignored |
| `SIGINT` (2) | terminate | yes | Ctrl-C |
| `SIGHUP` (1) | terminate | yes | Terminal hangup; by convention "reload config" for daemons |
| `SIGCHLD` (17) | ignore | yes | Child changed state (exit/stop) — drives reaping (§7) |
| `SIGSEGV` (11) | core dump | yes | Invalid memory access |
| `SIGSTOP`/`SIGCONT` | stop/resume | **STOP no** | Job control |
| `SIGPIPE` (13) | terminate | yes | Wrote to a pipe/socket with no reader |

### 11.2 Async-signal-safety — the rule that breaks programs

A signal handler runs *asynchronously*, interrupting the main flow at an arbitrary instruction — possibly in the middle of `malloc` holding its internal lock. If the handler then calls `malloc`, `printf`, or anything not on the **async-signal-safe** list (`signal-safety(7)`), it can **deadlock or corrupt state**. The ironclad rules:

1. In a handler, only call async-signal-safe functions (`write`, `_exit`, `sigaction`, a few others). **No `printf`, no `malloc`, no most-of-libc.**
2. The standard safe pattern: the handler does nothing but set a `volatile sig_atomic_t flag` (or write one byte to a self-pipe / `signalfd` / `eventfd`), and the main loop checks the flag.
3. Block signals you handle elsewhere with `pthread_sigmask`; in multithreaded programs, signals are delivered to *some* unblocked thread — usually you dedicate one thread to signal handling via `sigwait`.

```c
/* The correct minimal handler: just record, don't act. */
static volatile sig_atomic_t got_term = 0;
static void on_term(int sig) { (void)sig; got_term = 1; }  /* async-signal-safe */
/* ... main loop: if (got_term) graceful_shutdown(); ... */
```

The **self-pipe trick** (Bernstein) / modern `signalfd` turns async signals into a readable fd, so your `epoll`/`select` loop handles them synchronously — the standard way event-driven servers deal with signals safely.

---

## 12. Process groups, sessions, and daemons

These exist to support **job control** (the shell) and **service detachment** (daemons).

```
   SESSION (sid) ── led by a session leader (the login shell)
      |   has one controlling terminal (/dev/pts/N)
      |
      +── PROCESS GROUP (pgid)  "foreground"   <- gets Ctrl-C (SIGINT)
      |        |  proc  proc  proc   (a pipeline: ls | grep | wc)
      |
      +── PROCESS GROUP (pgid)  "background"
               |  proc  proc
```

- A **process group** is a set of processes (e.g., all stages of a shell pipeline) that can be signaled together: `kill(-pgid, SIGTERM)` hits the whole group. Ctrl-C sends `SIGINT` to the *foreground* process group.
- A **session** groups process groups and binds to one **controlling terminal**. When the terminal hangs up, `SIGHUP` goes to the session leader.

### 12.1 Becoming a daemon (the classic recipe)

A daemon must survive terminal logout and not hold a controlling TTY. The textbook double-fork:

```text
1. fork(); parent _exit()s        -> child is not a process-group leader
2. setsid()                       -> new session+group, NO controlling tty
3. fork() again; parent _exit()s  -> ensures we can never reacquire a tty
4. chdir("/")                     -> don't pin a mount point
5. umask(0)                       -> predictable file modes
6. close/redirect fds 0,1,2 to /dev/null
7. (optionally) write a PID file, set up syslog
```

> **In 2026, you usually should NOT do this.** Under `systemd`, the correct pattern is `Type=notify` (or `Type=exec`): you run in the **foreground**, log to stdout/stderr (systemd captures them to the journal), and let systemd handle daemonization, restart, and supervision. The double-fork dance is legacy knowledge you must *recognize* (and often *remove*), not code you should write anew.

---

## 13. Working code — a process supervisor (mini init / runit)

A real enterprise pattern: a supervisor that spawns worker processes, **reaps** them when they die, and **restarts** crashed ones with exponential backoff — exactly what `runit`, `s6`, `supervisord`, and a container's PID 1 do. This version is runnable and uses only the stdlib. It demonstrates `fork`/`exec`, `SIGCHLD`-driven reaping, orphan/zombie avoidance, and graceful `SIGTERM` shutdown.

```python
#!/usr/bin/env python3
"""
supervisor.py - a minimal process supervisor (like runit/s6/supervisord).

Spawns N worker processes, reaps them when they exit (no zombies), and
restarts crashed workers with exponential backoff. Handles SIGTERM by
shutting the whole tree down gracefully. POSIX only (uses fork/exec/wait).

Run:  python3 supervisor.py
Then: kill a worker from another shell (kill <pid>) and watch it restart;
      send SIGTERM to the supervisor and watch a clean shutdown.
"""
import os
import sys
import time
import signal
import errno

# Each worker is a child process. In real life this would exec() a service
# binary; here the child runs a trivial loop so the demo is self-contained.
NUM_WORKERS = 3
MAX_BACKOFF = 8.0          # seconds; cap on restart backoff
BACKOFF_BASE = 0.25        # seconds; first restart delay


class Worker:
    __slots__ = ("index", "pid", "backoff", "next_start", "starts")

    def __init__(self, index):
        self.index = index
        self.pid = None
        self.backoff = BACKOFF_BASE
        self.next_start = 0.0     # earliest monotonic time we may (re)start
        self.starts = 0


def worker_main(index):
    """Body of a worker process. Replace with execve() of a real service."""
    # Reset SIGTERM to default so the supervisor can kill us cleanly.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    print(f"[worker {index}] started pid={os.getpid()}", flush=True)
    # Simulate work; worker 1 deliberately crashes to exercise restart logic.
    for tick in range(1000):
        if index == 1 and tick == 3:
            print(f"[worker {index}] simulating a crash", flush=True)
            os._exit(17)          # _exit in child: no stdio double-flush
        time.sleep(1.0)
    os._exit(0)


class Supervisor:
    def __init__(self, n):
        self.workers = [Worker(i) for i in range(n)]
        self.shutting_down = False
        # Self-pipe: the SIGCHLD/SIGTERM handlers only write one byte here,
        # turning async signals into a synchronous, async-signal-safe wakeup.
        self.rfd, self.wfd = os.pipe()
        os.set_blocking(self.rfd, False)
        os.set_blocking(self.wfd, False)
        signal.signal(signal.SIGCHLD, self._wake)
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)

    def _wake(self, signum, frame):
        try:
            os.write(self.wfd, b"x")     # async-signal-safe: just a byte
        except OSError:
            pass

    def _on_term(self, signum, frame):
        self.shutting_down = True
        self._wake(signum, frame)

    def spawn(self, w):
        pid = os.fork()
        if pid == 0:
            # ---- child ----
            os.close(self.rfd)
            os.close(self.wfd)
            worker_main(w.index)
            os._exit(127)                # unreachable; safety net
        # ---- parent ----
        w.pid = pid
        w.starts += 1
        print(f"[supervisor] started worker {w.index} pid={pid} "
              f"(start #{w.starts})", flush=True)

    def reap(self):
        """Reap ALL exited children (signals don't queue counts -> loop)."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return                    # no children left
            if pid == 0:
                return                    # no more state changes pending
            self._on_child_exit(pid, status)

    def _on_child_exit(self, pid, status):
        w = next((x for x in self.workers if x.pid == pid), None)
        if w is None:
            return
        if os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
            why = f"exited code={code}"
            clean = (code == 0)
        elif os.WIFSIGNALED(status):
            why = f"killed by signal {os.WTERMSIG(status)}"
            clean = False
        else:
            why = "unknown"
            clean = False
        w.pid = None
        print(f"[supervisor] worker {w.index} (pid {pid}) {why}", flush=True)

        if self.shutting_down or clean:
            return
        # Crash: schedule a backoff restart (avoid tight crash loops).
        w.next_start = time.monotonic() + w.backoff
        print(f"[supervisor] will restart worker {w.index} in "
              f"{w.backoff:.2f}s", flush=True)
        w.backoff = min(w.backoff * 2, MAX_BACKOFF)

    def run(self):
        for w in self.workers:
            self.spawn(w)
        while True:
            # Block until a signal nudges the self-pipe (or a 1s timeout so
            # backoff timers fire). select avoids a busy loop.
            import select
            try:
                select.select([self.rfd], [], [], 1.0)
            except InterruptedError:
                pass
            self._drain_pipe()
            self.reap()                  # collect any dead children

            if self.shutting_down:
                self._shutdown()
                return

            now = time.monotonic()
            for w in self.workers:
                if w.pid is None and now >= w.next_start:
                    self.spawn(w)

    def _drain_pipe(self):
        try:
            while os.read(self.rfd, 4096):
                pass
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise

    def _shutdown(self):
        print("[supervisor] SIGTERM received; shutting down workers", flush=True)
        for w in self.workers:
            if w.pid:
                try:
                    os.kill(w.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        # Reap with a deadline; escalate to SIGKILL for stragglers.
        deadline = time.monotonic() + 5.0
        while any(w.pid for w in self.workers) and time.monotonic() < deadline:
            self.reap()
            time.sleep(0.05)
        for w in self.workers:
            if w.pid:
                print(f"[supervisor] worker {w.index} ignored TERM; SIGKILL",
                      flush=True)
                try:
                    os.kill(w.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.reap()
        print("[supervisor] all workers down; exiting", flush=True)


if __name__ == "__main__":
    if os.name != "posix":
        sys.exit("POSIX only (needs fork/waitpid/SIGCHLD)")
    Supervisor(NUM_WORKERS).run()
```

**What this teaches:**

- **Reaping in a `WNOHANG` loop** (`reap`) — because if two workers die nearly simultaneously, you get *one* `SIGCHLD`, not two. Looping until `waitpid` returns 0 collects them all. Miss this and you leak zombies.
- **The self-pipe trick** turns the async `SIGCHLD`/`SIGTERM` into a byte your `select` loop reads synchronously — the handler does nothing unsafe.
- **Exponential backoff** prevents a crash-looping worker from pegging a core restarting 1000×/sec — the same reason systemd has `StartLimitIntervalSec`.
- **Graceful then forceful shutdown**: `SIGTERM`, wait, then `SIGKILL` stragglers — exactly Kubernetes' `terminationGracePeriodSeconds` model.

---

## 14. Working code — a prefork worker-pool server

The model behind **Apache `prefork` MPM, Gunicorn sync workers, uWSGI, and PostgreSQL**: the parent opens a listening socket, then forks N workers that **all `accept()` on the same socket**. The kernel load-balances incoming connections across them. No per-request fork; no threads; full crash isolation per worker.

```python
#!/usr/bin/env python3
"""
prefork_server.py - a prefork TCP echo server (the Apache/Gunicorn model).

The parent binds+listens, then forks N workers. Every worker blocks in
accept() on the SAME inherited listening fd; the kernel hands each new
connection to exactly one worker (it serializes accept() internally on
modern Linux, avoiding the classic 'thundering herd'). Crash isolation:
if a worker dies, the parent reaps and replaces it.

Run:    python3 prefork_server.py
Test:   printf 'hello\\n' | nc 127.0.0.1 8080      (or: telnet 127.0.0.1 8080)
"""
import os
import socket
import signal
import sys
import time

HOST, PORT = "127.0.0.1", 8080
NUM_WORKERS = 4


def make_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(128)                       # backlog
    return s


def worker_loop(listener, worker_id):
    """Each worker independently accept()s and serves connections."""
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    print(f"[worker {worker_id}] pid={os.getpid()} accepting", flush=True)
    while True:
        try:
            conn, addr = listener.accept()   # kernel picks ONE worker
        except InterruptedError:
            continue
        except OSError:
            return
        with conn:
            conn.sendall(f"served by worker {worker_id} "
                         f"(pid {os.getpid()})\n".encode())
            data = conn.recv(4096)           # echo
            if data:
                conn.sendall(b"echo: " + data)


def main():
    if os.name != "posix":
        sys.exit("POSIX only (needs fork)")
    listener = make_listener()
    workers = {}                            # pid -> worker_id

    def spawn(worker_id):
        pid = os.fork()
        if pid == 0:
            worker_loop(listener, worker_id)
            os._exit(0)
        workers[pid] = worker_id
        return pid

    for wid in range(NUM_WORKERS):
        spawn(wid)

    shutting = {"down": False}

    def on_term(signum, frame):
        shutting["down"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    print(f"[master] pid={os.getpid()} listening on {HOST}:{PORT} "
          f"with {NUM_WORKERS} workers", flush=True)

    while not shutting["down"]:
        try:
            pid, status = os.waitpid(-1, 0)   # block until a worker dies
        except InterruptedError:
            continue
        except ChildProcessError:
            break
        if shutting["down"]:
            break
        wid = workers.pop(pid, None)
        if wid is not None:
            print(f"[master] worker {wid} (pid {pid}) died; respawning",
                  flush=True)
            time.sleep(0.1)                   # tiny backoff
            spawn(wid)

    # Graceful shutdown
    print("[master] shutting down workers", flush=True)
    for pid in list(workers):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    while workers:
        try:
            pid, _ = os.waitpid(-1, 0)
            workers.pop(pid, None)
        except ChildProcessError:
            break
    listener.close()
    print("[master] done", flush=True)


if __name__ == "__main__":
    main()
```

**What this teaches:**

- **One listening socket, many accepting workers.** The fd is inherited across `fork`, so all workers share the *same* kernel socket and its accept queue. The kernel — not your code — load-balances.
- **The thundering herd** (historical): when a connection arrived, *all* blocked workers used to wake, one won `accept`, the rest went back to sleep — wasted wakeups. Modern Linux serializes `accept()` wakeups (and `SO_REUSEPORT` gives each worker its *own* queue for even better scaling — the nginx/Envoy approach).
- **Crash isolation**: a worker segfault takes down one connection's worker, not the server. The master reaps and respawns. This is *the* reason Postgres uses one process per connection — a backend crash can't corrupt sibling backends' memory.
- **Why prefork for Python**: it sidesteps the GIL (§10) — N processes = N cores of real parallelism.

---

## 15. Working code — measuring context-switch cost

Context switches are not free: the kernel saves/restores registers, switches the kernel stack, and — for a *process* switch — reloads `CR3` (the page-table base), flushing much of the TLB. This C program measures the round-trip cost by ping-ponging a token between two processes over a pipe pair, forcing a context switch each way.

```c
/* ctxsw.c - measure context-switch cost via pipe ping-pong between two
 * processes. Each round-trip = 2 context switches + 2 tiny pipe ops.
 *
 * Build: cc -O2 -o ctxsw ctxsw.c
 * Run:   taskset -c 0 ./ctxsw     (pin to one CPU to force real switches;
 *                                   without pinning the two procs may run
 *                                   on different cores and never switch)
 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <time.h>
#include <sys/wait.h>

#define ROUNDS 1000000

static double now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}

int main(void) {
    int p2c[2], c2p[2];          /* parent->child, child->parent pipes */
    if (pipe(p2c) || pipe(c2p)) { perror("pipe"); return 1; }

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }

    char tok = 'x';
    if (pid == 0) {
        /* ---- child: read then echo back, ROUNDS times ---- */
        close(p2c[1]); close(c2p[0]);
        for (long i = 0; i < ROUNDS; i++) {
            if (read(p2c[0], &tok, 1) != 1) _exit(1);
            if (write(c2p[1], &tok, 1) != 1) _exit(1);
        }
        _exit(0);
    }

    /* ---- parent: kick off, time the ping-pong ---- */
    close(p2c[0]); close(c2p[1]);
    double t0 = now_ns();
    for (long i = 0; i < ROUNDS; i++) {
        if (write(p2c[1], &tok, 1) != 1) { perror("write"); return 1; }
        if (read(c2p[0], &tok, 1) != 1)  { perror("read");  return 1; }
    }
    double t1 = now_ns();
    wait(NULL);

    double total_ns = t1 - t0;
    /* Each round = 2 switches (parent->child, child->parent). */
    double per_switch = total_ns / (ROUNDS * 2.0);
    printf("rounds=%d  total=%.1f ms  per round-trip=%.0f ns  "
           "per context switch ~= %.0f ns\n",
           ROUNDS, total_ns / 1e6, total_ns / ROUNDS, per_switch);
    return 0;
}
```

**What this teaches:**

- Typical results on modern x86-64: **~1–5 microseconds per round-trip**, i.e. **~0.5–2.5 us per context switch** (the pipe `read`/`write` syscalls themselves contribute, so this is an upper bound on the *pure* switch cost).
- **`taskset -c 0`** pins both processes to one core. Without pinning, the scheduler may place them on separate cores where they run concurrently and *never* context-switch against each other — you'd measure cache-line ping-pong instead. This pinning subtlety is exactly the kind of thing that invalidates naïve benchmarks.
- Process switches are pricier than thread switches because of the **TLB flush** on `CR3` reload. Modern CPUs mitigate this with **PCID/ASID** (tagged TLB entries), and the kernel's lazy-TLB tricks help — but the gap is real, and it's why thread pools beat process pools for switch-heavy workloads.
- At ~1 us/switch, a server doing 100k switches/sec/core burns ~10% of a core on switching alone — which is why event loops (one thread, many connections) exist: they amortize away the per-connection switch.

---

## 16. Advanced: fork() hazards at scale & the container PID 1 problem

### fork() in a multithreaded process is a minefield

`fork()` duplicates **only the calling thread**, but the child inherits the full
memory image — including mutexes that other (now non-existent) threads held. If
another thread held the allocator lock at fork time, the child can **deadlock the
first time it calls `malloc`**. POSIX therefore allows only **async-signal-safe**
functions between `fork()` and `exec()` in a multithreaded program.

```
   parent: thread A holds malloc lock; thread B calls fork()
        -> child inherits the lock STATE (held) but NOT thread A to release it
        -> child's first malloc() -> deadlock forever
```

Safe patterns: `fork()` immediately followed by `exec()` (you replace the image, so
held-lock state is irrelevant), **`posix_spawn()`** (fork+exec done safely), or — as
a fragile last resort — `pthread_atfork()` handlers. This is why language runtimes
warn against `os.fork()` after threads start, and why a threaded server should not
"fork a worker that keeps running in-process."

### COW page-table copy cost — the Redis fork-latency class

COW makes the *data* copy lazy ([§6](#6-copy-on-write-how-fork-stays-cheap)), but
`fork()` must still **copy the page tables synchronously**. For a process with a huge
RSS (a 100 GB in-memory store), that page-table copy alone is tens-to-hundreds of ms
— a stall on the parent. This is the root of **Redis `BGSAVE`/fork latency spikes**:
the snapshot child is cheap data-wise, but the fork itself stalls the event loop.
Mitigations: huge pages shrink the page-table volume to copy
([03 §11](03_memory_management.md)); `madvise(MADV_DONTFORK)` excludes regions; or
avoid fork-based snapshots for very large heaps. Measure it with
[`examples/fork_cost_probe.py`](examples/README.md).

### The container PID 1 problem

A process running as **PID 1** has special kernel semantics: it gets **no default
signal dispositions**, and it is the only process that reaps orphaned zombies
([§7](#7-zombies-and-orphans--the-two-reaping-failures)). In containers:

- App is PID 1 with no `SIGTERM` handler → `docker stop`/k8s SIGTERM is **ignored**,
  so the container is `SIGKILL`ed after the grace period — no graceful drain
  ([scenarios 05](../enterprise_scenarios/05_cross_layer_triage.md)).
- App spawns children but doesn't reap → **zombies accumulate**.
- Shell-form `CMD` (`sh -c "app"`) makes the *shell* PID 1, which may not forward
  signals to the app at all.

The fix is a tiny init as PID 1 — **`tini`** (`docker run --init`), `dumb-init`, or
`s6` — that forwards signals and reaps zombies, with your app as its child. This is
the OS-level cause behind the container-drain failures in the runbooks.

---

## 17. Trade-offs summary

- **Process vs. thread is a crash-isolation vs. communication-cost trade.** Isolation and "no shared mutable state" bugs → processes. Cheap switching and shared big read-only state → threads. There is no universally right answer; there is a right answer *for your blast-radius tolerance*.
- **fork() + COW is cheap until something writes.** Refcounting GCs (CPython, Ruby) silently defeat COW; plan worker memory as approaching N× for those runtimes.
- **Reaping is mandatory.** Either `wait()` in a `SIGCHLD` loop, or `SIG_IGN`/`SA_NOCLDWAIT`, or run a real init in containers. Zombies exhaust PIDs; orphans must land on an init that reaps.
- **Signal handlers must be async-signal-safe** — set a flag / write to a self-pipe and act in the main loop. Everything else is a latent deadlock.
- **1:1 won for OS threads; M:N won inside language runtimes** (goroutines, virtual threads) for million-connection scale with blocking-style code.
- **The GIL is why Python servers prefork.** Processes give you the cores threads can't.
- **Daemonization is mostly systemd's job now** — run in the foreground, log to stdout, let the supervisor supervise.

## 18. Key takeaways

1. A **process owns resources** (one address space, fd table, credentials); a **thread is a unit of scheduling** that shares them. On Linux both are `task_struct`s created by `clone()` with different sharing flags.
2. The **address space** (text/data/heap/stack/mmap) plus the `task_struct` *is* the process; `/proc/<pid>/maps` and `smaps` (use **PSS**) are how you see the truth at runtime.
3. **fork/exec/wait/exit** separate duplication from program-loading; **COW** makes fork cheap; **`_exit` in children** avoids stdio double-flush.
4. **Zombies (unreaped) and orphans (parent died first)** are the default reaping failures; loop `waitpid(WNOHANG)` under `SIGCHLD`.
5. **Signals are async and dangerous** — only async-signal-safe calls in handlers; the self-pipe / `signalfd` pattern makes them synchronous and safe.
6. **Prefork** (one socket, many accepting processes) gives crash isolation and sidesteps the GIL — the model behind Apache, Gunicorn, and Postgres.
7. **Context switches cost ~1 us**, more for processes (TLB flush) than threads; pin with `taskset` when benchmarking or the switch may never happen.

> Read next: [02 — CPU Scheduling](02_cpu_scheduling.md) for *how* the kernel decides which runnable `task_struct` actually gets the CPU, and why `vruntime`, nice values, and cgroup quotas behave the way they do.
