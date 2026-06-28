"""
producer_consumer.py — Correct bounded-buffer producer/consumer with condition variables.

ENTERPRISE PROBLEM
------------------
The producer/consumer pattern is the backbone of every pipeline: ingestion
services buffering events before a database write, a logging library handing
records to a flush thread, Kafka consumers feeding worker pools. The bounded
buffer is what couples them safely:

  * If the buffer is unbounded and consumers are slow, producers allocate until
    the process OOMs.
  * If you hand-roll the synchronization wrong, you get LOST items (a producer
    overwrites a slot before a consumer read it) or DUPLICATED items (two
    consumers grab the same item) or a busy-wait that burns 100% CPU spinning.

The correct, textbook-correct primitive is a bounded buffer guarded by a mutex
plus condition variables: "not full" (producers wait on it) and "not empty"
(consumers wait on it). When a producer adds an item it signals "not empty";
when a consumer removes one it signals "not full". Threads SLEEP while waiting
(no CPU spin) and the mutex guarantees mutual exclusion on the buffer.

This file shows BOTH:
  1. RawBoundedBuffer — built from threading.Condition, so you can see exactly
     how the wait/notify dance works (the pattern interview questions ask for).
  2. The same workload via queue.Queue, which is the stdlib's correct, battle-
     tested implementation of precisely this pattern (use this in real code).

We run MULTIPLE producers + MULTIPLE consumers and then ASSERT that every
produced item was consumed exactly once: no losses, no duplicates.

RELATED OS CONCEPT DOC: ../04_concurrency_synchronization.md (condition variables,
                        the bounded-buffer problem), ../01_processes_threads.md.

HOW TO RUN
----------
    py producer_consumer.py

Cross-platform. Self-verifies with asserts.
"""

import collections
import queue
import threading


class RawBoundedBuffer:
    """A bounded buffer built directly from a mutex + two condition variables.

    This is the canonical synchronization exercise. Note the two invariants the
    Condition protects:
      * len(buffer) never exceeds `capacity` (producers wait on `not_full`).
      * consumers never read from an empty buffer (consumers wait on `not_empty`).
    A single Lock backs both conditions, so the buffer is mutated under exclusion.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._buf: collections.deque = collections.deque()
        self._lock = threading.Lock()
        # Both conditions SHARE the same underlying lock — required so that a
        # producer and consumer never both think they hold the buffer.
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)

    def put(self, item) -> None:
        with self._not_full:  # acquires the shared lock
            # WHILE (not if): guards against spurious wakeups and the case where
            # another producer refilled the buffer between notify and reacquire.
            while len(self._buf) >= self.capacity:
                self._not_full.wait()  # releases lock + sleeps until notified
            self._buf.append(item)
            self._not_empty.notify()  # wake one waiting consumer

    def get(self):
        with self._not_empty:
            while len(self._buf) == 0:
                self._not_empty.wait()
            item = self._buf.popleft()
            self._not_full.notify()  # wake one waiting producer
            return item


# A unique poison pill to tell consumers to stop.
_DONE = object()


def run_with_raw_buffer(num_producers: int, num_consumers: int, per_producer: int):
    """Multiple producers fill a RawBoundedBuffer; multiple consumers drain it."""
    buf = RawBoundedBuffer(capacity=16)
    consumed: list = []
    consumed_lock = threading.Lock()

    def producer(pid: int):
        for i in range(per_producer):
            # Encode producer id into the value so we can verify uniqueness later.
            buf.put(pid * per_producer + i)

    def consumer():
        while True:
            item = buf.get()
            if item is _DONE:
                return
            with consumed_lock:
                consumed.append(item)

    producers = [threading.Thread(target=producer, args=(p,)) for p in range(num_producers)]
    consumers = [threading.Thread(target=consumer) for _ in range(num_consumers)]
    for t in producers + consumers:
        t.start()
    for t in producers:
        t.join()  # all items now produced
    # One poison pill per consumer so each exits its loop.
    for _ in consumers:
        buf.put(_DONE)
    for t in consumers:
        t.join()
    return consumed


def run_with_stdlib_queue(num_producers: int, num_consumers: int, per_producer: int):
    """Identical workload using queue.Queue — the correct production choice."""
    q: "queue.Queue" = queue.Queue(maxsize=16)
    consumed: list = []
    consumed_lock = threading.Lock()

    def producer(pid: int):
        for i in range(per_producer):
            q.put(pid * per_producer + i)

    def consumer():
        while True:
            item = q.get()
            try:
                if item is _DONE:
                    return
                with consumed_lock:
                    consumed.append(item)
            finally:
                q.task_done()

    producers = [threading.Thread(target=producer, args=(p,)) for p in range(num_producers)]
    consumers = [threading.Thread(target=consumer) for _ in range(num_consumers)]
    for t in producers + consumers:
        t.start()
    for t in producers:
        t.join()
    for _ in consumers:
        q.put(_DONE)
    for t in consumers:
        t.join()
    return consumed


def verify(consumed: list, num_producers: int, per_producer: int, label: str) -> None:
    expected = set(range(num_producers * per_producer))
    got = consumed
    # No DUPLICATES: each item appears exactly once.
    assert len(got) == len(set(got)), f"{label}: duplicate items detected!"
    # No LOSSES: every expected item was consumed.
    assert set(got) == expected, f"{label}: lost or extra items detected!"
    assert len(got) == num_producers * per_producer, f"{label}: count mismatch"
    print(f"  {label}: consumed {len(got)} items, no losses, no duplicates. OK")


def main() -> None:
    NUM_PRODUCERS = 4
    NUM_CONSUMERS = 3
    PER_PRODUCER = 5000
    total = NUM_PRODUCERS * PER_PRODUCER

    print(f"Producers={NUM_PRODUCERS} Consumers={NUM_CONSUMERS} "
          f"items/producer={PER_PRODUCER} (total={total})")

    print("Running raw threading.Condition bounded buffer...")
    consumed_raw = run_with_raw_buffer(NUM_PRODUCERS, NUM_CONSUMERS, PER_PRODUCER)
    verify(consumed_raw, NUM_PRODUCERS, PER_PRODUCER, "RawBoundedBuffer")

    print("Running stdlib queue.Queue...")
    consumed_q = run_with_stdlib_queue(NUM_PRODUCERS, NUM_CONSUMERS, PER_PRODUCER)
    verify(consumed_q, NUM_PRODUCERS, PER_PRODUCER, "queue.Queue")

    print("All assertions passed: both implementations are correct under contention.")


if __name__ == "__main__":
    main()
