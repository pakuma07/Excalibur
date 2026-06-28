# 06 — I/O, Interrupts & Async I/O

> **Audience:** staff/principal. You can write a socket server. This doc is about *how I/O actually crosses the user/kernel boundary and reaches the device*, and why the event-loop architecture (nginx, Redis, Node) and io_uring exist — the design space from a blocking `read()` to a fully asynchronous, batched, zero-syscall submission ring.
>
> **Primary sources:** Kerrisk, *The Linux Programming Interface* (TLPI) ch. 3, 44, 59–63 (sockets, epoll, alternative I/O models); Tanenbaum, *Modern Operating Systems* ch. 5 (I/O); Silberschatz, *Operating System Concepts* ch. 12–13; Arpaci-Dusseau, OSTEP — I/O devices & interrupts; Jens Axboe, *Efficient IO with io_uring* (the io_uring design document); Dan Kegel, *The C10K problem*.

---

## 1. Why this matters at scale

A modern service spends most of its life *waiting for I/O* — a socket, a disk, an RPC. How you wait determines how many concurrent operations one box can handle and at what latency. The architectural arc is:

```
  one thread per connection   ──►  I/O multiplexing (select/poll/epoll)  ──►  async rings (io_uring)
  (simple; ~10K thread limit)      (one thread, many fds; the C10K answer)   (batched, syscall-light)
```

Three cost centers drive every decision here:

1. **The syscall boundary.** Crossing user→kernel→user is not free (mode switch, register save, possible cache/TLB effects, and post-Spectre/Meltdown mitigations made it more expensive). At a million IOPS, *syscalls per op* is a first-order metric — the entire reason io_uring batches.
2. **Who waits, and how.** Blocking a thread is simple but a thread costs ~MiB of stack and a scheduler slot. The C10K problem is "you cannot afford 10,000 threads"; the answer is one thread watching thousands of fds.
3. **How many copies.** A naive file-to-socket transfer copies data disk→page cache→user buffer→socket buffer→NIC. Zero-copy (`sendfile`/`splice`) removes the user-space round trips — the difference between a CDN saturating a 100 GbE NIC and falling over.

---

## 2. How a system call works

A system call is a **controlled transition from user mode (ring 3) to kernel mode (ring 0)**. The CPU enforces the privilege boundary; a syscall is the *only* sanctioned doorway through it.

```
  user space                      kernel space
  ─────────                       ────────────
  read(fd, buf, n)
    libc wrapper:
      mov  rax, 0      ; syscall number (read = 0 on x86-64 Linux)
      mov  rdi, fd     ; arg1
      mov  rsi, buf    ; arg2
      mov  rdx, n      ; arg3
      syscall  ───────────────►  CPU switches to ring 0, jumps to the
                                 syscall entry point (MSR_LSTAR)
                                   │
                                   ▼
                                 sys_call_table[rax]  ──► ksys_read(...)
                                   │  (do the work: page cache / block layer)
                                   ▼
                                 return value in rax
      ◄──────────────────────  sysret: back to ring 3
    errno = -rax if negative
```

Steps in detail (TLPI §3.1):
1. The app calls a **libc wrapper** (`read`), which marshals arguments into registers and puts the **syscall number** in `rax`.
2. The `syscall` instruction switches to kernel mode and jumps to a fixed entry point.
3. The kernel saves user registers, **validates arguments** (a bad pointer must not crash the kernel — `copy_from_user` checks it), and dispatches via the **system call table** indexed by the syscall number.
4. The handler runs in kernel mode, then `sysret`/`iret` restores user state and returns. A negative return becomes `errno`.

### 2.1 The cost

| | Approx cost |
|---|---|
| Function call | ~1 ns |
| System call (round trip) | ~100–500 ns (mode switch + entry/exit) |
| Syscall with Spectre/Meltdown mitigations (KPTI) | higher — TLB flush on the boundary |
| Context switch (to another thread) | ~1–5 µs (scheduler + cache/TLB cold) |

These numbers are why **vDSO** exists (`gettimeofday`/`clock_gettime` are served from a user-mapped page with *no* kernel transition), why **batching** matters (one `writev` beats many `write`s), and ultimately why **io_uring** lets you submit thousands of operations with *zero or one* syscall.

---

## 3. Interrupts vs polling, and DMA

How does the CPU learn a device finished?

- **Polling (busy-wait / PIO):** the CPU repeatedly reads a status register. Simple, lowest latency for *very* fast devices, but burns 100% CPU while waiting. Used for the fastest NVMe paths (`io_uring` polled mode, `NVMe poll queues`) where an interrupt's ~µs latency and context switch is *more* expensive than spinning.
- **Interrupts:** the device raises an IRQ; the CPU stops, runs an **interrupt handler (ISR)**, and resumes. Efficient when waits are long (the CPU does other work meanwhile), but each interrupt costs a context switch and at millions of IOPS the **interrupt storm** itself becomes the bottleneck → mitigated by **interrupt coalescing** (NAPI for networking: switch to polling under load) and per-queue MSI-X interrupts spread across cores.

**DMA (Direct Memory Access)** is the other half. Without it, the CPU would copy every byte between device and RAM (*programmed I/O*). With DMA, the CPU programs a **DMA controller** with a source, destination, and length; the controller moves the data **directly to/from RAM** and raises a *single* interrupt on completion.

```
  Without DMA (PIO):   device ─byte─► CPU register ─byte─► RAM   (CPU does every copy)
  With DMA:            device ───────────────────────────► RAM   (DMA engine moves it)
                       CPU is free; one interrupt when done
```

This is foundational: zero-copy techniques (§11) work *because* DMA can move data NIC↔RAM without the CPU touching it.

---

## 4. The I/O path, end to end

Putting §2–3 together, here is a buffered disk read:

```
  1. read(fd, buf, n)            user → kernel (syscall)
  2. VFS → filesystem            is the page in the page cache?
        hit  → copy_to_user, return    (no device I/O)
        miss → 3..7
  3. block layer builds a bio, queues it (blk-mq), scheduler orders it
  4. device driver programs the controller; sets up DMA
  5. CPU is free; the calling thread is put to SLEEP (blocking) / returns EAGAIN (non-blocking)
  6. device completes → raises an INTERRUPT
  7. ISR: DMA already placed data in a page-cache page; mark the bio complete,
     WAKE the sleeping thread
  8. thread resumes: copy page-cache page → user buf, return n
```

The thread sleeps at step 5 and is woken at step 7 — that *blocking* is what an event loop or async model avoids. Note the data copy at step 8 (page cache → user buffer): zero-copy removes exactly this copy for the file-to-socket case.

---

## 5. Blocking vs non-blocking vs asynchronous I/O

These three are distinct axes that are constantly conflated.

| Model | `read()` behaviour when no data | Notification | Mental model |
|---|---|---|---|
| **Blocking** (default) | thread **sleeps** until data arrives | none needed | "wait here until done" |
| **Non-blocking** (`O_NONBLOCK`) | returns **`EAGAIN`** immediately | you must **poll** / re-try | "tell me if it'd block; I'll check back" |
| **I/O multiplexing** (select/poll/epoll) | n/a — you ask *which fds are ready*, then do non-blocking reads | readiness event | "tell me which of these 10k fds can be read now" |
| **Signal-driven** (`SIGIO`) | kernel sends a signal when ready | async signal | "interrupt me when ready" (rarely used) |
| **Asynchronous** (POSIX AIO, **io_uring**) | you submit the *operation*; kernel does it and tells you when **complete** | completion event | "do this whole read and notify me when the *result* is ready" |

The key distinction — **readiness vs completion**:

- `epoll` is a **readiness** interface: it tells you a socket *can* be read without blocking; *you* then issue the `read`. (Reactor pattern.)
- `io_uring`/AIO are **completion** interfaces: you ask the kernel to *perform* the read; it hands you the finished result. (Proactor pattern.) This is what lets *disk* I/O be truly async — `epoll` famously does **not** work for regular files (they're "always ready", so epoll can't hide the blocking disk read).

---

## 6. The C10K problem

Dan Kegel's 1999 framing: a single server should handle **10,000 concurrent connections**. The naive **thread-per-connection** model breaks down:

- Each thread needs a stack (default ~8 MiB virtual, ~MiB resident) → 10K threads ≈ tens of GiB of address space and real memory pressure.
- The scheduler must time-slice 10K runnable threads; context switches dominate CPU.
- `select`/`poll` are **O(n)** in the number of fds — you hand the kernel the whole set on every call, and it scans all of them. At 10K fds called thousands of times/sec, that's quadratic-ish waste.

The answer, realized by nginx, lighttpd, HAProxy, Redis, and Node.js: **one (or a few) threads running an event loop over an O(1)-readiness interface (`epoll`/`kqueue`)**, doing non-blocking I/O. Today the bar is C10M (ten million); the same principles plus kernel-bypass (DPDK) and io_uring apply.

```
  thread-per-connection            event loop (reactor)
  ───────────────────────          ─────────────────────
  10,000 threads                   1 thread
  10,000 stacks (~GiB)             1 stack
  blocking read per thread         epoll_wait -> handle ready fds (non-blocking)
  scheduler thrash                 no per-conn thread; CPU spent on work, not switching
```

---

## 7. I/O multiplexing: select / poll / epoll / kqueue

| | `select` | `poll` | `epoll` (Linux) | `kqueue` (BSD/macOS) |
|---|---|---|---|---|
| fd limit | `FD_SETSIZE` (1024) | none | none | none |
| Cost per call | **O(n)** rescan all fds | **O(n)** | **O(ready)** | **O(ready)** |
| fd set passed each call | yes (recopied) | yes | **no** — registered once in the kernel | no |
| Stateful kernel structure | no | no | **yes** (`epoll` instance) | yes (`kqueue`) |
| Trigger modes | level | level | **level or edge** | level or edge |

**Why epoll wins at scale:** with `select`/`poll` you pass the entire fd set into the kernel on *every* call and the kernel scans all of them — O(n). With `epoll` you register fds once (`epoll_ctl`), and the kernel maintains a **ready list** updated by interrupts; `epoll_wait` returns only the fds that are actually ready — **O(number of ready events)**, independent of total fds. That is the whole game for 100K idle-but-watched connections.

### 7.1 Level-triggered vs edge-triggered

| | **Level-triggered (LT)** | **Edge-triggered (ET)** |
|---|---|---|
| Fires while | the condition *is true* (data is available) | the condition *transitions* (new data arrived) |
| If you don't drain | you'll be told again next `epoll_wait` | **you won't be told again** — you must read until `EAGAIN` |
| Difficulty | forgiving (default) | must drain fully each event or you stall the fd |
| Used by | most code, `select`/`poll` semantics | high-performance servers minimizing wakeups |

> ET rule: on an edge-triggered fd you **must** loop `read()`/`accept()` until you get `EAGAIN`, or you'll leave data unread with no further notification. ET reduces redundant wakeups (fewer `epoll_wait` returns) but every handler must be written to fully drain. nginx uses ET.

---

## 8. Enterprise working example: a single-threaded epoll echo server

This is the nginx/Redis architecture in miniature — one thread, non-blocking sockets, a readiness loop over Python's `selectors` (which uses `epoll` on Linux, `kqueue` on BSD/macOS). It handles thousands of concurrent connections without a thread per connection.

```python
"""
epoll_echo.py — single-threaded, event-loop TCP echo server.

The nginx/Redis model: one thread, non-blocking sockets, an epoll-backed
readiness loop. Scales to thousands of connections with O(ready) wakeups
and zero threads-per-connection.

Run server:  python epoll_echo.py 8888
Test:        printf 'hello\n' | nc 127.0.0.1 8888
Load test:   (see the client at the bottom)  python epoll_echo.py --client 8888 2000
"""
from __future__ import annotations
import selectors
import socket
import sys

# selectors.DefaultSelector picks epoll on Linux, kqueue on BSD/macOS,
# falling back to poll/select. This is the standard-library event loop core.
sel = selectors.DefaultSelector()


def run_server(port: int) -> None:
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("0.0.0.0", port))
    lsock.listen(1024)               # large backlog: many pending connects
    lsock.setblocking(False)         # non-blocking accept
    sel.register(lsock, selectors.EVENT_READ, data=None)  # data=None => listener
    print(f"echo server (event loop) listening on :{port}")

    while True:
        # epoll_wait under the hood: returns ONLY the fds that are ready.
        events = sel.select(timeout=None)
        for key, mask in events:
            if key.data is None:
                _accept(key.fileobj)         # the listening socket is ready
            else:
                _serve(key, mask)            # a client socket is ready


def _accept(lsock: socket.socket) -> None:
    # Edge cases: accept() may yield several pending conns; loop until EAGAIN.
    while True:
        try:
            conn, addr = lsock.accept()
        except BlockingIOError:
            return                            # drained all pending connects
        conn.setblocking(False)
        # Per-connection state: an outbound buffer for partial writes.
        sel.register(conn, selectors.EVENT_READ, data={"addr": addr, "out": b""})


def _serve(key, mask) -> None:
    sock: socket.socket = key.fileobj
    state = key.data
    if mask & selectors.EVENT_READ:
        try:
            data = sock.recv(65536)           # non-blocking read
        except BlockingIOError:
            return
        except ConnectionResetError:
            _close(sock)
            return
        if not data:                          # peer closed
            _close(sock)
            return
        state["out"] += data                  # echo: queue what we read

    if state["out"]:
        try:
            sent = sock.send(state["out"])    # may send fewer than offered
            state["out"] = state["out"][sent:]
        except BlockingIOError:
            pass                              # socket buffer full; try later
        except (BrokenPipeError, ConnectionResetError):
            _close(sock)
            return

    # Watch for write-readiness only when we have queued output. This avoids
    # busy-wakeups on writable sockets with nothing to send.
    want = selectors.EVENT_READ
    if state["out"]:
        want |= selectors.EVENT_WRITE
    sel.modify(sock, want, data=state)


def _close(sock: socket.socket) -> None:
    try:
        sel.unregister(sock)
    except KeyError:
        pass
    sock.close()


def run_client(port: int, n_conns: int) -> None:
    """Open many concurrent connections, send/recv once each, to show one
    server thread fielding thousands of simultaneous sockets."""
    import time
    socks = []
    for _ in range(n_conns):
        s = socket.create_connection(("127.0.0.1", port))
        socks.append(s)
    t0 = time.monotonic()
    for i, s in enumerate(socks):
        s.sendall(f"msg{i}\n".encode())
    ok = 0
    for i, s in enumerate(socks):
        if s.recv(4096) == f"msg{i}\n".encode():
            ok += 1
        s.close()
    dt = time.monotonic() - t0
    print(f"{ok}/{n_conns} round-trips OK in {dt*1000:.1f} ms "
          f"({n_conns/dt:,.0f} conn/s) on a single server thread")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--client":
        run_client(int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 1000)
    else:
        run_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8888)
```

**What this demonstrates:**
- One thread, `sel.select()` returns **only ready fds** — the O(ready) property that beats `select`/`poll`.
- Non-blocking sockets everywhere; `BlockingIOError` (= `EAGAIN`) is the normal "would block, try later" path, *not* an error.
- **Per-connection state** (`data={...}`) holds a partial-write buffer — because `send()` may transmit fewer bytes than offered (the socket send buffer filled), the hallmark issue event loops must handle.
- We only ask for `EVENT_WRITE` when we have queued output, avoiding a busy loop on always-writable sockets. This is exactly what production reactors (libuv, nginx) do.

---

## 9. Thread-per-connection vs event loop

| | **Thread-per-connection** | **Event loop (reactor)** |
|---|---|---|
| Concurrency unit | OS thread | callback / coroutine on one thread |
| Memory per conn | ~MiB (stack) | ~KiB (state object) |
| 10K connections | ~tens of GiB, scheduler thrash | one thread, modest RAM |
| Programming model | **simple** — linear, blocking code | inverted control flow (callbacks) or async/await |
| CPU parallelism | uses all cores naturally | one core per loop → run **N loops** (nginx workers, Redis... mostly single) |
| A slow handler | blocks only its own thread | **blocks the whole loop** (head-of-line) — never do blocking work in the loop |
| Used by | classic Apache (prefork), JDBC pools, Go (goroutines hide this) | nginx, Redis, Node.js, HAProxy, Envoy |

The honest middle ground:

- **Go** gives you the *simple* blocking programming model but multiplexes goroutines onto an epoll-based runtime under the hood — best of both, at the cost of a runtime.
- **The cardinal sin of event loops:** doing CPU-heavy or blocking work (a synchronous DB call, `bcrypt`, a big JSON parse) *inside the loop* stalls every connection. The fix is a thread/process pool for blocking work, or sharding across multiple loop processes (nginx workers, one per core; Redis is single-threaded for the data path and explicitly tells you not to block it).

```
  Hybrid that production systems actually run:
     N event-loop worker processes (one per core)   ← accept(), I/O
              │ offload CPU/blocking work
              ▼
     bounded thread pool / separate service
```

---

## 10. io_uring — the modern Linux async I/O

POSIX AIO was disappointing (effectively a thread pool for files; limited). Linux `aio` (`io_submit`) only worked well with `O_DIRECT`. **io_uring** (Jens Axboe, kernel 5.1, 2019) is the real answer: a true asynchronous, **completion-based** interface built on two shared **ring buffers** in memory shared between user space and the kernel.

```
  Two rings in memory mapped into BOTH user space and the kernel:

   user space                                   kernel
   ──────────                                   ──────
   Submission Queue (SQE ring)  ──────────────► kernel consumes SQEs,
     app writes "read fd X into buf, len n"      performs the I/O async
                                                     │
   Completion Queue (CQE ring)  ◄──────────────── kernel posts results
     app reads "request #7 done, returned n"      (CQE: result + user_data)
```

Why it is fast:

- **No per-op syscall.** You fill many **SQEs** (submission queue entries) in the shared ring, then call `io_uring_enter` **once** to submit a batch. With **`SQPOLL`** mode a dedicated kernel thread polls the SQ ring, so you can submit I/O with *zero* syscalls.
- **No data copy of the request.** The rings are shared memory; you're not passing structures across the boundary each time.
- **Everything is async, including files.** Unlike `epoll`, io_uring does *real* async for regular-file reads/writes (and `fsync`, `accept`, `send`, `recv`, `openat`, …) — it unifies disk and network async under one interface.
- **Advanced features:** fixed/registered buffers and files (skip per-op refcount/lookup), linked SQEs (chain "read then write"), multishot accept/recv.

> Trade-off & caution: io_uring is powerful but has a larger kernel attack surface (several CVEs and 2023–2024 hardening; some sandboxes disable it). It also has a steeper API — most people use **liburing**. For an event-loop server it can replace epoll *and* give async disk I/O in one mechanism.

### 10.1 Working example: io_uring via liburing (C)

This reads a file fully asynchronously: submit a `read` SQE, wait for the CQE, print the bytes. It is the smallest complete io_uring program.

```c
/* uring_read.c — read a file via io_uring (completion-based async I/O).
   Build:  cc -O2 -o uring_read uring_read.c -luring
   Run:    ./uring_read /etc/hostname
   Requires: liburing-dev and Linux >= 5.1.                              */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <liburing.h>

#define BUFSZ 4096

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s PATH\n", argv[0]); return 2; }

    int fd = open(argv[1], O_RDONLY);
    if (fd < 0) { perror("open"); return 1; }

    struct io_uring ring;
    if (io_uring_queue_init(8, &ring, 0) < 0) {   /* 8-entry rings */
        perror("io_uring_queue_init"); return 1;
    }

    char buf[BUFSZ];

    /* 1) Get a submission queue entry and describe the async read. */
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf, sizeof(buf), 0);  /* offset 0 */
    io_uring_sqe_set_data(sqe, (void *)0xCAFE);        /* user_data tag */

    /* 2) Submit the batch (here, one op) with a SINGLE syscall. */
    if (io_uring_submit(&ring) < 0) { perror("io_uring_submit"); return 1; }

    /* 3) Wait for the completion (CQE) — the result of the async read. */
    struct io_uring_cqe *cqe;
    if (io_uring_wait_cqe(&ring, &cqe) < 0) { perror("io_uring_wait_cqe"); return 1; }

    if (cqe->res < 0) {
        fprintf(stderr, "async read failed: %s\n", strerror(-cqe->res));
        return 1;
    }
    /* user_data round-trips so you can match completions to requests. */
    /* (void)io_uring_cqe_get_data(cqe); */
    int nread = cqe->res;
    io_uring_cqe_seen(&ring, cqe);   /* mark CQE consumed (advance ring) */

    printf("async read %d bytes:\n%.*s", nread, nread, buf);

    io_uring_queue_exit(&ring);
    close(fd);
    return 0;
}
```

The shape generalizes: to drive thousands of concurrent I/Os you fill *many* SQEs, `io_uring_submit()` once, then loop over CQEs as they complete — one event loop, async disk *and* network, minimal syscalls.

---

## 11. Zero-copy: sendfile, splice, mmap

The classic file-to-socket transfer (a web server sending a static file) does **four copies and two context switches** the naive way:

```
  read(file_fd, buf, n):   disk ─DMA─► page cache ─copy─► user buffer
  write(sock_fd, buf, n):  user buffer ─copy─► socket buffer ─DMA─► NIC
                           = 2 CPU copies + 2 DMA + 2 syscalls (4 mode switches)
```

**`sendfile(out_fd, in_fd, ...)`** moves data **between two fds entirely in the kernel** — no user-space buffer:

```
  sendfile:  disk ─DMA─► page cache ──(kernel)──► socket buffer ─DMA─► NIC
             with "scatter-gather" DMA the page-cache→socket CPU copy is also
             eliminated: NIC DMAs straight from the page cache.
             = 0 user-space copies, 1 syscall
```

| Technique | What it does | Copies removed | Constraint |
|---|---|---|---|
| **`sendfile`** | fd→fd in kernel (file→socket) | the two user-space copies | `in_fd` must be mmap-able (a file); historically `out_fd` a socket |
| **`splice`** | move data between fds via a kernel **pipe** buffer | user-space copies | one end must be a pipe; more general than sendfile |
| **`mmap` + `write`** | map file into user address space, write the mapping | the read() copy (file→user) | still copies user→socket; page-fault driven; good for random access |
| **`MSG_ZEROCOPY`** | zero-copy `send()` of user data to a socket | the user→kernel send copy | async completion via the error queue; for large sends |

This is why **Netflix/CDN** workloads care so much: `sendfile` lets one box saturate a 100 GbE link serving static content because the CPU never touches the payload — DMA does it all. nginx's `sendfile on;` is exactly this.

### 11.1 Working example: zero-copy file serving with os.sendfile

```python
"""
sendfile_serve.py — serve a file to a socket with zero-copy os.sendfile().

The payload bytes never enter the Python process address space: the kernel
streams page-cache pages straight to the socket (DMA to the NIC). This is the
nginx 'sendfile on;' / CDN technique.

POSIX only: os.sendfile is a Linux/BSD/macOS syscall; it does not exist on
Windows (which has its own TransmitFile). Run this on Linux or macOS.

Run server:  python sendfile_serve.py 8889 /path/to/largefile
Fetch:       nc 127.0.0.1 8889 > out  (then diff out largefile)
"""
from __future__ import annotations
import os
import socket
import sys


def serve_once(port: int, path: str) -> None:
    filesize = os.path.getsize(path)
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("0.0.0.0", port))
    lsock.listen(8)
    print(f"serving {path} ({filesize} bytes) via zero-copy sendfile on :{port}")

    conn, addr = lsock.accept()
    with conn, open(path, "rb") as f:
        offset = 0
        # os.sendfile(out_fd, in_fd, offset, count): the data goes
        # file -> page cache -> socket entirely inside the kernel.
        while offset < filesize:
            # Returns bytes sent this call; loop until the whole file is sent.
            sent = os.sendfile(conn.fileno(), f.fileno(), offset, filesize - offset)
            if sent == 0:
                break                      # EOF / peer closed
            offset += sent
        print(f"sent {offset} bytes to {addr} with zero user-space copies")
    lsock.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8889
    path = sys.argv[2] if len(sys.argv) > 2 else __file__
    serve_once(port, path)
```

**Contrast:** the naive version would be `conn.sendall(f.read())` — that `f.read()` pulls the whole file *into Python's heap* (a user-space copy), then `sendall` copies it back into the kernel socket buffer. `os.sendfile` skips both: the bytes stay in the page cache and DMA to the NIC.

---

## 12. Signal-driven I/O & buffering (the rest)

**Signal-driven I/O (`SIGIO`/`O_ASYNC`):** the kernel sends a signal when an fd becomes ready, so you don't poll. In practice it's **rarely used** for high-performance servers — signal delivery is expensive, signals can be coalesced/lost under load, and the async-signal-safety restrictions on handlers are severe. `epoll` superseded it. Worth knowing it exists; not worth building on.

**Buffering** sits *above* all the I/O models and is its own performance lever:

| Layer | Buffer | Controlled by |
|---|---|---|
| stdio (`FILE*`) | user-space buffer (line/full/unbuffered) | `setvbuf`; `fflush` empties it to the kernel |
| kernel | page cache / socket buffers | the OS; `fsync` for files |
| device | volatile write cache | FLUSH/FUA (see doc 05) |

Two classic bugs: (1) `fflush` is **not** `fsync` — flushing stdio only moves bytes from the user buffer into the kernel page cache; durability still needs `fsync`. (2) **Nagle's algorithm** (`TCP_NODELAY` to disable) buffers small TCP writes to coalesce them, adding latency to interactive/RPC traffic — turn it off for request/response protocols, leave it on for bulk.

---

## 13. Advanced: io_uring at the limit, accept-herd, and busy polling

### io_uring beyond the basics — toward C10M

The `epoll` model ([§7](#7-io-multiplexing-select--poll--epoll--kqueue)) still costs a
syscall per readiness check and per I/O. io_uring ([§10](#10-io_uring--the-modern-linux-async-io))
removes those; pushed to the limit it enables **millions** of connections/IOPS per
core:

- **SQPOLL** — a kernel thread polls the submission queue, so the app submits I/O
  **without any syscall** at all (pure shared-memory handoff). Trades a busy kernel
  thread for zero submission overhead — huge for high-IOPS storage/network.
- **Registered buffers & files** (`IORING_REGISTER_*`) — pre-pin buffers and fds once,
  skipping the per-I/O `get_user_pages`/fd-lookup cost.
- **Multishot** accept/recv — one submission yields *many* completions (accept a
  stream of connections, receive many datagrams) without re-arming.
- **Chained/linked SQEs** — express "read then write then close" as a dependency chain
  the kernel runs without round-tripping to userspace.

The cost: io_uring is **harder to reason about** (completion ordering, buffer
lifetime, security — several CVEs led some distros to gate it behind a sysctl). Use a
library (liburing, or a runtime's integration) rather than hand-rolling.

### The accept thundering herd — SO_REUSEPORT vs EPOLLEXCLUSIVE

When many workers/threads wait to `accept()` on one listening socket, a connection
arrival can wake *all* of them; all but one fail and go back to sleep — wasted wakeups
and a scalability ceiling ([scenarios 03.4](../enterprise_scenarios/03_concurrency_incidents.md)):

- **`EPOLLEXCLUSIVE`** — the kernel wakes only *one* waiter per event (fixes the herd
  for a shared listen fd).
- **`SO_REUSEPORT`** — each worker gets its **own** listen socket on the same port;
  the kernel load-balances incoming connections across them (a hash on the 4-tuple).
  This also removes the accept lock entirely and is how NGINX/Envoy scale accept
  across cores — and why a worker restart only drops *its* share of new connections.

### Busy polling — trading CPU for the lowest latency

For latency-critical paths (HFT, packet processing), even an interrupt + wakeup is too
slow. **Busy polling** (`SO_BUSY_POLL`, `epoll` busy-poll, or full kernel-bypass with
DPDK/AF_XDP, [Net 08](../comp_networking/08_network_performance_tuning.md)) spins on
the NIC queue instead of sleeping — eliminating interrupt and wakeup latency at the
cost of burning a core. The same trade-off as a spinlock vs mutex
([04 §3](04_concurrency_synchronization.md)): spend CPU to remove wait latency.

### eventfd / timerfd / signalfd — everything as a pollable fd

The Linux "everything is an fd" completion of the event-loop model: `eventfd`
(userspace wakeups / cross-thread notification), `timerfd` (timers as readable fds),
and `signalfd` (signals as a readable fd — the safe way to handle signals in an event
loop, [01 §11](01_processes_threads.md)). All integrate into one `epoll`/io_uring loop
so a server waits on I/O, timers, signals, and wakeups uniformly.

---

## Key Takeaways

1. **A syscall is a privileged user→kernel transition with real cost** (~100s of ns, worse with KPTI). At high IOPS, *syscalls per operation* is a first-order metric — the reason for `writev` batching, vDSO, and io_uring.
2. **DMA + interrupts** are how I/O completes without the CPU copying every byte; under extreme load, polling (and interrupt coalescing/NAPI) beats interrupts. DMA is *why* zero-copy is possible.
3. **Readiness ≠ completion.** `epoll`/`select`/`poll`/`kqueue` tell you an fd is *ready* (you still issue the I/O — reactor); `io_uring`/AIO *perform* the I/O and report *completion* (proactor). Only completion models do true async **disk** I/O.
4. **The C10K answer is the event loop:** one thread + `epoll` (O(ready), register fds once) + non-blocking sockets beats thread-per-connection (O(n) scans, MiB stacks, scheduler thrash).
5. **Edge-triggered epoll requires draining to `EAGAIN`;** level-triggered is forgiving. Always handle partial `send()`/`recv()` and `EAGAIN` as normal control flow, not errors.
6. **Never block the event loop.** Offload CPU/blocking work to a pool or shard across loop processes (nginx workers per core; Redis single-threaded data path). Go hides this with goroutines on an epoll runtime.
7. **io_uring** unifies async disk and network I/O via shared SQ/CQ rings, batches submissions into one (or zero, with SQPOLL) syscalls, and supports registered buffers/files and linked ops — at the cost of a larger kernel attack surface.
8. **Zero-copy (`sendfile`/`splice`/`mmap`)** removes user-space copies for file→socket transfers; with scatter-gather DMA the CPU never touches the payload — the technique behind CDN/nginx line-rate static serving. `fflush` is not `fsync`; disable Nagle for RPC.

> Read previous: [05 — File Systems & Storage](05_file_systems_storage.md) for the durability and page-cache mechanics these I/O models drive.
