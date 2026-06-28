# 10 — Cloud Networking, SDN & Overlays

> **Audience:** staff/principal. You can subnet by hand and you've debugged a Kubernetes Service that "won't connect." This doc is about *how the network became software* — control/data plane separation, the cloud VPC model, overlay encapsulation, and the full packet path between two pods on different nodes — reasoned end to end, with a worked CIDR plan and a NetworkPolicy you can ship.
>
> **Primary sources:** McKeown et al., *OpenFlow: Enabling Innovation in Campus Networks* (2008); the AWS VPC and Azure VNet documentation; RFC 7348 (VXLAN), RFC 8926 (GENEVE), RFC 2784 (GRE); the Kubernetes networking model & CNI spec; the Cilium / eBPF documentation and Cilium engineering blog; Gregg, *BPF Performance Tools*; Calico/Cilium NetworkPolicy references.

---

## 1. Why this matters at scale

Once your infrastructure is virtual, **the network is a program**, not a patch panel. Three consequences dominate cloud-scale design:

1. **The control plane and data plane split.** A central controller decides *policy* (where traffic may go); a fleet of dumb-but-fast forwarders execute it. This separation — the essence of **SDN** — is what lets a cloud provider reconfigure a million virtual networks in seconds without touching a single cable, and what lets Kubernetes give every pod an IP without anyone allocating subnets by hand.
2. **Addressing is virtualized via overlays.** Pods and VMs live on **overlay** networks whose packets are **encapsulated** inside the physical **underlay** network's packets. The tenant's 10.0.0.0/16 has nothing to do with the datacenter's physical fabric; encapsulation (VXLAN/GENEVE) bridges them. Get the encapsulation/MTU story wrong and you get the classic "works for small packets, hangs on large ones" outage.
3. **Policy moved from the perimeter to the workload.** Security groups, NACLs, and NetworkPolicies enforce **microsegmentation** at every NIC and pod — the data-plane realization of zero trust ([09](09_network_security.md) §4).

Staff engineers are expected to plan non-overlapping CIDR space that survives years of growth, reason about east-west vs north-south traffic, write correct NetworkPolicies, and trace a packet from one pod to another across nodes including the encapsulation and NAT hops.

---

## 2. From hardware to software-defined networking

### 2.1 Control plane vs data plane

```text
        CONTROL PLANE  (decides WHERE traffic goes — the "brain")
        - routing protocols, policy, topology, ACL compilation
        - slow path, runs in software, may be centralized
                         |
                         |  programs forwarding rules (e.g. OpenFlow)
                         v
        DATA PLANE  (moves packets — the "muscle")
        - per-packet lookup + forward, line rate
        - fast path, ASIC / eBPF / kernel / poll-mode driver
```

In a **traditional** router/switch, both planes live in the same box and are coupled. **SDN** decouples them: a logically centralized **controller** computes forwarding state and pushes it into many simple forwarding elements via a southbound API. This gives you a *global view* and *programmable policy* instead of per-box CLI.

### 2.2 OpenFlow

**OpenFlow** (McKeown et al., 2008) was the first widely-deployed southbound protocol: the controller installs **flow-table entries** (match on header fields → action: forward/drop/modify/send-to-controller) into switches. It proved the control/data split was practical. In modern clouds the *pure* OpenFlow model is largely supplanted by provider-specific control planes and, increasingly, **eBPF** (the controller programs in-kernel eBPF maps instead of OpenFlow tables — see Cilium, §6.4), but the architectural idea — centralize the brain, distribute the muscle — is everywhere.

| | Traditional networking | SDN |
|---|---|---|
| Planes | coupled in each device | **separated**; centralized control |
| Configuration | per-box CLI, manual | programmatic, API-driven, global view |
| Change speed | hours/days (tickets, cables) | seconds (push new flow state) |
| Innovation | vendor firmware cycle | software cycle |
| Failure mode | distributed (resilient) | controller is a critical dependency (mitigate with HA) |

---

## 3. The cloud VPC model

A **VPC** (Virtual Private Cloud; Azure VNet, GCP VPC) is a software-defined, isolated L3 network you own inside the provider's fabric. Its building blocks:

| Construct | What it is | Scope |
|---|---|---|
| **VPC** | an isolated network with a CIDR block (e.g., 10.0.0.0/16) | region |
| **Subnet** | a CIDR slice of the VPC, pinned to **one AZ** | availability zone |
| **Route table** | per-subnet rules: destination CIDR → target (local / igw / nat / peering / tgw) | subnet(s) |
| **Internet Gateway (IGW)** | gives a subnet bidirectional public internet (a subnet is "public" iff its route table points 0.0.0.0/0 at an IGW *and* instances have public IPs) | VPC |
| **NAT Gateway** | lets **private** subnets reach *out* to the internet without being reachable *in* | AZ |
| **Security Group (SG)** | **stateful** firewall at the ENI/instance level | instance |
| **NACL** | **stateless** firewall at the subnet boundary | subnet |
| **VPC Peering** | 1:1 private connection between two VPCs (non-transitive) | inter-VPC |
| **Transit Gateway (TGW)** | hub-and-spoke router connecting many VPCs/on-prem (transitive) | many VPCs |
| **PrivateLink / endpoint** | reach a specific *service* privately, without exposing the whole VPC | service |

### 3.1 Security Groups vs NACLs — the exam question

| | **Security Group** | **NACL (Network ACL)** |
|---|---|---|
| Layer of attachment | instance / ENI | subnet |
| State | **stateful** (return traffic auto-allowed) | **stateless** (must allow both directions explicitly) |
| Rules | **allow only** | allow **and** deny (ordered, numbered) |
| Evaluation | all rules evaluated (most-permissive union) | rules in number order, first match wins |
| Default | deny all inbound, allow all outbound | allow all (default NACL) |

> **Stateful (SG) vs stateless (NACL)** is the same distinction as in [09](09_network_security.md) §3. SGs are your primary, per-workload microsegmentation tool; NACLs are a coarse subnet-level backstop (and the only place you can write explicit *deny* rules, e.g., to block a known-bad IP range at the subnet edge).

### 3.2 Peering vs Transit Gateway

VPC **peering** is point-to-point and **non-transitive**: if A peers B and B peers C, A cannot reach C through B. N VPCs fully meshed need N(N−1)/2 peerings — 10 VPCs = 45 connections, unmanageable. A **Transit Gateway** is a central router: each VPC attaches once, and TGW route tables decide who reaches whom (transitive, with segmentation via separate route tables). The trade-off: TGW adds a hop, a per-GB cost, and a central dependency, in exchange for O(N) instead of O(N²) connectivity and centralized policy.

**PrivateLink** is different again: it exposes a *single service* (e.g., a SaaS API or your own service) into a consumer VPC as an endpoint, without peering the whole networks — least-privilege connectivity between organizations.

---

## 4. Overlay networks & encapsulation

The cloud/Kubernetes network the tenant sees (the **overlay**) is decoupled from the physical fabric (the **underlay**) by wrapping each overlay packet inside an underlay packet — **encapsulation** (a "tunnel").

```text
  overlay packet (pod A -> pod B, addresses 10.244.x):
     [ inner Eth | inner IP 10.244.1.5 -> 10.244.2.9 | inner payload ]

  encapsulated for the underlay (node 1 -> node 2, physical IPs 192.168.x):
     [ outer Eth | outer IP 192.168.0.11 -> 192.168.0.12 | UDP | VXLAN hdr |
       inner Eth | inner IP 10.244.1.5 -> 10.244.2.9 | inner payload ]
       \_______________ added by encapsulation ________________/
```

| Encap | Header | Transport | Notes |
|---|---|---|---|
| **VXLAN** (RFC 7348) | 8 B + outer UDP/IP/Eth (~50 B total) | UDP/4789 | 24-bit VNI → 16M segments; the de-facto datacenter overlay; HW offload common |
| **GENEVE** (RFC 8926) | variable, **TLV options** + UDP | UDP/6081 | extensible (carries metadata); used by NSX, OVN, AWS Gateway Load Balancer |
| **GRE** (RFC 2784) | 4–8 B, **no UDP** | IP proto 47 | simple, generic; no UDP entropy → weaker ECMP hashing |
| **IP-in-IP** | 20 B | IP proto 4 | minimal overhead; L3 only, no L2 emulation (Calico option) |

### 4.1 The MTU trap

Encapsulation **adds bytes** to every packet (VXLAN: ~50 B). If the underlay MTU is 1500 and the pod still believes its MTU is 1500, a full-size pod packet becomes 1550 on the wire → fragmented or **dropped** (if DF is set and PMTUD is blocked). Symptom: small packets and handshakes work, large transfers / TLS with big certs **hang**. Fixes: lower the overlay MTU (e.g., 1450 for VXLAN), or use **jumbo frames** (9000) on the underlay so the encapsulated packet still fits.

> This MTU mismatch is one of the most common and most baffling cloud/Kubernetes outages — it presents as "intermittent" or "only big responses fail." Always account for encapsulation overhead in the overlay MTU.

---

## 5. Kubernetes networking — the model

Kubernetes mandates a deliberately simple network model, and CNI plugins implement it:

> **The Kubernetes network model (the four rules):**
> 1. Every **pod gets its own IP** (no port-mapping gymnastics; a pod sees the same IP others use to reach it).
> 2. **Pods can reach all other pods** across nodes **without NAT**.
> 3. **Nodes can reach all pods** without NAT.
> 4. A pod's view of its own IP == others' view of it.

This "flat pod network" is the contract; *how* it's delivered (overlay vs routed, iptables vs eBPF) is the CNI plugin's choice.

### 5.1 CNI — the plugin interface

**CNI (Container Network Interface)** is the spec the kubelet calls when a pod is created/destroyed: it hands the plugin a network namespace and the plugin must wire up the pod's interface, assign an IP (IPAM), and program routes/policy. Plugins:

| CNI | Data path | Pod connectivity | Network policy |
|---|---|---|---|
| **Flannel** | VXLAN overlay (default) | encapsulated | none (needs an add-on) |
| **Calico** | routed (BGP) or IP-in-IP/VXLAN | routed (no encap if L3 reachable) | yes (iptables/eBPF) |
| **Cilium** | **eBPF** (VXLAN/GENEVE or routed) | eBPF datapath, optional encap | yes, rich (L3–L7) |
| **AWS VPC CNI** | **no overlay** — pods get real VPC IPs from ENIs | native VPC routing | SGs + policy |

> **Overlay vs native** is the central CNI trade-off. Overlays (Flannel VXLAN) work anywhere but pay encapsulation overhead + MTU complexity. Native/routed (AWS VPC CNI, Calico-BGP) give pods real underlay IPs — no encap, full performance, but pods consume the VPC's IP space (a real constraint: large clusters can exhaust a /16) and require underlay support.

### 5.2 kube-proxy: iptables vs IPVS vs eBPF

A **Service** has a stable virtual IP (ClusterIP); something must load-balance that VIP across the live pod IPs (endpoints). That something is the **kube-proxy** data path:

| Mode | Mechanism | Scaling |
|---|---|---|
| **iptables** | a chain of DNAT rules per Service; random/probabilistic selection | O(n) rules; **rule updates and matching degrade with thousands of Services** |
| **IPVS** | kernel L4 load balancer with a hash table + real LB algorithms (rr, lc, …) | O(1) lookup; scales to many more Services |
| **eBPF (Cilium)** | replace kube-proxy entirely; LB in an eBPF program with a hash map, often at the **XDP** layer | O(1), lowest overhead, no iptables sprawl |

> Cilium's "kube-proxy replacement" eliminates the iptables Service chains entirely, doing ClusterIP/NodePort load balancing in eBPF (and DSR/Maglev hashing). On large clusters this removes a real scaling cliff (iptables rule explosion) and cuts per-packet cost — the data-plane payoff of programming eBPF maps instead of compiling iptables rules. (See [08](08_network_performance_tuning.md) §8.3 for XDP mechanics.)

### 5.3 Service types & Ingress

| Type | Reachable from | How |
|---|---|---|
| **ClusterIP** | inside the cluster only | a virtual IP load-balanced to pods (default) |
| **NodePort** | outside, via `<anyNodeIP>:30000–32767` | every node forwards the port to the Service |
| **LoadBalancer** | outside, via a cloud LB | provisions a cloud L4 LB → NodePort → pods |
| **Ingress** | outside, **L7** (host/path routing, TLS) | an Ingress controller (nginx, Envoy) — one LB fronting many services |

**Ingress** (and its successor, the **Gateway API**) is the **north-south** L7 entry point: one external load balancer terminating TLS and routing by host/path to many backend Services, instead of one cloud LB per service.

### 5.4 Service mesh recap

A **service mesh** (Istio, Linkerd) injects a sidecar (or, increasingly, a per-node eBPF/Envoy proxy) into the data path to provide **mTLS, retries, circuit breaking, traffic splitting, and observability** for east-west traffic — without app code changes. It's the data-plane realization of zero trust ([09](09_network_security.md) §4.2): every service-to-service hop is mutually authenticated and encrypted. The cost is a proxy hop's latency and operational complexity (sidecarless/eBPF meshes aim to cut that hop).

---

## 6. East-west vs north-south, multi-region, eBPF, IPv6

### 6.1 East-west vs north-south

```text
        NORTH  (internet / clients)
          ^
          |  north-south traffic: in/out of the cluster or VPC
          |  (Ingress, LoadBalancer, IGW, WAF, CDN)
   +------+-----------------------------+
   |   [svc A] <----> [svc B] <----> [svc C]   |   EAST-WEST traffic:
   |       ^  service-to-service, the bulk      |   service mesh, NetworkPolicy,
   |       |  of microservice traffic           |   SG-to-SG, ClusterIP
   +-------------------------------------------+
          |
        SOUTH  (databases / backends)
```

- **North-south** crosses the trust boundary (clients ↔ services). Defended by CDN, WAF, Ingress, IGW, public LBs.
- **East-west** is internal service-to-service — typically the **majority of traffic** in a microservice architecture and historically the **least secured** ("inside = trusted"). Zero trust + service mesh + NetworkPolicy exist to authenticate and segment it. Microsegmentation is fundamentally about constraining east-west blast radius.

### 6.2 Multi-region / multi-cloud connectivity

| Need | Mechanism |
|---|---|
| On-prem ↔ cloud, **private, high-bandwidth, dedicated** | **AWS Direct Connect / Azure ExpressRoute** (a physical/dedicated circuit, predictable latency, bypasses the public internet) |
| On-prem ↔ cloud, quick / encrypted over internet | **site-to-site VPN** (IPsec, see [09](09_network_security.md) §7) |
| Many branches/sites, policy-driven, internet-as-transport | **SD-WAN** (SDN applied to the WAN: central policy chooses the best path — MPLS, broadband, LTE — per application) |
| VPC ↔ VPC across regions | inter-region peering or TGW peering |

**Direct Connect/ExpressRoute** trade provisioning time and cost for **consistent low latency and high bandwidth** that the public internet can't guarantee — the choice for replication, hybrid databases, and large data transfer.

### 6.3 eBPF / XDP for cloud networking

**eBPF** lets you run sandboxed programs in the kernel data path; **XDP** runs them at the earliest possible point (the driver, before skb allocation — see [08](08_network_performance_tuning.md) §8.3). In cloud networking this powers:

- **CNI data path** (Cilium): pod connectivity, LB, and policy as eBPF programs and maps — no iptables sprawl, lower per-packet cost.
- **L4 load balancing** at XDP (Cilium, Facebook Katran, Cloudflare): line-rate, in-kernel, DSR.
- **Observability** (Hubble, `bpftrace`): per-flow visibility without sidecars or sampling.
- **DDoS drop** (XDP_DROP) before the stack pays for the packet.

This is the modern face of SDN: instead of OpenFlow programming an ASIC, a controller programs **eBPF maps** in every node's kernel.

### 6.4 IPv6 in the cloud

IPv6's enormous address space removes the IP-scarcity pressure that forced NAT and overlay address-translation gymnastics — pods/instances can have globally unique addresses (dual-stack VPCs, IPv6-native clusters). It also re-emphasizes [09](09_network_security.md) §4.3: **no NAT means no accidental "NAT firewall"** — you *must* use security groups/NetworkPolicy as the real control. Cloud adoption is via dual-stack (IPv4 + IPv6) subnets and egress-only internet gateways (the IPv6 analog of a NAT gateway for outbound-only).

---

## 7. Worked example — VPC CIDR planning

Plan a VPC for a 3-AZ, growth-headroom production environment. **The cardinal rule: never overlap CIDRs** — overlapping ranges make peering, TGW, and on-prem connectivity impossible to route. Reserve space generously; you cannot easily renumber later.

### 7.1 Requirements

- One region, **3 AZs** (us-east-1a/1b/1c).
- Each AZ needs a **public** subnet (load balancers, NAT GW) and a **private** subnet (app/EKS pods).
- Headroom to add a **data** tier later, and to peer with other VPCs (so don't grab the whole 10.0.0.0/8).

### 7.2 The math

Pick the **VPC** block `10.20.0.0/16` (65,536 addresses; distinct from other VPCs' /16s to allow peering).

CIDR sizing: a `/n` block holds `2^(32-n)` addresses; AWS reserves **5** per subnet (network, VPC router, DNS, future, broadcast), so usable = `2^(32-n) − 5`.

| Prefix | Total addrs | Usable (AWS) | Use |
|---|---|---|---|
| /24 | 256 | 251 | a public subnet (LBs/NAT — few hosts) |
| /20 | 4,096 | 4,091 | a private subnet (lots of pods/instances) |
| /22 | 1,024 | 1,019 | a data subnet |

**Allocation strategy:** carve the /16 into per-AZ /20 "super-blocks", then subnet within each AZ. This keeps each AZ's space contiguous (clean route summarization) and leaves the upper half of the /16 unallocated for future tiers/AZs.

```text
VPC 10.20.0.0/16   (10.20.0.0 .. 10.20.255.255)

  AZ-a  super-block 10.20.0.0/20   (10.20.0.0  .. 10.20.15.255)
        public  10.20.0.0/24   (251 usable)  -> NAT-a, public LB ENIs
        private 10.20.4.0/22   (1019 usable) -> app/pods in AZ-a
        (10.20.8.0/21 reserved for AZ-a data/expansion)

  AZ-b  super-block 10.20.16.0/20  (10.20.16.0 .. 10.20.31.255)
        public  10.20.16.0/24
        private 10.20.20.0/22
        (10.20.24.0/21 reserved)

  AZ-c  super-block 10.20.32.0/20  (10.20.32.0 .. 10.20.47.255)
        public  10.20.32.0/24
        private 10.20.36.0/22
        (10.20.40.0/21 reserved)

  10.20.48.0/20 .. 10.20.240.0/20  -> 13 more /20 blocks FREE
                                       (future AZs, data tier, EKS pod CIDR)
```

### 7.3 Verifying the math (no overlaps, enough room)

- Each AZ super-block `/20` = 4,096 addrs; `10.20.0.0/20`, `10.20.16.0/20`, `10.20.32.0/20` start at multiples of 16 in the third octet (0, 16, 32) → **non-overlapping** by construction.
- Within AZ-a: public `10.20.0.0/24` occupies `10.20.0.x`; private `10.20.4.0/22` occupies `10.20.4–7.x`. They don't touch, and both fit inside `10.20.0.0/20` (which spans `10.20.0–15.x`). ✓
- Capacity check: a private `/22` = 1,019 usable IPs per AZ. With the **AWS VPC CNI** assigning *real VPC IPs to every pod*, 1,019 IPs ≈ 1,019 pods per AZ subnet — **plan pod density against subnet size** (this is the IP-exhaustion trap of native CNI from §5.1). If you need more, widen private subnets to `/21` or `/20`, which is why we left headroom.
- Route tables: public subnets → `0.0.0.0/0` via **IGW**; private subnets → `0.0.0.0/0` via the **NAT GW in their own AZ** (per-AZ NAT avoids cross-AZ data charges and an AZ-failure dependency); `10.20.0.0/16` is `local` everywhere.

> **CIDR planning checklist:** (1) one non-overlapping /16 per VPC, chosen to not collide with peers/on-prem; (2) contiguous per-AZ super-blocks; (3) public small, private large; (4) leave >50% of the /16 unallocated; (5) size private subnets for **pod density** if using a native CNI; (6) one NAT GW per AZ.

---

## 8. Worked example — Kubernetes NetworkPolicy

By default, **all pods can talk to all pods** (the flat model, §5). A `NetworkPolicy` is a **deny-by-default-once-selected** allowlist: the moment a policy selects a pod, all traffic in the policed direction is denied *except* what the policy permits. This is microsegmentation/zero-trust ([09](09_network_security.md) §4) expressed declaratively.

```yaml
# Allow ONLY the api pods to reach the database pods on 5432,
# allow the database to do DNS, and deny everything else to/from the db.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: db-allow-api-only
  namespace: prod
spec:
  # 1) WHICH pods this policy governs (the "selected" pods):
  podSelector:
    matchLabels:
      app: postgres
  # 2) Govern BOTH directions. Naming a type = "deny that direction
  #    except the rules below". Ingress with no rules = deny all inbound.
  policyTypes:
    - Ingress
    - Egress
  # 3) INBOUND allowlist: only pods labelled app=api, in this namespace,
  #    AND only on TCP 5432.
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: api
      ports:
        - protocol: TCP
          port: 5432
  # 4) OUTBOUND allowlist: the db only needs DNS (to kube-dns in kube-system).
  #    Without this, selecting Egress would block the db's own DNS lookups.
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
```

**What this does and the gotchas:**

- `podSelector: app=postgres` selects the DB pods; from that instant, *all* ingress and egress to those pods is denied except the rules below — that's the deny-by-default flip.
- The ingress rule allows **only** `app=api` pods, **only** on 5432. A pod with any other label, or hitting any other port, is dropped. East-west blast radius collapses to exactly the intended path.
- **Egress gotcha:** once you set `policyTypes: [Egress]`, you've cut *all* outbound — including DNS. Forgetting the kube-dns egress rule is the #1 NetworkPolicy mistake (the DB can't resolve names and everything mysteriously times out). The explicit DNS allow fixes it.
- **`from`/`to` semantics:** `podSelector` alone = "in the policy's namespace"; combine with `namespaceSelector` for cross-namespace; a separate `namespaceSelector` + `podSelector` in the *same* `from` element is an AND (that namespace AND that label), whereas separate list items are an OR.
- Policies are **additive** (allowlists union together) and require a **policy-enforcing CNI** (Calico, Cilium); on a CNI without policy support (vanilla Flannel) the YAML is silently ignored — a dangerous false sense of security.

---

## 9. Worked example — a packet from pod A to pod B across nodes

Trace a TCP packet, pod A (`10.244.1.5` on **node1**, physical `192.168.0.11`) → pod B (`10.244.2.9` on **node2**, physical `192.168.0.12`), on a **VXLAN overlay** CNI. This is the synthesis of §4 (encap), §5 (the model), and [08](08_network_performance_tuning.md) (the data path).

```text
NODE 1 (192.168.0.11)                                  NODE 2 (192.168.0.12)
+-----------------------------+                        +-----------------------------+
|  pod A netns                |                        |                pod B netns  |
|  eth0 = 10.244.1.5          |                        |          10.244.2.9 = eth0  |
|     | (veth pair)           |                        |           (veth pair) |     |
|  vethXXX                    |                        |                    vethYYY  |
|     |                       |                        |                       |     |
|  cni0 / bridge (10.244.1.1) |                        | (10.244.2.1) bridge / cni0  |
|     |                       |                        |                       |     |
|  vxlan0 (VTEP)              |                        |              (VTEP) vxlan0  |
|     |   encapsulate         |                        |        decapsulate    |     |
|  eth0 = 192.168.0.11 -------|---- physical fabric ---|------- 192.168.0.12 = eth0  |
+-----------------------------+    (the UNDERLAY)      +-----------------------------+

STEP 1  pod A sends to 10.244.2.9. In pod A's netns, the route is the default
        via 10.244.1.1 (the node's bridge). Packet leaves pod A's eth0...

STEP 2  ...through the veth pair into node1's root netns, arriving at the
        bridge cni0. Dst 10.244.2.9 is NOT on node1's local subnet
        (10.244.1.0/24) -> it must leave the node.

STEP 3  node1's route table: "10.244.2.0/24 dev vxlan0" (the CNI installed a
        route to pod B's subnet via the VXLAN device). Packet -> vxlan0 (VTEP).

STEP 4  ENCAPSULATION. The VTEP looks up which NODE owns 10.244.2.0/24
        (via the CNI's control plane / FDB) -> node2 at 192.168.0.12. It wraps
        the original packet:
          [ outer IP 192.168.0.11->192.168.0.12 | UDP:4789 | VXLAN VNI |
            inner IP 10.244.1.5->10.244.2.9 | payload ]

STEP 5  The encapsulated packet is a NORMAL underlay packet. node1 forwards it
        out its physical eth0 across the fabric to 192.168.0.12. Routers in
        between only ever see the OUTER header (192.168.x) -- the overlay is
        invisible to them.

STEP 6  node2's eth0 receives UDP:4789 -> handed to its vxlan0 VTEP.
        DECAPSULATION: strip the outer headers, recover the inner packet
        (10.244.1.5 -> 10.244.2.9).

STEP 7  node2 routes the inner packet: 10.244.2.9 is local -> bridge cni0 ->
        veth pair -> pod B's eth0. Delivered. NO NAT anywhere (rule #2 of the
        K8s model): pod B sees the real source 10.244.1.5.

RETURN  the reply retraces symmetrically (B->A), encapsulated node2->node1.
```

**Key observations:**

- **No NAT between pods** — pod B sees pod A's real IP. NAT *does* appear for **north-south** egress (pod → internet goes through the node's SNAT/masquerade or the cloud NAT GW) and for **Service** VIPs (kube-proxy DNATs ClusterIP → a pod IP, §5.2), but never pod-to-pod.
- **The underlay never learns overlay addresses** — fabric routers forward on `192.168.x` only. This is what lets the tenant pick any `10.244.x` without coordinating with the physical network (§2, §4).
- **MTU lands here:** the inner packet + ~50 B VXLAN must fit the underlay MTU, or step 5 fragments/drops (§4.1).
- **A Service call adds one step:** before STEP 1's routing, kube-proxy (iptables/IPVS) or Cilium eBPF rewrites the ClusterIP destination to a concrete pod IP (DNAT) — then the trace above proceeds. With **Cilium eBPF + native routing**, steps 3–6 collapse: no VXLAN encap (pods are routable on the underlay) and the LB/policy happen in eBPF, cutting the per-packet cost.

---

## 10. Advanced: the eBPF dataplane, SNAT exhaustion, and multi-cluster

### eBPF dataplane — replacing kube-proxy and the sidecar

Classic Kubernetes networking ([§5](#5-kubernetes-networking--the-model)) routes
Service traffic via **kube-proxy iptables/IPVS rules** — which scale poorly (thousands
of rules, linear matching) and lean on conntrack
([09 §advanced](09_network_security.md)). **Cilium** (eBPF) replaces kube-proxy: it
attaches eBPF programs at the socket and XDP/TC layers to do Service load-balancing,
NetworkPolicy, and observability **in the kernel**, with hash-map lookups instead of
linear iptables — far better scaling and latency. The frontier is **sidecarless service
mesh** (Cilium ambient, Istio ambient): move L4 mTLS/policy into a per-node eBPF/proxy
layer so every pod no longer needs an Envoy sidecar (cutting the per-pod CPU/memory and
latency tax of [07 §9](07_load_balancing_proxies.md)).

### SNAT / NAT-gateway port exhaustion — the cloud-scale 04.1

Ephemeral-port exhaustion ([scenarios 04.1](../enterprise_scenarios/04_network_incidents.md))
has a cloud-specific amplification: when many pods/instances egress through **one NAT
gateway / SNAT IP**, they **share that IP's ~64k port pool** *per destination*. A fleet
making many connections to one external endpoint (an API, a database) can exhaust SNAT
ports and see mysterious connection failures that *no single host* explains — the limit
is on the shared NAT, not the host. Fixes: more SNAT IPs / NAT-gateway capacity,
connection pooling (the real fix), or direct egress (per-instance public IPs / private
endpoints) to spread the tuple space. Azure's "SNAT port exhaustion" and AWS NAT-gateway
`ErrorPortAllocation` are the named versions.

### Multi-cluster and the address-space problem

Connecting clusters/regions ([§6](#6-east-west-vs-north-south-multi-region-ebpf-ipv6))
runs into **overlapping pod/Service CIDRs** (every cluster used `10.x`), so you can't
just route between them. Solutions: non-overlapping CIDR planning up front
([§7](#7-worked-example--vpc-cidr-planning)), or an overlay that does identity-based
routing (Cilium ClusterMesh, Submariner, Istio multi-cluster) rather than raw IP. This
is also where **IPv6** earns its keep — a flat, non-overlapping global address space
removes the NAT and CIDR-collision pain entirely, which is why large clouds run IPv6
internally.

---

## 11. Trade-offs summary

- **SDN (control/data split)** buys a global view and second-scale reconfiguration at the cost of a centralized control dependency (run it HA).
- **Overlay vs native CNI:** overlays (VXLAN) work anywhere but add encap overhead + MTU complexity; native/routed (AWS VPC CNI, Calico-BGP) give full performance and real IPs but consume underlay address space (the IP-exhaustion trap).
- **Security Groups (stateful, allow-only, per-instance)** vs **NACLs (stateless, allow+deny, per-subnet)** — SGs for microsegmentation, NACLs for coarse subnet deny rules.
- **Peering (O(N²), non-transitive, free-ish)** vs **Transit Gateway (O(N), transitive, central, per-GB cost)** — peering for a few VPCs, TGW once it grows.
- **kube-proxy iptables (simple, but O(n) rule explosion)** vs **IPVS (O(1))** vs **eBPF/Cilium (O(1), no iptables, lowest cost)** — eBPF wins at scale.
- **Direct Connect/ExpressRoute (dedicated, predictable, costly)** vs **VPN (cheap, internet-variable)** for hybrid connectivity.
- **NetworkPolicy** gives declarative zero-trust microsegmentation — but only with a policy-enforcing CNI, and only if you remember to allow DNS egress.

## 12. Key Takeaways

1. **Networking became software:** SDN splits the **control plane** (centralized brain, policy) from the **data plane** (distributed fast forwarders). OpenFlow proved it; modern clouds and **eBPF/Cilium** carry the idea forward (program eBPF maps instead of ASIC flow tables).
2. **The VPC model** = isolated CIDR'd network of per-AZ subnets, with route tables + IGW/NAT GW for north-south, **SGs (stateful) vs NACLs (stateless)** for policy, and **peering vs Transit Gateway vs PrivateLink** for inter-network reach.
3. **Overlays decouple tenant addressing from the physical underlay** via encapsulation (**VXLAN/GENEVE/GRE/IP-in-IP**). Always budget the **MTU** for encapsulation overhead — the silent cause of "big packets hang."
4. **Kubernetes mandates a flat, NAT-free pod network**; CNI plugins implement it (overlay vs native), kube-proxy load-balances Services (**iptables → IPVS → eBPF** as you scale), and Ingress/Gateway is the L7 north-south door.
5. **East-west traffic dominates microservices and was historically untrusted** — service mesh (mTLS) and **NetworkPolicy** retrofit zero-trust microsegmentation onto it.
6. **CIDR-plan deliberately:** one non-overlapping /16 per VPC, contiguous per-AZ super-blocks, public-small/private-large, >50% reserved, and size private subnets for **pod density** under native CNI.
7. **Trace the packet to understand the system:** pod→pod crosses veth → bridge → VTEP (**encapsulate**) → underlay → VTEP (**decapsulate**) → bridge → veth, with **no NAT between pods** (NAT only for north-south egress and Service VIPs). eBPF/native routing collapses the encap hops.

> Read next: [08 — Network Performance & Tuning](08_network_performance_tuning.md) for the data-path mechanics (XDP, offloads, BDP) behind eBPF load balancing and overlay throughput, and [09 — Network Security](09_network_security.md) for the threat model these segmentation and mTLS controls answer.
