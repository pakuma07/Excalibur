"""
tls_inspect.py — Inspect a TLS handshake: version, cipher, and certificate fields

ENTERPRISE PROBLEM
------------------
TLS is non-negotiable at enterprise scale, and the details bite you constantly:
which protocol version got negotiated (TLS 1.2 vs 1.3 — old clients and
middleboxes still force downgrades), which cipher suite (forward secrecy? a
deprecated suite your compliance scanner will flag?), and the certificate chain
(EXPIRY — the #1 cause of self-inflicted outages — plus SANs, issuer, validity
dates). SREs script exactly this kind of inspection to monitor cert expiry across
thousands of endpoints and to confirm a TLS-terminating load balancer is
presenting the right cert and negotiating an approved protocol version.

This script stands up an in-process TLS server using a SELF-SIGNED certificate
that it generates AT RUNTIME using ONLY the Python standard library — no openssl
binary, no `cryptography` package. It does this by:
  * generating an RSA keypair with stdlib `secrets` + Miller-Rabin primality, and
  * hand-encoding a v3 X.509 certificate in DER (ASN.1) and signing it with
    PKCS#1 v1.5 / SHA-256.
The `ssl` module then loads that PEM cert+key, the client connects with hostname
verification against the same cert (pinned), and we print the negotiated TLS
version, cipher suite, and certificate fields.

If anything in the local path fails on an exotic build, it falls back to
inspecting a public TLS endpoint and degrades gracefully with no network.

HOW TO RUN
----------
    py tls_inspect.py

Cross-platform: the `ssl` module wraps the platform TLS library (OpenSSL in the
python.org Windows build). The cert generator is pure Python, so it runs the same
on Windows, Linux and macOS. Key generation takes a fraction of a second.
"""

import base64
import hashlib
import os
import secrets
import socket
import ssl
import tempfile
import threading
import time

HOST = "127.0.0.1"


# ==========================================================================
# Pure-stdlib RSA keypair generation (no third-party crypto library).
# This is for a DISPOSABLE self-signed DEMO cert only — not production key gen.
# ==========================================================================
def _is_prime(n, rounds=20):
    """Miller-Rabin probabilistic primality test."""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        cand = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(cand):
            return cand


def generate_rsa(bits=2048):
    """Generate an RSA private key as a dict of its CRT parameters."""
    e = 65537
    while True:
        p, q = _gen_prime(bits // 2), _gen_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if phi % e == 0:
            continue
        d = pow(e, -1, phi)
        return {"n": p * q, "e": e, "d": d, "p": p, "q": q,
                "dp": d % (p - 1), "dq": d % (q - 1), "qinv": pow(q, -1, p)}


# ==========================================================================
# Minimal DER (ASN.1) encoder — just enough for an X.509 v3 certificate.
# ==========================================================================
def _der_len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _der(tag, body):
    return bytes([tag]) + _der_len(len(body)) + body


def _der_int(x):
    if x == 0:
        b = b"\x00"
    else:
        b = x.to_bytes((x.bit_length() + 7) // 8, "big")
        if b[0] & 0x80:          # ensure positive (leading sign bit clear)
            b = b"\x00" + b
    return _der(0x02, b)


def _seq(*items):
    return _der(0x30, b"".join(items))


def _set(*items):
    return _der(0x31, b"".join(items))


def _oid(dotted):
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        if p == 0:
            body += b"\x00"
            continue
        stack = []
        while p:
            stack.append(p & 0x7F)
            p >>= 7
        out = bytearray()
        for i, v in enumerate(reversed(stack)):
            out.append(v | (0x80 if i < len(stack) - 1 else 0))
        body += bytes(out)
    return _der(0x06, body)


def _null():
    return b"\x05\x00"


def _bitstr(b):
    return _der(0x03, b"\x00" + b)


def _utf8(s):
    return _der(0x0C, s.encode())


def _utctime(s):
    return _der(0x17, s.encode())


_OID_RSA = "1.2.840.113549.1.1.1"
_OID_SHA256RSA = "1.2.840.113549.1.1.11"
_OID_SHA256 = "2.16.840.1.101.3.4.2.1"
_OID_CN = "2.5.4.3"
_OID_O = "2.5.4.10"
_OID_SAN = "2.5.29.17"


def _spki(k):
    """SubjectPublicKeyInfo for the RSA key."""
    pubkey = _seq(_der_int(k["n"]), _der_int(k["e"]))
    return _seq(_seq(_oid(_OID_RSA), _null()), _bitstr(pubkey))


def _name(cn, o):
    return _seq(_set(_seq(_oid(_OID_CN), _utf8(cn))),
               _set(_seq(_oid(_OID_O), _utf8(o))))


def pkcs8_private_key(k):
    """Encode the RSA key as an unencrypted PKCS#8 PrivateKeyInfo (DER)."""
    rsa_priv = _seq(_der_int(0), _der_int(k["n"]), _der_int(k["e"]),
                    _der_int(k["d"]), _der_int(k["p"]), _der_int(k["q"]),
                    _der_int(k["dp"]), _der_int(k["dq"]), _der_int(k["qinv"]))
    return _seq(_der_int(0), _seq(_oid(_OID_RSA), _null()),
                _der(0x04, rsa_priv))


def _rsa_sign_sha256(k, message):
    """PKCS#1 v1.5 signature over SHA-256(message)."""
    h = hashlib.sha256(message).digest()
    emlen = (k["n"].bit_length() + 7) // 8
    digest_info = _seq(_seq(_oid(_OID_SHA256), _null()), _der(0x04, h))
    ps = b"\xff" * (emlen - len(digest_info) - 3)
    em = b"\x00\x01" + ps + b"\x00" + digest_info
    sig = pow(int.from_bytes(em, "big"), k["d"], k["n"])
    return sig.to_bytes(emlen, "big")


def make_self_signed_cert(k, cn="localhost", org="Example Enterprise",
                          days=3650):
    """Build and sign a v3 X.509 certificate (DER) with SAN localhost/127.0.0.1."""
    serial = secrets.randbits(64) | 1
    not_before = time.strftime("%y%m%d%H%M%SZ", time.gmtime(time.time() - 60))
    not_after = time.strftime("%y%m%d%H%M%SZ",
                              time.gmtime(time.time() + days * 86400))
    sig_alg = _seq(_oid(_OID_SHA256RSA), _null())
    # subjectAltName GeneralNames: dNSName [2], iPAddress [7]
    san = _der(0x30, _der(0x82, b"localhost") + _der(0x87, bytes([127, 0, 0, 1])))
    extensions = _der(0xA3, _seq(_seq(_oid(_OID_SAN), _der(0x04, san))))
    tbs = _seq(
        _der(0xA0, _der_int(2)),               # version: v3
        _der_int(serial),
        sig_alg,
        _name(cn, org),                        # issuer (== subject: self-signed)
        _seq(_utctime(not_before), _utctime(not_after)),
        _name(cn, org),                        # subject
        _spki(k),
        extensions,
    )
    signature = _rsa_sign_sha256(k, tbs)
    return _seq(tbs, sig_alg, _bitstr(signature))


def _pem(der_bytes, label):
    b64 = base64.encodebytes(der_bytes).decode().strip()
    return f"-----BEGIN {label}-----\n{b64}\n-----END {label}-----\n"


def write_temp_credentials():
    """Generate key+cert and write PEM files; return (cert_path, key_path)."""
    key = generate_rsa(2048)
    cert_der = make_self_signed_cert(key)
    tmp = tempfile.mkdtemp(prefix="tls_inspect_")
    cert_path = os.path.join(tmp, "cert.pem")
    key_path = os.path.join(tmp, "key.pem")
    with open(cert_path, "w") as f:
        f.write(_pem(cert_der, "CERTIFICATE"))
    with open(key_path, "w") as f:
        f.write(_pem(pkcs8_private_key(key), "PRIVATE KEY"))
    return cert_path, key_path


# ==========================================================================
# The TLS demo + inspection.
# ==========================================================================
def run_local_tls_demo(cert_path, key_path):
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def server_loop():
        raw, _ = listener.accept()
        with server_ctx.wrap_socket(raw, server_side=True) as tls:
            tls.recv(1024)
            tls.sendall(b"secure-pong")

    threading.Thread(target=server_loop, daemon=True).start()

    # Client trusts OUR self-signed cert specifically (certificate pinning).
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.load_verify_locations(cafile=cert_path)
    client_ctx.check_hostname = True

    with socket.create_connection((HOST, port), timeout=5) as raw:
        with client_ctx.wrap_socket(raw, server_hostname="localhost") as tls:
            tls.sendall(b"secure-ping")
            reply = tls.recv(1024)
            print(f"    application reply : {reply!r}")
            assert reply == b"secure-pong"
            _print_tls_details(tls, local=True)
    listener.close()


def run_network_tls_inspect(host="cloudflare.com", port=443):
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            _print_tls_details(tls, local=False)


def _print_tls_details(tls, local):
    version = tls.version()                 # e.g. 'TLSv1.3'
    cipher = tls.cipher()                    # (name, protocol, secret_bits)
    print(f"    TLS version       : {version}")
    print(f"    cipher suite      : {cipher[0]}")
    print(f"    cipher protocol   : {cipher[1]}")
    print(f"    symmetric bits    : {cipher[2]}")
    assert version and version.startswith("TLSv"), "no TLS version negotiated"

    cert = tls.getpeercert()
    if cert:
        subject = dict(x[0] for x in cert.get("subject", []))
        issuer = dict(x[0] for x in cert.get("issuer", []))
        print(f"    cert subject CN   : {subject.get('commonName')}")
        print(f"    cert issuer  CN   : {issuer.get('commonName')}")
        print(f"    cert valid from   : {cert.get('notBefore')}")
        print(f"    cert valid until  : {cert.get('notAfter')}")
        sans = [v for (t, v) in cert.get("subjectAltName", [])]
        if sans:
            print(f"    cert SAN          : {sans}")
        assert subject.get("commonName") == "localhost" or not local


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("TLS handshake inspection: version / cipher / certificate")
    print("=" * 70)

    try:
        print("\n[local] generating a self-signed cert (pure stdlib) ...")
        t0 = time.time()
        cert_path, key_path = write_temp_credentials()
        print(f"    RSA-2048 keypair + X.509 cert built in {time.time()-t0:.2f}s")
        print("\n[local] in-process TLS server, client pins the cert:")
        run_local_tls_demo(cert_path, key_path)
        print("    local TLS handshake + cert inspection verified ✓")
    except (ssl.SSLError, OSError, AssertionError) as e:
        print(f"    local TLS path failed ({e!r}); trying public endpoint ...")
        try:
            run_network_tls_inspect()
            print("    public TLS endpoint inspected ✓")
        except (socket.timeout, OSError, ssl.SSLError) as ne:
            print(f"    NOTE: no network / TLS unavailable ({ne!r}).")
            print("    Skipping gracefully.")

    print("\nDone. ✓")
