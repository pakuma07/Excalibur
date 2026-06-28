"""
token_bucket_ratelimiter.py — Edge rate limiting + HMAC request signing & replay prevention

ENTERPRISE PROBLEM
------------------
The API gateway / edge is where you protect everything behind it. Two patterns
live here and this script implements both, runnably:

  1. RATE LIMITING with a TOKEN BUCKET. Every client gets a bucket that refills at
     a steady rate (the sustained allowed rate) up to a maximum capacity (the
     allowed burst). Each request costs one token; no token => rejected (HTTP
     429). This is the algorithm AWS API Gateway, Stripe, NGINX `limit_req`, and
     Envoy use, because it cleanly separates "average rate" from "burst size" —
     unlike a fixed window, which lets 2x burst across the window boundary. A
     leaky/token bucket smooths traffic and is trivial to compute in O(1) per
     request with just a timestamp and a token count.

  2. REQUEST SIGNING + REPLAY PREVENTION with HMAC. How does the gateway trust a
     request without a TLS client cert? The client signs the request
     (method + path + body + timestamp + nonce) with a shared secret using HMAC
     (the core of AWS SigV4, webhook signatures from Stripe/GitHub, etc.). The
     server recomputes the HMAC with the same secret and compares in CONSTANT
     TIME (hmac.compare_digest — never `==`, which leaks timing). To stop replay
     attacks (an attacker re-sending a captured valid request), the server also:
       * rejects requests whose timestamp is outside a small clock-skew window, and
       * remembers recently-seen nonces and rejects duplicates.

This script exercises both: it drives a bucket past its limit and asserts the
right requests are dropped, then signs/verifies requests and asserts that
tampered, expired, and replayed requests are all rejected.

HOW TO RUN
----------
    py token_bucket_ratelimiter.py

Cross-platform: pure stdlib (time, hmac, hashlib, secrets). No network needed.
"""

import hashlib
import hmac
import secrets
import time


# --------------------------------------------------------------------------
# Token bucket rate limiter.
# --------------------------------------------------------------------------
class TokenBucket:
    def __init__(self, rate_per_sec, capacity, now=None):
        self.rate = float(rate_per_sec)    # tokens added per second (sustained)
        self.capacity = float(capacity)    # max tokens (the allowed burst)
        self.tokens = float(capacity)      # start full
        self.last = (now if now is not None else time.monotonic())

    def _refill(self, now):
        # Add tokens for the elapsed time, capped at capacity. O(1), no timers.
        elapsed = now - self.last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last = now

    def allow(self, cost=1, now=None):
        """Return True and consume `cost` tokens if available, else False."""
        now = now if now is not None else time.monotonic()
        self._refill(now)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# --------------------------------------------------------------------------
# HMAC request signing + replay prevention.
# --------------------------------------------------------------------------
class RequestSigner:
    """Client side: produce a signature over the canonical request."""

    def __init__(self, secret):
        self.secret = secret

    @staticmethod
    def _canonical(method, path, body, timestamp, nonce):
        # A fixed, unambiguous serialization both sides agree on. Order and
        # separators matter — any difference changes the HMAC.
        return "\n".join([
            method.upper(), path, str(timestamp), nonce,
            hashlib.sha256(body).hexdigest(),
        ]).encode("utf-8")

    def sign(self, method, path, body=b"", timestamp=None, nonce=None):
        timestamp = int(timestamp if timestamp is not None else time.time())
        nonce = nonce or secrets.token_hex(16)
        msg = self._canonical(method, path, body, timestamp, nonce)
        signature = hmac.new(self.secret, msg, hashlib.sha256).hexdigest()
        return {"timestamp": timestamp, "nonce": nonce, "signature": signature}


class SignatureVerifier:
    """Server side: verify signature, freshness, and non-replay."""

    def __init__(self, secret, max_skew_sec=30, nonce_ttl_sec=300):
        self.secret = secret
        self.max_skew = max_skew_sec
        self.nonce_ttl = nonce_ttl_sec
        self._seen = {}        # nonce -> expiry time

    def _gc(self, now):
        expired = [n for n, exp in self._seen.items() if exp <= now]
        for n in expired:
            del self._seen[n]

    def verify(self, method, path, body, headers, now=None):
        """Return (ok, reason). ok=True only if all checks pass."""
        now = int(now if now is not None else time.time())
        ts = int(headers["timestamp"])
        nonce = headers["nonce"]
        provided = headers["signature"]

        # 1) Freshness: reject requests too far from our clock (replay window).
        if abs(now - ts) > self.max_skew:
            return False, "stale-timestamp"

        # 2) Integrity/authenticity FIRST: recompute HMAC, compare in constant
        #    time. We verify the signature BEFORE touching the nonce store so a
        #    forged/tampered request is reported as bad-signature and never gets
        #    a chance to consume or poison a nonce slot.
        msg = RequestSigner._canonical(method, path, body, ts, nonce)
        expected = hmac.new(self.secret, msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            return False, "bad-signature"

        # 3) Replay: only now (signature is valid) check the nonce. Reject a
        #    nonce we've already accepted within its TTL.
        self._gc(now)
        if nonce in self._seen:
            return False, "replayed-nonce"

        # Accept: remember the nonce so it can't be replayed.
        self._seen[nonce] = now + self.nonce_ttl
        return True, "ok"


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("Token-bucket rate limiter + HMAC signing / replay prevention")
    print("=" * 70)

    # ---- 1) Token bucket: rate 5/s, burst 10 ----
    print("\n[1] Token bucket (rate=5/s, capacity=10)")
    # Use a virtual clock so the test is deterministic (no real sleeping).
    t = 1000.0
    bucket = TokenBucket(rate_per_sec=5, capacity=10, now=t)

    # Burst of 15 at the same instant: only the 10 in the bucket should pass.
    allowed = sum(1 for _ in range(15) if bucket.allow(now=t))
    print(f"    burst of 15 at t=0  -> {allowed} allowed, {15-allowed} rejected")
    assert allowed == 10, allowed

    # After 1 second, the bucket refilled 5 tokens => 5 more should pass.
    t += 1.0
    allowed2 = sum(1 for _ in range(15) if bucket.allow(now=t))
    print(f"    burst of 15 at t=1s -> {allowed2} allowed (refilled 5/s)")
    assert allowed2 == 5, allowed2

    # Sustained rate: over 10 virtual seconds at 5/s, ~50 requests pass.
    t2 = 2000.0
    b2 = TokenBucket(rate_per_sec=5, capacity=10, now=t2)
    passed = 0
    for step in range(100):                # 100 attempts over 10s (every 0.1s)
        t2 += 0.1
        if b2.allow(now=t2):
            passed += 1
    print(f"    100 attempts over 10s @5/s -> {passed} passed")
    # Expect ~capacity start drain isn't full here; sustained ~5/s*10s=50, plus
    # whatever was banked. Allow a tolerance band.
    assert 50 <= passed <= 60, passed
    print("    rate + burst behaviour verified ✓")

    # ---- 2) HMAC signing + verification (happy path) ----
    print("\n[2] HMAC request signing")
    secret = secrets.token_bytes(32)
    signer = RequestSigner(secret)
    verifier = SignatureVerifier(secret, max_skew_sec=30, nonce_ttl_sec=300)

    now = 5_000_000
    body = b'{"amount": 100}'
    hdrs = signer.sign("POST", "/v1/charge", body, timestamp=now)
    ok, reason = verifier.verify("POST", "/v1/charge", body, hdrs, now=now)
    print(f"    valid signed request           -> ok={ok} ({reason})")
    assert ok and reason == "ok"

    # ---- 3) Tampered body is rejected ----
    ok, reason = verifier.verify("POST", "/v1/charge", b'{"amount": 999999}',
                                 hdrs, now=now)
    print(f"    tampered body                  -> ok={ok} ({reason})")
    assert not ok and reason == "bad-signature"

    # ---- 4) Replay of the SAME request is rejected ----
    # (the first verify above already consumed this nonce)
    ok, reason = verifier.verify("POST", "/v1/charge", body, hdrs, now=now)
    print(f"    replayed (same nonce)          -> ok={ok} ({reason})")
    assert not ok and reason == "replayed-nonce"

    # ---- 5) Expired timestamp (outside skew window) is rejected ----
    old = signer.sign("POST", "/v1/charge", body, timestamp=now - 120)
    ok, reason = verifier.verify("POST", "/v1/charge", body, old, now=now)
    print(f"    stale timestamp (120s old)     -> ok={ok} ({reason})")
    assert not ok and reason == "stale-timestamp"

    # ---- 6) Wrong secret (forged) is rejected ----
    attacker = RequestSigner(secrets.token_bytes(32))
    forged = attacker.sign("POST", "/v1/charge", body, timestamp=now)
    ok, reason = verifier.verify("POST", "/v1/charge", body, forged, now=now)
    print(f"    forged with wrong secret       -> ok={ok} ({reason})")
    assert not ok and reason == "bad-signature"

    print("\nAll assertions passed. Limiter throttles; signer blocks tamper/"
          "replay/expiry/forgery. ✓")
