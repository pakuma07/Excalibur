"""
tcp_handshake_observer.py — Walk the TCP connection lifecycle and tune socket options

ENTERPRISE PROBLEM
------------------
Every TCP connection costs a 3-way handshake (SYN / SYN-ACK / ACK) — one full
round trip before a single byte of application data flows. At enterprise scale
this RTT tax dominates latency for short-lived requests, which is why we obsess
over connection pooling, keep-alive, and TLS session resumption. And the default
socket options are frequently *wrong* for your workload:

  * TCP_NODELAY disables Nagle's algorithm. Nagle batches small writes to avoid
    flooding the network with tiny packets — great for bulk transfer, terrible
    for latency-sensitive RPC/chat where it can add ~40 ms of delay when it
    interacts badly with delayed ACKs. Almost every RPC/database client sets
    TCP_NODELAY.
  * SO_REUSEADDR lets a server rebind its port immediately after restart instead
    of waiting out the TIME_WAIT state of old connections. Without it, a deploy
    can fail with "address already in use".
  * SO_KEEPALIVE makes the kernel probe an idle connection to detect a peer that
    vanished (crash, cable pull) — otherwise a half-open connection can sit
    forever consuming a slot.

This script opens a REAL TCP connection to an in-process server on 127.0.0.1,
annotates each lifecycle step (bind → listen → connect/handshake →
ESTABLISHED → data → FIN/close), and demonstrates setting and reading back each
of those socket options so you can see they actually took effect.

HOW TO RUN
----------
    py tcp_handshake_observer.py

Cross-platform: all options used (TCP_NODELAY, SO_REUSEADDR, SO_KEEPALIVE) exist
on Windows, Linux and macOS. The fine-grained keepalive *timers*
(TCP_KEEPIDLE/TCP_KEEPINTVL) are Linux-only and are guarded so this still runs on
Windows.
"""

import socket
import threading
import time

HOST = "127.0.0.1"


def start_echo_server():
    """A tiny blocking echo server in a background thread. Returns its port."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR on the SERVER side: rebind immediately after restart.
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, 0))      # port 0 => OS picks a free ephemeral port
    srv.listen(1)
    port = srv.getsockname()[1]

    def loop():
        conn, _addr = srv.accept()    # blocks until handshake completes
        with conn:
            while True:
                data = conn.recv(4096)
                if not data:          # peer sent FIN
                    break
                conn.sendall(data)
        srv.close()

    threading.Thread(target=loop, daemon=True).start()
    return port


def describe_state(sock):
    """Best-effort read of the TCP state for annotation (Linux only)."""
    if hasattr(socket, "TCP_INFO"):
        try:
            # struct tcp_info's first byte is tcpi_state.
            raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 1)
            return f"tcpi_state={raw[0]}"
        except OSError:
            pass
    return "(TCP_INFO not available on this OS — Windows uses a different API)"


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("TCP connection lifecycle + socket option tuning")
    print("=" * 70)

    # ---- Server side: bind / listen ----
    print("\n[1] SERVER bind() + listen()")
    port = start_echo_server()
    print(f"    server bound to {HOST}:{port}, SO_REUSEADDR set, listening")

    # ---- Client side: create socket and set options BEFORE connect ----
    print("\n[2] CLIENT socket() — set & verify options before connecting")
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # TCP_NODELAY: disable Nagle for low-latency small writes.
    cli.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    nodelay = cli.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    print(f"    TCP_NODELAY  set=1 -> readback={nodelay}  (Nagle disabled)")
    assert nodelay == 1

    # SO_REUSEADDR on client too (harmless; usually a server concern).
    cli.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reuse = cli.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
    print(f"    SO_REUSEADDR set=1 -> readback={reuse}")
    assert reuse == 1

    # SO_KEEPALIVE: detect dead peers on idle connections.
    cli.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    keep = cli.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
    print(f"    SO_KEEPALIVE set=1 -> readback={keep}")
    assert keep == 1

    # Fine-grained keepalive timers are Linux-only; guard for cross-platform.
    if hasattr(socket, "TCP_KEEPIDLE"):
        cli.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        idle = cli.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE)
        print(f"    TCP_KEEPIDLE set=60s -> readback={idle}  (Linux-only)")
    else:
        print("    TCP_KEEPIDLE  not available on this OS (expected on Windows)")

    # ---- The 3-way handshake happens inside connect() ----
    print("\n[3] CLIENT connect() — TCP 3-way handshake (SYN / SYN-ACK / ACK)")
    t0 = time.perf_counter()
    cli.connect((HOST, port))      # SYN ->, <- SYN-ACK, ACK ->
    handshake_ms = (time.perf_counter() - t0) * 1000
    print(f"    handshake completed in {handshake_ms:.3f} ms — now ESTABLISHED")
    local = cli.getsockname()
    peer = cli.getpeername()
    print(f"    4-tuple: local {local} <-> peer {peer}")
    print(f"    state probe: {describe_state(cli)}")

    # ---- Data transfer over the ESTABLISHED connection ----
    print("\n[4] DATA transfer on the ESTABLISHED connection")
    payload = b"the quick brown fox"
    cli.sendall(payload)
    echoed = cli.recv(4096)
    print(f"    sent {payload!r}")
    print(f"    recv {echoed!r}")
    assert echoed == payload, "echo mismatch"
    print("    round-trip verified ✓")

    # ---- Graceful teardown: FIN handshake ----
    print("\n[5] CLOSE — FIN handshake (active close -> TIME_WAIT)")
    # shutdown(SHUT_WR) sends a FIN but lets us still read. close() does both.
    cli.shutdown(socket.SHUT_WR)
    print("    sent FIN (shutdown SHUT_WR); peer will see EOF and FIN back")
    cli.close()
    print("    socket closed; the active closer now sits in TIME_WAIT")

    print("\nAll assertions passed. Lifecycle walked end to end. ✓")
