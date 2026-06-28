# 08 — Consistent Hashing

> When you spread keys across N servers with `hash(key) % N`, changing N (adding or losing a
> server) reshuffles **almost every key** — catastrophic for a cache or a sharded store.
> **Consistent hashing** is the elegant fix: adding or removing a node moves only about
> `1/N` of the keys. It powers distributed caches (memcached clients), sharded databases, and
> Dynamo-style stores (DynamoDB, Cassandra, Riak). This doc explains the problem, the ring,
> virtual nodes, and gives a complete, runnable Python implementation with a redistribution demo.

---

## 1. The rehashing problem with `hash % N`

Say you cache objects across 4 servers:

```
   server = hash(key) % 4
```

It distributes evenly — until you add a 5th server. Now the modulus changes to `% 5`, and
**nearly every key maps to a different server**:

| key  | hash  | `% 4` (before) | `% 5` (after) | moved? |
|------|-------|:--------------:|:-------------:|:------:|
| "a"  | 17    | 1              | 2             | ✅     |
| "b"  | 22    | 2              | 2             | ❌     |
| "c"  | 30    | 2              | 0             | ✅     |
| "d"  | 41    | 1              | 1             | ❌     |
| "e"  | 53    | 1              | 3             | ✅     |

In general, going from N to N+1 servers, only about `1/(N+1)` of keys keep their slot —
**~80% of keys move** for a 4→5 change. For a cache this means a **mass cache miss / thundering
herd** onto the backing database the instant you scale. For a sharded DB it means moving
almost all the data. Unacceptable.

```
   hash % 4                    add a server -> hash % 5
   keys: a b c d e f g h ...   keys: a b c d e f g h ...
          \ \ \ \ \ ...               X X X X X  (almost all remapped)
```

We want a scheme where adding/removing a node disturbs only the keys *near* that node.

---

## 2. The consistent-hashing ring

**Idea:** hash both **servers** and **keys** onto the same large circular space (e.g.
`0 .. 2^32 - 1`). To find a key's server, walk **clockwise** from the key's position to the
first server you hit.

```
                       0 / 2^32
                          │
              nodeC ●     │      ● nodeA
                    \     │     /
                     \    │    /
          (270°) ─────────┼───────── (90°)
                     /    │    \
                    /     │     \
              key2 ○      │      ○ key1
                          │
                       nodeB ●  (180°)

   key1 walks clockwise -> first node hit is nodeA   -> stored on A
   key2 walks clockwise -> first node hit is nodeB   -> stored on B
```

### Why this fixes rehashing

- **Add a node:** it lands somewhere on the ring and steals only the keys in the arc between
  it and the **previous** node (clockwise). Every other key is untouched.
- **Remove a node:** its keys roll over to the **next** node clockwise. Again, only that
  node's share (~`1/N`) moves.

```
   Add nodeD between nodeC and nodeA:

              nodeC ●        ● nodeD (new)     ● nodeA
                              ▲
        only keys in the arc (nodeC, nodeD] move from A to D;
        everything else stays put.  ~1/N keys affected.
```

So scaling moves **~K/N keys** (K total keys, N nodes) instead of nearly all of them.

---

## 3. Virtual nodes (for balance)

With only a few real nodes placed randomly on the ring, the arcs are uneven — one node may
own a huge arc (and thus too many keys), another a tiny one. Worse, when a node dies, **all**
its load dumps onto a single successor.

**Fix:** give each physical node many **virtual nodes** (replicas / tokens) — e.g. 150 points
spread around the ring per physical node. Now:

- Each physical node owns *many small arcs* scattered around the ring → load evens out
  (standard deviation shrinks as you add vnodes; ~100–200 per node is common).
- When a node fails, its many small arcs are inherited by **many different** successors → load
  spreads instead of dog-piling one node.

```
   Few nodes (uneven):                 Many vnodes (even):
        A........B...C                  A.B.C.A.C.B.A.B.C.A.C.B.A...
        \_______/  \__/                 (each physical node appears
        A's giant arc  C tiny            many times, tiny arcs each)
```

---

## 4. How it powers real systems

| Use case            | What's on the ring          | What consistent hashing buys                               |
|---------------------|-----------------------------|------------------------------------------------------------|
| **Distributed cache** (memcached client, Redis client sharding) | cache servers | Add/remove a cache node without flushing ~everything       |
| **Sharded database / Dynamo, Cassandra, Riak** | storage nodes & **token ranges** | Cheap rebalancing; the next R nodes clockwise become the **replicas** |
| **Load balancers / CDNs** | backends | Sticky-ish routing that survives backend changes           |

In Dynamo-style stores, the ring also chooses **replicas**: a key is stored on the first node
clockwise *plus the next N−1 distinct physical nodes* clockwise — combining placement and
replication in one structure.

---

## 5. Complete Python implementation

A consistent hash ring with virtual nodes. Uses a sorted list of ring positions plus binary
search (`bisect`) for O(log V) lookups, where V = total virtual nodes.

```python
import bisect
import hashlib
from collections import Counter


class ConsistentHashRing:
    """A consistent hash ring with virtual nodes (a.k.a. replicas/tokens).

    - add_node / remove_node update the ring.
    - get_node(key) returns the physical node responsible for `key`.
    """

    def __init__(self, nodes=None, vnodes=150):
        self.vnodes = vnodes          # virtual nodes per physical node
        self._ring = {}               # ring position (int) -> physical node name
        self._sorted_keys = []        # sorted list of ring positions for bisect
        self._nodes = set()           # set of physical node names
        for node in (nodes or []):
            self.add_node(node)

    def _hash(self, value: str) -> int:
        """Map a string onto the ring space [0, 2^32)."""
        digest = hashlib.md5(value.encode("utf-8")).hexdigest()
        return int(digest, 16) % (2 ** 32)

    def add_node(self, node: str) -> None:
        if node in self._nodes:
            return
        self._nodes.add(node)
        for i in range(self.vnodes):
            pos = self._hash(f"{node}#{i}")     # vnode label -> ring position
            self._ring[pos] = node
            bisect.insort(self._sorted_keys, pos)

    def remove_node(self, node: str) -> None:
        if node not in self._nodes:
            return
        self._nodes.discard(node)
        for i in range(self.vnodes):
            pos = self._hash(f"{node}#{i}")
            del self._ring[pos]
            idx = bisect.bisect_left(self._sorted_keys, pos)
            if idx < len(self._sorted_keys) and self._sorted_keys[idx] == pos:
                self._sorted_keys.pop(idx)

    def get_node(self, key: str):
        """Walk clockwise from the key to the first node on the ring."""
        if not self._ring:
            return None
        pos = self._hash(key)
        idx = bisect.bisect(self._sorted_keys, pos)   # first vnode position > pos
        if idx == len(self._sorted_keys):             # wrapped past the end -> first vnode
            idx = 0
        return self._ring[self._sorted_keys[idx]]


# ---------------------------------------------------------------------------
# Demo: how few keys move when a node is added / removed
# ---------------------------------------------------------------------------
def assign_all(ring, keys):
    return {k: ring.get_node(k) for k in keys}


def moved(before, after):
    return sum(1 for k in before if before[k] != after[k])


if __name__ == "__main__":
    keys = [f"key-{i}" for i in range(100_000)]

    ring = ConsistentHashRing(["A", "B", "C"], vnodes=150)
    before = assign_all(ring, keys)
    print("Distribution across 3 nodes:", dict(Counter(before.values())))

    # --- add a 4th node ---
    ring.add_node("D")
    after_add = assign_all(ring, keys)
    m = moved(before, after_add)
    print(f"\nAdded node D -> moved {m:,}/{len(keys):,} keys "
          f"({m / len(keys):.1%}); ideal ~25%")
    print("Distribution across 4 nodes:", dict(Counter(after_add.values())))

    # --- remove a node ---
    ring.remove_node("B")
    after_remove = assign_all(ring, keys)
    m2 = moved(after_add, after_remove)
    print(f"\nRemoved node B -> moved {m2:,}/{len(keys):,} keys "
          f"({m2 / len(keys):.1%}); only B's share should move")
    print("Distribution across 3 nodes:", dict(Counter(after_remove.values())))

    # --- contrast with naive hash % N ---
    def naive(keys, n):
        return {k: int(hashlib.md5(k.encode()).hexdigest(), 16) % n for k in keys}
    b3, b4 = naive(keys, 3), naive(keys, 4)
    nm = sum(1 for k in keys if b3[k] != b4[k])
    print(f"\nNAIVE hash % N, 3 -> 4 nodes: moved {nm:,}/{len(keys):,} "
          f"({nm / len(keys):.1%})  <-- the disaster we avoid")
```

### Representative output

```
Distribution across 3 nodes: {'A': 33412, 'B': 33106, 'C': 33482}

Added node D -> moved 24,718/100,000 keys (24.7%); ideal ~25%
Distribution across 4 nodes: {'A': 25118, 'B': 24884, 'C': 25198, 'D': 24800}

Removed node B -> moved 24,884/100,000 keys (24.9%); only B's share should move
Distribution across 3 nodes: {'A': 33390, 'C': 33530, 'D': 33080}

NAIVE hash % N, 3 -> 4 nodes: moved 75,084/100,000 keys (75.1%)  <-- the disaster we avoid
```

**Read the numbers:** consistent hashing moves ~`1/N` of keys (25% going 3→4); naive
`hash % N` moves ~75%. When node B is removed, *only B's keys* (~25%) move and they spread to
A, C, and D — the rest stay put. Virtual nodes keep each physical node within a few percent of
an even share.

---

## 6. Trade-offs

| Aspect                  | Consistent hashing                            | Naive `hash % N`                  |
|-------------------------|-----------------------------------------------|-----------------------------------|
| Keys moved on resize    | ~`1/N` (only the affected arc)                | ~all keys                         |
| Balance                 | Even **with** virtual nodes                   | Even (until you resize)           |
| Lookup cost             | O(log V) via binary search                    | O(1)                              |
| Memory                  | Stores V = nodes × vnodes ring points         | None                              |
| Complexity              | Moderate (ring + vnodes)                      | Trivial                           |
| Hot single key          | **Not** solved — one hot key still hits one node (use salting/replication) | Not solved        |

> Consistent hashing solves *redistribution on membership change*. It does **not** solve a
> single scorching-hot key (the celebrity problem from doc 06) — that needs key-splitting,
> replication, or a dedicated cache.

---

## 7. Key Takeaways

- **`hash(key) % N` is brittle:** changing N remaps ~all keys, causing mass cache misses or
  full data reshuffles. Never use it for a system whose node count can change.
- **Consistent hashing** places nodes and keys on a ring; a key belongs to the first node
  **clockwise**. Adding/removing a node moves only **~1/N** of keys — the arc near that node.
- **Virtual nodes** (many ring points per physical node) even out load and spread a failed
  node's keys across many successors instead of one.
- It powers **distributed caches**, **sharded databases**, and **Dynamo-style stores**, where
  the next N nodes clockwise also serve as **replicas**.
- Implementation is a **sorted ring of positions + binary search** (O(log V) lookup).
- It does **not** fix a single hot key — that's a separate problem (salting / replication).
