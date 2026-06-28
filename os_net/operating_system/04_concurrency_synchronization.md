# 04 — Concurrency & Synchronization

> **Audience:** staff/principal. You've written threaded code and seen it break in ways you couldn't reproduce. This doc is about *why* it broke — the hardware memory model underneath your mutex, the precise guarantees of each primitive, and how to build correct lock-free structures when a mutex is too slow.
>
> **Primary sources:** Herlihy & Shavit, *The Art of Multiprocessor Programming* (2e); McKenney, *Is Parallel Programming Hard, And, If So, What Can You Do About It?* (perfbook); Tanenbaum & Bos, *Modern Operating Systems* (4e); Silberschatz, Galvin & Gagne, *Operating System Concepts* (10e); Kerrisk, *The Linux Programming Interface* (TLPI, ch. 30–53); Dijkstra's classic notes (semaphores, dining philosophers); the C11/C++11 standard memory model (`§5.1.2.4`, `<stdatomic.h>`); the Linux `futex(2)` man page and the kernel RCU documentation.

---

## 1. Why this matters at scale

A single-threaded program executes one statement after another; its behavior is a function of its inputs. Add a second thread sharing memory and *that stops being true*. The program's behavior now depends on the **interleaving** chosen by the scheduler and on the **memory ordering** chosen by the CPU and compiler — neither of which you control, and both of which vary run to run. The result is the defining hazard of systems programming: bugs that are rare, non-deterministic, vanish under a debugger, and corrupt data silently.

Three facts drive this document:

1. **Atomicity is not the default.** `counter++` is three operations (load, add, store). Two threads can interleave them and lose an update. *Nothing* is atomic unless the hardware or a primitive makes it so.
2. **Memory operations are reordered.** The compiler reorders for optimization; the CPU reorders for pipelining and store buffering. A store by core A can become visible to core B in a different order than the program wrote it. Your mutex works *because* it issues the right memory barriers — not because the code "looks sequential."
3. **Synchronization has a cost, and the cost is contention.** A mutex under contention is a context switch (~µs). A cache line bounced between cores (false sharing) costs ~100 ns per bounce. A lock held across I/O serializes your whole service. At scale, the *granularity* and *placement* of synchronization decides throughput more than the algorithm does.

Staff engineers are expected to reason about correctness from the memory model up, and about performance from cache coherence and contention — not to sprinkle locks until the test passes.

---

## 2. Race conditions, critical sections, and atomicity

A **race condition** exists when the correctness of a computation depends on the relative timing of operations on shared state. The canonical example is the lost update:

```
   Shared: counter = 0
   Thread A: counter++            Thread B: counter++
   ----------------------------------------------------
   A: load counter (0)
                                  B: load counter (0)
   A: add 1 -> 1
                                  B: add 1 -> 1
   A: store 1
                                  B: store 1            <- one increment lost
   Final: counter == 1   (should be 2)
```

A **critical section** is a region of code that accesses shared state and must not be executed by more than one thread at a time. The job of synchronization is **mutual exclusion** over critical sections, plus the guarantee that updates become *visible* to other threads (memory ordering, §10).

An operation is **atomic** if it is indivisible: it either happens completely or not at all, with no observable intermediate state. Single aligned word loads/stores are atomic on x86-64; read-modify-write (`++`, `+=`, compare-and-swap) is *not* unless you use an atomic instruction (`lock`-prefixed on x86) or a lock.

The four requirements for a correct critical-section solution (Silberschatz):
1. **Mutual exclusion** — at most one thread in the critical section.
2. **Progress** — if no thread is in the section, one wanting in gets in (no needless blocking).
3. **Bounded waiting** — a thread can't be starved forever while others repeatedly enter.
4. **No assumptions** about relative thread speeds or CPU count.

---

## 3. Mutexes vs spinlocks — and when each

Both provide mutual exclusion; they differ in *what a waiting thread does*.

| | Spinlock | Mutex (blocking lock) |
|---|---|---|
| **Waiting thread** | Busy-loops on the lock (burns CPU) | Sleeps; kernel reschedules it; woken on release |
| **Cost to acquire (uncontended)** | One atomic (CAS/`xchg`) — nanoseconds | One atomic on the fast path (futex), else a syscall |
| **Cost when contended** | Wasted CPU cycles while spinning | Context switch (~1–5 µs) in and out |
| **Hold time it suits** | Very short (a few instructions), no sleeping inside | Anything, including long sections and I/O |
| **Where used** | Kernel (interrupt context can't sleep), tight hot paths | Userspace application code, anything that may block |
| **Danger** | Spinning while the holder is descheduled = pure waste; deadlock if holder can't be scheduled | Priority inversion; convoy effects |

**Rule:** spin only when the expected wait is shorter than the cost of a context switch *and* the holder is genuinely running on another core. In userspace you almost never want a raw spinlock — you want a mutex (which, in practice, *adaptively* spins briefly before sleeping; glibc's `PTHREAD_MUTEX_ADAPTIVE_NP` and the kernel's futex do exactly this). Spinlocks belong in the kernel and in lock-free fast paths.

> **Why kernels use spinlocks:** code in interrupt context cannot sleep (there's no process to reschedule), so a blocking mutex is illegal there. Linux uses spinlocks for short critical sections reachable from interrupt context, and sleeping mutexes (`struct mutex`) elsewhere.

---

## 4. Semaphores, condition variables, monitors

### 4.1 Semaphores (Dijkstra, 1965)

A **semaphore** is an integer with two atomic operations: `wait`/`P` (decrement; block if it would go negative) and `signal`/`V` (increment; wake a waiter). Two uses:

- **Counting semaphore** — models a pool of N identical resources (e.g., "5 DB connections available"). `P` to take one, `V` to return one.
- **Binary semaphore** (0/1) — acts like a lock, *but* unlike a mutex it has **no ownership**: any thread can `V`, which makes it suitable for *signaling between threads* (producer signals consumer) where the signaler isn't the holder.

### 4.2 Condition variables

A **condition variable (CV)** lets a thread wait for a *predicate* to become true while holding a mutex, releasing the mutex atomically as it sleeps. The contract:

```
   acquire(mutex)
   while (!predicate)          # MUST be a while loop, not an if
       cond_wait(cv, mutex)    # atomically: release mutex + sleep; on wake, re-acquire
   ... predicate is now true; act on it ...
   release(mutex)
```

Two rules people get wrong:
- **Always re-check the predicate in a `while` loop**, never an `if`. CVs permit **spurious wakeups** (POSIX explicitly allows them), and even without them, a different thread may have falsified the predicate between the signal and your wakeup (the "lost/stolen wakeup" / Mesa-semantics problem).
- **Signal while holding (or having held) the lock that guards the predicate.** Signaling without the lock risks a lost wakeup: the waiter checks the predicate, finds it false, and the signal arrives in the window *before* it sleeps — gone forever.

### 4.3 Monitors

A **monitor** (Hoare/Brinch Hansen) is a language-level construct bundling shared data + the mutex + condition variables so that mutual exclusion is *automatic* on entry to any method. Java's `synchronized` + `wait`/`notify` and Python's `threading.Condition` are monitors. Two signaling semantics matter:

- **Hoare semantics:** the signaled thread runs *immediately* and the signaler blocks. Strong guarantee, harder to implement.
- **Mesa semantics:** the signaled thread is merely made *runnable*; it competes for the lock later. Almost all real systems are Mesa — which is *exactly why you must re-check the predicate in a `while`.*

---

## 5. Read-write locks

A **read-write lock** allows either *many concurrent readers* or *one exclusive writer*. It pays off when reads vastly outnumber writes and critical sections are non-trivial.

| Mode | Concurrency |
|---|---|
| Read (shared) | Many readers simultaneously |
| Write (exclusive) | One writer, no readers |

The trade-offs:
- **Reader vs writer fairness:** a naive reader-preferring lock **starves writers** under continuous read load. A writer-preferring lock can starve readers. Production locks (`pthread_rwlock` with `PTHREAD_RWLOCK_PREFER_WRITER_NONRECURSIVE_NP`, Java `ReentrantReadWriteLock` in fair mode) pick a policy explicitly.
- **They're often slower than you think:** the rwlock itself has shared mutable state (the reader count) that every reader must atomically update — so on a many-core box, a read-heavy rwlock can become a *false-sharing* bottleneck on that counter. For very read-mostly data, **RCU (§14)** or per-CPU counters beat an rwlock decisively.

---

## 6. The classic problems (with working code)

### 6.1 Producer–consumer (bounded buffer)

The archetype: producers add items, consumers remove them, sharing a fixed-size buffer. Producers block when full; consumers block when empty. This is every work queue, every channel, every thread pool's task queue. A correct bounded queue with condition variables:

```python
"""bounded_queue.py — a correct bounded producer-consumer queue with CVs.
Demonstrates: while-loop predicate re-check (Mesa semantics), separate
not_full / not_empty conditions, graceful shutdown. Run: python bounded_queue.py"""
import threading
import time
import random


class BoundedQueue:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._buf: list = []
        self._lock = threading.Lock()
        # Two conditions sharing one lock: producers wait on not_full,
        # consumers on not_empty. Sharing the lock keeps the invariant atomic.
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)
        self._closed = False

    def put(self, item) -> None:
        with self._not_full:
            while len(self._buf) >= self.capacity and not self._closed:
                self._not_full.wait()          # release lock + sleep; re-check on wake
            if self._closed:
                raise RuntimeError("put on closed queue")
            self._buf.append(item)
            self._not_empty.notify()           # wake one consumer

    def get(self):
        with self._not_empty:
            while not self._buf and not self._closed:
                self._not_empty.wait()
            if not self._buf and self._closed:
                return None                    # sentinel: drained + closed
            item = self._buf.pop(0)
            self._not_full.notify()            # wake one producer
            return item

    def close(self):
        with self._lock:
            self._closed = True
            self._not_empty.notify_all()       # wake everyone to observe close
            self._not_full.notify_all()


def demo():
    q = BoundedQueue(capacity=8)
    produced, consumed = [], []
    n_items, n_producers, n_consumers = 200, 3, 4

    counter_lock = threading.Lock()
    next_id = [0]

    def producer():
        while True:
            with counter_lock:
                if next_id[0] >= n_items:
                    return
                item = next_id[0]; next_id[0] += 1
            q.put(item)
            produced.append(item)
            time.sleep(random.uniform(0, 0.001))

    def consumer():
        while True:
            item = q.get()
            if item is None:
                return
            consumed.append(item)
            time.sleep(random.uniform(0, 0.002))

    prods = [threading.Thread(target=producer) for _ in range(n_producers)]
    cons = [threading.Thread(target=consumer) for _ in range(n_consumers)]
    for t in prods + cons:
        t.start()
    for t in prods:
        t.join()
    q.close()                                  # no more items will be produced
    for t in cons:
        t.join()

    assert sorted(consumed) == list(range(n_items)), "every item consumed exactly once"
    print(f"produced {len(produced)}, consumed {len(consumed)} items, no loss/dup: OK")


if __name__ == "__main__":
    demo()
```

Note this is the *correct* pattern even though Python's GIL (§15) serializes the bytecode — the CV discipline is identical in C with `pthread_cond_t`, where the GIL doesn't save you.

### 6.2 Readers–writers

Many readers OR one writer (see §5). The hazard is starvation; the fix is a fairness policy. In practice, prefer `pthread_rwlock`/`ReentrantReadWriteLock` over hand-rolling — the correctness traps (reader-count races, upgrade deadlock) are subtle.

### 6.3 Dining philosophers (Dijkstra) — deadlock in miniature

Five philosophers, five forks, each needs both neighbors' forks to eat. The naive "pick up left, then right" deadlocks: all grab left simultaneously, all wait forever for right. This is the textbook illustration of the four Coffman conditions (§7), and the standard fixes map directly onto deadlock-prevention strategies:

- **Resource ordering** — number the forks; always pick up the lower-numbered first. Breaks *circular wait*. (Shown in §11.)
- **Limit concurrency** — allow at most 4 philosophers to try at once (a counting semaphore). Guarantees one can always get both forks.
- **Asymmetry** — odd philosophers pick left-then-right, even pick right-then-left.

---

## 7. Deadlock: the four Coffman conditions

A **deadlock** is a set of threads each blocked waiting for a resource held by another in the set — none can proceed. Coffman (1971) proved that deadlock requires **all four** of these conditions simultaneously; break any one and deadlock is impossible:

| Condition | Meaning | How to break it |
|---|---|---|
| **Mutual exclusion** | Resources are non-shareable (a lock is held by one thread) | Make resources shareable (rarely possible for locks) |
| **Hold and wait** | A thread holds resources while requesting more | Acquire all locks at once, or release all before requesting |
| **No preemption** | Resources can't be forcibly taken | Allow lock stealing / use `trylock` + back off |
| **Circular wait** | A cycle exists in the "waits-for" graph | **Impose a global lock-ordering** (the practical favorite) |

### 7.1 Prevention vs avoidance vs detection

- **Prevention** — design so one Coffman condition can never hold. **Lock ordering** (break circular wait) is the overwhelmingly common production answer: define a total order over all locks and always acquire in that order. Simple, static, robust.
- **Avoidance** — dynamically refuse a request that *could* lead to an unsafe state. The **Banker's algorithm** (Dijkstra) is the formalism: only grant a resource request if, afterward, there's still some sequence in which every process can finish. Requires knowing maximum claims in advance — rarely practical for general locks, but real in resource managers and admission control.
- **Detection & recovery** — let deadlocks happen, periodically build the waits-for graph, find cycles, and recover (abort a victim, roll back). This is what **databases do**: InnoDB/PostgreSQL detect lock-wait cycles and kill the cheapest victim transaction with a deadlock error. Practical when aborts are cheap (transactions can retry).

### 7.2 Banker's algorithm (safety check)

```python
"""bankers.py — Dijkstra's Banker's algorithm: is a resource state SAFE?
A state is safe iff there exists an ordering in which every process can obtain
its maximum need and finish. Used for deadlock AVOIDANCE. Run: python bankers.py"""
from copy import deepcopy


def is_safe(available, maximum, allocation):
    """available: [r]   maximum/allocation: [p][r].  Returns (safe?, safe_sequence)."""
    n_proc = len(maximum)
    n_res = len(available)
    need = [[maximum[p][r] - allocation[p][r] for r in range(n_res)]
            for p in range(n_proc)]
    work = list(available)
    finish = [False] * n_proc
    sequence = []

    made_progress = True
    while made_progress:
        made_progress = False
        for p in range(n_proc):
            if not finish[p] and all(need[p][r] <= work[r] for r in range(n_res)):
                # Pretend p runs to completion and releases its allocation.
                for r in range(n_res):
                    work[r] += allocation[p][r]
                finish[p] = True
                sequence.append(p)
                made_progress = True

    return all(finish), sequence


def request_resources(available, maximum, allocation, pid, request):
    """Grant `request` for process `pid` only if the resulting state stays safe."""
    need = [maximum[pid][r] - allocation[pid][r] for r in range(len(available))]
    if any(request[r] > need[r] for r in range(len(request))):
        return False, "request exceeds declared maximum"
    if any(request[r] > available[r] for r in range(len(request))):
        return False, "resources unavailable now (must wait)"
    # Tentatively grant, then check safety.
    av = [available[r] - request[r] for r in range(len(request))]
    alloc = deepcopy(allocation)
    for r in range(len(request)):
        alloc[pid][r] += request[r]
    safe, seq = is_safe(av, maximum, alloc)
    return safe, (f"granted; safe sequence {seq}" if safe
                  else "denied: would leave unsafe state")


if __name__ == "__main__":
    # 3 resource types, 5 processes (the classic textbook instance).
    available = [3, 3, 2]
    maximum = [[7, 5, 3], [3, 2, 2], [9, 0, 2], [2, 2, 2], [4, 3, 3]]
    allocation = [[0, 1, 0], [2, 0, 0], [3, 0, 2], [2, 1, 1], [0, 0, 2]]

    safe, seq = is_safe(available, maximum, allocation)
    print(f"initial state safe? {safe}  safe sequence: {seq}")
    assert safe

    ok, msg = request_resources(available, maximum, allocation, pid=1, request=[1, 0, 2])
    print(f"P1 requests [1,0,2]: {msg}")
    assert ok

    ok, msg = request_resources(available, maximum, allocation, pid=0, request=[0, 2, 0])
    print(f"P0 requests [0,2,0]: {msg}")
```

---

## 8. Livelock and starvation

Deadlock's cousins — threads are *not* blocked, yet still make no progress:

- **Livelock** — threads actively respond to each other and keep changing state without progressing. The classic: two people in a corridor each step aside in the same direction, repeatedly. In code, two threads each detect contention, both back off and retry simultaneously, and re-collide forever. **Fix:** randomized exponential backoff (the same trick Ethernet and TCP use) breaks the symmetry.
- **Starvation** — a thread is perpetually denied a resource it needs, even though the resource keeps becoming available, because others keep beating it to it. Causes: unfair locks, priority scheduling without aging, reader-preference rwlocks under constant reads. **Fix:** fairness (FIFO ticket locks, aging, bounded-waiting guarantees).

> **Priority inversion** is a special, infamous case: a high-priority thread waits on a lock held by a low-priority thread, which is itself starved of CPU by a medium-priority thread — so the high-priority thread is effectively blocked by the medium one. It froze the Mars Pathfinder in 1997. The fix is **priority inheritance** (the lock holder temporarily inherits the waiter's priority), available via `PTHREAD_PRIO_INHERIT`.

---

## 9. Memory barriers and the C/C++11 memory model

This is the part senior engineers most often have wrong. **Both the compiler and the CPU reorder memory operations.** Single-threaded correctness is preserved (the "as-if" rule), but *other threads can observe the reordering*. Without explicit ordering constraints, this "obviously correct" code is broken:

```text
   Initially x = 0, ready = 0
   Thread A:                         Thread B:
     x = 42;                           while (ready == 0) {}
     ready = 1;                        print(x);          // may print 0!
```

On a weakly-ordered CPU (ARM, POWER) — and even on x86 due to *compiler* reordering — Thread B can see `ready == 1` but still read the stale `x == 0`, because A's two stores became visible out of order, or B's two loads were reordered. The fix is to constrain ordering with **atomics and memory orderings**.

### 9.1 The C11/C++11 orderings

| Ordering | Guarantee | Use for |
|---|---|---|
| `relaxed` | Atomicity only; **no** ordering w.r.t. other operations | Counters/statistics where only the final count matters |
| `acquire` (on a load) | No reads/writes *after* it can be reordered *before* it; pairs with a release | Lock acquire; reading a flag that publishes data |
| `release` (on a store) | No reads/writes *before* it can be reordered *after* it; pairs with an acquire | Lock release; publishing data behind a flag |
| `acq_rel` | Both, for read-modify-write ops | CAS that both consumes and publishes |
| `seq_cst` | All `seq_cst` ops appear in a single global total order (strongest, default) | When in doubt; correctness over speed |

The **acquire/release** pair is the workhorse. The rule: *if thread A does a release-store to an atomic and thread B does an acquire-load that reads that value, then everything A did before the release is visible to B after the acquire.* This is the "publish/subscribe" of memory — and it's exactly how a correct lock works (acquire on lock, release on unlock).

A correct, portable version of the broken example:

```c
/* publish.c — correct cross-thread publication via acquire/release.
 * Build: cc -O2 -pthread -o publish publish.c */
#include <stdatomic.h>
#include <pthread.h>
#include <stdio.h>
#include <assert.h>

static int data = 0;                       /* plain, guarded by the atomic flag */
static atomic_int ready = 0;

static void *producer(void *_) {
    data = 42;                             /* (1) */
    atomic_store_explicit(&ready, 1, memory_order_release);  /* (2) publishes (1) */
    return NULL;
}

static void *consumer(void *_) {
    while (atomic_load_explicit(&ready, memory_order_acquire) == 0)
        ;                                   /* spin until published */
    /* The acquire pairs with the release: 'data = 42' is now guaranteed visible. */
    assert(data == 42);
    printf("consumer saw data = %d\n", data);
    return NULL;
}

int main(void) {
    pthread_t p, c;
    pthread_create(&c, NULL, consumer, NULL);
    pthread_create(&p, NULL, producer, NULL);
    pthread_join(p, NULL);
    pthread_join(c, NULL);
    return 0;
}
```

> **Hardware mapping:** x86-64 is **TSO (total store order)** — it never reorders loads-after-loads or stores-after-stores, so acquire/release are nearly free (just compiler barriers); only the store-load reordering needs an `mfence`/`lock` for `seq_cst`. ARM/POWER are weakly ordered and emit real barrier instructions (`dmb`, `lwsync`). This is why concurrency bugs "work on my x86 laptop" and explode on ARM servers.

---

## 10. False sharing and cache-line padding

Caches operate on **cache lines** (64 bytes on x86-64), not individual bytes. **Coherence** (MESI protocol) is maintained per *line*. If two threads on different cores write to two *different* variables that happen to live on the *same* cache line, the line ping-pongs between the cores' caches — each write invalidates the other core's copy — even though there's no logical sharing. This is **false sharing**, and it can silently cost an order of magnitude.

```
   struct { long a; long b; }  counters;   // a and b in the SAME 64B line
   Core 0 writes counters.a   ->  invalidates the line in Core 1's cache
   Core 1 writes counters.b   ->  invalidates the line in Core 0's cache
   ... line bounces across the interconnect on EVERY write (~100ns each)
```

The fix is **padding/alignment** so each hot variable owns its own cache line:

```c
/* false_sharing.c — measure the cost of false sharing and the padding fix.
 * Build: cc -O2 -pthread -o false_sharing false_sharing.c
 * Run:   ./false_sharing */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdint.h>
#include <time.h>

#define ITERS 100000000L
#define CACHELINE 64

/* Packed: both counters share a cache line  -> false sharing. */
struct packed { volatile long a; volatile long b; };

/* Padded: each counter sits alone on its own cache line -> no false sharing. */
struct padded {
    volatile long a; char pad[CACHELINE - sizeof(long)];
    volatile long b;
};

static double now(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void *bump_a(void *p) { volatile long *x = p; for (long i=0;i<ITERS;i++) (*x)++; return NULL; }
static void *bump_b(void *p) { volatile long *x = p; for (long i=0;i<ITERS;i++) (*x)++; return NULL; }

static double run(volatile long *a, volatile long *b) {
    pthread_t t1, t2;
    double t0 = now();
    pthread_create(&t1, NULL, bump_a, (void*)a);
    pthread_create(&t2, NULL, bump_b, (void*)b);
    pthread_join(t1, NULL); pthread_join(t2, NULL);
    return now() - t0;
}

int main(void) {
    static struct packed pk;
    static struct padded pd;
    double t_false = run(&pk.a, &pk.b);
    double t_ok    = run(&pd.a, &pd.b);
    printf("false sharing (same line): %.3f s\n", t_false);
    printf("padded (separate lines):   %.3f s\n", t_ok);
    printf("padding is %.1fx faster\n", t_false / t_ok);
    return 0;
}
```

On a typical multicore box the padded version is **2–8× faster** for identical logic. This is why high-performance code pads per-thread counters, why Java has `@Contended`, and why `struct` field layout is a performance decision in hot concurrent code.

---

## 11. A deadlock demo and the lock-ordering fix

Two accounts, two threads transferring in opposite directions — the classic lock-order deadlock and its fix.

```python
"""deadlock_fix.py — reproduce a lock-ordering deadlock, then fix it.
Run: python deadlock_fix.py   (the buggy variant may hang; we run it with a watchdog)"""
import threading


class Account:
    _next_id = 0

    def __init__(self, balance):
        self.balance = balance
        self.lock = threading.Lock()
        self.id = Account._next_id            # global order key for the fix
        Account._next_id += 1


def transfer_buggy(src, dst, amount):
    # Acquires in argument order -> opposing transfers acquire in opposite order
    # -> circular wait -> deadlock.
    with src.lock:
        with dst.lock:
            src.balance -= amount
            dst.balance += amount


def transfer_safe(src, dst, amount):
    # FIX: impose a GLOBAL lock ordering (by account id). Breaks circular wait
    # (Coffman condition #4) regardless of transfer direction.
    first, second = (src, dst) if src.id < dst.id else (dst, src)
    with first.lock:
        with second.lock:
            src.balance -= amount
            dst.balance += amount


def hammer(transfer, a, b, n):
    for _ in range(n):
        transfer(a, b, 1)
        transfer(b, a, 1)


def run(transfer, label, timeout=5.0):
    a, b = Account(1000), Account(1000)
    t1 = threading.Thread(target=hammer, args=(transfer, a, b, 100000))
    t2 = threading.Thread(target=hammer, args=(transfer, b, a, 100000))
    t1.start(); t2.start()
    t1.join(timeout); t2.join(timeout)
    if t1.is_alive() or t2.is_alive():
        print(f"{label}: DEADLOCKED (threads still alive after {timeout}s)")
        return False
    assert a.balance + b.balance == 2000, "money must be conserved"
    print(f"{label}: completed, balances conserved (a={a.balance}, b={b.balance})")
    return True


if __name__ == "__main__":
    # The safe version always completes. The buggy version frequently deadlocks;
    # we demonstrate the fix is robust.
    run(transfer_safe, "lock-ordered (safe)")
    print("note: transfer_buggy acquires locks in argument order and can deadlock\n"
          "      under opposing concurrent transfers; transfer_safe orders by id.")
```

The fix — **a global, consistent acquisition order** — is the single most important deadlock-avoidance technique in real code, and it generalizes: lock by a stable key (account id, file inode, address), not by argument position.

---

## 12. Lock-free programming, CAS, and the ABA problem

**Lock-free** means *some* thread always makes progress regardless of others' delays (no lock to block on); **wait-free** is the stronger guarantee that *every* thread finishes in bounded steps. Both are built on one hardware primitive: **compare-and-swap (CAS)**.

`CAS(addr, expected, new)`: atomically, if `*addr == expected`, set `*addr = new` and return true; else return false. The universal pattern is **read-modify-CAS-retry**:

```text
   do {
       old = atomic_load(addr)
       new = compute(old)
   } while (!CAS(addr, old, new))     // retry if someone else changed it meanwhile
```

A CAS-based lock-free counter (contrast with the lost-update race of §2):

```c
/* lockfree_counter.c — a correct lock-free counter via CAS retry loop.
 * Build: cc -O2 -pthread -o lockfree_counter lockfree_counter.c */
#include <stdatomic.h>
#include <pthread.h>
#include <stdio.h>

#define THREADS 8
#define PER     1000000

static atomic_long counter = 0;

static void *worker(void *_) {
    for (int i = 0; i < PER; i++) {
        long old = atomic_load_explicit(&counter, memory_order_relaxed);
        while (!atomic_compare_exchange_weak_explicit(
                   &counter, &old, old + 1,
                   memory_order_relaxed, memory_order_relaxed))
            ;                 /* old is reloaded by CAS on failure; just retry */
    }
    return NULL;
}

int main(void) {
    pthread_t t[THREADS];
    for (int i = 0; i < THREADS; i++) pthread_create(&t[i], NULL, worker, NULL);
    for (int i = 0; i < THREADS; i++) pthread_join(t[i], NULL);
    long expect = (long)THREADS * PER;
    printf("counter = %ld (expected %ld) %s\n",
           (long)counter, expect, counter == expect ? "OK" : "WRONG");
    return 0;
}
```

(For a pure increment, `atomic_fetch_add` is better — it's a single instruction. The CAS loop is shown because it's the *general* pattern for any read-modify-write, e.g., a max, a clamp, or a pointer swing in a lock-free stack.)

### 12.1 The ABA problem

CAS checks that a value is *unchanged*, but "unchanged value" is not "unchanged state." Between your read of `A` and your CAS, another thread can change `A → B → A`. Your CAS sees `A`, succeeds, and you proceed on a false premise — the classic failure of a naive lock-free stack where a node is freed and a *new* node is allocated at the same address.

Fixes:
- **Tagged pointers / version counters** — pack a monotonically increasing tag with the pointer and CAS both together (double-width CAS, `cmpxchg16b` on x86-64). The tag changes even when the pointer returns to `A`.
- **Hazard pointers** (Michael) or **epoch-based reclamation** — defer freeing memory until no thread can hold a stale reference, so an address can't be recycled under a peer.
- **RCU** (§14) — readers proceed without CAS; reclamation waits for a grace period.

---

## 13. Futexes — how userspace locks actually work

A pure-userspace mutex would have to spin (wasteful) or syscall on every operation (slow). Linux's **futex (fast userspace mutex)** gives the best of both: the *uncontended* path is a single atomic in userspace (no kernel at all), and only the *contended* path enters the kernel to sleep/wake.

```text
   lock():
     if CAS(state, UNLOCKED, LOCKED) succeeds:   # fast path: no syscall
         return
     # contended: ask the kernel to sleep us until state changes
     futex(&state, FUTEX_WAIT, LOCKED)

   unlock():
     state = UNLOCKED
     if there were waiters:
         futex(&state, FUTEX_WAKE, 1)            # syscall only if contended
```

This is why an uncontended `pthread_mutex_lock` costs ~20 ns (just the atomic) but a contended one costs a syscall + context switch. The design lesson: **your fast path should never touch the kernel**; pay the kernel only when you must block. Every modern mutex, semaphore, and CV in glibc is built on futexes.

---

## 14. RCU (Read-Copy-Update)

**RCU** (McKenney; pervasive in the Linux kernel) is a synchronization mechanism for *read-mostly* data that lets readers run with **zero locks, zero atomics, zero barriers on most architectures** — read-side cost is essentially free. Writers don't mutate in place; they:

1. **Copy** the data structure (or the affected node).
2. **Update** the copy.
3. **Atomically swing a pointer** to publish the new version (a release-store).
4. **Wait for a grace period** — until every CPU that might have been reading the old version has passed through a quiescent state (e.g., a context switch) — *then* free the old version.

```
   readers:  rcu_read_lock(); p = rcu_dereference(ptr); use(p); rcu_read_unlock();
             (rcu_read_lock is often a no-op / compiler barrier — nearly free)
   writer:   new = copy(old); modify(new);
             rcu_assign_pointer(ptr, new);     // publish (release)
             synchronize_rcu();                // wait for grace period
             free(old);                        // now safe: no reader can hold it
```

The genius: readers never block writers and never wait; the *writer* absorbs all the cost (the grace-period wait). This is ideal for routing tables, config, and the dentry cache — data read millions of times per second and updated rarely. It sidesteps the rwlock's reader-counter false-sharing bottleneck (§5) entirely. The cost is writer complexity and deferred reclamation (memory is freed late).

---

## 15. The GIL (Global Interpreter Lock)

CPython's **GIL** is a single mutex that allows only one thread to execute Python bytecode at a time. Consequences every Python systems engineer must internalize:

- **CPU-bound multithreading doesn't scale** in CPython — N threads doing pure-Python computation run no faster than one (they take turns holding the GIL). Use **`multiprocessing`** (separate interpreters, separate GILs, separate address spaces) or native extensions that release the GIL.
- **I/O-bound multithreading *does* help** — a thread releases the GIL while blocked in a syscall (socket read, disk I/O), so other threads run. This is why threaded I/O servers and the producer-consumer demo in §6 work fine.
- **The GIL does NOT make your code thread-safe.** It guarantees a single *bytecode* is atomic, but `counter += 1` is multiple bytecodes — the lost-update race of §2 still happens. You still need locks around compound operations.
- **C extensions** release the GIL around heavy native work (`Py_BEGIN_ALLOW_THREADS`), which is how NumPy, and database drivers achieve real parallelism — they drop into C, release the GIL, and the OS threads run in parallel.

The trajectory: PEP 703 introduces an *optional* no-GIL ("free-threaded") build of CPython (3.13+), making per-object locking and true thread parallelism possible — at which point all the C/atomics discipline in this document becomes directly relevant to Python too. Until then: **`multiprocessing` for CPU work, threads for I/O, locks for correctness regardless.**

---

## 16. Advanced: memory reclamation for lock-free, seqlocks, and the progress hierarchy

### The hardest lock-free problem: *when can I free this?*

Lock-free reads ([§12](#12-lock-free-programming-cas-and-the-aba-problem)) have a
subtle hazard: a reader may hold a pointer to a node a writer wants to free. Free it
too early → use-after-free; never free it → leak. This **safe-memory-reclamation
(SMR)** problem is the real difficulty in production lock-free code. The standard
solutions:

| Technique | How it works | Used by |
|---|---|---|
| **RCU** ([§14](#14-rcu-read-copy-update)) | writers wait for a *grace period* (all readers to pass a quiescent state) before freeing | the Linux kernel, everywhere |
| **Hazard pointers** | each reader publishes the pointer it's using; a writer frees only nodes no hazard pointer references | concurrent libraries, C++26 `std::hazard_pointer` |
| **Epoch-based reclamation (EBR)** | readers enter an epoch; memory from epoch N is freed once all readers have advanced past it | crossbeam (Rust), folly |
| **Reference counting (atomic)** | per-node atomic refcount; free at zero | simple, but the refcount itself is a contention/cache hotspot |

The trade-off: RCU and EBR give near-zero reader overhead but *defer* reclamation
(memory can balloon if a reader stalls); hazard pointers reclaim promptly but add a
per-read store. Choosing wrong is how lock-free code either leaks under load or
corrupts.

### Seqlocks — optimistic reads for read-mostly, write-rare data

A **seqlock** lets readers proceed without locking: the writer bumps a sequence
counter (odd = write in progress), and a reader retries if the counter changed or is
odd during its read. Readers never block writers and take no cache-line ownership —
ideal for a frequently-read, rarely-written value (the kernel uses it for
`gettimeofday`/timekeeping). The catch: readers may *retry*, and the protected data
must be trivially copyable (no pointers a torn read could dereference).

```
   writer:  seq++ (odd) ; write data ; seq++ (even)
   reader:  do { s = seq; if (s odd) retry; copy data; } while (seq != s);
```

### The progress hierarchy (know the guarantees you actually have)

"Lock-free" is one rung of a ladder — staff-level precision matters when reasoning
about real-time and contention:

- **Blocking** — a stalled thread (preempted, paused) can block all others (any mutex).
- **Obstruction-free** — a thread makes progress if it runs in isolation (weakest
  non-blocking).
- **Lock-free** — *some* thread always makes progress system-wide (no deadlock/
  livelock), but an individual thread can starve.
- **Wait-free** — *every* thread completes in a bounded number of steps (strongest;
  required for hard real-time, rare and expensive to achieve).

A CAS retry loop is lock-free, **not** wait-free (a thread can lose the race forever
under contention). If you need bounded latency, "lock-free" is not enough.

---

## 17. Trade-offs summary

- **Atomicity and ordering are not free defaults** — `++` races, and stores reorder across cores. Correctness comes from primitives that issue the right barriers (acquire/release), not from code that "looks sequential."
- **Spinlock vs mutex** is a wait-strategy choice: spin only for ultra-short, contention-rare, holder-is-running cases (mostly kernel); mutex (futex-backed, adaptively spinning) for everything in userspace.
- **Condition variables demand `while`-loop predicate checks** (Mesa semantics + spurious wakeups). Signal under the lock.
- **Deadlock needs all four Coffman conditions; break circular wait with a global lock order** — the dominant practical technique. Databases prefer *detection + abort*; OSes prefer *prevention*.
- **Livelock/starvation** are progress failures without blocking; fix with randomized backoff and fairness/aging. Watch for priority inversion (use priority inheritance).
- **False sharing** turns logically independent writes into cache-line ping-pong; pad hot per-thread data to 64 bytes.
- **Lock-free (CAS) trades blocking for retry loops and the ABA hazard**; mitigate ABA with tagged pointers, hazard pointers, epochs, or RCU. Use lock-free only where contention on a hot word genuinely dominates — it's harder to get right than a mutex.
- **Futexes** make the uncontended path kernel-free; **RCU** makes read-mostly reads barrier-free at the cost of writer complexity. **The GIL** serializes Python bytecode — `multiprocessing` for CPU, threads for I/O, locks regardless.

## 18. Key Takeaways

1. Concurrency bugs come from two sources: **non-deterministic interleaving** (races over compound operations) and **memory reordering** (the CPU/compiler making one core's stores visible out of order to another). You must defend against both.
2. **A correct critical section needs mutual exclusion, progress, and bounded waiting.** Pick the primitive by what a waiter should do: spin (rarely, kernel), block (mutex), count resources (semaphore), or wait on a predicate (condition variable — always re-check in a `while`).
3. **Deadlock requires all four Coffman conditions.** The everyday cure is a **global lock ordering** to break circular wait; the database cure is **cycle detection + victim abort**; the Banker's algorithm is the avoidance formalism.
4. **The C11 memory model** (relaxed/acquire/release/seq_cst) is the portable contract; **acquire/release pairing** is the publish/subscribe of memory and the basis of every correct lock. x86 is forgiving (TSO); ARM/POWER are not — test there.
5. **Cache coherence operates per 64-byte line**, so **false sharing** silently serializes independent work; pad hot concurrent data.
6. **Lock-free programming** rests on **CAS retry loops** and must handle the **ABA problem**; **futexes** keep uncontended locks out of the kernel; **RCU** gives near-free reads for read-mostly data.
7. **The GIL** means CPython threads don't parallelize CPU work (use `multiprocessing`) but do help I/O — and it never substitutes for your own locks.

> Read previous: [03 — Memory Management](03_memory_management.md) — the shared pages these threads race over, and why "the page is written" and "another core sees it" are different events.
