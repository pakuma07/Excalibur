# OS & Networking 🖥️🌐

The two foundations every distributed system stands on: the **operating system** that runs each node and the **network** that connects them. This reference covers both from first principles up to the depth a **staff/principal engineer** is expected to reason from in production — with runnable, enterprise-grade working examples for each.

It complements [`../system_design/`](../system_design/README.md): system design is the architecture *above*; this is the substrate *below*.

---

## 📁 Structure

### 1. [`operating_system/`](operating_system/README.md) — Operating Systems
Processes & threads, CPU scheduling (CFS/EEVDF, real-time, priority inversion), memory management (paging, TLB, allocators, NUMA, OOM), concurrency & synchronization (locks, the C11 memory model, lock-free, deadlock), file systems & storage (journaling, fsync, I/O schedulers, RAID/LVM, NVMe), I/O models (epoll, io_uring, zero-copy, the C10K problem), virtualization & containers (namespaces/cgroups, build-a-container-from-scratch, microVMs), and Linux internals & observability (the USE method, perf/eBPF/bpftrace, triage runbooks). Each concept doc now ends with an **Advanced** section (fork hazards & PID 1, sched_ext & core scheduling, cgroup-v2 memory/PSI/madvise, lock-free reclamation, durability ladder, io_uring at the limit, confidential computing, off-CPU/PMU/continuous profiling). **8 concept docs + 8 enterprise examples + 4 diagnostic scripts.**

### 2. [`comp_networking/`](comp_networking/README.md) — Computer Networking
Layering (OSI/TCP-IP, the packet walk), the link layer (Ethernet, ARP, VLANs, STP, leaf-spine), the network layer (IP, CIDR, NAT, OSPF/BGP, anycast), the transport layer (TCP state machine, congestion control — Reno/CUBIC/BBR, flow control, BDP), DNS (resolution flow, GeoDNS/failover, DNSSEC), HTTP & TLS (HTTP/2 & HTTP/3+QUIC, the TLS 1.3 handshake, PKI/mTLS), load balancing & proxies (L4 vs L7, consistent hashing, Maglev, service mesh), performance & tuning (BDP, buffer bloat/AQM, sysctl, NIC offloads, DPDK/XDP), network security (firewalls, DDoS, VPNs, zero-trust), cloud/SDN networking (VPCs, overlays, Kubernetes/CNI/Cilium), **RDMA & kernel bypass** (verbs, InfiniBand/RoCE/iWARP, GPUDirect, NVMe-oF), and **time synchronization** (NTP/PTP, hardware timestamping, TrueTime, leap smearing). Each concept doc ends with an **Advanced** section (offload/tunnel re-layering & XDP, lossless Ethernet/EVPN/MACsec, BGP security & SRv6, TFO/pacing/BBRv2/MPTCP/kTLS, encrypted DNS & Happy Eyeballs, 0-RTT/post-quantum TLS/SPIFFE, Maglev/P2C/DSR, kernel bypass & packet steering, conntrack/XDP scrubbing, eBPF dataplane & SNAT exhaustion). **12 concept docs + 9 enterprise examples + 4 diagnostic scripts.**

### 3. [`enterprise_scenarios/`](enterprise_scenarios/README.md) — Incident Runbooks 🚨
The concept docs teach *how the machinery works*; this is what you reach for **at 3am with the pager going off**. Real production failure modes as actionable runbooks (**symptom → triage with exact commands → root cause → mitigate now → permanent fix → prevention**): CFS throttling, OOMKilled, NUMA/THP regressions ([01](enterprise_scenarios/01_cpu_memory_incidents.md)); fsync stalls, disk saturation, inode exhaustion ([02](enterprise_scenarios/02_io_storage_incidents.md)); deadlock, lock convoy, false sharing, pool exhaustion ([03](enterprise_scenarios/03_concurrency_incidents.md)); port exhaustion, retransmission/retry storms, DNS, TLS, PMTUD blackholes, LB imbalance ([04](enterprise_scenarios/04_network_incidents.md)); and the end-to-end **"the service is slow"** drill + war-room playbook + postmortem template ([05](enterprise_scenarios/05_cross_layer_triage.md)). **5 runbooks, ~40 scenarios.**

---

## 🎯 How to use

| Goal | Start here |
|------|-----------|
| Understand how a node actually runs | [`operating_system/`](operating_system/README.md) in order |
| Understand how nodes talk | [`comp_networking/`](comp_networking/README.md) in order |
| See production problems solved in code | [`operating_system/examples/`](operating_system/examples/README.md) + [`comp_networking/examples/`](comp_networking/examples/README.md) |
| **The pager just fired** | [`enterprise_scenarios/`](enterprise_scenarios/README.md) — match the symptom, run the triage |
| Diagnose "the service is slow" (end-to-end) | [Scenarios 05 — Cross-Layer Triage](enterprise_scenarios/05_cross_layer_triage.md) |
| Diagnose "the server is slow" | OS [08 — USE method](operating_system/08_linux_internals_observability.md) |
| Diagnose "the network is slow" | Net [08 — Performance & Tuning](comp_networking/08_network_performance_tuning.md) |
| Design the architecture above this | [`../system_design/`](../system_design/README.md) |

---

## 🧵 The through-lines that connect both halves

- **A request's life** — it starts as a syscall (OS), crosses sockets and the TCP/IP stack (both), traverses NICs, switches, routers, proxies, and TLS (Net), and lands as another syscall on a remote node (OS). Latency hides in every one of those layers.
- **Concurrency everywhere** — the same primitives (event loops, thread pools, backpressure, rate limiting) appear in the kernel's I/O path and in the network's load balancers. The examples folders share patterns deliberately.
- **The CAP partition begins in the network** — every consistency/availability trade-off in system design ultimately rests on the fact that the network ([04 — Transport](comp_networking/04_transport_tcp_udp.md)) can drop, delay, or reorder packets, and the OS can pause a process at any instant.

> Staff/principal engineers are expected to explain behavior end-to-end — from `malloc` to the wire to the far-side scheduler — and to know which layer to instrument when something is wrong. That is what these two folders are for.
