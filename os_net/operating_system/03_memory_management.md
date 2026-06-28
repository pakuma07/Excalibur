# 03 — Memory Management

> **Audience:** staff/principal. You understand pointers and that "RAM is fast." This doc is about *how the OS turns a process's flat address space into physical pages*, where the latency actually goes (page faults, TLB misses, NUMA hops), and how the allocators and kernel knobs you tune in production actually behave under load.
>
> **Primary sources:** Drepper, *What Every Programmer Should Know About Memory* (2007); Tanenbaum & Bos, *Modern Operating Systems* (4e); Silberschatz, Galvin & Gagne, *Operating System Concepts* (10e); Kerrisk, *The Linux Programming Interface* (TLPI); Bovet & Cesati, *Understanding the Linux Kernel*; the Linux kernel `Documentation/admin-guide/mm/` and `Documentation/vm/` trees; the glibc/jemalloc/tcmalloc internals docs.

---

## 1. Why this matters at scale

Every memory access your code makes is a lie the hardware and OS conspire to tell. The pointer value in your register is a **virtual address**; the byte it names lives at some **physical address** the kernel chose, possibly in RAM, possibly swapped to disk, possibly not yet allocated at all. The machinery that maintains this illusion — page tables, the MMU, the TLB, the page-fault handler — is invisible until it dominates your latency, and then it dominates *completely*.

Three facts drive everything in this document:

1. **The memory hierarchy spans five orders of magnitude.** An L1 hit is ~1 ns; an L3 hit ~10 ns; a DRAM access ~100 ns; a remote-NUMA DRAM access ~150–300 ns; a minor page fault ~1 µs; a major fault (disk/swap) ~100 µs–10 ms. *Where your data lands in this hierarchy is decided by access pattern, not by how much RAM you bought.*
2. **Virtual memory is not free.** Every access goes through address translation. A TLB miss costs a page-table walk (up to 4 dependent memory loads on x86-64). A page fault costs a trap into the kernel. At scale these are not rounding errors — a poorly laid-out hot loop can spend the *majority* of its cycles stalled on translation and cache misses.
3. **The OS manages memory as a shared, oversubscribed resource.** Page cache, swap, the OOM killer, and cgroup limits decide who gets RAM and who gets killed. In a containerized fleet, *understanding these policies is the difference between a graceful degradation and a 3 a.m. pager.*

Staff engineers are expected to reason from the hardware and the page-table mechanics up — not to treat memory as an infinite flat array.

---

## 2. Physical vs virtual memory

| | Physical memory | Virtual memory |
|---|---|---|
| **What it is** | Actual DRAM cells, addressed by the memory controller | Per-process abstraction: a flat, contiguous-looking address space |
| **Size** | Bounded by installed RAM (e.g., 256 GiB) | Bounded by the architecture (x86-64: 48-bit canonical → 256 TiB user) |
| **Who sees it** | The kernel and the MMU | Every userspace pointer |
| **Sharing** | A physical page can back many virtual pages (shared libs, COW) | Each process has its own private mapping |
| **Allocation unit** | Page frame (4 KiB typical; 2 MiB / 1 GiB huge pages) | Page (same sizes) |

The key invariant: **virtual addresses are translated to physical addresses one page at a time** by the MMU, using per-process page tables the kernel maintains. This buys four things that make modern computing possible:

- **Isolation** — process A cannot name process B's physical pages.
- **Over-commit** — the sum of all virtual address spaces vastly exceeds physical RAM; pages are materialized lazily (demand paging) and reclaimed under pressure (swap).
- **Relocation** — a program is linked at fixed virtual addresses but can run anywhere in physical RAM; ASLR randomizes the virtual layout for security.
- **Sharing & COW** — `fork()` shares all pages copy-on-write; shared libraries map one physical copy into every process.

```
   Process A virtual                  Physical RAM                Process B virtual
  +----------------+                +-------------+              +----------------+
  | 0x5555_0000... |--------+       | frame 0x1A2 |<----+        | 0x5555_0000... |
  | (heap)         |        +------>|             |     |        | (heap)         |
  +----------------+                +-------------+     |        +----------------+
  | 0x7ffff7a0...  |--------+       | frame 0x0C7 |     +--------| 0x7ffff7a0...  |
  | (libc .text)   |        +------>| (shared RO) |<-------------| (libc .text)   |
  +----------------+                +-------------+              +----------------+
        (one physical copy of libc backs both processes)
```

---

## 3. The process address space layout

A Linux process's virtual address space (x86-64, classic layout) from low to high:

```
0xffff_ffff_ffff_ffff  +-------------------------+
                       |   kernel space          |  (not mapped into user PTEs)
0xffff_8000_0000_0000  +-------------------------+  <- canonical hole below here
                       :          ...            :
0x7fff_ffff_ffff       +-------------------------+
                       |   stack (grows DOWN)    |  <- %rsp; auto-extends on fault
                       |          |              |
                       |          v              |
                       +-------------------------+
                       |          ^              |
                       |   mmap region           |  <- shared libs, mmap(), THP,
                       |   (grows DOWN typically)|     malloc's large allocations
                       +-------------------------+
                       |          ^              |
                       |          |              |
                       |   heap (grows UP)       |  <- brk()/sbrk(); small malloc
                       +-------------------------+
                       |   .bss   (zero-init)    |  <- uninitialized globals
                       +-------------------------+
                       |   .data  (init globals) |
                       +-------------------------+
                       |   .text  (code, RO+X)   |  <- the program binary
0x0000_5555_5555_xxxx  +-------------------------+  (PIE base, ASLR-randomized)
0x0000_0000_0000_0000  +-------------------------+  (NULL page, unmapped -> SIGSEGV)
```

| Segment | Contents | Permissions | Growth |
|---|---|---|---|
| `.text` | machine code | r-x (RO, executable) | fixed |
| `.rodata` | string literals, consts | r-- | fixed |
| `.data` | initialized globals | rw- | fixed |
| `.bss` | zero-initialized globals | rw- | fixed (zero-fill, no file backing) |
| heap | `malloc` small allocations | rw- | up via `brk` |
| mmap | large `malloc`, shared libs, files | varies | dynamic |
| stack | locals, call frames, return addrs | rw- | down, auto-extended |

You can read a live process's map from `/proc/<pid>/maps`. Each line is a **VMA (virtual memory area)** — the kernel's `struct vm_area_struct`, a contiguous run of pages with uniform permissions and backing. The address space is *a list of VMAs*, not a single block; a page fault first looks up which VMA a faulting address falls in.

> **Practical:** `cat /proc/self/maps` from a shell shows the layout above for `cat` itself. `pmap -X <pid>` and `/proc/<pid>/smaps` break down RSS, PSS (proportional set size — your fair share of shared pages), and swap per VMA. PSS is the honest number for "how much RAM does this process really cost" in a shared system.

---

## 4. Paging and page tables

Virtual memory is divided into fixed-size **pages** (4 KiB on x86-64); physical RAM into equal-size **page frames**. The page table maps page → frame. A virtual address splits into a **page number** (high bits) and an **offset** (low 12 bits for 4 KiB pages); only the page number is translated, the offset is copied through.

A flat page table would be absurd: 48-bit address space / 4 KiB pages = 2^36 entries × 8 bytes = 512 GiB *per process*, mostly empty. The solution is a **multi-level (radix) page table** that only materializes the branches you use.

### 4.1 x86-64 four-level translation

x86-64 (without 5-level paging) walks four tables, 9 bits each, plus a 12-bit offset = 48 bits:

```
 Virtual address (48-bit canonical):
  47        39 38        30 29        21 20        12 11           0
 +-----------+-----------+-----------+-----------+-----------------+
 |  PML4 idx |  PDPT idx |   PD idx  |   PT idx  |   page offset   |
 +-----------+-----------+-----------+-----------+-----------------+
   9 bits      9 bits      9 bits      9 bits        12 bits

  CR3 ---> PML4 table ---> PDPT ---> PD ---> PT ---> physical frame
           (1 entry          ...                      + offset
            per 512GiB)                               = phys addr

  Each table = 512 entries x 8 bytes = one 4 KiB page.
  A full walk = 4 dependent memory loads (each may itself miss cache).
```

- **CR3** holds the physical address of the top-level (PML4) table; the kernel reloads it on every context switch (which is why context switches partially flush translation state).
- Each level's entry holds the physical frame number of the next table plus permission/status bits: **present (P)**, **read/write (R/W)**, **user/supervisor (U/S)**, **accessed (A)**, **dirty (D)**, **NX (no-execute)**.
- A "huge page" short-circuits the walk: a 2 MiB page terminates at the PD level (3 loads), a 1 GiB page at the PDPT level (2 loads) — fewer levels *and* fewer TLB entries to cover the same memory.

**The cost:** a cold translation is up to 4 dependent DRAM loads (~400 ns) before your *actual* load even starts. This is why the TLB exists.

---

## 5. The MMU, the TLB, and TLB shootdowns

The **MMU (Memory Management Unit)** is the hardware that performs translation on every access. To avoid walking the page table 4 levels deep every time, it caches recent translations in the **TLB (Translation Lookaside Buffer)** — a small, fully/highly-associative cache of page-number → frame-number entries.

```
   load [vaddr]
        |
        v
   +---------+   hit (~1 cycle)   +-----------------------+
   |  TLB    |------------------> | use cached frame, go  |
   +---------+                    +-----------------------+
        | miss
        v
   +------------------+  HW page walker (x86) or
   |  page-table walk |  SW handler (some RISC). 1-4 memory loads.
   +------------------+
        |
        v
   fill TLB entry, retry
```

- A modern core has separate L1 iTLB/dTLB (e.g., 64–128 entries each) and a shared L2 TLB (1k–2k entries). A 4 KiB page per entry means the L1 dTLB covers only ~256 KiB–512 KiB of working set. **Exceed that with random access and you eat a TLB miss on nearly every access** — this is the single biggest argument for huge pages (§9) in big-heap workloads.
- The TLB is tagged per address space on modern CPUs via **ASIDs / PCIDs** (process-context IDs) so a context switch need not fully flush it.

### 5.1 TLB shootdowns — the multicore tax

The TLB is **per-core**. When one core changes a page-table entry (unmaps a page, changes permissions, migrates a page during NUMA balancing), *every other core that might have cached the old translation must invalidate it*. The hardware does not do this for you on x86; the kernel must:

1. The initiating core updates the PTE.
2. It sends an **IPI (Inter-Processor Interrupt)** to all cores sharing that mm.
3. Each target core executes `invlpg` (or flushes the TLB) and acks.
4. The initiator waits for all acks before freeing the page.

This is a **TLB shootdown**, and it is a notorious scalability killer:

- `munmap`/`madvise(DONTNEED)`/`mprotect` on a multithreaded process with many cores can serialize on shootdown IPIs. Freeing a large mapping can stall *all* cores.
- Symptom in production: high `%sys` time, spikes in `TLB:` lines of `/proc/interrupts`, latency cliffs when thread count grows.
- Mitigations: batch unmaps; avoid `MADV_DONTNEED` churn (let the allocator retain memory — see jemalloc's `dirty_decay_ms`); use huge pages to reduce the number of mappings; pin threads to fewer NUMA nodes.

---

## 6. Page faults: minor, major, and the fault path

A **page fault** is a CPU trap raised when a translation cannot complete: the PTE is not present, or the access violates permissions. The kernel's `do_page_fault` handler decides what it means:

| Fault type | Cause | Cost (order of) | Counter |
|---|---|---|---|
| **Minor** | Page is in RAM but not mapped into *this* PTE yet: COW, demand-zero `.bss`, page already in page cache, shared-lib first touch | ~0.2–2 µs | `minflt` |
| **Major** | Page must be fetched from disk: swap-in, or first read of a file-backed `mmap` not in page cache | ~100 µs (SSD) – 10 ms (HDD) | `majflt` |
| **Invalid** | Address in no VMA, or permission violation (write to RO, exec NX) | → `SIGSEGV` / `SIGBUS` | — |

The minor-fault path for, e.g., a freshly `malloc`'d page on first write:

```
write to fresh heap page
   -> CPU: PTE not present -> trap (page fault)
      -> kernel: find VMA, it's anonymous & writable -> OK
         -> allocate a zeroed physical frame
            -> install PTE (present, rw)
               -> return to userspace, retry the store  (this all = "minor fault")
```

**This is why allocating memory is cheap but *touching* it is not.** `malloc(1 GiB)` returns instantly (it just `mmap`s an anonymous region — no frames yet). The cost is paid lazily, one minor fault per page, on first touch. Benchmarks that "allocate a big buffer" and forget to fault it in measure nothing. We demonstrate this cost directly in §15.

---

## 7. Demand paging and the COW path

**Demand paging:** pages are brought into RAM only when first accessed, not at `exec`/`malloc` time. The binary's `.text` is `mmap`'d from the executable file; the first instruction fetch faults it in from the page cache. `.bss` and fresh `malloc` memory are **demand-zero** — the kernel maps a shared, read-only zero page COW, and only allocates a real frame on the first *write*.

**Copy-on-write (`fork`)** is the same trick: after `fork`, parent and child share every page read-only; the first write by either triggers a minor fault that copies that one page. This makes `fork`+`exec` cheap (you don't copy a 4 GiB heap to immediately throw it away) — but it also means a `fork`-heavy server (or a Redis BGSAVE snapshot) can see a fault storm and a transient memory spike if the parent keeps writing while the child is alive.

> **Pitfall:** a multi-GiB JVM/CPython process that `fork`s and the child only `exec`s is fine; but a `fork`-based pre-fork web server where workers write all over the COW heap quietly duplicates pages until RSS balloons. `MADV_MERGEABLE`/KSM and careful "touch shared data before forking" help.

---

## 8. Page replacement algorithms

When physical RAM fills and the kernel needs a frame, it must **evict** a page (write it to swap if dirty/anonymous, or just drop it if it's clean file-backed cache). *Which* page to evict is the **page replacement** problem. The goal: evict the page least likely to be used soon (approximate the unimplementable optimal **Belady's MIN**, which evicts the page used furthest in the future).

### 8.1 The classic algorithms

| Algorithm | Rule | Pros | Cons |
|---|---|---|---|
| **OPT / Belady's MIN** | Evict the page used furthest in the future | Provably optimal, lowest fault count | Unimplementable (needs the future); used only as a benchmark |
| **FIFO** | Evict the oldest-loaded page | Trivial | Ignores usage; suffers **Belady's anomaly** |
| **LRU** | Evict the least-recently-used page | Excellent approximation of OPT for typical locality | Exact LRU needs a timestamp/list update on *every* access — too expensive in HW/kernel |
| **Clock (second-chance)** | Approximate LRU using the per-PTE **accessed** bit on a circular scan | Cheap, no per-access bookkeeping | Coarser than true LRU |
| **LRU-approx (Linux: two LRU lists, active/inactive)** | Pages start inactive; a second reference promotes to active; reclaim scans inactive first | Resists scan pollution; what Linux actually does | More complex; tuning via `swappiness` |

### 8.2 Belady's anomaly — why FIFO is dangerous

Intuitively, *more frames should never cause more faults*. For FIFO this is **false** — adding frames can *increase* fault count. The canonical reference string:

Reference string: `1 2 3 4 1 2 5 1 2 3 4 5`

```
FIFO with 3 frames:                          FIFO with 4 frames:
ref  frames        fault?                     ref  frames           fault?
 1   [1]            F                           1   [1]               F
 2   [1 2]          F                           2   [1 2]             F
 3   [1 2 3]        F                           3   [1 2 3]           F
 4   [2 3 4]        F (evict 1)                  4   [1 2 3 4]         F
 1   [3 4 1]        F (evict 2)                  1   [1 2 3 4]         hit
 2   [4 1 2]        F (evict 3)                  2   [1 2 3 4]         hit
 5   [1 2 5]        F (evict 4)                  5   [2 3 4 5]         F (evict 1)
 1   [1 2 5]        hit                          1   [3 4 5 1]         F (evict 2)
 2   [1 2 5]        hit                          2   [4 5 1 2]         F (evict 3)
 3   [2 5 3]        F (evict 1)                  3   [5 1 2 3]         F (evict 4)
 4   [5 3 4]        F (evict 2)                  4   [1 2 3 4]         F (evict 5)
 5   [3 4 5]        F (evict 5? -> 5 in) F       5   [2 3 4 5]         F (evict 1)
 ---------------------------------            -------------------------------
 9 faults with 3 frames                       10 faults with 4 frames  (!)
```

More frames, *more* faults. The lesson: FIFO does not have the **stack property** (the set of pages in N frames is not guaranteed to be a subset of the set in N+1 frames). LRU and OPT *do* have the stack property and are immune to Belady's anomaly. Real kernels use Clock/LRU-approx precisely to avoid this and to track usage cheaply via the hardware accessed bit.

A worked LRU/Clock simulator is in §15's companion; the takeaway for tuning is: **Linux uses an LRU-approximation (active/inactive lists driven by the accessed bit), tuned by `vm.swappiness`** (0 = avoid swapping anon pages, prefer dropping file cache; 100 = swap anon as readily as dropping cache).

---

## 9. Swapping, thrashing, and the working-set model

**Swapping** moves anonymous (non-file-backed) pages to a swap device to free RAM. It is the safety valve that makes over-commit safe — *until* the working set exceeds RAM.

**Thrashing** is the collapse mode: the active working set doesn't fit, so the kernel evicts a page that is immediately needed again, faults it back, evicts another needed page, and so on. CPU utilization drops toward zero while disk I/O saturates — the machine is "busy" doing nothing but paging.

```
 throughput
    ^
    |           _____________
    |          /             \      <- thrashing: WS > RAM, throughput collapses
    |         /               \
    |        /                 \
    |       /                   \___________
    +------------------------------------------> degree of multiprogramming
            (more processes = more demand for RAM)
```

### 9.1 The working-set model (Denning, 1968)

The **working set** `W(t, τ)` is the set of pages a process referenced in the last `τ` time units. Denning's insight: if the OS keeps each process's working set resident, the process makes progress; if it can't (sum of working sets > RAM), the system thrashes.

- **Policy:** a process should only run if its working set fits in RAM. If working sets don't all fit, *suspend* a process entirely (give its pages away) rather than let everyone thrash — counterintuitively, running fewer processes raises total throughput.
- **Modern echo:** this is exactly why cgroup memory limits and the OOM killer (§13) exist — bound each workload's footprint so one greedy job can't drive the box into a thrash spiral. It's also why "keep the working set in RAM" is the dominant performance rule for databases and caches.

> **Production signal:** thrashing shows as high `si`/`so` (swap-in/swap-out) in `vmstat`, `pgmajfault` climbing in `/proc/vmstat`, and high iowait with low useful CPU. The `PSI` (Pressure Stall Information) interface (`/proc/pressure/memory`) is the modern, precise signal: `some`/`full` avg10 memory pressure > a few percent means you're paging under stress.

---

## 10. Memory allocators: brk, mmap, and malloc internals

`malloc`/`free` are **userspace library** functions, not syscalls. They obtain memory from the kernel in bulk via two syscalls and then sub-allocate:

- **`brk`/`sbrk`** — moves the "program break", growing/shrinking the contiguous heap. Cheap for small, LIFO-ish growth; can't return memory to the OS unless it's at the top of the heap (a freed block in the middle just becomes reusable, not returned).
- **`mmap(MAP_ANONYMOUS)`** — maps a fresh region anywhere in the mmap area. Used for large allocations (glibc default: requests ≥ `M_MMAP_THRESHOLD`, 128 KiB). Can be `munmap`'d back to the OS individually.

### 10.1 What an allocator actually does

A general-purpose allocator must: satisfy variable-size requests fast, recycle freed memory, minimize **fragmentation**, and scale across threads. The hard part is fragmentation:

- **External fragmentation** — free memory exists but is split into chunks too small to satisfy a request.
- **Internal fragmentation** — a request is rounded up to a size class, wasting the slack.

### 10.2 The three production allocators

| Allocator | Design | Strengths | Where used |
|---|---|---|---|
| **ptmalloc2 (glibc)** | Per-thread + main **arenas**, bins by size (fast/small/large/unsorted), `brk` for main arena, `mmap` for big | Default everywhere; decent general case | The libc default |
| **tcmalloc (Google)** | **Thread-local caches** front a central heap; per-CPU caches in modern versions; size-class spans of pages | Very low contention, fast small allocs, good for many-threaded servers | Google, many C++ services |
| **jemalloc (FreeBSD/Facebook)** | Multiple **arenas** hashed by thread, per-thread `tcache`, fine size classes, decay-based purge (`dirty_decay_ms`) | Low fragmentation, excellent multithread scaling, great introspection (`malloc_stats_print`) | Redis (optional), Rust default-ish, Facebook |

**Arenas** are the key scaling idea: instead of one global heap behind a single lock (contention nightmare with N threads), give each thread (or CPU) its own arena/cache so the common path is lock-free or uncontended. The trade-off is **more fragmentation** (each arena holds its own free memory) — the classic space-vs-contention tension.

> **glibc arena gotcha:** glibc creates up to `8 × ncpu` arenas by default. On a 64-core box that's 512 arenas, each able to retain a chunk of memory → surprising RSS bloat in threaded programs. Tune with `MALLOC_ARENA_MAX=2` or switch to jemalloc/tcmalloc. This is one of the most common "why is my Java/Python C-extension process using so much RAM" answers in production.

### 10.3 Why freed memory often isn't returned to the OS

`free()` rarely calls `munmap`/`sbrk` down — it returns the chunk to the allocator's free lists for reuse. So RSS is "sticky": it tends to ratchet up to the high-water mark and stay there. This is *by design* (returning and re-faulting memory is expensive), but it surprises people watching RSS. jemalloc's decay purge and glibc's `malloc_trim()` can force return; in containers, set limits assuming the high-water mark, not the steady state.

---

## 11. Huge pages and Transparent Huge Pages (THP)

A 4 KiB page means the TLB covers a tiny working set and the page table is deep. **Huge pages** (2 MiB and 1 GiB on x86-64) fix both: one TLB entry covers 2 MiB, and the page-table walk is one level shorter.

| Mechanism | How | Use when |
|---|---|---|
| **HugeTLB (explicit)** | Reserve a pool (`vm.nr_hugepages`), allocate via `mmap(MAP_HUGETLB)` or `hugetlbfs` | Databases (Oracle, PostgreSQL `huge_pages=on`), big JVM/`-XX:+UseLargePages`, deterministic latency |
| **THP (transparent)** | Kernel auto-promotes 4 KiB runs to 2 MiB behind your back; `khugepaged` coalesces in background | General workloads; zero code change |

**The THP controversy:** THP's automatic promotion/demotion and the **`khugepaged`** background scanner can cause latency spikes, and the **memory compaction** needed to find a contiguous 2 MiB run can stall a thread (direct compaction) for milliseconds. Redis, MongoDB, and many databases *recommend disabling THP* (`/sys/kernel/mm/transparent_hugepage/enabled = never`) precisely because the unpredictable stalls hurt p99 more than the TLB savings help throughput. Explicit HugeTLB gives the benefit without the surprise.

> **Rule of thumb:** for big-heap, latency-sensitive services → explicit HugeTLB, THP off. For throughput-oriented batch work that touches huge contiguous arrays → THP `madvise` mode (`MADV_HUGEPAGE` only where you ask).

---

## 12. NUMA: local vs remote memory

On a multi-socket (or chiplet) server, RAM is partitioned into **NUMA nodes**, each attached to one socket. A core accessing its **local** node's RAM is fast (~100 ns); accessing a **remote** node's RAM crosses the interconnect (UPI/Infinity Fabric) and is ~1.5–2× slower with less bandwidth.

```
   +-------- Socket 0 --------+        +-------- Socket 1 --------+
   |  cores 0-15              |  UPI   |  cores 16-31             |
   |  +-------------------+   |<======>|  +-------------------+   |
   |  | local DRAM node 0 |   | remote |  | local DRAM node 1 |   |
   |  +-------------------+   | access |  +-------------------+   |
   +--------------------------+        +--------------------------+
      ~100 ns local                       ~150-300 ns remote (cross-socket)
```

- **First-touch policy:** Linux allocates a page on the NUMA node of the CPU that *first touches* it (not where it was `malloc`'d). So *the thread that initializes data determines its placement.* A common bug: one thread mallocs+zeros a big array, then worker threads on the other socket hammer it — all remote, half the bandwidth.
- **Tools:** `numactl --hardware` shows nodes and inter-node distances; `numactl --cpunodebind=0 --membind=0 ./app` pins a process; `numastat` shows local vs remote hit counts; `move_pages`/`mbind` and `set_mempolicy` are the syscall interface.
- **AutoNUMA** (kernel NUMA balancing) migrates pages toward the accessing thread automatically — but migration itself costs TLB shootdowns and page copies, so latency-critical apps often disable it (`numa_balancing=0`) and pin explicitly.

> **Production rule:** pin threads and their memory to the same node (`numactl`, or `NUMA`-aware allocators), and **first-touch data from the thread that will use it.** For a sharded service, run one process per NUMA node rather than one process spanning sockets.

---

## 13. The page cache, OOM killer, and cgroup limits

### 13.1 The page cache

The kernel caches file contents in otherwise-free RAM as the **page cache**. Reads check it first (a hit avoids disk entirely); writes go to dirty pages flushed later by `pdflush`/writeback. This is why "free" memory on a busy Linux box is usually near zero — it's all page cache, and that's *good*: `free -m`'s "available" column, not "free", is what matters. The page cache is reclaimed instantly under pressure (clean pages cost nothing to drop).

### 13.2 The OOM killer

When the kernel cannot reclaim enough memory to satisfy an allocation (and swap is exhausted or absent), it invokes the **OOM killer** rather than failing the allocation (because over-commit means the allocation already "succeeded"). It scores processes by `oom_score` (roughly proportional to RSS, adjustable via `oom_score_adj`) and kills the highest scorer, logging `Out of memory: Killed process ...` to `dmesg`.

- A process can protect itself with `oom_score_adj = -1000` (never kill) — used for critical daemons (sshd, the container runtime).
- `vm.overcommit_memory` controls the over-commit policy: `0` (heuristic, default), `1` (always allow — for workloads that sparse-allocate huge mappings), `2` (strict, never over-commit beyond `swap + ratio·RAM`).

### 13.3 cgroup v2 memory control — the container reality

In a container, memory is bounded by **cgroup v2** controllers, not the host's total RAM:

| Knob | Meaning |
|---|---|
| `memory.max` | Hard limit. Exceeding it triggers reclaim, then **cgroup OOM kill** *within the cgroup* |
| `memory.high` | Soft limit. Above it the kernel throttles the cgroup (aggressive reclaim, stalls) but doesn't kill |
| `memory.min` / `memory.low` | Protected memory the kernel won't reclaim under pressure |
| `memory.swap.max` | Per-cgroup swap cap |
| `memory.pressure` | PSI for this cgroup — the precise "am I memory-starved" signal |

The crucial gotcha: **a containerized OOM kill is invisible to the host's `free`/`top`** — the host has plenty of RAM, but the cgroup hit `memory.max`. You diagnose it via `dmesg` (`Memory cgroup out of memory`), `memory.events` (`oom_kill` count), or your orchestrator marking the pod `OOMKilled` (exit 137). JVMs/CPython runtimes that read the *host's* RAM instead of the cgroup limit will set heaps too large and get killed — set `-XX:MaxRAMPercentage` / use container-aware runtimes. We demonstrate cgroup OOM in §15.

---

## 14. mmap vs read/write, and memory ordering basics

### 14.1 mmap vs read/write

| | `read`/`write` | `mmap` |
|---|---|---|
| **Mechanism** | syscall copies bytes between kernel page cache and your buffer | maps file pages directly into your address space; access faults them in |
| **Copies** | one extra copy (page cache → user buffer) | zero-copy (you touch page-cache pages directly) |
| **Best for** | streaming, sequential, one-pass I/O | random access, repeated access, sharing between processes |
| **Cost** | syscall overhead per call | page-fault overhead on first touch; TLB pressure; `SIGBUS` if the file shrinks under you |
| **Sharing** | no | `MAP_SHARED` gives true shared memory across processes |

`mmap` is the basis of **shared memory** (`MAP_SHARED` on a file or `shm_open` object) — the lowest-latency IPC, since two processes literally share physical frames. We build a producer/consumer over shared memory in §15.

### 14.2 Memory ordering (preview of doc 04)

The CPU and compiler reorder memory operations for performance. On multicore, a store by core A may become visible to core B out of program order. This is invisible to single-threaded code but is *the* source of concurrency bugs. The fix is **memory barriers / fences** and the language memory model (C11 `atomic`, acquire/release). This is the bridge to [04 — Concurrency & Synchronization](04_concurrency_synchronization.md), which covers it in depth; here, just internalize: **on multicore, "the page is updated" and "every other core sees the update" are different events separated by cache-coherence traffic and possibly a fence.**

---

## 15. Working code — measuring what this document claims

### 15.1 The cost of page faults and the effect of access pattern on TLB/cache

This C program demonstrates three things at once: (1) faulting in memory is far slower than allocating it; (2) sequential access is dramatically faster than random access on the *same* data because of TLB and cache locality; (3) the gap widens as the working set exceeds cache/TLB reach.

```c
/* mem_access.c — measure page-fault cost and sequential vs random access.
 * Build:  cc -O2 -o mem_access mem_access.c
 * Run:    ./mem_access            (try sizes that exceed your L2/L3 and TLB reach)
 * On Linux you can also run under `perf stat -e dTLB-load-misses,LLC-load-misses`. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

static double now_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv) {
    size_t mb = (argc > 1) ? (size_t)atoll(argv[1]) : 512;
    size_t n  = mb * 1024 * 1024 / sizeof(uint64_t);   /* elements */
    uint64_t *a = malloc(n * sizeof(uint64_t));
    if (!a) { perror("malloc"); return 1; }

    /* (1) malloc is cheap; the FIRST touch (fault-in) is the real cost. */
    double t0 = now_sec();
    memset(a, 0, n * sizeof(uint64_t));   /* one minor fault per 4KiB page */
    double t_fault = now_sec() - t0;
    printf("fault-in %zu MiB: %.3f s  (%.1f ns/page)\n",
           mb, t_fault, t_fault * 1e9 / (mb * 1024.0 / 4.0));

    /* Build a random permutation of indices to defeat prefetch & TLB. */
    size_t *idx = malloc(n * sizeof(size_t));
    for (size_t i = 0; i < n; i++) idx[i] = i;
    for (size_t i = n - 1; i > 0; i--) {     /* Fisher-Yates */
        size_t j = (size_t)((double)rand() / RAND_MAX * i);
        size_t t = idx[i]; idx[i] = idx[j]; idx[j] = t;
    }

    volatile uint64_t sink = 0;

    /* (2) Sequential access: cache lines & TLB entries reused, HW prefetch wins. */
    t0 = now_sec();
    for (size_t pass = 0; pass < 4; pass++)
        for (size_t i = 0; i < n; i++) sink += a[i];
    double t_seq = now_sec() - t0;

    /* (3) Random access: each touch likely a new cache line AND new page. */
    t0 = now_sec();
    for (size_t pass = 0; pass < 4; pass++)
        for (size_t i = 0; i < n; i++) sink += a[idx[i]];
    double t_rand = now_sec() - t0;

    double ops = 4.0 * n;
    printf("sequential: %.3f s  (%.1f ns/access)\n", t_seq,  t_seq  * 1e9 / ops);
    printf("random:     %.3f s  (%.1f ns/access)\n", t_rand, t_rand * 1e9 / ops);
    printf("random is %.1fx slower than sequential\n", t_rand / t_seq);

    free(idx); free(a);
    (void)sink;
    return 0;
}
```

**What you observe:** fault-in runs at ~hundreds of ns *per page* (a minor fault each). Sequential access runs at a few ns/element (prefetch + cache + TLB reuse). Random access over a buffer larger than L3 and TLB reach is commonly **10–50× slower** — pure TLB-miss + cache-miss latency, with nothing the CPU can hide. This is Drepper's central lesson made measurable: *layout and access pattern, not allocation size, decide performance.*

### 15.2 LRU vs FIFO page-replacement simulator (and Belady's anomaly)

A small, exact simulator you can use to reproduce §8's worked examples and explore your own reference strings.

```python
"""page_replace.py — exact FIFO / LRU / OPT page-replacement simulators.
Reproduces Belady's anomaly and compares fault counts. Run: python page_replace.py"""
from collections import deque, OrderedDict


def fifo(refs, frames):
    mem, q, faults = set(), deque(), 0
    for p in refs:
        if p not in mem:
            faults += 1
            if len(mem) >= frames:
                victim = q.popleft(); mem.discard(victim)
            mem.add(p); q.append(p)
    return faults


def lru(refs, frames):
    mem, faults = OrderedDict(), 0          # ordered by recency, oldest first
    for p in refs:
        if p in mem:
            mem.move_to_end(p)              # mark most-recently-used
        else:
            faults += 1
            if len(mem) >= frames:
                mem.popitem(last=False)     # evict least-recently-used
            mem[p] = True
    return faults


def opt(refs, frames):
    """Belady's MIN: evict the page whose next use is furthest in the future."""
    mem, faults = [], 0
    for i, p in enumerate(refs):
        if p in mem:
            continue
        faults += 1
        if len(mem) >= frames:
            # choose victim = page reused furthest ahead (or never)
            def next_use(pg):
                try:
                    return refs[i + 1:].index(pg)
                except ValueError:
                    return float("inf")
            victim = max(mem, key=next_use)
            mem.remove(victim)
        mem.append(p)
    return faults


if __name__ == "__main__":
    refs = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]   # the classic Belady string
    print("reference string:", refs)
    for f in (3, 4):
        print(f"  frames={f}: FIFO={fifo(refs, f):2d}  "
              f"LRU={lru(refs, f):2d}  OPT={opt(refs, f):2d}")

    # Belady's anomaly: FIFO faults INCREASE going 3 -> 4 frames.
    f3, f4 = fifo(refs, 3), fifo(refs, 4)
    print(f"\nBelady's anomaly (FIFO): {f3} faults @3 frames -> "
          f"{f4} faults @4 frames  => {'ANOMALY' if f4 > f3 else 'none'}")

    # LRU and OPT have the stack property: more frames never increases faults.
    assert lru(refs, 4) <= lru(refs, 3), "LRU must be monotonic"
    assert opt(refs, 4) <= opt(refs, 3), "OPT must be monotonic"
    print("LRU/OPT monotonic in frame count (stack property): OK")
```

### 15.3 mmap-based shared memory between processes

A parent and child share an anonymous `MAP_SHARED` region — the lowest-latency IPC there is, because both processes touch the *same physical frames*. The child increments a shared counter and signals the parent through shared memory.

```python
"""shm_ipc.py — shared memory between parent and child via mmap(MAP_SHARED).
Demonstrates true zero-copy IPC: both processes write the same physical pages.
Run: python shm_ipc.py   (POSIX / Linux / macOS)"""
import mmap
import os
import struct
import time

# Anonymous shared mapping: 4 KiB, shared across fork() because of MAP_SHARED.
SIZE = mmap.PAGESIZE
shm = mmap.mmap(-1, SIZE, flags=mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)

# Layout: [0:8] = counter (uint64), [8:9] = "done" flag.
def write_counter(buf, value):
    buf[0:8] = struct.pack("<Q", value)

def read_counter(buf):
    return struct.unpack("<Q", buf[0:8])[0]

write_counter(shm, 0)
shm[8] = 0

pid = os.fork()
if pid == 0:
    # ---- child: producer ----
    for i in range(1, 1_000_001):
        write_counter(shm, i)          # writes a SHARED physical page
    shm[8] = 1                          # signal completion
    os._exit(0)
else:
    # ---- parent: consumer ----
    while shm[8] == 0:
        time.sleep(0.001)               # spin-wait on the shared flag
    final = read_counter(shm)
    os.waitpid(pid, 0)
    print(f"child counted to {final} in shared memory")
    assert final == 1_000_000, "parent must see the child's writes (same frames)"
    print("parent observed child's writes via shared physical pages: OK")
    shm.close()
```

This is exactly how high-performance IPC (Redis with a forked BGSAVE child reading the COW heap, PostgreSQL shared buffers, Chrome's shared-memory transports) avoids copying — and why understanding which pages are *shared* vs *private* (COW) is essential to reading `/proc/<pid>/smaps` correctly (PSS divides shared pages across sharers).

### 15.4 Observing a cgroup memory limit and OOM (Linux, cgroup v2)

A shell recipe (run as root on a cgroup-v2 Linux host) that creates a 64 MiB-limited cgroup and watches a memory hog get OOM-killed *inside the cgroup* while the host has plenty of RAM:

```text
# Create a cgroup limited to 64 MiB (cgroup v2; requires root)
mkdir -p /sys/fs/cgroup/demo
echo "64M" > /sys/fs/cgroup/demo/memory.max
echo "0"   > /sys/fs/cgroup/demo/memory.swap.max   # no swap escape hatch

# Put a shell into it, then run a hog that touches 256 MiB
echo $$ > /sys/fs/cgroup/demo/cgroup.procs
python3 -c "b = bytearray(256*1024*1024); print('touched all pages')"
#   -> "Killed"  (the bytearray() faults in pages past memory.max)

# Confirm it was a CGROUP oom, not a host oom:
cat /sys/fs/cgroup/demo/memory.events     # oom_kill 1
dmesg | tail                              # "Memory cgroup out of memory: Killed process ..."
free -m                                   # host still shows plenty free!
```

The lesson made concrete: the kernel killed the process because *the cgroup* hit `memory.max`, not because the box ran out of RAM. This is exactly the Kubernetes `OOMKilled` / exit-137 path, and why your container memory request/limit math must account for the allocator high-water mark (§10.3), the runtime heap, *and* page-cache that counts against the cgroup.

---

## 16. Advanced: cgroup v2 memory control, PSI, and madvise

### memory.high vs memory.max — throttle vs kill

cgroup v2 splits memory control into two thresholds, and confusing them causes
incidents ([scenarios 01.5](../enterprise_scenarios/01_cpu_memory_incidents.md)):

| Knob | Behavior |
|---|---|
| `memory.max` | **Hard limit.** Exceed it → the cgroup OOM killer fires. The wall. |
| `memory.high` | **Soft limit.** Above it the kernel *throttles* the cgroup and reclaims aggressively — the process slows (allocation stalls) instead of dying. |
| `memory.low`/`memory.min` | Reclaim *protection* — memory the kernel won't reclaim under pressure. |

Setting `memory.high` slightly below `memory.max` gives a **graceful degradation
band**: the workload is throttled (and you get a PSI signal) before the OOM killer is
reached — far better than a hard kill. `memory.events` (`high`, `max`, `oom_kill`)
counts each event; watch with
[`examples/cgroup_throttle_watch.py`](examples/README.md).

### PSI — the pressure-stall information interface

`/proc/pressure/memory` (and the per-cgroup `memory.pressure`) report **time lost to
memory stalls**, split into `some` (at least one task stalled) and `full` (all tasks
stalled). This is the single best "am I memory-bound?" signal — it measures the
*stall*, not just utilization, so it catches reclaim/thrash that a `free`-based alert
misses. Alert on `full avg10` for memory and I/O; it's the modern replacement for
guessing from utilization. (`psi_watcher.py` demonstrates parsing and alerting.)

### madvise & friends — telling the kernel your access pattern

The kernel guesses your memory behavior; `madvise(2)` lets you tell it:

- `MADV_DONTNEED` — drop pages now (free them; next touch faults in zeros/from file).
- `MADV_FREE` — lazy free: pages *may* be reclaimed under pressure but reused cheaply
  if you touch them again (the modern allocator default for returning memory — RSS
  may stay high until pressure, which looks like a "leak" but isn't,
  [scenarios 01.6](../enterprise_scenarios/01_cpu_memory_incidents.md)).
- `MADV_HUGEPAGE` / `MADV_NOHUGEPAGE` — opt in/out of THP per region (the right way to
  use huge pages without global `always`, [§11](#11-huge-pages-and-transparent-huge-pages-thp)).
- `MADV_SEQUENTIAL`/`MADV_RANDOM` — tune read-ahead.
- `MAP_POPULATE` (mmap) — pre-fault now to avoid minor-fault latency on the hot path.

### KSM and userfaultfd

**KSM** (Kernel Same-page Merging) dedups identical pages across processes/VMs — big
memory savings for many similar VMs, at CPU cost and a side-channel risk.
**`userfaultfd`** lets *userspace* handle page faults — the mechanism behind live
migration (post-copy), CRIU checkpoint/restore, and userspace-managed memory.

---

## 17. Trade-offs summary

- **Virtual memory buys isolation, over-commit, and sharing — but every access pays translation.** The TLB and page cache are what make it affordable; exceeding their reach is the latency cliff.
- **Allocation is cheap; touching is not.** Cost is paid lazily via minor faults. Big buffers that are never faulted in measure nothing.
- **Access pattern beats allocation size.** Sequential reuses cache lines + TLB entries; random over a large buffer is bounded by miss latency the CPU can't hide.
- **FIFO suffers Belady's anomaly; LRU/Clock/LRU-approx don't.** Linux uses LRU-approx (active/inactive + accessed bit), tuned by `swappiness`.
- **Thrashing = working set > RAM.** The fix is to bound footprints (cgroups) and keep the working set resident, not to add multiprogramming.
- **Allocators trade fragmentation for thread scalability via arenas/caches.** glibc's default arena count bloats threaded-process RSS; jemalloc/tcmalloc and `MALLOC_ARENA_MAX` are the usual fixes. Freed memory is sticky by design.
- **Huge pages cut TLB misses and walk depth; THP can cause stalls** — prefer explicit HugeTLB for latency-sensitive big-heap services.
- **NUMA: first-touch decides placement.** Pin threads + memory to a node and initialize data where it'll be used.
- **In containers, the cgroup limit — not host RAM — defines OOM.** OOM kills can be invisible to the host; size limits to the allocator high-water mark.

## 18. Key Takeaways

1. A pointer is a **virtual address**; the MMU translates it through a **multi-level page table**, caching results in the **TLB**. A cold translation is up to 4 dependent DRAM loads — the TLB is what hides that, and exceeding its reach (random access over a big heap) is a primary latency source.
2. **Page faults** are the heartbeat of demand paging: minor (~µs, remap/COW/zero-fill) vs major (~ms, disk/swap). `malloc` is free; the per-page minor fault on first touch is where the time goes.
3. **Page replacement** approximates Belady's optimal; FIFO's lack of the stack property causes **Belady's anomaly**, so real kernels use **Clock / LRU-approx** driven by the hardware accessed bit.
4. **Thrashing** is working-set-exceeds-RAM collapse; the working-set model and modern **cgroup limits + PSI** are the tools to prevent and detect it.
5. **Allocators** (ptmalloc/tcmalloc/jemalloc) sub-allocate kernel memory (`brk`/`mmap`) and trade **fragmentation for thread scalability** via arenas/caches; RSS is sticky by design.
6. **Huge pages** cut TLB pressure and walk depth; **THP** can cause unpredictable compaction stalls — explicit HugeTLB for latency-critical paths.
7. **NUMA first-touch** placement and **TLB shootdown** IPI cost are the two multicore-memory effects that silently cap scalability.
8. In production, **the cgroup memory limit defines OOM**, not host RAM — a fact that explains most container `OOMKilled` mysteries.

> Read next: [04 — Concurrency & Synchronization](04_concurrency_synchronization.md) — once multiple cores share these pages, *when* a write becomes visible to another core (memory ordering) becomes the central correctness problem.
