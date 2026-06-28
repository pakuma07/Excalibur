"""
consistent_hash.py — A consistent-hashing ring with virtual nodes

ENTERPRISE PROBLEM
------------------
You have N cache servers (memcached, Redis), shards, or load-balancer backends,
and a stream of keys to distribute across them. The obvious answer —
`server = hash(key) % N` — is a trap: change N (add or remove ONE server) and
*almost every* key remaps to a different server. For a cache, that's a mass miss
storm that stampedes your origin; for a sharded store, it's a full reshuffle of
your data. This is exactly what took down services in the early days of scaling
distributed caches.

CONSISTENT HASHING (Karger et al., 1997) fixes this. Map both servers AND keys
onto a fixed ring (a hash space, e.g. 0 .. 2^32-1). A key is owned by the first
server found walking clockwise from the key's position. Now adding/removing a
server only remaps the keys in the arc between that server and its neighbor —
O(K/N) keys move, not O(K). It's the partitioning scheme behind Dynamo,
Cassandra, memcached client libs, and many L7 load balancers (so a given client
keeps hitting the same backend for cache affinity).

VIRTUAL NODES. A single point per server gives lumpy, unfair load (some arcs are
huge). The fix is to place each physical server at MANY points on the ring
("virtual nodes" / replicas). With ~100-200 vnodes per server the load evens out,
and removing a server spreads its keys across ALL remaining servers (not just one
unlucky neighbor).

This script builds the ring, proves the load is balanced within a tolerance, and
proves that adding/removing a node remaps only a small fraction of keys.

HOW TO RUN
----------
    py consistent_hash.py

Cross-platform: pure stdlib (hashlib + bisect). No network.
"""

import bisect
import hashlib
from collections import Counter


class ConsistentHashRing:
    def __init__(self, nodes=None, vnodes=150):
        # vnodes = virtual nodes per physical node. More => smoother load,
        # more memory and slightly slower lookups. 100-200 is typical.
        self.vnodes = vnodes
        self._ring = {}          # ring position (int) -> physical node name
        self._sorted_keys = []   # sorted ring positions for binary search
        self._nodes = set()
        for n in (nodes or []):
            self.add_node(n)

    @staticmethod
    def _hash(value):
        """Map a string to a 32-bit position on the ring (MD5-derived).

        MD5 is used purely as a fast, uniform hash here — NOT for security.
        """
        digest = hashlib.md5(value.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big")   # take 32 bits

    def add_node(self, node):
        if node in self._nodes:
            return
        self._nodes.add(node)
        # Place `vnodes` virtual points for this physical node around the ring.
        for i in range(self.vnodes):
            pos = self._hash(f"{node}#{i}")
            self._ring[pos] = node
            bisect.insort(self._sorted_keys, pos)

    def remove_node(self, node):
        if node not in self._nodes:
            return
        self._nodes.discard(node)
        for i in range(self.vnodes):
            pos = self._hash(f"{node}#{i}")
            # A collision could have overwritten this slot; guard with .get.
            if self._ring.get(pos) == node:
                del self._ring[pos]
                idx = bisect.bisect_left(self._sorted_keys, pos)
                if idx < len(self._sorted_keys) and \
                        self._sorted_keys[idx] == pos:
                    self._sorted_keys.pop(idx)

    def get_node(self, key):
        """Return the physical node that owns `key` (first node clockwise)."""
        if not self._sorted_keys:
            return None
        pos = self._hash(key)
        # Binary search for the first ring position >= pos; wrap to 0.
        idx = bisect.bisect_right(self._sorted_keys, pos)
        if idx == len(self._sorted_keys):
            idx = 0
        return self._ring[self._sorted_keys[idx]]


def _distribution(ring, keys):
    c = Counter(ring.get_node(k) for k in keys)
    return c


if __name__ == "__main__":
    # Make checkmark/box-drawing output safe on legacy Windows consoles
    # (cp1252) by switching stdout to UTF-8 where supported.
    import sys as _sys
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("=" * 70)
    print("Consistent hashing with virtual nodes")
    print("=" * 70)

    nodes = ["cache-a", "cache-b", "cache-c", "cache-d"]
    ring = ConsistentHashRing(nodes, vnodes=200)
    keys = [f"user:{i}" for i in range(50_000)]

    # ---- 1) Load balance ----
    dist = _distribution(ring, keys)
    print("\n[1] Load distribution across 4 nodes (50k keys, 200 vnodes each):")
    ideal = len(keys) / len(nodes)
    for n in nodes:
        share = dist[n]
        pct = 100 * share / len(keys)
        print(f"    {n}: {share:6d} keys ({pct:5.2f}%)  ideal={ideal:.0f}")
    # Each node should be within ~15% of the ideal share with 200 vnodes.
    for n in nodes:
        assert abs(dist[n] - ideal) / ideal < 0.15, \
            f"{n} load {dist[n]} too far from ideal {ideal:.0f}"
    print("    every node within 15% of ideal share ✓")

    # ---- 2) Minimal remapping when ADDING a node ----
    before = {k: ring.get_node(k) for k in keys}
    ring.add_node("cache-e")
    after = {k: ring.get_node(k) for k in keys}
    moved = sum(1 for k in keys if before[k] != after[k])
    moved_pct = 100 * moved / len(keys)
    # With 5 nodes, the new node should ideally own ~1/5 of keys; the fraction
    # that MOVED should be near that 1/5 — far below the ~80% a modulo scheme
    # would churn.
    print(f"\n[2] Added 'cache-e': {moved} keys remapped ({moved_pct:.2f}%)")
    print(f"    (a hash%%N scheme would have moved ~{100*4/5:.0f}% of keys)")
    assert moved_pct < 30, f"too many keys moved on add: {moved_pct:.1f}%"
    # Keys that moved should have moved TO the new node (not churned elsewhere).
    moved_to_new = sum(1 for k in keys
                       if before[k] != after[k] and after[k] == "cache-e")
    assert moved_to_new == moved, "keys churned to wrong nodes on add"
    print("    all remapped keys went to the new node only ✓")

    # ---- 3) Minimal remapping when REMOVING a node ----
    before = {k: ring.get_node(k) for k in keys}
    removed_owned = sum(1 for k in keys if before[k] == "cache-c")
    ring.remove_node("cache-c")
    after = {k: ring.get_node(k) for k in keys}
    moved = sum(1 for k in keys if before[k] != after[k])
    print(f"\n[3] Removed 'cache-c': {moved} keys remapped ({100*moved/len(keys):.2f}%)")
    # Only keys that WERE on cache-c should move; everyone else stays put.
    assert moved == removed_owned, \
        "keys not previously on cache-c were disturbed by its removal"
    untouched = sum(1 for k in keys
                    if before[k] != "cache-c" and before[k] == after[k])
    assert untouched == len(keys) - removed_owned
    print("    only keys that lived on cache-c moved; the rest untouched ✓")

    print("\nAll assertions passed. Balanced load + minimal remapping. ✓")
