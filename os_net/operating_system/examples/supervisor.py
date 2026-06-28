"""
supervisor.py — A mini process supervisor: spawn, monitor, restart with backoff.

ENTERPRISE PROBLEM
------------------
Production never runs a single bare process. Something has to KEEP IT RUNNING:
systemd, runit, supervisord, Kubernetes' kubelet, or an Erlang/OTP supervisor
tree. The job is always the same:
  * SPAWN child worker processes.
  * MONITOR them (reap exits, learn the exit code).
  * RESTART crashed children — but with EXPONENTIAL BACKOFF, so a child that
    crash-loops (e.g. bad config, dead dependency) doesn't burn the CPU
    restarting thousands of times per second. systemd calls this
    StartLimitIntervalSec / RestartSec; OTP calls it max_restarts/max_seconds.
  * Stop everything cleanly on shutdown.

WHY PROCESSES (not threads)? A crashed thread can corrupt the whole process; a
crashed CHILD PROCESS is isolated — the supervisor survives and restarts it.
This crash-isolation is the entire reason prefork servers and supervisor trees
exist. (See ../01_processes_threads.md, "concurrency model is a one-way door".)

WINDOWS NOTE: multiprocessing uses the "spawn" start method on Windows (and on
macOS by default). Spawn re-imports this module in the child, so:
  * The worker entry point MUST be a top-level (module-level) function — a
    nested/lambda target can't be pickled for spawn.
  * All process-spawning code MUST sit behind `if __name__ == "__main__":` or
    the child re-runs main() and you get a fork bomb. We honor both rules.

RELATED OS CONCEPT DOC: ../01_processes_threads.md (fork/exec, zombies/reaping,
                        supervisor trees, crash isolation).

HOW TO RUN
----------
    py supervisor.py

Cross-platform (uses multiprocessing 'spawn'). Short demo: it restarts a
deliberately-crashing child a couple of times with growing backoff, then stops.
Self-verifies with asserts.
"""

import multiprocessing as mp
import os
import time


# ---------------------------------------------------------------------------
# WORKER ENTRY POINT — must be module-level so 'spawn' can pickle/import it.
# ---------------------------------------------------------------------------
def crashing_worker(crash_after: float) -> None:
    """A worker that runs briefly, then deliberately crashes (non-zero exit).

    Real workers would loop forever serving requests; this one simulates a
    process that hits a fatal error so we can watch the supervisor restart it.
    """
    print(f"    [child pid={os.getpid()}] started; will crash in {crash_after}s")
    time.sleep(crash_after)
    print(f"    [child pid={os.getpid()}] crashing now (exit 1)")
    os._exit(1)  # hard crash with non-zero status (no cleanup, like a real fault)


def stable_worker(run_for: float) -> None:
    """A worker that runs for a while and exits 0 (clean, voluntary stop)."""
    print(f"    [child pid={os.getpid()}] stable worker, running {run_for}s")
    time.sleep(run_for)
    print(f"    [child pid={os.getpid()}] finished cleanly (exit 0)")


class Supervisor:
    """Supervises ONE child, restarting it on crash with exponential backoff."""

    def __init__(self, target, args=(), max_restarts: int = 3,
                 base_backoff: float = 0.1, backoff_factor: float = 2.0):
        self.target = target
        self.args = args
        self.max_restarts = max_restarts
        self.base_backoff = base_backoff
        self.backoff_factor = backoff_factor
        # Observability: every supervisor exports these.
        self.restarts = 0
        self.exit_codes: list[int] = []
        self.backoffs: list[float] = []

    def _spawn(self) -> mp.Process:
        p = mp.Process(target=self.target, args=self.args)
        p.start()
        print(f"  [supervisor] spawned child pid={p.pid}")
        return p

    def run(self) -> None:
        proc = self._spawn()
        while True:
            # join() reaps the child (no zombies) and blocks until it exits.
            proc.join()
            code = proc.exitcode  # 0 = clean; non-zero/negative = crash/signal
            self.exit_codes.append(code)

            if code == 0:
                print("  [supervisor] child exited cleanly (0); not restarting.")
                return

            if self.restarts >= self.max_restarts:
                print(f"  [supervisor] child crashed (exit {code}); restart limit "
                      f"({self.max_restarts}) reached. Giving up (crash-loop guard).")
                return

            # EXPONENTIAL BACKOFF: 0.1, 0.2, 0.4, ... so a crash loop can't spin.
            backoff = self.base_backoff * (self.backoff_factor ** self.restarts)
            self.backoffs.append(backoff)
            self.restarts += 1
            print(f"  [supervisor] child crashed (exit {code}); "
                  f"restart #{self.restarts} after {backoff:.2f}s backoff")
            time.sleep(backoff)
            proc = self._spawn()


def main() -> None:
    # 'spawn' is the default on Windows/macOS; set it explicitly so the demo
    # behaves identically everywhere (and to make the requirement visible).
    try:
        mp.set_start_method("spawn")
    except RuntimeError:
        pass  # already set (e.g. when re-run in the same interpreter)

    print("=== Demo 1: a crash-looping child, restarted with exponential backoff ===")
    sup = Supervisor(
        target=crashing_worker,
        args=(0.15,),          # child crashes 0.15s after start
        max_restarts=3,        # keep the demo short
        base_backoff=0.1,
        backoff_factor=2.0,
    )
    sup.run()

    print(f"  Summary: restarts={sup.restarts}, exit_codes={sup.exit_codes}, "
          f"backoffs={[round(b, 3) for b in sup.backoffs]}")

    # --- Self-verification ---------------------------------------------------
    # We spawned once + restarted max_restarts times => max_restarts+1 exits,
    # all non-zero (every run crashed).
    assert sup.restarts == 3, f"expected 3 restarts, got {sup.restarts}"
    assert len(sup.exit_codes) == 4, f"expected 4 child exits, got {len(sup.exit_codes)}"
    assert all(c != 0 for c in sup.exit_codes), f"a child exited 0 unexpectedly: {sup.exit_codes}"
    # Backoff must be strictly increasing (exponential).
    assert sup.backoffs == sorted(sup.backoffs), "backoff was not monotonically increasing"
    assert sup.backoffs[0] < sup.backoffs[-1], "backoff did not grow"
    print("  Demo 1 assertions passed: crash-loop bounded, backoff grew exponentially.")

    print("=== Demo 2: a stable child that exits cleanly is NOT restarted ===")
    sup2 = Supervisor(target=stable_worker, args=(0.1,), max_restarts=3)
    sup2.run()
    assert sup2.restarts == 0, f"clean exit should not restart, got {sup2.restarts}"
    assert sup2.exit_codes == [0], f"expected single clean exit, got {sup2.exit_codes}"
    print("  Demo 2 assertions passed: clean exit was respected (no restart).")

    print("All supervisor assertions passed.")


if __name__ == "__main__":
    # MUST guard process-spawning under __main__ for 'spawn' (else fork bomb).
    main()
