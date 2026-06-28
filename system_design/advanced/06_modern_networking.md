# Modern Networking for Architects

> Staff/Principal deep-dive. From the TCP/TLS substrate up through HTTP/2, HTTP/3+QUIC, gRPC internals, load-balancing layers, and the service-mesh → eBPF evolution. The goal is *mechanistic* understanding: why each layer exists, what it costs, and where it bites you at scale.

---

## 1. Why It Matters

At scale, the network is where latency, tail latency, and cascading failures are born. A staff engineer should be able to reason about *where a millisecond goes* — DNS, TCP handshake, TLS handshake, head-of-line blocking, congestion control, connection reuse — and *where a fleet-wide outage comes from* (a retry storm amplified by a load balancer, an mTLS cert expiry, a connection-pool exhaustion). The modern stack (HTTP/2, QUIC, service mesh, eBPF) exists almost entirely to attack two enemies: **handshake round-trips** and **head-of-line (HoL) blocking**.

```
DNS  +  TCP handshake  +  TLS handshake  +  request  +  server  +  response
~RTT      1 RTT             1-2 RTT          0.5 RTT     time       0.5 RTT
└────────────── all of this is "time to first byte" overhead ──────────────┘
```

---

## 2. TCP Recap (the substrate)

### 2.1 The three-way handshake
```
client                         server
  │ ── SYN (seq=x) ──────────────► │
  │ ◄─ SYN-ACK (seq=y, ack=x+1) ── │
  │ ── ACK (ack=y+1) ────────────► │
  │ ── (data can now flow) ──────► │      => 1 RTT before any data
```
Connection teardown is a 4-way `FIN/ACK` exchange, leaving the initiator in `TIME_WAIT` (2·MSL) — which at high connection churn exhausts ephemeral ports/sockets. This is a primary reason to **reuse connections** (keep-alive / pooling).

### 2.2 Congestion control
TCP self-clocks against the network using a **congestion window (cwnd)**:
- **Slow start:** cwnd starts ~10 MSS and *doubles* per RTT until a threshold or loss → exponential ramp, but it means short connections never reach full bandwidth (another reason to reuse connections).
- **Congestion avoidance:** additive increase (+1 MSS/RTT).
- **Loss reaction:** classic loss-based (Reno/CUBIC) treats *packet loss* as the congestion signal and multiplicatively cuts cwnd — AIMD. On lossy-but-high-bandwidth paths this underutilizes the link.
- **BBR** (Google, 2016) instead models the path's **bottleneck bandwidth and round-trip propagation time**, pacing to the bandwidth-delay product rather than reacting to loss — dramatically better on the modern, buffer-bloated, occasionally-lossy internet.

### 2.3 TCP head-of-line blocking
TCP delivers a single, strictly-ordered byte stream. If segment #5 is lost, segments #6–#10 *arrive* but the kernel **cannot deliver them to the application** until #5 is retransmitted — everything behind the lost packet is stalled. This is fine for one logical stream but **disastrous when you multiplex many independent streams over one TCP connection** (HTTP/2's Achilles heel — see §5). QUIC exists to fix exactly this.

### 2.4 Nagle's algorithm
To avoid flooding the network with tiny "tinygram" packets, Nagle buffers small writes until the prior unacknowledged data is ACKed. Combined with **delayed ACKs** (the receiver waits up to ~40 ms before ACKing), you get the infamous **Nagle + delayed-ACK interaction**: ~40 ms stalls on small request/response workloads. Latency-sensitive servers set `TCP_NODELAY` to disable Nagle.

```python
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # disable Nagle
sock.setsockopt(socket.SOL_SOCKET,  socket.SO_KEEPALIVE, 1)  # detect dead peers
```

---

## 3. TLS 1.3 Handshake

TLS 1.3 (RFC 8446, 2018) is a security *and* a latency win: it cut the handshake from 2 RTT (TLS 1.2) to **1 RTT**, and offers **0-RTT** resumption.

Full 1-RTT handshake:
```
client                                         server
  │ ── ClientHello + key_share (guesses group) ──► │
  │                                                │  picks params,
  │ ◄─ ServerHello + key_share, {EncryptedExt,     │  derives keys
  │      Certificate, CertVerify, Finished} ────── │  (rest is ENCRYPTED)
  │ ── {Finished} ───────────────────────────────► │
  │ ── application data ─────────────────────────► │   => 1 RTT
```
Key improvements: the client sends its `key_share` (an ephemeral ECDHE public value) in the **first** flight, so the server can derive keys immediately; everything after ServerHello is encrypted; legacy/static-RSA and weak ciphers are removed (always forward-secret). **0-RTT** ("early data") lets a returning client send application data in the *first* packet using a pre-shared key from a prior session — but 0-RTT data is **replayable**, so it must only carry idempotent requests.

---

## 4. (covered above — TLS sits between TCP and HTTP)

---

## 5. HTTP/2

HTTP/1.1's problem: one request per TCP connection at a time. Browsers worked around it by opening 6+ parallel connections (expensive handshakes, no shared congestion state). HTTP/2 (RFC 7540, 2015; from Google's SPDY) puts **many streams on one connection**:

- **Binary framing:** the protocol is now frames (`HEADERS`, `DATA`, `SETTINGS`, `WINDOW_UPDATE`, `RST_STREAM`, ...) instead of text.
- **Multiplexing:** many concurrent **streams** (each a request/response), each carrying interleaved frames tagged with a stream ID, over one TCP connection. Solves application-layer HoL blocking *within HTTP*.
- **HPACK** header compression: a static table of common headers + a dynamic table + Huffman coding, so repeated headers (cookies, user-agent) aren't re-sent verbatim. (Designed to resist the CRIME attack that killed naive header compression.)
- **Server push:** server proactively sends resources the client will need (`PUSH_PROMISE`). In practice push proved hard to use well and is largely **deprecated/removed** in browsers — mention it, don't rely on it.
- **Flow control & stream priorities** per stream.

**The catch — TCP HoL blocking strikes back.** HTTP/2 multiplexes *above* TCP. A single lost TCP segment stalls *every* multiplexed stream because TCP must deliver bytes in order (§2.3). So HTTP/2 trades 6 connections' worth of head-of-line blocking for *one connection where one loss blocks everything*. On lossy networks (mobile) this can be worse. The only real fix is to stop using TCP — enter QUIC.

```
HTTP/2 over TCP:   stream A ─┐
                   stream B ─┼──► ONE ordered TCP byte stream
                   stream C ─┘     └─ lost segment stalls A, B, AND C
```

---

## 6. HTTP/3 + QUIC

**QUIC** (RFC 9000) is a transport built on **UDP**, with TLS 1.3 *baked in*, designed by Google and standardized at the IETF. **HTTP/3** (RFC 9114) is HTTP semantics over QUIC.

Why UDP? Because TCP is implemented in the OS kernel and ossified by middleboxes; QUIC runs in **user space** (ships with the app/browser), so it can iterate fast, and it implements its own reliability, ordering, and congestion control on top of UDP datagrams.

What QUIC fixes:
1. **No transport HoL blocking.** QUIC has **independent streams** with *per-stream* ordered delivery. A lost packet only stalls the stream(s) whose data it carried — other streams keep flowing. This is the headline win over HTTP/2-over-TCP.
2. **1-RTT and 0-RTT connection setup.** QUIC merges the transport + TLS handshake: a new connection is **1 RTT** (vs TCP 1 RTT + TLS 1 RTT = 2 RTT), and a resumed connection is **0-RTT** — data in the first packet.
3. **Connection migration.** A QUIC connection is identified by a **Connection ID**, not the 4-tuple, so it **survives an IP change** (Wi-Fi → cellular) without a new handshake — huge for mobile.
4. Always-encrypted (incl. most transport metadata), so middleboxes can't ossify it.

```
TCP+TLS1.3 new conn:  SYN/SYN-ACK/ACK  +  TLS 1-RTT     = 2 RTT to data
QUIC new conn:        Initial + handshake (TLS in-band)  = 1 RTT to data
QUIC resumed:         0-RTT early data                   = 0 RTT to data

Per-stream delivery:  A ──╮
                      B ──┼─► independent QUIC streams over UDP
                      C ──╯    loss on A stalls ONLY A
```

Trade-offs: more CPU (crypto + congestion control in user space), UDP sometimes throttled/blocked by networks, and operational unfamiliarity. But for high-latency/lossy/mobile traffic it is a clear win, which is why most large CDNs and Google/Meta serve HTTP/3.

---

## 7. gRPC Internals

gRPC is RPC over **HTTP/2**, with Protobuf as the default payload. Understanding it = understanding HTTP/2 framing:

- **Each RPC = one HTTP/2 stream.** The method is the `:path` pseudo-header, e.g. `POST /helloworld.Greeter/SayHello`.
- **Message framing on the stream:** each protobuf message is prefixed by a 5-byte header: `[1 byte compressed-flag][4 byte big-endian length]` followed by the serialized bytes — carried in HTTP/2 `DATA` frames. Multiple messages (for streaming) are just concatenated length-prefixed frames on the same stream.
- **Metadata = HTTP/2 headers** (HPACK-compressed). Status is returned in **trailers** (`grpc-status`, `grpc-message`) — a `HEADERS` frame *after* the data, which is why gRPC requires real HTTP/2 trailers (and why gRPC-Web needs a proxy: browsers can't read trailers).
- **Four call types map directly onto HTTP/2 streams:**
  - **Unary:** one request message, one response message.
  - **Server streaming:** one request, a stream of responses (many DATA frames).
  - **Client streaming:** a stream of requests, one response.
  - **Bidirectional streaming:** both directions stream independently on the same full-duplex HTTP/2 stream.
- **Deadlines/cancellation** propagate as headers (`grpc-timeout`) and stream `RST_STREAM` — embracing the network-is-unreliable reality (cf. the RPC chapter in *DDIA*).

```
HTTP/2 stream  (one RPC)
 ├─ HEADERS  :method=POST :path=/svc/Method  grpc-timeout=1S ...
 ├─ DATA     [0][00 00 00 12][...18 bytes protobuf...]   <- length-prefixed msg
 ├─ DATA     [0][00 00 00 0a][...10 bytes...]            <- next (if streaming)
 └─ HEADERS  grpc-status=0  grpc-message=OK              <- trailers
```

Because everything rides HTTP/2, gRPC inherits its multiplexing benefit *and* its TCP-HoL-blocking weakness; gRPC-over-HTTP/3 is emerging to address the latter.

---

## 8. Load Balancing Layers

Balancers operate at different OSI layers, each with a different trade-off between *visibility* and *cost*.

| Layer | Sees | Decision basis | Cost | Examples |
|---|---|---|---|---|
| **L3 (network)** | IP packets | dst IP / routing | cheapest | ECMP routing, **anycast** |
| **L4 (transport)** | TCP/UDP 4-tuple | per-flow (consistent hash of 4-tuple) | low; can't read payload | Maglev, IPVS, AWS NLB, `RST`-aware |
| **L7 (application)** | HTTP, gRPC, headers, paths | content (path, header, cookie), retries, TLS termination | highest (parses, often terminates TLS) | Envoy, NGINX, HAProxy, ALB |

- **L4** keeps each connection pinned to one backend (it must — TCP state lives there); great throughput, no application awareness. Maglev (NSDI 2016) is the canonical software L4 LB using consistent hashing for flow→backend stability.
- **L7** can do path/host routing, header-based canarying, retries, per-request load balancing across a connection pool, and TLS termination — at the cost of parsing every request.
- **DSR (Direct Server Return):** the LB forwards the *request* to the backend, but the backend replies **directly to the client**, bypassing the LB on the (much larger) response path. Massively reduces LB bandwidth; used in high-throughput L4 setups. Requires L2 adjacency or tunneling and careful ARP/loopback config.
- **Anycast:** the *same* IP advertised via BGP from many locations; the internet's routing delivers the client to the topologically-nearest PoP. Backbone of CDNs and global L3/L4 LB and DDoS absorption. Caveat: route changes can break long-lived TCP flows (mitigated by stable per-PoP L4 + connection-aware steering).

---

## 9. Service Mesh — Sidecar Data Plane (Envoy/Istio)

As microservices proliferate, cross-cutting concerns — mTLS, retries, timeouts, circuit breaking, load balancing, observability — were being reimplemented in every language's client library. A **service mesh** moves them into the *infrastructure*.

**Architecture:**
- **Data plane:** a per-pod **sidecar proxy** (Envoy) intercepts all inbound/outbound traffic (via iptables/eBPF redirection). The app talks to `localhost`; the sidecar does the networking.
- **Control plane:** **Istiod** distributes config to sidecars via **xDS** APIs (LDS/listeners, RDS/routes, CDS/clusters, EDS/endpoints) — the de-facto standard config protocol Envoy speaks.

```
   ┌─────────── Pod A ───────────┐        ┌─────────── Pod B ───────────┐
   │  app ──localhost──► Envoy ───┼─mTLS──►┼─── Envoy ──localhost──► app │
   └──────────────────────▲──────┘        └──────▲──────────────────────┘
                          xDS                    xDS
                           └──────── Istiod (control plane) ─────────┘
```

Data-plane features the sidecar provides transparently:
- **mTLS** between every pair of services (identity via SPIFFE/SVID certs, auto-rotated) — zero-trust east-west encryption with no app code.
- **Circuit breaking / outlier detection:** eject unhealthy endpoints; cap concurrent connections/requests to stop cascading failure.
- **Retries with budgets, timeouts, traffic splitting** (canary, A/B), **fault injection**, rich L7 telemetry (golden signals) for free.

Example Istio config (circuit breaking + retries):
```yaml
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata: {name: ratings-cb}
spec:
  host: ratings.default.svc.cluster.local
  trafficPolicy:
    connectionPool:
      tcp:  {maxConnections: 100}
      http: {http2MaxRequests: 1000, maxRequestsPerConnection: 10}
    outlierDetection:           # eject a backend after consecutive 5xx
      consecutive5xxErrors: 5
      interval: 10s
      baseEjectionTime: 30s
      maxEjectionPercent: 50
---
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata: {name: ratings-retry}
spec:
  hosts: [ratings]
  http:
  - route: [{destination: {host: ratings}}]
    retries: {attempts: 3, perTryTimeout: 2s, retryOn: "5xx,reset,connect-failure"}
    timeout: 10s
```

**The cost of the sidecar model:** one extra proxy *per pod* → significant CPU/memory overhead (often hundreds of MB and added latency per hop, ×2 since both ends proxy), plus operational complexity. At thousands of pods this is real money and real tail latency.

---

## 10. Sidecarless / Ambient Mesh & eBPF (Cilium)

The industry response to sidecar overhead is to push mesh functions **lower** — into the kernel and/or a shared per-node proxy.

- **Istio Ambient mesh:** removes per-pod sidecars. A per-node **ztunnel** handles L4 + mTLS (zero-trust tunnel) for all pods on the node; optional per-namespace **waypoint** proxies (Envoy) handle L7 only when you need it. You pay for L7 only where you use it; mTLS/L4 is cheap and shared.
- **eBPF (Cilium):** **extended Berkeley Packet Filter** lets you run sandboxed programs *inside the Linux kernel* at hook points (socket ops, XDP, tc). Cilium uses eBPF to do **service load balancing, network policy, and observability directly in the kernel datapath** — bypassing iptables and often the per-pod proxy entirely. Benefits:
  - **kube-proxy replacement:** O(1) eBPF map lookups instead of O(N) iptables chains → far better at scale.
  - **socket-level LB:** connect-time translation to the backend, no per-packet NAT, no extra hop.
  - **XDP** at the NIC driver for line-rate DDoS drop / L3-L4 LB (this is how Cilium and Meta's Katran do software LB).
  - mTLS and L7 policy increasingly handled with eBPF + a minimal node proxy rather than a full per-pod Envoy.

```
 Sidecar mesh:     app │ Envoy │  ──►  network  ──►  │ Envoy │ app   (2 proxies/req)
 Ambient/eBPF:     app │       │ ──► [node ztunnel/eBPF kernel datapath] ──► │ app
                        \____ no per-pod proxy on the fast path ____/
```

Trade-off: eBPF needs a recent kernel, is harder to debug, and very-rich L7 policy still benefits from a real proxy (Envoy) — so production stacks blend kernel eBPF for L3/L4 with a thin L7 proxy where needed.

---

## 11. Connection Pooling & Keep-Alive

Every concept above conspires to make **establishing a connection expensive** (DNS + TCP RTT + TLS RTT(s) + slow-start ramp). The mitigation is to **not** establish connections per request:

- **HTTP keep-alive** (persistent connections) — reuse a TCP/TLS connection for many sequential requests (HTTP/1.1 default). HTTP/2/3 go further: many *concurrent* requests per connection.
- **Connection pools** — clients keep a bounded set of warm connections per upstream. Tuning levers and their failure modes:
  - **Pool size too small** → requests queue → latency spikes under load.
  - **Pool size too large** → server FD/memory exhaustion; thundering-herd reconnects.
  - **Idle timeout vs server's keep-alive timeout mismatch** → the classic race where the client reuses a connection the server just closed → spurious resets (set client idle timeout *below* the server's).
  - **HTTP/2 max-streams-per-connection** caps concurrency per connection; pools must open more connections past that cap.
- **L7 load balancers and gRPC** need care: a single long-lived HTTP/2 connection pins all RPCs to *one* backend, defeating L4 balancing. Solutions: client-side LB (gRPC's `round_robin` over resolved endpoints), or an L7 mesh proxy that load-balances per-request.

```python
# Reuse connections with a pooled session (requests + urllib3 pooling)
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
retry = Retry(total=3, backoff_factor=0.2, status_forcelist=[502, 503, 504])
adapter = HTTPAdapter(pool_connections=20,   # distinct hosts
                      pool_maxsize=50,        # warm conns per host
                      max_retries=retry)
session.mount("https://", adapter)
session.mount("http://", adapter)
# subsequent requests on `session` reuse the warm TLS connections (keep-alive)
```

---

## 12. Key Takeaways

1. The modern stack is a sustained assault on two enemies: **handshake round-trips** and **head-of-line blocking**.
2. **TCP** gives you ordered reliability but a single-stream HoL block; tune `TCP_NODELAY` (Nagle/delayed-ACK), reuse connections (avoid `TIME_WAIT` and slow-start), and know your congestion control (CUBIC vs **BBR**).
3. **TLS 1.3** cuts the handshake to 1-RTT (0-RTT on resume, with replay caveats) and is always forward-secret.
4. **HTTP/2** multiplexes streams over one TCP connection (binary framing, HPACK) but reintroduces HoL blocking *at the TCP layer*; server push is effectively dead.
5. **HTTP/3 + QUIC** move transport to user-space UDP with **per-stream** delivery (no transport HoL block), 1-/0-RTT setup, and **connection migration** — a clear win on lossy/mobile paths.
6. **gRPC = RPC over HTTP/2** with length-prefixed protobuf in DATA frames and status in trailers; its four streaming modes map directly onto HTTP/2 streams.
7. **Load balance at the right layer:** L3/anycast for global routing, L4 (Maglev/DSR) for cheap flow balancing, L7 (Envoy) for content-aware routing and retries.
8. **Service mesh** externalizes mTLS/retries/circuit-breaking into an Envoy sidecar driven by xDS — powerful but with real per-pod overhead; **ambient mesh + eBPF (Cilium)** push that work into the kernel/shared node proxies to reclaim the cost.
9. **Connection pooling/keep-alive** is the highest-leverage latency optimization; mismatched idle timeouts and HTTP/2-pinning-on-L4 are the classic footguns.

---

## References

- RFC 9293 (TCP); Jacobson, *Congestion Avoidance and Control*, SIGCOMM 1988; Cardwell et al., *BBR: Congestion-Based Congestion Control*, ACM Queue 2016.
- RFC 8446 — **TLS 1.3** (E. Rescorla).
- RFC 7540 / RFC 9113 — **HTTP/2**; RFC 7541 — **HPACK**.
- RFC 9000 — **QUIC**; RFC 9114 — **HTTP/3** (and RFC 9001 QUIC-TLS).
- gRPC documentation: *gRPC over HTTP/2* protocol spec (grpc.io).
- Eisenbud et al., *Maglev: A Fast and Reliable Software Network Load Balancer*, NSDI 2016.
- Istio & Envoy documentation (xDS, DestinationRule, Ambient mesh); Cilium / eBPF documentation; Meta, *Katran* (XDP L4 LB).
- M. Kleppmann, *Designing Data-Intensive Applications*, Ch. 4 (RPC realities) and Ch. 8 (unreliable networks).
