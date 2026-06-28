"""
epoll_echo_server.py — Single-threaded event-loop TCP echo server (the nginx/Redis model)

ENTERPRISE PROBLEM
------------------
The naive "one thread (or process) per connection" server model collapses at scale:
10,000 concurrent connections means 10,000 threads, each with its own stack
(~1 MB), and the kernel scheduler thrashing on context switches. This is the
classic "C10k problem". nginx, Redis, HAProxy, Node.js and Envoy all solve it the
same way: a SINGLE thread runs an *event loop* that asks the OS "which of my
thousands of sockets is ready right now?" and only touches those. The OS
readiness primitive is epoll (Linux), kqueue (BSD/macOS) or IOCP/select
(Windows). Python's `selectors` module picks the best one available automatically
(epoll on Linux, select on Windows — which is fine for this demo).

The win: memory and scheduling cost grow with *active* work, not with the number
of idle connections. One thread can hold tens of thousands of mostly-idle
connections (think WebSocket fan-out, chat, pub/sub) for almost no cost.

The catch (and why you must understand it): the event loop is cooperative. If any
callback blocks — a slow disk read, a synchronous DB call, a CPU-heavy loop — it
stalls EVERY connection, because there is only one thread. Event-loop servers
must keep every handler non-blocking and fast.

HOW TO RUN
----------
    py epoll_echo_server.py
    (or: python epoll_echo_server.py)

Runs end-to-end with no external dependency: it starts the event-loop server in a
background thread, fires many concurrent client connections at it, and asserts
that the single server thread served them all correctly.

Cross-platform: uses `selectors.DefaultSelector`, which is `select`-based on
Windows. Same code, same semantics; only the underlying syscall differs.
"""

import selectors
import socket
import threading
import time

HOST = "127.0.0.1"


class EventLoopEchoServer:
    """A single-threaded, non-blocking TCP echo server driven by one selector.

    The entire server — accepting new connections AND echoing data on existing
    ones — happens in ONE thread inside ONE `while` loop. No per-connection
    threads. This is the structural heart of nginx/Redis/Node.
    """

    def __init__(self, host=HOST, port=0):
        # A selector multiplexes readiness events across many file objects.
        # DefaultSelector == EpollSelector on Linux, SelectSelector on Windows.
        self.selector = selectors.DefaultSelector()

        # The listening socket. We set it non-blocking so accept() never stalls
        # the loop: if no connection is pending we'd get BlockingIOError, but
        # because the selector only tells us about the listener when a connection
        # IS pending, accept() will succeed.
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR lets us rebind the port immediately after restart
        # (otherwise it lingers in TIME_WAIT). Standard for any server.
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(128)  # backlog: pending connections the kernel queues
        self.listener.setblocking(False)

        # Register the listener for READ readiness. "Readable" on a listening
        # socket means "a new connection is waiting to be accepted".
        # We tag it with data=None so the loop knows it's the listener.
        self.selector.register(self.listener, selectors.EVENT_READ, data=None)

        self.port = self.listener.getsockname()[1]
        self.connections_served = 0     # count of connections we fully handled
        self.bytes_echoed = 0
        self._running = False

    def _accept(self):
        """Called when the listener is readable: accept ALL pending connections."""
        # There may be several connections queued; drain them in a loop.
        while True:
            try:
                conn, addr = self.listener.accept()
            except BlockingIOError:
                break  # no more pending connections right now
            conn.setblocking(False)
            # Register the new client socket for read readiness. We attach the
            # peer address as `data` so we can identify it in the event loop.
            self.selector.register(conn, selectors.EVENT_READ, data=addr)

    def _service(self, key, mask):
        """Called when an established client socket is readable: echo its data."""
        conn = key.fileobj
        try:
            data = conn.recv(4096)  # non-blocking; selector guaranteed readiness
        except (BlockingIOError, ConnectionResetError):
            data = b""
        if data:
            # Echo it straight back. (In a real server you'd buffer and register
            # for EVENT_WRITE if the send buffer were full; for a demo with small
            # payloads a single send is fine.)
            try:
                conn.sendall(data)
                self.bytes_echoed += len(data)
            except OSError:
                self._close(conn)
        else:
            # Empty recv => peer closed the connection (clean FIN).
            self._close(conn)

    def _close(self, conn):
        # Always unregister from the selector BEFORE closing the fd.
        try:
            self.selector.unregister(conn)
        except KeyError:
            pass
        conn.close()
        self.connections_served += 1

    def serve_forever(self):
        self._running = True
        while self._running:
            # THE EVENT LOOP. select(timeout) blocks until at least one
            # registered socket is ready, then returns only the ready ones.
            # This is the whole magic: we never poll idle sockets.
            events = self.selector.select(timeout=0.5)
            for key, mask in events:
                if key.data is None:
                    self._accept()        # the listener fired
                else:
                    self._service(key, mask)  # a client fired

    def stop(self):
        self._running = False

    def close(self):
        self.selector.close()
        self.listener.close()


def _client_roundtrip(port, message):
    """One blocking client: connect, send, read the echo back, return it."""
    with socket.create_connection((HOST, port), timeout=5) as s:
        s.sendall(message)
        # Read exactly len(message) bytes back (echo is same length).
        chunks = bytearray()
        while len(chunks) < len(message):
            part = s.recv(4096)
            if not part:
                break
            chunks.extend(part)
    return bytes(chunks)


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("Single-threaded event-loop echo server (the nginx/Redis model)")
    print("Selector backend in use:", type(selectors.DefaultSelector()).__name__)
    print("=" * 70)

    server = EventLoopEchoServer()
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"[server] listening on {HOST}:{server.port} in ONE background thread")
    time.sleep(0.2)  # let the loop come up

    # Fire MANY concurrent clients. They are all served by the single server
    # thread above — that is the point being proved.
    N = 50
    results = {}
    lock = threading.Lock()

    def run_client(i):
        msg = f"hello-from-client-{i:03d}".encode()
        echoed = _client_roundtrip(server.port, msg)
        with lock:
            results[i] = (msg, echoed)

    threads = [threading.Thread(target=run_client, args=(i,)) for i in range(N)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    # Verify every client got its OWN message echoed back correctly.
    for i, (sent, got) in results.items():
        assert sent == got, f"client {i}: echo mismatch {sent!r} != {got!r}"

    print(f"[clients] {N} concurrent clients completed in {elapsed*1000:.1f} ms")
    print(f"[verify ] all {N} echoes matched exactly  ✓")

    # Give the loop a moment to process the FINs and bump its counter.
    time.sleep(0.3)
    server.stop()
    server_thread.join(timeout=2)

    print(f"[server ] connections served by the single thread: "
          f"{server.connections_served}")
    print(f"[server ] total bytes echoed: {server.bytes_echoed}")

    assert server.connections_served >= N, (
        f"expected >= {N} served, got {server.connections_served}")
    assert len(results) == N

    server.close()
    print("\nAll assertions passed. One thread served all connections. ✓")
