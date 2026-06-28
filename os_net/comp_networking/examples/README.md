# Examples — Runnable Computer-Networking Demos

> **Audience:** staff/principal engineers and anyone learning the concepts in this
> series by *running* them. Each script in this folder is a **standalone,
> self-verifying** program that demonstrates one real computer-networking problem
> solved the way it's solved at enterprise scale — the C10k event loop, raw DNS on
> the wire, an L7 reverse proxy, consistent hashing, edge rate limiting, and more.
>
> Every file uses the **Python standard library only** (`socket`, `selectors`,
> `ssl`, `http`, `struct`, `threading`, `hashlib`, `hmac`), runs **end-to-end with
> no external service** (client/server demos spin up the server on `127.0.0.1` in a
> background thread inside the same process), is **heavily commented**, and **prints
> clear output while asserting correctness**. The network-dependent scripts
> degrade gracefully offline.

---

## How to run

These were written and verified on **Windows + Python 3.11** using the `py`
launcher. They are cross-platform (Windows / Linux / macOS).

```powershell
py epoll_echo_server.py
py tcp_handshake_observer.py
py dns_query.py
py http_client.py
py tls_inspect.py
py reverse_proxy.py
py consistent_hash.py
py subnet_calculator.py
py token_bucket_ratelimiter.py
```

(`python <file>.py` works too if `python` is on your PATH. On the Bash side the
Windows Store `python3` stub will *not* work — use `py` or PowerShell `python`.)

A script exits `0` and prints `All assertions passed` / `✓` lines when it
succeeds.

---

## Index

| # | File | Enterprise problem it solves | Concept doc |
|---|------|------------------------------|-------------|
| 1 | [`epoll_echo_server.py`](epoll_echo_server.py) | The **C10k problem**: serve thousands of concurrent connections from **one thread** with an event loop (`selectors`) instead of a thread-per-connection — the nginx / Redis / Node concurrency model. | [04 — Transport: TCP & UDP](../04_transport_tcp_udp.md), [08 — Performance & Tuning](../08_network_performance_tuning.md) |
| 2 | [`tcp_handshake_observer.py`](tcp_handshake_observer.py) | The **TCP connection lifecycle** and the **socket options that decide your latency**: 3-way handshake cost, `TCP_NODELAY` (Nagle), `SO_REUSEADDR` (fast restart), `SO_KEEPALIVE` (dead-peer detection) — set and read back. | [04 — Transport: TCP & UDP](../04_transport_tcp_udp.md) |
| 3 | [`dns_query.py`](dns_query.py) | **DNS on the wire**: build and parse a raw RFC 1035 query/response with `struct`, including **name-compression pointers**. Understand the Internet's control plane and largest blast-radius SPOF. | [05 — DNS](../05_dns.md) |
| 4 | [`http_client.py`](http_client.py) | **HTTP/1.1 framing** from scratch over a raw socket: status line, headers, and **both body-framing modes** (`Content-Length` and `Transfer-Encoding: chunked`) — the seam where request-smuggling bugs live. | [06 — HTTP & TLS](../06_http_tls.md) |
| 5 | [`tls_inspect.py`](tls_inspect.py) | **TLS handshake inspection**: negotiated version, cipher suite, and certificate fields (incl. **expiry** — the #1 self-inflicted outage). Generates a self-signed cert at runtime with **pure stdlib** (RSA keygen + hand-built X.509 DER). | [06 — HTTP & TLS](../06_http_tls.md), [09 — Network Security](../09_network_security.md) |
| 6 | [`reverse_proxy.py`](reverse_proxy.py) | A runnable **L7 reverse proxy**: **round-robin** vs **least-connections** backend selection, plus **active health checks** that eject a failing backend from rotation. Routes real traffic through in-process backends and prints the distribution. | [07 — Load Balancing & Proxies](../07_load_balancing_proxies.md) |
| 7 | [`consistent_hash.py`](consistent_hash.py) | **Consistent hashing with virtual nodes** — the partitioning behind every distributed cache / sharded store / sticky LB. Proves balanced load *and* **minimal key remapping** (O(K/N), not O(K)) when a node is added or removed. | [07 — Load Balancing & Proxies](../07_load_balancing_proxies.md), [05 — DNS](../05_dns.md) |
| 8 | [`subnet_calculator.py`](subnet_calculator.py) | **IP address planning**: CIDR math (network / broadcast / usable range / count), subnet splitting and **VLSM** right-sizing, plus a **longest-prefix-match** routing-table lookup — how every router picks a route. | [03 — Network Layer, IP & Routing](../03_network_layer_routing.md) |
| 9 | [`token_bucket_ratelimiter.py`](token_bucket_ratelimiter.py) | The **API-gateway edge security pattern**: a **token-bucket rate limiter** (rate + burst) and **HMAC request signing** with **replay prevention** (timestamp skew window + nonce cache, constant-time compare). | [09 — Network Security](../09_network_security.md), [07 — Load Balancing & Proxies](../07_load_balancing_proxies.md) |

---

## Diagnostic scripts (accompany the incident runbooks)

These parse live kernel state on **Linux** (`/proc/net/*`, `/proc/sys/net/*`) to
diagnose the network failure modes in
[`../../enterprise_scenarios/04_network_incidents.md`](../../enterprise_scenarios/04_network_incidents.md).
On non-Linux they fall back to embedded **sample data** and a `--selftest` that
asserts the parsing/decision logic, so they run everywhere. `cert_expiry_check.py`
works live on any OS with network egress.

```powershell
py socket_state_summary.py --selftest    # on Linux: py socket_state_summary.py
py port_exhaustion_probe.py --selftest
py tcp_retrans_monitor.py --selftest     # on Linux: py tcp_retrans_monitor.py
py cert_expiry_check.py --selftest       # live: py cert_expiry_check.py example.com
```

| File | What it diagnoses | Runbook |
|------|-------------------|---------|
| [`socket_state_summary.py`](socket_state_summary.py) | TCP states from `/proc/net/tcp` — spot **TIME_WAIT floods** and **CLOSE_WAIT pileups** (socket leak) and **SYN_RECV** surges. | [04.1, 04.2, 04.4](../../enterprise_scenarios/04_network_incidents.md) |
| [`port_exhaustion_probe.py`](port_exhaustion_probe.py) | **Ephemeral-port utilization** vs `ip_local_port_range`, with the top churn destination — the "Cannot assign requested address" precursor. | [04.1](../../enterprise_scenarios/04_network_incidents.md) |
| [`tcp_retrans_monitor.py`](tcp_retrans_monitor.py) | **TCP retransmit rate** over an interval from `/proc/net/snmp` — the host-wide packet-loss / lossy-path signal. | [04.3](../../enterprise_scenarios/04_network_incidents.md) |
| [`cert_expiry_check.py`](cert_expiry_check.py) | **TLS certificate days-to-expiry** with WARNING/CRITICAL/EXPIRED severity — the highest-value TLS alert. | [04.7](../../enterprise_scenarios/04_network_incidents.md) |

---

## Design notes shared by all examples

- **Self-contained.** Client/server demos start the server on `127.0.0.1:0` (OS
  picks a free port) in a daemon thread, then drive it from the same process.
  Nothing listens on a fixed port; nothing needs Docker or a second terminal.
- **Self-verifying.** Each script ends in `assert`s that fail loudly if the
  behaviour is wrong, so they double as regression tests. Run them in a loop in CI
  if you like.
- **Offline-safe.** The only scripts that touch the public network are
  `dns_query.py` (queries `1.1.1.1`) and the *fallback* path of `tls_inspect.py`.
  Both catch timeouts/`OSError`, print a clear note, and still pass their offline
  self-checks — they never hard-fail a test box with no egress.
- **Windows-first, cross-platform.** Verified on Windows + Python 3.11. The
  `selectors` module uses `select` on Windows (fine for these demos; swap to
  `epoll`/`kqueue` automatically on Linux/macOS). Linux-only knobs (e.g.
  `TCP_KEEPIDLE`, `TCP_INFO`) are feature-detected and skipped elsewhere with a
  comment, so every file *runs* on Windows and at least *parses* everywhere.
- **Console encoding.** Each `__main__` switches stdout to UTF-8 where supported
  so the `✓` status glyphs render on legacy Windows (cp1252) consoles.
