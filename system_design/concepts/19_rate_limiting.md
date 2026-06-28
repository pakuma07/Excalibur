# Rate Limiting

## Introduction

**Rate limiting** controls how many operations a client may perform in a given window of time. It is the mechanism behind "API allows 100 requests per minute" or "5 login attempts per hour." A rate limiter decides, for each incoming request: **allow it, or reject it (usually with HTTP 429).**

It is one of the most important reliability and security primitives in distributed systems — cheap to add, and the difference between a service that degrades gracefully under load and one that falls over.

---

## Why Rate Limit?

| Goal | What rate limiting prevents |
| --- | --- |
| **Stability / availability** | A single buggy or aggressive client exhausting CPU, memory, DB connections, or downstream quotas |
| **Fairness** | One tenant starving others on shared infrastructure ("noisy neighbor") |
| **Cost control** | Runaway usage of metered downstream services (third-party APIs, LLM tokens, egress) |
| **Security** | Brute-force credential stuffing, scraping, and basic DoS mitigation |
| **Business tiers** | Enforcing plan limits (free = 100/day, pro = 100k/day) |

> **Mental model:** A rate limiter is a *budget* attached to a *key* (user, API key, IP, endpoint) that *refills over time*. Every algorithm below is just a different policy for tracking and refilling that budget.

---

## Client-Side vs Server-Side

| | Client-side | Server-side |
| --- | --- | --- |
| **Who enforces** | The caller throttles itself | The service rejects excess |
| **Purpose** | Politeness, smoothing bursts, respecting `Retry-After` | Protection — the only enforcement you can *trust* |
| **Trust** | Cannot be relied upon (clients can be buggy or malicious) | Authoritative |
| **Example** | An SDK that paces requests; honoring `Retry-After` on 429 | An API gateway returning 429 |

**Rule:** Client-side limiting is a courtesy and an optimization. Server-side limiting is the actual control. You generally want both — well-behaved clients reduce wasted traffic, but the server must never depend on them.

## Where to Place It

```
        Client
          |
   [ CDN / WAF ]        <- coarse IP-level DDoS limits
          |
  [ API Gateway ]       <- PRIMARY place: per-API-key/user/route limits, central policy
          |
   Load Balancer
          |
  [ Service A ][ Service B ]   <- fine-grained, resource-specific limits (e.g. per-DB)
          |
     [ Database ]
```

The **API gateway** (or a dedicated edge layer) is the canonical place: it sees every request, knows the authenticated identity, and can reject cheaply *before* requests reach expensive backend services. Individual services may add their own limits for resources only they understand. Limiting at the edge protects everything behind it.

---

## The Algorithms

We'll implement five. Each `RateLimiter.allow(key)` returns `True` (allowed) or `False` (rejected). For clarity these are single-process implementations using wall-clock time; the distributed Redis versions follow.

### 1. Token Bucket

**Idea:** A bucket holds up to `capacity` tokens and refills at a constant `refill_rate` tokens/second. Each request costs one token. If a token is available, consume it and allow; otherwise reject. Because tokens accumulate up to `capacity`, the bucket **allows short bursts** up to its capacity while enforcing the average rate over time.

```
capacity = 5, refill = 1 token/sec
            ____________
tokens:    | * * * * * |  full -> can burst 5 immediately
           |___________|
After burst of 5: empty. Then 1 token returns each second.
```

```python
import time


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity          # max tokens (max burst size)
        self.refill_rate = refill_rate    # tokens added per second
        self.tokens = float(capacity)     # start full
        self.last = time.monotonic()

    def allow(self, cost: int = 1) -> bool:
        now = time.monotonic()
        # Lazily add tokens accrued since the last call, capped at capacity.
        elapsed = now - self.last
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last = now

        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


if __name__ == "__main__":
    tb = TokenBucket(capacity=5, refill_rate=2)  # 2/sec, burst up to 5
    allowed = sum(tb.allow() for _ in range(8))
    print(f"Immediate burst of 8: {allowed} allowed (expect 5)")
    time.sleep(1.0)
    print(f"After 1s refill: allowed={tb.allow()} (expect True, ~2 tokens back)")
```

**Trade-offs:** Smooth average rate **with controlled bursts**, O(1) time and memory per key. The most popular general-purpose choice (used by AWS, Stripe, NGINX `limit_req`). Bursts can briefly exceed the steady rate, which is usually desirable.

### 2. Leaky Bucket

**Idea:** Requests enter a fixed-size queue (the bucket) and "leak out" — i.e., are processed — at a constant rate. If the bucket is full, new requests overflow and are rejected. Unlike token bucket, it **smooths output to a constant rate** and does not allow bursts to pass through to the backend.

```
requests in (bursty)
      | | || |
      v v vv v
   [___________]  bucket (queue), capacity = N
        |
        v  leaks at constant rate
   steady output -> backend
```

```python
import time


class LeakyBucket:
    def __init__(self, capacity: int, leak_rate: float):
        self.capacity = capacity        # max queued requests
        self.leak_rate = leak_rate      # requests drained per second
        self.water = 0.0                # current queue level
        self.last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        # Leak out whatever has drained since the last check.
        elapsed = now - self.last
        self.water = max(0.0, self.water - elapsed * self.leak_rate)
        self.last = now

        if self.water + 1 <= self.capacity:
            self.water += 1   # admit request into the bucket
            return True
        return False          # bucket full -> overflow -> reject


if __name__ == "__main__":
    lb = LeakyBucket(capacity=5, leak_rate=2)
    allowed = sum(lb.allow() for _ in range(8))
    print(f"Burst of 8 into leaky bucket: {allowed} allowed (expect 5)")
```

**Trade-offs:** Produces a **perfectly smooth** outflow — ideal when the downstream needs a steady, predictable load (e.g., a payment processor). The cost is added latency for queued requests and no burst tolerance. Token bucket and leaky bucket are duals: token bucket limits the *input* allowing bursts; leaky bucket shapes the *output* to a constant rate.

### 3. Fixed Window Counter

**Idea:** Divide time into fixed windows (e.g., each calendar minute). Keep one counter per window per key. Increment on each request; reject once the counter exceeds the limit. At the window boundary the counter resets to zero.

```
limit = 100/min
| window 12:00:00-12:00:59 | window 12:01:00-12:01:59 |
| count up to 100          | resets to 0              |
```

```python
import time


class FixedWindowCounter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self.count = 0
        self.window_start = self._current_window()

    def _current_window(self) -> int:
        return int(time.time() // self.window)

    def allow(self) -> bool:
        w = self._current_window()
        if w != self.window_start:   # new window -> reset
            self.window_start = w
            self.count = 0
        if self.count < self.limit:
            self.count += 1
            return True
        return False
```

**Trade-offs:** Trivial to implement and extremely cheap (one integer per key). **Major flaw: the boundary burst.** A client can send `limit` requests in the last second of one window and `limit` more in the first second of the next — `2 * limit` requests in a ~2-second span, double the intended rate. Good enough for coarse limits; the sliding-window algorithms below fix this.

### 4. Sliding Window Log

**Idea:** Store the **timestamp of every request** in a log. To decide a new request, discard timestamps older than the window, then count what remains. Allow if the count is below the limit. This is the **most accurate** method — it enforces the limit over any rolling window with no boundary artifact.

```
window = 60s, now = 12:00:30
log: [12:00:01, 12:00:15, 12:00:28, ...]  (drop anything < 11:59:30)
allow if len(log_within_window) < limit
```

```python
import time
from collections import deque


class SlidingWindowLog:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self.log = deque()  # request timestamps, oldest at left

    def allow(self) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        # Evict timestamps that have aged out of the window.
        while self.log and self.log[0] <= cutoff:
            self.log.popleft()

        if len(self.log) < self.limit:
            self.log.append(now)
            return True
        return False
```

**Trade-offs:** Perfectly accurate, no boundary spikes. **Cost: memory grows with the number of requests in the window** — storing one timestamp per request is expensive for high limits and many keys. Use when precision matters and request volume per key is modest.

### 5. Sliding Window Counter

**Idea:** A pragmatic hybrid that approximates the sliding window log using only two fixed-window counters. Keep the current window's count and the previous window's count, then **weight the previous window by how much of it still overlaps** the rolling window:

```
estimate = current_count + previous_count * (fraction of previous window still in view)
```

```
limit = 100/min, now is 30% into the current minute (so 70% of the
previous minute still falls within the trailing 60s):
estimate = current_count + previous_count * 0.70
```

```python
import time


class SlidingWindowCounter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self.cur_window = self._win()
        self.cur_count = 0
        self.prev_count = 0

    def _win(self) -> int:
        return int(time.time() // self.window)

    def allow(self) -> bool:
        w = self._win()
        if w == self.cur_window + 1:
            # Advanced exactly one window: yesterday's current becomes previous.
            self.prev_count, self.cur_count = self.cur_count, 0
            self.cur_window = w
        elif w > self.cur_window:
            # Skipped one or more empty windows.
            self.prev_count, self.cur_count = 0, 0
            self.cur_window = w

        elapsed_in_window = (time.time() % self.window) / self.window
        weight = 1.0 - elapsed_in_window  # fraction of previous window still in view
        estimate = self.cur_count + self.prev_count * weight

        if estimate < self.limit:
            self.cur_count += 1
            return True
        return False
```

**Trade-offs:** Near the accuracy of the log but with **O(1) memory** (two counters). Smooths the fixed-window boundary burst. The weighting assumes requests were spread evenly in the previous window, so it's an approximation — but a very good one, which is why it is the **industry favorite** (Cloudflare popularized it).

---

## Distributed Rate Limiting with Redis

The single-process limiters above break the moment you run **multiple instances** behind a load balancer — each replica has its own counter, so the effective limit multiplies by the number of replicas. The fix is a **shared, centralized store**, typically Redis, holding the counters all instances read and write.

### Simple approach: INCR + EXPIRE (fixed window)

```python
import redis

r = redis.Redis()

def allow_fixed_window(key: str, limit: int, window_seconds: int) -> bool:
    # One Redis key per (client, window). E.g. "rl:user42:1718900000".
    bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
    pipe = r.pipeline()
    pipe.incr(bucket)
    pipe.expire(bucket, window_seconds)  # auto-clean stale windows
    count, _ = pipe.execute()
    return count <= limit
```

This is simple but has a subtle race: between `INCR` and `EXPIRE`, a crash could leave a key without a TTL. The atomic Lua version avoids that and supports a proper token bucket.

### Atomic approach: Lua script (token bucket)

A Lua script runs **atomically** inside Redis — read-modify-write with no race between clients. This is the production-grade pattern.

```python
import redis
import time

TOKEN_BUCKET_LUA = """
-- KEYS[1] = bucket key
-- ARGV[1] = capacity, ARGV[2] = refill_rate (tokens/sec)
-- ARGV[3] = now (seconds, float), ARGV[4] = requested tokens
local capacity    = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now         = tonumber(ARGV[3])
local requested   = tonumber(ARGV[4])

local data   = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

-- Refill based on elapsed time, capped at capacity.
local delta = math.max(0, now - ts)
tokens = math.min(capacity, tokens + delta * refill_rate)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
-- TTL so idle keys are reclaimed (time to fully refill + margin).
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / refill_rate) + 1)
return allowed
"""

r = redis.Redis()
_script = r.register_script(TOKEN_BUCKET_LUA)

def allow_token_bucket(key: str, capacity: int, refill_rate: float, cost: int = 1) -> bool:
    return bool(_script(keys=[f"rl:{key}"],
                        args=[capacity, refill_rate, time.time(), cost]))
```

**Why Lua:** it collapses the read, compute, and write into one atomic round-trip, eliminating both the lost-update race and the INCR/EXPIRE gap. Trade-off: a centralized store adds latency (a network hop per request) and is itself a dependency you must make highly available. Many systems run a local in-memory limiter as a first pass and a Redis limiter for cross-instance correctness.

---

## Response Headers and Status Codes

When you reject a request, communicate clearly so well-behaved clients can back off.

- **`429 Too Many Requests`** — the standard status code for a rate-limited request.
- **`Retry-After`** — seconds (or an HTTP date) the client should wait before retrying.
- **`RateLimit-Limit` / `RateLimit-Remaining` / `RateLimit-Reset`** — the (IETF draft, widely used) headers telling the client its budget and when it refills. Older APIs use `X-RateLimit-*`.

```python
def make_response(allowed: bool, limit: int, remaining: int, reset_seconds: int):
    headers = {
        "RateLimit-Limit": str(limit),
        "RateLimit-Remaining": str(max(0, remaining)),
        "RateLimit-Reset": str(reset_seconds),  # seconds until window resets
    }
    if allowed:
        return 200, headers, "OK"
    headers["Retry-After"] = str(reset_seconds)
    return 429, headers, "Too Many Requests"
```

**Client side:** on a 429, honor `Retry-After`; use **exponential backoff with jitter** for retries to avoid synchronized retry storms (the "thundering herd").

---

## Comparison Table

| Algorithm | Allows bursts? | Output shape | Memory per key | Accuracy | Boundary spike? | Best for |
| --- | --- | --- | --- | --- | --- | --- |
| **Token bucket** | Yes (up to capacity) | Bursty within limit | O(1) | High | No | General-purpose APIs; the default choice |
| **Leaky bucket** | No | Perfectly smooth | O(1) + queue | High | No | Steady downstream load (payments, hardware) |
| **Fixed window** | At boundaries | Spiky | O(1) | Low | **Yes** | Simple, coarse limits |
| **Sliding window log** | No | Smooth | **O(n) requests** | Highest | No | Precise limits, low volume |
| **Sliding window counter** | Mild | Smooth | O(1) (2 counters) | High (approx) | No | High-scale APIs; best balance |

---

## Key Takeaways

- **Rate limiting protects availability, ensures fairness, controls cost, and blunts abuse.** It is a foundational reliability and security control.
- **Server-side enforcement is the only one you can trust;** client-side throttling (honoring `Retry-After`, backoff with jitter) is a valuable courtesy on top.
- **The API gateway/edge is the primary placement** — it sees every authenticated request and rejects cheaply before backends do expensive work.
- **Token bucket is the sensible default** (smooth average rate with controlled bursts). **Leaky bucket** when you need perfectly steady downstream load.
- **Avoid plain fixed-window counters** for anything precise — the boundary burst lets clients hit ~2x the limit. **Sliding window counter** fixes this with O(1) memory and is the high-scale favorite; **sliding window log** is the most accurate but memory-hungry.
- **In a multi-instance deployment you must centralize state** (e.g., Redis) or your effective limit multiplies by the replica count. Use an **atomic Lua script** to avoid read-modify-write races.
- **Always return `429` with `Retry-After` and `RateLimit-*` headers** so good clients can self-correct.
