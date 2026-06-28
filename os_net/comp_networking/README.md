# Computer Networking 🌐

A from-scratch-to-deep reference on computer networking for **staff/principal engineers** — the layer that connects every distributed system. Each doc builds from first principles (why layering, what a packet is) to the depth you need in production: TCP congestion control, DNS failure modes, TLS handshakes, load-balancing math, kernel tuning, and cloud/Kubernetes networking.

---

## 📚 Concept docs

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Network Models & Layering](01_network_models_layering.md) | Why layering, OSI vs TCP/IP, encapsulation/PDUs, the end-to-end principle, the IP hourglass, MTU/fragmentation, the full host→host packet walk |
| 02 | [Link Layer & Switching](02_link_layer_switching.md) | Ethernet frames & MAC, hubs/bridges/switches, MAC learning, broadcast/collision domains, ARP (+ spoofing), VLANs/802.1Q, STP/RSTP, LACP, leaf-spine/Clos fabric, VXLAN |
| 03 | [Network Layer, IP & Routing](03_network_layer_routing.md) | IPv4/IPv6 headers, subnetting/CIDR (worked VLSM), NAT/PAT, ICMP/traceroute, longest-prefix match, OSPF vs RIP vs **BGP** (+ the Meta/Cloudflare outages), ECMP, anycast, MPLS |
| 04 | [Transport: TCP & UDP](04_transport_tcp_udp.md) | Ports/multiplexing, UDP, the TCP header & state machine, handshake/teardown/TIME_WAIT, reliable delivery (RTO/Karn), flow control, **congestion control** (Reno/CUBIC/**BBR**), Nagle, SACK, BDP/bufferbloat |
| 05 | [DNS](05_dns.md) | The namespace hierarchy, full resolution flow, record types, message format, caching/TTLs, DNS-based traffic steering (GeoDNS/failover), anycast resolvers, security (Kaminsky/DNSSEC/DoH), the Dyn 2016 outage |
| 06 | [HTTP & TLS](06_http_tls.md) | HTTP anatomy, caching (ETag/Cache-Control), HTTP/1.1 → **HTTP/2** (multiplexing/HPACK) → **HTTP/3+QUIC**, TLS 1.2 vs 1.3 handshakes, PKI/certs, SNI/ALPN, mTLS, PFS, OCSP |
| 07 | [Load Balancing, Proxies & Edge](07_load_balancing_proxies.md) | Forward vs reverse proxies, L4 vs L7, algorithms (RR/least-conn/**consistent hashing**/P2C/Maglev), health checks, TLS termination, NGINX/HAProxy/Envoy, service mesh, GSLB/CDN/DSR |
| 08 | [Network Performance & Tuning](08_network_performance_tuning.md) | Latency budget, BDP & window scaling, **buffer bloat & AQM** (CoDel), sysctl tuning, NIC offloads (GRO/GSO/TSO, RSS/RPS/RFS), NAPI, DPDK/XDP, iperf3/ss/tcpdump triage |
| 09 | [Network Security](09_network_security.md) | Threat model, stateful/stateless firewalls (iptables/nftables), zero-trust/BeyondCorp, **DDoS** (SYN flood, amplification, Mirai), SYN cookies, VPNs (IPsec/WireGuard), SSH, IDS/IPS/WAF |
| 10 | [Cloud Networking, SDN & Overlays](10_cloud_sdn_overlays.md) | Control/data plane + OpenFlow, the **VPC model** (subnets/route tables/SG vs NACL/peering/PrivateLink), overlays (VXLAN/GENEVE), **Kubernetes networking** (CNI, kube-proxy, Cilium/eBPF), Direct Connect/SD-WAN |
| 11 | [RDMA & Kernel Bypass](11_rdma_kernel_bypass.md) | Why the kernel TCP stack bottlenecks at 100/200/400GbE, **RDMA** (zero-copy/kernel-bypass/remote-CPU-bypass), the **verbs** model (QP/CQ/MR, one-sided vs two-sided), fabrics (**InfiniBand**/**RoCE**/**iWARP**), lossless Ethernet & **PFC/ECN/DCQCN**, RDMA vs DPDK vs XDP, GPUDirect/NCCL, NVMe-oF, SmartNIC/DPU, AWS EFA |
| 12 | [Time Synchronization (NTP & PTP)](12_time_synchronization.md) | Why clock skew silently breaks logs/traces/TLS/Kerberos/ordering/leases (MiFID II), wall-clock vs **monotonic** (the NTP-step bug), offset vs frequency/drift, **NTP** (stratum, 4-timestamp offset/delay, Marzullo selection, chrony vs ntpd), **PTP/IEEE 1588** (sub-µs via NIC **hardware timestamping/PHC**, boundary/transparent clocks, ptp4l/phc2sys), GNSS/atomic grandmasters & holdover, **TrueTime**/ClockBound, leap seconds & **smearing** |

---

## 🛠️ Working enterprise examples

Runnable, self-verifying, stdlib-only programs that run end-to-end on `127.0.0.1` (no external deps) — see [`examples/`](examples/README.md):

`epoll_echo_server.py` · `tcp_handshake_observer.py` · `dns_query.py` · `http_client.py` · `tls_inspect.py` · `reverse_proxy.py` · `consistent_hash.py` · `subnet_calculator.py` · `token_bucket_ratelimiter.py`

```bash
cd examples
py epoll_echo_server.py
```

---

## 🎯 The recurring networking trade-offs

- **Reliability vs latency** — TCP (ordered, reliable, HOL-blocking) vs UDP/QUIC (fast, app handles loss).
- **Latency vs throughput** — Nagle, batching, and big windows raise throughput at the cost of latency; BDP sets the ceiling.
- **L4 vs L7** — L4 is fast and protocol-agnostic; L7 sees the request (routing, retries, caching) but costs CPU and terminates TLS.
- **Caching/TTL vs freshness** — DNS and HTTP TTLs trade failover speed and load against staleness.
- **Strong security vs performance** — TLS, mTLS, and deep packet inspection add handshakes and CPU.

> The network is a partition waiting to happen (CAP starts here). At staff/principal level you must reason about where a millisecond goes, why a connection stalls, and how a packet actually traverses from one pod to another across the world.

When the network breaks in production, see [`../enterprise_scenarios/04_network_incidents.md`](../enterprise_scenarios/04_network_incidents.md) — port exhaustion, retransmission & retry storms, accept-queue overflow, buffer bloat, DNS outages, TLS/cert incidents, PMTUD blackholes, LB imbalance, and metastable failure — each as `symptom → triage → root cause → fix → prevention`, plus the end-to-end [cross-layer triage](../enterprise_scenarios/05_cross_layer_triage.md).

Related: see [`../operating_system/`](../operating_system/README.md) for the kernel network stack and [`../../system_design/`](../../system_design/README.md) — especially [`advanced/06_modern_networking.md`](../../system_design/advanced/06_modern_networking.md).
