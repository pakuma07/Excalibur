"""
worker_pool.py — A bounded worker pool with backpressure and graceful shutdown.

ENTERPRISE PROBLEM
------------------
Every request-processing or batch system needs a way to do work concurrently
WITHOUT spawning an unbounded number of threads/processes. Unbounded fan-out is
the classic outage: 50k incoming jobs each spawn a thread, the box runs out of
memory or thread handles, and the whole service falls over. The fix is a
*bounded* pool of N workers fed by a *bounded* task queue:

    producers --> [ bounded queue (backpressure) ] --> [ N workers ] --> results

This is the exact model behind:
  * Gunicorn / uWSGI prefork workers (fixed worker count, OS-level isolation).
  * Celery worker concurrency (--concurrency=N).
  * Java's ThreadPoolExecutor with a bounded ArrayBlockingQueue.
  * Go's "worker pool" idiom (N goroutines reading one channel).

Two properties make this production-grade:
  1. BACKPRESSURE: the queue has a maxsize. When it is full, the producer
     BLOCKS instead of allocating without limit. Slowness propagates upstream
     instead of turning into an OOM.
  2. GRACEFUL SHUTDOWN: we send one sentinel (poison pill) per worker so every
     worker drains the queue and exits cleanly — no work is lost, no thread is
     killed mid-task.

RELATED OS CONCEPT DOC: ../01_processes_threads.md (concurrency models),
                        ../04_concurrency_synchronization.md (queues/locks).

HOW TO RUN
----------
    py worker_pool.py
    python worker_pool.py

Runs cross-platform (Windows/Linux/macOS). Uses threads, so it is ideal for
I/O-bound work; for CPU-bound work swap threading.Thread for a process pool
(the structure is identical). Self-verifies with asserts.
"""

import queue
import threading
import time

# A unique sentinel object. Identity comparison (`is`) is unambiguous: no real
# task can accidentally equal it. This is the "poison pill" shutdown signal.
_SHUTDOWN = object()


class WorkerPool:
    """A fixed-size pool of worker threads fed by a bounded queue."""

    def __init__(self, num_workers: int, max_queue: int):
        # maxsize > 0 gives us BACKPRESSURE: put() blocks when the queue is full.
        self.tasks: "queue.Queue" = queue.Queue(maxsize=max_queue)
        self.num_workers = num_workers
        self._workers: list[threading.Thread] = []

        # Shared result accounting. A Lock protects them because multiple worker
        # threads mutate them concurrently (the classic read-modify-write race).
        self._lock = threading.Lock()
        self.completed = 0
        self.results: list = []

    def start(self) -> None:
        """Spawn the worker threads. They block on queue.get() until fed."""
        for i in range(self.num_workers):
            t = threading.Thread(target=self._worker_loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def _worker_loop(self) -> None:
        """Each worker pulls tasks until it receives the shutdown sentinel."""
        while True:
            task = self.tasks.get()  # blocks when queue empty (no busy-wait)
            try:
                if task is _SHUTDOWN:
                    return  # clean exit: this worker is done
                result = self._do_work(task)
                with self._lock:
                    self.completed += 1
                    self.results.append(result)
            finally:
                # task_done() MUST pair with every get() so join() works.
                self.tasks.task_done()

    @staticmethod
    def _do_work(task) -> int:
        """Simulate an I/O-bound unit of work (e.g. an HTTP call or a DB write)."""
        n = task
        time.sleep(0.002)  # pretend latency
        return n * n

    def submit(self, task) -> None:
        """Enqueue a task. BLOCKS if the queue is full (backpressure)."""
        self.tasks.put(task)

    def shutdown(self) -> None:
        """Graceful shutdown: drain all work, then stop every worker."""
        # Wait for all currently-queued real tasks to be processed.
        self.tasks.join()
        # One poison pill per worker guarantees each one wakes up and exits.
        for _ in self._workers:
            self.tasks.put(_SHUTDOWN)
        for t in self._workers:
            t.join(timeout=5.0)


def main() -> None:
    NUM_TASKS = 2000
    NUM_WORKERS = 8
    MAX_QUEUE = 100  # small queue => backpressure kicks in quickly

    pool = WorkerPool(num_workers=NUM_WORKERS, max_queue=MAX_QUEUE)
    pool.start()

    print(f"Submitting {NUM_TASKS} tasks to a pool of {NUM_WORKERS} workers "
          f"(queue cap={MAX_QUEUE})...")
    start = time.perf_counter()

    # The producer loop. Because the queue is bounded, submit() naturally
    # throttles us: if the workers fall behind, this loop blocks here. That is
    # backpressure working — the producer cannot outrun the consumers.
    for i in range(NUM_TASKS):
        pool.submit(i)

    pool.shutdown()
    elapsed = time.perf_counter() - start

    throughput = pool.completed / elapsed
    print(f"Completed {pool.completed} tasks in {elapsed:.3f}s "
          f"=> {throughput:,.0f} tasks/sec")

    # ---- Self-verification --------------------------------------------------
    # Every submitted task must have been processed exactly once.
    assert pool.completed == NUM_TASKS, f"expected {NUM_TASKS}, got {pool.completed}"
    assert len(pool.results) == NUM_TASKS, "result count mismatch"
    # Results are correct (n*n for every n in range).
    assert sorted(pool.results) == sorted(i * i for i in range(NUM_TASKS)), "wrong results"
    # The queue is empty and all workers have exited.
    assert pool.tasks.empty(), "queue not drained"
    assert all(not t.is_alive() for t in pool._workers), "a worker is still alive"
    print("All assertions passed: every task processed exactly once, pool shut down cleanly.")


if __name__ == "__main__":
    main()
