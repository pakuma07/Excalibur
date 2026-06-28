"""
dns_query.py — Build and parse a raw DNS query packet by hand (stdlib only)

ENTERPRISE PROBLEM
------------------
DNS is the Internet's control plane: it's how clients find your services, how
load is steered between regions (GSLB), and — when it breaks — how an entire
company goes dark (see the Dyn 2016 and Facebook 2021 outages). Understanding the
*on-the-wire* message format (RFC 1035) is what lets you debug resolution
failures, read a packet capture, reason about EDNS0 buffer sizes and truncation,
and understand why DNS amplification attacks work.

This script does NOT use any DNS library. It constructs a DNS query packet
field-by-field with `struct`, sends it over UDP to a public resolver (Cloudflare
1.1.1.1), and parses the response — including the tricky part: NAME COMPRESSION.
DNS saves bytes by letting a name end in a 2-byte pointer (high bits 0b11) to an
earlier offset in the packet, so a correct parser must follow those pointers.

THE PACKET FORMAT (RFC 1035 §4)
    Header (12 bytes): ID, flags, QDCOUNT, ANCOUNT, NSCOUNT, ARCOUNT
    Question:          QNAME (length-prefixed labels), QTYPE, QCLASS
    Answer(s):         NAME, TYPE, CLASS, TTL, RDLENGTH, RDATA

HOW TO RUN
----------
    py dns_query.py

Cross-platform (UDP sockets are identical everywhere). If there is no network
(common in CI / locked-down enterprise boxes), the script catches the timeout,
prints a clear note, and STILL self-verifies the encode/decode logic against a
hand-built packet so the test never hard-fails.
"""

import socket
import struct
import random

RESOLVER = "1.1.1.1"
DNS_PORT = 53
TYPE_A = 1
CLASS_IN = 1


def encode_qname(name):
    """Encode 'www.example.com' as length-prefixed labels + a zero terminator.

    'www.example.com' -> b'\\x03www\\x07example\\x03com\\x00'
    """
    out = bytearray()
    for label in name.split("."):
        if label:                       # skip empty (e.g. trailing dot)
            encoded = label.encode("ascii")
            out.append(len(encoded))    # each label is prefixed by its length
            out.extend(encoded)
    out.append(0)                       # root label = zero-length terminator
    return bytes(out)


def build_query(name, qtype=TYPE_A, txn_id=None):
    """Build a complete DNS query packet for `name`."""
    if txn_id is None:
        txn_id = random.randint(0, 0xFFFF)
    # Flags: 0x0100 sets RD (Recursion Desired) — ask the resolver to do the work.
    flags = 0x0100
    qdcount, ancount, nscount, arcount = 1, 0, 0, 0
    # '!' = network byte order (big-endian); 'H' = unsigned 16-bit.
    header = struct.pack("!HHHHHH", txn_id, flags,
                         qdcount, ancount, nscount, arcount)
    question = encode_qname(name) + struct.pack("!HH", qtype, CLASS_IN)
    return txn_id, header + question


def read_name(message, offset):
    """Read a (possibly compressed) DNS name starting at `offset`.

    Returns (name_string, offset_after_name). Compression pointers (top two bits
    of a length byte set: 0b11xxxxxx) redirect to an earlier offset; we follow
    them but the "offset after" we return is the position right after the FIRST
    pointer we encountered (per RFC 1035 §4.1.4).
    """
    labels = []
    jumped = False
    after_pointer = offset
    guard = 0
    while True:
        guard += 1
        if guard > 128:                 # paranoia: stop pathological loops
            raise ValueError("name parse exceeded label limit (loop?)")
        length = message[offset]
        if length & 0xC0 == 0xC0:
            # Compression pointer: 14-bit offset spread over these two bytes.
            pointer = ((length & 0x3F) << 8) | message[offset + 1]
            if not jumped:
                after_pointer = offset + 2   # consumed 2 bytes in the stream
            offset = pointer                  # jump to the referenced name
            jumped = True
            continue
        if length == 0:                 # zero length = end of name
            offset += 1
            if not jumped:
                after_pointer = offset
            break
        offset += 1
        labels.append(message[offset:offset + length].decode("ascii", "replace"))
        offset += length
    return ".".join(labels), after_pointer


def parse_response(message):
    """Parse a DNS response message into a dict of header + answer records."""
    (txn_id, flags, qd, an, ns, ar) = struct.unpack("!HHHHHH", message[:12])
    rcode = flags & 0x000F             # response code (0 = no error)
    offset = 12

    # Skip the question section (echoed back by the server).
    for _ in range(qd):
        _qname, offset = read_name(message, offset)
        offset += 4                    # QTYPE (2) + QCLASS (2)

    answers = []
    for _ in range(an):
        name, offset = read_name(message, offset)
        rtype, rclass, ttl, rdlength = struct.unpack(
            "!HHIH", message[offset:offset + 10])
        offset += 10
        rdata = message[offset:offset + rdlength]
        offset += rdlength
        value = None
        if rtype == TYPE_A and rdlength == 4:
            value = ".".join(str(b) for b in rdata)   # dotted-quad IPv4
        answers.append({
            "name": name, "type": rtype, "class": rclass,
            "ttl": ttl, "value": value,
        })
    return {"id": txn_id, "rcode": rcode, "qdcount": qd, "ancount": an,
            "answers": answers}


def query(name, server=RESOLVER, timeout=3.0):
    """Send a query over UDP and parse the reply."""
    txn_id, packet = build_query(name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, DNS_PORT))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    result = parse_response(data)
    assert result["id"] == txn_id, "transaction ID mismatch (spoofed reply?)"
    return result


def _self_test_offline():
    """Verify encode + name-compression decode WITHOUT any network."""
    # 1) QNAME round-trips correctly.
    assert encode_qname("www.example.com") == \
        b"\x03www\x07example\x03com\x00"
    assert encode_qname("a.bc") == b"\x01a\x02bc\x00"

    # 2) Hand-build a message that uses a compression POINTER and parse it.
    #    Bytes 12.. contain "example.com\0", then a name at the end that is
    #    "www" + pointer-to-offset-12.
    header = struct.pack("!HHHHHH", 0x1234, 0x8180, 0, 0, 0, 0)
    base = header + b"\x07example\x03com\x00"   # 'example.com' starts at offset 12
    ptr_name = b"\x03www" + struct.pack("!H", 0xC000 | 12)  # 'www' + ->offset 12
    message = base + ptr_name
    name, _ = read_name(message, len(base))
    assert name == "www.example.com", name
    return True


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("Raw DNS query/parse (stdlib struct only) with name-compression")
    print("=" * 70)

    # Always run the offline self-test first so the script self-verifies even
    # with no network access.
    assert _self_test_offline()
    print("[offline] QNAME encode + compression-pointer decode  ✓")

    # Now attempt a real query against Cloudflare's public resolver.
    target = "example.com"
    print(f"\n[network] querying {RESOLVER} for A {target} ...")
    try:
        result = query(target)
        print(f"    txn id   : 0x{result['id']:04x}")
        print(f"    rcode    : {result['rcode']} (0 = NOERROR)")
        print(f"    answers  : {result['ancount']}")
        for a in result["answers"]:
            tname = {1: "A", 5: "CNAME", 28: "AAAA"}.get(a["type"], a["type"])
            print(f"      {a['name']}  type={tname}  ttl={a['ttl']}  "
                  f"value={a['value']}")
        a_records = [a for a in result["answers"] if a["type"] == TYPE_A]
        assert a_records, "expected at least one A record"
        # Sanity check: A record value parses as a dotted-quad IPv4.
        octets = a_records[0]["value"].split(".")
        assert len(octets) == 4 and all(0 <= int(o) <= 255 for o in octets)
        print("    live A record validated ✓")
    except (socket.timeout, OSError) as e:
        print(f"    NOTE: no network / resolver unreachable ({e!r}).")
        print("    Skipping live query — offline self-test already passed.")

    print("\nDone. Encode/decode logic verified. ✓")
