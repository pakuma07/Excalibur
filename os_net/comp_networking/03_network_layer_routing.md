# 03 — Network Layer, IP & Routing

> **Audience:** staff/principal. You've subnetted a `/24` before. This doc is about how IP addressing, subnetting, and the routing system *actually* work — from the bits of the IPv4 header through longest-prefix match, OSPF/BGP, ECMP, and anycast — and why a single BGP misconfiguration can take a company (or a chunk of the Internet) offline.
>
> **Primary sources:** Kurose & Ross ch. 4–5; Tanenbaum & Wetherall ch. 5; Stevens, *TCP/IP Illustrated Vol. 1* ch. 3, 6, 7–9; RFC 791 (IPv4), RFC 792 (ICMP), RFC 1918 (private addrs), RFC 4632 (CIDR), RFC 8200 (IPv6), RFC 4861 (NDP), RFC 4271 (BGP-4), RFC 2328 (OSPFv2), RFC 4786 (anycast); Cloudflare post-mortems (July 2 2019 BGP leak; June 2025 outages) and Facebook/Meta Oct 4 2021 outage report.

---

## 1. The network layer's contract

The network layer (IP) provides one thing the link layer cannot: **global, end-to-end host addressing and forwarding across independently administered networks.** A link-layer MAC gets a frame one hop; an IP address gets a packet across the planet, through dozens of autonomous networks, with no shared L2.

IP's contract is deliberately minimal (the end-to-end principle and the hourglass waist — see [01](01_network_models_layering.md)):
- **Best-effort, connectionless:** no delivery guarantee, no ordering, no duplicate suppression, no built-in flow/congestion control. Those are TCP's or the app's problem.
- **Stateless forwarding:** a router forwards each packet independently by destination address; it holds no per-flow state. This is *why* the Internet survives router failures (reroute; nothing to lose) and scales to billions of flows.
- **Global addressing:** every routable interface has an address meaningful everywhere.

Two jobs split the network layer: the **data plane** (forward this packet now, in hardware, by table lookup) and the **control plane** (build the forwarding tables — routing protocols, by hand or by OSPF/BGP).

---

## 2. IPv4 addressing & the header

### 2.1 The address

An IPv4 address is **32 bits**, written as four dotted-decimal octets: `192.168.10.5` = `11000000.10101000.00001010.00000101`. ~4.3 billion total — long exhausted, hence NAT and IPv6.

An address is split into a **network portion** (prefix) and a **host portion** by a **prefix length** (`/n`, the number of leading network bits). This is **CIDR** (§4); the old "Class A/B/C" scheme (fixed /8, /16, /24 boundaries) is obsolete and only worth knowing as history.

### 2.2 The IPv4 header (RFC 791)

```
  0                   1                   2                   3
  0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |Ver=4  |  IHL  |     ToS/DSCP  |          Total Length         |  word 0
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |         Identification        |Flags|     Fragment Offset     |  word 1  (fragmentation)
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |  Time To Live |    Protocol   |        Header Checksum        |  word 2
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                       Source IP Address                       |  word 3
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                    Destination IP Address                     |  word 4
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
 |                  Options (if IHL > 5)         |    Padding    |  optional
 +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

| Field | Bits | Meaning / why it matters |
|---|---|---|
| Version | 4 | 4 for IPv4. |
| IHL | 4 | Header length in 32-bit words (min 5 = 20 bytes). |
| ToS / DSCP | 8 | QoS class (DiffServ); ECN bits live here too. |
| Total Length | 16 | Whole packet (header+data) in bytes → max **65,535**. |
| Identification, Flags, Frag Offset | 16+3+13 | **Fragmentation** (DF/MF bits, reassembly). See [01](01_network_models_layering.md) §7. |
| **TTL** | 8 | Decremented by **every router**; at 0 the packet is dropped and ICMP "Time Exceeded" is returned. Loop protection — and the engine of **traceroute** (§9). |
| Protocol | 8 | Payload type: 1=ICMP, 6=TCP, 17=UDP. |
| Header Checksum | 16 | Covers the *header only* (recomputed at each hop because TTL changes). IPv6 drops it — L2 CRC + L4 checksums suffice. |
| Source / Dest IP | 32 each | **Unchanged end-to-end** (absent NAT). |

---

## 3. Subnetting & CIDR

A prefix `/n` means "the first `n` bits are the network; the remaining `32−n` bits identify hosts within it."

```
 10.0.5.0/24    netmask 255.255.255.0     11111111.11111111.11111111.00000000
                                          └──────── 24 network ──────┘└ 8 host ┘
   Network address  (all host bits 0):  10.0.5.0       <- names the subnet, not a host
   Broadcast address(all host bits 1):  10.0.5.255     <- L3 broadcast to the subnet
   Usable host range:                   10.0.5.1 – 10.0.5.254
   Usable host count:  2^(32-24) - 2 =  254            <- minus network & broadcast
```

The **−2** (network + broadcast not usable as host addresses) is the rule that catches people. Exception: RFC 3021 `/31` point-to-point links use both addresses (2 usable, no broadcast needed on a 2-host link).

### 3.1 Worked subnet math

**Example A — split `192.168.1.0/24` into 4 equal subnets.** Need 4 subnets → 2 extra bits (2² = 4) → new prefix `/26`. Each `/26` has 2⁶ = 64 addresses, 62 usable.

```
 /26 block size = 256 / 4 = 64 addresses each
 ┌─────────────────┬──────────────┬───────────────┬──────────────────┐
 │ Subnet          │ Network       │ Broadcast      │ Usable hosts     │
 ├─────────────────┼──────────────┼───────────────┼──────────────────┤
 │ 192.168.1.0/26  │ .0           │ .63           │ .1   – .62  (62) │
 │ 192.168.1.64/26 │ .64          │ .127          │ .65  – .126 (62) │
 │ 192.168.1.128/26│ .128         │ .191          │ .129 – .190 (62) │
 │ 192.168.1.192/26│ .192         │ .255          │ .193 – .254 (62) │
 └─────────────────┴──────────────┴───────────────┴──────────────────┘
```

**Example B — VLSM (variable-length subnet masking): right-size to need.** Use the right prefix for each subnet instead of wasting a uniform size:

| Need | Hosts required | Host bits | Prefix | Block size | Usable |
|---|---|---|---|---|---|
| Server LAN | 100 | 7 (2⁷=128) | /25 | 128 | 126 |
| Office LAN | 50 | 6 (2⁶=64) | /26 | 64 | 62 |
| Point-to-point link | 2 | 1 (RFC 3021) | /31 | 2 | 2 |

The mental shortcuts staff engineers keep in their heads:
- **Block size = 256 − (the mask octet value)** in the "interesting" octet. For `/26`, mask octet = 192, block = 64. Subnets fall on multiples of 64.
- **Hosts = 2^(32−prefix) − 2.** `/30` = 2 usable (classic router link, pre-/31). `/24` = 254. `/16` = 65,534.
- **CIDR aggregation (supernetting):** contiguous prefixes merge into one route. `10.0.0.0/24` + `10.0.1.0/24` → `10.0.0.0/23`. This is *the* mechanism that keeps the global routing table from exploding — one advertised prefix instead of thousands.

---

## 4. Private addresses (RFC 1918) & NAT/PAT

IPv4 exhaustion forced **address reuse**. RFC 1918 reserves three ranges that are **not globally routable** — anyone can use them internally, and they appear on millions of private networks simultaneously:

| Range | Prefix | Size | Typical use |
|---|---|---|---|
| `10.0.0.0 – 10.255.255.255` | `10.0.0.0/8` | 16.7M | Large enterprises, cloud VPCs |
| `172.16.0.0 – 172.31.255.255` | `172.16.0.0/12` | 1M | Mid-size |
| `192.168.0.0 – 192.168.255.255` | `192.168.0.0/16` | 65K | Home/SOHO |

(Also: `100.64.0.0/10` CGNAT shared space (RFC 6598), `169.254.0.0/16` link-local, `127.0.0.0/8` loopback.)

### 4.1 NAT and PAT

A **NAT** gateway rewrites private source addresses to a public one as packets leave, and reverses it on the way back. The common form is **PAT (Port Address Translation)** / "NAT overload" / "masquerade" — *many* private hosts share *one* public IP, disambiguated by **source port**:

```
 Inside (private)                 NAT gateway (public 203.0.113.5)         Outside
 10.0.0.10:51000 ──► to 93.184.x:443 ──► rewrite src to 203.0.113.5:40001 ──► server
 10.0.0.11:51000 ──► to 93.184.x:443 ──► rewrite src to 203.0.113.5:40002 ──► server
                              NAT translation table:
                   ┌────────────────────────┬───────────────────────────┐
                   │ inside  10.0.0.10:51000 │ outside 203.0.113.5:40001 │
                   │ inside  10.0.0.11:51000 │ outside 203.0.113.5:40002 │
                   └────────────────────────┴───────────────────────────┘
 Reply to 203.0.113.5:40001 ──► table lookup ──► rewrite dst to 10.0.0.10:51000 ──► inside
```

NAT's consequences (the reason it's both indispensable and hated):
- **It conserved IPv4** and let the Internet keep growing past exhaustion — its actual historical role.
- **It breaks the end-to-end principle**: the gateway holds per-flow state (violating IP's statelessness), and inbound connections can't reach an inside host without explicit port-forwarding. This breaks peer-to-peer (VoIP, games, WebRTC) → spawns **STUN/TURN/ICE** and **NAT hole-punching** as workarounds.
- **It is not a firewall** (though it incidentally hides inside hosts). Don't conflate the two.
- **IPv6 is supposed to make NAT unnecessary** (every device gets a globally routable address) — the cleaner answer, if NAT-as-perceived-security weren't so culturally entrenched.

---

## 5. IPv6 — addressing, SLAAC, and why

### 5.1 Why
IPv4's 32 bits (~4.3B addresses) ran out (IANA exhausted its pool in 2011). **IPv6 (RFC 8200)** uses **128 bits** → 3.4×10³⁸ addresses — enough to never worry again. Beyond size, IPv6 also: removes in-transit fragmentation, drops the header checksum, mandates no-NAT global addressing, and bakes in autoconfiguration.

### 5.2 Addressing
Written as eight groups of four hex digits, with `::` compressing one run of zeros:
```
 2001:0db8:0000:0000:0000:ff00:0042:8329
 2001:db8::ff00:42:8329          (leading zeros dropped, one :: for the zero run)

 /64 is the standard subnet size:  2001:db8:abcd:0012 : :/64
                                   └──── 64-bit prefix ────┘└ 64-bit interface ID ┘
 fe80::/10   link-local (every interface auto-gets one; used by NDP, like IPv4 169.254)
 ::1         loopback        ::/0  default route        ff00::/8  multicast (no broadcast!)
```

IPv6 has **no broadcast** — it uses **multicast** (e.g., solicited-node multicast for neighbor discovery) so only relevant NICs are interrupted. **NDP (RFC 4861)** replaces ARP (see [02](02_link_layer_switching.md) §7).

### 5.3 SLAAC (Stateless Address Autoconfiguration)
A host can configure its own global address **without DHCP**:
```
 1. Host forms a link-local fe80:: address; runs DAD (Duplicate Address Detection).
 2. Host multicasts a Router Solicitation (RS).
 3. Router replies with a Router Advertisement (RA) carrying the /64 prefix + gateway.
 4. Host appends its own interface ID (EUI-64 from MAC, or a random/privacy address)
    to the advertised prefix → a complete, globally routable address. No server needed.
```
The `/64` boundary is *load-bearing* in IPv6: SLAAC assumes a 64-bit interface ID, so subnets are essentially always `/64`. (Stateful **DHCPv6** still exists where you need central control, e.g., DNS handout / address tracking.)

---

## 6. ICMP — the control protocol (RFC 792)

ICMP is IP's diagnostic/error channel, carried *inside* IP packets (protocol number 1). It does not carry user data; it reports problems and answers probes.

| Type | Message | Used by |
|---|---|---|
| 0 / 8 | Echo Reply / Echo Request | **ping** |
| 3 | Destination Unreachable (code 4 = "Fragmentation Needed", DF set) | reachability; **PMTUD** |
| 5 | Redirect | "use a better gateway" |
| 11 | **Time Exceeded** (TTL hit 0) | **traceroute** |

> **Operational rule (repeat from [01](01_network_models_layering.md)):** do **not** blanket-block ICMP. Type 3 Code 4 is essential for Path MTU Discovery; blocking it silently black-holes large packets ("small requests work, big ones hang"). Rate-limit ICMP, don't kill it.

`ping` = ICMP Echo Request/Reply, measuring round-trip time and loss. `traceroute` is cleverer — §9.

---

## 7. The routing table & longest-prefix match

Every IP host and router has a **routing table (FIB/RIB)** mapping destination *prefixes* to *next hops*. Forwarding a packet is a single question: **which table entry's prefix matches the destination IP, and if several match, which is most specific?**

> **Longest-Prefix Match (LPM):** among all routes whose prefix contains the destination address, pick the one with the **longest prefix** (most network bits = most specific). The default route `0.0.0.0/0` matches everything but is the *least* specific, so it's the fallback only when nothing better matches.

```
 Routing table:
   10.1.2.0/24    -> via 10.0.0.1   (eth1)     specific
   10.1.0.0/16    -> via 10.0.0.2   (eth1)     broader
   0.0.0.0/0      -> via 203.0.113.1(eth0)     default (matches anything)

 Packet to 10.1.2.50:
   /24 matches (10.1.2.0–10.1.2.255)  ✔  prefix len 24
   /16 matches (10.1.0.0–10.1.255.255)✔  prefix len 16
   /0  matches                        ✔  prefix len 0
   → LPM picks the /24 (longest)  → next hop 10.0.0.1

 Packet to 10.1.9.7:
   /24 no, /16 yes, /0 yes  → LPM picks /16 → next hop 10.0.0.2

 Packet to 8.8.8.8:
   only /0 matches          → default route → next hop 203.0.113.1
```

LPM is *why* CIDR works: you can advertise a broad aggregate (`10.0.0.0/8`) and override it with more-specific routes (`10.1.2.0/24`) for special handling, and forwarding always picks the most specific. In hardware, LPM is done at line rate with **TCAM** (ternary content-addressable memory) or tries. This is also the lever in **BGP route hijacks**: announce a *more-specific* prefix than the legitimate owner and you win the LPM contest, sucking in their traffic (§8.4).

---

## 8. Routing protocols

A router's table is built either **statically** (configured by hand) or **dynamically** (learned from a routing protocol that reacts to topology changes).

| | Static | Dynamic |
|---|---|---|
| Setup | Manual | Protocol-driven (OSPF, BGP, …) |
| Reacts to failure | No (stale until you fix it) | Yes (reconverges) |
| Scale | Tiny networks, stub links, default routes | Anything that changes |
| Predictability | Total | Emergent (and occasionally catastrophic — §8.4) |

Dynamic routing splits into **interior** (within one administrative domain / Autonomous System) and **exterior** (between ASes).

### 8.1 IGPs — interior gateway protocols

**OSPF (Open Shortest Path First, RFC 2328) — link-state.** Every router floods **Link-State Advertisements (LSAs)** describing its directly-connected links and costs. Every router thus builds an *identical, complete map* of the area's topology, then runs **Dijkstra's shortest-path** algorithm locally to compute its own routes.
- Fast, loop-free convergence; metric = cost (typically inverse of bandwidth).
- Scales via **areas** (hierarchy) to bound the flooding and SPF cost. The standard enterprise/DC IGP.

**RIP (Routing Information Protocol) — distance-vector.** Each router tells its neighbors "here are the distances (hop counts) I know," and they add one and pass it on. No router sees the whole map — it trusts its neighbors' summaries ("routing by rumor").
- Simple, but slow to converge and prone to **count-to-infinity** loops (mitigated by split horizon, poison reverse, and a max metric of **16 = unreachable**, which caps network diameter at 15 hops).
- Largely obsolete; know it as the canonical distance-vector contrast to OSPF's link-state.

```
 Link-state (OSPF):   everyone has the WHOLE map → compute shortest paths locally (Dijkstra)
 Distance-vector(RIP):everyone knows only DISTANCES from neighbors → "routing by rumor"
```

### 8.2 BGP — the exterior gateway protocol (RFC 4271)

**BGP-4 is the routing protocol of the Internet.** It runs *between* Autonomous Systems — networks under one administrative control, each with a globally unique **ASN** (e.g., AS13335 = Cloudflare, AS32934 = Meta). BGP is a **path-vector** protocol: routes carry the full **AS_PATH** — the list of ASes a prefix's announcement traversed.

```
   AS 100 (you)      AS 200 (transit)      AS 300 (Cloudflare)
   advertises  ──────►  prepends 200  ──────►  prepends 300
   10.0.0.0/22         AS_PATH: [100]          others see: AS_PATH [300 200 100]
                                               to reach 10.0.0.0/22
```

- **Path-vector, not shortest-metric:** BGP doesn't pick "fewest hops." It chooses by a long preference ladder — **local preference** (your policy: prefer this peer), then shortest **AS_PATH**, then MED, etc. **Routing is policy, not just topology** — that's the whole point of BGP. ASes route by *business relationship* (customer vs. peer vs. transit), not by physics.
- **AS_PATH also prevents loops:** if a router sees its own ASN already in the path, it rejects the route.
- **Route propagation is gossip across the whole Internet:** when you announce a prefix, your neighbors tell their neighbors, and it ripples to the global table (~950K+ IPv4 routes today). Convergence after a big change can take seconds to minutes.

### 8.3 Why BGP outages take down the Internet
BGP is built on **implicit trust** — historically a router *believed* whatever its neighbors announced. There is no built-in authentication that an AS is *entitled* to announce a prefix. Combine that with the **longest-prefix-match** rule (§7, a more-specific announcement always wins) and global propagation, and a single mistake propagates worldwide in seconds. Two failure modes:

1. **Route leak / hijack (announce the wrong thing):** an AS announces prefixes it shouldn't — accidentally or maliciously. Because LPM prefers more-specifics and BGP trusts neighbors, traffic for those prefixes is *globally redirected* to the wrong AS.
2. **Withdrawal (announce nothing):** an AS *stops* announcing its own prefixes. The rest of the Internet's routers delete those routes — and the network *vanishes*. There is now no path to it.

### 8.4 The canonical real-world post-mortems (cite these)
- **Facebook/Meta, Oct 4 2021 (~6 hours global outage).** A faulty maintenance command on the backbone caused Meta's routers to **withdraw the BGP routes** to their data centers — including the prefixes serving Facebook's **DNS** authoritative servers. From the Internet's view, Meta's networks *disappeared*. The cascade was vicious: DNS being unreachable meant *everything* (Facebook, Instagram, WhatsApp) failed to resolve, **and** engineers couldn't remotely access the management tooling (it depended on the same DNS/network) to fix it — they reportedly needed physical data-center access. A textbook lesson that **DNS and out-of-band management must not depend on the very network they manage.**
- **Cloudflare, July 2 2019.** A bad WAF regex (`(?:(?:\"|'|\]|...)*)*`) caused catastrophic CPU backtracking and a global outage — a reminder that "BGP-adjacent" edge outages aren't always BGP; sometimes it's the data plane software. Cloudflare's **June 2019** Verizon/AS701 **route leak** (a small ISP leaked a full table optimizer's routes, Verizon propagated them, and traffic for Cloudflare/Amazon/etc. blackholed) is the cleaner BGP example.
- **Pakistan/YouTube 2008** and **AS7007 (1997)** are the classic teaching hijacks: a more-specific announcement (LPM) globally rerouted/blackholed a major service.

**The mitigations** the industry has standardized on: **RPKI** (Resource Public Key Infrastructure) cryptographically signs "AS X is authorized to originate prefix P" so routers can drop **Route Origin Validation (ROV)** failures; **prefix filters / max-prefix limits** on peers; **MANRS** best practices. RPKI deployment is now broad (Cloudflare's "Is BGP safe yet?" pushed it) but not universal — the trust problem isn't fully solved.

---

## 9. Traceroute & the TTL trick

`traceroute` (Unix; `tracert` on Windows) maps the routers on the path to a destination by **abusing the TTL field and ICMP Time Exceeded** (§2, §6):

```
 Probe 1: send a packet with TTL = 1.
          First router decrements TTL 1→0, DROPS it, returns ICMP Time Exceeded (type 11).
          → its source IP is hop 1.
 Probe 2: send with TTL = 2.
          Router 1: 2→1 (forward). Router 2: 1→0 → ICMP Time Exceeded.
          → hop 2.
 Probe N: TTL = N reveals the Nth router.
 ...continue until a probe reaches the destination, which replies differently
    (ICMP Port Unreachable for UDP probes, or Echo Reply for ICMP probes) → done.
```

```
 $ traceroute -n 8.8.8.8
  1  10.0.1.1      0.5 ms      <- TTL=1 expired here (your gateway)
  2  100.64.0.1    8.2 ms      <- TTL=2 (ISP edge / CGNAT)
  3  203.0.113.9  12.1 ms
  ...
  9  8.8.8.8      18.7 ms      <- destination replied; trace complete
```

Caveats a staff engineer must know:
- **Three probes per hop** (the three RTT columns) because each may take a different ECMP path (§10) — that's why hops sometimes show different IPs across columns.
- **`* * *`** means a router didn't reply (ICMP rate-limited or filtered) — *not* necessarily that the path is broken; the packet often still gets through.
- **Asymmetric routing:** traceroute shows the *forward* path's routers; the return path can differ entirely (BGP policy). RTTs include the return trip.

---

## 10. ECMP — Equal-Cost Multi-Path

When the routing table has **multiple equal-cost next hops** to the same prefix (the normal case in a leaf-spine fabric — see [02](02_link_layer_switching.md) §11), the router spreads traffic across all of them.

```
   Leaf  ──► Spine 1 ─┐
        ──► Spine 2 ─┼──► all equal-cost to the destination leaf
        ──► Spine 3 ─┘
   Hash(5-tuple: src IP, dst IP, src port, dst port, proto) → pick a next hop.
```

- **Per-flow hashing (5-tuple)**, not per-packet — so a given TCP connection always takes *one* path → **no reordering** (which would wreck TCP). The trade-off (same as LACP, [02](02_link_layer_switching.md) §10): one flow can't exceed one link; ECMP gives aggregate, not single-flow, bandwidth. "Elephant flows" can hash-collide onto one link and create hotspots (flowlet/adaptive load balancing addresses this).
- This is what lets the data-center fabric use **all** links simultaneously (vs. STP blocking half at L2). All-active multipath is the entire reason DCs route instead of switch at the fabric.

---

## 11. Anycast

**Anycast** announces the *same IP prefix from many physically distinct locations* (multiple sites, each its own BGP origin). The routing system, doing its normal job, delivers each client to the **topologically nearest** instance — "nearest" by BGP path, which usually correlates with lowest latency.

```
 203.0.113.53/32 announced from London, NYC, Tokyo, Frankfurt (all the same IP).
 A user in Paris   → BGP routes to London/Frankfurt instance.
 A user in Osaka   → BGP routes to Tokyo instance.
 No DNS games, no client logic — the ROUTING system load-balances geographically.
```

Where it's load-bearing:
- **DNS root servers:** there are 13 root *names* (a–m) but **far more than 13 physical servers** — each is anycast to hundreds of sites worldwide. That's how 13 "servers" handle global DNS and survive DDoS (attack traffic is absorbed locally at the nearest site, not concentrated).
- **CDNs (Cloudflare, Google, Fastly):** anycast a small set of IPs from every edge POP; users hit the closest POP automatically. It also provides **DDoS absorption** — a volumetric attack is split across all POPs by geography instead of crushing one site.
- **Trade-off:** anycast is great for **stateless/short** request-response (DNS, HTTP with TLS-resumption-tolerant edges). Long-lived stateful connections can be disrupted if BGP reconverges mid-flow and flips a client to a different site (which has no connection state) — so anycast TCP needs care, which is part of why QUIC's connection IDs (decoupling the connection from the 5-tuple) help.

---

## 12. MPLS (brief)

**MPLS (Multi-Protocol Label Switching)** sits "layer 2.5": routers prepend a short **label** (a 20-bit value in a 32-bit shim header) and forward by **exact label match** instead of longest-prefix IP lookup. The first router (ingress LER) classifies the packet into a **FEC** and pushes a label; interior routers (LSRs) swap labels along a pre-established **Label-Switched Path (LSP)**; the egress router pops it.

Why it exists:
- **Forward by fixed-length label** (fast, simple) and, more importantly, **engineer traffic onto specific paths** (Traffic Engineering / RSVP-TE) independent of the IGP shortest path — e.g., steer a flow away from a congested link, or build guaranteed-bandwidth tunnels.
- **VPNs:** label stacking carries customer traffic isolated across a provider core (L3VPN/BGP-MPLS, L2VPN/VPLS). This is how carriers sell "private WAN" over shared infrastructure.
- In the cloud era it's increasingly displaced by **SD-WAN** and VXLAN/EVPN overlays, but it still underpins most carrier/WAN backbones.

---

## 13. Working examples

### 13.1 A CIDR / subnet calculator (pure Python, runs)

```python
#!/usr/bin/env python3
"""
subnet_calc.py — CIDR subnet calculator using only the stdlib `ipaddress`.
Run: python subnet_calc.py
Prints network, broadcast, host range, usable count, and a /N split.
"""
import ipaddress

def describe(cidr: str) -> dict:
    net = ipaddress.ip_network(cidr, strict=False)   # strict=False tolerates host bits
    hosts = list(net.hosts())          # excludes network & broadcast for IPv4 (except /31,/32)
    return {
        "input":        cidr,
        "network":      str(net.network_address),
        "netmask":      str(net.netmask),
        "prefix":       f"/{net.prefixlen}",
        "broadcast":    str(net.broadcast_address) if net.version == 4 else "n/a (IPv6)",
        "total_addrs":  net.num_addresses,
        "usable_hosts": len(hosts),
        "first_host":   str(hosts[0]) if hosts else "n/a",
        "last_host":    str(hosts[-1]) if hosts else "n/a",
        "is_private":   net.is_private,   # RFC 1918 / RFC 4193 check
    }

def split_into(cidr: str, num_subnets: int):
    net = ipaddress.ip_network(cidr, strict=False)
    # bits needed to make >= num_subnets pieces
    new_prefix = net.prefixlen + (num_subnets - 1).bit_length()
    return list(net.subnets(new_prefix=new_prefix))

def membership(cidr: str, ip: str) -> bool:
    return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)

if __name__ == "__main__":
    for cidr in ("192.168.1.0/24", "10.0.5.0/26", "172.16.0.0/12", "203.0.113.10/31"):
        print(f"\n=== {cidr} ===")
        for k, v in describe(cidr).items():
            print(f"  {k:13} {v}")

    print("\n=== split 192.168.1.0/24 into 4 subnets ===")
    for s in split_into("192.168.1.0/24", 4):
        h = list(s.hosts())
        print(f"  {str(s):18} hosts {h[0]}–{h[-1]}  broadcast {s.broadcast_address}")

    print("\n=== membership ===")
    print("  10.1.2.50 in 10.1.0.0/16 ?", membership("10.1.0.0/16", "10.1.2.50"))
    print("  10.2.0.1  in 10.1.0.0/16 ?", membership("10.1.0.0/16", "10.2.0.1"))

    # sanity assertions (this file is also a self-test)
    d = describe("10.0.5.0/26")
    assert d["usable_hosts"] == 62 and d["broadcast"] == "10.0.5.63"
    assert len(split_into("192.168.1.0/24", 4)) == 4
    assert membership("10.1.0.0/16", "10.1.2.50") is True
    print("\nall assertions passed.")
```

### 13.2 Longest-prefix-match router lookup (pure Python, runs)

```python
#!/usr/bin/env python3
"""
lpm_router.py — a routing table with longest-prefix-match forwarding (§7).
Run: python lpm_router.py
"""
import ipaddress

class RoutingTable:
    def __init__(self):
        self.routes = []   # list of (network, next_hop, iface)

    def add(self, cidr: str, next_hop: str, iface: str):
        self.routes.append((ipaddress.ip_network(cidr), next_hop, iface))

    def lookup(self, dst_ip: str):
        """Return (matched_prefix, next_hop, iface) using longest-prefix match."""
        addr = ipaddress.ip_address(dst_ip)
        best = None
        for net, next_hop, iface in self.routes:
            if addr in net:
                # most specific = longest prefix wins
                if best is None or net.prefixlen > best[0].prefixlen:
                    best = (net, next_hop, iface)
        if best is None:
            return None
        net, next_hop, iface = best
        return (str(net), next_hop, iface)

if __name__ == "__main__":
    rt = RoutingTable()
    rt.add("0.0.0.0/0",   "203.0.113.1", "eth0")   # default route (least specific)
    rt.add("10.1.0.0/16", "10.0.0.2",    "eth1")   # broad
    rt.add("10.1.2.0/24", "10.0.0.1",    "eth1")   # specific
    rt.add("10.1.2.128/25","10.0.0.9",   "eth2")   # MORE specific (overrides the /24)

    tests = {
        "10.1.2.50":  ("10.1.2.0/24",   "10.0.0.1"),    # /24 beats /16 and /0
        "10.1.2.200": ("10.1.2.128/25", "10.0.0.9"),    # /25 beats /24 (longest wins)
        "10.1.9.7":   ("10.1.0.0/16",   "10.0.0.2"),    # only /16 (and /0) match
        "8.8.8.8":    ("0.0.0.0/0",     "203.0.113.1"), # nothing specific → default
    }
    for dst, (exp_net, exp_hop) in tests.items():
        net, hop, iface = rt.lookup(dst)
        ok = (net == exp_net and hop == exp_hop)
        print(f"  dst {dst:12} -> {net:15} via {hop:13} ({iface})  {'OK' if ok else 'FAIL'}")
        assert ok, f"LPM mismatch for {dst}"
    print("\nlongest-prefix match verified.")
```

### 13.3 Routing & ICMP from the CLI

```bash
# Which next hop will the kernel pick for a destination? (longest-prefix match in action)
ip route get 8.8.8.8
# 8.8.8.8 via 203.0.113.1 dev eth0 src 203.0.113.5    <- default route chosen

ip route                                  # full table; "default via ..." is 0.0.0.0/0
ip -6 route                               # the IPv6 table (note fe80::/64 link-local + ::/0)

# Subnet sanity from the shell:
ipcalc 192.168.1.0/26                     # if installed: network/broadcast/host range

# ICMP echo (ping) and the TTL-based path trace (traceroute, §9):
ping -c3 1.1.1.1
traceroute -n 1.1.1.1                     # each hop = a router that returned ICMP Time Exceeded
mtr -n 1.1.1.1                            # continuous traceroute+ping (loss per hop)

# Look up the AS / BGP origin of a prefix (whose network is this?):
whois -h whois.cymru.com " -v 1.1.1.1"    # returns ASN, AS name, prefix, country
dig +short CHAOS TXT hostname.bind @1.1.1.1   # which anycast instance answered (varies by POP)
```

---

## 14. Advanced: BGP security & operations, and segment routing

### BGP is built on trust — and that's the problem

BGP ([§8](#8-routing-protocols)) has no built-in authentication of *who owns a prefix*.
Any AS can announce any prefix; neighbors largely believe it. Two failure classes
follow:

- **Route hijack** — an AS announces a prefix it doesn't own, attracting that traffic
  (accidental, as in the 2008 Pakistan/YouTube incident, or malicious for
  interception). A **more-specific** hijack (longer prefix) wins via longest-prefix
  match ([§7](#7-the-routing-table--longest-prefix-match)) regardless of legitimacy.
- **Route leak** — an AS re-advertises routes it shouldn't (e.g. propagating a
  provider's routes to another provider), pulling in traffic it can't carry → outage.

**Defenses:** **RPKI** (Resource Public Key Infrastructure) cryptographically binds a
prefix to its owning AS (ROAs), and **ROV** (Route Origin Validation) lets routers
drop invalid origins; **prefix filters**, **max-prefix limits**, and **RFC 9234 roles
(ASPA)** stop leaks; **MANRS** is the industry norms package. BGP sessions use TCP-MD5/
TCP-AO and GTSM (TTL=255 check) to resist spoofing.

### The self-inflicted BGP outage (Facebook, Oct 2021)

A routine config change withdrew the BGP routes to Facebook's DNS servers; with the
prefixes gone, the **entire estate became unreachable globally** — and because
internal tooling and even badge access depended on the same network, recovery was
hampered. The lesson is staff-canon: **BGP withdrawal has a global blast radius**,
control-plane changes need staged rollout + automatic rollback, and out-of-band
recovery access must not depend on the network it manages.

### Operational BGP knobs and ECMP hashing

In practice you steer traffic with **local-preference** (outbound), **AS-path
prepending** and **MED** (inbound), and **communities** (tags that signal policy to
neighbors). Across equal-cost paths, **ECMP** ([§10](#10-ecmp--equal-cost-multi-path))
hashes the 5-tuple to pick a path — keeping a flow on one path (no reordering) but
risking **elephant-flow imbalance** (one big flow pins a link); **flowlet switching**
mitigates it by rehashing at packet-train gaps.

### Segment Routing (SR / SRv6) — source-routed paths

**Segment Routing** encodes the path as a stack of "segments" in the packet header
(MPLS labels for SR-MPLS, or IPv6 addresses for **SRv6**), so the **source** picks the
route and the core stays stateless — no per-flow state in the network. It's the modern
successor to MPLS-TE ([§12](#12-mpls-brief)) for traffic engineering and fast reroute,
and SRv6 folds it into the IPv6 data plane.

---

## Key Takeaways

1. **IP's contract is deliberately minimal:** best-effort, connectionless, stateless forwarding with global addressing. That minimalism is *why* the Internet scales and survives router loss — and why reliability/ordering live in TCP, not IP.
2. **An IPv4 address is prefix + host split by a `/n` mask (CIDR).** Usable hosts = 2^(32−prefix) − 2 (network + broadcast excluded; `/31` is the point-to-point exception). Block size = 256 − mask-octet. CIDR aggregation (supernetting) is what keeps the global table from exploding.
3. **RFC 1918 private space + NAT/PAT** stretched IPv4 past exhaustion by sharing one public IP across many hosts via source-port rewriting — at the cost of breaking end-to-end reachability (P2P needs STUN/TURN/hole-punching). NAT is not a firewall.
4. **IPv6 (128-bit) is the real fix:** vast address space, no in-transit fragmentation, no broadcast (multicast + NDP instead of ARP), and SLAAC for serverless autoconfig on `/64` subnets.
5. **Forwarding = longest-prefix match:** the most-specific matching route wins; `0.0.0.0/0` is the last-resort default. LPM is why CIDR composes — and why a more-specific BGP announcement can hijack traffic.
6. **IGPs build intra-AS routes:** OSPF (link-state, everyone has the map, runs Dijkstra, fast/loop-free) vs. RIP (distance-vector, "routing by rumor," slow, count-to-infinity). **BGP is the inter-AS protocol: path-vector, policy-driven (AS_PATH, local-pref), built on trust.**
7. **BGP outages are existential because of trust + LPM + global propagation.** Meta Oct 2021 *withdrew* its own routes (including DNS) and vanished from the Internet for hours, locked out of its own tooling — the lesson is that DNS and out-of-band management must not depend on the network they manage. RPKI/ROV + prefix filters are the standardized defenses.
8. **Traceroute weaponizes TTL + ICMP Time Exceeded** to enumerate path routers; `* * *` is usually filtered ICMP, not a break; paths are often asymmetric.
9. **ECMP and anycast both let the routing system load-balance:** ECMP spreads flows (per-5-tuple hash, no single-flow speedup) across equal-cost paths so the DC fabric uses every link; anycast announces one IP from many sites so routing delivers each client to the nearest instance — the backbone of DNS root resilience and CDN edge + DDoS absorption.
10. **MPLS** forwards by fixed label for traffic engineering and provider VPNs — "layer 2.5," now partly displaced by SD-WAN and VXLAN/EVPN overlays but still core to carrier WANs.

> Read prior: [01 — Network Models & Layering](01_network_models_layering.md) for the encapsulation/packet-walk foundation, and [02 — Link Layer & Switching](02_link_layer_switching.md) for the L2 fabric (leaf-spine, VXLAN) that this routing layer drives.
