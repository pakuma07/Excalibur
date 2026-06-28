# 08 — Network Performance & Tuning

> **Audience:** staff/principal. You know what TCP and a socket are. This doc is about *where the milliseconds and the megabits actually go*, and how to reason about — and fix — a network that is "slow" from first principles rather than by twiddling sysctls until the graph moves.
>
> **Primary sources:** Gregg, *Systems Performance* (2nd ed., ch. 10 Network); Kurose & Ross, *Computer Networking: A Top-Down Approach*; Gregg, *BPF Performance Tools* (ch. 10); Cloudflare engineering blog (BBR, buffer bloat, kernel-bypass); AWS networking docs (ENA, placement groups); the FQ-CoDel RFC 8290, CoDel RFC 8289, TCP window-scaling RFC 7323; the Linux kernel networking & ip-sysctl documentation.

---

## 1. Why this matters at scale

Two systems can run the identical code and differ 100× in user-visible performance because of the network path between them. At scale the network is where you discover that:

1. **Latency and bandwidth are independent axes.** You cannot buy your way out of latency with more bandwidth, and you cannot buy your way out of a bandwidth ceiling by lowering latency. A fat transcontinental pipe with 80 ms RTT will *crawl* on a default-tuned TCP connection no matter how many gigabits it nominally carries — because the **bandwidth-delay product** exceeds the window. This single misunderstanding costs more engineering hours than any other in network performance.
2. **The kernel does an enormous amount of work per packet** — interrupts, softirqs, copies, context switches — and that per-packet cost, not the wire, is the bottleneck for small-packet and high-PPS workloads. This is why DPDK, XDP, and kernel-bypass exist.
3. **Queues, not links, cause the latency you feel.** A saturated link with a giant buffer ("buffer bloat") adds *seconds* of delay while reporting zero packet loss and full throughput. The fix is an active queue discipline, not a bigger buffer.

Staff engineers are expected to localize a problem to **latency vs throughput**, then to a **layer** (app, socket buffer, congestion control, qdisc, NIC, wire), and to back the diagnosis with a measurement — `ss`, `tcpdump`, `iperf3`, not vibes.

---

## 2. The metrics — define them precisely

Imprecise vocabulary is the root of most network-performance arguments. Pin these down.

| Metric | Definition | Unit | Notes |
|---|---|---|---|
| **Latency (RTT)** | round-trip time for a packet + its ack | seconds (ms, µs) | the *one-way* delay is ~RTT/2 but rarely symmetric |
| **Bandwidth** | theoretical max bit rate of the link | bits/s | a property of the *link*, fixed |
| **Throughput** | bits/s actually delivered, all overhead included | bits/s | ≤ bandwidth; what `iperf3` reports |
| **Goodput** | *application* bytes delivered ÷ time | bits/s | throughput minus headers, retransmits, ACKs |
| **Jitter** | variation in latency (stddev or p99−p50 of RTT) | seconds | murder for real-time (VoIP, gaming, RPC tails) |
| **Packet loss** | fraction of packets that never arrive | % | TCP treats loss as a congestion signal |
| **PPS** | packets per second | 1/s | the metric that matters for the *kernel*, not the wire |

> **Goodput < Throughput < Bandwidth.** A 1 Gbit/s link (bandwidth) might deliver 940 Mbit/s of TCP segments (throughput) of which 905 Mbit/s is your payload (goodput) after Ethernet/IP/TCP headers and ACK traffic. If you quote one number, quote *goodput* — it is what the user gets.

### 2.1 Two regimes, two diagnoses

```
            small messages / RPC / interactive   <-- LATENCY-bound
            (you wait on RTTs; bytes are tiny)
   --------------------------------------------------------------------
            bulk transfer / backup / replication  <-- THROUGHPUT-bound
            (you wait on the pipe filling)
```

The first thing to establish on any "network is slow" ticket is **which regime you are in**. They have disjoint fixes (§9, §10). A throughput fix (bigger buffers) does nothing for a latency problem and vice versa.

---

## 3. The latency budget

End-to-end one-way delay decomposes into four additive terms (Kurose & Ross §1.4):

```
d_total = d_proc  +  d_queue  +  d_trans  +  d_prop
          (nodal     (waiting    (push        (speed of
           processing) in buffer) bits onto    light in
                                  the wire)     the medium)
```

| Term | Cause | Order of magnitude | Reducible by |
|---|---|---|---|
| **d_prop** propagation | distance ÷ signal speed (~2×10⁸ m/s in fiber) | ~5 µs/km; ~50 ms NY↔London | moving closer (CDN/edge), shorter paths |
| **d_trans** serialization | packet_bits ÷ link_bandwidth | 1500 B over 1 Gbit/s = 12 µs | faster link, smaller packets |
| **d_queue** queuing | congestion at routers/NICs | 0 → seconds (buffer bloat!) | AQM, less load, traffic shaping |
| **d_proc** processing | route lookup, checksums, copies, softirq | µs (kernel) to ms (deep stacks) | offloads, kernel-bypass, less hops |

### 3.1 Propagation dominates over distance; serialization over fat packets on slow links

Worked example — a 1500-byte packet:

- **Over a 10 km metro fiber, 10 Gbit/s link:** d_prop = 10 km × 5 µs/km = **50 µs**; d_trans = 1500×8 / 10e9 = **1.2 µs**. Propagation dominates 40:1.
- **Over a 256 kbit/s legacy WAN, 1 km:** d_prop = **5 µs**; d_trans = 12000 / 256000 = **47 ms**. Serialization dominates by 4 orders of magnitude.

> Lesson: on **long fast paths** (datacenter↔datacenter, internet), *propagation* is the floor and you fight it with **placement** (CDN, edge, region selection), not tuning. On **slow links**, *serialization* dominates and you fight it with smaller packets / compression / faster links.

The speed of light is the one budget line you cannot tune. ~50 ms NY↔London is physics; the only knobs are "be closer" and "make fewer round trips" (§10).

---

## 4. The bandwidth-delay product & TCP window scaling

This is the single most important quantitative idea in network performance.

> **BDP = bandwidth × RTT.** It is the number of bytes "in flight" needed to keep the pipe *full*. TCP cannot have more unacknowledged data outstanding than the **receive window** (and the congestion window). If `window < BDP`, the sender stalls waiting for ACKs and throughput is capped at `window / RTT` — regardless of the link speed.

```
throughput_max = min(cwnd, rwnd) / RTT          (the fundamental TCP equation)
```

### 4.1 Worked BDP

A 1 Gbit/s link with 80 ms RTT (NY↔London):

```
BDP = 1e9 bits/s × 0.080 s = 8e7 bits = 10,000,000 bytes ≈ 10 MB
```

If the receive window is the historical 64 KiB max (16-bit window field, no scaling):

```
throughput = 65536 B / 0.080 s = 819,200 B/s ≈ 6.5 Mbit/s   (!!)
```

You have a **gigabit** link delivering **6.5 megabit** because the window is 1500× too small. To fill it you need a ~10 MB window, which requires **TCP window scaling** (RFC 7323): a TCP option negotiated at handshake that left-shifts the 16-bit window by up to 14 bits (max window ~1 GiB). It is on by default in Linux (`net.ipv4.tcp_window_scaling=1`) but the *buffer* must be allowed to grow to BDP — that is what `tcp_rmem`/`tcp_wmem` autotuning does (§6).

### 4.2 Computing the required window — runnable

```python
"""bdp.py — compute the bandwidth-delay product and the socket buffer you need
to saturate a long-fat-network (LFN) path. Run: python bdp.py"""

def bdp_bytes(bandwidth_bps: float, rtt_seconds: float) -> float:
    """Bytes in flight required to keep the pipe full."""
    return bandwidth_bps * rtt_seconds / 8.0

def max_throughput_bps(window_bytes: float, rtt_seconds: float) -> float:
    """TCP throughput ceiling given a fixed window."""
    return window_bytes * 8.0 / rtt_seconds

def report(name: str, bandwidth_gbit: float, rtt_ms: float):
    bw = bandwidth_gbit * 1e9
    rtt = rtt_ms / 1000.0
    bdp = bdp_bytes(bw, rtt)
    capped = max_throughput_bps(65536, rtt)          # 64 KiB, no scaling
    print(f"{name:<22} BW={bandwidth_gbit:>5} Gbit/s  RTT={rtt_ms:>4} ms")
    print(f"  required window (BDP)  = {bdp/1e6:8.2f} MB")
    print(f"  with 64 KiB window cap = {capped/1e6:8.2f} Mbit/s "
          f"({capped/bw*100:.2f}% of link)\n")

if __name__ == "__main__":
    report("LAN (same rack)",      10,  0.1)
    report("Cross-AZ",             10,  1.0)
    report("Cross-region (US)",    10, 35.0)
    report("NY <-> London",         1, 80.0)
    # sanity: BDP scales linearly with both terms
    assert abs(bdp_bytes(1e9, 0.080) - 10_000_000) < 1
    assert max_throughput_bps(65536, 0.080) < 7e6   # the 6.5 Mbit/s disaster
    print("assertions passed")
```

> **Rule:** before touching any buffer sysctl, compute BDP. Your `tcp_rmem`/`tcp_wmem` *maximum* must exceed the BDP of your fattest path, or autotuning can never open the window far enough.

---

## 5. Buffer bloat & AQM

The naive intuition "bigger router buffers = fewer drops = better" is **wrong** and gives you buffer bloat: a massively over-buffered queue that fills up and stays full, adding standing latency.

```
Sender ---> [ router queue: 1000 packets @ 1 Gbit/s ] ---> Receiver
            depth 1000 × 12 µs serialization = 12 ms of STANDING delay
            (and a 10,000-packet buffer = 120 ms, while showing 0% loss!)
```

TCP's loss-based congestion control (Reno/CUBIC) *needs* a signal to back off. A deep buffer hides loss, so TCP keeps pushing, the queue stays full, and every packet — including your latency-sensitive ones sharing the link — eats the full queue delay. You see full throughput, zero loss, and terrible latency. That is the buffer-bloat signature.

### 5.1 The fix: Active Queue Management

AQM drops/marks packets *before* the buffer is full, signalling congestion early.

| AQM | Idea | Why it wins |
|---|---|---|
| **CoDel** (RFC 8289) | Tracks the *sojourn time* (how long packets sit in the queue), not queue length. If the minimum sojourn over a window exceeds a target (5 ms), it drops packets at an increasing rate. Length-agnostic, no tuning. | Distinguishes a *good* queue (transient burst) from a *bad* standing queue. Self-tuning. |
| **FQ-CoDel** (RFC 8290) | CoDel + **fair queuing**: hashes flows into separate sub-queues, round-robins them, applies CoDel per queue, and prioritizes *sparse* (latency-sensitive) flows. | A single bulk download cannot starve your SSH/VoIP packets. The default `qdisc` on modern Linux. |
| **CAKE** | FQ-CoDel + shaping + per-host fairness + DiffServ awareness | the "one knob" home/edge solution |

Check and set the qdisc:

```bash
# what queue discipline is on the interface?
tc qdisc show dev eth0

# replace it with fq_codel (modern default; great for shared/edge links)
sudo tc qdisc replace dev eth0 root fq_codel

# fq (fair-queue, pacing-friendly) is preferred WITH BBR on servers:
sudo tc qdisc replace dev eth0 root fq

# inspect drops/marks to confirm AQM is acting
tc -s qdisc show dev eth0
```

> **Pairing rule:** on a **server** doing bulk egress with **BBR** congestion control, use `fq` (it provides the pacing BBR relies on). On a **shared/edge/contended** link (home router, branch office), use `fq_codel`. CUBIC + `fq_codel` is a safe general default.

### 5.2 ECN — congestion signalling without loss

**Explicit Congestion Notification** lets AQM *mark* a packet (set 2 bits in the IP header) instead of dropping it; the receiver echoes the mark and the sender backs off without a retransmit. `sysctl net.ipv4.tcp_ecn=1` (negotiated; `2` = accept but don't initiate). Pairs naturally with CoDel/DCTCP in datacenters.

---

## 6. Kernel network-stack tuning (sysctl)

These are the knobs that matter, with *why*, not a copy-paste blob. Read `sysctl <name>` first; change with `sysctl -w` (runtime) and persist in `/etc/sysctl.d/`.

### 6.1 Socket buffers (the BDP knobs)

```bash
# format: "min default max" (bytes). Autotuning grows from default toward max.
sysctl net.ipv4.tcp_rmem      # receive: e.g. 4096 131072 6291456
sysctl net.ipv4.tcp_wmem      # send:    e.g. 4096  16384 4194304
sysctl net.core.rmem_max      # ceiling for SO_RCVBUF (and tcp_rmem max)
sysctl net.core.wmem_max
```

The `max` values must exceed your **BDP** (§4) or the window can never open enough on long-fat paths. For a 10 MB BDP, set `rmem_max`/`wmem_max` ≥ ~16 MB and bump the `tcp_rmem`/`tcp_wmem` max accordingly. Do **not** set them to gigabytes blindly — oversized buffers reintroduce buffer bloat at the endpoint and waste memory across millions of sockets.

### 6.2 Connection acceptance & backlog

```bash
sysctl net.core.somaxconn          # max accept() backlog (listen() ceiling). Default 4096 on modern kernels; raise for high-accept servers.
sysctl net.ipv4.tcp_max_syn_backlog  # half-open (SYN_RECV) queue depth
sysctl net.core.netdev_max_backlog   # packets queued for the stack when the NIC out-paces the CPU
```

If `ss -lnt` shows `Recv-Q` at the listen socket pinned at the backlog and you see `nstat -az | grep -i ListenOverflows` climbing, your app isn't `accept()`-ing fast enough or `somaxconn` is too low.

### 6.3 Ephemeral ports & connection churn

```bash
sysctl net.ipv4.ip_local_port_range   # e.g. 32768 60999 -> ~28k outbound conns per (dst ip,port)
sysctl net.ipv4.tcp_tw_reuse          # 1: reuse TIME_WAIT sockets for new OUTBOUND conns (safe with timestamps)
sysctl net.ipv4.tcp_fin_timeout       # how long FIN_WAIT_2 lingers
```

A client making many short-lived connections exhausts the ephemeral range and piles up `TIME_WAIT` sockets (60 s each by default). The right fix is **connection pooling / keepalive** (§10), not just `tcp_tw_reuse`. Widen the range only as a stopgap.

### 6.4 Congestion control

```bash
sysctl net.ipv4.tcp_available_congestion_control   # what's loaded (cubic, reno, bbr...)
sysctl net.ipv4.tcp_congestion_control             # the active default
# switch to BBR (model-based; great on lossy/long paths, e.g. internet egress):
sudo modprobe tcp_bbr
sudo sysctl -w net.ipv4.tcp_congestion_control=bbr
sudo sysctl -w net.core.default_qdisc=fq           # BBR wants fq pacing
```

| CC algorithm | Signal | Best for | Watch out |
|---|---|---|---|
| **CUBIC** (Linux default) | packet loss | LAN, general, low-loss | collapses on random (non-congestion) loss; fills buffers |
| **BBR** (Google) | bottleneck bandwidth + RTT estimate | long/lossy paths, internet, CDN edge | v1 can be unfair to CUBIC; use BBRv2/v3 where available; needs `fq` |
| **Reno** | loss | textbooks, reference | conservative, slow recovery |
| **DCTCP** | ECN marks | datacenters with ECN-capable switches | requires ECN end-to-end |

### 6.5 A high-throughput server cheat-sheet (`/etc/sysctl.d/99-net-tuning.conf`)

```bash
# --- socket buffers: sized for a ~10 MB BDP path (e.g. cross-region 10G) ---
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_rmem = 4096 131072 16777216
net.ipv4.tcp_wmem = 4096 65536  16777216
net.ipv4.tcp_window_scaling = 1            # negotiate large windows (RFC 7323)

# --- congestion control + pacing for internet-facing bulk egress ---
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq

# --- accept path for a high-connection server ---
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.core.netdev_max_backlog = 250000       # raise for 10G+ NICs / high PPS

# --- connection churn / ephemeral ports for an egress-heavy box ---
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15

# --- misc latency/throughput ---
net.ipv4.tcp_slow_start_after_idle = 0     # don't reset cwnd on idle (keepalive APIs!)
net.ipv4.tcp_mtu_probing = 1               # discover/avoid PMTU black holes
net.ipv4.tcp_ecn = 1                       # explicit congestion notification

# apply: sudo sysctl --system
```

> **Caveat:** `tcp_slow_start_after_idle=0` is one of the highest-leverage, least-known fixes for "every request after a quiet period is slow" — a kept-alive HTTP/2 or gRPC connection that idles reverts to a tiny `cwnd` and re-pays slow start. Disabling idle reset keeps the window open.

---

## 7. NIC offloads — moving per-packet work off the CPU

The CPU cost of building/parsing one packet is fixed; at 10–100 Gbit/s with 1500-byte frames you'd burn whole cores on it. Offloads batch or delegate that work.

| Offload | What it does | Direction | Effect |
|---|---|---|---|
| **TSO** (TCP Segmentation Offload) | kernel hands the NIC one big (64 KiB) buffer; NIC splits it into MSS-sized segments | TX | fewer trips through the stack per byte |
| **GSO** (Generic Segmentation Offload) | same idea in software, deferred to the last moment before the driver | TX | TSO's benefit even without HW support |
| **LRO** (Large Receive Offload) | NIC merges many received segments into one big buffer | RX | fewer stack traversals; **lossy** of per-packet info — bad for routers/bridges |
| **GRO** (Generic Receive Offload) | software, *lossless* re-segmentable merge | RX | LRO's benefit, forwarding-safe; the default |
| **Checksum offload** | NIC computes/verifies IP/TCP/UDP checksums | both | saves a full pass over payload bytes |

```bash
# inspect offloads
ethtool -k eth0 | grep -E 'tcp-segmentation|generic-segmentation|generic-receive|large-receive|rx-checksumming'
# toggle (e.g. disable GRO when capturing for accurate per-packet timing)
sudo ethtool -K eth0 gro off
```

> **Gotcha:** GRO/LRO **coalesce** packets, so `tcpdump` on the host sees giant "frames" that never existed on the wire and your latency/PPS measurements are wrong. Disable GRO (`ethtool -K eth0 gro off`) while capturing for timing analysis, or capture at a tap/switch.

### 7.1 Spreading the load across cores: RSS / RPS / RFS

A single NIC interrupt queue pins all RX processing to one CPU — a hard PPS ceiling. Scale out:

- **RSS (Receive Side Scaling)** — *hardware*: the NIC hashes each packet's flow tuple to one of N hardware queues, each with its own interrupt → spread across cores. Configure queue count with `ethtool -L eth0 combined N` and IRQ affinity with `set_irq_affinity`.
- **RPS (Receive Packet Steering)** — *software* RSS for NICs with one queue: the kernel hashes flows to CPUs in softirq. Set via `/sys/class/net/eth0/queues/rx-0/rps_cpus` (a CPU bitmask).
- **RFS (Receive Flow Steering)** — RPS that steers a flow to the CPU **where the consuming application runs**, maximizing cache locality. `net.core.rps_sock_flow_entries` + per-queue `rps_flow_cnt`.

```bash
ethtool -l eth0                 # show queue counts
ethtool -L eth0 combined 8      # use 8 RX/TX queues (1 per core ideal)
# RPS: steer rx-0 to CPUs 0-7 (bitmask 0xff)
echo ff | sudo tee /sys/class/net/eth0/queues/rx-0/rps_cpus
```

---

## 8. Interrupts, NAPI, and the cost of copies & context switches

### 8.1 Interrupt storm → NAPI

At high PPS, one hardware interrupt **per packet** would livelock the CPU (it spends all its time in interrupt context, never running the app). **NAPI** (New API) solves this: on the first packet the NIC raises an IRQ, the driver then **disables that IRQ and polls** the ring buffer in softirq, draining many packets per poll, and re-enables the IRQ only when the ring drains. Interrupt-driven at low load (low latency), polled at high load (high throughput) — the best of both.

**Interrupt coalescing** is the hardware complement: the NIC waits to batch a few packets (or a few µs) before raising an IRQ.

```bash
ethtool -c eth0                       # show coalescing settings
sudo ethtool -C eth0 rx-usecs 50      # raise: more throughput, +latency
sudo ethtool -C eth0 rx-usecs 0       # lower: less latency, more IRQs/CPU
```

> The coalescing trade-off **is** the latency-vs-throughput trade-off made concrete: more coalescing batches work (throughput up, CPU down) at the cost of added latency. Tune toward your regime (§2.1).

### 8.2 The cost of copies and context switches

A "normal" `recv()` of a packet pays, roughly:

```
NIC DMA -> kernel skb (RX ring)        [DMA, no CPU copy]
softirq: protocol processing            [CPU]
copy: kernel skb -> socket buffer       [COPY #1]
context switch: wake the blocked thread [CTX SWITCH]
copy: socket buffer -> user buffer       [COPY #2, the read() syscall]
```

Each **copy** is a full pass over the bytes through the memory hierarchy; each **context switch** is ~1–5 µs of direct cost plus cache/TLB pollution. At millions of PPS these dominate. Mitigations:

- **`sendfile()` / `splice()` / `MSG_ZEROCOPY`** — avoid the user↔kernel copy for bulk transfer (e.g., serving static files, proxying).
- **`io_uring`** — batched, shared-ring submission/completion that amortizes syscalls and context switches.
- **Kernel-bypass** — the nuclear option (§8.3).

### 8.3 DPDK & XDP — eliminating the kernel from the data path

| Approach | Where it runs | How it bypasses cost | Use when |
|---|---|---|---|
| **DPDK** | userspace, **poll-mode driver** owns the NIC | no interrupts, no kernel stack, no copies — userspace maps the NIC rings directly and busy-polls; packets go straight to your app's memory | NFV, software routers/LBs, trading — willing to dedicate cores to 100% busy-poll |
| **XDP** (eBPF) | **in-kernel, at the driver hook**, before skb allocation | run an eBPF program on the raw RX frame the instant it arrives — drop/redirect/transmit *before* the expensive skb + stack work | DDoS drop, L4 load balancing (Cilium, Katran), per-packet filtering at line rate **without** giving up the kernel for everything else |

```text
NORMAL:   NIC IRQ -> skb alloc -> netfilter -> routing -> socket -> COPY -> app
XDP:      NIC -> [eBPF program] -> {XDP_DROP | XDP_TX | XDP_REDIRECT | XDP_PASS}
                  ^ runs before skb allocation; a dropped DDoS packet costs ~nanoseconds
DPDK:     NIC rings <-> userspace poll-mode driver <-> app   (kernel never sees it)
```

> XDP_DROP is why a modern eBPF firewall can absorb tens of millions of attack PPS on commodity hardware: the packet is discarded *before* the kernel pays for an skb. Cloudflare and Facebook (Katran) build their L4 load balancers and DDoS scrubbing on XDP for exactly this reason.

---

## 9. Measuring — iperf3, netperf, ss, tcpdump

### 9.1 Throughput: iperf3

```bash
# server
iperf3 -s
# client: 30 s TCP test, report retransmits & cwnd
iperf3 -c SERVER -t 30 -i 1
# 8 parallel streams (works around single-flow window limits to find LINK ceiling)
iperf3 -c SERVER -P 8
# UDP at a target rate to measure loss & jitter (latency-sensitive workloads)
iperf3 -c SERVER -u -b 900M
# reverse (server -> client), useful behind asymmetric NAT/firewalls
iperf3 -c SERVER -R
```

Read the `Retr` (retransmits) and `Cwnd` columns. If a **single** stream is slow but `-P 8` saturates the link, you have a **per-flow window** problem (BDP/buffers, §4/§6), not a link problem.

### 9.2 Latency & request-rate: netperf

```bash
netperf -H SERVER -t TCP_RR -- -O min_latency,mean_latency,p99_latency,transaction_rate
# TCP_RR = request/response: measures round-trip latency, the RPC regime metric
# TCP_STREAM = bulk throughput (iperf3-like)
```

### 9.3 Per-connection state: ss (the workhorse)

```bash
# socket summary by state — spot TIME_WAIT/CLOSE_WAIT buildup at a glance
ss -s

# every TCP socket with detailed internal info: rtt, cwnd, retrans, pacing, delivery rate
ss -tinp

# listening sockets with their accept queue depth (Recv-Q = pending, Send-Q = backlog max)
ss -ltn

# filter: established conns to port 443
ss -tn state established '( dport = :443 )'
```

`ss -ti` is the closest thing to an X-ray of a live connection. Key fields:

- `rtt:23.4/4.1` — smoothed RTT / RTT variance (ms). High variance = jitter.
- `cwnd:10` — congestion window in MSS units. Stuck at the initial ~10 → can't ramp (loss? slow start reset?).
- `retrans:0/3` — current/total retransmits. Nonzero total under load → loss-driven throughput loss.
- `rcv_space` / `delivery_rate` — autotuning state and measured goodput.

### 9.4 Packet-level truth: tcpdump

```bash
# capture handshake + retransmits to a file for Wireshark
sudo tcpdump -i eth0 -w /tmp/trace.pcap 'tcp port 443 and host 10.0.0.5'
# live: show SYN/SYN-ACK timing and flags
sudo tcpdump -i eth0 -nn 'tcp[tcpflags] & (tcp-syn|tcp-fin|tcp-rst) != 0'
# relative seq numbers + timestamps to eyeball RTT and retransmits
sudo tcpdump -i eth0 -nn -ttt 'host 10.0.0.5'
```

In Wireshark, *Statistics → TCP Stream Graphs → Round Trip Time* and the `tcp.analysis.retransmission` filter localize the problem fast. Remember to disable GRO (§7) for accurate per-packet timing.

### 9.5 A latency-vs-throughput measurement tool — runnable

```python
"""netprobe.py — distinguish a LATENCY problem from a THROUGHPUT problem
against any TCP service, using only the stdlib. It measures:
  (1) connect + round-trip latency distribution (the RPC regime)
  (2) bulk throughput by streaming N bytes and timing it (the bulk regime)
Run a local echo-ish target first, e.g.:  python -m http.server 8000
Then:  python netprobe.py 127.0.0.1 8000
"""
import socket, statistics, sys, time

def percentile(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)

def measure_latency(host, port, n=50, timeout=2.0):
    """Connect, send a tiny request, time first-byte response. Reports RTT-ish."""
    samples = []
    req = b"GET / HTTP/1.0\r\n\r\n"
    for _ in range(n):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # no Nagle delay
        t0 = time.perf_counter()
        try:
            s.connect((host, port))
            s.sendall(req)
            s.recv(1)                      # wait for first response byte
            samples.append((time.perf_counter() - t0) * 1000.0)  # ms
        except OSError:
            pass
        finally:
            s.close()
    return samples

def measure_throughput(host, port, target_bytes=8 << 20, timeout=10.0):
    """Drain bytes from the server and time it -> goodput in Mbit/s."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    s.sendall(b"GET / HTTP/1.0\r\n\r\n")
    got, t0 = 0, time.perf_counter()
    while got < target_bytes:
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        got += len(chunk)
    dt = time.perf_counter() - t0
    s.close()
    return (got * 8.0 / dt / 1e6) if dt > 0 else float("nan"), got

def diagnose(lat_p50, lat_p99, mbps):
    jitter = lat_p99 - lat_p50
    print("\n--- diagnosis ---")
    if lat_p50 > 50:
        print(f"LATENCY-bound: p50={lat_p50:.1f} ms is high. "
              "Check propagation (geo/placement), # of round trips, Nagle/delayed-ack.")
    if jitter > lat_p50:
        print(f"JITTER: p99-p50={jitter:.1f} ms >> p50. "
              "Check queuing/buffer-bloat (AQM), CPU scheduling, GC pauses.")
    if mbps < 100:
        print(f"THROUGHPUT-bound: {mbps:.0f} Mbit/s. "
              "Check BDP vs window (tcp_rmem/wmem), retransmits (ss -ti), congestion control.")
    if lat_p50 <= 50 and jitter <= lat_p50 and mbps >= 100:
        print("Healthy on both axes for this target.")

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
    lat = measure_latency(host, port)
    if lat:
        p50, p99 = percentile(lat, 50), percentile(lat, 99)
        print(f"latency  n={len(lat)}  p50={p50:.2f} ms  "
              f"p99={p99:.2f} ms  jitter(p99-p50)={p99-p50:.2f} ms")
    else:
        p50 = p99 = float("nan")
        print("latency: no successful samples (is the target up?)")
    try:
        mbps, got = measure_throughput(host, port)
        print(f"throughput  {got/1e6:.2f} MB streamed  goodput={mbps:.1f} Mbit/s")
    except OSError as e:
        mbps = 0.0
        print(f"throughput: failed ({e})")
    # the percentile helper is the load-bearing logic; assert it here.
    assert abs(percentile([0, 10], 50) - 5.0) < 1e-9
    assert percentile([1, 2, 3, 4], 100) == 4
    if lat:
        diagnose(p50, p99, mbps)
```

---

## 10. Diagnosing & fixing — the runbook

### 10.1 "The network is slow" triage runbook

```text
STEP 0  Define the symptom. Latency (waiting) or throughput (slow bulk)? Which path? Reproduce.

STEP 1  Is it even the network?
        - Compare client->server RTT (ping / ss rtt) to the app's reported latency.
          If app latency >> RTT, the time is in the APP/server, not the network.

STEP 2  Latency regime?  (small messages, high p50, high jitter)
        - ping / mtr SERVER          -> per-hop RTT & loss; find the bad hop
        - ss -ti dport = :PORT       -> rtt, rttvar (jitter), retrans
        - Check Nagle + delayed-ACK interaction (40 ms stalls!) -> TCP_NODELAY
        - High p50, low jitter, far geo -> PROPAGATION. Fix with CDN/edge/region (§10.3).
        - High jitter -> queuing/buffer-bloat -> tc qdisc (fq_codel), or CPU/sched/GC.

STEP 3  Throughput regime?  (bulk transfer below link rate)
        - iperf3 -c SERVER          (single flow)
        - iperf3 -c SERVER -P 8     (parallel)
            single slow + parallel fast  -> per-flow WINDOW. Compute BDP (§4);
                                            raise tcp_rmem/wmem max; check window scaling.
            both slow                    -> LINK or shared bottleneck; check the path,
                                            shaper (tc), NIC errors (ethtool -S), CC.
        - ss -ti during transfer: retrans climbing? -> loss-driven; try BBR, check the path.
        - cwnd stuck small after idle? -> tcp_slow_start_after_idle=0.

STEP 4  Host saturation?
        - mpstat -P ALL 1  / top    -> a single CPU pinned at 100% in %soft (softirq)
                                       = single RX queue. Enable RSS/RPS/RFS (§7.1).
        - ethtool -S eth0 | grep -iE 'drop|err|discard|no_buf' -> NIC-level loss.
        - sar -n DEV 1 / ip -s link -> rx/tx drops, errors, overruns.

STEP 5  Packet truth, if still unexplained:
        - tcpdump -w trace.pcap ... ; open in Wireshark.
          Look for: retransmissions, dup-ACKs, zero-window (rcv buffer full = SLOW CONSUMER),
          large RTT between SYN and SYN-ACK (path/handshake), resets.
```

### 10.2 ss / tcpdump worked triage

```bash
# 1) Quick state census — is something piling up?
ss -s
#   millions of TIME_WAIT  -> connection churn -> POOL connections / keepalive (§10.4)
#   many CLOSE_WAIT        -> app not close()-ing sockets (app bug, fd leak)

# 2) Zero-window = the RECEIVER is the bottleneck (slow consumer), not the network
sudo tcpdump -i eth0 -nn 'tcp[tcpflags] & tcp-ack != 0 and tcp[14:2] = 0'  # win=0 advertisements

# 3) Live retransmit watch on a hot connection
watch -n1 "ss -ti dport = :443 | grep -E 'retrans|cwnd|rtt'"

# 4) Are drops at the NIC or the qdisc?
ethtool -S eth0 | grep -iE 'drop|discard|overrun|no_buffer'
tc -s qdisc show dev eth0 | grep -iE 'drop|overlimit'
```

### 10.3 CDN & edge caching — beating propagation

You cannot reduce `d_prop` for a fixed pair of endpoints, so you **move the endpoint closer**:

- A **CDN** terminates the user's TCP/TLS connection at a nearby **edge PoP**, slashing handshake and round-trip latency (the user's RTT is to the PoP, not the origin). Cacheable content is served from the edge; dynamic requests ride a *warm, long-lived, BBR-tuned* backbone connection from edge→origin.
- This is the single highest-leverage latency fix for geographically distributed users — it attacks the one term physics won't let you tune, and it eliminates repeated TLS handshakes by terminating close to the user.

### 10.4 Connection pooling & keepalive economics

Every new TCP connection pays a fixed up-front latency tax:

```
TCP handshake:        1 RTT  (SYN, SYN-ACK, ACK)
TLS 1.2 handshake:   +2 RTT  (TLS 1.3: +1 RTT; 0-RTT resumption: +0)
                     ---------
cold HTTPS request:  ~3 RTT before a single byte of your request is sent
```

At 80 ms RTT, that is **~240 ms of pure setup** *per connection*. Plus slow start: a fresh connection's `cwnd` starts at ~10 MSS (~14 KB), so a 1 MB response needs several RTTs to ramp.

> **Pooling / keepalive amortizes the handshake and keeps `cwnd` warm.** Reuse a connection across many requests and the per-request cost drops from ~3 RTT to ~1 RTT (or 0.5 with pipelining/multiplexing). This is *the* reason HTTP/1.1 keep-alive, HTTP/2 multiplexing, and gRPC channel reuse exist. The corollary: pair pooling with `tcp_slow_start_after_idle=0` (§6.5) so an idle-then-busy pooled connection doesn't re-pay slow start.

Sizing a pool: `pool_size ≈ throughput_target × per_request_latency` (Little's Law). Too small → requests queue behind connections; too large → idle connections waste FDs and server memory, and you may exhaust ephemeral ports (§6.3).

---

## 11. Advanced: kernel bypass, packet steering, and NIC-NUMA placement

### The packet-steering stack — RSS / RPS / RFS / XPS / aRFS

Getting packets processed on the *right* core is half of network performance
([08 §8 NAPI](#8-interrupts-napi-and-the-cost-of-copies--context-switches),
[scenarios 01.4](../enterprise_scenarios/01_cpu_memory_incidents.md)):

| Mechanism | What it steers | Where |
|---|---|---|
| **RSS** | inbound packets → multiple NIC RX queues by 5-tuple hash | hardware (NIC) |
| **RPS** | softirq RX processing → CPUs (software RSS) | kernel |
| **RFS** | RX processing → the CPU where the *app* consuming it runs | kernel |
| **aRFS** | RFS accelerated into the NIC (steer the queue itself) | hardware |
| **XPS** | TX → a specific queue per CPU (avoid TX lock contention) | kernel |

The goal: a packet is received, processed, and consumed **on the same core** (warm
cache, no cross-core handoff). RFS/aRFS achieve that for the app; misconfigured
steering is why one core drowns in `%soft` while others idle.

### NIC ↔ NUMA placement

A NIC is attached to one NUMA node ([03 §12](../operating_system/03_memory_management.md)).
If interrupts/queues land on the *far* node, every packet crosses the interconnect —
the same remote-memory tax as CPU scheduling. Pin NIC IRQs and the app threads to the
**NIC-local NUMA node** (`/sys/class/net/<dev>/device/numa_node`); for very high PPS
this placement can matter more than any sysctl.

### Kernel bypass — XDP, AF_XDP, DPDK

At millions of packets/sec the per-packet kernel cost (skb alloc, stack traversal,
copies) dominates. Three escapes, in increasing radicalism:

- **XDP** — run eBPF **in the driver before skb allocation**: drop/redirect/rewrite at
  line rate. Powers DDoS scrubbing ([09 §5](09_network_security.md)) and Katran/Cilium
  LBs ([07 §advanced](07_load_balancing_proxies.md)) — and keeps the rest of the stack.
- **AF_XDP** — deliver raw frames to a userspace app via a shared ring, **zero-copy**,
  bypassing the stack but staying in Linux.
- **DPDK** — take the NIC away from the kernel entirely; a userspace **poll-mode
  driver** busy-polls the device on dedicated cores (no interrupts, no stack). Maximum
  throughput, but you re-implement the networking you need and burn whole cores
  (the busy-poll trade, [06 §advanced](../operating_system/06_io_models_async.md)).

> Rule of thumb: tune sysctls/steering first; reach for XDP for drop/redirect at scale;
> reach for DPDK/AF_XDP only when you genuinely need every last packet-per-second and
> can afford dedicated cores and the engineering.

---

## 12. Trade-offs summary

- **Latency vs throughput are orthogonal.** Diagnose the regime first; their fixes don't transfer.
- **BDP is the master number for throughput.** `throughput ≤ window / RTT`. Buffers must exceed BDP; window scaling must be on.
- **Bigger buffers are not better** — they cause buffer bloat. Use AQM (FQ-CoDel) to keep queues short; pair `fq` with BBR.
- **Offloads (TSO/GSO/GRO/checksum) trade per-packet CPU for batching** — but GRO/LRO break per-packet measurement; disable when capturing.
- **Interrupt coalescing and NAPI are the latency-vs-throughput knob in hardware** — more batching = more throughput, more latency.
- **Copies and context switches dominate at high PPS;** `sendfile`/`io_uring` reduce them, XDP/DPDK eliminate the kernel from the hot path.
- **Beat propagation with placement (CDN/edge), not tuning;** beat handshake cost with pooling/keepalive.

## 13. Key Takeaways

1. Speak precisely: **goodput < throughput < bandwidth**, and **latency, jitter, loss, PPS** are independent axes. Quote goodput; it's what users get.
2. Decompose latency into **propagation + serialization + queuing + processing**. Propagation is physics (fix with placement); queuing is buffer bloat (fix with AQM).
3. **BDP = bandwidth × RTT** is the number to compute before touching any buffer. A small window throttles a fat pipe to a trickle; window scaling + buffers ≥ BDP unlock it.
4. The kernel's **per-packet cost** (interrupts, copies, context switches) — not the wire — bounds high-PPS workloads. **NAPI, RSS/RPS/RFS, and ultimately XDP/DPDK** address it.
5. **Tune to your regime:** congestion control (BBR vs CUBIC), qdisc (fq vs fq_codel), coalescing, and `slow_start_after_idle` all trade latency against throughput — pick deliberately.
6. **Measure, don't guess:** `iperf3 -P` localizes window vs link; `ss -ti` X-rays a live connection (rtt, cwnd, retrans); `tcpdump`/Wireshark gives packet truth (zero-window = slow consumer, retransmits = loss).
7. The two highest-leverage architectural fixes are **CDN/edge** (kills propagation latency) and **connection pooling/keepalive** (kills repeated handshakes and slow-start).

> Read next: [09 — Network Security](09_network_security.md) for the threat model and defenses that live on the same data path, and [10 — Cloud Networking, SDN & Overlays](10_cloud_sdn_overlays.md) for how all of this changes when the network is software.
