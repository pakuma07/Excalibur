"""
deadlock_demo.py — Reproducing a lock-ordering deadlock AND the fixes.

ENTERPRISE PROBLEM
------------------
The most common real deadlock in production is the LOCK-ORDERING deadlock:
two code paths each grab two locks, but in OPPOSITE orders.

    Thread 1: lock(A) ... then lock(B)
    Thread 2: lock(B) ... then lock(A)

If Thread 1 holds A and Thread 2 holds B at the same moment, each waits forever
for the lock the other holds. This satisfies the four Coffman conditions for
deadlock: mutual exclusion, hold-and-wait, no preemption, and circular wait.

It shows up whenever two resources can be locked together: transferring money
between two accounts, joining two tables' row locks, a parent/child object both
needing a mutex. It is intermittent (timing-dependent), so it usually escapes
testing and strikes under production load.

TWO STANDARD FIXES:
  1. GLOBAL LOCK ORDERING: always acquire locks in a fixed total order (e.g. by
     id()/address). This breaks the "circular wait" condition — no cycle can
     form. This is what databases do (lock rows in primary-key order).
  2. TRY-LOCK WITH TIMEOUT + BACKOFF: don't block forever; if you can't get the
     second lock in time, release the first and retry. This breaks "hold-and-
     wait"/"no preemption". Slower but works when a global order is impractical.

This demo deliberately TRIGGERS the deadlock but uses lock TIMEOUTS so the
process can never actually hang the test — it detects the stuck threads,
reports it, then shows both fixes completing successfully.

RELATED OS CONCEPT DOC: ../04_concurrency_synchronization.md (deadlock, the
                        Coffman conditions, lock ordering).

HOW TO RUN
----------
    py deadlock_demo.py

Cross-platform. Uses timeouts so it CANNOT hang. Self-verifies with asserts.
"""

import threading
import time

lock_a = threading.Lock()
lock_b = threading.Lock()


def demonstrate_deadlock() -> bool:
    """Force the classic AB / BA ordering. Returns True if a deadlock occurred.

    We use acquire(timeout=...) for the SECOND lock so the threads give up
    instead of hanging forever — that lets us *detect* and report the deadlock
    rather than freezing the program.
    """
    deadlocked = {"t1": False, "t2": False}
    # A barrier makes both threads grab their first lock at the same instant,
    # which reliably produces the deadlock window (otherwise it's racy).
    barrier = threading.Barrier(2)

    def t1():
        with lock_a:                       # Thread 1 takes A first
            barrier.wait()                 # ensure T2 has taken B
            # Now try for B (which T2 holds). With a timeout we won't hang.
            got = lock_b.acquire(timeout=1.0)
            if got:
                lock_b.release()
            else:
                deadlocked["t1"] = True    # could not get B: stuck behind T2

    def t2():
        with lock_b:                       # Thread 2 takes B first (opposite order!)
            barrier.wait()                 # ensure T1 has taken A
            got = lock_a.acquire(timeout=1.0)
            if got:
                lock_a.release()
            else:
                deadlocked["t2"] = True    # could not get A: stuck behind T1

    threads = [threading.Thread(target=t1), threading.Thread(target=t2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return deadlocked["t1"] or deadlocked["t2"]


def fixed_with_global_ordering() -> bool:
    """FIX 1: always lock in a global order (sorted by id). No cycle can form."""
    completed = {"t1": False, "t2": False}

    def transfer(first_intended, second_intended, who):
        # Impose a TOTAL ORDER: acquire the lower-id() lock first, always. Both
        # threads therefore agree on order, so no circular wait is possible.
        first, second = sorted((first_intended, second_intended), key=id)
        with first:
            time.sleep(0.01)  # hold a while to provoke contention
            with second:
                completed[who] = True

    t1 = threading.Thread(target=transfer, args=(lock_a, lock_b, "t1"))
    t2 = threading.Thread(target=transfer, args=(lock_b, lock_a, "t2"))
    t1.start(); t2.start()
    t1.join(timeout=3.0); t2.join(timeout=3.0)
    return completed["t1"] and completed["t2"]


def fixed_with_trylock() -> bool:
    """FIX 2: try-lock with timeout + backoff. Release everything and retry."""
    completed = {"t1": False, "t2": False}

    def transfer(first: threading.Lock, second: threading.Lock, who):
        while True:
            if first.acquire(timeout=0.1):
                try:
                    if second.acquire(timeout=0.1):
                        try:
                            completed[who] = True
                            return
                        finally:
                            second.release()
                finally:
                    first.release()
            # Couldn't get both: back off briefly and retry (breaks hold-and-wait).
            time.sleep(0.01)

    # Note: opposite acquisition orders — but try-lock makes it safe anyway.
    t1 = threading.Thread(target=transfer, args=(lock_a, lock_b, "t1"))
    t2 = threading.Thread(target=transfer, args=(lock_b, lock_a, "t2"))
    t1.start(); t2.start()
    t1.join(timeout=3.0); t2.join(timeout=3.0)
    return completed["t1"] and completed["t2"]


def main() -> None:
    print("--- 1. Triggering a lock-ordering deadlock (with timeouts so we can detect it) ---")
    deadlocked = demonstrate_deadlock()
    print(f"  Deadlock detected? {deadlocked}  "
          f"(at least one thread could not acquire its 2nd lock => circular wait)")
    assert deadlocked, "expected to observe a deadlock with the AB/BA ordering"

    print("--- 2. FIX A: global lock ordering (sort by id) ---")
    ok_order = fixed_with_global_ordering()
    print(f"  Both threads completed without deadlock? {ok_order}")
    assert ok_order, "global-ordering fix should let both threads complete"

    print("--- 3. FIX B: try-lock with timeout + backoff ---")
    ok_try = fixed_with_trylock()
    print(f"  Both threads completed without deadlock? {ok_try}")
    assert ok_try, "try-lock fix should let both threads complete"

    print("All assertions passed: deadlock reproduced, then both fixes verified.")


if __name__ == "__main__":
    main()
