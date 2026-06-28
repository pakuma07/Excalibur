# 04 — Network Incidents

> **Audience:** staff/principal on-call. Each scenario: **Symptom → Triage → Root
> cause → Mitigate now → Permanent fix → Prevention.** Theory in
> [Transport (TCP/UDP)](../comp_networking/04_transport_tcp_udp.md),
> [Performance & Tuning](../comp_networking/08_network_performance_tuning.md),
> [Load Balancing](../comp_networking/07_load_balancing_proxies.md),
> [DNS](../comp_networking/05_dns.md), and [HTTP & TLS](../comp_networking/06_http_tls.md).

> **First fork:** is it **connectivity** (can't connect / timeouts), **latency**
> (slow but works), or **throughput** (bandwidth-limited)? The triage and fix differ
> completely. And always check: is it **one path/AZ/region** or global?

---

## 4.1 Ephemeral port exhaustion — "cannot assign requested address"

**Symptom.** A client (or proxy) suddenly fails to open new outbound connections:
`EADDRNOTAVAIL` / "Cannot assign requested address" / connection failures under load,
especially toward *one* destination (a DB, a downstream service, a proxy).

**Triage.**
```bash
ss -s                                    # totals; huge TIME-WAIT count?
ss -tan | awk '{print $1}' | sort | uniq -c   # states: TIME-WAIT dominating?
sysctl net.ipv4.ip_local_port_range      # size of the ephemeral pool (e.g. 32768-60999 ≈ 28k)
cat /proc/sys/net/ipv4/tcp_tw_reuse
```

**Root cause.** Each outbound TCP connection consumes an **ephemeral port**; the pool
is ~28k per (src IP, dst IP, dst port) tuple. A high rate of **short-lived**
connections to one destination piles up sockets in **TIME-WAIT** (held ~60 s after
close), exhausting the pool. Classic with a client that opens a new connection per
request instead of pooling.

**Mitigate now.**
```bash
sysctl -w net.ipv4.ip_local_port_range="1024 65535"   # widen the pool
sysctl -w net.ipv4.tcp_tw_reuse=1                      # reuse TIME-WAIT for new OUTBOUND conns
```

**Permanent fix.** **Connection pooling / keep-alive** — reuse connections instead of
one-per-request (the real fix; eliminates the churn). Spread load across more
destination IPs/ports. (Do **not** use the long-removed `tcp_tw_recycle` — it breaks
NAT.)

**Prevention.** Always pool HTTP/DB clients; alert on TIME-WAIT count and connection
churn rate. See [Transport §TIME-WAIT](../comp_networking/04_transport_tcp_udp.md).

---

## 4.2 Accept-queue / SYN-backlog overflow — connections dropped under burst

**Symptom.** Under a connection surge, clients see timeouts or resets *before* the app
sees the request; the app looks healthy but new connections fail intermittently.

**Triage.**
```bash
ss -ltn                                  # Recv-Q (current accept backlog) vs Send-Q (backlog limit) on LISTEN
nstat -az | grep -iE 'ListenOverflows|ListenDrops|TCPReqQFullDrop'   # overflow counters CLIMBING
netstat -s | grep -iE 'listen|SYN'       # SYN cookies sent, overflows
```
`ListenOverflows` incrementing = the accept queue filled and connections were dropped.

**Root cause.** Two queues: the **SYN queue** (half-open handshakes) and the **accept
queue** (completed handshakes waiting for the app to `accept()`). If the app accepts
too slowly (busy, blocked, undersized) or the backlog is too small, completed
connections are **dropped** — the client's connect succeeds at TCP level then stalls
or resets.

**Mitigate now.**
```bash
sysctl -w net.core.somaxconn=4096               # raise the accept-queue cap
sysctl -w net.ipv4.tcp_max_syn_backlog=8192     # raise the SYN-queue cap
# AND raise the app's listen() backlog argument (somaxconn only caps it).
```

**Permanent fix.** Size `somaxconn` + the app's `listen(backlog)` for peak burst;
make `accept()` fast (don't do work on the accept thread; use `SO_REUSEPORT` to fan
accept across workers); add capacity so the app drains the queue.

**Prevention.** Alert on `ListenOverflows`/`ListenDrops`; load-test connection-
establishment rate (not just steady throughput). See
[Transport §handshake/backlog](../comp_networking/04_transport_tcp_udp.md).

---

## 4.3 TCP retransmissions — latency spikes and throughput collapse

**Symptom.** Intermittent latency spikes (hundreds of ms), reduced throughput,
correlated with one network path/host/AZ. Works, but slow and jittery.

**Triage.**
```bash
ss -tin                                  # per-socket: 'retrans', 'rtt', 'cwnd', 'rto'
nstat -az | grep -iE 'Retrans|TCPLostRetransmit|TCPTimeouts'   # retransmit counters
mtr <dst>                                # per-hop loss & latency — WHERE is the loss?
tcpdump -ni eth0 'tcp[tcpflags] & tcp-syn != 0'   # capture for deep analysis
```
Rising `retrans` / `TCPTimeouts` = packet loss; `mtr` localizes which hop loses.

**Root cause.** Packet loss triggers retransmission. A single loss with fast-
retransmit costs ~1 RTT; a **tail loss** needing an **RTO timeout** costs ~200 ms+ (a
huge p99 hit). Causes: a congested/failing link, an overloaded NIC/switch, buffer
bloat ([4.5](#45-buffer-bloat--latency-under-load)), or a bad cable/optic on one path.

**Mitigate now.** Route around the bad path (drain the AZ/host); reduce load on the
congested link.

**Permanent fix.** Fix the lossy hop (cable/optic/switch, capacity); enable modern
loss recovery (RACK-TLP, `tcp_recovery`); for high-BDP paths, **BBR** congestion
control tolerates loss better than CUBIC; ECN to signal congestion without drops.

**Prevention.** Monitor retransmit rate per path/AZ; synthetic probes (`mtr`/ping
mesh) to catch a degrading link before it's an incident. See
[Transport §congestion control](../comp_networking/04_transport_tcp_udp.md).

---

## 4.4 Connection resets / RST — "connection reset by peer"

**Symptom.** Clients get `ECONNRESET` ("connection reset by peer") intermittently,
often after an idle period or under load.

**Triage.**
```bash
nstat -az | grep -iE 'TCPAbort|Reset'    # resets sent/received
ss -tan | grep -E 'CLOSE-WAIT|FIN-WAIT'  # half-closed pileups
tcpdump -ni any 'tcp[tcpflags] & tcp-rst != 0'   # who sends the RST, when
# Compare idle-timeout configs across the path (client / LB / server / firewall).
```

**Root cause.** Common variants:
- **Idle-timeout mismatch:** the LB/firewall/NAT silently drops an idle connection;
  the next use gets a RST. (The classic: client keep-alive 300 s, LB idle timeout
  60 s.)
- **Backlog overflow** ([4.2](#42-accept-queue--syn-backlog-overflow--connections-dropped-under-burst))
  can RST.
- **App crash / abrupt close** (`SO_LINGER` 0) sends RST instead of graceful FIN.
- A stateful firewall expiring the flow.

**Mitigate now.** Align idle timeouts; add client-side retry on idempotent requests;
restart a crashing backend.

**Permanent fix.** **Make timeouts consistent across the whole path** — client
keep-alive < LB idle timeout < server keep-alive, with margin (the single most common
fix). Enable TCP keepalives to keep NAT/firewall state alive; graceful shutdown
(drain, FIN) instead of abrupt close.

**Prevention.** Document and test the timeout ladder across every hop; alert on RST
rate. See [HTTP §keep-alive](../comp_networking/06_http_tls.md) and
[Load Balancing](../comp_networking/07_load_balancing_proxies.md).

---

## 4.5 Buffer bloat — latency under load (the bandwidth/latency trap)

**Symptom.** Latency is fine when idle but **explodes under load** — a bulk transfer
(backup, upload) on a link makes *interactive* traffic on the same link slow.
Throughput is fine; latency under load is terrible.

**Triage.**
```bash
ping <dst>               # while a bulk transfer runs: RTT climbs from 1ms to 100s of ms
ss -tin                  # large 'cwnd', growing send-Q; deep queues
tc -s qdisc show dev eth0   # which queue discipline; FIFO (pfifo_fast) = bloat-prone
```

**Root cause.** **Buffer bloat**: oversized buffers (in NICs, switches, routers) fill
up under a bulk flow. TCP keeps sending until the buffer is full; that full buffer
adds huge **queueing delay** to *every* packet, including latency-sensitive ones. Big
buffers trade latency for throughput — badly.

**Mitigate / fix.** Use a modern **AQM** queue discipline that keeps queues short:
```bash
tc qdisc replace dev eth0 root fq_codel   # or cake — Active Queue Management
# fq_codel isolates flows and drops/marks early to keep latency low under load.
```
Pair with **BBR** congestion control (paces to estimated bandwidth, doesn't fill
buffers). Right-size NIC ring buffers (`ethtool -G`).

**Prevention.** `fq_codel`/`cake` as the default qdisc; BBR for high-BDP paths;
monitor latency-under-load, not just idle latency. See
[Net Performance §buffer bloat/AQM](../comp_networking/08_network_performance_tuning.md).

---

## 4.6 DNS outage / latency / stampede — "it's always DNS"

**Symptom.** Broad, weird failures across many services at once: intermittent
connection failures, added latency, resolution timeouts. The blast radius is
suspiciously wide (DNS underlies everything).

**Triage.**
```bash
dig +trace example.com                   # full resolution path; where does it break?
dig @<resolver> example.com              # is a specific resolver slow/failing?
cat /etc/resolv.conf                     # which resolvers, ndots, timeout, attempts
nstat / resolver metrics: query latency, SERVFAIL/timeout rate
# In k8s: CoreDNS pod CPU/latency; conntrack table full?
```

**Root cause.** Variants:
- **Resolver overload / cache stampede:** a popular record's TTL expires → a flood of
  uncached lookups overwhelms the resolver.
- **`ndots` blowup (Kubernetes classic):** `ndots:5` makes every external lookup try
  5 search-domain permutations first → 5–10× DNS queries, latency, and CoreDNS load.
- **conntrack exhaustion** on the DNS path (UDP) dropping queries.
- **Upstream DNS / record misconfig** (a bad/expired record, a failed GeoDNS).

**Mitigate now.** Add/scale resolver capacity; cache aggressively (node-local DNS
cache); for the `ndots` case, use FQDNs (trailing dot) or lower `ndots`.

**Permanent fix.** **Node-local DNS cache** (NodeLocal DNSCache in k8s) to absorb load
and cut latency; sane TTLs with jitter; scale CoreDNS; FQDNs for known externals;
raise conntrack limits. Treat DNS as a tier-0 dependency with its own SLO.

**Prevention.** Monitor DNS query latency and failure rate as a first-class signal;
node-local cache by default; review `ndots`. ("It's always DNS" is a meme because DNS
is rarely on anyone's dashboard until it fails.) See [DNS](../comp_networking/05_dns.md).

---

## 4.7 TLS incidents — handshake CPU, cert expiry, version mismatch

**Symptom.** One of: (a) CPU spikes on TLS-terminating nodes under connection
churn; (b) sudden total failure of a service at a *round* time (cert expiry); (c)
handshake failures after a client/server upgrade.

**Triage.**
```bash
# Cert expiry (the most common total outage):
echo | openssl s_client -connect host:443 2>/dev/null | openssl x509 -noout -dates
# Handshake cost / failures:
openssl s_client -connect host:443 -tls1_3      # negotiate; see version/cipher/alerts
perf top                                          # on the LB: time in TLS/asymmetric crypto
ssl handshake metrics: full vs resumed ratio
```

**Root cause.**
- **Handshake CPU:** a **full** TLS handshake does expensive asymmetric crypto; a flood
  of *new* connections (no session resumption) burns CPU. Worse with one-connection-
  per-request clients ([4.1](#41-ephemeral-port-exhaustion--cannot-assign-requested-address)).
- **Cert expiry:** an unrotated certificate hit its `notAfter` → every handshake fails
  at once. A self-inflicted, total, embarrassing outage.
- **Version/cipher mismatch:** after dropping TLS 1.0/1.1 or an old cipher, a legacy
  client/server can no longer negotiate.

**Mitigate now.** Expiry → rotate the cert immediately. CPU → enable session
resumption / keep-alive; scale TLS terminators; offload to hardware/edge.

**Permanent fix.** **Automated cert rotation** (ACME/cert-manager, short-lived certs)
so expiry can't happen; **TLS 1.3** (1-RTT handshake, 0-RTT resumption) + session
tickets; connection reuse so handshakes are rare; mTLS via a service mesh with
auto-rotated certs.

**Prevention.** **Alert on cert expiry weeks ahead** (the single highest-value TLS
alert); monitor full-vs-resumed handshake ratio and TLS CPU; track negotiated
versions before deprecating one. See [HTTP & TLS](../comp_networking/06_http_tls.md).

---

## 4.8 MTU / PMTUD blackhole — "small requests work, large ones hang"

**Symptom.** Connections establish and small requests succeed, but **large** payloads
(big POST, large response) **hang** or time out. Often after a VPN/tunnel/overlay
(VXLAN, IPsec, WireGuard, cloud overlay) enters the path.

**Triage.**
```bash
ping -M do -s 1472 <dst>     # DF bit, 1472+28=1500; increase size until it fails -> find MTU
tracepath <dst>              # reports path MTU and where it drops
ip link show                 # interface MTU; overlay interfaces are often 1450, not 1500
```

**Root cause.** A tunnel/overlay reduces the effective **MTU** (encapsulation
overhead). Large packets need fragmentation; the sender relies on **Path MTU
Discovery** (PMTUD), which uses ICMP "fragmentation needed" messages — but if a
firewall **blocks ICMP**, the sender never learns to shrink packets. Big packets are
silently dropped (a **PMTUD blackhole**): small requests fit, large ones vanish.

**Mitigate now.** Lower the MTU / clamp MSS on the path:
```bash
# Clamp TCP MSS to the path MTU so packets always fit (common overlay fix):
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
ip link set dev eth0 mtu 1450      # match the overlay's effective MTU
```

**Permanent fix.** **Allow ICMP type 3 code 4** (fragmentation-needed) through
firewalls so PMTUD works; set correct MTU on overlay interfaces; MSS clamping at
tunnel edges. (For jumbo frames, ensure MTU 9000 is consistent end-to-end or you get
the same blackhole.)

**Prevention.** Never blanket-block ICMP; standardize overlay MTU/MSS; test large
payloads across every tunnel. See
[Network Layer §MTU/fragmentation](../comp_networking/03_network_layer_routing.md) and
[Cloud/Overlays](../comp_networking/10_cloud_sdn_overlays.md).

---

## 4.9 Load-balancer imbalance — one backend hot, others idle

**Symptom.** Uneven load: one (or a few) backends are saturated while others are
nearly idle, despite "round-robin" LB. Tail latency on the hot backends.

**Triage.**
```bash
# Per-backend request rate / connections (from LB stats or each backend's metrics).
ss -tan state established | awk '{print $5}' | sort | uniq -c   # conns per peer
# Long-lived connections? gRPC/HTTP2 multiplexes many requests over ONE connection.
```

**Root cause.** Common causes:
- **L4 LB + long-lived connections:** L4 balances *connections*, not requests. With
  HTTP/2 or gRPC (many requests multiplexed over one persistent connection), a
  connection-balanced LB pins all of a client's requests to one backend → imbalance.
- **Sticky sessions / hashing skew:** a hash on a low-cardinality or hot key.
- **Connections established before a scale-up** stay on old backends (new ones get no
  traffic until clients reconnect).

**Mitigate now.** Cycle connections (rolling restart of clients/backends) to
rebalance; shift to request-level balancing for HTTP/2.

**Permanent fix.** Use an **L7 LB** that balances *requests* for HTTP/2/gRPC (or a
service mesh sidecar doing per-request LB, e.g. Envoy with least-request); for L4,
periodic connection draining/recycling; choose **least-connections / EWMA** over
naive round-robin; **consistent hashing with bounded load** for cache affinity
without hotspots.

**Prevention.** Per-backend load dashboards; know your protocol (HTTP/1.1 vs HTTP/2)
when choosing L4 vs L7; test balancing after scale events. See
[Load Balancing & Proxies](../comp_networking/07_load_balancing_proxies.md).

---

## 4.10 Retry storm / metastable failure — the self-sustaining outage

**Symptom.** A brief blip (a slow dependency, a GC pause, a deploy) escalates into a
**full outage that doesn't recover even after the trigger is gone**. Load stays pinned
at maximum; restarting one service doesn't help.

**Triage.**
```bash
# Request rate to the struggling service is FAR above normal (retries multiplying it).
# Look for: every layer retrying; no jitter; no circuit breaker; timeouts > caller timeout.
nstat / app metrics: request rate vs normal baseline (3-10x = retry amplification)
```

**Root cause.** **Metastable failure** (a top distributed-systems failure class): a
trigger causes some requests to fail/slow → clients **retry** → retries multiply load
→ more failures → more retries. The retry-generated load **sustains the failure** even
after the original trigger clears. Amplified when every layer retries (3 layers × 3
retries = 27×).

**Mitigate now.** **Shed load** hard (the only reliable exit): drop a large fraction
of traffic, open circuit breakers, disable retries temporarily, scale up. You must get
load *below* the recovery threshold to escape the metastable state.

**Permanent fix.**
- **Retry budgets:** cap retries as a small % of traffic (not per-request) — the key
  fix.
- **Circuit breakers:** stop calling a failing dependency; fail fast.
- **Exponential backoff with full jitter** on every retry.
- **Retry at one layer only** (usually the edge), never compounded.
- **Load shedding / admission control** as a standing mechanism.

**Prevention.** These are platform defaults, not per-service choices. Load-test the
*recovery* path (inject a blip, confirm the system recovers without manual shedding).
See [Net Security §DDoS/overload](../comp_networking/09_network_security.md) and the
Production/System-Design chapters of the language books.

---

## 4.11 SYN flood / volumetric DDoS — connectivity under attack

**Symptom.** A flood of new connections/packets; legitimate clients can't connect; the
accept/SYN queue or bandwidth is saturated by attacker traffic.

**Triage.**
```bash
ss -tan state syn-recv | wc -l           # huge number of half-open = SYN flood
nstat -az | grep -i syncookie            # SYN cookies being sent
tcpdump -ni eth0 'tcp[tcpflags] & tcp-syn != 0'   # source distribution (spoofed?)
```

**Root cause.** Attacker sends SYNs (often spoofed) and never completes the handshake,
filling the SYN queue (**SYN flood**), or floods bandwidth/PPS (volumetric), or hits
an expensive endpoint (L7/application DDoS).

**Mitigate now.**
```bash
sysctl -w net.ipv4.tcp_syncookies=1      # SYN cookies: serve handshakes without queue state
# Rate-limit / blackhole at the edge; engage upstream DDoS scrubbing / CDN.
```

**Permanent fix.** Edge DDoS protection (CDN/scrubbing, cloud DDoS service); SYN
cookies on; rate limiting and WAF for L7 floods; anycast to absorb/spread volumetric
attacks; autoscaling with admission control. Capacity + upstream absorption, not host
tuning alone.

**Prevention.** DDoS protection in front of public endpoints by default; alert on
SYN-recv and new-connection rate anomalies. See
[Network Security](../comp_networking/09_network_security.md).

---

## 4.12 Cross-AZ / cross-region latency tax — "slow after a failover"

**Symptom.** Latency jumps after a deploy, failover, or scale event — requests now
cross an availability-zone or region boundary that they didn't before.

**Triage.**
```bash
mtr <dst>                # RTT: same-AZ ~0.5ms, cross-AZ ~1-2ms, cross-region 10-150ms
# Map which AZ/region the caller and callee are in (cloud metadata / topology labels).
# A chatty call (N round trips) × cross-region RTT = the whole regression.
```

**Root cause.** A dependency that *was* same-AZ is now cross-AZ or cross-region — every
round trip pays the higher RTT, and a **chatty** protocol (many sequential round trips)
multiplies it. Common after a failover moved a service, or a scheduler placed pods
across zones, or a DB primary failed over to another region.

**Mitigate now.** Restore co-location (pin caller+callee to the same AZ); fail back if
the cross-region placement was incidental.

**Permanent fix.** **Topology-aware routing** (prefer same-AZ/zone-local endpoints —
e.g. k8s Topology Aware Hints / `internalTrafficPolicy: Local`); reduce chattiness
(batch round trips, co-locate the data); accept cross-region only where the
availability benefit is worth the latency. Note cross-AZ traffic also has a **$ cost**
in cloud.

**Prevention.** Latency SLOs per dependency; topology-aware placement and routing by
default; test latency after failover. See
[Cloud/SDN](../comp_networking/10_cloud_sdn_overlays.md) and the System-Design chapter.

---

## Quick-reference: symptom → first command

| Symptom | First look |
|---|---|
| "Cannot assign requested address" | `ss -s` TIME-WAIT; port range (4.1) |
| Conns dropped under burst | `ss -ltn` Recv-Q; `nstat ListenOverflows` (4.2) |
| Latency spikes + throughput drop | `ss -tin` retrans; `mtr` (4.3) |
| "Connection reset by peer" | RST capture; idle-timeout ladder (4.4) |
| Latency fine idle, bad under load | `ping` during bulk; `tc qdisc` (4.5) |
| Wide weird failures | `dig +trace`; `ndots`; resolver latency (4.6) |
| TLS CPU / total outage at a round time | cert `notAfter`; resumption ratio (4.7) |
| Small reqs OK, large hang | `ping -M do -s`; overlay MTU (4.8) |
| One backend hot | per-backend conns; L4-vs-L7 (4.9) |
| Blip → outage that won't recover | request rate ≫ baseline = retries (4.10) |
| Can't connect under attack | `ss state syn-recv`; syncookies (4.11) |
| Slow after failover | `mtr` RTT; AZ/region topology (4.12) |

---

## Key takeaways

1. **Port exhaustion = connection churn** — pool/keep-alive is the fix; `tcp_tw_reuse`
   buys time (never `tcp_tw_recycle`).
2. **`ListenOverflows` = accept-queue drops** — raise `somaxconn` **and** the app's
   `listen(backlog)`, and make `accept()` fast.
3. **Retransmits cost an RTT; RTO timeouts cost ~200ms+** — find the lossy hop with
   `mtr`, prefer BBR + RACK on lossy/high-BDP paths.
4. **Most RSTs are an idle-timeout mismatch** — make the timeout ladder consistent:
   client < LB < server.
5. **Buffer bloat is latency-under-load** — `fq_codel`/`cake` + BBR keep queues short.
6. **"It's always DNS"** — node-local cache, sane TTLs+jitter, and watch `ndots` in
   Kubernetes; give DNS a tier-0 SLO.
7. **Automate cert rotation and alert weeks ahead** — expiry is a total, self-inflicted
   outage; TLS 1.3 + resumption cut handshake CPU.
8. **Large-request hangs = MTU/PMTUD blackhole** — don't block ICMP frag-needed; clamp
   MSS on overlays.
9. **HTTP/2-gRPC needs L7 (request-level) balancing** — L4 pins a client to one backend.
10. **Retry storms cause metastable outages** — retry budgets + circuit breakers +
    jittered backoff + load shedding; you escape only by shedding load below the
    recovery threshold.

> Next: [05 — Cross-Layer Triage](05_cross_layer_triage.md) — putting OS + network
> together for "the service is slow" and the war-room playbook.
