# 02 — Link Layer & Switching

> **Audience:** staff/principal. You can read a routing table. This doc is about the layer *below* IP — how frames actually get from one NIC to the next across a LAN or data-center fabric, why switches are not routers, and the protocols (ARP, STP, 802.1Q, LACP, VXLAN) that keep L2 from melting down.
>
> **Primary sources:** Kurose & Ross ch. 6; Tanenbaum & Wetherall ch. 4 (the MAC sublayer); Stevens, *TCP/IP Illustrated Vol. 1* ch. 4 (ARP); IEEE 802.3 (Ethernet), 802.1Q (VLANs), 802.1D/802.1w (STP/RSTP), 802.1AX (LACP); RFC 826 (ARP), RFC 7348 (VXLAN); Cilium/Isovalent and Cloudflare engineering blogs (data-center networking, eBPF datapath).

---

## 1. What the link layer is for

The network layer (IP) gives you **host-to-host** addressing across the whole Internet. But IP packets don't travel over "the Internet" directly — they travel over a *sequence of individual links*: an Ethernet segment, a Wi-Fi cell, a fiber span. The **link layer's job is one hop**: get a frame from this NIC to the next NIC *on the same physical/logical network*.

Two defining properties follow from "one hop":

1. **Link-layer addresses (MAC) have local scope only.** A MAC address is meaningful only on the link it's attached to; it is *rewritten at every router* (see [01](01_network_models_layering.md) §8). Contrast with an IP address, which is meaningful end-to-end.
2. **The link layer is medium-specific.** Ethernet, Wi-Fi, and PPP each have their own framing, addressing, and error-detection. IP rides on top of all of them uniformly (the hourglass waist).

This doc is mostly about **Ethernet** (IEEE 802.3) and **switched Ethernet**, because that's what wired LANs and data centers run.

---

## 2. Ethernet frames & MAC addresses

### 2.1 The frame format

```
 ┌──────────┬─────────┬────────┬────────┬──────────┬──────────────────────┬──────┐
 │ Preamble │  SFD    │ Dst    │ Src    │ Type/Len │ Payload (46–1500)    │ FCS  │
 │  7 bytes │ 1 byte  │ MAC 6  │ MAC 6  │  2 bytes │ (IP packet, ARP, …)  │  4   │
 └──────────┴─────────┴────────┴────────┴──────────┴──────────────────────┴──────┘
   └─ physical-layer sync ─┘   └────────── what the NIC/switch sees ─────────────┘

  EtherType (when ≥ 0x0600):  0x0800 IPv4   0x0806 ARP   0x86DD IPv6   0x8100 802.1Q VLAN
  Min payload 46 bytes (padded if shorter) → minimum frame 64 bytes (see CSMA/CD, §3).
  Max payload 1500 bytes = the Ethernet MTU. Jumbo frames raise this to ~9000 (§9).
  FCS = CRC-32 over dst..payload; corrupt frames are silently dropped (end-to-end retransmits).
```

The **Preamble + SFD** are L1 clocking and aren't delivered up. The fields a switch acts on are **Dst MAC**, **Src MAC**, and (for VLAN-aware switches) the optional **802.1Q tag**.

### 2.2 MAC address structure

A MAC address is 48 bits, written as six hex octets: `00:1A:2B:3C:4D:5E`.

```
   00:1A:2B : 3C:4D:5E
   └──────┘   └──────┘
    OUI         NIC-specific
   (vendor)    (per-device)

 Two special bits in the first octet:
   bit 0 (I/G): 0 = unicast,    1 = multicast/broadcast
   bit 1 (U/L): 0 = globally unique (burned-in by vendor), 1 = locally administered

 Broadcast:  ff:ff:ff:ff:ff:ff   (every NIC on the segment receives & processes it)
 Multicast:  01:00:5e:..  (IPv4 multicast),  33:33:..  (IPv6 multicast/ND)
```

- The first 24 bits (**OUI**, Organizationally Unique Identifier) are assigned to vendors by the IEEE → you can often identify a NIC's manufacturer from its MAC.
- The U/L bit matters in modern privacy contexts: phones use **MAC randomization** (locally administered, U/L=1) per-SSID to defeat Wi-Fi tracking.

---

## 3. CSMA/CD history & why switched Ethernet won

Original Ethernet (1970s–90s) was a **shared bus**: every host tapped the same coax (10BASE5/10BASE2) or shared a **hub**. Only one host could transmit at a time across the whole medium → a single **collision domain**.

To arbitrate the shared medium, classic Ethernet used **CSMA/CD** (Carrier Sense Multiple Access with Collision Detection):

```
 CSMA/CD algorithm (half-duplex shared medium):
   1. Carrier Sense:  listen; if the wire is busy, wait.
   2. Transmit while continuing to listen.
   3. Collision Detect: if you hear your own signal garbled, a collision happened.
   4. Jam: send a 32-bit jam signal so everyone notices.
   5. Backoff: wait a random time (binary exponential backoff), then retry.
```

The **64-byte minimum frame** comes directly from CSMA/CD: a frame must be long enough that the sender is *still transmitting* when the farthest collision would reflect back, so it can detect the collision. Frame too short + cable too long = undetected collisions = corruption. This coupled frame size, cable length, and bit rate — a real constraint of the shared-bus era.

### 3.1 Why this is now history

**Switches + full-duplex killed CSMA/CD.** A modern switch gives each port its own dedicated, full-duplex link to one host:
- Each port is its **own collision domain** → with one host per port, **there are no collisions at all**. CSMA/CD is disabled on full-duplex links.
- Host and switch can transmit *simultaneously* (full-duplex) — double the effective bandwidth, zero contention.

> Modern Ethernet (1G/10G/100G, switched, full-duplex) keeps the *frame format* of classic Ethernet but has thrown out the *access method*. CSMA/CD survives only on the exam and in legacy half-duplex corners. (Wi-Fi still needs **CSMA/CA** — collision *avoidance* — because radios can't reliably detect collisions while transmitting.)

---

## 4. Hubs vs switches vs bridges

| Device | OSI layer | Forwarding decision | Collision domains | Broadcast domains | Verdict |
|---|---|---|---|---|---|
| **Hub (repeater)** | L1 | None — repeats every bit to *every* port | **1** (all ports share) | 1 | Obsolete. Dumb electrical repeater. |
| **Bridge** | L2 | By dst MAC (software, few ports) | 1 per port | 1 | The conceptual ancestor of the switch. |
| **Switch** | L2 | By dst MAC (hardware ASIC, many ports) | **1 per port** | 1 (per VLAN) | The default LAN device. |
| **Router** | L3 | By dst IP (longest-prefix match) | 1 per port | **1 per port** (terminates broadcast) | Connects networks; see [03](03_network_layer_routing.md). |

The progression is about *forwarding intelligence*:
- A **hub** is a multi-port repeater — it understands nothing, just blasts every incoming bit out every other port. Everyone shares one collision domain; throughput collapses under load.
- A **bridge** was the first device to *learn* which hosts are on which side and forward selectively — but it was slow (software) and small. A **switch is a bridge in hardware** with many ports and line-rate forwarding via ASICs/TCAMs.
- A **router** operates one layer up — it forwards by IP, connects *different* networks, and **stops broadcasts** (a broadcast does not cross a router). This is the key difference: a switch keeps you on one IP network; a router moves you between them.

---

## 5. The MAC address table & learning

A switch forwards frames by consulting its **MAC address table** (a.k.a. CAM table, forwarding table). The beautiful part: **nobody configures it. The switch learns it by watching traffic.**

### 5.1 Backward learning algorithm

```
 On receiving a frame on port P with src MAC = S, dst MAC = D:

   1. LEARN:   table[S] = (port P, timestamp now)        # source teaches us where S lives
   2. LOOK UP D:
        if D is broadcast/multicast  → FLOOD (send out all ports except P)
        elif D in table              → FORWARD out table[D].port only   (unicast, efficient)
        else  (unknown unicast)      → FLOOD (send out all ports except P)
   3. AGE OUT: entries older than the aging timer (default ~300s) are evicted.
```

```
 Example: A (port 1) sends to B (port 3), table initially empty.
 ┌─────────────────────────────────────────────────────────────┐
 │  t0: A→B frame arrives on port 1.                            │
 │      LEARN  A → port 1.   D=B unknown → FLOOD to ports 2,3,4 │
 │  t1: B replies B→A on port 3.                                │
 │      LEARN  B → port 3.   D=A known(port1) → FORWARD port 1  │  (no more flooding!)
 │                                                              │
 │   MAC table:   A → port 1                                    │
 │                B → port 3                                    │
 └─────────────────────────────────────────────────────────────┘
```

Key properties:
- **Self-configuring**: plug-and-play, the reason Ethernet LANs "just work."
- **Flooding on unknown unicast** is the safety net — a frame is never lost just because the switch hasn't learned the destination yet; it's flooded and the reply teaches the switch.
- **CAM table overflow attack (MAC flooding):** an attacker spams thousands of bogus source MACs to fill the finite CAM table. When full, the switch can no longer learn → it floods *all* traffic → the attacker now sniffs everyone's frames (turning the switch into a hub). Mitigation: **port security** (limit MACs per port).

---

## 6. Broadcast vs collision domains

These two terms confuse people; pin them down precisely.

| | Collision domain | Broadcast domain |
|---|---|---|
| Definition | Set of devices that *contend* for the same medium (a collision here affects all) | Set of devices a single broadcast frame reaches |
| Bounded by | Each **switch port** (and router port) | Each **router port** (and each **VLAN**) |
| In switched Ethernet | One per port → effectively gone (full-duplex) | The whole LAN/VLAN |

```
   ┌──────── one BROADCAST domain (VLAN 10) ─────────┐
   │   ┌─Switch──┐                                   │
   │   │ p1 p2 p3│   each port = its own COLLISION   │
   │   └─┬──┬──┬─┘   domain (1 host each, full-dup)  │
   │     A  B  C                                     │
   └─────────────────────────────────────────────────┘
            │
        ┌───┴────┐
        │ ROUTER │   ← broadcasts STOP here; a router separates broadcast domains
        └───┬────┘
   ┌────────┴───────── different broadcast domain (different subnet) ─────────┐
```

**Why broadcast domains matter at scale:** every host in a broadcast domain processes every broadcast (ARP, DHCP, mDNS). A flat L2 network with thousands of hosts drowns in **broadcast traffic** — every ARP storms every NIC's CPU. This is the *primary reason* you segment large networks into smaller broadcast domains using **routers and VLANs**, and the reason large data centers prefer **routed (L3) fabrics** over giant flat L2 (§11).

---

## 7. ARP — Address Resolution Protocol (RFC 826)

ARP answers the question every host must ask before sending a frame: **"I have an IP address for my next hop; what's its MAC address?"** It bridges L3 (IP) and L2 (MAC), which is why it's called "layer 2.5."

### 7.1 The exchange

```
 Host A (10.0.1.5, aa:aa:aa:aa:aa:aa) wants to send to 10.0.1.9 on the same subnet:

 1. ARP cache miss → A BROADCASTS an ARP Request:
      Eth: dst=ff:ff:ff:ff:ff:ff  src=aa:..   type=0x0806
      ARP: "Who has 10.0.1.9?  Tell 10.0.1.5 (aa:aa:..)"
            → every host on the segment receives it.

 2. Host C (10.0.1.9, cc:cc:..) recognizes its own IP and UNICASTS an ARP Reply:
      Eth: dst=aa:aa:..  src=cc:cc:..
      ARP: "10.0.1.9 is at cc:cc:cc:cc:cc:cc"

 3. A caches  10.0.1.9 → cc:cc:..  (TTL ~minutes) and sends its frame.
    (C also learned A's mapping from the request — ARP is opportunistic.)
```

The ARP packet itself (28 bytes for IPv4-over-Ethernet) carries hardware type (1 = Ethernet), protocol type (0x0800 = IPv4), opcode (1 = request, 2 = reply), and both sender/target hardware+protocol addresses.

> **Crucial scope rule:** ARP is only ever used for the **next hop on the local subnet**. If the destination IP is *remote*, the host ARPs for its **default gateway's** MAC, not the destination's (see [01](01_network_models_layering.md) §8). A host never ARPs for an off-subnet IP.

> **IPv6 has no ARP.** It uses **NDP (Neighbor Discovery Protocol, RFC 4861)** over ICMPv6, with Neighbor Solicitation/Advertisement messages sent to *solicited-node multicast* groups instead of broadcast — more efficient (only the relevant host's NIC is interrupted) and more extensible.

### 7.2 ARP spoofing / poisoning

ARP has **no authentication** — any host can claim any IP. An attacker on the LAN sends **gratuitous/unsolicited ARP replies**: "10.0.1.1 (the gateway) is at *my* MAC." Victims update their caches and now send all gateway-bound traffic to the attacker → **man-in-the-middle**. This is the foundation of tools like `ettercap` and is a *trivial* attack on an unprotected LAN.

Defenses:
- **Dynamic ARP Inspection (DAI)** on switches: validate ARP replies against the DHCP snooping table; drop forgeries.
- **Static ARP entries** for critical hosts (the gateway), or **802.1X** port authentication.
- **Encryption (TLS)** doesn't *prevent* the MITM but makes the intercepted traffic useless — defense in depth.

---

## 8. VLANs & 802.1Q

A **VLAN (Virtual LAN)** lets one physical switch fabric host *multiple, isolated* broadcast domains. Ports tagged VLAN 10 form one logical LAN; ports tagged VLAN 20 form another. Hosts in different VLANs cannot reach each other at L2 — **traffic between VLANs must be routed** (by a router or an L3 switch / "router-on-a-stick").

Why VLANs exist:
- **Broadcast containment** (§6) without buying more physical switches.
- **Segmentation/security**: put guest Wi-Fi, IoT, and prod servers in separate VLANs on the same hardware.
- **Mobility**: a host keeps its VLAN/subnet regardless of which switch port it plugs into.

### 8.1 802.1Q tagging

To carry multiple VLANs over a single inter-switch link, switches insert a 4-byte **802.1Q tag** into the Ethernet frame:

```
 Untagged frame:
 ┌────────┬────────┬──────────┬─────────────┬─────┐
 │ Dst    │ Src    │ EtherType│ Payload     │ FCS │
 └────────┴────────┴──────────┴─────────────┴─────┘

 802.1Q-tagged frame (inserted after Src MAC):
 ┌────────┬────────┬═════════════════════════┬──────────┬─────────────┬─────┐
 │ Dst    │ Src    ║ TPID 0x8100 │ TCI        ║ EtherType│ Payload     │ FCS │
 └────────┴────────┴═════════════════════════┴──────────┴─────────────┴─────┘
                      TCI = PCP(3 bits, QoS) │ DEI(1) │ VLAN ID (12 bits)
                                                         └─ 0–4095, so 4094 usable VLANs
```

- **TPID = 0x8100** marks the frame as tagged.
- **VLAN ID** is 12 bits → **4094 usable VLANs** (0 and 4095 reserved). This 12-bit limit is a real scaling ceiling in large multi-tenant clouds — and the reason **VXLAN** (§12) exists (24-bit IDs → 16M segments).
- **PCP** carries 802.1p priority (QoS class).
- **Q-in-Q (802.1ad)** stacks two tags (provider + customer) for carrier networks.

### 8.2 Access vs trunk ports

```
   ┌───────── Switch 1 ─────────┐         ┌───────── Switch 2 ─────────┐
   │ p1(access VLAN10)─ Host A  │         │  Host C ─(access VLAN10)p1 │
   │ p2(access VLAN20)─ Host B  │ TRUNK   │  Host D ─(access VLAN20)p2 │
   │ p24 ════════════════════════════════════════════════════ p24     │
   │     (carries VLAN10+20,    │  802.1Q │  carries both, tagged)     │
   │      frames tagged)        │  tagged │                            │
   └────────────────────────────┘         └────────────────────────────┘
```

- **Access port:** belongs to exactly **one VLAN**. Frames to/from the host are **untagged** — the host is unaware of VLANs; the switch adds/removes the tag on ingress/egress. This is what an end device (laptop, server NIC) normally connects to.
- **Trunk port:** carries **multiple VLANs** between switches (or to a router/hypervisor). Frames are **802.1Q-tagged** so the other end knows which VLAN each frame belongs to. The **native VLAN** is the one VLAN carried untagged on a trunk (a legacy/compat feature, and a security footgun — "VLAN hopping" via double-tagging).

---

## 9. Spanning Tree Protocol — loops & how to survive them

L2 has a fatal flaw that L3 does not: **Ethernet frames have no TTL.** An IP packet with a routing loop dies when TTL hits 0. An Ethernet frame in an L2 loop circulates **forever**, and worse — broadcasts get *flooded*, *re-flooded*, and *multiplied* at every switch:

```
   ┌──Switch A──┐════════┌──Switch B──┐
   │            │        │            │
   │            │════════│            │   ← redundant link creates a LOOP
   └────────────┘        └────────────┘

 A broadcast enters → A floods to B (both links) → B floods back to A (both links)
 → A floods again → ... exponential frame multiplication = BROADCAST STORM.
 The CAM tables thrash (same MAC seen on flapping ports). The LAN dies in seconds.
```

But you *want* redundant links for fault tolerance. The resolution: **STP (Spanning Tree Protocol, IEEE 802.1D)** logically disables links to make the physical mesh into a loop-free **tree**, while keeping the disabled links as hot standby.

### 9.1 How STP builds the tree

```
 1. Elect a ROOT BRIDGE: lowest Bridge ID (priority + MAC) wins.
 2. Each non-root switch finds its ROOT PORT: the port with the lowest cost path to root.
 3. Each segment elects a DESIGNATED PORT: the port (on whichever switch) closest to root.
 4. All remaining redundant ports go to BLOCKING state (carry BPDUs only, no data).
    → result: exactly one active path between any two switches = a spanning tree.

 Switches exchange BPDUs (Bridge Protocol Data Units) to elect/maintain the tree.
 Classic 802.1D convergence: ~30–50s (listening 15s + learning 15s) — painfully slow.
```

- **RSTP (802.1w)** is the modern default: sub-second convergence via explicit handshakes and edge-port detection. **MSTP (802.1s)** maps groups of VLANs to separate trees for load distribution.
- **PortFast / edge ports:** access ports to end hosts skip the listening/learning delay (a server NIC can't create a loop) — but pair it with **BPDU Guard** so someone plugging a rogue switch into that port can't hijack the topology.

> **The data-center critique of STP:** STP *wastes* the bandwidth of every blocked link (often ~50%) and converges slowly. This is a primary motivation for moving the data-center fabric to **L3/ECMP** (§11), where *all* links carry traffic and there are no L2 loops to break. STP is for campus/access networks; modern fabrics route instead.

---

## 10. Link aggregation (LACP) & jumbo frames

### 10.1 Link aggregation (802.1AX / LACP)
Bond multiple physical links into one logical link ("port channel," "bond," "team") for **more bandwidth and redundancy** — and crucially, without an STP loop (the bundle appears as *one* link to STP).

```
   Host/Switch  ═══ link 1 (10G) ═══╗
                ═══ link 2 (10G) ═══╬═══►  one logical 20G "bond0"
                                    ╝
 - LACP (active) negotiates the bundle with the peer; "static" just assumes it.
 - Traffic is spread per-FLOW (hash of src/dst MAC+IP+port), NOT per-packet,
   so a single TCP flow stays on one link (avoids reordering) — meaning one flow
   never exceeds one member-link's speed. Aggregate helps *many* flows.
 - Survives a member failure: the flow rehashes onto a surviving link.
```

The per-flow hashing caveat is the one staff engineers must remember: **LACP gives you aggregate throughput across many flows, not a faster single flow.** A single 25 Gb/s elephant flow does not get faster on a 2×25G bond.

### 10.2 MTU & jumbo frames
Standard Ethernet payload (MTU) is **1500 bytes**. **Jumbo frames** raise it to ~**9000 bytes**:
- **Fewer frames per byte** → less per-packet overhead (headers, interrupts, ACK processing) → higher throughput and lower CPU, especially for storage/backup (iSCSI, NFS, NVMe-oF) and east-west data-center traffic.
- **The catch: MTU must match end-to-end.** A single device on the path with MTU 1500 will fragment or (with DF set) black-hole the jumbo frames — exactly the silent PMTUD failure from [01](01_network_models_layering.md) §7. Jumbo frames are great *inside a controlled fabric*, dangerous across uncontrolled paths.

### 10.3 PoE (Power over Ethernet)
802.3af/at/bt delivers DC power (up to ~90W with 802.3bt) over the same twisted pair that carries data — powering APs, IP phones, cameras, and small switches from the switch port. Not a frame-layer concern, but a real deployment consideration: a "switch budget" includes a *power* budget, and oversubscribing PoE will brown out endpoints.

---

## 11. The data-center fabric — leaf-spine (Clos)

Classic enterprise networks were **3-tier**: access → aggregation → core, a tree optimized for **north-south** traffic (client ↔ server). Modern data centers are dominated by **east-west** traffic (server ↔ server: distributed databases, microservices, ML training shuffles, storage replication). The 3-tier tree is wrong for this: east-west traffic between two leaf racks must climb to the core and back, the core becomes a bottleneck, and STP blocks half your links.

The answer is a **leaf-spine (folded Clos) topology**:

```
                 ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
   SPINE layer   │ Spine 1│  │ Spine 2│  │ Spine 3│  │ Spine 4│
                 └─┬─┬─┬─┬─┘  └─┬─┬─┬─┬┘  └─┬─┬─┬─┬┘  └─┬─┬─┬─┬┘
                   │ │ │ └──────┼─┼─┼──┐    │ │ │      ... full mesh ...
   ┌───────────────┼─┼─────────┼─┼────┼────┼─┼──────────────────────┐
   │  every leaf connects to EVERY spine (and to no other leaf)      │
   └─────┬───────────────┬───────────────┬───────────────┬──────────┘
       ┌─┴──┐          ┌─┴──┐          ┌─┴──┐          ┌─┴──┐
 LEAF  │Leaf│          │Leaf│          │Leaf│          │Leaf│   (Top-of-Rack switch)
 (ToR)  └┬┬┬┘           └┬┬┬┘           └┬┬┬┘           └┬┬┬┘
   servers...        servers...       servers...      servers...
```

Properties that make this the universal cloud/DC design:
- **Constant hop count:** *any* server reaches *any other* server in exactly **leaf → spine → leaf = 2 hops**. Predictable, uniform latency — no "near vs far" rack penalty.
- **Horizontal scale:** need more bandwidth? Add a spine (every leaf gets another uplink). Need more servers? Add a leaf. Scale-out, not scale-up.
- **All links active via L3 ECMP:** the fabric is **routed** (BGP or OSPF to the ToR), not switched. **ECMP (Equal-Cost Multi-Path)** spreads flows across all the spine uplinks — no STP, no blocked links, no broadcast storms. (ECMP detailed in [03](03_network_layer_routing.md).) Cilium/Isovalent and the big clouds run exactly this, often with BGP all the way to the host.

### 11.1 Oversubscription math (the load-bearing back-of-envelope)

Oversubscription = (total downlink/server-facing bandwidth) ÷ (total uplink/spine-facing bandwidth). It tells you how much you've *bet* that not all servers burst at once.

```
 A leaf switch with:
   - 48 server ports × 25 Gb/s downlinks   = 1200 Gb/s toward servers
   -  8 uplink  ports × 100 Gb/s to spines =  800 Gb/s toward the fabric

 Oversubscription ratio = downlink : uplink = 1200 : 800 = 3 : 2  (1.5:1)

 Interpretation: if every server tried to send to a different rack at full 25G
 simultaneously, demand (1200G) would exceed fabric capacity (800G) by 1.5×.
 You are betting peak east-west fan-out stays under 800G. 1:1 ("non-blocking") is
 ideal but expensive; 3:1 is common for general workloads; storage/ML clusters
 push toward 1:1 because their shuffles really do saturate every link at once.
```

To make it **non-blocking (1:1)** here you'd need 12×100G uplinks (1200G) to match the 1200G of downlinks. The trade-off is pure cost vs. tail-latency-under-burst: under-provision the uplinks and east-west congestion shows up as latency spikes and incast drops exactly when your distributed job does its all-to-all phase.

---

## 12. VXLAN overlays

The 12-bit 802.1Q VLAN ID caps you at **4094** segments — fine for a campus, hopelessly small for a public cloud with millions of tenants. And VLANs are tied to physical L2 topology, which clashes with VM/container mobility (a VM should keep its L2 adjacency when it migrates across racks/L3 boundaries).

**VXLAN (RFC 7348)** solves both by tunneling L2 frames inside **UDP/IP** — a MAC-in-UDP overlay over the routed (L3) fabric:

```
 Original L2 frame (tenant's view):
   ┌────────┬────────┬──────────────────┐
   │ Dst MAC│ Src MAC│ payload (IP, ...) │
   └────────┴────────┴──────────────────┘
                      │  encapsulated by the VTEP (VXLAN Tunnel End Point)
                      ▼
 VXLAN-encapsulated packet on the wire:
   ┌──────┬──────┬──────┬─────────────────┬══════════════════════════════┐
   │ Outer│ Outer│ Outer│ VXLAN hdr       ║   original inner L2 frame     ║
   │ Eth  │ IP   │ UDP  │ (24-bit VNI)    ║   (Dst/Src MAC + payload)     ║
   └──────┴──────┴──────┴─────────────────┴══════════════════════════════┘
                  dst port 4789      └─ VNI: 24 bits → 16,777,216 segments
```

- **VNI (VXLAN Network Identifier)** is **24 bits → 16M segments** vs. 4094 VLANs — solves multi-tenancy at cloud scale.
- **VTEPs** (on hypervisors, ToR switches, or smart NICs) encapsulate/decapsulate. The tenant sees a flat L2 network; the physical fabric sees ordinary routed UDP/IP packets it can ECMP-balance across the leaf-spine.
- **Decoupling:** the overlay (tenant L2 adjacency) is independent of the underlay (physical L3 topology). A VM can live in VXLAN segment 5000 regardless of which rack it's on — enabling live migration across L3 boundaries.
- **Cost:** the ~50-byte VXLAN/UDP/IP outer header eats into MTU (run the underlay at jumbo/≥1550 MTU so the inner frame still gets its full 1500), and encap/decap costs CPU unless offloaded to the NIC. Control plane is typically **EVPN/BGP** (or, in Kubernetes, the CNI — e.g., Cilium can do VXLAN or pure routing). This is the standard model for OpenStack, NSX, and Kubernetes overlay networking.

---

## 13. Working examples

### 13.1 Reading the ARP table and MAC table (CLI)

```bash
# --- ARP cache on a host (IP -> MAC for on-link neighbors) ---
ip neigh show
# 10.0.1.1  dev eth0 lladdr 00:11:22:33:44:55 REACHABLE   <- the default gateway's MAC
# 10.0.1.9  dev eth0 lladdr cc:cc:cc:cc:cc:cc STALE        <- a peer; STALE = needs revalidation
arp -n                  # legacy equivalent (net-tools)

# Force-populate the cache by pinging a neighbor, then inspect:
ping -c1 10.0.1.9 >/dev/null; ip neigh show 10.0.1.9

# Watch ARP on the wire (the broadcast request + unicast reply, §7):
sudo tcpdump -i eth0 -n arp -c 4
# ARP, Request who-has 10.0.1.9 tell 10.0.1.5, length 28      <- broadcast
# ARP, Reply 10.0.1.9 is-at cc:cc:cc:cc:cc:cc, length 46      <- unicast reply

# --- On a switch (Cisco IOS): the MAC address (CAM) table that learning built (§5) ---
#   show mac address-table
#   Vlan   Mac Address        Type      Ports
#   10     0011.2233.4455     DYNAMIC   Gi1/0/1     <- learned, port it lives on
#   10     cccc.cccc.cccc     DYNAMIC   Gi1/0/3
#   show spanning-tree        <- which ports are Root/Designated/Blocking (§9)
#   show etherchannel summary <- LACP bundle status (§10)
```

### 13.2 An ARP request builder/parser in pure Python

Builds an RFC-826 ARP request frame and parses it back — no root, no scapy, runs anywhere.

```python
#!/usr/bin/env python3
"""
arp_tool.py — build and parse an Ethernet/ARP request frame (RFC 826).
Pure stdlib. Run: python arp_tool.py
"""
import struct

ETH_P_ARP = 0x0806
HTYPE_ETH = 1          # hardware type: Ethernet
PTYPE_IPV4 = 0x0800    # protocol type: IPv4
OP_REQUEST = 1
OP_REPLY = 2
BROADCAST = b"\xff\xff\xff\xff\xff\xff"

def mac_to_bytes(s: str) -> bytes:
    return bytes(int(o, 16) for o in s.split(":"))

def bytes_to_mac(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)

def ip_to_bytes(s: str) -> bytes:
    return bytes(int(o) for o in s.split("."))

def bytes_to_ip(b: bytes) -> str:
    return ".".join(str(x) for x in b)

def build_arp_request(sender_mac: str, sender_ip: str, target_ip: str) -> bytes:
    """Frame an ARP request: 'Who has target_ip? Tell sender_ip'."""
    # Ethernet header: dst=broadcast, src=sender, type=ARP
    eth = struct.pack("!6s6sH", BROADCAST, mac_to_bytes(sender_mac), ETH_P_ARP)
    # ARP payload (28 bytes for IPv4-over-Ethernet)
    arp = struct.pack(
        "!HHBBH6s4s6s4s",
        HTYPE_ETH, PTYPE_IPV4,
        6, 4,                      # hardware addr len, protocol addr len
        OP_REQUEST,
        mac_to_bytes(sender_mac), ip_to_bytes(sender_ip),
        b"\x00\x00\x00\x00\x00\x00",   # target MAC unknown — that's what we're asking
        ip_to_bytes(target_ip),
    )
    return eth + arp

def parse_arp(frame: bytes) -> dict:
    dst, src, etype = struct.unpack("!6s6sH", frame[:14])
    assert etype == ETH_P_ARP, f"not ARP (ethertype {etype:#06x})"
    (htype, ptype, hlen, plen, op,
     sha, spa, tha, tpa) = struct.unpack("!HHBBH6s4s6s4s", frame[14:42])
    return {
        "eth_dst": bytes_to_mac(dst), "eth_src": bytes_to_mac(src),
        "operation": {OP_REQUEST: "request", OP_REPLY: "reply"}.get(op, op),
        "sender_mac": bytes_to_mac(sha), "sender_ip": bytes_to_ip(spa),
        "target_mac": bytes_to_mac(tha), "target_ip": bytes_to_ip(tpa),
    }

if __name__ == "__main__":
    frame = build_arp_request("aa:bb:cc:11:22:33", "10.0.1.5", "10.0.1.9")
    print(f"built ARP request: {len(frame)} bytes (14 Eth + 28 ARP)")
    parsed = parse_arp(frame)
    for k, v in parsed.items():
        print(f"  {k:12} {v}")
    assert parsed["operation"] == "request"
    assert parsed["target_ip"] == "10.0.1.9"
    assert parsed["target_mac"] == "00:00:00:00:00:00"   # the unknown we're resolving
    assert parsed["eth_dst"] == "ff:ff:ff:ff:ff:ff"      # it's a broadcast
    print("\nOK: 'Who has 10.0.1.9? Tell 10.0.1.5' — broadcast, target MAC unknown.")
```

> **Optional (scapy) to actually send it and watch the reply:**
> ```text
> from scapy.all import Ether, ARP, srp
> ans, _ = srp(Ether(dst="ff:ff:ff:ff:ff:ff")/ARP(pdst="10.0.1.9"), timeout=2, iface="eth0")
> for _, r in ans: print(r.psrc, "is-at", r.hwsrc)   # the reply maps IP -> MAC
> ```
> Sending raw frames needs root/CAP_NET_RAW; the stdlib builder/parser above needs neither and teaches the byte layout.

---

## 14. Advanced: lossless Ethernet (RoCE), the EVPN control plane, and L2 security

### Lossless Ethernet — PFC, ECN, and RDMA over Converged Ethernet

Standard Ethernet drops frames under congestion and lets TCP recover
([04 §9](04_transport_tcp_udp.md)). But **RDMA** (remote direct memory access — used by
high-performance storage, AI training fabrics, and HPC) needs **lossless** transport,
because **RoCEv2** (RDMA over Converged Ethernet) performs poorly with loss. The data
center makes Ethernet lossless with two mechanisms:

- **PFC (Priority Flow Control, 802.1Qbb)** — per-traffic-class PAUSE: a switch tells
  the upstream "stop sending class 3" instead of dropping. Enables lossless lanes
  alongside lossy ones.
- **ECN + DCQCN** — switches mark (not drop) packets at congestion onset; endpoints
  reduce rate. This keeps queues short *without* loss.

The catch: PFC can cause **head-of-line blocking** and even **PFC deadlock/storms**
across a fabric — a notorious source of large AI-cluster outages. Tuning RoCE
(PFC + ECN thresholds) is a deep specialty; this is why some hyperscalers moved AI
fabrics to custom congestion control or InfiniBand.

### EVPN — the control plane VXLAN was missing

Plain VXLAN ([§12](#12-vxlan-overlays)) floods to learn MACs (BUM traffic), which
doesn't scale. **EVPN** (Ethernet VPN) uses **BGP** ([03 §8](03_network_layer_routing.md))
as a control plane to *distribute* MAC/IP-to-VTEP mappings — no flooding, fast
convergence, multihoming (ESI), and integrated routing/bridging. EVPN-VXLAN is the
standard modern data-center fabric overlay; it's how a leaf-spine
([§11](#11-the-data-center-fabric--leaf-spine-clos)) carries thousands of tenant L2
segments without broadcast storms.

### L2 security — the trust-the-wire assumptions that bite

The link layer assumes a trusted segment; attackers on it have powerful options:

- **ARP spoofing** ([§7](#7-arp--address-resolution-protocol-rfc-826)) — forge ARP
  replies to MITM a host. Defense: **Dynamic ARP Inspection (DAI)** + DHCP snooping on
  switches.
- **MAC flooding** — overflow the CAM table ([§5](#5-the-mac-address-table--learning))
  so the switch fails open (floods everything), turning it into a hub for sniffing.
  Defense: **port security** (cap MACs per port).
- **VLAN hopping** — double-tagging or DTP abuse to reach other VLANs. Defense:
  disable DTP, explicit trunk config, no native-VLAN overlap.
- **MACsec (802.1AE)** — line-rate L2 encryption/authentication, so even an attacker
  on the wire can't read or forge frames — increasingly used host-to-switch in
  zero-trust fabrics.

---

## Key Takeaways

1. **The link layer is one hop.** MAC addresses have *local scope* and are rewritten at every router; the medium-specific framing (Ethernet) rides under IP via the hourglass. Switches keep you on one IP network; routers move you between them and stop broadcasts.
2. **Switched, full-duplex Ethernet retired CSMA/CD.** One host per port = one collision domain per port = zero collisions. The 64-byte minimum frame is a fossil of the shared-bus collision-detection era.
3. **Switches self-learn the MAC table by backward learning** (learn from source, flood unknown/broadcast, forward known unicast, age out). MAC flooding overflows the CAM table and degrades a switch into a hub — defend with port security.
4. **Collision domain = per port (gone in practice); broadcast domain = per router/VLAN.** Broadcast traffic is *why* you segment large networks — flat L2 at scale drowns in ARP/DHCP broadcasts.
5. **ARP maps IP→MAC for the next hop only** (the gateway, if the destination is remote). It is unauthenticated → ARP spoofing enables trivial LAN MITM; mitigate with Dynamic ARP Inspection / static entries / 802.1X. IPv6 replaces ARP with NDP over multicast.
6. **VLANs (802.1Q, 12-bit ID, 4094 max) virtualize broadcast domains;** access ports are untagged (one VLAN), trunk ports are tagged (many VLANs). Inter-VLAN traffic must be routed.
7. **Ethernet frames have no TTL → L2 loops are fatal (broadcast storms).** STP/RSTP breaks loops by blocking redundant links — at the cost of wasted bandwidth and (classic STP) slow convergence, which is why data centers route instead.
8. **LACP bonds links for aggregate throughput and redundancy** but hashes per-flow — a single flow never exceeds one member link. Jumbo frames (MTU 9000) cut overhead but must match end-to-end or black-hole.
9. **The data-center fabric is leaf-spine (Clos): any-to-any in 2 hops, all links active via L3/ECMP, scale-out by adding spines/leaves.** Oversubscription (downlink:uplink) is the explicit bet on peak east-west fan-out — 3:1 typical, 1:1 for storage/ML.
10. **VXLAN tunnels L2-in-UDP/IP with a 24-bit VNI (16M segments),** decoupling tenant overlay from physical underlay for cloud-scale multi-tenancy and VM/container mobility — at the cost of MTU overhead and encap CPU (usually NIC-offloaded, EVPN/BGP control plane).

> Read next: [03 — Network Layer, IP & Routing](03_network_layer_routing.md) for how the routed fabric and the global Internet actually pick the next hop — CIDR, longest-prefix match, OSPF, BGP, ECMP, and anycast.
