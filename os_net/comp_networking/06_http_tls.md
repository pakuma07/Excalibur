# 06 — HTTP & TLS

> **Audience:** staff/principal. You've written HTTP clients and read a `curl -v` dump before. This doc is about how HTTP *actually* works on the wire — request/response anatomy, the caching decision tree, the three major protocol versions and *why* each was invented — and how TLS turns a plaintext stream into an authenticated, confidential channel, from the 1.2 vs 1.3 handshakes through PKI, revocation, and the operational failure modes (cert expiry, mixed content) that take production down.
>
> **Primary sources:** Grigorik, *High Performance Browser Networking* (HPBN), ch. 9–13; RFC 9110 (HTTP Semantics), RFC 9111 (HTTP Caching), RFC 9112 (HTTP/1.1), RFC 9113 (HTTP/2), RFC 9114 (HTTP/3) + RFC 9000 (QUIC); RFC 8446 (TLS 1.3), RFC 5246 (TLS 1.2), RFC 6066 (SNI), RFC 7301 (ALPN), RFC 6960 (OCSP), RFC 5280 (X.509/PKI); Stevens, *TCP/IP Illustrated*; Cloudflare, Fastly, HAProxy, and NGINX engineering docs.

---

## 1. Why this matters at scale

HTTP is the universal application protocol and TLS is the universal security layer. Almost every byte your service serves passes through both. Two facts dominate staff-level reasoning here:

1. **Latency is round-trips.** On the modern Internet, bandwidth is cheap and abundant; *round-trip time (RTT) is the scarce resource* (HPBN's central thesis). A cold HTTPS request over TCP+TLS 1.2 costs **3 RTTs before the first application byte** (1 for TCP, 2 for TLS). The entire history of HTTP/2, HTTP/3, TLS 1.3, and connection reuse is an attack on that round-trip count. If you can't reason about RTT budgets, you can't reason about web performance.
2. **TLS failures are binary and total.** A misconfigured storage engine degrades; an expired certificate or a `Mixed Content` block takes the whole endpoint to a hard error for *every* user simultaneously. The most common large-scale web outages are not code bugs — they're certificate expiry and TLS/PKI misconfiguration.

```
   The round-trip cost of "GET https://x" on a cold connection (TLS 1.2):

   client                                              server
     |------------------ TCP SYN ---------------------->|  \
     |<----------------- SYN-ACK -----------------------|   } 1 RTT  (TCP)
     |------------------ ACK --------------------------->|  /
     |------------------ ClientHello ------------------->|  \
     |<----------------- ServerHello, Cert, ... --------|   } 1 RTT  (TLS)
     |------------------ ClientKeyExchange, Finished --->|   }
     |<----------------- Finished ----------------------|   } 1 RTT  (TLS)
     |------------------ GET / ------------------------->|  \
     |<----------------- 200 OK ------------------------|   } 1 RTT  (HTTP)
                                                            = 4 RTTs to first byte
```

Cut TLS to 1.3 (1-RTT) and reuse the connection (0 further handshakes), and the same request on a warm connection is **1 RTT**. That is the prize.

---

## 2. HTTP message anatomy

HTTP is a stateless request/response protocol. Through HTTP/1.1 it is **text on the wire**; HTTP/2 and HTTP/3 carry the *same semantics* (RFC 9110) in a binary framing. Learn the semantics once; they're version-independent.

### 2.1 The request

```
GET /search?q=tls HTTP/1.1            <- request line: METHOD  target  version
Host: example.com                     <- headers (case-insensitive names)
User-Agent: curl/8.0
Accept: text/html, application/json;q=0.9
Accept-Encoding: gzip, br
Cookie: session=abc123
                                      <- empty line ends the header block (CRLF CRLF)
<optional body>                       <- present for POST/PUT/PATCH
```

- **Request line**: method, request target (origin-form path+query, or absolute-form to a proxy), protocol version.
- **`Host` is mandatory in HTTP/1.1** — it's what makes virtual hosting (many domains on one IP) possible. In HTTP/2/3 it becomes the `:authority` pseudo-header.
- The body length is delimited by `Content-Length` *or* `Transfer-Encoding: chunked` — never both.

### 2.2 Methods and their properties

| Method | Safe? | Idempotent? | Cacheable? | Purpose |
|---|---|---|---|---|
| `GET` | yes | yes | yes | Retrieve a representation. No body semantics. |
| `HEAD` | yes | yes | yes | Headers only — check existence/metadata cheaply. |
| `OPTIONS` | yes | yes | no | Capabilities / CORS preflight. |
| `POST` | no | **no** | only if explicit | Process the body (create, RPC, form submit). |
| `PUT` | no | yes | no | Replace the resource at the target with the body. |
| `PATCH` | no | no | no | Partial update. |
| `DELETE` | no | yes | no | Remove the resource. |

- **Safe** = read-only, no observable side effect → freely prefetchable/retriable.
- **Idempotent** = N identical requests have the same effect as 1 → **safe to retry**. This property is *load-bearing*: a proxy, retry library, or HTTP/2 stack will silently re-send idempotent requests on a connection failure. `POST` is not idempotent, which is why naive retries on POST cause duplicate charges/orders. Make critical POSTs idempotent with an **idempotency key** (Stripe's pattern) so a retry is a no-op server-side.

### 2.3 Status codes

| Class | Meaning | Key examples |
|---|---|---|
| **1xx** | Informational | `100 Continue`, `101 Switching Protocols` (WebSocket/upgrade) |
| **2xx** | Success | `200 OK`, `201 Created`, `204 No Content`, `206 Partial Content` (range) |
| **3xx** | Redirection | `301`/`308` permanent, `302`/`307` temporary, **`304 Not Modified`** (conditional cache hit) |
| **4xx** | Client error | `400`, `401` (auth required), `403` (forbidden), `404`, `409` (conflict), `422`, **`429` (rate limited)** |
| **5xx** | Server error | `500`, `502` (bad gateway — upstream broke), `503` (unavailable — overload/maint), `504` (gateway timeout) |

> Staff distinction worth internalizing: `502`/`503`/`504` are *proxy/LB* verdicts about an upstream (see [07](07_load_balancing_proxies.md)). A spike in `502`s points at the backend connection; a spike in `504`s points at backend *latency* exceeding the proxy's timeout. `307`/`308` (vs `302`/`301`) preserve the method and body on redirect — `301`/`302` historically let clients rewrite POST→GET, a footgun.

---

## 3. Statelessness, cookies, and sessions

HTTP is **stateless**: each request is independent and the server keeps no per-client connection state required to interpret it. This is what lets any request hit any backend behind a load balancer — the property that makes horizontal scaling trivial. State is reintroduced explicitly:

```
1. POST /login           --> server validates, mints session
2. <-- 200, Set-Cookie: session=abc123; HttpOnly; Secure; SameSite=Lax
3. GET /account          --> Cookie: session=abc123  (browser echoes it)
4. server looks up abc123 -> "this is user 42"
```

- **Server-side sessions**: the cookie holds an opaque ID; the *state* lives in a server-side store (Redis, DB). Pro: revocable instantly, small cookie. Con: every backend needs to reach the store → shared state, the enemy of pure horizontal scale.
- **Client-side / stateless tokens (JWT)**: the cookie/header carries a *signed* token containing the claims. Pro: no server lookup, any backend verifies the signature locally. Con: **cannot be revoked before expiry** without reintroducing a server-side blocklist (which defeats the point). Keep them short-lived + refresh.

Cookie attributes that are non-negotiable in production:

| Attribute | Effect |
|---|---|
| `HttpOnly` | JS `document.cookie` can't read it → mitigates XSS token theft. |
| `Secure` | Only sent over HTTPS. |
| `SameSite=Lax/Strict` | Not sent on cross-site requests → CSRF defense. `Lax` is the modern default. |
| `Domain` / `Path` | Scope. `__Host-` prefix forces Secure + Path=/ + no Domain. |

> Sessions and load balancing intersect: **sticky sessions** (pinning a client to one backend) exist precisely because some apps keep session state in process memory. They trade away even load distribution and graceful failover — covered in [07 §11](07_load_balancing_proxies.md). The clean answer is a shared session store so any backend is interchangeable.

---

## 4. Content negotiation

The client advertises preferences; the server picks a representation. Driven by `Accept*` request headers and the `Vary` response header.

```
Request:  Accept: application/json, text/html;q=0.8     <- q = relative quality 0..1
          Accept-Encoding: br, gzip
          Accept-Language: en-US, en;q=0.7

Response: Content-Type: application/json
          Content-Encoding: br
          Vary: Accept-Encoding, Accept-Language        <- cache key MUST include these
```

The `Vary` header is the subtle one: it tells caches *which request headers* changed the response, so they don't serve a `gzip` body to a client that can't decode it, or English to a French speaker. A missing or overly broad `Vary` is a classic cache-correctness bug — `Vary: User-Agent` effectively disables caching (too many distinct values).

---

## 5. Caching — the single biggest web performance lever

The fastest request is the one you never make. HTTP caching (RFC 9111) is a formal protocol for *not* re-fetching. There are two distinct mechanisms; you almost always use both.

### 5.1 Freshness (no network at all) vs validation (cheap network)

| Mechanism | Header | Effect |
|---|---|---|
| **Freshness** | `Cache-Control: max-age=N` (or legacy `Expires`) | Cache may serve the stored copy with **zero network** until it's `N` seconds old. |
| **Validation** | `ETag` / `Last-Modified` + conditional request | When stale, ask the origin "still valid?" → `304 Not Modified` (no body) or `200` (new body). |

`Cache-Control` directives that matter:

| Directive | Meaning |
|---|---|
| `max-age=N` | Fresh for N seconds. |
| `s-maxage=N` | Like `max-age` but for **shared** caches (CDN/proxy) only; overrides `max-age` there. |
| `no-cache` | May store, but **must revalidate** before each use. (Not "don't cache"!) |
| `no-store` | Never write to cache at all. (Sensitive data.) |
| `private` | Only the browser may cache; shared caches must not. |
| `public` | Shared caches may cache even normally-uncacheable responses. |
| `immutable` | Never revalidate within `max-age` (for content-hashed asset URLs). |
| `stale-while-revalidate=N` | Serve stale instantly while revalidating in the background — kills tail latency. |

### 5.2 ETags and conditional requests

An **ETag** is an opaque version tag (typically a content hash). The validation loop:

```
1. GET /app.js
   <-- 200 OK,  ETag: "v17abc",  Cache-Control: max-age=60

   ... 60s later, copy is stale ...

2. GET /app.js
   If-None-Match: "v17abc"        <- conditional request
   <-- 304 Not Modified           <- no body! just "your copy is still good"
       (or 200 + new body + new ETag if it changed)
```

The win: a `304` carries **no body**, so revalidating a 2 MB unchanged asset costs ~1 RTT and a few hundred bytes instead of 2 MB. `Last-Modified` + `If-Modified-Since` is the timestamp-based equivalent (1-second granularity; ETags are preferred for precision).

### 5.3 The full caching decision (the diagram to memorize)

```
                       request for resource R
                                |
                    is there a stored copy?
                       /                  \
                      no                   yes
                      |                      |
                  fetch from           is it FRESH?  (age < max-age, not no-cache)
                  origin (200)           /          \
                      |                yes            no  (stale)
                  store per            |               |
                  Cache-Control    serve from      have a validator (ETag / Last-Modified)?
                                   cache, 0 RTT      /                    \
                                   (FASTEST)        yes                     no
                                                     |                       |
                                       conditional GET (If-None-Match)   full GET to origin
                                                     |                       |
                                              304? --+-- 200?            store 200
                                               |          |
                                       serve stored,   replace stored,
                                       refresh age     serve new
```

> Practical pattern (the "two-bucket" strategy used by every CDN-fronted app): **content-hashed immutable assets** (`app.4f3a.js`) get `Cache-Control: public, max-age=31536000, immutable` — cached forever, never revalidated, and you bust the cache by changing the URL. The **HTML entry point** gets `no-cache` (always revalidate) so a deploy is visible immediately. This gives you both instant deploys *and* permanent asset caching.

---

## 6. HTTP/1.0 → 1.1 → 2 → 3: the round-trip war

Every HTTP version after 1.0 exists to claw back round-trips and fix head-of-line (HOL) blocking. Track that one theme and the history is obvious.

### 6.1 HTTP/1.0: a connection per request

HTTP/1.0 opened a fresh TCP connection for *every* resource and closed it after the response. A page with 50 assets = 50 TCP handshakes (+ 50 TLS handshakes over HTTPS). Catastrophic on high-RTT links.

### 6.2 HTTP/1.1: keep-alive and the pipelining failure

- **Persistent connections (keep-alive)**, the default in 1.1: reuse one TCP connection for many sequential request/response pairs. Amortizes the TCP+TLS handshake over many requests — the single biggest 1.1 win (§9 measures it).
- **Pipelining**: send request 2 before response 1 arrives. *Sounds* great; **failed in practice and is effectively dead.** Why: HTTP/1.1 responses on a connection must come back **in request order**. A slow first response blocks every response queued behind it — **HOL blocking at the application layer**. Buggy proxies mishandled it too. Browsers never shipped it on by default.

```
HTTP/1.1 pipelining HOL blocking:
  client sends:   [req A][req B][req C]   (all at once)
  server must:    [resp A.................][resp B][resp C]
                   ^ A is slow -> B and C wait, even though they're ready
```

Because of this, browsers worked around 1.1 by **opening 6 parallel connections per origin** — wasteful (6× handshakes, 6× congestion windows, 6× server sockets) and the reason 1.1-era sites resorted to *domain sharding* and *concatenation/spriting* hacks.

### 6.3 HTTP/2: binary framing + multiplexing

HTTP/2 (RFC 9113, from Google's SPDY) keeps HTTP/1.1 *semantics* but replaces the text wire format with a **binary framing layer**. Key concepts:

- **Streams, messages, frames**: one TCP connection carries many independent **streams** (each a request/response). A message is split into **frames** (`HEADERS`, `DATA`, ...), each tagged with a stream ID. Frames from different streams are **interleaved on the wire**.
- **Multiplexing**: because frames are interleaved and reassembled by stream ID, many requests share **one** connection with no application-layer HOL blocking and no ordering constraint. This kills the need for 6 connections, domain sharding, and spriting.
- **HPACK header compression** (RFC 7541): HTTP headers are huge and repetitive (cookies, user-agent on every request). HPACK uses a static table + a per-connection dynamic table + Huffman coding to shrink them, often to a few bytes for a repeat request.
- **Stream prioritization**: clients express a dependency/weight tree so the server sends critical resources (CSS, above-the-fold) before less important ones. (The original priority scheme was complex and poorly implemented; RFC 9218 replaced it with a simpler urgency-based scheme.)

```
HTTP/2: one TCP connection, frames interleaved by stream id
  wire: |H s1|H s3|D s1|D s3|D s1|H s5|D s5|D s3| ...
                ^ stream 3 doesn't wait for stream 1 to finish
```

- **Server push** (sending resources the client didn't request yet) was the headline 1.0-era feature and **is dead**: Chrome removed it in 2022. It was hard to use without wasting bandwidth (pushing assets the client already cached) and the benefit was marginal vs `103 Early Hints` (which just tells the client what to fetch). Don't design around it.

**The remaining flaw: TCP-level HOL blocking.** HTTP/2 multiplexes *above* TCP, but TCP delivers bytes strictly in order. A single lost TCP segment stalls **every** HTTP/2 stream until it's retransmitted — because TCP won't hand the later bytes (belonging to other streams) up to the application. HTTP/2 solved *application*-layer HOL blocking but inherited *transport*-layer HOL blocking. That is precisely what HTTP/3 fixes.

### 6.4 HTTP/3 and QUIC: HTTP over UDP

HTTP/3 (RFC 9114) runs over **QUIC** (RFC 9000), a transport built on **UDP** that reimplements TCP's reliability + congestion control *plus* TLS 1.3 *in user space*.

| Problem | TCP+TLS+HTTP/2 | QUIC+HTTP/3 |
|---|---|---|
| Transport HOL blocking | yes — one lost segment stalls all streams | **no** — streams are independent; a loss on stream 1 doesn't stall stream 2 |
| Handshake RTTs (new) | TCP (1) + TLS 1.3 (1) = 2 | **1** (transport + crypto merged); **0-RTT** on resumption |
| Connection identity | 4-tuple (IP:port) — breaks on network change | **Connection ID** — survives IP change |
| Where it lives | kernel (TCP) — slow to evolve | user space — ships with the app/browser |

Key QUIC properties:
- **Independent streams**: QUIC has streams natively, each with its own delivery guarantee. Packet loss only stalls the affected stream → no transport HOL blocking. This is the headline win.
- **Merged crypto+transport handshake**: QUIC integrates TLS 1.3, so the connection + encryption come up together in **1 RTT**, or **0-RTT** with resumption (send data in the first flight).
- **Connection migration**: a connection is identified by a **Connection ID**, not the IP:port 4-tuple. Switch from Wi-Fi to cellular and the connection *survives* — invaluable on mobile. (TCP would have to fully re-establish.)
- **Cost**: UDP is more CPU-expensive than TCP per byte today (less NIC offload, user-space stacks), and some middleboxes/firewalls drop UDP. Deploy HTTP/3 with HTTP/2 fallback (advertised via the `Alt-Svc` header / DNS HTTPS records).

```
Protocol stack comparison:

  HTTP/2                      HTTP/3
  +----------------+          +----------------+
  |  HTTP/2        |          |  HTTP/3        |
  +----------------+          +----------------+
  |  TLS 1.2/1.3   |          |  QUIC (incl.   |
  +----------------+          |  TLS 1.3 +     |
  |  TCP           |          |  streams +     |
  +----------------+          |  congestion)   |
  |  IP            |          +----------------+
  +----------------+          |  UDP           |
                              +----------------+
                              |  IP            |
                              +----------------+
```

> Cross-link: QUIC reimplements the reliability and congestion-control machinery covered in the transport-layer doc, just in user space over UDP. The independent-stream design is *the* reason it beats HTTP/2 on lossy mobile networks.

---

## 7. TLS: confidentiality, integrity, authentication

TLS gives three guarantees over a plaintext transport: **confidentiality** (eavesdroppers see ciphertext), **integrity** (tampering is detected), and **authentication** (you're talking to who you think — via certificates). It splits into a **handshake** (asymmetric crypto to agree on keys + authenticate) and a **record/bulk phase** (symmetric crypto for speed).

### 7.1 TLS 1.2 handshake (2 RTTs)

```
client                                                   server
  |--- ClientHello --------------------------------------->|
  |     (TLS versions, cipher suites, client random,       |
  |      extensions: SNI, ALPN)                             |
  |                                                         |
  |<-- ServerHello (chosen cipher, server random) ---------|
  |<-- Certificate (the chain)                              |
  |<-- ServerKeyExchange (ECDHE params, signed)             |   1 RTT
  |<-- ServerHelloDone -------------------------------------|
  |                                                         |
  |--- ClientKeyExchange (ECDHE pubkey) ------------------->|
  |--- ChangeCipherSpec ----------------------------------->|
  |--- Finished (encrypted, MAC of handshake) ------------->|   2 RTT
  |<-- ChangeCipherSpec ------------------------------------|
  |<-- Finished --------------------------------------------|
  |======== application data (encrypted) =================>|
```

Two full round-trips before the first application byte — and that's *on top* of the TCP handshake. Three RTTs total to first byte on a cold HTTPS connection.

### 7.2 TLS 1.3 handshake (1 RTT, or 0-RTT)

TLS 1.3 (RFC 8446) is a ground-up redesign. It removed every legacy/insecure option (static RSA key exchange, RC4, CBC modes, renegotiation, compression) and **folds key agreement into the first flight**: the client *guesses* the server's preferred (EC)DHE group and sends its key share immediately in the `ClientHello`.

```
client                                                   server
  |--- ClientHello -------------------------------------->|
  |     + key_share (client's ECDHE pubkey, guessed group)|
  |     + SNI, ALPN, supported_versions                   |
  |                                                       |
  |<-- ServerHello + key_share ---------------------------|  1 RTT
  |<-- {EncryptedExtensions, Certificate,                 |  (everything after
  |     CertificateVerify, Finished}  (ENCRYPTED)         |   ServerHello is
  |                                                       |   already encrypted)
  |--- {Finished} (encrypted) --------------------------->|
  |======== application data ===========================>|
```

- **1 RTT to first byte** — half of TLS 1.2. With TCP that's 2 RTTs cold; over QUIC the TCP RTT disappears too.
- **0-RTT resumption**: on a *resumed* connection, the client can send application data in the very first flight using a pre-shared key (PSK) from the prior session — **zero handshake RTTs**. The catch: 0-RTT data is **replayable** by an attacker (it isn't bound to a fresh handshake), so it's only safe for *idempotent* requests. Never put a non-idempotent POST in 0-RTT.
- TLS 1.3 mandates **forward secrecy** (ephemeral ECDHE always) — see §7.6.

### 7.3 The certificate chain and PKI

Authentication answers "is this really `example.com`?". The server presents an **X.509 certificate** binding its public key to its domain name, **signed** by a Certificate Authority (CA). Trust chains up to a **root CA** in the client's trust store (shipped by the OS/browser):

```
   Root CA cert (self-signed, in OS/browser trust store, offline & precious)
        |  signs
   Intermediate CA cert (the CA's online signer)
        |  signs
   Leaf/server cert  (CN/SAN = example.com, public key, validity dates)
        ^ server sends LEAF + INTERMEDIATE(s); client already trusts the ROOT
```

- The client validates: signature chains to a trusted root, not expired, domain matches a **Subject Alternative Name (SAN)** (the CN is deprecated for hostname matching), and not revoked (§7.7).
- **A #1 production outage cause: forgetting to send the intermediate.** It works in your browser (which cached the intermediate) and fails for fresh clients/`curl`. Always serve the full chain (leaf + intermediates), never the root.
- **Certificate Transparency (CT)**: CAs must log every cert to public append-only logs; browsers reject certs not in CT logs. This catches mis-issued certs.

### 7.4 SNI — Server Name Indication

TLS is established *before* HTTP, so the server doesn't yet know which `Host` you want — but it needs to pick the right certificate for virtual hosts on one IP. **SNI** (RFC 6066) solves this: the client puts the target hostname in the (plaintext) `ClientHello`. This is what makes HTTPS virtual hosting and shared CDN IPs possible. (ESNI/ECH encrypts the SNI to close the privacy leak that it's plaintext.)

### 7.5 ALPN — Application-Layer Protocol Negotiation

How does the client know whether to speak HTTP/1.1 or HTTP/2 *before* sending an HTTP request? **ALPN** (RFC 7301): the client lists its supported protocols (`h2`, `http/1.1`) in the `ClientHello`; the server picks one in the `ServerHello`. HTTP/2 over TLS is negotiated *entirely* by ALPN — no extra round-trip. (`h3` is advertised separately via `Alt-Svc`/DNS since it's UDP.)

### 7.6 Perfect forward secrecy (PFS)

With **ephemeral** key exchange (ECDHE — the "E" is ephemeral), each session derives keys from a fresh, throwaway key pair. Consequence: even if the server's long-term private key is later stolen, **past recorded sessions cannot be decrypted** — each session's secret is gone. The dead static-RSA key exchange lacked this (steal the key, decrypt years of recorded traffic). TLS 1.3 makes PFS mandatory. This is non-negotiable for any sensitive service.

### 7.7 Revocation: OCSP and CRLs

What if a private key is stolen *before* the cert expires? You must revoke it.

| Mechanism | How | Problem |
|---|---|---|
| **CRL** (Certificate Revocation List) | CA publishes a big list of revoked serials; client downloads it. | Huge, stale, slow. |
| **OCSP** | Client asks the CA's OCSP responder "is serial X still valid?" online. | Privacy leak (CA learns who you visit) + latency + **fails open** (browsers soft-fail if the responder is down, so it provides weak security). |
| **OCSP stapling** | The *server* periodically fetches a signed OCSP response and **staples** it to the TLS handshake. | Client gets revocation status with no extra request, no privacy leak. The recommended approach. |

> In practice, revocation is famously broken (soft-fail OCSP gives little real protection), which is why the industry moved to **short-lived certificates** (e.g., 90-day Let's Encrypt, trending toward 47-day mandates) — a cert that expires in weeks barely needs revocation. Automate renewal (ACME) or you *will* have an expiry outage.

### 7.8 mTLS — mutual TLS

In normal TLS only the *server* authenticates. In **mutual TLS**, the **client also presents a certificate** and the server validates it. The handshake gains a `CertificateRequest` from the server and a client `Certificate` + `CertificateVerify`. mTLS is the backbone of **zero-trust service-to-service auth** and service meshes — every service has an identity cert, and the mesh enforces "service A may call service B" cryptographically. See [07 §9](07_load_balancing_proxies.md) on the service mesh.

### 7.9 The cost of TLS and session resumption

The expensive part is the handshake (asymmetric crypto + RTTs), not the bulk encryption (modern CPUs do AES-GCM/ChaCha20 at multi-GB/s via AES-NI). So the optimization target is **avoid full handshakes**:

- **Connection reuse** (keep-alive): the cheapest win — one handshake serves thousands of requests.
- **Session resumption** (TLS 1.3 PSK / 1.2 session tickets): a resumed handshake skips the certificate exchange and signature, dropping to 1 RTT (or 0-RTT). The client presents a ticket from a prior session.
- **OCSP stapling**: removes the client's revocation round-trip.

---

## 8. Working code — a Python HTTPS client that prints the TLS details

This connects, performs a real TLS handshake, and prints the negotiated protocol version, cipher suite, ALPN result, and peer certificate metadata — exactly what you'd inspect when debugging a TLS issue. Runnable as-is (needs outbound network).

```python
"""
tls_inspect.py — open a real TLS connection and print handshake details.
Run: python tls_inspect.py example.com
"""
import socket
import ssl
import sys
from datetime import datetime, timezone


def inspect(host: str, port: int = 443) -> None:
    # A default context: validates the cert chain + hostname (secure defaults).
    ctx = ssl.create_default_context()
    # Advertise HTTP/2 then HTTP/1.1 via ALPN; server picks one.
    ctx.set_alpn_protocols(["h2", "http/1.1"])

    raw = socket.create_connection((host, port), timeout=10)
    with ctx.wrap_socket(raw, server_hostname=host) as tls:  # SNI = server_hostname
        print(f"== TLS to {host}:{port} ==")
        print(f"  Protocol : {tls.version()}")            # e.g. TLSv1.3
        cipher, proto, bits = tls.cipher()
        print(f"  Cipher   : {cipher} ({bits} bits)")     # e.g. TLS_AES_256_GCM_SHA384
        print(f"  ALPN     : {tls.selected_alpn_protocol()}")  # h2 / http/1.1 / None

        cert = tls.getpeercert()
        subject = dict(x[0] for x in cert["subject"])
        issuer = dict(x[0] for x in cert["issuer"])
        print(f"  Subject  : {subject.get('commonName', '?')}")
        print(f"  Issuer   : {issuer.get('organizationName', '?')} / "
              f"{issuer.get('commonName', '?')}")
        print(f"  Valid    : {cert['notBefore']}  ->  {cert['notAfter']}")

        # Days until expiry — the metric that prevents cert-expiry outages.
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days = (not_after.replace(tzinfo=timezone.utc)
                - datetime.now(timezone.utc)).days
        print(f"  Expires in: {days} days" + ("  <-- RENEW NOW" if days < 30 else ""))

        sans = [v for k, v in cert.get("subjectAltName", []) if k == "DNS"]
        print(f"  SANs     : {', '.join(sans[:5])}{' ...' if len(sans) > 5 else ''}")

        # Issue a minimal request to prove the channel works. Only meaningful as a
        # text line when ALPN settled on HTTP/1.1; if the server picked h2, the
        # reply is binary HTTP/2 frames, so we just report that instead.
        if tls.selected_alpn_protocol() in (None, "http/1.1"):
            req = (f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                   f"User-Agent: tls-inspect\r\nConnection: close\r\n\r\n")
            tls.sendall(req.encode())
            status_line = tls.recv(4096).split(b"\r\n", 1)[0].decode("latin-1")
            print(f"  HTTP     : {status_line}")
        else:
            print("  HTTP     : (server chose h2; channel verified by handshake)")


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "example.com"
    inspect(host)
```

Typical output:

```
== TLS to example.com:443 ==
  Protocol : TLSv1.3
  Cipher   : TLS_AES_256_GCM_SHA384 (256 bits)
  ALPN     : h2
  Subject  : example.com
  Issuer   : DigiCert Inc / DigiCert Global G3 TLS ECC SHA384 2020 CA1
  Valid    : Jan  1 00:00:00 2026 GMT  ->  Jan  1 23:59:59 2027 GMT
  Expires in: 192 days
  SANs     : example.com, www.example.com
  HTTP     : (server chose h2; channel verified by handshake)
```

### 8.1 Inspecting a certificate with `openssl s_client`

The command every engineer reaches for to debug TLS:

```bash
# Full handshake + the chain the server actually sent (-showcerts).
# -servername sends SNI; without it you get the default vhost's cert.
openssl s_client -connect example.com:443 -servername example.com -showcerts </dev/null

# Just the leaf cert's dates and names:
echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName

# Check the negotiated protocol/cipher and verify the chain (look for "Verify return code: 0 (ok)")
echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null \
  | grep -E "Protocol|Cipher|Verify return code"

# Force TLS 1.3 / a specific version to test support:
openssl s_client -connect example.com:443 -tls1_3 </dev/null
```

`curl -v` gives the same TLS view inline with the HTTP exchange:

```bash
# -v shows the TLS handshake (* lines), request (>), and response (<).
curl -v https://example.com/ 2>&1 | grep -E "SSL connection|ALPN|subject|issuer|^[<>]"

# Watch the connection get reused across two requests (no second handshake):
curl -v https://example.com/ https://example.com/ 2>&1 | grep -iE "Re-using|SSL connection"
```

### 8.2 An ETag conditional-request demo

Demonstrates the `200` → store ETag → `If-None-Match` → `304` loop against any real server.

```python
"""
etag_demo.py — show conditional requests collapsing to 304 Not Modified.
Run: python etag_demo.py https://httpbingo.org/etag/v17abc
(httpbingo's /etag/{tag} endpoint echoes the tag as an ETag and honors
 If-None-Match -> 304.)
"""
import sys
import http.client
from urllib.parse import urlsplit


def get(url: str, extra_headers: dict | None = None):
    parts = urlsplit(url)
    conn = http.client.HTTPSConnection(parts.netloc, timeout=10)
    path = parts.path + (("?" + parts.query) if parts.query else "")
    conn.request("GET", path, headers=extra_headers or {})
    resp = conn.getresponse()
    body = resp.read()
    etag = resp.getheader("ETag")
    conn.close()
    return resp.status, etag, len(body)


def main(url: str) -> None:
    # 1) First fetch: full 200 with a body and an ETag.
    status, etag, n = get(url)
    print(f"GET (cold)            -> {status}, ETag={etag}, body={n} bytes")
    assert status == 200 and etag, "expected a 200 with an ETag"

    # 2) Conditional fetch: send the ETag back. Unchanged -> 304, empty body.
    status2, etag2, n2 = get(url, {"If-None-Match": etag})
    print(f"GET If-None-Match     -> {status2}, body={n2} bytes")
    assert status2 == 304, f"expected 304 Not Modified, got {status2}"
    assert n2 == 0, "a 304 must carry no body"
    print("OK: revalidation collapsed a full body into a 304 (saved the bytes).")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://httpbingo.org/etag/v17abc"
    main(url)
```

### 8.3 Measuring the connection-reuse benefit

Quantifies *why* keep-alive matters: one handshake amortized over many requests vs a handshake per request.

```python
"""
reuse_benefit.py — measure new-connection-per-request vs a single reused
connection. The gap is dominated by repeated TCP+TLS handshakes.
Run: python reuse_benefit.py example.com
"""
import sys
import time
import http.client

N = 8


def per_request_connections(host: str) -> float:
    """Open and tear down a fresh TLS connection for every request (HTTP/1.0 style)."""
    start = time.perf_counter()
    for _ in range(N):
        conn = http.client.HTTPSConnection(host, timeout=10)
        conn.request("HEAD", "/")          # HEAD: no body, isolate handshake cost
        conn.getresponse().read()
        conn.close()                       # tear down -> next request re-handshakes
    return time.perf_counter() - start


def reused_connection(host: str) -> float:
    """One persistent connection (keep-alive) for all N requests."""
    start = time.perf_counter()
    conn = http.client.HTTPSConnection(host, timeout=10)
    for _ in range(N):
        conn.request("HEAD", "/")
        conn.getresponse().read()          # keep the connection open
    conn.close()
    return time.perf_counter() - start


def main(host: str) -> None:
    cold = per_request_connections(host)
    warm = reused_connection(host)
    print(f"{N} requests, new connection each : {cold*1000:7.1f} ms "
          f"({cold/N*1000:.1f} ms/req)")
    print(f"{N} requests, one reused connection: {warm*1000:7.1f} ms "
          f"({warm/N*1000:.1f} ms/req)")
    if warm > 0:
        print(f"Reuse speedup: {cold/warm:.1f}x  "
              f"(the difference is {N-1} avoided TCP+TLS handshakes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "example.com")
```

On a remote host with ~50 ms RTT the per-request version typically runs **3–5× slower** — each request pays the full TCP+TLS handshake again. This is the entire justification for keep-alive, connection pooling, and HTTP/2/3 multiplexing.

---

## 9. Common pitfalls that cause real outages

| Pitfall | Symptom | Fix |
|---|---|---|
| **Certificate expiry** | Total outage; every client errors at the same instant. | Automate renewal (ACME), alert at 30/14/7 days, monitor from outside (§8 `days` check). |
| **Missing intermediate cert** | Works in your browser, fails for fresh clients/`curl`/mobile. | Serve the full chain (leaf + intermediates). Test with `openssl s_client` on a clean box. |
| **Mixed content** | HTTPS page loads an `http://` asset → browser blocks it; broken page, console errors. | Serve every subresource over HTTPS; use `Content-Security-Policy: upgrade-insecure-requests`. |
| **Hostname mismatch** | `curl` "certificate subject name does not match". | SAN must list the exact name; wildcards (`*.x.com`) cover one label only. |
| **Clock skew** | "certificate not yet valid" on a host with a wrong clock. | NTP everywhere; certs are time-bound. |
| **Caching a private response publicly** | One user sees another's data via a shared cache. | `Cache-Control: private` (or `no-store`) on personalized responses; correct `Vary`. |
| **`no-cache` ≠ `no-store` confusion** | Sensitive data stored on disk. | `no-store` for secrets; `no-cache` only means revalidate. |
| **Retrying non-idempotent requests** | Duplicate charges/orders after a transient network blip. | Idempotency keys on POST; only retry idempotent methods automatically. |
| **0-RTT on a mutating request** | Replay attack double-applies the request. | Restrict 0-RTT/early-data to safe, idempotent methods. |

---

## 10. Advanced: 0-RTT replay, post-quantum TLS, OCSP/CT, and SPIFFE identity

### 0-RTT and the replay hazard

TLS 1.3 ([§7](#7-tls-confidentiality-integrity-authentication)) and QUIC
([§6](#6-http10--11--2--3-the-round-trip-war)) offer **0-RTT** resumption: a returning
client sends application data **in the first flight**, saving a round trip. The catch:
0-RTT data is **replayable** — an attacker can capture and resend it, and the server
can't distinguish the replay during the handshake. So 0-RTT is only safe for
**idempotent** requests ([scenarios 04 / system design idempotency]); never put a
non-idempotent operation (a payment, a POST that mutates) on a 0-RTT path. Most stacks
restrict 0-RTT to GETs for this reason.

### Post-quantum TLS — the migration already happening

A "harvest now, decrypt later" adversary records today's TLS traffic to decrypt once
quantum computers can break RSA/ECDH. The mitigation is **hybrid key exchange** —
combining a classical curve (X25519) with a post-quantum KEM (**ML-KEM / Kyber**, e.g.
`X25519MLKEM768`) so the session is secure if *either* holds. Major browsers and CDNs
have **already enabled** this hybrid by default. Signatures (authentication) are
migrating more slowly. Staff takeaway: PQ key exchange is no longer theoretical —
larger handshake messages and new cipher negotiation are landing in production now.

### Proving the cert is still valid — OCSP stapling and CT

A certificate ([§7](#7-tls-confidentiality-integrity-authentication)) can be *revoked*
before it expires. Checking revocation via **OCSP** historically meant the client made
a separate request to the CA (slow, a privacy leak, and a soft-fail that attackers
bypass). **OCSP stapling** fixes this: the server fetches a signed, time-stamped "still
valid" proof from the CA and **staples** it into the handshake — no client-side CA
round trip. **Certificate Transparency (CT)** logs every issued cert to public append-
only logs, so a mis-issued cert for your domain is detectable (monitor CT logs for your
domains — it catches both attacks and rogue internal issuance).

### Service identity — mTLS, SPIFFE/SPIRE, and rotation

In zero-trust meshes ([07 §9](07_load_balancing_proxies.md),
[10](10_cloud_sdn_overlays.md)) every workload authenticates with **mTLS**
([§7](#7-tls-confidentiality-integrity-authentication)). **SPIFFE** standardizes a
workload identity (the SPIFFE ID, delivered as an X.509 SVID or JWT), and **SPIRE**
issues and **auto-rotates** these short-lived certs. Short-lived, auto-rotated identity
is what makes mTLS operable at scale — it removes the cert-expiry outage class
([scenarios 04.7](../enterprise_scenarios/04_network_incidents.md)) and the long-lived-
key theft risk.

---

## Key Takeaways

1. **RTT is the currency.** Every HTTP/TLS evolution (keep-alive → HTTP/2 multiplexing → HTTP/3/QUIC, TLS 1.2 → 1.3 → 0-RTT) exists to cut round-trips. Reason in RTTs, not bandwidth.
2. **HTTP semantics are version-independent** (RFC 9110). Methods (safe/idempotent), status codes, and headers are identical across 1.1/2/3; only the wire framing changes.
3. **Idempotency is load-bearing.** Proxies and clients silently retry idempotent requests; make critical POSTs idempotent with keys or you'll get duplicates.
4. **Caching has two gears**: freshness (`max-age`, zero network) and validation (`ETag`/`If-None-Match` → cheap `304`). The "immutable hashed assets + `no-cache` HTML" pattern gives instant deploys and permanent caching at once.
5. **HTTP/2 fixed application-layer HOL blocking but not transport-layer**; one lost TCP segment still stalls all streams. **HTTP/3/QUIC** fixes it with independent streams over UDP, plus 1-RTT/0-RTT handshakes and connection migration.
6. **Server push is dead** (use `103 Early Hints`); don't design around it.
7. **TLS 1.3** halves the handshake to 1 RTT (0-RTT on resumption), mandates **forward secrecy**, and removed every legacy weak option. The handshake — not bulk encryption — is the cost; reuse connections and resume sessions.
8. **PKI is a chain to a trusted root.** Serve the full chain, match the SAN, and remember revocation is weak (OCSP soft-fails) — which is why the industry moved to short-lived, auto-renewed certs.
9. **mTLS** (client also presents a cert) is the foundation of zero-trust service-to-service auth and the service mesh ([07](07_load_balancing_proxies.md)).
10. **The most common web outages are operational TLS failures** — cert expiry, missing intermediates, mixed content — not code bugs. Monitor certs from outside and automate renewal.

> Read next: [07 — Load Balancing, Proxies & Edge](07_load_balancing_proxies.md) for how these connections are terminated, balanced, and routed at the edge — TLS termination vs passthrough, L4 vs L7, and the proxy/mesh topologies that front every real service.
