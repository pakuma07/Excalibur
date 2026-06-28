"""
reverse_proxy.py — A runnable L7 reverse proxy: round-robin, least-connections, health checks

ENTERPRISE PROBLEM
------------------
A reverse proxy / L7 load balancer (nginx, HAProxy, Envoy, ALB) sits in front of a
pool of backends and is responsible for three things this demo makes concrete:

  1. BACKEND SELECTION. How do you spread requests?
     * Round-robin: simple, fair when backends and requests are uniform — but
       blind to how busy each backend actually is.
     * Least-connections: send the next request to the backend with the fewest
       in-flight requests. Far better when request durations vary (the classic
       failure of round-robin: it keeps feeding a backend that's stuck on slow
       requests). This is the default many shops standardize on.
  2. ACTIVE HEALTH CHECKS. The proxy periodically probes each backend; an
     unhealthy backend is removed from rotation so users never hit it. When it
     recovers, it's added back. Getting this wrong causes either blackholed
     traffic (too slow to eject) or flapping/thundering herds (too aggressive).
  3. IT'S L7. It terminates the client connection, reads the HTTP request, picks
     a backend, opens its OWN connection to that backend, and copies the response
     back. That's what lets a proxy route by path, retry, and rewrite — at the
     cost of parsing every request.

This script spins up 3 in-process HTTP backends (one deliberately made
unhealthy), runs the proxy in front of them, sends a burst of requests through
it, and PRINTS the request distribution — asserting that traffic balanced across
the healthy backends and that the unhealthy one received nothing.

HOW TO RUN
----------
    py reverse_proxy.py

Cross-platform: threads + sockets + http.server, all stdlib. Self-contained on
127.0.0.1.
"""

import socket
import threading
import time
import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"


# --------------------------------------------------------------------------
# Backends: each reports its own name so we can see who served each request.
# --------------------------------------------------------------------------
class Backend:
    def __init__(self, name, healthy=True):
        self.name = name
        self.healthy_response = healthy   # if False, /health returns 503
        self.served = 0
        self._lock = threading.Lock()
        bn = self.name

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/health":
                    code = 200 if outer.healthy_response else 503
                    self.send_response(code)
                    self.end_headers()
                    self.wfile.write(b"ok" if code == 200 else b"bad")
                    return
                with outer._lock:
                    outer.served += 1
                # Small artificial work so concurrent requests actually overlap
                # in-flight — this is what gives least-connections something to
                # balance against (with instant responses every strategy looks
                # the same because nothing is ever concurrently in-flight).
                time.sleep(0.02)
                body = bn.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer((HOST, 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def addr(self):
        return (HOST, self.port)

    def stop(self):
        self.httpd.shutdown()


# --------------------------------------------------------------------------
# Backend pool with health state and in-flight connection counts.
# --------------------------------------------------------------------------
class PoolMember:
    def __init__(self, backend):
        self.backend = backend
        self.healthy = True
        self.inflight = 0          # active requests for least-connections


class ReverseProxy:
    def __init__(self, members, strategy="round_robin", check_interval=0.3):
        self.members = [PoolMember(b) for b in members]
        self.strategy = strategy
        self._rr_index = 0
        self._lock = threading.Lock()
        self._check_interval = check_interval
        self._running = True

        # Listening socket for incoming client connections.
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((HOST, 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]

        threading.Thread(target=self._health_loop, daemon=True).start()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    # ---- Active health checking ----
    def _health_loop(self):
        while self._running:
            for m in self.members:
                host, port = m.backend.addr()
                try:
                    with urllib.request.urlopen(
                            f"http://{host}:{port}/health", timeout=0.5) as r:
                        m.healthy = (r.status == 200)
                except Exception:
                    m.healthy = False
            time.sleep(self._check_interval)

    def _healthy_members(self):
        return [m for m in self.members if m.healthy]

    # ---- Backend selection strategies ----
    def _select(self):
        healthy = self._healthy_members()
        if not healthy:
            return None
        with self._lock:
            if self.strategy == "least_connections":
                # Pick the healthy member with the fewest in-flight requests.
                return min(healthy, key=lambda m: m.inflight)
            # round_robin (default)
            self._rr_index = (self._rr_index + 1) % len(healthy)
            return healthy[self._rr_index]

    # ---- Connection handling (the L7 hop) ----
    def _accept_loop(self):
        self.listener.settimeout(0.5)
        while self._running:
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,),
                             daemon=True).start()

    def _handle(self, client):
        with client:
            # Read the client's HTTP request (headers; assume no body for GET).
            client.settimeout(2.0)
            request = self._read_http_message(client)
            if not request:
                return
            member = self._select()
            if member is None:
                client.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                               b"Content-Length: 0\r\n\r\n")
                return
            member.inflight += 1
            try:
                response = self._proxy_to_backend(member.backend, request)
                client.sendall(response)
            finally:
                member.inflight -= 1

    @staticmethod
    def _read_http_message(sock):
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _proxy_to_backend(backend, request):
        # Open the proxy's OWN connection to the chosen backend and relay.
        with socket.create_connection(backend.addr(), timeout=2.0) as up:
            up.sendall(request)
            resp = bytearray()
            up.settimeout(2.0)
            try:
                while True:
                    chunk = up.recv(4096)
                    if not chunk:
                        break
                    resp.extend(chunk)
            except socket.timeout:
                pass
            return bytes(resp)

    def stop(self):
        self._running = False
        try:
            self.listener.close()
        except OSError:
            pass


def _client_get(port, path="/"):
    with urllib.request.urlopen(f"http://{HOST}:{port}{path}", timeout=2) as r:
        return r.read().decode()


def _run_scenario(strategy, n_requests=60):
    print(f"\n--- strategy: {strategy} ---")
    b1 = Backend("backend-1")
    b2 = Backend("backend-2")
    b3 = Backend("backend-3", healthy=False)   # this one is DOWN
    proxy = ReverseProxy([b1, b2, b3], strategy=strategy)

    # Give the first health check time to run so b3 is ejected before traffic.
    time.sleep(0.6)
    healthy = [m.backend.name for m in proxy.members if m.healthy]
    print(f"    healthy backends after first health check: {healthy}")

    # Fire requests CONCURRENTLY so multiple are in-flight at once. This is
    # essential for least-connections to differ from round-robin: with strictly
    # serial requests nothing is ever concurrently in-flight, so every backend
    # always shows 0 connections and `min` would trivially pick the first.
    dist = defaultdict(int)
    dist_lock = threading.Lock()

    def worker():
        who = _client_get(proxy.port)
        with dist_lock:
            dist[who] += 1

    workers = [threading.Thread(target=worker) for _ in range(n_requests)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    print(f"    request distribution: {dict(dist)}")

    # Assertions: unhealthy backend got nothing; load spread over healthy ones.
    assert "backend-3" not in dist, "traffic reached the unhealthy backend!"
    assert dist["backend-1"] > 0 and dist["backend-2"] > 0, \
        "traffic did not spread across healthy backends"
    assert sum(dist.values()) == n_requests
    # Round-robin should be roughly even; allow generous slack for timing.
    if strategy == "round_robin":
        assert abs(dist["backend-1"] - dist["backend-2"]) <= n_requests * 0.4

    proxy.stop()
    for b in (b1, b2, b3):
        b.stop()
    return dict(dist)


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("L7 reverse proxy: round-robin + least-connections + health checks")
    print("=" * 70)

    rr = _run_scenario("round_robin")
    lc = _run_scenario("least_connections")

    print("\nResults")
    print(f"    round_robin      : {rr}")
    print(f"    least_connections: {lc}")
    print("\nAll assertions passed. Unhealthy backend ejected; load balanced. ✓")
