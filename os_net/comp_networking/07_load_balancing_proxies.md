# 07 — Load Balancing, Proxies & Edge

> **Audience:** staff/principal. You've put a load balancer in front of a service. This doc is about how proxies and load balancers *actually* distribute traffic — L4 vs L7, the balancing algorithms and their failure modes, health checking and the thundering-herd on failover, connection draining, TLS termination vs passthrough — up through GSLB/anycast/CDNs at the global edge and the service mesh. The goal is to reason about *where* to put intelligence in the request path and what each placement costs.
>
> **Primary sources:** Grigorik, *High Performance Browser Networking*; NGINX, HAProxy, and Envoy documentation; Google's *Maglev* paper (Eisenbud et al., NSDI 2016); Mitzenmacher, *The Power of Two Choices in Randomized Load Balancing* (2001); Karger et al., *Consistent Hashing* (1997); Cloudflare and Fastly engineering blogs; RFC 9110/9111 (HTTP); the Envoy and Istio architecture docs.

---

## 1. Why this matters at scale

A single server can't serve a real workload — you need many, and something must spread traffic across them, route around failures, and present one address to the world. That "something" is a **load balancer / proxy**, and it sits in the hot path of *every* request. Three consequences follow:

1. **It's the availability chokepoint and the availability *guarantor* simultaneously.** It's the one box every request crosses (so it must not be a SPOF), but it's also what makes any individual backend disposable — drain it, deploy, fail it out, and users never notice. Get balancing/health-checking right and a backend dying is a non-event; get it wrong and one slow backend (or one failover) takes everything down via a thundering herd.
2. **Where you terminate TLS and parse HTTP decides what you *can* do.** A pure transport (L4) balancer is fast and protocol-agnostic but blind to URLs, cookies, and headers. An application (L7) balancer can route by path, retry, rate-limit, and rewrite — but it must terminate TLS and parse every request, costing CPU and latency. This single choice shapes your whole edge.
3. **The edge is where you shed load.** Rate limiting, circuit breaking, caching, and geo-routing belong as far from your origin as possible — at the CDN/edge — so abuse and overload die before they reach a backend.

```
        clients
           |
        [ DNS / GSLB / anycast ]     <- global: pick a region/POP  (§8)
           |
        [ CDN / edge POP ]           <- cache, rate-limit, TLS, WAF (§8, §10)
           |
        [ L4 LB ]  (ECMP/Maglev)     <- fast, connection-level     (§2, §3)
           |
        [ L7 LB / reverse proxy ]    <- route by path/header, retry (§2, §6)
           |
        [ backends ]  (+ service mesh sidecars for east-west)       (§9)
```

---

## 2. Proxies: forward vs reverse, L4 vs L7

### 2.1 Forward vs reverse proxy

```
FORWARD proxy (acts for the CLIENT):
   client --> [forward proxy] --> any server on the Internet
   (corporate egress, content filtering, caching, anonymity)
   The proxy knows the client; the origin sees the proxy.

REVERSE proxy (acts for the SERVER):
   client --> [reverse proxy] --> your backend pool
   (load balancing, TLS termination, caching, WAF, routing)
   The client thinks the proxy IS the server; backends are hidden.
```

A **load balancer is a reverse proxy** whose primary job is distributing across a pool. NGINX, HAProxy, and Envoy are all reverse proxies; CDNs are globally distributed reverse proxies.

### 2.2 L4 vs L7 — the defining distinction

| | **L4 (transport) LB** | **L7 (application) LB** |
|---|---|---|
| Operates on | TCP/UDP connections (IP:port, 4-tuple) | HTTP requests (method, path, headers, cookies) |
| Sees the payload? | **No** — opaque bytes; can't read URLs | **Yes** — parses HTTP |
| TLS | Passes through (or terminates for visibility) | Usually **terminates** to read the request |
| Routing | By connection/flow only | By path, host, header, cookie, weight |
| Per-request decisions | No (one decision per connection) | **Yes** (every request can go elsewhere) |
| Retries / rewrites / cache | No | Yes |
| Cost | Very cheap; line-rate; millions of conns | CPU per request (parse + TLS); higher latency |
| Examples | IPVS, Maglev, AWS NLB, HAProxy `mode tcp` | NGINX, Envoy, HAProxy `mode http`, AWS ALB |

**What L4 can't do:** route `/api` to one pool and `/static` to another, retry a failed request on another backend, add/inspect headers, or do HTTP-aware health checks — because it never sees HTTP. **What L4 buys:** raw speed, protocol-agnosticism (any TCP/UDP service), and the ability to do TLS *passthrough* (§5) so it never holds your private keys.

> The common production shape is **both, layered**: an L4 layer (often anycast + Maglev/ECMP) spreads connections across many L7 proxies, and the L7 proxies do the smart per-request work. L4 gives scale and DDoS absorption; L7 gives intelligence.

---

## 3. Load-balancing algorithms

The core question: given a request/connection, which backend gets it? Each algorithm trades simplicity, evenness, and statefulness.

| Algorithm | How | Best for | Weakness |
|---|---|---|---|
| **Round-robin** | Next backend in rotation. | Uniform backends, uniform requests. | Ignores actual load; a slow/heavy request can pile on a busy backend. |
| **Weighted round-robin** | Rotation biased by capacity weights. | Heterogeneous backend sizes. | Static; weights don't track real-time load. |
| **Least-connections** | Send to the backend with fewest active connections. | Long-lived/variable-duration requests. | "Connections" ≠ "load" if requests differ wildly in cost. |
| **Least-response-time** | Fewest active conns *and* lowest observed latency. | Latency-sensitive, heterogeneous load. | Needs live latency tracking; can over-react to noise. |
| **Consistent hashing** | Hash key (client IP / session / URL) → ring → backend. | Affinity / sticky / cache locality. | Uneven without virtual nodes; key skew → hotspots. |
| **Power of two choices (P2C)** | Pick 2 backends at random, send to the less loaded. | Distributed LBs without global state. | Slightly worse than perfect, but near-optimal & cheap. |
| **Maglev hashing** | Consistent-hash variant with near-perfect even buckets + minimal disruption. | Stateless L4 LBs at huge scale. | More complex to build. |

### 3.1 Why "round-robin is fine" is usually wrong at scale

Round-robin is load-*oblivious*: it counts requests, not work. With variable request costs (a 2 ms cache hit vs a 2 s report), it routes a heavy request to a backend that just got three other heavy ones. **Least-connections** approximates load far better for variable workloads and is the sane default for most HTTP services.

### 3.2 Power of two choices (P2C): near-optimal with almost no state

A subtle, important result (Mitzenmacher 2001): instead of tracking global load to pick the *least*-loaded backend, pick **two backends at random and send to the less loaded of the two**. This reduces the maximum load from `O(log n / log log n)` (pure random) to `O(log log n)` — an *exponential* improvement — with essentially no coordination. It's why modern distributed LBs (Envoy, NGINX) use P2C ("least request" with two choices) rather than true global least-connections, which would require a synchronized global view that doesn't scale.

### 3.3 Consistent hashing: affinity without a state table

Plain `hash(key) % N` is catastrophic when `N` changes: adding/removing one backend remaps **almost every** key (every cache misses, every sticky session breaks). **Consistent hashing** (Karger 1997) places backends and keys on a ring (hash space); a key maps to the next backend clockwise. Adding/removing a backend only remaps the keys in **one arc** — `~K/N` keys, not all of them. **Virtual nodes** (many ring positions per backend) smooth out the uneven arc sizes. This is how you get cache locality and sticky routing that survives scaling events. **Maglev hashing** improves on it for L4 LBs by giving near-perfectly even buckets while keeping disruption minimal on backend changes. (Full ring implementation in §7.2.)

---

## 4. Health checks and the thundering herd

A load balancer must know which backends are alive, or it will faithfully route traffic into a black hole.

| Type | How | Pros / cons |
|---|---|---|
| **Active** | LB periodically probes (`GET /healthz`, TCP connect). | Detects failure *before* a user hits it; costs probe traffic; interval = detection lag. |
| **Passive** | LB observes real traffic; ejects a backend after N consecutive errors/timeouts (outlier detection). | Zero probe overhead, reacts to *real* failures; first failures hit users. |

Production uses **both**: active probes catch a dead backend proactively; passive ejection catches a backend that's *up but broken* (returning 500s, slow). Envoy calls passive ejection **outlier detection**.

**Health-check design traps:**
- **Shallow vs deep checks.** A shallow `/healthz` returning `200` if the process is up won't catch a backend whose database is unreachable. A deep check (verifies dependencies) catches more — but risks **correlated failure**: if the shared DB blips, *every* backend fails its deep check at once and the LB ejects the **entire pool** → total outage from a transient dependency hiccup. Mitigate with a "fail open if everything is unhealthy" rule (a.k.a. panic mode) and separate liveness from readiness.

### 4.1 The thundering herd on failover

When a backend dies, its load **redistributes onto the survivors instantly**. If the pool was running hot, the survivors now take a step-up in load they can't handle, so *they* fall over, redistributing onto an even smaller pool — a **cascading failure**.

```
   4 backends @ 70% each, one dies:
      load that was on D (70%) spreads over A,B,C
      -> each now ~93%  ... if that tips them, the cascade is on
```

This is also a *retry storm*: a failed request gets retried, and at the moment of failover everyone retries at once, multiplying load exactly when capacity dropped. Defenses: provision headroom (N+2, not N+1), **cap retries with budgets/jitter**, circuit breakers (§10), and *slow-start* a recovering backend rather than dumping full load on it instantly.

---

## 5. TLS termination vs passthrough

| | **TLS termination** | **TLS passthrough** |
|---|---|---|
| Where TLS ends | At the LB; backend gets plaintext (or re-encrypted) | At the backend; LB just forwards encrypted bytes |
| LB can read HTTP? | **Yes** → L7 routing, caching, WAF | **No** → L4 only |
| Holds private keys? | Yes (key management at the edge) | No (keys stay on backends) |
| Backend CPU | Offloaded (no TLS) | Pays TLS |
| Use when | You want L7 features, central cert mgmt | Compliance/E2E encryption; LB must not see plaintext |

- **Termination** is the norm: terminate at the edge, centralize certs, and optionally **re-encrypt** to the backend (`TLS-to-backend`) so the internal hop is still encrypted — the best of both for most shops.
- **Passthrough** is for end-to-end encryption requirements where even the LB must not see plaintext (regulated data, or the backend does mTLS client-auth itself). The cost is you lose all L7 capability at that LB.

---

## 6. The proxy zoo: NGINX, HAProxy, Envoy

| | **NGINX** | **HAProxy** | **Envoy** |
|---|---|---|---|
| Origin / sweet spot | Web server + reverse proxy + static + L7 | Pure, fast TCP/HTTP load balancer | Cloud-native L7 proxy / service-mesh data plane |
| Config | Static file, reload to change | Static file, reload (hitless reloads) | **Dynamic** via xDS APIs (hot config, no restart) |
| Strengths | Ubiquitous, static content, simple LB, caching | Extremely fast/efficient L4+L7, rich balancing & health checks, observability | Dynamic config, gRPC/HTTP2/3 first-class, rich filters, outlier detection, the mesh sidecar |
| Typical use | Edge web server / simple reverse proxy | Dedicated high-throughput LB tier | Kubernetes ingress + **service mesh** sidecar (Istio) |

**Rule of thumb:** NGINX for serving + simple reverse-proxying; HAProxy when load balancing *is* the job and you want maximum efficiency and control; Envoy when you need dynamic, API-driven config and service-mesh / east-west traffic (it was *built* for the dynamic, programmable, observable proxy role).

### 6.1 API gateways

An **API gateway** is an L7 reverse proxy specialized for APIs: it does authentication/authorization, rate limiting per consumer, request/response transformation, API-key/JWT validation, request aggregation, and per-route policy — concerns you don't want duplicated in every microservice. It's the *north-south* (client→system) policy enforcement point. (Distinct from the service mesh, which handles *east-west*, service→service — §9.)

---

## 7. Working code

### 7.1 An L7 reverse proxy with round-robin, least-connections, and health checks

A real, runnable threaded HTTP reverse proxy. It forwards requests to a pool of backends, supports round-robin and least-connections selection, runs active health checks in a background thread, and skips unhealthy backends. Includes a self-test that spins up in-process backends.

```python
"""
mini_lb.py — a runnable L7 (HTTP) reverse proxy / load balancer.

Features: round-robin & least-connections selection, active health checks,
unhealthy-backend ejection. Threaded; stdlib only. Run: python mini_lb.py
(the __main__ block starts 3 in-process backends + the LB and self-tests it).
"""
from __future__ import annotations
import http.client
import http.server
import itertools
import threading
import time
from urllib.parse import urlsplit


class Backend:
    def __init__(self, url: str):
        p = urlsplit(url)
        self.host = p.hostname
        self.port = p.port
        self.url = url
        self.healthy = True
        self.active = 0                      # in-flight requests (for least-conn)
        self.lock = threading.Lock()


class Pool:
    """Holds backends and the selection policy + active health checker."""
    def __init__(self, urls: list[str], policy: str = "round_robin"):
        self.backends = [Backend(u) for u in urls]
        self.policy = policy
        self._rr = itertools.cycle(range(len(self.backends)))
        self._rr_lock = threading.Lock()
        self._stop = threading.Event()
        self._hc = threading.Thread(target=self._health_loop, daemon=True)
        self._hc.start()

    def _health_loop(self, interval: float = 1.0, path: str = "/healthz"):
        while not self._stop.wait(interval):
            for b in self.backends:
                try:
                    c = http.client.HTTPConnection(b.host, b.port, timeout=1)
                    c.request("GET", path)
                    ok = c.getresponse().status == 200
                    c.close()
                except Exception:
                    ok = False
                b.healthy = ok               # active health check result

    def stop(self):
        self._stop.set()

    def pick(self) -> Backend | None:
        live = [b for b in self.backends if b.healthy]
        if not live:
            return None                      # all down -> caller returns 503
        if self.policy == "least_conn":
            # fewest in-flight requests wins (ties -> first)
            return min(live, key=lambda b: b.active)
        # round-robin over the FULL list, skipping unhealthy ones
        for _ in range(len(self.backends)):
            with self._rr_lock:
                idx = next(self._rr)
            b = self.backends[idx]
            if b.healthy:
                return b
        return live[0]


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    pool: Pool = None                        # injected before serving
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):               # quiet
        pass

    def do_GET(self):
        self._proxy("GET")

    def _proxy(self, method: str):
        backend = self.pool.pick()
        if backend is None:
            self.send_error(503, "no healthy backends")
            return
        with backend.lock:
            backend.active += 1              # track in-flight for least-conn
        try:
            conn = http.client.HTTPConnection(backend.host, backend.port, timeout=5)
            # Forward, stamping which backend served it (so the test can see balancing).
            conn.request(method, self.path, headers={"X-Forwarded-For":
                                                     self.client_address[0]})
            upstream = conn.getresponse()
            body = upstream.read()
            self.send_response(upstream.status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Upstream", backend.url)
            self.end_headers()
            self.wfile.write(body)
            conn.close()
        except Exception as e:
            self.send_error(502, f"bad gateway: {e}")
        finally:
            with backend.lock:
                backend.active -= 1


# ---------------- self-test: real backends + the LB, end to end ----------------
def _make_backend_server(name: str, port: int, healthy: bool = True):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path == "/healthz":
                self.send_response(200 if healthy else 500)
                self.end_headers(); self.wfile.write(b"ok"); return
            payload = name.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(port: int, path: str = "/"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    upstream = r.getheader("X-Upstream")
    c.close()
    return r.status, body.decode(), upstream


if __name__ == "__main__":
    # Backends 8101, 8102 healthy; 8103 unhealthy (/healthz returns 500).
    _make_backend_server("A", 8101, healthy=True)
    _make_backend_server("B", 8102, healthy=True)
    _make_backend_server("C", 8103, healthy=False)

    pool = Pool(["http://127.0.0.1:8101", "http://127.0.0.1:8102",
                 "http://127.0.0.1:8103"], policy="round_robin")
    ProxyHandler.pool = pool
    lb = http.server.ThreadingHTTPServer(("127.0.0.1", 8100), ProxyHandler)
    threading.Thread(target=lb.serve_forever, daemon=True).start()

    time.sleep(1.5)                          # let the health check run >= 1 cycle

    # Round-robin across the TWO healthy backends; C must be ejected.
    served = [_get(8100)[1] for _ in range(6)]
    print("round-robin servers:", served)
    assert set(served) == {"A", "B"}, f"unhealthy C must be skipped: {served}"
    assert served.count("A") == 3 and served.count("B") == 3, "even RR over healthy"

    # Switch to least-connections and confirm it still only uses healthy backends.
    # (With serialized requests, in-flight count returns to 0 between calls, so
    # least-conn deterministically prefers the first healthy backend -- never C.)
    pool.policy = "least_conn"
    served2 = {_get(8100)[1] for _ in range(6)}
    print("least-conn servers:", served2)
    assert served2 <= {"A", "B"} and "C" not in served2, "must never route to unhealthy C"

    pool.stop()
    print("OK: health checks ejected the bad backend; both policies balanced.")
```

### 7.2 A consistent-hashing ring

A runnable consistent-hash ring with virtual nodes, asserting the two properties that make it useful: keys map deterministically, and removing a node remaps only that node's share (not the whole keyspace).

```python
"""
consistent_hash.py — a consistent-hashing ring with virtual nodes.
Run: python consistent_hash.py  (executes assertions)
"""
from __future__ import annotations
import hashlib
from bisect import bisect_right


class HashRing:
    def __init__(self, nodes: list[str] | None = None, vnodes: int = 150):
        self.vnodes = vnodes                 # virtual nodes per real node -> evenness
        self._ring: dict[int, str] = {}      # hash position -> node
        self._sorted: list[int] = []         # sorted positions for bisect
        for n in nodes or []:
            self.add(n)

    @staticmethod
    def _hash(key: str) -> int:
        return int.from_bytes(hashlib.md5(key.encode()).digest()[:8], "big")

    def add(self, node: str) -> None:
        for i in range(self.vnodes):
            pos = self._hash(f"{node}#{i}")
            self._ring[pos] = node
        self._sorted = sorted(self._ring)

    def remove(self, node: str) -> None:
        for i in range(self.vnodes):
            self._ring.pop(self._hash(f"{node}#{i}"), None)
        self._sorted = sorted(self._ring)

    def get(self, key: str) -> str:
        """Map a key to a node: first vnode clockwise from the key's position."""
        if not self._sorted:
            raise ValueError("empty ring")
        h = self._hash(key)
        idx = bisect_right(self._sorted, h)
        if idx == len(self._sorted):
            idx = 0                          # wrap around the ring
        return self._ring[self._sorted[idx]]


if __name__ == "__main__":
    nodes = ["cache-a", "cache-b", "cache-c", "cache-d"]
    ring = HashRing(nodes)
    keys = [f"user:{i}" for i in range(10_000)]

    # 1) Deterministic: same key -> same node, always.
    before = {k: ring.get(k) for k in keys}
    assert all(ring.get(k) == before[k] for k in keys), "mapping must be stable"

    # 2) Roughly even distribution thanks to virtual nodes (within ~25% of 1/N).
    from collections import Counter
    counts = Counter(before.values())
    ideal = len(keys) / len(nodes)
    spread = max(counts.values()) / min(counts.values())
    print("distribution:", dict(counts), f"(ideal ~{ideal:.0f} each, spread {spread:.2f}x)")
    assert spread < 1.5, "virtual nodes should keep buckets reasonably even"

    # 3) THE property: removing a node only remaps that node's keys.
    ring.remove("cache-c")
    after = {k: ring.get(k) for k in keys}
    moved = [k for k in keys if before[k] != after[k]]
    # Only keys that were on cache-c should have moved.
    assert all(before[k] == "cache-c" for k in moved), "only cache-c's keys may move"
    frac = len(moved) / len(keys)
    print(f"removed cache-c: {len(moved)} keys remapped ({frac:.1%}) "
          f"-- naive hash%%N would remap ~{(1-1/len(nodes)):.0%}")
    assert frac < 0.35, "should be near 1/N, not the whole keyspace"

    print("OK: stable, even, and minimal-disruption on membership change.")
```

### 7.3 An NGINX upstream config

The production analog of the Python above — round-robin by default, least-connections, weights, passive ejection, and TLS termination with re-encryption to the backend.

```nginx
# Define the backend pool.
upstream api_backends {
    least_conn;                       # least-connections (drop this line = round-robin)

    server 10.0.1.11:8080 weight=3;   # weighted: 3x the traffic (bigger box)
    server 10.0.1.12:8080 weight=1;
    server 10.0.1.13:8080 backup;     # only used when the others are down

    # Passive health: eject after 3 fails within 30s, then probe again.
    server 10.0.1.14:8080 max_fails=3 fail_timeout=30s;

    keepalive 32;                     # pool of idle upstream keepalive connections
}

server {
    listen 443 ssl;
    http2 on;
    server_name api.example.com;

    # --- TLS termination at the edge ---
    ssl_certificate     /etc/ssl/api.example.com.fullchain.pem;  # leaf + intermediates
    ssl_certificate_key /etc/ssl/api.example.com.key;
    ssl_protocols       TLSv1.2 TLSv1.3;

    location /api/ {
        proxy_pass http://api_backends;

        # Reuse upstream connections (needs HTTP/1.1 + cleared Connection header).
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # Preserve client info the backend would otherwise lose behind the proxy.
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Only retry the next upstream on connect/transport errors (NOT on app errors,
        # to avoid duplicating non-idempotent requests).
        proxy_next_upstream error timeout;
        proxy_connect_timeout 2s;
        proxy_read_timeout    10s;
    }

    location = /healthz { return 200 'ok'; }   # the LB's own liveness endpoint
}
```

---

## 8. Global server load balancing (GSLB), anycast, and CDNs

Within a datacenter you balance across servers; across the planet you balance across **regions/POPs**. Two mechanisms:

### 8.1 DNS-based GSLB

The authoritative DNS server returns *different* answers based on the resolver's geography/health: a user in Frankfurt gets the EU region's IP, a user in Virginia gets us-east. Pros: works with any client, coarse global steering. Cons: **DNS caching/TTL lag** — clients cache the answer, so failover is as slow as the TTL, and resolvers don't perfectly reflect user location.

### 8.2 Anycast

The *same* IP address is announced from many locations via BGP; the Internet's routing naturally delivers each client to the **topologically nearest** announcement. Pros: instant failover (withdraw the route and traffic reroutes), no DNS TTL lag, and it absorbs/disperses DDoS across all POPs. This is how Cloudflare, Google DNS (8.8.8.8), and root DNS servers work. Cons: routing changes can shift a connection mid-flight (mostly fine for stateless HTTP; QUIC's connection migration — see [06 §6.4](06_http_tls.md) — helps).

### 8.3 CDNs

A **CDN** is a globally distributed reverse-proxy + cache network sitting at the edge (often anycast-fronted). It serves cached content from the POP nearest the user (cutting RTT — the dominant web cost, [06 §1](06_http_tls.md)), terminates TLS at the edge, absorbs DDoS, and runs WAF/rate-limiting/edge-compute before traffic ever reaches your origin. The CDN is your first and most important load-shedding layer.

### 8.4 Direct Server Return (DSR)

In normal L4 balancing, response traffic flows back *through* the LB. With **DSR**, the LB forwards the request to the backend but the backend replies **directly to the client** (the LB is bypassed on the return path). Because responses are usually far larger than requests, DSR removes the LB from the heavy path → it scales to enormous throughput with a cheap LB. Cost: complex (MAC/IP rewriting, the backend must own the VIP), and the LB loses visibility into responses (so it's L4-only).

---

## 9. The service mesh — east-west traffic

Everything above is mostly *north-south* (client → system). Inside a microservice system, the bigger problem is *east-west*: service A calling service B calling C, with retries, timeouts, mTLS, and observability needed on **every** hop. Putting that logic in every service (in every language) is unmaintainable.

A **service mesh** solves it with the **sidecar model**: deploy a proxy (Envoy) next to *every* service instance; all traffic in/out of the service goes through its sidecar. The sidecars form the **data plane**; a **control plane** (e.g., Istio) configures them dynamically (via Envoy's xDS).

```
   Service A pod                    Service B pod
   +---------------+                +---------------+
   | app  <-> [Envoy] <==mTLS==> [Envoy] <-> app   |
   +---------------+                +---------------+
          ^                                ^
          +--------- control plane --------+
                  (Istio: config, certs, policy)
```

What the mesh moves out of your app code, uniformly across languages:
- **mTLS everywhere** — automatic cert issuance/rotation and service-identity-based auth ([06 §7.8](06_http_tls.md)). Zero-trust east-west.
- **Traffic shaping** — canary/blue-green by weight, fault injection, mirroring, request routing by header.
- **Resilience** — retries, timeouts, circuit breaking, outlier detection — consistently, not per-service.
- **Observability** — uniform metrics/traces/logs at every hop for free.

Cost: a proxy per instance (latency + memory + CPU overhead) and real operational complexity. Worth it at high service count; overkill for a handful of services (an API gateway + libraries may suffice).

> Cross-link: the mesh is the east-west complement to the north-south API gateway (§6.1). It leans entirely on mTLS and Envoy's dynamic config; see the system-design service-communication material for when a mesh earns its keep vs simpler RPC libraries.

---

## 10. Edge resilience: rate limiting & circuit breaking

These protect the origin and belong as far out as possible (CDN/edge/LB), so abuse and overload die before consuming backend capacity.

### 10.1 Rate limiting

Cap request rate per client/key to prevent abuse and protect capacity. Common algorithms:

| Algorithm | Behavior |
|---|---|
| **Fixed window** | N requests per wall-clock window. Simple; allows 2N burst at the window boundary. |
| **Sliding window** | Smooths the boundary burst by weighting the previous window. |
| **Token bucket** | Tokens refill at rate R, bucket holds B; a request spends a token. Allows bursts up to B, sustained rate R. The most common — it matches "steady rate + some burst". |
| **Leaky bucket** | Requests drain at a fixed rate; excess queues or drops. Enforces a *smooth* output rate. |

The LB/gateway returns `429 Too Many Requests` (with `Retry-After`) when the limit is exceeded ([06 §2.3](06_http_tls.md)). Rate-limit *at the edge* and *per identity* (API key, user, IP), not just globally.

### 10.2 Circuit breaking

When a backend is failing, **stop sending it traffic** instead of piling on requests that will time out (which makes the failure worse and ties up your own threads/connections). A circuit breaker is a state machine:

```
   CLOSED  --(failure rate > threshold)-->  OPEN
   (normal)                                  (reject fast, don't call backend)
      ^                                         |
      |                                  (after cooldown)
      |                                         v
   (probe succeeds) <----------------------  HALF-OPEN
                                          (let a few trial requests through)
```

- **CLOSED**: requests flow; failures are counted.
- **OPEN**: trip the breaker — fail fast (return cached/default/`503`) *without* calling the backend, giving it room to recover and freeing the caller.
- **HALF-OPEN**: after a cooldown, allow a few probes; success → CLOSED, failure → back to OPEN.

This is the direct defense against the cascading failure / thundering herd of §4.1 — it bounds the blast radius of a sick dependency. Envoy and meshes implement it natively (outlier detection + circuit breakers).

---

## 11. Sticky sessions — trade-offs

**Sticky sessions (session affinity)** pin a client to a specific backend (via a cookie the LB sets, or consistent hashing on client IP). Used when a backend holds per-session state in process memory.

| Benefit | Cost |
|---|---|
| In-memory session state works without a shared store | **Uneven load** — popular/long-lived clients pin to specific backends; you can't balance freely |
| Local cache warmth / data locality | **Failover loses state** — the pinned backend dies → that user's session is gone |
| | **Breaks graceful scaling** — adding/removing backends reshuffles affinity (mitigated by consistent hashing) |
| | **Deploys are disruptive** — draining a sticky backend strands its sessions |

> Staff verdict: **prefer stateless backends with a shared session store** (Redis) so any backend serves any request — that's what makes load balancing, failover, and rolling deploys clean ([06 §3](06_http_tls.md)). Reach for stickiness only when you genuinely can't externalize the state, and prefer consistent-hash affinity (§3.3) over a fixed cookie so scaling events don't reshuffle everyone.

---

## 12. Connection draining & graceful shutdown

You cannot just kill a backend during a deploy — in-flight requests would be dropped (users see `502`). **Connection draining** (a.k.a. graceful shutdown / lame-duck mode) is the protocol:

```
1. Mark the backend OUT in the LB (health check starts failing / admin sets it down).
2. LB stops sending it NEW connections/requests.
3. Existing in-flight requests are allowed to COMPLETE (up to a drain timeout).
4. Once drained (or timeout hit), the backend is removed and can be stopped.
```

- Pair it with a **readiness probe** distinct from liveness: flip readiness to "not ready" first so the orchestrator/LB stops routing, *then* shut down after the drain window.
- The reverse — **slow start** — applies on the way *in*: ramp a freshly-added (or recovered) backend up gradually rather than dumping full load on a cold instance (empty caches, cold JIT, connection pools warming). Both NGINX (`slow_start`) and Envoy support it.

This is the mechanism that makes zero-downtime rolling deploys possible: drain → deploy → slow-start → repeat.

---

## 13. Advanced: stateless L4 at scale, P2C, DSR, and outlier detection

### Stateless L4 at hyperscale — Maglev, Katran, consistent hashing

A traditional L4 LB keeps a **per-connection state table** (which backend each flow
maps to) — a memory and failover liability at millions of flows. Google's **Maglev**
and Meta's **Katran** (an **XDP**/eBPF LB, [08 §advanced](08_network_performance_tuning.md))
are **stateless**: every LB node independently computes the same backend for a packet
using **consistent hashing**, so any node can handle any packet and a node failure
doesn't drop the flow table. Multiple LB nodes sit behind **ECMP + anycast**
([03 §10-11](03_network_layer_routing.md)) — the router sprays packets across LB nodes,
and consistent hashing keeps each flow pinned to one backend.

The subtlety: when the backend set changes, naive hashing reshuffles *every* flow
(breaking live connections). **Consistent hashing with bounded load** (and Maglev's
hashing) minimizes disruption — only a small fraction of flows move — while capping any
one backend's share.

### Power of Two Choices (P2C) — near-optimal balancing, cheaply

"Least connections" requires global state; pure random causes imbalance. **P2C** picks
**two backends at random and sends to the less-loaded of the two** — provably reducing
max load dramatically (the "power of two choices" result) with almost no coordination.
Combined with **EWMA** latency (favor backends that have been responding fast), it's the
modern default in Envoy/Finagle and beats round-robin under heterogeneous backends.

### Direct Server Return (DSR)

For high-throughput, asymmetric workloads (video, downloads), **DSR** has the LB
forward only the *inbound* packets to the backend, and the backend replies **directly
to the client**, bypassing the LB on the (much larger) return path. This removes the LB
as a return-traffic bottleneck — common in L4 LBs handling bulk egress.

### Outlier detection — passively ejecting bad backends

Active health checks ([§4](#4-health-checks-and-the-thundering-herd)) catch a *dead*
backend; they miss one that's *up but degraded* (slow disk, GC, a bad deploy on one
host). **Outlier detection** (Envoy) passively watches real traffic and **ejects** a
backend that returns too many 5xx/timeouts, then probes it back gradually. This is the
mesh-level analogue of a circuit breaker ([§10](#10-edge-resilience-rate-limiting--circuit-breaking))
and the fix for the "one bad host poisons p99" pattern
([scenarios 02.5](../enterprise_scenarios/02_io_storage_incidents.md),
[04.9](../enterprise_scenarios/04_network_incidents.md)).

---

## Key Takeaways

1. **The LB is both the chokepoint and the enabler.** It must not be a SPOF (replicate it), and it's what makes every backend disposable (drain, deploy, fail out invisibly).
2. **L4 vs L7 is the defining choice.** L4 = fast, protocol-agnostic, can do TLS passthrough, but blind to HTTP. L7 = routes by path/header/cookie, retries, caches, rate-limits — but terminates TLS and costs CPU per request. The common shape layers both.
3. **Round-robin is load-oblivious; least-connections is the better default** for variable workloads. **Power-of-two-choices** gets near-optimal balancing with almost no shared state — which is why distributed LBs use it.
4. **Consistent hashing** (with virtual nodes) gives affinity/cache-locality that survives scaling — only `~K/N` keys move when membership changes, vs *everything* with `hash % N`. **Maglev** refines it for L4 at scale.
5. **Health-check both ways** (active probes + passive ejection), and beware **deep checks causing correlated whole-pool ejection** and the **thundering-herd cascade on failover** — provision headroom, cap retries, slow-start recoveries.
6. **TLS termination** centralizes certs and unlocks L7 (re-encrypt to backends for internal security); **passthrough** keeps keys on backends but forces L4-only.
7. **Pick the proxy for the job**: NGINX (serving + simple proxy), HAProxy (dedicated high-efficiency LB), Envoy (dynamic config + service-mesh sidecar).
8. **Global steering** is GSLB (DNS, TTL-limited) and **anycast** (BGP, instant failover, DDoS absorption); **CDNs** are anycast edge caches that shed load before it reaches the origin. **DSR** removes the LB from the (large) response path for L4 scale.
9. **The service mesh** (sidecar + control plane) moves mTLS, retries, traffic shaping, and observability out of every service for east-west traffic — the complement to the north-south API gateway.
10. **Push resilience to the edge**: rate-limit per identity (token bucket → `429`), and **circuit-break** failing dependencies (CLOSED/OPEN/HALF-OPEN) to stop cascades. Prefer **stateless backends + shared session store** over sticky sessions, and always **drain connections** for zero-downtime deploys.

> Read alongside [06 — HTTP & TLS](06_http_tls.md) (what these proxies terminate, route, and cache) and the system-design service-communication material (when a mesh vs RPC libraries earns its keep).
