"""
rate_limiter.py — Token-bucket and sliding-window rate limiters, thread-safe.

ENTERPRISE PROBLEM
------------------
Every API gateway, every public endpoint, every client calling a third-party
service needs throttling. Without it:
  * A buggy or malicious client can saturate your service (no fairness).
  * You blow past a downstream's quota and get your whole fleet banned.
  * A traffic spike turns into a cascading failure instead of clean 429s.

Two algorithms dominate production systems:

1. TOKEN BUCKET (used by AWS API Gateway, nginx limit_req, Stripe, Envoy):
   A bucket holds up to `capacity` tokens and refills at `rate` tokens/sec.
   Each request costs one token. If a token is available, the request is
   allowed; otherwise it is denied. This ALLOWS BURSTS up to the bucket size
   while enforcing a long-run average rate — usually what you actually want.

2. SLIDING-WINDOW LOG: record the timestamp of each allowed request; a new
   request is allowed only if fewer than `limit` requests occurred in the last
   `window` seconds. This is precise (no boundary spikes like fixed windows)
   but costs O(requests) memory. Good for strict per-window quotas.

Both must be THREAD-SAFE: a real gateway calls them from many request threads
at once, so the check-and-decrement must be atomic (guarded by a lock).

RELATED OS CONCEPT DOC: ../04_concurrency_synchronization.md (locks/atomicity),
                        ../02_cpu_scheduling.md (fairness & admission control).

HOW TO RUN
----------
    py rate_limiter.py

Cross-platform. Self-verifies with asserts.
"""

import collections
import threading
import time


class TokenBucket:
    """Thread-safe token bucket. Allows bursts up to `capacity`, refills lazily."""

    def __init__(self, rate: float, capacity: float):
        self.rate = rate          # tokens added per second
        self.capacity = capacity  # max tokens (burst size)
        self._tokens = float(capacity)  # start full
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Add tokens proportional to elapsed time. Caller must hold the lock."""
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        # Lazy refill: we don't run a timer thread; we compute how many tokens
        # *would* have accrued since the last call and cap at capacity.
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def allow(self, cost: float = 1.0) -> bool:
        """Atomically try to consume `cost` tokens. True if allowed."""
        with self._lock:
            self._refill()
            if self._tokens >= cost:
                self._tokens -= cost
                return True
            return False


class SlidingWindowLog:
    """Thread-safe sliding-window limiter: <= `limit` events per `window` seconds."""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._events: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window
            # Evict timestamps that have slid out of the window.
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()
            if len(self._events) < self.limit:
                self._events.append(now)
                return True
            return False


def demo_token_bucket() -> None:
    print("--- Token bucket: rate=10/s, capacity=10 (burst) ---")
    # Fresh bucket starts full, so the first 10 requests should pass instantly
    # (the burst), then further requests are denied until tokens refill.
    tb = TokenBucket(rate=10, capacity=10)
    allowed = sum(tb.allow() for _ in range(20))
    print(f"  Burst of 20 instant requests => {allowed} allowed (expected ~10 = capacity)")
    assert allowed == 10, f"expected 10 burst tokens, got {allowed}"

    # After waiting 0.5s at 10 tokens/s, ~5 tokens should have refilled.
    time.sleep(0.5)
    refilled = sum(tb.allow() for _ in range(20))
    print(f"  After 0.5s wait => {refilled} allowed (expected ~5 from refill)")
    assert 3 <= refilled <= 7, f"refill out of expected range: {refilled}"
    print("  Token bucket assertions passed.")


def demo_sliding_window() -> None:
    print("--- Sliding window: limit=5 per 0.5s ---")
    sw = SlidingWindowLog(limit=5, window=0.5)
    first = sum(sw.allow() for _ in range(10))
    print(f"  10 instant requests => {first} allowed (expected 5 = limit)")
    assert first == 5, f"expected 5, got {first}"

    # Wait for the window to fully slide; all old events expire and we get a
    # fresh quota of 5.
    time.sleep(0.55)
    second = sum(sw.allow() for _ in range(10))
    print(f"  After window slides => {second} allowed (expected 5 again)")
    assert second == 5, f"expected 5 after slide, got {second}"
    print("  Sliding window assertions passed.")


def demo_thread_safety() -> None:
    """Hammer a bucket from many threads; the allowed count must never exceed
    capacity even under heavy contention (proves the lock makes it atomic)."""
    print("--- Thread-safety: 50 threads hammering a capacity=100 bucket ---")
    tb = TokenBucket(rate=0, capacity=100)  # rate=0 => no refill during the test
    allowed_count = 0
    count_lock = threading.Lock()

    def worker():
        nonlocal allowed_count
        for _ in range(100):
            if tb.allow():
                with count_lock:
                    allowed_count += 1

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"  50*100=5000 attempts => {allowed_count} allowed (must be exactly 100)")
    # Without a lock, two threads could both see tokens>0 and double-spend,
    # pushing this above 100. The lock guarantees exactly capacity.
    assert allowed_count == 100, f"race condition! allowed {allowed_count}, expected 100"
    print("  Thread-safety assertion passed: no token was double-spent.")


def main() -> None:
    demo_token_bucket()
    demo_sliding_window()
    demo_thread_safety()
    print("All rate-limiter assertions passed.")


if __name__ == "__main__":
    main()
