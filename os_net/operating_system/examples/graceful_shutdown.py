"""
graceful_shutdown.py — Drain in-flight work on SIGINT/SIGTERM, then exit cleanly.

ENTERPRISE PROBLEM
------------------
When Kubernetes (or systemd, or a deploy script) wants to stop your process, it
sends SIGTERM and waits a grace period before sending the un-catchable SIGKILL.
A 12-factor app MUST handle SIGTERM gracefully:
  * STOP accepting new work.
  * FINISH (drain) the work already in flight.
  * Flush buffers, commit offsets, close connections.
  * Exit 0 before the grace period expires.

Get this wrong and every rolling deploy drops requests, double-processes a
message (because you died mid-ack), or corrupts a half-written file. This is
the same pattern as a Kubernetes preStop hook + terminationGracePeriodSeconds.

HOW SIGNALS WORK HERE:
  * The OS delivers the signal asynchronously. Our handler must be tiny and
    async-signal-safe: it just FLIPS A FLAG (an Event). It must NOT do real
    work inside the handler.
  * The main loop checks that flag and performs the orderly drain itself.

PLATFORM NOTE (Windows vs POSIX):
  * SIGINT (Ctrl-C) and SIGTERM exist on BOTH Windows and POSIX, so we register
    those. On Windows, Python synthesizes SIGTERM handling for the current
    process and SIGINT is delivered for Ctrl-C.
  * Signals like SIGHUP/SIGQUIT are POSIX-only; we register them ONLY if they
    exist (guarded with hasattr) so this file runs unmodified on Windows.
  * To keep the demo deterministic and CI-safe, instead of waiting for a real
    Ctrl-C we raise SIGINT to OURSELVES after a moment, then prove the drain ran.

RELATED OS CONCEPT DOC: ../01_processes_threads.md (signals, signal-safety),
                        ../06_io_models_async.md (event loops / draining).

HOW TO RUN
----------
    py graceful_shutdown.py
    # In a real terminal you could also press Ctrl-C to trigger the same drain.

Cross-platform (Windows/POSIX). Self-verifies with asserts.
"""

import os
import queue
import signal
import threading
import time

# The shutdown flag. An Event is the right primitive: the signal handler sets
# it (cheap, safe), and the worker loop polls/waits on it.
_shutdown = threading.Event()


def _install_signal_handlers() -> list[str]:
    """Register handlers for the signals that exist on this platform."""
    installed = []

    def handler(signum, _frame):
        # IMPORTANT: keep this minimal and async-signal-safe. Just set the flag.
        # Do NOT log, allocate, or grab locks here in production code.
        _shutdown.set()

    # SIGINT (Ctrl-C) and SIGTERM exist on Windows and POSIX alike.
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, handler)
            installed.append(name)

    # POSIX-only signals: register only if present so Windows doesn't choke.
    for name in ("SIGHUP", "SIGQUIT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, handler)
            installed.append(name)

    return installed


class Worker:
    """A worker that processes a stream of jobs and drains on shutdown."""

    def __init__(self):
        self.jobs: "queue.Queue" = queue.Queue()
        self.processed = 0
        self.accepted = 0

    def feed(self, n: int) -> None:
        for i in range(n):
            self.jobs.put(i)
            self.accepted += 1

    def run(self) -> None:
        """Main loop. Process jobs until shutdown is requested, then drain."""
        while not _shutdown.is_set():
            try:
                job = self.jobs.get(timeout=0.05)
            except queue.Empty:
                continue
            self._process(job)

        # --- SHUTDOWN REQUESTED: stop taking new work, drain what remains. ---
        print(f"  [worker] shutdown signal received; draining "
              f"{self.jobs.qsize()} in-flight jobs...")
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            self._process(job)
        print("  [worker] drain complete; exiting cleanly.")

    def _process(self, job) -> None:
        time.sleep(0.001)  # simulate work
        self.processed += 1


def main() -> None:
    installed = _install_signal_handlers()
    print(f"Installed signal handlers for: {', '.join(installed)}")
    print(f"Platform: {os.name} (on POSIX you can also Ctrl-C to trigger drain)")

    worker = Worker()
    worker.feed(500)  # queue up real work

    # Run the worker in a background thread so the main thread can simulate the
    # operator/orchestrator sending a termination signal.
    t = threading.Thread(target=worker.run, name="worker")
    t.start()

    # Let it process for a moment, then raise SIGINT in THIS process — exactly
    # what a Ctrl-C or `kill -INT <pid>` would do — to exercise the graceful
    # path. We use signal.raise_signal() (Python 3.8+) rather than
    # os.kill(getpid(), ...) because raise_signal reliably routes through the
    # registered Python handler on BOTH Windows and POSIX, whereas os.kill with
    # SIGINT to self on Windows just hard-terminates the process.
    time.sleep(0.05)
    print("Raising SIGINT in this process (simulating Ctrl-C / kill)...")
    signal.raise_signal(signal.SIGINT)

    t.join(timeout=10.0)

    print(f"Accepted={worker.accepted} processed={worker.processed} "
          f"queue_remaining={worker.jobs.qsize()}")

    # --- Self-verification ---------------------------------------------------
    assert _shutdown.is_set(), "shutdown flag should have been set by the handler"
    assert not t.is_alive(), "worker should have exited"
    # The whole point: NO job is lost. Everything accepted was processed during
    # normal operation or during the drain.
    assert worker.processed == worker.accepted, (
        f"lost work! processed {worker.processed} of {worker.accepted}")
    assert worker.jobs.qsize() == 0, "queue should be fully drained"
    print("All assertions passed: caught signal, drained all in-flight work, lost nothing.")


if __name__ == "__main__":
    main()
