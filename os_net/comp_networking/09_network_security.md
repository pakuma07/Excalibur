# 09 — Network Security

> **Audience:** staff/principal. You can read a packet capture and you know what TLS is for. This doc is about *the network threat model and the layered defenses that answer it* — firewalls, segmentation, zero trust, DDoS, VPNs, and the protocol-level attacks (ARP/DNS/BGP/amplification) that still take down real services — reasoned from first principles, with runnable code where it teaches.
>
> **Primary sources:** Kurose & Ross, *Computer Networking* (ch. 8 Security); Gregg, *Systems Performance* / *BPF Performance Tools* (XDP DDoS drop); the Cloudflare and AWS engineering blogs (Mirai/Dyn 2016, the 2018 memcached amplification, anycast scrubbing); BeyondCorp papers (Google, 2014–2017); RFC 4301 (IPsec), RFC 4253 (SSH transport), RFC 4987 (SYN flood / SYN cookies), RFC 2827/BCP38 (ingress filtering); NIST SP 800-207 (Zero Trust Architecture).

---

## 1. Why this matters at scale

Network security is **adversarial systems engineering**: every assumption your protocols make is something an attacker will violate on purpose. At scale three truths dominate:

1. **The network is hostile by default.** Any unauthenticated, unencrypted byte on a shared medium can be read, forged, replayed, or redirected. "It's on our internal VLAN" is not a security property — it's the *first* thing an attacker pivots through. This is the entire motivation for **zero trust**.
2. **Availability is a security property.** A correct, encrypted service that is knocked offline by a DDoS has failed its users exactly as badly as one that was breached. DDoS defense is security, not just SRE.
3. **Defense is layered or it is absent.** No single control is sufficient — a firewall doesn't stop a stolen credential, TLS doesn't stop a volumetric flood, and a WAF doesn't stop BGP hijacking. **Defense in depth** is the only design that survives the failure of any one layer.

Staff engineers are expected to articulate a **threat model** (who, what they can do, what they want), map each threat to a specific layer of defense, and know which classic attacks (SYN flood, ARP spoofing, DNS poisoning, BGP hijack, amplification) are still live and what actually mitigates them.

---

## 2. The threat model

A threat model names the **adversary's capabilities** and **goals**, so you can reason about which defenses matter. The canonical network adversary (Dolev-Yao) controls the network: they can observe, inject, modify, drop, and replay any message.

| Threat | Adversary capability | Goal | Primary defense |
|---|---|---|---|
| **Eavesdropping** | read packets on the path (tap, mirror, rogue AP, compromised hop) | confidentiality breach | **encryption** (TLS, IPsec, WireGuard) |
| **Spoofing** | forge a source address/identity | impersonate a host/user | **authentication** (mTLS, signatures), BCP38 ingress filtering |
| **MITM** | sit between two parties, relay+modify | read/alter a "secure" channel | **authenticated key exchange** + cert validation (PKI), pinning |
| **Replay** | capture a valid message, resend it later | repeat a privileged action | **nonces, timestamps, sequence numbers** (§9) |
| **Tampering** | modify packets in flight | corrupt data/commands | **integrity** (MAC/AEAD, HMAC) |
| **DoS / DDoS** | exhaust a resource (bandwidth, CPU, state, conntrack) | deny availability | rate limiting, SYN cookies, anycast scrubbing (§5) |

> **CIA + Authenticity + Availability.** Confidentiality (encryption), Integrity (MACs), Availability (DDoS defense), Authenticity (signatures/certs). Map every defense you deploy to which of these it provides — and note that **encryption alone provides none of integrity, authenticity, or availability**. That is why we use **AEAD** (encryption + integrity together) and separate auth and anti-DDoS layers.

---

## 3. Firewalls — stateless vs stateful packet filtering

A firewall decides, per packet, **allow / drop / reject** based on a policy. The pivotal distinction is whether it remembers connections.

### 3.1 Stateless vs stateful

| | **Stateless** (packet filter) | **Stateful** (connection tracking) |
|---|---|---|
| Decision basis | each packet in isolation (5-tuple, flags) | the packet **+ the connection's state** |
| "Allow replies to my outbound conn" | impossible without opening the whole port range | automatic — the reply matches an established flow |
| State held | none | a **conntrack** table entry per flow |
| Cost / risk | cheap, no memory; clumsy rules | per-flow memory → **conntrack-table-exhaustion DoS** |
| Example | ACLs on a router; `iptables` without `-m state` | Linux `nf_conntrack`, AWS security groups |

A stateful firewall tracks each connection's state machine (NEW → ESTABLISHED → RELATED → … ) so a rule like "allow established/related" lets return traffic in without exposing anything. This is why modern firewalls are stateful — but it also means the **conntrack table is a finite resource an attacker can exhaust** (see §5 protocol DoS).

### 3.2 conntrack

```bash
# Linux connection-tracking table size and current usage
sysctl net.netfilter.nf_conntrack_max          # max tracked flows
cat /proc/sys/net/netfilter/nf_conntrack_count  # current
conntrack -L                                    # list live flows (conntrack-tools)
conntrack -S                                    # per-CPU stats: insert_failed, drop, early_drop
```

If `nf_conntrack_count` approaches `nf_conntrack_max`, new legitimate connections are dropped — a real outage mode under connection floods. Size the table for your peak flow count and watch `insert_failed`.

### 3.3 iptables vs nftables

`iptables` (legacy) and `nftables` (its replacement) are the userspace tools that program the kernel's netfilter hooks. `nftables` unifies IPv4/IPv6, has a cleaner syntax, atomic ruleset replacement, and **sets/maps** for O(1) matching of large lists (e.g., a blocklist of 100k IPs without 100k linear rules).

```text
netfilter hook chain (a packet's journey through the kernel firewall):

  RX -> [PREROUTING] -> routing -> [INPUT]  -> local process
                                \-> [FORWARD] -> [POSTROUTING] -> TX
        local process -> [OUTPUT] -> routing -> [POSTROUTING] -> TX

  DNAT/redirect happens at PREROUTING; SNAT/masquerade at POSTROUTING.
```

### 3.4 A hardened-server ruleset (iptables)

The canonical default-deny inbound posture: drop everything, then allow only what you need. Order matters — rules are evaluated top to bottom, first match wins.

```bash
#!/usr/bin/env bash
# harden-iptables.sh — default-deny inbound, stateful allow. Run as root.
set -euo pipefail

# 1) Flush and set default policies to DROP (fail closed).
iptables -F; iptables -X
iptables -P INPUT   DROP        # deny inbound by default
iptables -P FORWARD DROP        # not a router
iptables -P OUTPUT  ACCEPT      # trust our own egress (tighten in high-sec envs)

# 2) Always allow loopback (apps talk to themselves over 127.0.0.1).
iptables -A INPUT -i lo -j ACCEPT

# 3) Stateful core: allow replies to connections WE initiated, and related
#    (e.g. FTP data, ICMP errors). This is what makes it a stateful firewall.
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -m conntrack --ctstate INVALID -j DROP    # drop malformed/out-of-state

# 4) Anti-spoofing & ICMP hygiene.
iptables -A INPUT -s 127.0.0.0/8 ! -i lo -j DROP            # martian: loopback src on real iface
iptables -A INPUT -p icmp --icmp-type echo-request \
         -m limit --limit 5/s -j ACCEPT                     # rate-limited ping
iptables -A INPUT -p icmp --icmp-type echo-request -j DROP

# 5) SYN-flood mitigation: rate-limit NEW connections per source (see §5.1).
iptables -A INPUT -p tcp --syn -m conntrack --ctstate NEW \
         -m limit --limit 60/s --limit-burst 100 -j ACCEPT
iptables -A INPUT -p tcp --syn -m conntrack --ctstate NEW -j DROP

# 6) The actual services: SSH (rate-limited against brute force), HTTPS.
iptables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW \
         -m recent --set --name SSH
iptables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW \
         -m recent --update --seconds 60 --hitcount 5 --name SSH -j DROP  # >4/min -> drop
iptables -A INPUT -p tcp --dport 22  -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# 7) Log a sample of what we drop (for forensics), then the policy DROPs the rest.
iptables -A INPUT -m limit --limit 2/min -j LOG --log-prefix "iptables-drop: "
```

### 3.5 The same policy in nftables

`nftables` expresses the identical intent more compactly, with named sets and atomic load (`nft -f`):

```bash
#!/usr/sbin/nft -f
# harden.nft — load atomically with: nft -f harden.nft
flush ruleset

table inet filter {
    set ssh_ratelimit { type ipv4_addr; flags dynamic, timeout; timeout 1m; }

    chain input {
        type filter hook input priority 0; policy drop;     # default-deny

        iif "lo" accept
        ct state established,related accept                  # stateful allow
        ct state invalid drop

        ip protocol icmp icmp type echo-request limit rate 5/second accept
        ip protocol icmp icmp type echo-request drop

        # SYN-flood guard: cap NEW TCP conns per second.
        tcp flags syn ct state new limit rate 60/second burst 100 packets accept
        tcp flags syn ct state new drop

        # SSH with per-source brute-force throttle via a dynamic set.
        tcp dport 22 ct state new add @ssh_ratelimit { ip saddr limit rate 5/minute } accept
        tcp dport 443 accept

        limit rate 2/minute log prefix "nft-drop: "
    }
    chain forward { type filter hook forward priority 0; policy drop; }
    chain output  { type filter hook output  priority 0; policy accept; }
}
```

> **Why default-DROP, allow-specific** (and not allow-most, deny-known-bad): a denylist fails *open* — every new service/port you forget is exposed. An allowlist fails *closed* — a forgotten service is simply unreachable until you grant it. Always fail closed at the firewall.

---

## 4. Segmentation & zero-trust networking

### 4.1 Network segmentation

Split the network into zones (VLANs, subnets, security groups) so a breach in one zone cannot freely reach another. The classic "DMZ" puts internet-facing servers in a segment that *cannot* initiate connections into the trusted internal network. **Microsegmentation** takes this to the per-workload level (each service only reaches the specific peers it needs), typically enforced by host firewalls, security groups, or Kubernetes NetworkPolicies (see [10](10_cloud_sdn_overlays.md)).

Segmentation limits **blast radius** and **lateral movement** — the single most important defensive property once you accept that *some* breach is inevitable.

### 4.2 Zero trust (BeyondCorp)

The traditional **perimeter model** ("hard shell, soft interior") assumes that being inside the network = trusted. This fails catastrophically: VPN credential theft, a single compromised laptop, or an insider gives an attacker the soft interior. Google's **BeyondCorp** (2014) reframed it:

> **Zero trust:** trust is never granted based on **network location**. Every request — internal or external — is authenticated, authorized, and encrypted based on **device identity + user identity + context**, not on the source IP or VLAN.

| Perimeter model | Zero trust (BeyondCorp / NIST 800-207) |
|---|---|
| "inside = trusted" | "never trust, always verify" |
| network location grants access | identity + device posture + policy grants access, per request |
| VPN puts you "on the network" | no privileged network; an access proxy authorizes each request |
| flat lateral movement after entry | every hop re-authenticated → lateral movement blocked |

The practical mechanisms: an **identity-aware proxy** in front of every app, strong device certs, short-lived credentials, mTLS between services (see service mesh in [10](10_cloud_sdn_overlays.md)), and continuous authorization. The VPN-as-perimeter is replaced by per-request authorization.

### 4.3 NAT is not security

NAT (Network Address Translation) maps private addresses to public ones and, as a side effect, makes internal hosts unaddressable from outside *unless a mapping exists*. People mistake this for a firewall. It is not:

- NAT provides **no authentication, no integrity, no encryption**, and no policy beyond "I happen to have a mapping."
- Outbound-initiated mappings, UPnP/PCP hole-punching, and NAT-traversal techniques (STUN/TURN) all open paths in.
- IPv6 largely removes NAT, which makes the "NAT = firewall" myth actively dangerous when migrating.

> NAT is an **addressing** mechanism with an incidental obscurity benefit. Put a real stateful firewall and segmentation behind it; never rely on NAT as a security control.

---

## 5. DDoS — types and mitigations

Distributed Denial of Service exhausts a finite resource using many sources. The three families attack different resources:

| Family | Exhausts | Examples | Signature |
|---|---|---|---|
| **Volumetric** | **bandwidth** | UDP/ICMP floods, **amplification/reflection** (DNS, NTP, memcached) | huge bits/s; pipe saturated |
| **Protocol / state** | **connection state / CPU** | **SYN flood**, ACK flood, conntrack exhaustion | high PPS, half-open conns, low bytes |
| **Application-layer (L7)** | **app resources** (DB, threads, CPU) | HTTP floods, "slowloris" slow-read, expensive query floods | looks like real traffic; modest PPS |

### 5.1 SYN flood and SYN cookies

The TCP handshake reserves kernel state when a SYN arrives (the half-open connection in the SYN-RECV backlog), *before* the client proves it can receive (the final ACK). A **SYN flood** sends SYNs with spoofed sources and never completes the handshake, filling the backlog so legitimate SYNs are dropped.

```text
Normal:   client --SYN-->  server   (server allocates SYN-RECV state, sends SYN-ACK)
          client <-SYN-ACK-
          client --ACK-->            (handshake done)

Flood:    attacker --SYN(spoofed src)--> server  x 100,000
          server allocates 100,000 SYN-RECV entries; SYN-ACKs go to forged
          addresses (never answered); backlog full -> real clients rejected.
```

**SYN cookies** (Bernstein, RFC 4987) eliminate the stored state. Instead of remembering the half-open connection, the server **encodes** the connection state into the initial sequence number it sends in the SYN-ACK — a MAC over (client IP/port, server IP/port, timestamp, MSS class) keyed by a server secret. The server then **forgets** the connection entirely. When the final ACK arrives (ack = ISN+1), the server *recomputes and validates the cookie* to reconstruct the state. No backlog → nothing to flood.

```bash
sysctl net.ipv4.tcp_syncookies        # 1 = enable when the backlog overflows (default)
```

> The trade-off: cookies encode only a few bits of TCP options (MSS into a small class, no SACK/timestamp/window-scale negotiation in the cookie itself), so a cookie-completed connection can be slightly suboptimal — fine as an *under-attack* fallback, which is exactly when Linux engages them (only when the backlog overflows).

### 5.2 Amplification / reflection — the volumetric multiplier

The attacker spoofs the **victim's** source address and sends a *small* query to an open server that returns a *large* response — the response is "reflected" to the victim, amplified by the response/request size ratio.

| Protocol | Amplification factor | Note |
|---|---|---|
| DNS (ANY query) | ~28–54× | open resolvers |
| NTP (monlist) | ~556× | the 2014 NTP attacks |
| **memcached** | **~10,000–51,000×** | the **2018 GitHub 1.35 Tbps** attack; UDP/11211 exposed to the internet |

The root enabler is **source-address spoofing**, which is preventable by **BCP38 / ingress filtering** (RFC 2827): every network drops outbound packets whose source address it could not legitimately originate. Universal BCP38 deployment would kill reflection attacks; it remains incompletely deployed, which is why amplification persists.

### 5.3 The 2016 Dyn / Mirai attack

In October 2016 the **Mirai** botnet — ~100k+ compromised IoT devices (cameras, DVRs with default credentials) — directed an L7+volumetric flood at **Dyn**, a managed DNS provider. Because Dyn served DNS for Twitter, GitHub, Netflix, Reddit and others, taking down the *DNS layer* took down all of them at once. Peak was estimated near **1.2 Tbps**.

Lessons that became doctrine:

- **DNS is critical infrastructure** — protect and over-provision it; use multiple independent providers (Dyn customers with a second DNS provider stayed up).
- **IoT default credentials are a systemic, internet-scale risk.**
- **Concentration is fragility** — a shared dependency (managed DNS) is a shared blast radius.

### 5.4 Mitigations — the layered stack

```text
                 (1) ANYCAST + SCRUBBING (absorb volumetric at the edge, globally)
                          |
                 (2) STATELESS FILTER / XDP_DROP (drop spoofed/garbage at line rate)
                          |
                 (3) SYN COOKIES + conntrack limits (survive state exhaustion)
                          |
                 (4) RATE LIMITING / WAF (shed L7 abuse near the app)
                          |
                       your service
```

- **Anycast + scrubbing centers (Cloudflare, AWS Shield, Akamai):** the same IP is announced from many global PoPs; a volumetric flood is *spread across the entire anycast network* (each PoP absorbs a fraction) and "scrubbed" — bad traffic dropped, clean traffic forwarded. This is how providers absorb terabit attacks: distribute, then filter.
- **XDP_DROP** (eBPF, see [08](08_network_performance_tuning.md) §8.3): drop attack packets *before* the kernel allocates an skb — nanoseconds per dropped packet, tens of millions of PPS on commodity hardware. The basis of modern in-host DDoS defense.
- **Rate limiting** at L7 (per-IP, per-token, per-endpoint) sheds application floods that look like real requests.

### 5.5 A token-bucket rate limiter — runnable

```python
"""ratelimit.py — a thread-safe token-bucket rate limiter, the workhorse of L7
DDoS / abuse mitigation. Capacity = burst tolerance; refill_rate = sustained rate.
Run: python ratelimit.py"""
import threading, time

class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float, _now=time.monotonic):
        self.rate = float(rate_per_sec)      # sustained refill rate (tokens/s)
        self.capacity = float(capacity)      # max burst (bucket size)
        self.tokens = float(capacity)        # start full
        self._now = _now
        self.updated = _now()
        self.lock = threading.Lock()

    def _refill(self):
        now = self._now()
        elapsed = now - self.updated
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.updated = now

    def allow(self, cost: float = 1.0) -> bool:
        """Consume `cost` tokens if available; return whether the request is allowed."""
        with self.lock:
            self._refill()
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False

class PerKeyLimiter:
    """One bucket per client key (e.g. source IP / API token)."""
    def __init__(self, rate_per_sec, capacity, _now=time.monotonic):
        self.rate, self.capacity, self._now = rate_per_sec, capacity, _now
        self.buckets, self.lock = {}, threading.Lock()

    def allow(self, key, cost=1.0):
        with self.lock:
            b = self.buckets.get(key)
            if b is None:
                b = self.buckets[key] = TokenBucket(self.rate, self.capacity, self._now)
        return b.allow(cost)

if __name__ == "__main__":
    # Deterministic virtual clock so the test is reproducible (no real sleeping).
    clock = {"t": 0.0}
    now = lambda: clock["t"]
    bucket = TokenBucket(rate_per_sec=10, capacity=5, _now=now)

    # Burst: capacity=5, so the first 5 are allowed instantly, the 6th is not.
    allowed = [bucket.allow() for _ in range(7)]
    assert allowed[:5] == [True] * 5, allowed
    assert allowed[5] is False and allowed[6] is False, allowed

    # After 0.3 s at 10 tokens/s we've refilled 3 tokens -> 3 more allowed.
    clock["t"] += 0.3
    assert sum(bucket.allow() for _ in range(5)) == 3

    # Tokens never exceed capacity even after a long idle.
    clock["t"] += 100.0
    bucket._refill()
    assert bucket.tokens == 5.0

    # Per-key isolation: hammering key "A" must not throttle key "B".
    lim = PerKeyLimiter(rate_per_sec=1, capacity=2, _now=now)
    assert lim.allow("A") and lim.allow("A") and not lim.allow("A")
    assert lim.allow("B"), "a different key must have its own bucket"
    print("rate-limiter assertions passed")
```

---

## 6. TLS / mTLS recap

TLS provides **confidentiality** (encryption), **integrity** (AEAD), and **server authenticity** (the cert chains to a trusted CA). It is the answer to eavesdropping, tampering, and server-side MITM on the application data path.

- **Server TLS:** the client validates the server's certificate (name + chain + expiry + revocation). The client is *not* authenticated by TLS itself.
- **mTLS (mutual TLS):** *both* sides present and validate certificates. This is the cryptographic backbone of **zero trust** service-to-service auth (§4.2) — a service proves its identity with a short-lived cert, not a network position. The service mesh (Istio/Linkerd, see [10](10_cloud_sdn_overlays.md)) issues and rotates these certs automatically.
- **TLS 1.3** removed the legacy RSA key exchange (forward secrecy is now mandatory via ephemeral DH), cut the handshake to 1-RTT (0-RTT on resumption), and dropped broken ciphers.

> Full treatment of the handshake, PKI, certificate transparency, and forward secrecy lives in the cryptography/TLS reference; here the point is *where it fits the threat model*: TLS answers eavesdropping/tampering/MITM on the data path, and mTLS turns it into a mutual identity primitive for zero trust. It does **not** address availability (DDoS) or stolen-credential abuse.

---

## 7. VPNs — IPsec vs WireGuard vs TLS-VPN

A VPN builds an authenticated, encrypted tunnel so two networks (or a client and a network) communicate as if directly connected over a hostile path.

| | **IPsec** | **WireGuard** | **TLS-VPN** (OpenVPN, SSL-VPN) |
|---|---|---|---|
| Layer | L3 (IP), kernel | L3 (IP), kernel | L4 (over TLS/DTLS), userspace usually |
| Crypto agility | huge, negotiable suites (IKE) | **fixed, opinionated** (Curve25519, ChaCha20-Poly1305, BLAKE2s) | TLS cipher suites |
| Codebase | large/complex (decades of options) | **~4k LOC** — auditable, small attack surface | moderate |
| Config model | SAs, IKE phases, policies | public-key peers + allowed-IPs (SSH-like) | certs/PKI + config |
| NAT/firewall traversal | tricky (ESP, NAT-T) | UDP, roams well | excellent (looks like HTTPS over 443) |
| Best for | site-to-site, standards interop | modern point-to-point / mesh, performance | client access through restrictive firewalls |

> **WireGuard's thesis:** crypto agility is a liability (downgrade attacks, complexity, bugs). It picks one modern suite, is small enough to audit, lives in the kernel for speed, and uses an SSH-like public-key model. It has become the default for new deployments where interop with legacy IPsec isn't required. Note the zero-trust caveat (§4.2): a VPN that grants flat network access reintroduces the perimeter model — prefer per-app/per-request authorization over "VPN = on the trusted network."

---

## 8. SSH, IDS/IPS, WAF

### 8.1 SSH

SSH (RFC 4253) provides an authenticated, encrypted channel for remote login, command execution, and tunneling. Its security rests on:

- **Host key verification (TOFU):** the client pins the server's public key on first connect (`known_hosts`). A changed host key = possible MITM (the loud warning). This is the weak point — unverified first-connect is a real MITM window; use SSH certificates or known-hosts distribution to close it.
- **User auth:** prefer **public-key** (or SSH certificates from a CA) over passwords. Disable password auth and root login on hardened servers.
- **Tunneling:** local/remote/dynamic (SOCKS) port forwarding — powerful, and a common exfiltration/pivot path an attacker abuses, so audit `AllowTcpForwarding`.

### 8.2 IDS / IPS

- **IDS (Intrusion Detection System)** — observes (often on a mirror/SPAN port), *detects* and alerts on suspicious patterns. Out-of-band; cannot block.
- **IPS (Intrusion Prevention System)** — inline; can *drop* the offending traffic. Higher risk (a false positive blocks real traffic; it's a latency/availability point).
- **Signature-based** (Snort/Suricata rules: known-bad byte patterns) vs **anomaly-based** (statistical deviation from a learned baseline). Signature catches known attacks cheaply; anomaly catches novel ones with more false positives.

### 8.3 WAF (Web Application Firewall)

An L7 firewall that inspects HTTP semantics (URLs, headers, bodies, parameters) to block application attacks — SQLi, XSS, path traversal, and L7 DDoS — that lower-layer firewalls cannot see because they operate below HTTP. Deployed at the CDN edge (Cloudflare, AWS WAF) or in front of the app. A WAF is a *mitigation*, not a fix: it buys time and catches generic exploitation, but the durable fix is parameterized queries, output encoding, and input validation in the app.

---

## 9. Replay attacks & HMAC request signing — runnable

Encryption hides content but does **not** stop **replay**: an attacker who captures a valid (even encrypted) request can resend it to repeat its effect ("transfer $100" replayed 1000×). The defenses are **integrity + freshness**:

- **HMAC signature** over the request → tampering is detected (integrity/authenticity).
- **Timestamp** → reject requests outside a small window (bounds replay to that window).
- **Nonce** (unique per request, server remembers recently-seen) → reject duplicates within the window (kills replay entirely).

```python
"""sign.py — HMAC request signing with timestamp + nonce to prevent tampering
AND replay. The shared secret authenticates; the timestamp+nonce make each
request single-use. Run: python sign.py"""
import hashlib, hmac, secrets, time

SECRET = b"shared-secret-rotate-me"
WINDOW_SECONDS = 30          # accept requests at most this stale

def sign(method, path, body, ts, nonce, secret=SECRET):
    """Canonical string -> HMAC-SHA256. Order & delimiters must be fixed."""
    msg = b"\n".join([
        method.encode(), path.encode(), body,
        str(ts).encode(), nonce.encode(),
    ])
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()

def make_request(method, path, body):
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    sig = sign(method, path, body, ts, nonce)
    return {"method": method, "path": path, "body": body,
            "ts": ts, "nonce": nonce, "sig": sig}

class Verifier:
    def __init__(self, now=time.time):
        self.seen = {}           # nonce -> expiry; remembered within the window
        self._now = now

    def _gc(self, now):
        for n, exp in list(self.seen.items()):
            if exp < now:
                del self.seen[n]

    def verify(self, req) -> tuple[bool, str]:
        now = self._now()
        self._gc(now)
        # 1) integrity/authenticity: recompute the MAC, constant-time compare
        expected = sign(req["method"], req["path"], req["body"],
                        req["ts"], req["nonce"])
        if not hmac.compare_digest(expected, req["sig"]):
            return False, "bad signature (tampered or wrong key)"
        # 2) freshness: reject stale (and far-future) requests
        if abs(now - req["ts"]) > WINDOW_SECONDS:
            return False, "stale timestamp (outside replay window)"
        # 3) anti-replay: a nonce may be used at most once within the window
        if req["nonce"] in self.seen:
            return False, "replayed nonce"
        self.seen[req["nonce"]] = req["ts"] + WINDOW_SECONDS
        return True, "ok"

if __name__ == "__main__":
    clock = {"t": 1_000_000.0}
    v = Verifier(now=lambda: clock["t"])

    req = make_request("POST", "/transfer", b'{"to":"bob","amt":100}')
    req["ts"] = int(clock["t"])      # align with virtual clock
    req["sig"] = sign(req["method"], req["path"], req["body"], req["ts"], req["nonce"])

    ok, why = v.verify(req)
    assert ok, why                                   # first time: accepted

    ok, why = v.verify(req)
    assert not ok and "replay" in why, why           # exact resend: rejected

    # Tampering the body invalidates the signature.
    bad = dict(req, body=b'{"to":"bob","amt":1000000}')
    ok, why = v.verify(bad)
    assert not ok and "signature" in why, why

    # A fresh, distinct request still works.
    req2 = make_request("POST", "/transfer", b'{"to":"carol","amt":5}')
    req2["ts"] = int(clock["t"])
    req2["sig"] = sign(req2["method"], req2["path"], req2["body"], req2["ts"], req2["nonce"])
    ok, why = v.verify(req2)
    assert ok, why

    # Far outside the window -> rejected as stale.
    clock["t"] += WINDOW_SECONDS + 5
    req3 = make_request("GET", "/balance", b"")
    req3["ts"] = int(clock["t"]) - (WINDOW_SECONDS + 1)
    req3["sig"] = sign(req3["method"], req3["path"], req3["body"], req3["ts"], req3["nonce"])
    ok, why = v.verify(req3)
    assert not ok and "stale" in why, why

    print("HMAC signing + replay-prevention assertions passed")
```

> Note `hmac.compare_digest` — a **constant-time** comparison. A naive `==` on the signature leaks, via timing, how many leading bytes matched, enabling a byte-at-a-time forgery. Constant-time comparison of secrets is non-negotiable.

---

## 10. Common attacks & their defenses (reference table)

| Attack | Layer | Mechanism | Defense |
|---|---|---|---|
| **ARP spoofing** | L2 | forge ARP replies → MITM on the LAN (attacker claims the gateway's IP) | dynamic ARP inspection (DAI), static ARP for critical hosts, encrypt above L2 (don't trust the LAN) |
| **DNS cache poisoning** | L7 | inject forged DNS responses → redirect a domain | **DNSSEC** (signed records), source-port + txid randomization, DoH/DoT |
| **BGP hijacking** | L3 routing | announce a prefix you don't own → reroute/blackhole traffic (e.g., the 2018 Amazon Route 53 / MyEtherWallet hijack) | **RPKI** (Route Origin Authorization), prefix filtering, BGPsec, route monitoring |
| **SYN flood** | L4 | exhaust half-open backlog (§5.1) | SYN cookies, backlog tuning, scrubbing |
| **Amplification/reflection** | L3/L4 | spoof victim src to open resolvers (DNS/NTP/memcached, §5.2) | BCP38 ingress filtering, close open resolvers, rate-limit, scrubbing |
| **Replay** | L7 | resend a captured valid request (§9) | nonce + timestamp + HMAC |
| **TLS downgrade / stripping** | L4/L7 | force a weaker protocol or plaintext (sslstrip) | HSTS, TLS 1.3 (no legacy suites), no plaintext fallback |
| **Slowloris** | L7 | hold many connections open with trickled partial requests | connection/timeout limits, reverse-proxy buffering |

> **ARP spoofing and DNS poisoning share a root cause:** unauthenticated trust in a local/legacy protocol. The general fix is the zero-trust posture — *don't trust the network*; authenticate and encrypt above it (mTLS, DNSSEC). **BGP hijacking** is the internet-scale version: routing was built on implicit trust, and **RPKI** is the (still-deploying) authentication retrofit.

---

## 11. Defense in depth & secrets in transit

### 11.1 Defense in depth

No layer is sufficient; design so the failure of any one is survivable:

```text
  Edge/CDN (anycast, WAF, DDoS scrubbing, TLS termination)
     |
  Network firewall + segmentation (stateful, microsegmented)
     |
  Identity-aware proxy / mTLS (zero trust: authenticate every request)
     |
  Host firewall (iptables/nftables, conntrack limits, SYN cookies)
     |
  Application (input validation, parameterized queries, authz, rate limit)
     |
  Data (encryption at rest, least-privilege access)
```

Each layer assumes the ones outside it may be breached. A stolen VPN credential is stopped by mTLS + per-request authz; a volumetric flood is absorbed at the edge before it reaches the app; an L7 exploit that slips the WAF is caught by parameterized queries.

### 11.2 Secrets in transit

- **Encrypt everything, even internally.** "Internal network" is not a trust boundary (zero trust). mTLS for service-to-service; TLS for everything client-facing.
- **Short-lived, automatically rotated** credentials (service-mesh-issued certs, OIDC tokens) limit the value of any captured secret.
- **Never put secrets in URLs/query strings** (they land in logs, proxies, `Referer` headers); use headers/bodies over TLS.
- **AEAD, not encrypt-then-hope** — use authenticated encryption (TLS 1.3, ChaCha20-Poly1305, AES-GCM) so confidentiality and integrity travel together; raw encryption without a MAC is malleable.

---

## 12. Advanced: conntrack at scale, XDP DDoS scrubbing, and the kill chain

### Conntrack — the stateful-firewall resource that runs out

A stateful firewall ([§3](#3-firewalls--stateless-vs-stateful-packet-filtering)) and
every NAT ([03 §4](03_network_layer_routing.md)) keep a **connection-tracking table**
(`nf_conntrack`) — one entry per flow. Under high connection rates or a flood it
**fills**, and new connections are **dropped** with `nf_conntrack: table full` in
`dmesg` — a silent, brutal outage that looks like random connection failures
([scenarios 04.6](../enterprise_scenarios/04_network_incidents.md) DNS, 04.1 ports).

```bash
sysctl net.netfilter.nf_conntrack_count        # current entries
sysctl net.netfilter.nf_conntrack_max          # the ceiling — raise it
dmesg | grep -i conntrack                       # "table full, dropping packet"
```

Mitigations: raise `nf_conntrack_max` and the hash size, shorten timeouts for
short-lived states, or **`NOTRACK`** flows that don't need stateful handling (e.g. a
busy LB's traffic). On Kubernetes nodes this is a classic limit; eBPF dataplanes
(Cilium, [10 §advanced](10_cloud_sdn_overlays.md)) avoid conntrack for much of the
traffic.

### XDP-based DDoS scrubbing — dropping at the door

Volumetric and SYN floods ([§5](#5-ddos--types-and-mitigations)) must be dropped as
*cheaply* as possible. **XDP** ([08 §advanced](08_network_performance_tuning.md)) runs
an eBPF filter in the NIC driver **before** the kernel allocates an skb or conntrack
entry — so a host (or scrubbing tier) can drop millions of attack packets per second
per core, the technique behind Cloudflare's L3/4 mitigation and Cilium's defenses. It
sidesteps the very conntrack/stack costs an attacker is trying to exhaust.

### Map defenses to the kill chain (MITRE ATT&CK)

Staff-level security reasoning is *systematic*, not a pile of point defenses. Map
network controls to attacker stages so you can see coverage gaps:

```
   Recon/scan      -> rate-limit, hide topology, IDS on scan signatures
   Initial access  -> firewall/segmentation, mTLS, WAF (§3,§4,§6)
   Lateral movement-> zero-trust microsegmentation, NetworkPolicy (§4, 10 §8)
   Exfiltration    -> egress filtering/allowlists, DLP, DNS-exfil detection (04.6)
   Impact (DDoS)   -> anycast absorption, XDP scrubbing, autoscaling (§5)
```

The recurring theme is **egress control** — most breaches *leave* through paths nobody
restricted; a default-deny egress policy (allowlist outbound) blocks exfiltration and
SSRF-to-metadata ([scenarios 04 / system design SSRF]) far more than another inbound
rule.

---

## 13. Trade-offs summary

- **Stateful firewalls** give clean "allow replies" semantics but turn the conntrack table into an exhaustible resource — size and monitor it.
- **Zero trust beats the perimeter model** but costs an identity/PKI/mTLS investment and per-request authorization on the hot path.
- **NAT is addressing, not security** — never rely on it; it gets more dangerous as IPv6 removes it.
- **SYN cookies** trade a few TCP option bits for statelessness — perfect as an under-attack fallback, which is when Linux engages them.
- **Anycast + scrubbing** is the only way to absorb terabit volumetric attacks — distribute the load globally, then filter; **XDP** drops attack PPS in-host for near-free.
- **WireGuard** trades crypto agility for a tiny, auditable, fast codebase; **IPsec** trades complexity for standards interop.
- **WAF/IPS are mitigations, not fixes** — they buy time; the durable fix lives in the application.

## 14. Key Takeaways

1. Start from a **threat model** (eavesdrop, spoof, MITM, replay, tamper, DoS) and map every defense to **C-I-A + authenticity**. Encryption alone provides confidentiality only — you also need integrity (AEAD/HMAC), authenticity (certs/signatures), and availability (DDoS defense).
2. **Stateful firewalls + segmentation** limit blast radius; **zero trust (BeyondCorp)** drops network-location trust entirely — authenticate every request by identity + device, not by VLAN. **NAT is not a firewall.**
3. **DDoS comes in three flavors** (volumetric / protocol / L7) hitting three resources (bandwidth / state / app). Defend in layers: **anycast+scrubbing → stateless/XDP drop → SYN cookies+conntrack limits → rate limiting/WAF**.
4. **SYN cookies** make the handshake stateless under flood; **amplification** (DNS/NTP/memcached, up to ~51,000×) is enabled by source spoofing and killed by **BCP38**. **Mirai/Dyn 2016** proved DNS is critical infrastructure and concentration is fragility.
5. **mTLS** turns TLS into the zero-trust identity primitive for service-to-service auth. Pick the VPN to fit: **WireGuard** (small/modern/fast), **IPsec** (interop), **TLS-VPN** (firewall traversal).
6. **Replay is not stopped by encryption** — add **timestamp + nonce + HMAC** (and compare MACs in constant time). Know the protocol attacks still in the wild: **ARP spoof, DNS poison, BGP hijack** — all rooted in unauthenticated trust, all answered by "don't trust the network."
7. **Defense in depth or nothing:** design so the breach of any single layer is survivable, and encrypt secrets in transit *everywhere* — including inside your own network.

> Read next: [10 — Cloud Networking, SDN & Overlays](10_cloud_sdn_overlays.md) for how segmentation, mTLS, and these defenses are expressed in VPCs, security groups, and Kubernetes NetworkPolicies, and [08 — Network Performance & Tuning](08_network_performance_tuning.md) for the XDP/data-path mechanics behind in-host DDoS drop.
