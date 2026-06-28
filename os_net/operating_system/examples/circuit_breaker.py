"""
circuit_breaker.py — A thread-safe circuit breaker (CLOSED / OPEN / HALF_OPEN).

ENTERPRISE PROBLEM
------------------
When service A calls a flaky downstream B (a database, a payment provider, a
microservice), naive retry-on-failure makes outages WORSE: B is struggling,
so A retries, which piles more load onto B, which makes B fail harder — a
"retry storm" / cascading failure. The circuit breaker (popularized by Netflix
Hystrix, now in resilience4j, Polly, Istio/Envoy outlier detection) stops this:

  CLOSED    : normal. Calls go through. Count consecutive failures.
              -> if failures reach `fail_threshold`, trip to OPEN.
  OPEN       : the downstream is presumed dead. Calls FAIL FAST immediately
              (no network call, no waiting) for `cooldown` seconds. This sheds
              load off B and gives it time to recover.
              -> after `cooldown` elapses, allow one trial call: HALF_OPEN.
  HALF_OPEN : a single probe is allowed through.
              -> if it succeeds enough times, close the circuit (recovered).
              -> if it fails, re-open immediately (still broken).

The breaker must be THREAD-SAFE because many request threads share one breaker
instance per downstream. All state transitions happen under a single lock.

RELATED OS CONCEPT DOC: ../04_concurrency_synchronization.md (state machines
                        under locks), ../06_io_models_async.md (timeouts/failure).

HOW TO RUN
----------
    py circuit_breaker.py

Cross-platform. Self-verifies with asserts.
"""

import threading
import time

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when the breaker is OPEN and the call is rejected (fail-fast)."""


class CircuitBreaker:
    def __init__(self, fail_threshold: int, cooldown: float, success_threshold: int = 1):
        self.fail_threshold = fail_threshold      # consecutive failures to trip
        self.cooldown = cooldown                  # seconds to stay OPEN
        self.success_threshold = success_threshold  # successes in HALF_OPEN to close
        self._lock = threading.Lock()
        self._state = CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = 0.0
        # Counters for observability (every real breaker exports these as metrics).
        self.rejected_calls = 0

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def call(self, func, *args, **kwargs):
        """Invoke `func` through the breaker. Raises CircuitOpenError if OPEN."""
        # --- Admission check (under lock): may we attempt the call at all? ---
        with self._lock:
            if self._state == OPEN:
                # Has the cooldown elapsed? If so, allow ONE probe (HALF_OPEN).
                if time.monotonic() - self._opened_at >= self.cooldown:
                    self._state = HALF_OPEN
                    self._successes = 0
                else:
                    self.rejected_calls += 1
                    raise CircuitOpenError("circuit is OPEN; failing fast")

        # --- The actual downstream call happens OUTSIDE the lock, so a slow
        #     call does not block other threads' admission checks. ---
        try:
            result = func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        else:
            self._on_success()
            return result

    def _on_success(self) -> None:
        with self._lock:
            if self._state == HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    # Probe(s) succeeded => downstream recovered. Close up.
                    self._state = CLOSED
                    self._failures = 0
            else:  # CLOSED
                self._failures = 0  # reset the consecutive-failure counter

    def _on_failure(self) -> None:
        with self._lock:
            if self._state == HALF_OPEN:
                # Probe failed => still broken. Re-open and restart cooldown.
                self._state = OPEN
                self._opened_at = time.monotonic()
                return
            self._failures += 1
            if self._failures >= self.fail_threshold:
                self._state = OPEN
                self._opened_at = time.monotonic()


# ---------------------------------------------------------------------------
# A simulated flaky downstream we can flip between "broken" and "healthy".
# ---------------------------------------------------------------------------
class FlakyDependency:
    def __init__(self):
        self.healthy = False

    def call(self) -> str:
        if not self.healthy:
            raise RuntimeError("downstream timeout")
        return "ok"


def main() -> None:
    dep = FlakyDependency()  # starts broken
    breaker = CircuitBreaker(fail_threshold=3, cooldown=0.5, success_threshold=2)

    print("Phase 1: downstream is BROKEN. Expect breaker to trip to OPEN.")
    failures = 0
    rejections = 0
    for i in range(8):
        try:
            breaker.call(dep.call)
        except CircuitOpenError:
            rejections += 1
            print(f"  call {i}: REJECTED fast (state={breaker.state})")
        except RuntimeError:
            failures += 1
            print(f"  call {i}: downstream failed (state={breaker.state})")
    # 3 real failures trip it; the remaining 5 calls fail fast.
    assert breaker.state == OPEN, f"expected OPEN, got {breaker.state}"
    assert failures == 3, f"expected 3 real failures before tripping, got {failures}"
    assert rejections == 5, f"expected 5 fast rejections, got {rejections}"
    print(f"  Breaker tripped after {failures} failures; {rejections} calls failed fast.")

    print("Phase 2: wait out the cooldown, downstream RECOVERS.")
    dep.healthy = True
    time.sleep(0.55)  # let cooldown expire

    # First call after cooldown -> HALF_OPEN probe. success_threshold=2 means we
    # need two successes to fully close.
    breaker.call(dep.call)
    assert breaker.state == HALF_OPEN, f"expected HALF_OPEN after first probe, got {breaker.state}"
    print(f"  First probe succeeded (state={breaker.state}).")
    breaker.call(dep.call)
    assert breaker.state == CLOSED, f"expected CLOSED after 2 successes, got {breaker.state}"
    print(f"  Second probe succeeded => circuit CLOSED (recovered).")

    print("Phase 3: confirm normal calls flow while CLOSED.")
    for _ in range(5):
        assert breaker.call(dep.call) == "ok"
    assert breaker.state == CLOSED
    print("  5 calls succeeded normally.")

    print("All circuit-breaker assertions passed: tripped, failed fast, and recovered.")


if __name__ == "__main__":
    main()
