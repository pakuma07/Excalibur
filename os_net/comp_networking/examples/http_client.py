"""
http_client.py — A minimal HTTP/1.1 client over a raw socket (incl. chunked encoding)

ENTERPRISE PROBLEM
------------------
HTTP libraries (requests, urllib3) hide a lot. When you're debugging a flaky
gateway, a load balancer that mangles headers, a backend that returns a malformed
chunked body, or you're writing a high-performance proxy/health-checker, you need
to know exactly what bytes go on the wire and how a response is framed. The two
ways HTTP/1.1 delimits a response body are the crux of countless bugs and even
security issues (HTTP request smuggling lives in the seam between Content-Length
and Transfer-Encoding):

  * Content-Length: <n>  — read exactly n body bytes.
  * Transfer-Encoding: chunked — body arrives as a series of
    "<hexlen>\\r\\n<data>\\r\\n" chunks, terminated by a "0\\r\\n\\r\\n" chunk.
    Used when the server streams a response of unknown total size.

This script builds an HTTP/1.1 request by hand, sends it over a raw TCP socket,
and parses the status line, headers, and body — handling BOTH framing modes. It
is demoed against a tiny `http.server` backend started in-process, with one
endpoint returning a Content-Length body and another returning a chunked body.

HOW TO RUN
----------
    py http_client.py

Cross-platform: raw sockets + http.server are stdlib and identical on Windows,
Linux and macOS. Runs fully self-contained on 127.0.0.1.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
CRLF = b"\r\n"


# --------------------------------------------------------------------------
# In-process backend: a tiny HTTP server with a normal and a chunked endpoint.
# --------------------------------------------------------------------------
class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence default stderr access logging for clean demo output

    def do_GET(self):
        if self.path == "/chunked":
            # Stream a chunked response. We write raw chunks ourselves so the
            # client's chunk-decoder gets exercised.
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in (b"chunk-one ", b"chunk-two ", b"chunk-three"):
                self.wfile.write(b"%X\r\n%s\r\n" % (len(piece), piece))
            self.wfile.write(b"0\r\n\r\n")   # terminating zero-length chunk
        else:
            body = b"hello over content-length"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def start_backend():
    server = ThreadingHTTPServer((HOST, 0), DemoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


# --------------------------------------------------------------------------
# The hand-rolled HTTP/1.1 client.
# --------------------------------------------------------------------------
class RawHTTPResponse:
    def __init__(self, status, reason, headers, body):
        self.status = status
        self.reason = reason
        self.headers = headers          # dict, lowercased keys
        self.body = body                # bytes


def _read_until(sock, marker):
    """Read from the socket until `marker` appears; return (before, rest)."""
    buf = bytearray()
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
    idx = buf.find(marker)
    if idx == -1:
        return bytes(buf), b""
    return bytes(buf[:idx]), bytes(buf[idx + len(marker):])


def _recv_exactly(sock, n, prefetched=b""):
    """Return exactly n bytes, using any already-buffered `prefetched` data."""
    buf = bytearray(prefetched)
    while len(buf) < n:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf[:n]), bytes(buf[n:])


def _decode_chunked(sock, prefetched):
    """Decode a Transfer-Encoding: chunked body from socket + buffered bytes."""
    body = bytearray()
    buf = bytes(prefetched)

    def fill_until(marker):
        nonlocal buf
        while marker not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk

    while True:
        fill_until(CRLF)
        line, _, buf = buf.partition(CRLF)
        # Chunk size is hex; may carry ";ext" chunk-extensions we ignore.
        size = int(line.split(b";")[0].strip() or b"0", 16)
        if size == 0:
            # Read (and discard) the trailing CRLF after the last chunk.
            fill_until(CRLF)
            break
        # Ensure we have the chunk data plus its trailing CRLF.
        while len(buf) < size + 2:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        body.extend(buf[:size])
        buf = buf[size + 2:]            # skip data + trailing CRLF
    return bytes(body)


def http_get(host, port, path="/", timeout=5.0):
    """Perform an HTTP/1.1 GET over a raw socket and parse the response."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        # Build the request line + headers by hand. Connection: close keeps the
        # framing simple (server closes after the body on CL responses).
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"User-Agent: raw-http-client/1.0\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("ascii")
        sock.sendall(request)

        # 1) Read up to the blank line separating headers from body.
        head_bytes, rest = _read_until(sock, CRLF + CRLF)
        head_lines = head_bytes.split(CRLF)

        # 2) Parse the status line: "HTTP/1.1 200 OK".
        proto, status, reason = (head_lines[0].decode("latin1").split(" ", 2)
                                 + ["", ""])[:3]
        status = int(status)

        # 3) Parse headers into a lowercased dict.
        headers = {}
        for line in head_lines[1:]:
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.decode("latin1").strip().lower()] = \
                    v.decode("latin1").strip()

        # 4) Frame the body: chunked takes precedence over Content-Length.
        if headers.get("transfer-encoding", "").lower() == "chunked":
            body = _decode_chunked(sock, rest)
        elif "content-length" in headers:
            n = int(headers["content-length"])
            body, _ = _recv_exactly(sock, n, rest)
        else:
            # No framing header (Connection: close): read until EOF.
            body = bytearray(rest)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body.extend(chunk)
            body = bytes(body)

        return RawHTTPResponse(status, reason, headers, body)


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("Minimal HTTP/1.1 client over a raw socket")
    print("=" * 70)

    server, port = start_backend()
    print(f"[backend] http.server on {HOST}:{port}")

    # --- Case 1: Content-Length framed response ---
    print("\n[GET /] Content-Length framing")
    resp = http_get(HOST, port, "/")
    print(f"    status : {resp.status} {resp.reason}")
    print(f"    headers: content-type={resp.headers.get('content-type')!r} "
          f"content-length={resp.headers.get('content-length')!r}")
    print(f"    body   : {resp.body!r}")
    assert resp.status == 200
    assert resp.body == b"hello over content-length"
    assert int(resp.headers["content-length"]) == len(resp.body)
    print("    verified ✓")

    # --- Case 2: chunked transfer-encoding ---
    print("\n[GET /chunked] Transfer-Encoding: chunked framing")
    resp = http_get(HOST, port, "/chunked")
    print(f"    status : {resp.status} {resp.reason}")
    print(f"    transfer-encoding: {resp.headers.get('transfer-encoding')!r}")
    print(f"    reassembled body : {resp.body!r}")
    assert resp.status == 200
    assert resp.headers.get("transfer-encoding") == "chunked"
    assert resp.body == b"chunk-one chunk-two chunk-three"
    print("    chunk reassembly verified ✓")

    server.shutdown()
    print("\nAll assertions passed. Both framing modes parsed correctly. ✓")
