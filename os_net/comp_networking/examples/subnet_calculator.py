"""
subnet_calculator.py — CIDR/subnet math, VLSM splitting, and longest-prefix-match routing

ENTERPRISE PROBLEM
------------------
IP address planning is foundational network engineering: carving a corporate
address block into subnets for sites/VPCs/VLANs without overlap or waste, knowing
exactly which addresses are usable, and understanding how routers actually pick a
route. Two operations show up constantly:

  1. SUBNETTING / VLSM. Given a CIDR block (e.g. 10.0.0.0/16), compute the network
     address, broadcast address, usable host range, and host count — and split it
     into smaller subnets (e.g. into /24s, or via VLSM into right-sized blocks per
     site). Get the masks wrong and you either overlap (routing breaks) or waste
     thousands of addresses.

  2. LONGEST-PREFIX MATCH (LPM). When a packet's destination matches MULTIPLE
     routes in the routing table, the router uses the MOST SPECIFIC one — the
     longest prefix. A default route 0.0.0.0/0 matches everything; 10.0.0.0/8
     beats it for 10.x; 10.1.2.0/24 beats both for 10.1.2.x. This single rule is
     how the entire Internet's routing works (it's what FIB lookups implement in
     hardware).

This script implements CIDR math, subnet splitting, and an LPM routing-table
lookup from first principles (integer/bitmask arithmetic), with `ipaddress` from
the stdlib used to cross-check the hand-rolled results.

HOW TO RUN
----------
    py subnet_calculator.py

Cross-platform: pure stdlib (custom bit math + `ipaddress` for verification).
"""

import ipaddress


# --------------------------------------------------------------------------
# Hand-rolled IPv4 <-> integer helpers (so the math is visible, not magic).
# --------------------------------------------------------------------------
def ip_to_int(ip):
    a, b, c, d = (int(x) for x in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def int_to_ip(n):
    return f"{(n >> 24) & 0xFF}.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"


def mask_from_prefix(prefix):
    """Prefix length (0-32) -> 32-bit netmask integer."""
    if prefix == 0:
        return 0
    return (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF


class Subnet:
    """A computed view of a CIDR block: network, broadcast, host range, count."""

    def __init__(self, cidr):
        net_str, prefix_str = cidr.split("/")
        self.prefix = int(prefix_str)
        self.mask = mask_from_prefix(self.prefix)
        ip_int = ip_to_int(net_str)
        # Network address = IP AND mask. Broadcast = network OR inverse-mask.
        self.network_int = ip_int & self.mask
        self.broadcast_int = self.network_int | (~self.mask & 0xFFFFFFFF)

    @property
    def network(self):
        return int_to_ip(self.network_int)

    @property
    def broadcast(self):
        return int_to_ip(self.broadcast_int)

    @property
    def total_addresses(self):
        return self.broadcast_int - self.network_int + 1

    @property
    def usable_hosts(self):
        # /31 (point-to-point, RFC 3021) and /32 (host route) are special:
        # no network/broadcast reservation in the usual sense.
        if self.prefix >= 31:
            return self.total_addresses
        return self.total_addresses - 2   # minus network + broadcast

    @property
    def first_host(self):
        if self.prefix >= 31:
            return self.network
        return int_to_ip(self.network_int + 1)

    @property
    def last_host(self):
        if self.prefix >= 31:
            return self.broadcast
        return int_to_ip(self.broadcast_int - 1)

    def split(self, new_prefix):
        """Split this block into equal subnets of `new_prefix` length."""
        assert new_prefix > self.prefix, "new prefix must be longer (smaller block)"
        step = 1 << (32 - new_prefix)        # addresses per child subnet
        children = []
        addr = self.network_int
        while addr <= self.broadcast_int:
            children.append(Subnet(f"{int_to_ip(addr)}/{new_prefix}"))
            addr += step
        return children

    def __repr__(self):
        return f"<Subnet {self.network}/{self.prefix}>"


def vlsm_allocate(block, host_requirements):
    """Variable-Length Subnet Masking: allocate right-sized subnets.

    Given a parent block and a list of (name, hosts_needed), allocate subnets
    largest-first so the block is used efficiently. Returns a list of
    (name, Subnet).
    """
    parent = Subnet(block)
    # Allocate the biggest requirements first to avoid fragmentation.
    reqs = sorted(host_requirements, key=lambda x: x[1], reverse=True)
    cursor = parent.network_int
    out = []
    for name, hosts in reqs:
        # Smallest prefix whose usable host count covers `hosts`.
        prefix = 32
        while prefix > 0:
            test = Subnet(f"{int_to_ip(cursor)}/{prefix}")
            if test.usable_hosts >= hosts:
                break
            prefix -= 1
        sub = Subnet(f"{int_to_ip(cursor)}/{prefix}")
        assert sub.broadcast_int <= parent.broadcast_int, \
            f"ran out of space allocating {name}"
        out.append((name, sub))
        cursor = sub.broadcast_int + 1       # next free address
    return out


# --------------------------------------------------------------------------
# Longest-prefix-match routing table.
# --------------------------------------------------------------------------
class RoutingTable:
    def __init__(self):
        self.routes = []     # list of (network_int, mask, prefix, next_hop)

    def add(self, cidr, next_hop):
        s = Subnet(cidr)
        self.routes.append((s.network_int, s.mask, s.prefix, next_hop))

    def lookup(self, ip):
        """Return the next hop for `ip` via longest-prefix match."""
        addr = ip_to_int(ip)
        best = None
        best_prefix = -1
        for net, mask, prefix, hop in self.routes:
            if (addr & mask) == net and prefix > best_prefix:
                best = hop
                best_prefix = prefix
        return best


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("CIDR / subnet calculator + VLSM + longest-prefix-match routing")
    print("=" * 70)

    # ---- 1) Basic CIDR math, cross-checked against the stdlib ----
    s = Subnet("192.168.1.0/24")
    print("\n[1] 192.168.1.0/24")
    print(f"    network   : {s.network}")
    print(f"    broadcast : {s.broadcast}")
    print(f"    host range: {s.first_host} - {s.last_host}")
    print(f"    usable    : {s.usable_hosts} hosts")
    ref = ipaddress.ip_network("192.168.1.0/24")
    assert s.network == str(ref.network_address)
    assert s.broadcast == str(ref.broadcast_address)
    assert s.usable_hosts == ref.num_addresses - 2
    print("    matches ipaddress stdlib ✓")

    # /31 edge case (point-to-point link).
    p2p = Subnet("10.0.0.0/31")
    assert p2p.usable_hosts == 2, p2p.usable_hosts
    print(f"    /31 link 10.0.0.0/31 usable hosts = {p2p.usable_hosts} (RFC 3021) ✓")

    # ---- 2) Equal split ----
    print("\n[2] Split 10.0.0.0/22 into /24s")
    children = Subnet("10.0.0.0/22").split(24)
    for c in children:
        print(f"    {c.network}/{c.prefix}  ({c.usable_hosts} hosts)")
    assert len(children) == 4
    assert [c.network for c in children] == \
        ["10.0.0.0", "10.0.1.0", "10.0.2.0", "10.0.3.0"]
    print("    4 contiguous /24s ✓")

    # ---- 3) VLSM right-sizing ----
    print("\n[3] VLSM allocate from 172.16.0.0/24")
    plan = vlsm_allocate("172.16.0.0/24", [
        ("sales (100 hosts)", 100),
        ("eng   (50 hosts)", 50),
        ("ops   (25 hosts)", 25),
        ("p2p   (2 hosts)", 2),
    ])
    for name, sub in plan:
        print(f"    {name:18s} -> {sub.network}/{sub.prefix} "
              f"({sub.usable_hosts} usable)")
    # No two allocations overlap.
    ranges = [(sub.network_int, sub.broadcast_int) for _, sub in plan]
    for i in range(len(ranges)):
        for j in range(i + 1, len(ranges)):
            a, b = ranges[i], ranges[j]
            assert b[0] > a[1] or a[0] > b[1], "VLSM allocations overlap!"
    print("    no overlaps; largest-first packing ✓")

    # ---- 4) Longest-prefix-match routing ----
    print("\n[4] Longest-prefix-match routing table")
    rt = RoutingTable()
    rt.add("0.0.0.0/0", "default-gw")        # default route
    rt.add("10.0.0.0/8", "core-router")      # broad
    rt.add("10.1.0.0/16", "regional-router")
    rt.add("10.1.2.0/24", "site-router")     # most specific
    cases = {
        "10.1.2.55": "site-router",      # matches all four -> picks /24
        "10.1.9.9":  "regional-router",  # matches /8 and /16 -> picks /16
        "10.9.9.9":  "core-router",      # matches /8 only (besides default)
        "8.8.8.8":   "default-gw",       # matches only the default route
    }
    for ip, expected in cases.items():
        hop = rt.lookup(ip)
        print(f"    {ip:12s} -> {hop}")
        assert hop == expected, f"{ip}: expected {expected}, got {hop}"
    print("    most-specific route always wins ✓")

    print("\nAll assertions passed. Subnet math + LPM verified. ✓")
