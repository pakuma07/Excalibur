# 11 — RDMA & Kernel Bypass

> **Audience:** staff/principal. You know TCP, sockets, interrupts, and context switches (if the per-packet kernel cost is fuzzy, read [08 — Network Performance & Tuning](08_network_performance_tuning.md) §8 first). This doc covers what happens when the host **CPU and kernel stack become the bottleneck** at 100/200/400 GbE — and the techniques (RDMA, DPDK, XDP) that take the kernel out of the data path. It is where "the network is software" ([10](10_cloud_sdn_overlays.md)) meets "the network is hardware."
>
> **Primary sources:** the InfiniBand Architecture Specification (verbs model & transport); RFC 5040–5044 (iWARP); the IBTA RoCEv2 annex; *RDMA Aware Networks Programming* (NVIDIA); the DCQCN paper (Zhu et al., SIGCOMM 2015); "RDMA over Commodity Ethernet at Scale" (Guo et al., Microsoft, SIGCOMM 2016); NVIDIA GPUDirect/NCCL docs; the NVMe-oF spec; AWS EFA/`libfabric` docs; Linux `rdma-core`/`libibverbs`.

---

## 1. Why this matters at scale

[08](08_network_performance_tuning.md) established that *the kernel does enormous work per packet* — interrupts, softirqs, copies, context switches — and that for high-PPS workloads that per-packet cost, not the wire, is the bottleneck. RDMA takes that to its conclusion: at 100/200/400 GbE the kernel TCP/IP stack **cannot keep up** without burning a frightening number of cores. Line-rate microsecond networking means removing the CPU and kernel from the data path entirely.

The numbers force the issue. Saturating 100 GbE with 1500-byte frames is ~8.1 **million packets/second**; at RPC/storage message sizes it is tens of millions. Recall the per-`recv()` cost from [08 §8.2](08_network_performance_tuning.md#82-the-cost-of-copies-and-context-switches):

```text
NIC DMA -> kernel skb (RX ring)        [DMA, no CPU copy]
softirq: protocol processing            [CPU: route, checksum, TCP state]
copy: kernel skb -> socket buffer       [COPY #1]
context switch: wake the blocked thread [CTX SWITCH ~1-5 us + cache/TLB pollution]
copy: socket buffer -> user buffer       [COPY #2, the read() syscall]
```

Two copies and a context switch per message × tens of millions/second exceeds the memory bandwidth and cycles a server can spare. A single core doing kernel TCP tops out far below 100 GbE; you either dedicate a dozen cores to networking (RSS/RPS, [08 §7.1](08_network_performance_tuning.md#71-spreading-the-load-across-cores-rss--rps--rfs)) or change the model. The OS zero-copy techniques — `sendfile`/`splice`/`MSG_ZEROCOPY`, `io_uring` ([../operating_system/06_io_models_async.md](../operating_system/06_io_models_async.md)) — shave one copy and amortize syscalls, but still ride the kernel transport and still touch the remote CPU.

> **Framing:** there are two things you might remove the kernel from. **Packet processing** (forward/drop/rewrite at line rate) → DPDK and XDP/AF_XDP ([08 §8.3](08_network_performance_tuning.md#83-dpdk--xdp--eliminating-the-kernel-from-the-data-path), [10](10_cloud_sdn_overlays.md)). **Memory-to-memory data movement** (read/write a remote buffer with no remote CPU) → **RDMA**, this chapter. They are complementary (§7).

---

## 2. RDMA — the core idea

**Remote Direct Memory Access**: one host's NIC reads/writes *another host's memory directly* over the network, **without involving the remote CPU or kernel** on the data path. DMA — a device moving data to/from RAM without a CPU copy — has existed inside a machine forever ([../operating_system/06_io_models_async.md](../operating_system/06_io_models_async.md)). RDMA extends it *across the network*: the remote NIC becomes a DMA engine for a buffer in your address space.

It delivers four properties at once; the power is their conjunction:

| Property | Meaning | Eliminates |
|---|---|---|
| **Zero-copy** | data moves NIC↔app buffer directly, no kernel staging copy | COPY #1 and COPY #2 |
| **Kernel-bypass** | data path is user-buffer → NIC → wire → NIC → user-buffer, no syscall per op | syscall + context switch per message |
| **OS-/CPU-bypass (remote)** | one-sided ops never interrupt or run code on the remote CPU | remote interrupt, softirq, scheduling |
| **HW transport offload** | reliability, ordering, segmentation, retransmit run *on the NIC* | the host TCP state machine entirely |

```text
KERNEL TCP (per message):
  app -> write() -> [kernel: copy, TCP, IP, qdisc, driver] -> NIC -> wire
  wire -> NIC -> IRQ -> [softirq: IP, TCP, copy to skb] -> copy to user -> app
          ^ both CPUs run the full stack for every message

RDMA WRITE (one-sided):
  app posts a Work Request -> NIC reads source buffer by DMA -> wire
  remote NIC writes directly into remote pre-registered buffer
          ^ remote CPU NOT involved; no remote syscall, no remote copy
```

The win: **1–2 µs** in-rack latency (vs tens of µs for tuned kernel TCP) and full line rate at 100/200/400 GbE with **near-zero host CPU**. The price: a different programming model (§3), a sensitive fabric (§5), and real operational/security complexity (§8).

---

## 3. The verbs API & model

RDMA is not programmed with sockets. The API is **verbs** (`libibverbs`) — abstract operations a consumer requests of the NIC (the IBTA's **Channel Adapter**: HCA for InfiniBand, RNIC for RoCE/iWARP). The shift: you do not `send()`/`recv()` and block. You **post work requests onto queues** the NIC services asynchronously, and **reap completions** from a queue. It is inherently async ring-buffer programming — closer to `io_uring` than to BSD sockets.

### 3.1 The four core objects

| Object | What it is |
|---|---|
| **Queue Pair (QP)** | the connection endpoint: a Send Queue + a Receive Queue. You post Work Requests here. Roughly "the socket," but two queues, in the NIC. |
| **Completion Queue (CQ)** | where the NIC posts a Work Completion when a request finishes. One CQ serves many QPs. You poll or wait on it. |
| **Memory Region (MR)** | app memory **registered** with the NIC: pinned (unswappable), given `lkey` (local) and `rkey` (hand to a remote peer). The NIC may only touch registered memory. |
| **Protection Domain (PD)** | groups QPs and MRs; an op may only touch memory in the same PD. The coarse isolation boundary. |

### 3.2 Memory registration & pinning

The step with no socket analogue, and the one newcomers underestimate. For the NIC to DMA into a buffer **without the CPU**: (1) the pages must be **pinned** (never swapped/moved, since the NIC DMAs to physical addresses and cannot fault them back in), and (2) the NIC must hold the virtual→physical mapping plus keys. `ibv_reg_mr` does both. It is **expensive** (a syscall that pins pages and programs the NIC's translation tables), so it is done up front and amortized — register pools once, reuse, never per-op. Hence: RDMA apps register memory pools at startup, and registration-cache invalidation bugs are a classic footgun.

### 3.3 One-sided vs two-sided

| Class | Operations | Remote CPU? | Remote posts a RECV? | Model |
|---|---|---|---|---|
| **One-sided** | `READ`, `WRITE`, atomics (`FETCH_ADD`, `CMP_SWAP`) | **No** | **No** | "reach into the remote buffer" |
| **Two-sided** | `SEND` / `RECV` | **Yes** (consumed a RECV) | **Yes**, beforehand | message passing, like sockets |

- **One-sided** (`READ`/`WRITE`): the initiator names a remote virtual address + `rkey` (exchanged out-of-band at setup). The remote NIC services it autonomously; **the remote CPU never runs an instruction and never knows it happened**. The purest expression of RDMA — e.g. a KV store can `READ` a value straight out of a server's heap with zero server work. Catch: the initiator must know *where* to read/write, so designs pre-exchange buffer layouts or use a small two-sided control channel.
- **Two-sided** (`SEND`/`RECV`): the receiver must have **already posted a RECV** before the SEND arrives, or the message is dropped / the QP errors. Its CPU is involved (gets a completion) but there is still no copy and no kernel transport. Natural for request/response and MPI.

### 3.4 Completion model — polling vs events

The classic latency-vs-CPU trade ([08 §8.1](08_network_performance_tuning.md#81-interrupt-storm--napi); busy-poll trade in [../operating_system/06_io_models_async.md](../operating_system/06_io_models_async.md)):

| Model | How | Latency | CPU | Use when |
|---|---|---|---|---|
| **Busy poll** | spin on `ibv_poll_cq()` | lowest | a core pinned 100% | latency-critical: HPC, trading, low-latency KV |
| **Event channel** | arm CQ, block on the completion fd; NIC raises an IRQ | higher | ~zero while idle | throughput / many idle QPs, storage targets |

### 3.5 The verbs flow — end to end

```text
SETUP (once, control path):
  ibv_open_device     open the HCA/RNIC
  ibv_alloc_pd        create a Protection Domain
  ibv_reg_mr          register + PIN a buffer -> {lkey, rkey, addr}
  ibv_create_cq       create a Completion Queue
  ibv_create_qp       create a Queue Pair (SQ+RQ) bound to CQ + PD
  <exchange QP num, LID/GID, rkey, remote addr OUT-OF-BAND (e.g. TCP socket)>
  ibv_modify_qp       INIT -> RTR (ready-to-receive) -> RTS (ready-to-send)

DATA PATH (per op, NO syscall):
  ibv_post_send { opcode=RDMA_WRITE, local{addr,lkey}, remote{addr,rkey}, len }
        |  NIC DMAs the local buffer, segments, transmits
        |  remote NIC writes directly into remote registered buffer (no remote CPU)
        v
  ibv_poll_cq -> Work Completion (SUCCESS)   [poll, or wait on event channel]
```

The expensive, syscall-laden work is all in **setup**; the **data path is pure user-space ring manipulation + a doorbell write** — no kernel transition per op. That asymmetry is the point.

### 3.6 Tooling

```bash
# inventory: devices, ports, link state, GIDs
ibv_devices ; ibv_devinfo -v
rdma link show            # modern iproute2 rdma tool
rdma resource show        # live QPs / CQs / MRs

# canonical micro-benchmarks (perftest) -- "iperf3 for RDMA":
ib_write_bw  -d mlx5_0 <server>   # one-sided WRITE bandwidth
ib_write_lat -d mlx5_0 <server>   # WRITE latency (expect ~1-2 us in-rack)
ib_send_bw   -d mlx5_0 <server>   # two-sided SEND/RECV bandwidth

# RoCE health (see section 5):
show_gids                         # which GID index is RoCEv2 / which IP
ethtool -S <dev> | grep -iE 'pause|pfc|ecn|cnp|out_of_buffer|discard'
```

---

## 4. The fabrics — InfiniBand, RoCE, iWARP

RDMA is a *model*; three fabrics implement it, differing in what wire/switches they need and how reliability and congestion are handled.

- **InfiniBand (IB)** — a purpose-built, **lossless-by-design** fabric: its own link/network/transport, its own switches, addressed by LIDs from a **Subnet Manager** (not IP). Credit-based **link-level flow control** means a sender never transmits unless the receiver has buffer space, so the fabric **does not drop on congestion**. The gold standard for HPC and the largest AI clusters — but a separate, premium network alongside (not on top of) your Ethernet.
- **RoCE — RDMA over Converged Ethernet** — runs the IB transport over Ethernet. **RoCEv1**: IB transport in an Ethernet frame, **not routable** (single L2), largely historical. **RoCEv2**: IB transport over **UDP/IP** (dst port 4791), **routable** across L3, so it scales to a Clos fabric ([02 §leaf-spine](02_link_layer_switching.md)) — this is "RoCE" today. The catch — the dominant operational fact about RoCE — is that the IB transport *assumes a lossless fabric*, but Ethernet drops under congestion. So RoCEv2 needs the Ethernet made **lossless** via **PFC** and managed with **ECN/DCQCN** (§5). Get it wrong and it collapses rather than degrading gracefully.
- **iWARP — RDMA over TCP** — layers RDMA semantics (RDMAP→DDP→MPA) on ordinary **TCP** (RFC 5040–5044). Because TCP already provides reliability and congestion control, iWARP **tolerates a lossy network and routes over any IP network with no special switch config** — its operational advantage. The price: the complex TCP state machine runs *on the NIC*, historically meaning higher latency, more per-connection NIC memory, and less ecosystem momentum than RoCE.

| | **InfiniBand** | **RoCEv2** | **iWARP** |
|---|---|---|---|
| Underlying transport | native IB | IB transport over **UDP/IP** | **TCP**/IP |
| Wire / switches | dedicated IB | standard Ethernet (tuned lossless) | any Ethernet, any switch |
| Routable (L3) | yes (rare) | **yes** | **yes** |
| Lossless required | built-in (credits) | **yes — PFC/ECN (operator's job)** | **no** (TCP handles loss) |
| Congestion control | credits + IB CC | **DCQCN** (ECN-based) | TCP (CUBIC/etc.) |
| Latency (in-rack) | lowest (~1 µs) | very low (~1-2 µs) | higher (TCP on NIC) |
| Ecosystem | HPC, top AI | mainstream DC/AI/storage | niche, "works anywhere" |
| Operational difficulty | self-contained | **high — lossless tuning** | low |

> **Picking one:** dedicated HPC / largest training fabrics → **InfiniBand**. RDMA on existing Ethernet at scale, willing to invest in lossless tuning → **RoCEv2** (the mainstream choice). RDMA over a network you can't make lossless → **iWARP**. On a cloud → whatever the provider exposes (AWS **EFA**, §9.4).

---

## 5. Why lossless matters — PFC, ECN, DCQCN

The section most teams learn the hard way. RoCE's reliable-connected transport assumes in-order, drop-free delivery; one dropped packet forces a **go-back-N** retransmission of everything after it, catastrophic for throughput. So the Ethernet must be made *lossless* — controlling congestion *before* a buffer overflows (cross-ref [02 — lossless Ethernet / 802.1Qbb](02_link_layer_switching.md)). Two mechanisms at different timescales:

- **PFC — Priority Flow Control (802.1Qbb)** — the fast, blunt hammer. When a switch's per-priority ingress buffer fills past a threshold, it sends a **PAUSE** *upstream* telling the previous hop to stop that class. Makes the link lossless — but it is **hop-by-hop backpressure** and dangerous: **head-of-line blocking** (PAUSE stops a whole class, not a flow); **congestion spreading** (one hot receiver pauses an ever-widening tree of "victim" senders); and **PFC deadlock** (a cyclic buffer dependency — possible after a route change in a Clos — can form a PAUSE cycle and **wedge the fabric permanently**, the failure the Microsoft "RDMA at Scale" paper works hardest to prevent).
- **ECN + DCQCN — the smart, end-to-end controller** — PFC alone is too coarse, so RoCE adds an end-to-end loop so PFC almost never fires: switches **mark** (don't drop) packets with ECN as the queue builds ([08 §5.2](08_network_performance_tuning.md#52-ecn--congestion-signalling-without-loss)); the receiver NIC sees the mark and returns a **CNP (Congestion Notification Packet)**; **DCQCN** (the de-facto RoCE CC, NIC-implemented) **rate-limits the offending flow in hardware**, then probes back up — a DCTCP-like decrease/gentle-increase loop. The goal: keep queues shallow so ECN does the work and **PFC is the last resort**.

> **Layered defense:** DCQCN/ECN throttles flows *early and per-flow* so queues stay short; PFC is the *safety net* guaranteeing no drops in the brief window before DCQCN reacts. A correct deployment lives almost entirely in the ECN regime. A *broken* one relies on PFC for everyday congestion — and then suffers HOL blocking, spreading, and deadlock.

### 5.1 Symptom / Cause / Fix

| Symptom | Cause | Fix |
|---|---|---|
| **RoCE throughput collapses under load**; `ib_write_bw` great point-to-point, terrible in production | fabric **not actually lossless** — drops → go-back-N retransmit storms | verify PFC enabled on the *right priority* end-to-end; check `ethtool -S` for `rx_discards`/`out_of_buffer`; confirm DCQCN/ECN marking on |
| Throughput fine but huge latency / unrelated flows stall | **PFC HOL blocking & congestion spreading** — PAUSE storms throttling victims | set ECN thresholds *below* PFC so DCQCN reacts first; isolate RDMA in its own PFC priority/queue |
| Whole fabric/region wedges, no traffic, no link down | **PFC deadlock** from a cyclic buffer dependency (often post link/route flap) | break the cycle (deadlock-free / up-down routing); cap PFC scope, prefer ECN-dominant configs |
| PFC counters climbing in steady state | DCQCN not engaging — ECN not marked, CNPs lost, or thresholds inverted | ECN must mark *before* PFC pauses; ensure CNP priority isn't itself paused; check switch WRED/min-max |
| Works on one switch model, breaks across vendors | inconsistent PFC priority / DSCP→queue / ECN config across hops | standardize DSCP/priority mapping fabric-wide; every hop must agree (RoCEv2 traffic class rides IP DSCP) |

> **"RoCE tuning is hard" is not folklore.** Correctness is a property of the *whole fabric*: a bad DSCP-to-priority mapping or ECN threshold on **one switch** can make the entire deployment unreliable, and the failures (deadlock, spreading) are non-local and hard to reproduce. This is the biggest reason teams choose InfiniBand (lossless by construction) or a managed cloud transport like EFA (§9.4) that hides the fabric.

---

## 6. Where RDMA is used

A specialist tool; where it appears, microseconds and CPU cycles are the product.

- **HPC / MPI** — the original home. MPI (Open MPI, MPICH) maps point-to-point and collectives onto verbs; one-sided `PUT`/`GET` map directly to RDMA `WRITE`/`READ`. Codes on thousands of nodes live or die on all-reduce latency.
- **Distributed AI training — the current driver.** Synchronous data-parallel training is a huge, latency-sensitive **all-reduce** of gradients every step. RDMA, especially **GPUDirect RDMA** (NIC DMAs straight into GPU memory, skipping host RAM and CPU — §9.1), keeps thousands of GPUs from idling on communication. The largest clusters use IB or carefully tuned RoCE for exactly this.
- **High-performance storage — NVMe-oF** — carries the NVMe command set over RDMA, giving remote-SSD latency near local PCIe NVMe (§9.2). The backbone of disaggregated all-flash storage.
- **Low-latency databases, caches, KV stores** — one-sided `READ` to fetch values from a remote heap **with no remote CPU** (FaRM-style designs), and `WRITE` for log replication where tail latency dominates.
- **Hyperscaler east-west fabrics** — Microsoft and others run RDMA on production datacenter Ethernet for intra-DC storage and service traffic, because at their packet rates kernel TCP would cost too many cores.

---

## 7. Kernel bypass more broadly — RDMA vs DPDK vs XDP/AF_XDP

"Kernel bypass" is an umbrella over techniques that share a goal (kernel off the hot path) but differ in *what abstraction they bypass to* (tie back to [08 §8.3](08_network_performance_tuning.md#83-dpdk--xdp--eliminating-the-kernel-from-the-data-path), [10](10_cloud_sdn_overlays.md)).

| | **RDMA** | **DPDK** | **XDP / AF_XDP** |
|---|---|---|---|
| Abstraction | **remote memory** (READ/WRITE/SEND) | **raw packets** in userspace | **raw frames** at driver hook (eBPF) / fast socket |
| Reliability | offloaded **on the NIC** (RC) | **you build it** | none — you process frames |
| Remote CPU on data path | **no** (one-sided) | yes | yes |
| Copies | **zero** (NIC↔registered buffer) | zero (PMD maps rings) | zero (AF_XDP) / N/A (XDP) |
| Cost model | NIC busy or app polls CQ | **dedicated cores busy-poll** | kernel context (XDP) / app (AF_XDP) |
| Keeps kernel for other traffic | device yes, flows no | **no — owns the NIC** | **yes** |
| Canonical use | HPC, AI all-reduce, NVMe-oF, KV | SW routers/LBs/firewalls, NFV | DDoS drop, L4 LB (Katran/Cilium), filtering |

> **One-line discriminator:** reach for **RDMA when the abstraction you want is *memory*** (move bytes between two buffers as fast as physics allows, ideally without the remote CPU). Reach for **DPDK/XDP when the abstraction is *packets*** (forward, drop, rewrite, load-balance frames). AI training wants RDMA; a software LB wants XDP; a userspace 5G UPF / virtual router wants DPDK. They coexist — a SmartNIC (§9.3) may run all three.

---

## 8. Trade-offs — and why most apps don't need it

RDMA is not a faster socket you drop in. Why most applications should *not* use it:

- **A lower-level model.** No `connect()/send()/recv()`. You manage QPs, CQs, MRs, pinning, async completions, QP state transitions, and out-of-band key/address exchange — closer to writing a driver than a network app. The error model is unforgiving (one bad WR can move a QP to an unrecoverable error state needing teardown).
- **Hardware/fabric requirements.** RDMA-capable NICs on **both** ends and — for RoCE — a fabric configured lossless end-to-end (§5). A capital and operational commitment, not a library import.
- **Debugging difficulty.** When the data path bypasses the kernel, your tools go blind: `tcpdump` sees nothing of one-sided ops, `ss`/`netstat` show nothing, counters live in NIC hardware (`ethtool -S`, `rdma resource show`). RoCE failures are non-local (§5.1).
- **Security — memory exposure.** RDMA's superpower is its danger: you hand a peer an `rkey` + address and it can **read/write your process memory with no CPU mediation**. A leaked or guessable `rkey`, or too-coarse a PD, is a direct memory-disclosure/corruption vector. Registration also **pins pages** (unswappable), so a careless app can lock down large RAM. Isolation (PDs, per-tenant MRs, `rkey` rotation) must be designed — much of why exposing raw RDMA to untrusted tenants took dedicated sandboxed transports (§9.4).
- **Diminishing returns.** If your service spends milliseconds in app logic at modest message rates, 1 µs vs 30 µs and a saved copy are noise. RDMA pays off only when the network is genuinely the bottleneck *and* volume is high: HPC, AI training, storage fabrics, lowest-latency datastores. Otherwise, tuned kernel TCP + [08](08_network_performance_tuning.md) is the right answer.

> **Staff judgment:** RDMA is correct when (a) the workload is memory-to-memory bulk or microsecond-latency, (b) the volume would otherwise burn many cores on kernel TCP, and (c) you control both endpoints and the fabric. Absent all three, its complexity outweighs the win.

---

## 9. Advanced

### 9.1 GPUDirect RDMA & NCCL — the AI training fabric

The bottleneck in data-parallel training is the per-step gradient **all-reduce** across all GPUs. Naively, gradients go GPU memory → host RAM (PCIe copy) → kernel → NIC and back — three copies and the CPU in the loop every step. **GPUDirect RDMA** removes all of it: the NIC DMAs **directly to/from GPU memory** (the GPU buffer is the registered MR), so a gradient flows GPU↔NIC↔wire↔NIC↔GPU with **no host staging and no CPU copy**. **NCCL** is what frameworks (PyTorch DDP/FSDP) actually call: it implements ring/tree all-reduce and picks the transport — NVLink/NVSwitch within a node, GPUDirect RDMA over IB/RoCE between nodes — scaling collectives to tens of thousands of GPUs while keeping them compute-bound. This single use case is why RDMA fabrics are a strategic, capacity-constrained resource in every AI build-out.

### 9.2 NVMe-oF — disaggregated storage at near-local latency

**NVMe over Fabrics** carries the NVMe submission/completion queue model — itself a ring/doorbell design much like verbs — over a network. With the **RDMA binding** (over RoCE or IB), an NVMe command and its data move host↔remote-SSD zero-copy with no remote CPU, within a few µs of local PCIe NVMe. This makes **storage disaggregation** practical: compute and flash become independent pools joined by an RDMA fabric instead of every server carrying captive disks. (NVMe-oF also has a TCP binding — simpler, no special fabric, higher latency — the RoCE-vs-iWARP trade reappearing a layer up.)

### 9.3 SmartNICs / DPUs — offloading the host

A **SmartNIC / DPU** (NVIDIA BlueField, AWS Nitro, Intel IPU) is a NIC with its own CPU cores, memory, and programmable packet engines. It extends bypass from "the host app polls the NIC" to "the NIC *is a computer* running the infrastructure data plane." DPUs offload and isolate: RDMA transport, the **virtual switch / overlay encap-decap** ([10 — VXLAN/GENEVE](10_cloud_sdn_overlays.md)), firewalling, storage virtualization (presenting NVMe-oF as local NVMe), and encryption — all on the NIC, freeing host cores for tenants and creating a hardware trust boundary. This underpins modern hyperscaler hosts: the **DPU runs network/storage data plane**, the host CPU runs only customer code.

### 9.4 Convergence with the cloud — EFA and managed RDMA

Clouds wanted RDMA's performance for HPC/AI without exposing the brittle, security-fraught raw fabric to untrusted multi-tenant networks. AWS's answer is **EFA (Elastic Fabric Adapter)**: an OS-bypass NIC with a custom transport — **SRD (Scalable Reliable Datagram)** — that deliberately *departs* from classic RC RDMA. SRD does **not** require a lossless fabric; it sprays packets across **many paths (multipath/ECMP)**, tolerates out-of-order delivery, and does reliability/CC in the NIC suited to a large lossy cloud network — sidestepping the PFC/DCQCN fragility of §5. Apps reach it through **`libfabric`** (OFI) rather than raw verbs, with NCCL/MPI beneath. The pattern generalizes: the cloud exposes RDMA-*class* performance through a **managed, sandboxed transport** that hides the fabric, trades strict in-order RC semantics for robustness at scale, and integrates with the DPU/Nitro model (§9.3). Same theme as the rest of the folder — the cloud wraps a powerful but dangerous primitive in software ([10](10_cloud_sdn_overlays.md)) so mortals can use it.

---

## 10. Trade-offs summary

- **Kernel TCP is the bottleneck at 100/200/400 GbE**, not the wire — two copies + a context switch × tens of millions/s exceeds core/memory budgets. RDMA removes CPU and kernel from the data path; DPDK/XDP do the same for *packet* processing.
- **RDMA = zero-copy + kernel-bypass + remote-CPU-bypass + HW transport**, all at once. One-sided `READ`/`WRITE` touch remote memory with **no remote CPU**; two-sided `SEND`/`RECV` is message passing with a posted receive.
- **Queues, not sockets:** QP/CQ/MR/PD, register-and-pin up front, post work requests, reap completions (poll for latency, events for idle CPU).
- **Fabric is the deciding factor:** InfiniBand is lossless by construction (HPC/AI gold standard); RoCEv2 reuses routable Ethernet but **demands a lossless fabric (PFC/ECN/DCQCN)** and is operationally hard; iWARP runs over TCP and works anywhere but is slower/niche.
- **RoCE's failures are non-local** — PFC HOL blocking, congestion spreading, deadlock — so correctness is a whole-fabric property; "RoCE tuning is hard" is real.
- **Choose by abstraction:** memory → RDMA; packets → DPDK/XDP. They coexist (often on one DPU).
- **Most apps should not use RDMA** — different model, special hardware/fabric, blind to standard tools, real memory-exposure surface. It wins only when the network is genuinely the bottleneck *and* you control both ends and the fabric.

## 11. Key Takeaways

1. At hundred-gigabit speeds the **per-packet kernel cost dominates the wire**; RDMA does memory-to-memory transfer with **zero copies, no syscall per op, and no remote CPU** — ~1–2 µs in-rack and near-zero host CPU.
2. Learn the **verbs model** (QP, CQ, MR+pinning, PD; one-sided vs two-sided; poll vs event). Cost is all in *setup/registration*; the data path is user-space ring + doorbell.
3. **One-sided ops are the special sauce** — reading/writing a remote buffer with the remote CPU asleep is what sockets can never do.
4. **InfiniBand vs RoCE vs iWARP** is fundamentally *what makes the network reliable*: built-in credits, a tuned-lossless Ethernet, or TCP — descending hardware cost, ascending latency.
5. **RoCE lives or dies on a lossless fabric.** ECN/DCQCN should carry everyday congestion (per-flow, early); PFC is the last-resort safety net. Invert that and you get collapse/HOL/deadlock.
6. **Kernel bypass splits by abstraction:** RDMA for memory, DPDK/XDP for packets ([08](08_network_performance_tuning.md), [10](10_cloud_sdn_overlays.md)). Don't reach for any until tuned kernel TCP is provably the bottleneck.
7. The frontier is **GPUDirect/NCCL**, **NVMe-oF**, **SmartNIC/DPU** offload, and **managed cloud transports (EFA/SRD)** that deliver RDMA-class performance while hiding the dangerous fabric.

> Related: [08 — Network Performance & Tuning](08_network_performance_tuning.md) (per-packet cost, DPDK/XDP, NIC offloads) · [02 — Link Layer & Switching](02_link_layer_switching.md) (lossless Ethernet, PFC, leaf-spine) · [10 — Cloud Networking, SDN & Overlays](10_cloud_sdn_overlays.md) (DPU offload, overlays, the managed cloud) · [../operating_system/06_io_models_async.md](../operating_system/06_io_models_async.md) (DMA, zero-copy, busy-poll vs event). When an RDMA fabric breaks in production, the discipline is the same as the rest of this folder: localize to a layer, back the diagnosis with a counter, and remember the data path is invisible to your socket tools.
