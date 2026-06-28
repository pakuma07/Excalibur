# 04 — Transport Layer: TCP & UDP

> **Audience:** staff/principal. You've opened a socket and seen `TIME_WAIT` in `ss`. This doc is about how the transport layer *actually* turns a best-effort packet stream into either a raw datagram service (UDP) or a reliable, ordered, flow- and congestion-controlled byte stream (TCP) — down to the header bits, the state machine, the retransmission timers, and the congestion-control feedback loops that decide whether your fleet saturates a 100 Gbit link or collapses under bufferbloat.
>
> **Primary sources:** Stevens, *TCP/IP Illustrated Vol. 1* ch. 17–24; Kurose & Ross ch. 3; RFC 9293 (TCP, obsoletes 793); RFC 768 (UDP); RFC 1122 (host requirements); RFC 6298 (RTO computation, Karn/Jacobson); RFC 5681 (TCP congestion control); RFC 2018 (SACK); RFC 6582 (NewReno); RFC 8312 (CUBIC); the BBR paper (Cardwell et al., ACM Queue 2016); RFC 896/1122 (Nagle); RFC 9000 (QUIC); Grigorik, *High Performance Browser Networking*; Cloudflare engineering blog (bufferbloat, BBR, TIME_WAIT).

---

## 1. What the transport layer is for

IP (see [03](03_network_layer_routing.md)) delivers packets **host-to-host**, best-effort: no ordering, no reliability, no flow control. That is not enough to run two *programs* talking to each other. The transport layer adds the missing pieces and, crucially, **multiplexes** the single host-to-host IP pipe among many concurrent conversations.

Two jobs define it:

1. **Multiplexing / demultiplexing by port.** A host runs hundreds of flows at once (browser tabs, SSH, a database pool). The kernel must hand each arriving segment to the right socket. The key is the **4-tuple** `(src IP, src port, dst IP, dst port)` plus protocol — that tuple *is* the connection identity.
2. **A delivery contract on top of best-effort IP.** Here the two protocols diverge sharply:
   - **UDP** adds essentially *nothing* but ports and an optional checksum — a thin demux layer over IP.
   - **TCP** adds reliability, ordering, de-duplication, flow control, and congestion control — a full reliable byte-stream abstraction.

```
        Application (HTTP, DNS, gRPC, ...)
   ┌───────────────────┴───────────────────┐
   │            Transport layer             │
   │   TCP  (reliable, ordered, stream)     │   <- ports + delivery contract
   │   UDP  (datagram, best-effort)         │
   └───────────────────┬───────────────────┘
                       IP   (host-to-host, best-effort, unordered)
                    Link / physical
```

### 1.1 Ports & the demux key

A **port** is a 16-bit number (0–65535). Well-known ports (0–1023) are privileged (HTTP 80, HTTPS 443, DNS 53, SSH 22). Ephemeral ports (typically 32768–60999 on Linux, see `/proc/sys/net/ipv4/ip_local_port_range`) are assigned to client sockets.

The demux key differs by protocol:

| Protocol | Demux on | Consequence |
|---|---|---|
| **UDP** | `(dst IP, dst port)` (2-tuple) | one socket receives from many peers; you read the sender per-datagram |
| **TCP** | `(src IP, src port, dst IP, dst port)` (4-tuple) | a listening socket spawns one connected socket *per peer* |

This 4-tuple distinction is why a server on `:443` can hold a million simultaneous TCP connections: each is a distinct 4-tuple, even though they all share the server's `(IP, 443)`. It also bounds client-side concurrency to one peer: a single client IP can open at most ~28k connections to one `(server IP, port)` before it exhausts ephemeral ports (§13.4).

---

## 2. UDP — the thin layer (RFC 768)

UDP is almost the null transport: it exposes IP's best-effort datagram service to applications, adding only ports and an integrity check.

### 2.1 The header — 8 bytes, that's all

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          Source Port          |       Destination Port        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|            Length             |           Checksum            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                          data ...                             |
```

- **Source / Destination Port** — 16 bits each. Source port is optional (may be 0 if no reply expected).
- **Length** — total datagram length (header + data), min 8.
- **Checksum** — covers header, data, and a *pseudo-header* (src/dst IP, protocol, length) borrowed from IP. Optional on IPv4 (0 = disabled), **mandatory on IPv6**.

### 2.2 What UDP does *not* give you

No connection, no handshake, no acknowledgment, no retransmission, no ordering, no de-duplication, no flow control, no congestion control. A `sendto()` either fits in one datagram and goes out, or it doesn't. If it's lost, reordered, or duplicated, that's the application's problem.

### 2.3 When to reach for UDP

- **Low-latency, loss-tolerant** traffic where a retransmit would arrive too late to be useful: real-time voice/video (RTP), games. You'd rather drop a frame than stall.
- **Request/response that fits in one datagram** and has its own retry: **DNS** (see [05](05_dns.md)), NTP, DHCP, SNMP.
- **You want to build your own transport.** QUIC (§14) runs over UDP precisely to escape TCP's kernel ossification and head-of-line blocking. QUIC re-implements reliability, ordering, and congestion control in user space.
- **Multicast / broadcast** — TCP is strictly point-to-point; UDP can do one-to-many.

> Staff rule: choose UDP when *the application's notion of "in time" is tighter than a retransmit RTT*, or when you genuinely need to own the congestion/reliability logic. Otherwise TCP's decades of tuning win.

---

## 3. The TCP header (RFC 9293)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          Source Port          |       Destination Port        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Sequence Number                        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Acknowledgment Number                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Data  |Rsvd |C|E|U|A|P|R|S|F|                                 |
| Offset|     |W|C|R|C|S|S|Y|I|            Window Size           |
|       |     |R|E|G|K|H|T|N|N|                                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|           Checksum            |         Urgent Pointer        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Options (0–40 bytes, padded)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                          data ...                             |
```

| Field | Bits | Meaning |
|---|---|---|
| **Source / Dest Port** | 16 + 16 | With the IP addresses, forms the connection 4-tuple. |
| **Sequence Number** | 32 | Byte offset of the *first* data byte in this segment within the stream. On SYN, it's the **ISN** (initial sequence number) and the SYN consumes one sequence number. |
| **Acknowledgment Number** | 32 | Next sequence number the receiver *expects* (i.e., everything below it is received). Cumulative. Valid only when ACK flag set. |
| **Data Offset** | 4 | Header length in 32-bit words (5–15 → 20–60 bytes). Tells where data begins after options. |
| **Flags** | 8 | `CWR`, `ECE` (ECN), `URG`, `ACK`, `PSH` (push to app now), `RST` (abort), `SYN` (open), `FIN` (close). |
| **Window Size** | 16 | Receiver's free buffer (flow control). Scaled by the **window scale** option (§7). |
| **Checksum** | 16 | Over header + data + IP pseudo-header. Mandatory. |
| **Urgent Pointer** | 16 | Offset of urgent data (when URG set). Almost never used. |
| **Options** | 0–40 B | MSS, window scale, SACK-permitted, SACK blocks, timestamps (§ below). |

### 3.1 The options that matter

- **MSS (Maximum Segment Size)** — exchanged in SYNs; largest payload a peer will accept (typically 1460 = 1500 MTU − 20 IP − 20 TCP). Avoids IP fragmentation.
- **Window Scale (WSopt)** — left-shift the 16-bit window by up to 14, raising the max window from 64 KiB to ~1 GiB. *Required* for high bandwidth-delay-product links (§11).
- **SACK-Permitted / SACK** — selective acknowledgment (§10).
- **Timestamps (TSopt)** — round-trip-time measurement on every segment + PAWS (protection against wrapped sequence numbers).

> Sequence/ack numbers count **bytes, not segments**. This is the whole basis of TCP's cumulative-ack reliability and its byte-granular sliding window.

---

## 4. Connection setup — the 3-way handshake

TCP is connection-oriented: both ends must agree on starting sequence numbers and options before data flows. This costs **one full RTT** before the first byte of application data.

```
 Client                                              Server
   │                                                   │
   │  SYN  seq=x, MSS, WScale, SACK-perm                │   CLOSED→LISTEN
   │ ─────────────────────────────────────────────────►│
   │                                          (SYN-RCVD)│
   │  SYN+ACK  seq=y, ack=x+1, MSS, WScale, SACK-perm   │
   │ ◄─────────────────────────────────────────────────│
   │ (ESTABLISHED)                                      │
   │  ACK  seq=x+1, ack=y+1                              │
   │ ─────────────────────────────────────────────────►│
   │                                       (ESTABLISHED)│
   │  ── application data flows both ways ──            │
```

1. **SYN**: client picks a random ISN `x`, sends `SYN seq=x`. (Random ISN — RFC 6528 — defends against blind injection.)
2. **SYN+ACK**: server picks ISN `y`, acks the client's SYN (`ack=x+1`), sends its own `SYN seq=y`.
3. **ACK**: client acks the server's SYN (`ack=y+1`). Connection is now `ESTABLISHED` on both ends.

**Why three, not two?** Each side must (a) announce its ISN and (b) confirm it saw the peer's ISN. That's four logical messages, but the server piggybacks its SYN onto the ACK, collapsing to three. The exchange also negotiates MSS, window scale, and SACK.

**SYN backlog & SYN floods.** A half-open connection (SYN received, ACK not yet) sits in the **SYN queue**. An attacker spraying SYNs with spoofed sources fills it → legitimate clients can't connect. Defense: **SYN cookies** — encode the connection state into the ISN so no server-side state is held until the final ACK returns.

### 4.1 TCP Fast Open (TFO)

RFC 7413 lets a client send data *in* the SYN on a repeat connection (using a server-issued cookie), saving the 1-RTT setup cost. Adoption is patchy due to middlebox interference — a recurring theme that motivated QUIC.

---

## 5. Connection teardown — 4-way & TIME_WAIT

TCP connections are **full-duplex**; each direction is closed independently with its own FIN. Hence *four* segments (vs three for setup), though they can overlap.

```
 Client (active close)                               Server
   │  FIN  seq=u                                        │   ESTABLISHED
   │ ─────────────────────────────────────────────────►│
   │ (FIN_WAIT_1)                              (CLOSE_WAIT)
   │  ACK  ack=u+1                                       │
   │ ◄─────────────────────────────────────────────────│
   │ (FIN_WAIT_2)                                       │
   │            ... server finishes sending ...         │
   │  FIN  seq=v                                         │
   │ ◄─────────────────────────────────────────────────│   (LAST_ACK)
   │ (TIME_WAIT)                                        │
   │  ACK  ack=v+1                                       │
   │ ─────────────────────────────────────────────────►│
   │                                            (CLOSED)│
   │  ... wait 2*MSL, then ...                          │
   │ (CLOSED)                                           │
```

The side that calls `close()` first does the **active close** and ends up in `TIME_WAIT`.

### 5.1 Why TIME_WAIT and why 2·MSL

After sending the final ACK, the active closer waits **2·MSL** (Maximum Segment Lifetime; MSL is 2 minutes in the RFC, so the wait is conventionally 60 s on Linux via `TCP_TIMEWAIT_LEN`). Two reasons:

1. **The final ACK might be lost.** If it is, the peer retransmits its FIN; the closer must still be around to re-ACK it. Disappearing immediately would leave the peer stuck in `LAST_ACK`.
2. **Flush stale duplicates.** Old segments from this 4-tuple must die out before the tuple can be reused, or a delayed packet from the *previous* incarnation could be mistaken for data in a new one. 2·MSL = one MSL for the last ACK to arrive + one MSL for the peer's possible retransmission to expire.

> TIME_WAIT is *correct and necessary*, not a bug. But on a busy client/proxy that does the active close on millions of short connections, accumulated TIME_WAIT sockets can exhaust ephemeral ports — see §13.4 for the real diagnosis and the right (and wrong) fixes.

---

## 6. The TCP state machine (RFC 9293)

```
                              ┌──────────┐
                  ┌──────────►│  CLOSED  │◄───────────┐
                  │           └────┬─────┘            │
          passive │ open      active│ open (SYN sent) │
                  │                 │                 │
              ┌───▼────┐      ┌─────▼─────┐           │
              │ LISTEN │      │  SYN_SENT │           │
              └───┬────┘      └─────┬─────┘           │
        recv SYN  │ send       recv │ SYN+ACK         │
        SYN+ACK   │            send │ ACK             │
              ┌───▼─────┐           │                 │
              │SYN_RCVD │           │                 │
              └───┬─────┘           │                 │
        recv ACK  │      ┌──────────▼─────┐           │
                  └─────►│  ESTABLISHED   │           │
                         └──┬──────────┬──┘           │
        ── active close ────┘          └──── passive close ──┐
        (we send FIN)                  (peer sent FIN)       │
              ┌──────────┐                  ┌───────────┐    │
              │FIN_WAIT_1│                  │ CLOSE_WAIT│    │
              └────┬─────┘                  └─────┬─────┘    │
        recv ACK   │  recv FIN                    │ send FIN │
              ┌────▼─────┐ ┌─────────┐      ┌─────▼─────┐    │
              │FIN_WAIT_2│ │ CLOSING │      │ LAST_ACK  │    │
              └────┬─────┘ └────┬────┘      └─────┬─────┘    │
        recv FIN   │            │ recv ACK        │ recv ACK │
        send ACK   │            │                 │          │
              ┌────▼─────┐      │                 │          │
              │TIME_WAIT │◄─────┘                 └──────────┘
              └────┬─────┘   wait 2*MSL
                   └──────────────────────────────────────►(CLOSED)
```

The states you actually grep for in `ss -tan`:

| State | Meaning | If you see a pile of them… |
|---|---|---|
| `LISTEN` | server socket waiting | normal |
| `SYN-SENT` / `SYN-RECV` | mid-handshake | SYN-RECV pile → SYN flood or backlog overflow |
| `ESTABLISHED` | data flowing | normal; count = live connections |
| `CLOSE-WAIT` | **peer** closed, *your app hasn't called `close()`* | **app bug** — you're leaking sockets |
| `FIN-WAIT-2` | you closed, waiting for peer's FIN | peer not closing; check the other end |
| `TIME-WAIT` | active closer cooling down 2·MSL | normal on the active-close side; pile → §13.4 |

> `CLOSE_WAIT` accumulation is the single most common "we ran out of file descriptors" production incident: the kernel did its job, but your code never called `close()` on a peer-closed socket.

---

## 7. Reliable delivery: sequence, ACK, retransmission

TCP turns lossy IP into a reliable byte stream with **cumulative acknowledgments** and **retransmission**.

- Every byte has a sequence number. The receiver ACKs the **next byte it expects** (`ack = highest contiguous byte + 1`). One ACK can cover many segments (cumulative).
- The sender keeps unacked data in a **retransmission buffer**. If an ACK doesn't arrive before the **RTO** (retransmission timeout) fires, it resends.

### 7.1 RTO estimation & Karn's algorithm (RFC 6298)

The RTO must adapt to the path's RTT. Jacobson/Karels smoothing:

```text
SRTT      = smoothed RTT          (exponentially weighted moving avg)
RTTVAR    = RTT variation
on each RTT sample R:
    RTTVAR = (1 - 1/4) * RTTVAR + (1/4) * |SRTT - R|
    SRTT   = (1 - 1/8) * SRTT    + (1/8) * R
    RTO    = SRTT + max(G, 4 * RTTVAR)      # G = clock granularity
clamp RTO to [1s (RFC), 60s]
```

The variance term is critical: a path with jittery RTT needs a *looser* RTO or it will retransmit prematurely. RTO also **backs off exponentially** (doubles) on repeated timeouts.

**Karn's algorithm** solves the *retransmission ambiguity*: when a segment is retransmitted and an ACK arrives, you can't tell if it acks the original or the retransmit — so you **don't sample RTT from retransmitted segments at all**, and you keep the backed-off RTO until a *clean* (non-retransmitted) ACK gives a fresh sample. Timestamps (TSopt) sidestep this by letting you measure RTT unambiguously on every segment.

### 7.2 Fast retransmit & fast recovery (RFC 5681)

Waiting a full RTO to detect loss is slow. **Fast retransmit:** when the sender sees **3 duplicate ACKs** (the receiver keeps re-acking the same expected byte because it's getting out-of-order segments past a hole), it retransmits the missing segment *immediately* — without waiting for RTO. **Fast recovery** then avoids dropping all the way back to slow start (§9.4).

```
seq sent:   1000  1500  2000  2500  3000     (1500 is LOST)
acks back:  1500  1500  1500  1500           <- dup ACK for 1500
                   dup1  dup2  dup3
            └─ 3 dup ACKs → retransmit 1500 now (don't wait for RTO)
```

---

## 8. Flow control: the sliding window

Flow control stops a fast sender from overrunning a slow *receiver's* buffer. (Distinct from congestion control, which protects the *network*.)

The receiver advertises a **Window Size** in every segment: how many more bytes it can buffer beyond the cumulative ACK. The sender may have at most `min(rwnd, cwnd)` bytes in flight.

```
Receiver's stream buffer (advertised window = rwnd):

  ...acked...│  in flight (sent, not acked)  │  usable  │  closed
  ───────────┼───────────────────────────────┼──────────┼──────────
             ▲                                ▲          ▲
            ACK                            sent edge   ACK + rwnd
                                                       (right edge)
```

As the app `read()`s data, the buffer drains and the window reopens; the receiver sends a **window update**.

### 8.1 Zero-window & the persist timer

If the receiver's buffer fills, it advertises **window = 0**, halting the sender. When space frees up it sends a window update — but *that update could be lost*, deadlocking both ends. TCP defends with the **persist timer**: the sender periodically pokes a 1-byte **zero-window probe** to force the receiver to re-advertise its window.

### 8.2 Silly Window Syndrome (SWS)

If the receiver opens the window in tiny increments (a few bytes as the app reads slowly) and the sender ships tiny segments to match, you waste 40 bytes of header per byte of data — **silly window syndrome**. Two fixes (RFC 1122):

- **Receiver side (Clark's solution):** don't advertise a window increase until it's worth it (≥ one MSS or half the buffer).
- **Sender side:** **Nagle's algorithm** (§12) — don't send a small segment while unacked small data is outstanding.

---

## 9. Congestion control — the deep dive

Flow control protects the receiver; **congestion control** protects the *network* (the shared routers and links between you). TCP has no explicit signal from the network (mostly), so it *infers* congestion from **loss** (or, for BBR, from RTT and delivery rate) and probes for available bandwidth. Jacobson's 1988 congestion-collapse fix is the foundation; RFC 5681 is the modern spec.

The sender maintains a **congestion window** `cwnd` (in addition to the receiver's `rwnd`). **In-flight bytes ≤ min(cwnd, rwnd).** Congestion control is entirely about how `cwnd` evolves.

### 9.1 Slow start

Start small (`cwnd = ~10 MSS`, IW10, RFC 6928) and **double `cwnd` every RTT** (increase by 1 MSS per ACK) — exponential growth — until you hit `ssthresh` or detect loss.

```
cwnd (MSS)
  │                              ╱ congestion avoidance (linear, +1/RTT)
40│                          ╱
  │                      ╱
  │ ssthresh ─ ─ ─ ─ ╱─────────────────
  │              ╱  (switch to linear here)
  │          ╱
  │      ╱   slow start (exponential, x2/RTT)
 1│   ╱
  └────────────────────────────────────► RTT rounds
```

### 9.2 Congestion avoidance & AIMD

Above `ssthresh`, switch to **linear** growth: `cwnd += 1 MSS per RTT` (additive increase). On loss, **halve** `cwnd` (multiplicative decrease). This is **AIMD** (Additive Increase, Multiplicative Decrease) — the property that makes TCP flows converge to a fair share of a bottleneck.

```text
on ACK (cong. avoidance):  cwnd += MSS * MSS / cwnd     # ~+1 MSS per RTT
on triple-dup-ACK (loss):  ssthresh = cwnd/2; cwnd = ssthresh   # fast recovery
on RTO timeout (severe):   ssthresh = cwnd/2; cwnd = 1 MSS      # back to slow start
```

The AIMD sawtooth is the iconic TCP throughput shape: climb linearly, halve on loss, repeat.

### 9.3 Why AIMD converges to fairness

Two flows sharing a link: additive increase moves them *equally* (parallel to the 45° fairness line); multiplicative decrease moves them *proportionally* (toward the origin along a ray). The net effect of repeated AIMD cycles drives both flows toward equal share. Additive-increase/additive-decrease or multiplicative/multiplicative would *not* converge — this is the deep reason AIMD specifically is used.

### 9.4 Fast recovery (NewReno, RFC 6582)

After a fast retransmit, instead of collapsing `cwnd` to 1, set `cwnd = ssthresh = cwnd/2` and *inflate* it per duplicate ACK to keep data flowing, then deflate when the missing segment is acked. This is why a *single* loss costs you ~half your window, but an RTO (which implies the pipe truly drained) costs you everything.

### 9.5 Reno vs CUBIC vs BBR — and why BBR matters

| | **Reno / NewReno** | **CUBIC** (Linux default since 2.6.19) | **BBR** (Google, 2016) |
|---|---|---|---|
| Congestion signal | packet loss | packet loss | **RTT + delivery rate** (model-based) |
| `cwnd` growth | linear (AIMD) | **cubic function** of time since last loss | paces to estimated `BtlBw × RTprop` |
| Behavior on loss | halve | reduce by ~0.3, cubic re-growth | largely ignores loss (loss ≠ congestion) |
| Strength | simple, well-understood | fast recovery on high-BDP links; scales to fat pipes | high throughput on lossy/long links; **avoids bufferbloat** |
| Weakness | underutilizes high-BDP links | still loss-based → fills buffers (bufferbloat) | can be unfair to loss-based flows; harder to reason about |

**Why loss-based control is increasingly wrong.** Reno and CUBIC treat *loss* as the congestion signal. But on modern networks, routers have huge buffers; by the time a packet is dropped, the buffer is already full of *your* packets adding latency (**bufferbloat**, §11). Loss-based control therefore *deliberately fills buffers* until they overflow — great for throughput, terrible for latency.

**BBR's insight (Cardwell et al.):** the optimal operating point is at the **BDP** — exactly enough in flight to keep the bottleneck link busy with an *empty* queue. BBR continuously estimates two quantities — **BtlBw** (bottleneck bandwidth, the max delivery rate seen) and **RTprop** (round-trip propagation, the min RTT seen) — and paces the send rate to `BtlBw`, keeping inflight near `BtlBw × RTprop`. It detects congestion from *RTT inflation*, not loss. Result: near-line-rate throughput with low queueing delay, robust to non-congestive loss (Wi-Fi, cellular). YouTube saw large median-latency and throughput improvements deploying BBR. The trade-off: fairness with CUBIC flows sharing a buffer is imperfect, and BBRv1 could be aggressive — hence BBRv2/v3 refinements.

### 9.6 ECN — getting the signal without loss

**Explicit Congestion Notification** (the `ECE`/`CWR` flags + IP ECN bits) lets a congested router *mark* a packet instead of dropping it; the receiver echoes the mark and the sender backs off — congestion control without packet loss. Underused historically due to middlebox mangling, but central to modern datacenter TCP (DCTCP).

---

## 10. SACK — selective acknowledgment (RFC 2018)

Cumulative ACKs have a flaw: if segments 1, 3, 4, 5 arrive but 2 is lost, the receiver can only keep acking "I want 2" — the sender learns nothing about 3–5 and may needlessly resend them (Reno's go-back-N-ish behavior). **SACK** lets the receiver report the *non-contiguous* ranges it actually has:

```
received: [1] [_] [3][4][5]      (2 is missing)
ACK ack=2  +  SACK blocks: {3-5}
   → sender retransmits ONLY 2, knows 3-5 are safe
```

SACK is negotiated in the SYN (`SACK-Permitted`) and carried in option blocks. It dramatically improves recovery when *multiple* segments are lost in a window — essential on lossy or high-BDP paths. **D-SACK** extends it to report *duplicate* segments received, helping the sender distinguish loss from reordering.

---

## 11. Bandwidth-delay product & bufferbloat

### 11.1 BDP — sizing the window

The **bandwidth-delay product** is how many bytes can be "in flight" to keep a pipe full:

```text
BDP (bytes) = bandwidth (bytes/sec) * RTT (sec)
```

To saturate a link you need `cwnd ≥ BDP` *and* the receiver window `rwnd ≥ BDP`. Example: a 1 Gbit/s link with 80 ms RTT:

```text
BDP = (1e9 / 8) bytes/s * 0.080 s = 10,000,000 bytes ≈ 10 MB
```

A 10 MB window is *far* beyond the 64 KiB the unscaled 16-bit window field allows — this is exactly why **window scaling** (§3.1) is mandatory on fat, long pipes. Without it you'd cap at `64KiB / 80ms ≈ 6.5 Mbit/s` regardless of the 1 Gbit link.

### 11.2 Bufferbloat

If a bottleneck router has a **huge buffer**, a loss-based sender (CUBIC) keeps growing `cwnd`, filling that buffer with a deep standing queue. Throughput stays high but **every packet now waits behind a full queue** → RTT balloons from 20 ms to seconds. This is **bufferbloat**: oversized, unmanaged buffers turning transient bursts into persistent latency. Fixes: **AQM** (Active Queue Management — CoDel, FQ-CoDel, fair queuing) drops/marks *early* to keep queues short; and **BBR**, which paces to BDP and refuses to fill the buffer in the first place.

---

## 12. Nagle's algorithm & delayed ACK — the bad interaction

### 12.1 Nagle (RFC 896)

Nagle batches small writes to avoid flooding the network with tiny "tinygram" segments (the classic telnet-per-keystroke problem):

```text
if there is unacknowledged data outstanding:
    buffer new small data; send it only when an ACK arrives
    OR when a full MSS has accumulated
else:
    send immediately
```

### 12.2 Delayed ACK (RFC 1122)

The receiver delays sending a pure ACK (up to ~40–200 ms) hoping to (a) piggyback it on return data or (b) ack two segments at once.

### 12.3 The pathological interaction

Put them together on a request/response protocol with a small last segment and you get a **40–200 ms stall**:

```
Sender (Nagle on)            Receiver (delayed ACK)
  send full segment ───────► got it; DELAY the ACK (hoping for more)
  small final segment        ...waiting to piggyback...
  └ Nagle: holds it          ...still waiting...
    (unacked data out!)      ⏱ 40ms delayed-ACK timer fires → ACK
  ◄────────────────────────  ACK arrives
  NOW Nagle releases the small segment
```

Each side is correctly waiting for the other. The result is a periodic ~40 ms latency hiccup that murders the p99 of chatty RPC protocols. **Fix:** set `TCP_NODELAY` (disable Nagle) on latency-sensitive interactive/RPC sockets — see the demo in §13.2. Most RPC frameworks (gRPC) set it by default. Do *not* mix many small `write()`s with `TCP_NODELAY` and no Nagle, or you reintroduce tinygrams — use `writev`/buffering instead.

---

## 13. Working code — enterprise examples

### 13.1 A TCP echo client/server (observe the handshake)

```python
"""
tcp_demo.py — minimal TCP echo server + client.
Run the server:  python tcp_demo.py server
Run the client:  python tcp_demo.py client
While connected, observe state with:  ss -tan | grep 9099
and the handshake with:  sudo tcpdump -ni lo 'tcp port 9099'
"""
import socket, sys, threading, time

HOST, PORT = "127.0.0.1", 9099


def server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR: rebind immediately even if old sockets sit in TIME_WAIT.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT))
    s.listen(128)                       # 128 = accept backlog
    print(f"listening on {HOST}:{PORT}")
    while True:
        conn, peer = s.accept()         # returns a NEW socket (the 4-tuple)
        print("accepted from", peer)
        with conn:
            while True:
                data = conn.recv(4096)
                if not data:            # peer did orderly close (recv returns b'')
                    print("peer closed")
                    break
                conn.sendall(data)      # echo


def client():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))             # <- the 3-way handshake happens here
    print("connected; local 4-tuple end:", s.getsockname())
    for i in range(3):
        msg = f"hello {i}".encode()
        s.sendall(msg)
        echoed = s.recv(4096)
        print("echoed:", echoed.decode())
        time.sleep(0.5)
    s.close()                           # <- active close -> we enter TIME_WAIT


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "server"
    (server if mode == "server" else client)()
```

Observe the handshake while the client runs:

```bash
# Watch the three SYN/SYN-ACK/ACK packets and the FIN/ACK teardown:
sudo tcpdump -ni lo 'tcp port 9099'
# Flags [S] = SYN, [S.] = SYN+ACK, [.] = ACK, [P.] = PSH+ACK (data), [F.] = FIN+ACK

# Watch the connection states (run repeatedly):
ss -tan | grep 9099
# After the client closes you'll see the active closer in TIME-WAIT for ~60s.
```

### 13.2 Demonstrating the TCP_NODELAY effect

```python
"""
nodelay_demo.py — measure the Nagle + delayed-ACK stall, then remove it.
Sends many small request/response round-trips and times them with vs without
TCP_NODELAY. Expect a dramatic per-RTT improvement with NODELAY on.
Run server:  python nodelay_demo.py server
Run client:  python nodelay_demo.py client
"""
import socket, sys, time

HOST, PORT, N = "127.0.0.1", 9100, 200


def server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((HOST, PORT)); s.listen(8)
    conn, _ = s.accept()
    with conn:
        while True:
            data = conn.recv(64)
            if not data:
                break
            conn.sendall(b"ok")          # tiny reply -> triggers delayed ACK


def run_client(nodelay: bool) -> float:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if nodelay:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.connect((HOST, PORT))
    t0 = time.perf_counter()
    for _ in range(N):
        s.sendall(b"x")                  # small request
        s.recv(64)                       # wait for reply (one round-trip)
    elapsed = time.perf_counter() - t0
    s.close()
    return elapsed


def client():
    # Run twice on fresh connections so the server must be restarted between
    # runs in practice; here we just show the call pattern.
    for nd in (False, True):
        print(f"TCP_NODELAY={nd}: {run_client(nd)*1000:.1f} ms for {N} round-trips")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "server"
    (server if mode == "server" else client)()
```

On a chatty request/response pattern over a real network, the `TCP_NODELAY=False` run can be 40 ms × N slower because of the delayed-ACK stall; with `TCP_NODELAY=True` each round-trip is bounded by RTT alone.

### 13.3 Computing the optimal window from BDP

```python
"""
bdp.py — compute the bandwidth-delay product and the required window /
window-scale factor to saturate a link. Pure stdlib; prints a sizing table.
"""
import math


def bdp_bytes(bandwidth_bps: float, rtt_seconds: float) -> float:
    return (bandwidth_bps / 8.0) * rtt_seconds


def required_window_scale(bdp: float) -> int:
    """How many bits of left-shift the 16-bit window field needs.
    Base window max is 65535 bytes; scale shifts left by `shift` bits."""
    if bdp <= 65535:
        return 0
    return min(14, math.ceil(math.log2(bdp / 65535)))


if __name__ == "__main__":
    scenarios = [
        ("LAN 10Gbit, 0.2ms", 10e9, 0.0002),
        ("WAN 1Gbit, 80ms", 1e9, 0.080),
        ("Sat 100Mbit, 600ms", 100e6, 0.600),
    ]
    print(f"{'scenario':24} {'BDP':>14} {'win-scale shift':>16}")
    for name, bw, rtt in scenarios:
        b = bdp_bytes(bw, rtt)
        print(f"{name:24} {b/1e6:>10.2f} MB {required_window_scale(b):>14}")
    # WAN 1Gbit/80ms -> ~10 MB BDP -> needs window scaling (shift ~8);
    # the unscaled 64 KiB window would cap throughput at ~6.5 Mbit/s.
```

### 13.4 Diagnosing TIME_WAIT exhaustion

```bash
# 1. Count sockets by state — a huge TIME-WAIT count on a client/proxy is the symptom:
ss -tan state time-wait | wc -l
ss -tan | awk 'NR>1 {print $1}' | sort | uniq -c | sort -rn

# 2. Check the ephemeral port range and how close you are to exhausting it:
cat /proc/sys/net/ipv4/ip_local_port_range      # e.g. 32768 60999  (~28k ports)

# 3. THE RIGHT FIXES (in order of preference):
#  a) Stop doing the active close on the client. Use HTTP keep-alive / connection
#     pooling so connections are REUSED, not opened-and-closed per request.
#  b) Have the SERVER do the active close where possible (server TIME_WAITs are
#     cheaper — it's not exhausting ephemeral ports against one peer).
#  c) Enable safe reuse of TIME_WAIT sockets for OUTBOUND connections:
sysctl -w net.ipv4.tcp_tw_reuse=1                # SAFE (uses TCP timestamps)

# 4. THE WRONG FIX — do NOT do this; it breaks NAT'd clients and can cause
#    silent data corruption from old-incarnation packets:
#    net.ipv4.tcp_tw_recycle  (removed in Linux 4.12 for exactly this reason)
```

> Diagnosis discipline: TIME_WAIT exhaustion is almost always an **architecture smell** (per-request connections) — the fix is connection pooling/keep-alive, not kernel knob roulette.

---

## 14. Socket options worth knowing

| Option | Level | What it does | When |
|---|---|---|---|
| `SO_REUSEADDR` | `SOL_SOCKET` | Rebind a port still in TIME_WAIT (and on some OSes share via wildcard rules). | Always on servers — survives quick restarts. |
| `SO_REUSEPORT` | `SOL_SOCKET` | Multiple sockets bind the *same* port; kernel load-balances accepts across them. | Multi-process/thread accept scaling (one listener per worker). |
| `TCP_NODELAY` | `IPPROTO_TCP` | Disable Nagle. | Latency-sensitive RPC/interactive (§12). |
| `SO_KEEPALIVE` (+ `TCP_KEEPIDLE/INTVL/CNT`) | `SOL_SOCKET`/`IPPROTO_TCP` | Probe idle connections to detect dead peers / reap zombie connections through NAT. | Long-lived idle connections (DB pools, push). Default idle is *2 hours* — usually too long; tune it. |
| `TCP_USER_TIMEOUT` | `IPPROTO_TCP` | Cap how long unacked data may stay outstanding before the connection fails. | Fail fast on dead paths. |
| `SO_LINGER` | `SOL_SOCKET` | Control close() behavior; linger=0 sends RST (abortive close, skips TIME_WAIT). | Rarely; abortive close loses unsent data. |
| `SO_RCVBUF`/`SO_SNDBUF` | `SOL_SOCKET` | Socket buffer sizes (bound the window). | High-BDP tuning; usually leave to autotuning. |

---

## 15. QUIC — the contrast (cross-link to HTTP)

TCP has a structural problem for multiplexed protocols: **head-of-line (HOL) blocking**. If you carry many independent streams over one TCP connection (HTTP/2 does), a *single* lost packet stalls *all* streams, because TCP delivers bytes strictly in order — the receiver can't hand stream B's data to the app while stream A's earlier byte is missing.

**QUIC** (RFC 9000) fixes this by running over **UDP** and re-implementing transport in user space:

| | TCP (+TLS, +HTTP/2) | QUIC (+HTTP/3) |
|---|---|---|
| Transport | TCP in kernel | UDP + QUIC in user space |
| Streams | one ordered byte stream → HOL blocking across multiplexed streams | **independent streams**; loss on one doesn't block others |
| Handshake | TCP 3-way (1 RTT) **+** TLS (1–2 RTT) | **crypto + transport in 1 RTT**, 0-RTT on resume |
| Connection ID | the 4-tuple (breaks on IP change) | **connection ID** survives IP changes (Wi-Fi↔cellular migration) |
| Evolvability | ossified by middleboxes | encrypted, evolves in user space |

The cost: UDP is sometimes deprioritized/blocked, and user-space CPU overhead is higher than kernel TCP offload. See the HTTP doc for HTTP/2 vs HTTP/3 details.

---

## 16. Advanced: TFO, pacing, BBRv2/v3, MPTCP, and kTLS

### TCP Fast Open (TFO) — data in the SYN

A normal connection wastes one RTT on the handshake before any data
([§4](#4-connection-setup--the-3-way-handshake)). **TFO** (RFC 7413) lets a returning
client send request data **in the SYN** using a cookie from a prior connection — saving
that RTT, which matters for short request/response flows. Adoption is limited by
middleboxes that strip the option and by replay concerns (the SYN data can be
replayed), so it's mostly used in controlled environments; QUIC's 0-RTT
([06 §6](06_http_tls.md)) is the more common modern answer.

### Pacing and `fq` — stop sending in bursts

Classic TCP sends a whole congestion window back-to-back, creating micro-bursts that
overflow shallow switch buffers and cause loss. **Pacing** spreads packets evenly
across the RTT; the **`fq`** (fair queue) qdisc implements it in the kernel and is a
prerequisite for **BBR**. Pacing + `fq_codel`/`fq` is a high-leverage host setting for
both throughput and tail latency
([Net 08 §5](08_network_performance_tuning.md), [scenarios 04.5](../enterprise_scenarios/04_network_incidents.md)).

### BBRv2/v3 — model-based congestion control matured

BBR ([§9](#9-congestion-control--the-deep-dive)) estimates bandwidth and RTT to set
rate, rather than treating loss as the only congestion signal — so it tolerates random
loss far better than CUBIC on lossy/high-BDP paths. **BBRv1 was too aggressive**
(starved CUBIC flows, ignored loss/ECN); **BBRv2/v3** add loss and ECN response for
fairness and are the current generation. Staff takeaway: BBR is excellent for
long-fat/lossy WAN paths but **test fairness** before deploying it alongside CUBIC
flows on a shared link.

### MPTCP — one connection, many paths

**Multipath TCP** (RFC 8684) spreads one logical connection across multiple paths
(Wi-Fi + cellular, or multiple NICs) for resilience and aggregate bandwidth, while
presenting a normal socket to the app. It powers seamless Wi-Fi/cellular handoff on
phones and multi-NIC server resilience — transparent failover without the app
reconnecting.

### kTLS — TLS in the kernel, and why it matters

**Kernel TLS** moves symmetric encryption into the kernel, which unlocks **zero-copy
`sendfile` over TLS** ([06 §2](../operating_system/06_io_models_async.md) zero-copy):
a CDN can serve an encrypted file straight from the page cache to the NIC without
copying it through userspace to encrypt — a major efficiency win for TLS-heavy serving,
and it can offload the crypto to the NIC. It's the answer to "TLS killed my zero-copy."

---

## 17. Trade-offs summary

- **UDP vs TCP** = "I'll handle reliability/timing myself, give me speed" vs "give me a correct byte stream." Choose UDP when a retransmit would arrive too late, or you're building your own transport (QUIC).
- **The 4-tuple is connection identity** — it bounds both server scale (millions of distinct tuples on one port) and client scale (ephemeral-port exhaustion to one peer).
- **TIME_WAIT is correct, not a bug.** Exhaustion means your architecture opens too many short connections — fix with pooling/keep-alive, not `tcp_tw_recycle`.
- **Congestion control is the soul of TCP.** Loss-based (Reno/CUBIC) fills buffers → bufferbloat; **BBR** models BDP and keeps queues empty → the modern default for long/lossy/high-throughput paths.
- **Window scaling is mandatory** on any link where BDP > 64 KiB — i.e., essentially all WAN today.
- **Nagle + delayed ACK** is the classic 40 ms latency trap; `TCP_NODELAY` for interactive/RPC.
- **SACK** turns multi-loss recovery from go-back-N into surgical retransmission — enable it.
- **HOL blocking is TCP's structural ceiling** for multiplexed protocols; QUIC over UDP is the answer (HTTP/3).

## 18. Key Takeaways

1. The transport layer adds **multiplexing (ports)** and a **delivery contract** on top of best-effort IP. UDP adds almost nothing; TCP adds everything.
2. TCP connection identity is the **4-tuple**; it governs both how a server scales to millions of connections and how a client exhausts ephemeral ports.
3. The **3-way handshake** costs 1 RTT and negotiates ISN/MSS/window-scale/SACK; **teardown** is 4-way and the active closer pays **2·MSL in TIME_WAIT** to flush the last ACK and stale duplicates.
4. Reliability = **byte sequence numbers + cumulative ACKs + RTO** (Jacobson smoothing, Karn's ambiguity rule) and **fast retransmit** on 3 dup-ACKs; SACK makes multi-loss recovery surgical.
5. **Flow control** (receiver window, zero-window persist, SWS) protects the *receiver*; **congestion control** (slow start → AIMD → fast recovery) protects the *network*.
6. **CUBIC** (loss-based) is the Linux default but causes bufferbloat; **BBR** (model-based, BDP-targeting) gives high throughput with low latency and is the strategic choice for long/lossy paths.
7. Size your window to the **bandwidth-delay product**; window scaling is non-negotiable on the modern WAN.
8. **Nagle + delayed ACK** is a real 40 ms latency bug for chatty RPC — `TCP_NODELAY`. **HOL blocking** is TCP's structural limit, which **QUIC** (HTTP/3, over UDP) removes.

> Read next: [05 — DNS](05_dns.md) for how names become the addresses you connect to, and the HTTP doc for how HTTP/2 and HTTP/3 (QUIC) sit on top of this transport.
