"""
lru_cache.py — An O(1) thread-safe LRU cache (hashmap + doubly linked list).

ENTERPRISE PROBLEM
------------------
Every service has an in-process cache: rendered templates, parsed configs,
authz decisions, hot DB rows. Memory is finite, so the cache needs an eviction
policy. LRU (Least Recently Used) is the workhorse default: when full, evict
the item that hasn't been touched for the longest time, on the bet that recent
accesses predict future ones. It is what backs:
  * functools.lru_cache in Python's stdlib.
  * Guava CacheBuilder / Caffeine in Java.
  * The page cache's approximation of LRU in the OS itself (clock algorithm).

The interesting engineering is doing get/put in O(1):
  * A HASHMAP gives O(1) key -> node lookup.
  * A DOUBLY LINKED LIST keeps items in recency order; moving a node to the
    "most recently used" end and evicting from the "least" end are O(1) pointer
    splices (no array shifting, no scan).

It must be THREAD-SAFE: services call it from many request threads. Every public
operation mutates the linked list, so all of it runs under one lock. (We also
track hit/miss stats, the single most useful cache metric in production —
a low hit ratio means the cache is too small or the keys are too sparse.)

RELATED OS CONCEPT DOC: ../03_memory_management.md (page replacement / LRU/clock),
                        ../04_concurrency_synchronization.md (lock discipline).

HOW TO RUN
----------
    py lru_cache.py

Cross-platform. Self-verifies with asserts.
"""

import threading


class _Node:
    """One entry in the doubly linked list."""
    __slots__ = ("key", "value", "prev", "next")

    def __init__(self, key=None, value=None):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class LRUCache:
    """O(1) get/put LRU cache. Thread-safe. Tracks hit/miss/eviction counts.

    The linked list uses two SENTINEL nodes (head and tail) so we never have to
    special-case inserting/removing at the ends — there is always a node on
    each side. Layout:  head <-> [MRU] <-> ... <-> [LRU] <-> tail
    """

    def __init__(self, capacity: int):
        assert capacity > 0, "capacity must be positive"
        self.capacity = capacity
        self._map: dict = {}             # key -> _Node
        self._head = _Node()             # sentinel: most-recently-used side
        self._tail = _Node()             # sentinel: least-recently-used side
        self._head.next = self._tail
        self._tail.prev = self._head
        self._lock = threading.Lock()
        # Observability counters.
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    # --- Internal O(1) linked-list splices (caller holds the lock) -----------
    def _remove(self, node: _Node) -> None:
        node.prev.next = node.next
        node.next.prev = node.prev

    def _push_front(self, node: _Node) -> None:
        """Insert node right after head => mark it most-recently-used."""
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node
        self._head.next = node

    # --- Public API ----------------------------------------------------------
    def get(self, key, default=None):
        with self._lock:
            node = self._map.get(key)
            if node is None:
                self.misses += 1
                return default
            self.hits += 1
            # Touch: move to the MRU side.
            self._remove(node)
            self._push_front(node)
            return node.value

    def put(self, key, value) -> None:
        with self._lock:
            node = self._map.get(key)
            if node is not None:
                # Update existing key and mark it most-recently-used.
                node.value = value
                self._remove(node)
                self._push_front(node)
                return
            # Insert new key.
            node = _Node(key, value)
            self._map[key] = node
            self._push_front(node)
            if len(self._map) > self.capacity:
                # Evict the LRU node (the one just before the tail sentinel).
                lru = self._tail.prev
                self._remove(lru)
                del self._map[lru.key]
                self.evictions += 1

    def __len__(self) -> int:
        with self._lock:
            return len(self._map)

    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


def demo_basic() -> None:
    print("--- Basic LRU semantics (capacity=2) ---")
    c = LRUCache(capacity=2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1            # touch 'a' => 'b' is now LRU
    c.put("c", 3)                     # capacity exceeded => evict 'b'
    assert c.get("b") is None, "b should have been evicted"
    assert c.get("a") == 1
    assert c.get("c") == 3
    assert c.evictions == 1
    assert len(c) == 2
    print(f"  Evicted least-recently-used key correctly. "
          f"hits={c.hits} misses={c.misses} evictions={c.evictions}")


def demo_stats() -> None:
    print("--- Hit/miss stats over a workload ---")
    c = LRUCache(capacity=100)
    # Warm the cache, then read keys with locality (low keys hit, high miss).
    for i in range(100):
        c.put(i, i * 10)
    for i in range(200):
        c.get(i % 150)  # keys 0..99 hit, 100..149 miss/evict churn
    print(f"  hits={c.hits} misses={c.misses} hit_ratio={c.hit_ratio():.2%} "
          f"evictions={c.evictions} size={len(c)}")
    assert c.hits + c.misses == 200
    assert len(c) == 100  # never exceeds capacity
    print("  Stats consistent; size never exceeded capacity.")


def demo_thread_safety() -> None:
    print("--- Thread-safety: 16 threads pounding one cache ---")
    c = LRUCache(capacity=50)

    def worker(base: int):
        for i in range(2000):
            k = (base + i) % 200
            if c.get(k) is None:
                c.put(k, k)

    threads = [threading.Thread(target=worker, args=(b * 13,)) for b in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The key invariant: under concurrency the map and list never corrupt, so
    # size stays within capacity and the list length matches the map.
    assert len(c) <= c.capacity, f"size {len(c)} exceeded capacity {c.capacity}"
    # Walk the linked list and confirm it has exactly len(map) real nodes — proof
    # the doubly linked list stayed consistent (no lost/dangling pointers).
    count = 0
    n = c._head.next
    while n is not c._tail:
        count += 1
        n = n.next
    assert count == len(c), f"linked list ({count}) and map ({len(c)}) out of sync"
    print(f"  Final size={len(c)} (<= {c.capacity}); list and map consistent. OK")


def main() -> None:
    demo_basic()
    demo_stats()
    demo_thread_safety()
    print("All LRU-cache assertions passed.")


if __name__ == "__main__":
    main()
