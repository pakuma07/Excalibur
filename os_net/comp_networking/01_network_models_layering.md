# 01 — Network Models & Layering

> **Audience:** staff/principal. You know what an IP address is and you've run `curl`. This doc is about *why networks are layered at all*, what physically and logically happens at each layer, and how a single packet actually crosses from one host to another through switches and routers — ARP, routing, encapsulation and all.
>
> **Primary sources:** Kurose & Ross, *Computer Networking: A Top-Down Approach* (ch. 1); Tanenbaum & Wetherall, *Computer Networks* (ch. 1); Stevens, *TCP/IP Illustrated Vol. 1* (ch. 1–4); RFC 1122 (Host Requirements), RFC 791 (IPv4), RFC 793 (TCP), RFC 826 (ARP), RFC 894 (IP over Ethernet); Cloudflare engineering blog (MTU/PMTUD posts); Saltzer, Reed & Clark, *End-to-End Arguments in System Design* (1984).

---

## 1. Why layering at all

A network stack is a textbook case of **decomposition by interface contract**. The problem — "move bytes reliably from a process on one machine to a process on another, over heterogeneous physical media, across administrative domains, at planetary scale" — is far too large to solve as one monolith. Layering splits it into modules where each layer:

1. **Provides a service** to the layer above through a well-defined interface.
2. **Consumes the service** of the layer below.
3. **Talks to its peer** on the other host via a *protocol* — a set of message formats and rules — without knowing how the layers below actually deliver those messages.

The payoff is the same payoff you get from any good abstraction boundary:

- **Independent evolution.** Wi-Fi replaced Ethernet cabling at the link layer without TCP changing one line. IPv6 is replacing IPv4 underneath TCP and HTTP. QUIC is replacing TCP underneath HTTP/3. Each happened because the *interface contract* between layers held while the *implementation* changed.
- **Reuse.** TCP doesn't care whether it runs over Ethernet, fiber, 5G, or a carrier pigeon (RFC 1149, only half a joke). The link layer doesn't care whether it carries IPv4, IPv6, or ARP.
- **Tractable reasoning.** You can debug a routing problem at L3 without thinking about electrical signalling at L1.

> **The cost of layering.** It is not free. Strict layering can hide information a lower layer needs (e.g., TCP can't see that the link is a lossy wireless link vs. a congested wire — it assumes loss == congestion). It can also cost performance via redundant work (each layer adds a header; each may checksum). Real stacks therefore *cheat* with cross-layer hints (TCP offload, ECN, MSS clamping). Staff engineers should know both the clean model and where production stacks violate it deliberately.

---

## 2. The two reference models, side by side

There are two models you must hold simultaneously. **OSI** is the pedagogical/abstract 7-layer model (ISO, 1984). **TCP/IP** is the model the actual Internet runs on (RFC 1122), usually drawn as 4 or 5 layers. They mostly line up; the differences are where the interview questions and the real-world confusion live.

```
   OSI (7-layer, conceptual)        TCP/IP (5-layer, practical)     Example protocols / units
 ┌──────────────────────────┐     ┌──────────────────────────┐
 │ 7  Application           │ ─┐  │                          │   HTTP, DNS, TLS*, SSH,
 │ 6  Presentation          │  ├─►│ 5  Application           │   gRPC, SMTP    (PDU: message/data)
 │ 5  Session               │ ─┘  │                          │
 ├──────────────────────────┤     ├──────────────────────────┤
 │ 4  Transport             │ ───►│ 4  Transport             │   TCP, UDP, QUIC*, SCTP
 │                          │     │                          │   (PDU: segment / datagram)
 ├──────────────────────────┤     ├──────────────────────────┤
 │ 3  Network               │ ───►│ 3  Network (Internet)    │   IP, ICMP, IGMP, OSPF, BGP
 │                          │     │                          │   (PDU: packet / datagram)
 ├──────────────────────────┤     ├──────────────────────────┤
 │ 2  Data Link             │ ───►│ 2  Link (Data Link)      │   Ethernet, Wi-Fi 802.11,
 │                          │     │                          │   ARP, PPP, VLAN  (PDU: frame)
 ├──────────────────────────┤     ├──────────────────────────┤
 │ 1  Physical              │ ───►│ 1  Physical              │   copper, fiber, radio
 │                          │     │                          │   (PDU: bits / symbols)
 └──────────────────────────┘     └──────────────────────────┘
   * TLS and QUIC straddle layers; see notes below.
```

**The 4-layer view** (the original RFC 1122 view) collapses Physical and Link into one "Link" layer, giving: Application, Transport, Internet, Link. The 5-layer view keeps Physical separate, which is more honest for anyone who has ever debugged a bad cable. Use 5-layer for teaching, 4-layer when quoting RFC 1122.

### 2.1 Where OSI and TCP/IP disagree

| Topic | OSI | TCP/IP reality |
|---|---|---|
| Layers 5/6/7 | Distinct Session, Presentation, Application | Squashed into one **Application** layer. Session (e.g., resumption) and presentation (e.g., encoding/encryption) are handled *inside* application protocols or libraries (TLS, gRPC, JSON). |
| Conformance | Designed top-down as a standard before implementation | Defined by *running code* (RFC 1122) — "we reject kings, presidents, and voting; we believe in rough consensus and running code" (Dave Clark). |
| L2/L1 split | Sharp Data-Link vs Physical | Often merged; the "Link layer" includes the NIC driver and the medium. |
| Where it won | Textbooks, OSI layer numbers ("that's a layer-7 load balancer") | The actual Internet. |

> **The "layer N" vocabulary survives even though OSI lost.** Engineers say "layer-2 switch," "layer-3 routing," "layer-4 load balancer (by IP:port)," "layer-7 proxy (by URL/host)." These numbers are *OSI* numbers. Know them cold.

### 2.2 The awkward protocols

- **TLS / SSL** sits above Transport (it rides on TCP) but below the Application protocol (HTTP runs on it). In OSI terms it's doing Presentation (encryption) and some Session (handshake, resumption). Pragmatically: "layer 6-ish," but it's just a library between your socket and your HTTP code.
- **QUIC** (RFC 9000) is a transport protocol that runs *over UDP* (which is itself transport) because the Internet's middleboxes only reliably pass TCP and UDP. So QUIC is L4 functionality tunneled inside an L4 protocol — a layering violation born of ossification. HTTP/3 runs on QUIC.
- **ARP** (RFC 826) is the classic layering oddity: it maps an L3 address (IP) to an L2 address (MAC), so it spans L2/L3. It's carried directly in Ethernet frames (not inside IP), which is why we treat it as a link-layer helper.
- **ICMP** (RFC 792) is carried *inside* IP packets (it has an IP header) yet it's a control protocol *for* IP. It's "layer 3.5." `ping` and `traceroute` are built on it (see [03](03_network_layer_routing.md)).

---

## 3. Encapsulation & decapsulation

This is the single most important mechanical idea in networking. As data descends the stack on the sending host, **each layer wraps the PDU from the layer above in its own header (and sometimes trailer)**. As it ascends on the receiving host, each layer strips its header and hands the payload up. The peer layers see "their" PDU as if they were talking directly.

```
 SENDER (top-down encapsulation)                      Each layer's PDU name
 ┌───────────────────────────────────────────────┐
 │ App data: "GET /index.html HTTP/1.1\r\n..."     │   message
 └───────────────────────────────────────────────┘
        │ hand to TCP
        ▼
 ┌──────────┬────────────────────────────────────┐
 │ TCP hdr  │ HTTP message (payload)              │   segment
 └──────────┴────────────────────────────────────┘
        │ hand to IP
        ▼
 ┌────────┬──────────┬────────────────────────────┐
 │ IP hdr │ TCP hdr  │ payload                     │   packet / datagram
 └────────┴──────────┴────────────────────────────┘
        │ hand to Ethernet
        ▼
 ┌──────────┬────────┬──────────┬──────────┬──────┐
 │ Eth hdr  │ IP hdr │ TCP hdr  │ payload  │ FCS  │   frame
 └──────────┴────────┴──────────┴──────────┴──────┘
        │ NIC serializes to bits/symbols
        ▼
 ~~~~~~~~~~~~~~~~~~~~~~~~ wire ~~~~~~~~~~~~~~~~~~~~~~~  bits


 RECEIVER (bottom-up decapsulation): strip Eth → check FCS →
 strip IP → strip TCP → deliver HTTP message to the listening socket.
```

The key invariant: **a header from layer N is payload (opaque bytes) to layer N−1.** Ethernet doesn't parse the IP header; it just carries it. IP doesn't parse TCP; it just carries it. This is what makes the layers swappable.

### 3.1 Overhead accounting (why MTU math matters)

For a typical IPv4 + TCP packet over Ethernet with no options:

| Header | Size | Notes |
|---|---|---|
| Ethernet header | 14 bytes | 6 dst MAC + 6 src MAC + 2 EtherType |
| Ethernet FCS (trailer) | 4 bytes | CRC-32 (not counted in MTU) |
| IPv4 header | 20 bytes | minimum, no options |
| TCP header | 20 bytes | minimum, no options |
| **Total L2–L4 overhead** | **54 bytes** of the frame (14 Eth + 20 IP + 20 TCP) | leaving 1500 − 40 = **1460** bytes of TCP payload on a standard 1500-byte MTU |

The 1460 number is the canonical **TCP MSS** (Maximum Segment Size) on a 1500-byte Ethernet link. Add 12 bytes of TCP timestamp options and it drops to 1448. Every byte of header is a byte you don't get to spend on payload — at scale this is why jumbo frames (§7) and header compression exist.

---

## 4. What actually lives at each layer

Going top-down (the "top-down approach"), because that's how you experience the network as an application developer.

### 4.1 Application layer (L7)
Where your program lives. Protocols define message formats and exchange rules:
- **HTTP/HTTPS** (RFC 9110/9111), **DNS** (RFC 1035 — name → IP), **SSH**, **SMTP/IMAP**, **gRPC** (HTTP/2 framing), **WebSocket**.
- This layer cares about *semantics*: "fetch this resource," "resolve this name." It has no idea about IP addresses or routing — it hands a hostname + payload to the transport layer (often via the sockets API) and the lower layers do the work.

### 4.2 Transport layer (L4)
Provides **process-to-process** communication (the network layer only gets you host-to-host). The differentiator is the **port number** (16-bit), which multiplexes many conversations onto one IP address.

| | TCP (RFC 793 / 9293) | UDP (RFC 768) |
|---|---|---|
| Connection | Connection-oriented (3-way handshake) | Connectionless |
| Reliability | Reliable, ordered, retransmits lost data | Best-effort, may drop/reorder/dup |
| Flow control | Yes (receiver window) | No |
| Congestion control | Yes (cwnd, AIMD) | No (app's problem) |
| Header | 20+ bytes | 8 bytes |
| Use | Web, files, anything needing order/reliability | DNS, VoIP, games, QUIC, DHCP |

### 4.3 Network layer (L3)
**Host-to-host delivery across networks** — the global addressing and routing layer. This is **IP** (IPv4 RFC 791, IPv6 RFC 8200). Its job:
- **Addressing:** every interface gets an IP address that is globally (or at least routably) meaningful.
- **Routing:** each router examines the destination IP and forwards the packet one hop closer (longest-prefix match — see [03](03_network_layer_routing.md)).
- **Best-effort, connectionless:** IP makes *no* guarantees — no ordering, no delivery, no duplicate suppression. Those are L4's job (or the app's). This minimalism is deliberate (see §6, the hourglass).
- Helpers: **ICMP** (errors/diagnostics), **ARP** (IP→MAC, technically L2.5), routing protocols (OSPF, BGP) which run *above* IP but configure L3 forwarding.

### 4.4 Link layer (L2)
**Hop-to-hop delivery across a single link/network segment.** This is **Ethernet** (IEEE 802.3), **Wi-Fi** (802.11), PPP, etc. Its job:
- Frame the bits, address them with **MAC addresses** (48-bit, link-local scope only), detect errors (FCS/CRC).
- Get a frame from one NIC to the next NIC *on the same link or LAN*. A MAC address is meaningless one hop away — it is rewritten at every router (§8).
- Covered in depth in [02 — Link Layer & Switching](02_link_layer_switching.md).

### 4.5 Physical layer (L1)
Turns bits into signals on the medium: voltage levels on copper, light pulses on fiber, RF modulation on the air. Defines connectors, cabling, encoding (e.g., 8b/10b), baud rates. When you "have a bad cable," you have an L1 problem that manifests as L2 frame errors.

---

## 5. The end-to-end principle

> **End-to-End Argument** (Saltzer, Reed, Clark, 1984): *A function should be implemented at the endpoints of a communication system rather than in the network's intermediate nodes, unless it can be completely and correctly implemented only at the endpoints — in which case partial implementation in the network is only a performance optimization.*

The canonical example is **reliable file transfer**. Even if every link did its own reliability check (link-layer ACKs), you *still* need an end-to-end check, because errors can occur in the endpoints' memory, in a router's buffer, while crossing the bus — anywhere the per-link check doesn't cover. Since the endpoints must check anyway, putting full reliability *in the network* is largely redundant. Hence: **the network core stays dumb and fast (just forward packets); the smarts (reliability, ordering, encryption) live at the endpoints (TCP, TLS).**

Consequences you live with:
- **IP is best-effort and stateless.** Routers don't track connections, which is *why* the Internet scales and survives router failures (reroute around them; no per-flow state to lose).
- **Innovation happens at the edge.** You can deploy a new transport (QUIC) or app protocol without asking the network operators' permission. This is the architectural reason the Internet was able to grow explosively.
- **Where it's violated:** NAT, firewalls, and "middleboxes" put per-flow state in the network and are the bane of new-protocol deployment ("ossification"). QUIC encrypts almost everything precisely to keep middleboxes from meddling. Cloudflare and others have written extensively on fighting middlebox ossification.

---

## 6. The hourglass model — IP as the narrow waist

```
   Many applications ───────────────────────────────
    HTTP  DNS  SSH  SMTP  RTP  gRPC  QUIC  ...        \   wide top
        \    \    |    /    /    /                      \
         \    \   |   /    /    /                        \
          TCP      UDP      SCTP   DCCP                    >  many transports
              \     |       /                            /
               \    |      /                            /
        ════════════════════════════════                <- THE NARROW WAIST
                    IP                                       (IPv4 / IPv6)
        ════════════════════════════════                <- everything passes through here
               /    |      \
          Ethernet Wi-Fi  Fiber  5G  PPP  DOCSIS  ...    >  many link technologies
         /     |     \      \                           /
   Many physical media ──────────────────────────────/   wide bottom
```

The Internet's architecture is an **hourglass**: a huge diversity of applications and transports at the top, a huge diversity of physical media and link technologies at the bottom, and a **single thin layer — IP — at the waist** that everything funnels through. "IP over everything, everything over IP."

Why this shape is genius:
- **Any app can run over any medium** as long as both speak IP. You don't need an N×M matrix of "HTTP-over-Wi-Fi," "HTTP-over-fiber," "DNS-over-5G" adapters — you need each app to speak IP, and each medium to carry IP. The waist decouples the two wide ends.
- **The waist must be minimal and stable** to stay universal. This is the deep reason IP is so feature-poor (best-effort, no QoS guarantees, no built-in security): every feature added to the waist is a feature *every* medium and *every* app must support. Minimalism is what kept IP universal.
- **The waist is hard to change** — which is exactly why IPv4→IPv6 has taken 25+ years. You're replacing the one thing everything depends on. (Tanenbaum: the narrow waist is both the Internet's greatest strength and its greatest source of inertia.)

---

## 7. MTU & fragmentation

The **MTU (Maximum Transmission Unit)** is the largest payload a link can carry in one frame. For standard Ethernet it's **1500 bytes** (the IP packet, not counting the 14-byte Ethernet header / 4-byte FCS). When an IP packet is larger than the next link's MTU, something must give.

```
 Standard Ethernet frame size budget:
 ┌──────┬────────────────── 1500-byte MTU (IP packet) ───────────────┬─────┐
 │ Eth  │ IP hdr │ TCP hdr │ ............ payload ............        │ FCS │
 │ 14   │  20    │   20    │           up to 1460                     │  4  │
 └──────┴────────┴─────────┴──────────────────────────────────────────┴─────┘
 Jumbo frame: MTU 9000 (data-center / storage), needs end-to-end support.
```

### 7.1 IPv4 fragmentation
If an IPv4 packet exceeds a link's MTU and the **Don't Fragment (DF)** bit is *not* set, a router may **fragment** it: split the payload into pieces, each with its own IP header, using the IP header's `Identification`, `Flags (MF = More Fragments)`, and `Fragment Offset` fields. **Reassembly happens only at the final destination**, never at intermediate routers (because different fragments may take different paths).

Fragmentation is *bad* for performance and reliability:
- **Loss amplification:** lose one fragment, the whole original packet is undeliverable and must be resent.
- **Reassembly cost & attacks:** holding fragments consumes router/host memory; historically a source of DoS (teardrop, fragment overlap attacks).
- **Stateless-firewall trouble:** L4 ports are only in the first fragment.

### 7.2 IPv6 changed this
**IPv6 routers do not fragment.** If a packet is too big, the router drops it and sends back an ICMPv6 "Packet Too Big." Fragmentation, if needed at all, is done **only by the source host**. This pushes the responsibility to the endpoints (end-to-end principle again).

### 7.3 Path MTU Discovery (PMTUD)
The right answer is to *avoid* fragmentation by discovering the smallest MTU along the path:
1. Sender sets the **DF bit** and sends a packet at its local MTU.
2. If a router on the path has a smaller MTU, it drops the packet and returns **ICMP "Fragmentation Needed"** (IPv4) / "Packet Too Big" (IPv6), reporting the smaller MTU.
3. Sender lowers its packet size and retries. Eventually it converges on the **Path MTU**.

> **The classic production failure:** an overzealous firewall blocks *all* ICMP (treating it as "hacker stuff"). PMTUD breaks silently — the sender never learns the path MTU, packets get black-holed, and you get the infamous "small requests work, large ones hang" symptom (TLS handshake completes, then the big certificate or POST body stalls). **Never blanket-block ICMP.** Cloudflare and most ops teams treat "ICMP Type 3 Code 4 must pass" as a hard rule. TCP also has **MSS clamping** as a workaround: a middlebox rewrites the TCP MSS option down so the endpoints never send oversized segments in the first place.

---

## 8. The full packet walk: host → host through switches and routers

This is the question that separates people who *know* networking from people who've memorized acronyms. Let's trace an HTTP request from host **A (10.0.1.5)** on LAN 1 to host **B (10.0.2.9)** on LAN 2, where the two LANs are connected by a **router R**. A and B are on different subnets, so this goes through L3 routing.

```
   LAN 1 (10.0.1.0/24)                          LAN 2 (10.0.2.0/24)
 ┌─────────┐      ┌─────────┐                 ┌─────────┐      ┌─────────┐
 │  Host A │──────│ Switch  │────┐      ┌──────│ Switch  │──────│  Host B │
 │10.0.1.5 │      │  (L2)   │    │      │      │  (L2)   │      │10.0.2.9 │
 │MAC aa:..│      └─────────┘    │      │      └─────────┘      │MAC bb:..│
 └─────────┘                ┌────┴──────┴────┐                  └─────────┘
                            │    Router R    │
                            │ if0 10.0.1.1   │  ← A's default gateway
                            │ MAC rr:11      │
                            │ if1 10.0.2.1   │  ← B's default gateway
                            │ MAC rr:22      │
                            └────────────────┘
```

**Step 0 — DNS & socket.** A's app resolves `host-b` → `10.0.2.9` (DNS, L7) and opens a TCP socket. TCP/IP build the segment and packet.

**Step 1 — A decides: local or remote?** A compares B's IP (`10.0.2.9`) against its own subnet (`10.0.1.0/24`) using its netmask. `10.0.2.9` is **not** in `10.0.1.0/24`, so B is **remote**. A must send the frame to its **default gateway** (router R, `10.0.1.1`) — *not* directly to B.

> Critical insight: **the destination IP stays B's the whole way (10.0.2.9), but the destination MAC is the next hop's.** A addresses the *frame* to the router, but the *packet* to B.

**Step 2 — ARP for the gateway.** A needs R's MAC address for `10.0.1.1`. It checks its ARP cache; on a miss it **broadcasts** an ARP request: "Who has 10.0.1.1? Tell 10.0.1.5" (dst MAC `ff:ff:ff:ff:ff:ff`). The switch floods it; R replies "10.0.1.1 is at rr:11". A caches it. (ARP details in [02](02_link_layer_switching.md).)

**Step 3 — A frames and sends.** A builds the frame:

```
 ┌────────────┬────────────┬─────────────────────────────────────┐
 │ dst MAC    │ src MAC    │ IP packet                            │
 │ rr:11 (R)  │ aa:.. (A)  │ src=10.0.1.5 dst=10.0.2.9  TCP ...   │
 └────────────┴────────────┴─────────────────────────────────────┘
```
Note: **L2 dst = router**, but **L3 dst = B**.

**Step 4 — Switch forwards (L2, no IP involved).** The switch on LAN 1 looks up `rr:11` in its MAC table and forwards the frame out the port toward R. **The switch never looks at the IP header.** It does not decrement TTL. It is invisible to L3 (you won't see it in a `traceroute`).

**Step 5 — Router R routes (L3).** R receives the frame, sees dst MAC = its own → strips the Ethernet header → reads the IP header. dst = `10.0.2.9`. R consults its **routing table**, finds `10.0.2.0/24` is directly connected on `if1`. R now must deliver to B on LAN 2:
- R **decrements the TTL** (and recomputes the IP header checksum). If TTL hits 0, R drops it and sends ICMP "Time Exceeded" — this is the mechanism `traceroute` exploits.
- R ARPs for B's MAC (`10.0.2.9 → bb:..`) on `if1` if not cached.
- R builds a **brand-new Ethernet frame**:

```
 ┌────────────┬────────────┬─────────────────────────────────────┐
 │ dst MAC    │ src MAC    │ IP packet (UNCHANGED addresses)      │
 │ bb:.. (B)  │ rr:22 (R)  │ src=10.0.1.5 dst=10.0.2.9  TTL−1     │
 └────────────┴────────────┴─────────────────────────────────────┘
```

> **The big takeaway:** the **IP source/dest never change** end-to-end (absent NAT). The **MAC source/dest are rewritten at every router hop**. Routers swap L2 framing; switches don't touch L3. TTL decrements once per *router*, not per switch.

**Step 6 — B's switch forwards, B decapsulates.** LAN 2's switch forwards to B's port. B sees dst MAC = its own, strips Ethernet, sees dst IP = its own, strips IP, hands the TCP segment to the kernel, which delivers the payload to the socket bound to the destination port. The HTTP request arrives. The reply retraces the path in reverse.

---

## 9. Working example — parse a raw Ethernet/IP/TCP packet in Python

This is pure standard library (`struct`, `socket`), no dependencies, and it *runs*. It parses a hard-coded raw frame (so it runs anywhere without root) and prints every field at each layer. It demonstrates exactly the decapsulation from §3.

```python
#!/usr/bin/env python3
"""
parse_packet.py — decapsulate a raw Ethernet/IPv4/TCP frame, field by field.
Pure stdlib. Run: python parse_packet.py
The sample frame is a real-shaped SYN packet built byte-for-byte below.
"""
import struct
import socket

def mac(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)

def ipv4(b: bytes) -> str:
    return ".".join(str(x) for x in b)

def parse_ethernet(frame: bytes):
    # 6 dst + 6 src + 2 ethertype = 14 bytes
    dst, src, etype = struct.unpack("!6s6sH", frame[:14])
    return {
        "dst_mac": mac(dst),
        "src_mac": mac(src),
        "ethertype": hex(etype),       # 0x0800 = IPv4, 0x0806 = ARP, 0x86dd = IPv6
    }, frame[14:]

def parse_ipv4(packet: bytes):
    # First byte: version (4 bits) + IHL (4 bits, in 32-bit words)
    ver_ihl = packet[0]
    version = ver_ihl >> 4
    ihl = (ver_ihl & 0x0F) * 4         # header length in bytes
    (tos, total_len, ident, flags_frag, ttl, proto, checksum) = struct.unpack(
        "!BHHHBBH", packet[1:12]
    )
    src = ipv4(packet[12:16])
    dst = ipv4(packet[16:20])
    flags = flags_frag >> 13           # top 3 bits: reserved, DF, MF
    frag_offset = flags_frag & 0x1FFF
    proto_name = {1: "ICMP", 6: "TCP", 17: "UDP"}.get(proto, str(proto))
    return {
        "version": version, "ihl_bytes": ihl, "tos": tos,
        "total_length": total_len, "id": ident,
        "DF": bool(flags & 0b010), "MF": bool(flags & 0b001),
        "frag_offset": frag_offset, "ttl": ttl,
        "protocol": proto_name, "checksum": hex(checksum),
        "src_ip": src, "dst_ip": dst,
    }, packet[ihl:]

def parse_tcp(segment: bytes):
    (src_port, dst_port, seq, ack, off_flags, window, checksum, urg) = struct.unpack(
        "!HHIIHHHH", segment[:20]
    )
    data_offset = (off_flags >> 12) * 4       # header length in bytes
    flag_bits = off_flags & 0x01FF
    names = ["FIN", "SYN", "RST", "PSH", "ACK", "URG", "ECE", "CWR", "NS"]
    flags = [names[i] for i in range(9) if flag_bits & (1 << i)]
    return {
        "src_port": src_port, "dst_port": dst_port,
        "seq": seq, "ack": ack, "header_bytes": data_offset,
        "flags": flags, "window": window, "checksum": hex(checksum),
    }, segment[data_offset:]

def build_sample_syn() -> bytes:
    """Build a realistic Ethernet/IPv4/TCP SYN so the demo is self-contained."""
    eth = struct.pack("!6s6sH",
                      bytes.fromhex("rr11rr11rr11".replace("rr", "aa")),  # dst
                      bytes.fromhex("001122334455"),                       # src
                      0x0800)                                              # IPv4
    # IPv4 header (20 bytes, no options)
    ip = struct.pack("!BBHHHBBH4s4s",
                     0x45,            # ver 4, IHL 5 (20 bytes)
                     0x00,            # ToS
                     40,              # total length = 20 IP + 20 TCP
                     0x1c46,          # identification
                     0x4000,          # flags=DF, frag offset 0
                     64,              # TTL
                     6,               # protocol = TCP
                     0x0000,          # checksum (left 0 for the demo)
                     socket.inet_aton("10.0.1.5"),
                     socket.inet_aton("10.0.2.9"))
    # TCP header (20 bytes): SYN, seq=1000, window=64240
    tcp = struct.pack("!HHIIHHHH",
                      49152, 80,      # src port, dst port (HTTP)
                      1000, 0,        # seq, ack
                      (5 << 12) | 0x002,  # data offset=5 words, flags=SYN
                      64240, 0x0000, 0)   # window, checksum, urg
    return eth + ip + tcp

if __name__ == "__main__":
    frame = build_sample_syn()
    print(f"raw frame: {len(frame)} bytes\n")

    eth, rest = parse_ethernet(frame)
    print("== Ethernet (L2) =="); [print(f"  {k:12} {v}") for k, v in eth.items()]

    if eth["ethertype"] == hex(0x0800):
        ip, rest = parse_ipv4(rest)
        print("\n== IPv4 (L3) =="); [print(f"  {k:14} {v}") for k, v in ip.items()]

        if ip["protocol"] == "TCP":
            tcp, payload = parse_tcp(rest)
            print("\n== TCP (L4) =="); [print(f"  {k:12} {v}") for k, v in tcp.items()]
            print(f"\n== Payload (L7) == {len(payload)} bytes: {payload!r}")
```

Expected output (abridged):
```
raw frame: 54 bytes

== Ethernet (L2) ==
  dst_mac      aa:11:aa:11:aa:11
  src_mac      00:11:22:33:44:55
  ethertype    0x800
== IPv4 (L3) ==
  version        4
  ihl_bytes      20
  DF             True
  ttl            64
  protocol       TCP
  src_ip         10.0.1.5
  dst_ip         10.0.2.9
== TCP (L4) ==
  src_port     49152
  dst_port     80
  flags        ['SYN']
  window       64240
```

> **Optional (scapy):** capturing *live* packets needs raw sockets / root. With scapy (`pip install scapy`): `from scapy.all import sniff; sniff(prn=lambda p: p.summary(), count=5)`. Scapy parses every layer for you (`pkt.show()`), but the hand-rolled parser above is the one that teaches you *what the bytes mean*.

---

## 10. Annotated CLI walkthrough (Linux `ip` / `tcpdump`)

Everything in §8 is observable. Commands tagged for Linux; macOS uses `ifconfig`/`route -n get`.

```bash
# --- L3: my addresses and which subnet each interface owns ---
ip -brief addr show
# eth0   UP   10.0.1.5/24 fe80::211:22ff:fe33:4455/64
#                  ^^^^^^^^^ the /24 tells the host what is "local" (no router needed)

# --- L3: the routing table — how the host picks a next hop ---
ip route
# default via 10.0.1.1 dev eth0          <- everything not local goes to the gateway
# 10.0.1.0/24 dev eth0 proto kernel scope link src 10.0.1.5   <- directly connected
#         ^^^ "scope link" = reachable without a router (ARP directly)

# Which route would the kernel actually pick for a given dst? (longest-prefix match)
ip route get 10.0.2.9
# 10.0.2.9 via 10.0.1.1 dev eth0 src 10.0.1.5     <- confirms: go via the gateway R

# --- L2.5: the ARP cache (IP -> MAC for on-link neighbors) ---
ip neigh
# 10.0.1.1 dev eth0 lladdr rr:11:rr:11:rr:11 REACHABLE   <- the gateway's MAC (step 2 of §8)

# --- L2/L3/L4 together: watch the actual bytes on the wire ---
sudo tcpdump -i eth0 -n -e -vv 'tcp port 80' -c 4
#   -e  show L2 (MAC) header   -n  no DNS/port name resolution   -vv  verbose IP/TCP fields
# 12:00:00.000 aa:..:55 > rr:11:..:11, ethertype IPv4 (0x0800), length 74:
#     10.0.1.5.49152 > 10.0.2.9.80: Flags [S], seq 1000, win 64240, ...
#     ^L2 src/dst MAC (A -> gateway)        ^L3 src/dst IP (A -> B, unchanged)  ^L4 ports/flags
```

Reading that one `tcpdump` line top to bottom *is* the encapsulation diagram from §3: MAC header (L2) → IPv4 (L3) → TCP flags/ports (L4). Notice the **dst MAC is the gateway** while the **dst IP is the real destination** — the §8 insight, visible in the wild. Run `tcpdump` on the router's other interface and you'd see the *same IPs* but *different MACs* — the L2 rewrite.

```bash
# See where ICMP errors / TTL come in (the traceroute mechanism, detailed in doc 03):
traceroute -n 8.8.8.8        # each hop = a router that decremented TTL to 0 and sent ICMP
```

---

## 11. Advanced: where the clean model leaks

The OSI/TCP-IP layering ([§2](#2-the-two-reference-models-side-by-side)) is a teaching
model; production breaks its tidy boundaries constantly, and a staff engineer must
know where.

### Offloads mean the kernel rarely sees real packets

With **TSO/GSO** (segmentation offload) and **GRO/LRO** (receive coalescing,
[Net 08 §7](08_network_performance_tuning.md)), the OS hands the NIC a **64 KB
"super-segment"** and the NIC chops it into MTU-sized frames on the wire (and the
reverse on receive). So a `tcpdump` on the host shows giant segments that **never
existed on the wire** — the L4/L2 boundary is blurred for performance. When you debug
MTU/MSS ([scenarios 04.8](../enterprise_scenarios/04_network_incidents.md)), remember
the host's view ≠ the wire's view; capture at a switch SPAN port for ground truth.

### Tunnels re-layer the stack (a packet inside a packet)

VXLAN, GRE, IPsec, WireGuard, and QUIC all **encapsulate** — an inner L2/L3/L4 packet
becomes the *payload* of an outer one ([02 §12](02_link_layer_switching.md),
[10 §4](10_cloud_sdn_overlays.md)). The model now has *two* IP layers, and tools that
assume one get confused. This re-layering is why overlay MTUs shrink (encap overhead)
and why a firewall ACL written for the inner addresses never matches the outer-
encapsulated packet.

### XDP/eBPF — processing below the layers

**XDP** runs an eBPF program **in the NIC driver, before the kernel network stack even
allocates an `skb`** — so you can drop/redirect/rewrite packets at L2/L3 without the
normal layer traversal (the basis of eBPF DDoS scrubbing and Cilium's dataplane,
[Net 08 §advanced](08_network_performance_tuning.md), [10 §advanced](10_cloud_sdn_overlays.md)).
It deliberately *bypasses* the layered path for speed — the model's narrow waist
([§6](#6-the-hourglass-model--ip-as-the-narrow-waist)) is still IP, but the
*processing* no longer walks every layer.

### The end-to-end principle, revisited

The model says reliability belongs at the endpoints ([§5](#5-the-end-to-end-principle)),
yet the middle is full of **middleboxes** — NATs, L7 proxies, TLS-terminating LBs,
WAFs — that inspect and rewrite. This "ossification" is exactly why **QUIC** moved the
transport into userspace over encrypted UDP: to take the transport back from
middleboxes that had calcified TCP. The layering didn't change; who *owns* each layer
did.

---

## Key Takeaways

1. **Layering is decomposition by interface contract.** Each layer offers a service up, consumes a service down, and talks to its peer via a protocol — enabling independent evolution (Wi-Fi, IPv6, QUIC all swapped in without rewriting their neighbors). The cost is hidden information and redundant work, which real stacks deliberately cheat around.
2. **Hold both models.** OSI (7 layers) gives you the vocabulary engineers actually use ("layer-7 proxy"); TCP/IP (4–5 layers, RFC 1122) is what the Internet runs. The squashed L5–L7 and the awkward protocols (TLS, QUIC, ARP, ICMP) are where the real understanding lives.
3. **Encapsulation is the mechanical heart:** each layer wraps the PDU above in its header; a layer-N header is opaque payload to layer N−1. That opacity is exactly what makes layers swappable. Standard overhead is 54 bytes (Eth+IP+TCP) → MSS 1460.
4. **The end-to-end principle keeps the core dumb and the edges smart** — IP is best-effort and stateless, reliability/encryption live in the endpoints. This is why the Internet scales and why edge innovation needs no permission. NAT/firewalls violate it and cause "ossification."
5. **IP is the narrow waist of the hourglass.** Everything funnels through one minimal, stable layer; that minimalism is *why* IP stayed universal and *why* IPv6 migration is so painful.
6. **MTU and fragmentation:** IPv4 routers may fragment (reassembly only at the destination); IPv6 never fragments in transit. PMTUD finds the path MTU using ICMP — so **never blanket-block ICMP**, or large packets black-hole silently.
7. **The packet walk:** destination IP is constant end-to-end; destination MAC is rewritten at every router. Switches operate purely at L2 (don't touch IP, don't decrement TTL, invisible to traceroute); routers operate at L3 (rewrite L2 framing, decrement TTL, ARP for the next hop). Internalize this and most "why can't host A reach host B" puzzles solve themselves.

> Read next: [02 — Link Layer & Switching](02_link_layer_switching.md) for what happens *inside* a single LAN (Ethernet, ARP, VLANs, switching, the data-center fabric), and [03 — Network Layer, IP & Routing](03_network_layer_routing.md) for how routers decide the next hop at global scale (CIDR, OSPF, BGP, anycast).
