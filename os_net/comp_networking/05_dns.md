# 05 — DNS

> **Audience:** staff/principal. You've added an `A` record and waited for it to propagate. This doc is about how the Domain Name System *actually* resolves a name — the hierarchical delegation from root to authoritative server, the on-the-wire message format, caching and TTL economics, how DNS quietly became the Internet's load balancer and its single largest blast-radius SPOF, and the security holes (Kaminsky, cache poisoning) that DNSSEC and DoH/DoT try to close.
>
> **Primary sources:** RFC 1034 (concepts) & RFC 1035 (implementation/message format); RFC 2181 (clarifications); RFC 6891 (EDNS0); RFC 7766 (DNS-over-TCP); RFC 4033–4035 (DNSSEC); RFC 7858 (DoT); RFC 8484 (DoH); RFC 2308 (negative caching); Kurose & Ross ch. 2; Stevens, *TCP/IP Illustrated Vol. 1* ch. 14; Grigorik, *High Performance Browser Networking* ch. on DNS; Cloudflare 1.1.1.1 engineering posts; the Dyn October 2016 DDoS post-mortem.

---

## 1. What DNS solves

Humans use names (`api.example.com`); IP routing uses addresses (`93.184.216.34`, `2606:2800:...`). DNS is the **globally distributed, hierarchical, cached database** that maps between them — and much more (mail routing, service discovery, policy records).

Why not a single flat file? The original `HOSTS.TXT`, distributed by SRI-NIC, did exactly that — and collapsed under growth: one file, one authority, no caching, every change a manual edit downloaded by every host. DNS (Mockapetris, 1983) replaced it with three design properties that still define it:

1. **Hierarchical namespace** → delegation, so no single entity owns the whole tree.
2. **Distributed authority** → each zone is administered independently.
3. **Aggressive caching with TTLs** → most lookups never reach an authoritative server.

DNS is the canonical example of a system that scales by **hierarchy + delegation + caching** — the same pattern you'd reach for designing any planet-scale name service.

---

## 2. The hierarchical namespace

A domain name is read **right-to-left**, most-significant label last. The trailing dot is the (usually implicit) **root**.

```
                              . (root)
                   ┌──────────┼───────────┐
                  com        org          edu     ...   (TLDs)
              ┌────┤
         example   google                                (2nd level / registrable)
          ┌───┤
        api   www                                         (subdomains)
```

A **zone** is a contiguous portion of the tree administered as a unit, served by **authoritative name servers**. Delegation happens at zone cuts: the `com` servers don't *know* `api.example.com`; they know the **NS records** that say "ask example.com's servers." This delegation is the whole trick — it bounds what any one server must know.

| Tier | Who runs it | Knows |
|---|---|---|
| **Root** | 13 named root server *identities* (`a.root-servers.net` … `m`), each an anycast cloud of hundreds of instances | the TLD name servers (`.com`, `.org`, `.io`, …) |
| **TLD / registry** | Verisign (`.com`), PIR (`.org`), country registries (`.uk`) | the authoritative NS for each registered 2nd-level domain |
| **Authoritative** | the domain owner (or their DNS provider: Route 53, Cloudflare, NS1) | the actual records (`A`, `AAAA`, `MX`, …) for the zone |

> There are 13 root *server letters* (a–m), a historical limit from fitting the NS set in a single 512-byte UDP packet — **not** 13 physical machines. Each letter is **anycast** (§9) to hundreds of sites worldwide.

---

## 3. The resolution flow

Two roles do the work:

- **Stub resolver** — the tiny client in your OS/app (`getaddrinfo`). It asks *one* recursive resolver and trusts the answer. It does **recursive** queries ("give me the final answer").
- **Recursive resolver** (a.k.a. recursive/caching name server) — your ISP's, or a public one (1.1.1.1, 8.8.8.8). It does the legwork: walking the hierarchy with **iterative** queries and caching everything.

```
Stub          Recursive            Root        .com TLD       example.com
resolver      resolver             servers     servers        authoritative
(your OS)     (1.1.1.1)
   │              │                    │            │               │
   │ "A api.example.com?" (recursive)  │            │               │
   │─────────────►│                    │            │               │
   │              │ "A api.example.com?" (iterative)│               │
   │              │───────────────────►│            │               │
   │              │  "ask .com NS"      │            │               │
   │              │◄───────────────────│            │               │
   │              │ "A api.example.com?"             │               │
   │              │───────────────────────────────► │               │
   │              │  "ask example.com NS @1.2.3.4"   │               │
   │              │◄─────────────────────────────────               │
   │              │ "A api.example.com?"                             │
   │              │────────────────────────────────────────────────►│
   │              │  "api.example.com A = 93.184.216.34, TTL 300"    │
   │              │◄─────────────────────────────────────────────────
   │  "93.184.216.34" (cached at every level above)                 │
   │◄─────────────│                    │            │               │
```

Key distinction:

- **Recursive query**: "do whatever it takes, give me the final answer." Stub → recursive resolver.
- **Iterative query**: "give me the best you have — the answer, or a *referral* to who's closer." Recursive resolver → root/TLD/authoritative. Each server answers with either the record or an NS referral one level down.

A *cold* lookup is ~4 round-trips (root → TLD → auth → answer). A *warm* one is a single cache hit at the recursive resolver — which is why caching (§6) is the difference between DNS being usable and being the slowest hop in every connection.

---

## 4. Record types

| Type | Maps | Notes |
|---|---|---|
| **A** | name → IPv4 (32-bit) | the workhorse |
| **AAAA** | name → IPv6 (128-bit) | "quad-A" |
| **CNAME** | name → *another name* (canonical alias) | the target is then resolved; **a CNAME cannot coexist with other records at the same name** (no CNAME at zone apex — use ALIAS/ANAME or flattening) |
| **MX** | domain → mail server name + **preference** | lower preference = higher priority; targets must be A/AAAA, not CNAME |
| **TXT** | name → free text | SPF, DKIM, domain-ownership verification, ACME challenges |
| **NS** | zone → authoritative name server name | *the* delegation record; appears in both parent (referral) and child (authoritative) |
| **SOA** | zone → "start of authority" | one per zone: primary NS, admin email, **serial**, refresh/retry/expire, and **minimum TTL** (used for negative caching, §6.2) |
| **PTR** | IP → name (reverse DNS) | lives under `in-addr.arpa` (v4) / `ip6.arpa` (v6); used by mail anti-spam, logging |
| **SRV** | `_service._proto.name` → host + port + priority + weight | generic service discovery (SIP, XMPP, LDAP, Kubernetes) |
| **CAA** | domain → which CAs may issue certs | issuance policy; checked by CAs |

### 4.1 SOA in detail

```text
example.com.  IN  SOA  ns1.example.com. hostmaster.example.com. (
    2024061501   ; serial   (bump on every change; secondaries compare)
    7200         ; refresh  (secondary re-checks primary every 2h)
    3600         ; retry    (retry if refresh failed)
    1209600      ; expire   (secondary stops answering if primary unreachable 14d)
    300 )        ; minimum  (negative-cache TTL for NXDOMAIN, RFC 2308)
```

The **serial** drives zone transfers (AXFR/IXFR): a secondary that sees a higher serial pulls the updated zone.

---

## 5. The DNS message format (RFC 1035)

One format for queries *and* responses — the QR bit distinguishes them.

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      ID (16-bit txn id)                       |  HEADER
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|QR|  Opcode   |AA|TC|RD|RA| Z|AD|CD|    RCODE      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    QDCOUNT (# questions)                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    ANCOUNT (# answers)                        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|       NSCOUNT (# authority)   |     ARCOUNT (# additional)    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    QUESTION section (QNAME, QTYPE, QCLASS)     |
|                    ANSWER section (resource records)          |
|                    AUTHORITY section (NS records)             |
|                    ADDITIONAL section (glue, EDNS0 OPT)       |
```

- **ID** — 16-bit transaction ID; matches reply to query (security-critical, §8).
- **Flags** — `QR` (query/response), `Opcode`, `AA` (authoritative answer), `TC` (truncated → retry over TCP), `RD` (recursion desired), `RA` (recursion available), `AD`/`CD` (DNSSEC authenticated/checking-disabled), `RCODE` (0=NOERROR, 2=SERVFAIL, 3=NXDOMAIN).
- **QNAME encoding**: a sequence of length-prefixed labels ending in a zero byte. `api.example.com` → `\x03api\x07example\x03com\x00`.
- **Name compression**: a label may be replaced by a 2-byte pointer (`0xC0 | offset`) to an earlier name in the message — vital to fit answers in small packets.

---

## 6. Transport, the 512-byte limit & EDNS0

### 6.1 UDP first, TCP fallback

DNS is overwhelmingly **UDP/53**: one request, one response, no handshake — exactly the request/response-fits-in-a-datagram case UDP was built for (see [04](04_transport_tcp_udp.md) §2.3). The classic limit: a UDP DNS message was capped at **512 bytes** (a number chosen to survive any path without IP fragmentation). If a response exceeds the negotiated size, the server sets the **TC (truncated)** bit and the resolver **retries over TCP/53**, which has no such limit.

### 6.2 EDNS0 (RFC 6891)

512 bytes is far too small for DNSSEC signatures, IPv6 glue, and large NS sets. **EDNS0** adds a pseudo-record (the **OPT** RR in the additional section) that lets a resolver advertise a larger UDP buffer (e.g., 1232 or 4096 bytes) and carry extended flags/RCODEs. Modern resolvers default to ~1232 bytes (chosen to avoid IPv6 fragmentation). Without EDNS0, big responses force TCP fallback; with it, most fit in one UDP packet.

> The DNS amplification DDoS abuses exactly this: a tiny spoofed-source query yields a huge EDNS0/DNSSEC response aimed at the victim — a ~50× amplification factor. Mitigations: response-rate limiting, BCP38 source filtering.

### 6.3 Caching & TTLs

Every record carries a **TTL** (seconds). A recursive resolver caches the record and serves it from cache until the TTL expires. This is what makes DNS fast and survivable:

- **Low TTL (30–60s)** → fast failover/migration, but more queries hitting authoritative servers and slower client behavior; you depend on resolvers *honoring* short TTLs (many clamp to a minimum).
- **High TTL (hours/days)** → cheap, resilient to authoritative outages, but a change takes that long to "propagate" (really: to expire from caches).

**Negative caching (RFC 2308):** NXDOMAIN/no-data answers are *also* cached, for the duration of the zone's SOA `minimum` field. This stops a typo or a probing client from hammering authoritative servers — but it also means a *just-created* record can be shadowed by a cached negative answer until that TTL expires (the "I created the record but it still says NXDOMAIN" gotcha).

---

## 7. DNS as the Internet's traffic director

Because every connection starts with a name lookup, DNS is a natural **control point** for steering traffic — and most large-scale load balancing/failover lives here.

| Technique | How | Trade-off |
|---|---|---|
| **Round-robin DNS** | return multiple A records; clients pick (often the first, or randomize) | crude balancing; no health awareness; client caching skews it |
| **DNS failover** | health-check backends; pull a dead IP from the answer set | bounded by **TTL** — clients keep the dead IP until cache expires |
| **GeoDNS** | answer differs by the resolver's (or client's, via EDNS Client Subnet) location | sends users to the nearest region/PoP |
| **Latency-based routing** | answer with the lowest-measured-latency endpoint (Route 53 LBR) | requires latency telemetry per resolver |
| **Weighted routing** | split traffic by weight (canary, blue/green) | coarse; cache + resolver pooling blur the ratio |

The fundamental limit of all DNS-based steering: **you control the answer, not the client's cache.** Set TTLs to balance failover speed against authoritative load. For sub-second failover you need an **anycast VIP** (route-level steering) or an application-layer load balancer, not DNS — DNS failover is minutes-grained at best.

### 7.1 CDNs & DNS

A CDN like Cloudflare/Akamai/Fastly works by making your hostname a **CNAME to the CDN's name**, then answering that name with the **nearest edge PoP** via GeoDNS/anycast. The CDN's authoritative servers do per-resolver geo/latency mapping (refined by **EDNS Client Subnet**, which forwards a truncated client IP so the CDN sees roughly *where the client is*, not just where its resolver is). This is why "CDN" and "smart DNS" are inseparable.

---

## 8. DNS security

DNS was designed in a trusting era: **no authentication, plaintext, UDP, a 16-bit ID**. That is a lot of attack surface.

### 8.1 Cache poisoning & the Kaminsky attack

If an attacker can get a forged response accepted by a recursive resolver, they poison its cache — every downstream client is now sent to the attacker's IP. The forgery must match the **16-bit transaction ID** *and* the **source port** of the outstanding query, arriving before the legitimate answer.

**Kaminsky (2008)** made this devastatingly practical: instead of racing one record, the attacker queries for random non-existent subdomains (`aaaa.bank.com`, `aaab.bank.com`, …) and floods forged referrals that *also* set the **NS/glue** for `bank.com` itself. Each attempt is a fresh race with no negative-cache penalty, so the attacker gets effectively unlimited tries to win the 16-bit-ID race and hijack the *entire* zone's delegation.

**Mitigation (pre-DNSSEC):** **source-port randomization** — adding ~16 bits of entropy turns a 1-in-65k race into ~1-in-4-billion. Necessary but not sufficient.

### 8.2 DNSSEC (RFC 4033–4035)

DNSSEC adds **origin authentication and integrity** (not confidentiality) via a **chain of cryptographic signatures** rooted at the signed root zone:

```
   Root KSK (trust anchor, baked into resolvers)
        │ signs
   .com DS  ──►  .com ZSK ──► signs .com records (incl. example.com DS)
        │
   example.com DS ──► example.com ZSK ──► signs A/AAAA/MX RRsets (RRSIG)
```

New record types: **RRSIG** (signature over an RRset), **DNSKEY** (the zone's public keys: KSK signs ZSK, ZSK signs records), **DS** (a hash of the child's KSK, held in the *parent* — this is the delegation-of-trust link), **NSEC/NSEC3** (signed proof of *non-existence*, so even NXDOMAIN is authenticated). A validating resolver walks the chain root → TLD → zone; any break is `SERVFAIL`.

DNSSEC's downsides are why adoption lags: complex key management/rollover, larger responses (driving EDNS0/TCP), NSEC zone-walking (mitigated by NSEC3), and operational fragility (an expired signature takes the zone *down*).

### 8.3 DoT and DoH — encrypting the stub→resolver hop

DNSSEC authenticates records but leaves the query **in plaintext** — anyone on-path sees every name you look up, and can tamper or block. Two protocols encrypt the stub↔resolver channel:

| | **DoT** (DNS-over-TLS, RFC 7858) | **DoH** (DNS-over-HTTPS, RFC 8484) |
|---|---|---|
| Transport | TLS on dedicated port **853** | HTTPS on **443** (looks like web traffic) |
| Visibility | network can *see* it's DNS (port 853) and block it | blends into HTTPS; hard to block/censor |
| Controversy | enterprise-friendly (identifiable, policy-able) | bypasses enterprise/parental DNS policy; centralizes to browser-chosen resolvers |

Both protect confidentiality and integrity on the *first hop*; they are orthogonal to DNSSEC (which protects authenticity end-to-end from the zone). Defense in depth uses both.

### 8.4 Split-horizon DNS

The same name resolves **differently depending on who asks** — typically internal clients get RFC 1918 private addresses (`db.corp.example.com → 10.0.5.4`) while external clients get a public VIP or NXDOMAIN. Implemented with views keyed on source subnet. Common in enterprises; the operational hazard is the two views drifting out of sync, producing "works from the office, fails from VPN" mysteries.

---

## 9. Anycast for resolvers

Public resolvers (Cloudflare **1.1.1.1**, Google **8.8.8.8**, Quad9 **9.9.9.9**) and the root/TLD servers all use **anycast** (see [03](03_network_layer_routing.md)): the *same* IP is announced via BGP from hundreds of PoPs worldwide, and the routing system delivers each client to the topologically nearest instance. Benefits:

- **Latency** — your query goes to a nearby PoP, not a single far-away box.
- **DDoS resilience** — attack traffic is spread across the whole anycast cloud; one PoP absorbing a flood doesn't take the service down (this is also how the root servers survive constant attack).
- **Operational simplicity for clients** — one memorable IP, globally.

This is why "13 root servers" can serve the planet: each letter is an anycast constellation of hundreds of physical nodes.

---

## 10. Working code — building & parsing DNS on raw sockets

This is a stdlib-only DNS client: it **builds** a query packet by hand, sends it over UDP/53, and **parses** the response (including name compression). No `dnspython` required — it shows the wire format from §5 concretely.

```python
"""
mini_resolver.py — build and parse a DNS A-record query using only the stdlib.
Demonstrates the RFC 1035 wire format: header, QNAME label encoding, and
answer parsing with 0xC0 name-compression pointers.

Usage:  python mini_resolver.py example.com 1.1.1.1
"""
import socket
import struct
import sys
import random


def build_query(qname: str, qtype: int = 1) -> tuple[bytes, int]:
    """Build a DNS query for qname. qtype 1 = A. Returns (packet, txn_id)."""
    txn_id = random.randint(0, 0xFFFF)            # 16-bit ID (match the reply)
    flags = 0x0100                                 # RD=1 (recursion desired)
    header = struct.pack(">HHHHHH", txn_id, flags, 1, 0, 0, 0)  # QDCOUNT=1

    # QNAME: each label length-prefixed, terminated by a zero byte.
    qname_bytes = b"".join(
        bytes([len(label)]) + label.encode() for label in qname.split(".")
    ) + b"\x00"
    question = qname_bytes + struct.pack(">HH", qtype, 1)        # QTYPE, QCLASS=IN
    return header + question, txn_id


def read_name(msg: bytes, offset: int) -> tuple[str, int]:
    """Parse a (possibly compressed) DNS name. Returns (name, next_offset).
    A pointer (top two bits set, 0xC0) jumps elsewhere in the message."""
    labels = []
    jumped = False
    next_offset = offset
    while True:
        length = msg[offset]
        if length & 0xC0 == 0xC0:                  # compression pointer
            pointer = ((length & 0x3F) << 8) | msg[offset + 1]
            if not jumped:
                next_offset = offset + 2           # resume after the 2-byte ptr
            offset = pointer
            jumped = True
            continue
        if length == 0:                            # end of name
            offset += 1
            if not jumped:
                next_offset = offset
            break
        labels.append(msg[offset + 1: offset + 1 + length].decode())
        offset += 1 + length
    return ".".join(labels), next_offset


def parse_response(msg: bytes, expected_id: int) -> list[str]:
    txn_id, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", msg[:12])
    if txn_id != expected_id:
        raise ValueError("transaction ID mismatch (possible spoof / stale reply)")
    rcode = flags & 0x0F
    if rcode == 3:
        raise ValueError("NXDOMAIN")
    if rcode != 0:
        raise ValueError(f"server error, RCODE={rcode}")

    offset = 12
    for _ in range(qd):                            # skip the echoed question(s)
        _, offset = read_name(msg, offset)
        offset += 4                                # QTYPE + QCLASS

    addresses = []
    for _ in range(an):                            # parse answer RRs
        _, offset = read_name(msg, offset)
        rtype, rclass, ttl, rdlength = struct.unpack(">HHIH", msg[offset:offset + 10])
        offset += 10
        rdata = msg[offset:offset + rdlength]
        if rtype == 1 and rdlength == 4:           # A record
            addresses.append(".".join(str(b) for b in rdata))
        offset += rdlength
    return addresses


def resolve(qname: str, server: str = "1.1.1.1") -> list[str]:
    packet, txn_id = build_query(qname)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    try:
        sock.sendto(packet, (server, 53))          # UDP/53
        response, _ = sock.recvfrom(4096)
        # If the TC (truncated) bit were set you would retry over TCP/53 here.
        return parse_response(response, txn_id)
    finally:
        sock.close()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "example.com"
    resolver = sys.argv[2] if len(sys.argv) > 2 else "1.1.1.1"
    print(f"{name} A records via {resolver}:")
    for addr in resolve(name, resolver):
        print("  ", addr)
```

What it demonstrates: the 12-byte header with the RD flag, the length-prefixed QNAME encoding, transaction-ID matching (the anti-spoof check from §8.1), and crucially **name-compression pointer** handling (`0xC0`) in `read_name` — the part most hand-rolled parsers get wrong.

### 10.1 The same walk with `dig +trace`

```bash
# +trace makes dig do the iterative walk itself, root -> TLD -> authoritative,
# showing each referral instead of asking one recursive resolver:
dig +trace api.example.com

#  ; (1) query the ROOT servers -> referral: ".com NS a.gtld-servers.net ..."
#  com.            172800  IN  NS  a.gtld-servers.net.
#  ; (2) query a .com TLD server -> referral: "example.com NS ns1.example.com ..."
#  example.com.    172800  IN  NS  ns1.example.com.
#  ; (3) query example.com's authoritative server -> the ANSWER (AA bit set):
#  api.example.com. 300    IN  A   93.184.216.34

# Useful companions:
dig api.example.com               # normal recursive query via your resolver
dig +short MX example.com         # just the mail servers
dig +dnssec example.com           # request RRSIG/DNSKEY (see the signatures)
dig @8.8.8.8 example.com          # force a specific resolver
dig -x 93.184.216.34              # reverse (PTR) lookup
```

`dig +trace` is the single best teaching/diagnosis tool for delegation problems: it shows you *exactly* which level returns the wrong (or no) NS referral.

### 10.2 A health-check + DNS-failover pattern

```text
COMPONENTS
  - authoritative DNS provider with a health-check API (Route 53, NS1, Cloudflare)
  - two backend VIPs:  primary 203.0.113.10   secondary 198.51.100.20
  - record:  api.example.com  A  with TTL = 60s   (short, for fast failover)

CONTROL LOOP (runs on the DNS provider, every ~10–30s)
  every interval:
      probe HTTP(S) GET https://203.0.113.10/healthz   (the primary)
      if 3 consecutive probes fail:
          mark primary UNHEALTHY
          update the api.example.com answer set -> [198.51.100.20]  (secondary)
      if primary recovers (N consecutive OK):
          restore answer set -> [203.0.113.10]

CLIENT-OBSERVED FAILOVER TIME ≈ probe_detection_time + TTL
  = (3 * interval) + 60s   ->  ~minutes, NOT seconds

WHY TTL IS THE FLOOR
  Even the instant the provider swaps the answer, resolvers and OS stub caches
  keep serving the dead IP until their cached record's TTL expires. You cannot
  push an invalidation to the world's resolvers.

IF YOU NEED SUB-SECOND FAILOVER
  Do it at the routing/LB layer, not DNS:
    - anycast the SAME VIP from both sites; withdraw the BGP route on failure
      (reconvergence is seconds, independent of any DNS TTL), OR
    - put a stateful L4/L7 load balancer in front and fail over behind ONE VIP.
  Keep DNS TTLs modest (60s) as the coarse outer layer; use route/LB failover
  as the fast inner layer.
```

---

## 11. Enterprise failure modes

- **DNS as a SPOF / the Dyn DDoS (Oct 21, 2016).** A massive Mirai-botnet DDoS against **Dyn**, a managed DNS provider, took down resolution for Twitter, GitHub, Netflix, Reddit, Spotify and more — *not* because those sites were down, but because nobody could **resolve their names**. The lesson burned into every SRE since: **DNS is a top-of-funnel dependency for everything**, so run **secondary DNS on a second, independent provider** (delegate NS records to both). DNS is the most-shared, highest-blast-radius dependency you have.
- **Low-TTL trade-offs.** Cutting TTL to 5s for "fast failover" multiplies authoritative query load (and cost), increases client-perceived latency on cache misses, and many resolvers *clamp* TTLs to a floor anyway — so you pay the cost without getting the speed. Match TTL to your real failover mechanism (§10.2).
- **Negative-cache surprises.** A query for a not-yet-created record gets NXDOMAIN cached for the SOA `minimum`; the record then "doesn't propagate" until that negative TTL expires. Pre-create records, or keep the negative TTL low during migrations.
- **CNAME-at-apex breakage.** You cannot put a CNAME at the zone apex (`example.com` needs SOA/NS there). Using a naive CNAME breaks the zone; use the provider's ALIAS/ANAME/flattening.
- **Resolver doesn't honor your TTL.** Browsers, JVMs (historically cached forever via `networkaddress.cache.ttl`), and connection pools cache independently of DNS TTL. "I lowered the TTL but old clients still hit the dead IP" is usually app-layer caching, not DNS.
- **Split-horizon drift.** Internal and external views diverge → "works in the office, broken on VPN." Treat the two views as one artifact under the same change control.

---

## 12. Advanced: encrypted DNS, negative caching, service discovery, and Happy Eyeballs

### Encrypted DNS — DoT, DoH, DoQ

Classic DNS is plaintext UDP ([§6](#6-transport-the-512-byte-limit--edns0)) — anyone on
path can see and tamper with queries. Three encryptions now exist:

- **DoT** (DNS over TLS, port 853) — DNS in a TLS tunnel; easy for networks to
  identify/manage (its own port).
- **DoH** (DNS over HTTPS, port 443) — DNS inside HTTPS, indistinguishable from web
  traffic; great for user privacy, but **bypasses enterprise DNS controls** (split-
  horizon, filtering) — a real operational tension.
- **DoQ** (DNS over QUIC) — DNS over QUIC, avoiding TCP head-of-line blocking.

Note these protect the query *in transit*; **DNSSEC** ([§8](#8-dns-security)) protects
*integrity/authenticity* of the answer. They're complementary, not substitutes.

### Negative caching and TTL strategy

Resolvers cache **failures** too — `NXDOMAIN`/no-data responses are cached for the time
in the zone's **SOA minimum** field (RFC 2308). Two consequences: a too-long negative
TTL means a newly-created record stays "missing" for a while (the "I added the record
but it's still NXDOMAIN" confusion); and negative caching is what protects authoritative
servers from floods of lookups for non-existent names. TTL is the core DNS lever:
**short TTL** = fast failover/changes but more query load and resolver dependence;
**long TTL** = resilient and cheap but slow to change — and during the Dyn 2016 outage,
records with long TTLs survived while short-TTL records went dark first.

### DNS for service discovery

Inside data centers and clouds, DNS *is* the service-discovery layer: **SRV records**
advertise host+port+priority+weight for a service; Kubernetes gives every Service a DNS
name (`svc.namespace.svc.cluster.local`) backed by CoreDNS; Consul/etcd expose DNS
interfaces. This is why DNS query volume and latency are *internal* SLO concerns, and
why the `ndots`/CoreDNS issues in
[scenarios 04.6](../enterprise_scenarios/04_network_incidents.md) hit so hard — every
internal call may start with a lookup.

### Happy Eyeballs — dual-stack without the timeout

A dual-stack client given both AAAA (IPv6) and A (IPv4) records that naively tries IPv6
first and waits for it to time out gives users a terrible experience on broken-IPv6
networks. **Happy Eyeballs** (RFC 8305) races IPv4 and IPv6 connection attempts with a
small head start for IPv6 and uses whichever connects first — making IPv6 deployment
safe. It's why "enable IPv6" no longer risks latency regressions for users on flaky
v6 paths.

---

## 13. Trade-offs summary

- **Hierarchy + delegation + caching** is *the* scaling pattern; DNS is its reference implementation. Each server only needs to know its zone plus where to delegate.
- **Recursive (do it all) vs iterative (answer-or-referral)** is the division of labor between resolver and authoritative servers.
- **TTL is the master dial:** low = fast change/failover + high load + cache-honoring risk; high = cheap/resilient + slow change. DNS failover is *minutes*, not seconds — pair it with route/LB failover for speed.
- **UDP/53 with the 512-byte limit → TCP fallback or EDNS0;** DNSSEC and IPv6 make EDNS0 effectively mandatory.
- **Security is bolted on:** source-port randomization blunts Kaminsky; **DNSSEC** authenticates records (not confidentiality); **DoT/DoH** encrypt the first hop. Use DNSSEC + DoH/DoT together.
- **DNS is the highest-blast-radius dependency you own** (Dyn 2016) — multi-provider secondary DNS is table stakes for anything that must stay up.

## 14. Key Takeaways

1. DNS scales by **hierarchical delegation + aggressive caching**: root → TLD → authoritative, with TTL-bounded caching at recursive resolvers absorbing nearly all queries.
2. **Stub → recursive (recursive query) → root/TLD/auth (iterative queries)** is the resolution flow; a cold lookup is ~4 RTT, a warm one is a single cache hit.
3. Know your records cold: **A/AAAA, CNAME (not at apex), MX, TXT, NS, SOA (drives TTL/transfers/negative caching), PTR, SRV, CAA.**
4. The **RFC 1035 message** is one format with a QR bit; **name compression (0xC0 pointers)** and the **TC bit → TCP fallback** (or **EDNS0** for bigger UDP) are the wire-format facts that bite implementers.
5. **TTL is the central trade-off** and the floor on DNS-based failover; for sub-second failover use **anycast/route withdrawal or an LB VIP**, not DNS.
6. DNS is the Internet's de-facto **load balancer/GeoDNS/CDN steering layer** — you control the answer, not the client's cache.
7. **Anycast** (1.1.1.1, 8.8.8.8, the root servers) delivers low latency and DDoS resilience from one IP.
8. Security: **Kaminsky** made cache poisoning practical → **source-port randomization** + **DNSSEC** (authenticity) + **DoT/DoH** (confidentiality). **DNS is the largest shared SPOF** (Dyn 2016) — run multi-provider secondary DNS.

> Read next: the HTTP doc for how a resolved address turns into a TLS+HTTP/2 or HTTP/3 (QUIC) connection, and [04 — Transport: TCP & UDP](04_transport_tcp_udp.md) for why DNS chose UDP and when it falls back to TCP.
