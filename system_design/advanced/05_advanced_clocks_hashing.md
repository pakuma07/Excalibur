# Advanced Time & Hashing

> Staff/Principal deep-dive. Two foundational primitives that quietly govern correctness and balance in distributed systems: **ordering events in time** and **mapping keys to nodes**.

---

# Part I — Clocks: Ordering Events Without a Global Now

## 1. Why It Matters

A distributed system has no shared clock and no instantaneous communication. The seemingly trivial question "did event A happen before event B?" has no objective answer across machines. Yet correctness frequently depends on it: last-writer-wins conflict resolution, causal consistency, distributed transactions, snapshot isolation, debugging "impossible" log orderings. Getting time wrong produces the worst class of bug — **silent data loss and corruption** — because there is no exception, just a stale value that wins.

> **The core insight (Lamport, 1978):** in the absence of a global clock, the only thing we can reason about is **causality** — the *happens-before* relation `→` — not wall-clock simultaneity.

`a → b` (a happened-before b) iff:
1. `a` and `b` are in the same process and `a` precedes `b`, **or**
2. `a` is a send and `b` is the matching receive, **or**
3. transitivity: `a → c` and `c → b`.

If neither `a → b` nor `b → a`, the events are **concurrent** (`a ∥ b`) — there is *genuinely* no fact about their order.

## 2. Physical vs Logical Clocks

| | Physical (wall) clock | Logical clock |
|---|---|---|
| Source | Quartz / NTP / PTP / GPS / atomic | Monotonic counters tied to events |
| Measures | Approximate real time | Causal order |
| Failure mode | **Skew** (clocks disagree) & **drift** (rate differs); NTP can step *backward* | Cannot measure real durations |
| Two flavors | *time-of-day* (`CLOCK_REALTIME`, can jump) vs *monotonic* (`CLOCK_MONOTONIC`, only forward, no absolute meaning) | — |

**Clock skew dangers (concrete):**
- **NTP step-back:** a correction can make `now()` go *backwards*. Code that assumed monotonic timestamps for ordering, lease expiry, or "newest wins" can resurrect deleted data or expire a still-valid lease.
- **Leap seconds:** historically smeared or stepped; broke major outages (the 2012 leap-second kernel hang).
- **VM pause / GC pause:** a process can be frozen for seconds; when it wakes its notion of "now" is stale. A node that *thinks* it still holds a lock may not.
- **LWW data loss:** two writes with skewed timestamps — the one with the larger (possibly wrong) timestamp wins, silently dropping the other. Cassandra LWW is the textbook example.

**Never** use wall-clock timestamps alone to order events across machines or to gate correctness.

## 3. Lamport Clocks (recap)

A single integer counter per process giving a *total order* consistent with causality.

Rules:
- On any local event: `C := C + 1`.
- On send: increment, attach `C` to the message.
- On receive of timestamp `t`: `C := max(C, t) + 1`.

Property: `a → b ⟹ C(a) < C(b)`. **The converse does not hold** — `C(a) < C(b)` does *not* imply `a → b` (they might be concurrent). To break ties into a *total* order, append the process id: order by `(C, pid)`.

**Limitation:** Lamport clocks can *order* but cannot *detect concurrency* — given two timestamps you cannot tell whether one causally precedes the other or they are concurrent. For that you need vector clocks.

## 4. Vector Clocks

Each of `N` processes keeps a vector `V[1..N]`. `V_i[i]` counts process `i`'s own events; `V_i[j]` is `i`'s knowledge of `j`'s progress.

Rules:
- Local event at `i`: `V_i[i] += 1`.
- Send from `i`: increment `V_i[i]`, attach the whole vector.
- Receive at `j` of vector `W`: `V_j[k] = max(V_j[k], W[k])` for all `k`, then `V_j[j] += 1`.

Comparison:
- `V_a ≤ V_b` iff `∀k: V_a[k] ≤ V_b[k]`.
- `a → b` iff `V_a < V_b` (≤ and not equal).
- `a ∥ b` (**concurrent**) iff neither `V_a ≤ V_b` nor `V_b ≤ V_a`.

This is what powers **conflict detection** in Dynamo-style stores (DynamoDB, Riak, Voldemort): concurrent writes are *detected* and either surfaced as siblings for application-level merge or resolved by a CRDT. Cost: O(N) per timestamp; with churn you need version vectors / dotted version vectors to bound growth.

```
P1: [1,0,0] --send--> P2 recv: max([0,1,0],[1,0,0])+self = [1,2,0]
P3 independently: [0,0,1]
   [1,2,0] vs [0,0,1] : neither dominates  ->  CONCURRENT (conflict!)
```

## 5. Hybrid Logical Clocks (HLC)

The dilemma: logical clocks give correct causality but no relation to real time (you can't query "events in the last 5 minutes"); physical clocks give real time but are unsafe for ordering. **HLC (Kulkarni, Demirbas, et al., 2014)** gives you *both* in a single 64-bit-friendly timestamp.

An HLC timestamp is a pair `(l, c)`:
- `l` ("logical") tracks the **maximum physical time seen so far** (its own or any received), so it stays close to wall-clock time.
- `c` ("counter") is a small integer that breaks ties / advances when physical time hasn't moved or has gone backward.

Guarantees:
- `e → f ⟹ HLC(e) < HLC(f)` (captures causality, like Lamport).
- `l` is always within the clock's uncertainty of physical time — so timestamps are *meaningful* and monotonic even when the underlying clock jumps backward.
- `c` is bounded (doesn't grow unboundedly under normal skew).

This is why **CockroachDB and YugabyteDB use HLC** for MVCC timestamps: you get causally-correct transaction ordering *and* timestamps you can compare to wall time, without TrueTime hardware.

### Working Python — HLC

```python
"""Hybrid Logical Clock — Kulkarni et al., 2014.
Timestamp = (l, c): l ~ physical time, c = tie-break counter.
Guarantees: monotonic, causality-respecting, and l stays near wall clock
even if the OS clock steps backward.
"""
from dataclasses import dataclass
import threading, time

@dataclass(order=True, frozen=True)
class HLCTimestamp:
    l: int   # physical component (e.g. ms since epoch)
    c: int   # logical counter
    def __str__(self) -> str:
        return f"{self.l}.{self.c}"

class HybridLogicalClock:
    def __init__(self, phys=lambda: int(time.time() * 1000)):
        self._phys = phys              # injectable physical clock (ms)
        self._l = 0
        self._c = 0
        self._lock = threading.Lock()

    def now(self) -> HLCTimestamp:
        """Call on a local or send event."""
        with self._lock:
            pt = self._phys()
            l_prev = self._l
            self._l = max(l_prev, pt)            # never let l go backward
            self._c = self._c + 1 if self._l == l_prev else 0
            return HLCTimestamp(self._l, self._c)

    def update(self, msg: HLCTimestamp) -> HLCTimestamp:
        """Call on receiving a message stamped `msg`."""
        with self._lock:
            pt = self._phys()
            l_prev, c_prev = self._l, self._c
            self._l = max(l_prev, msg.l, pt)     # advance to the max
            if self._l == l_prev == msg.l:
                self._c = max(c_prev, msg.c) + 1
            elif self._l == l_prev:
                self._c = c_prev + 1
            elif self._l == msg.l:
                self._c = msg.c + 1
            else:                                # physical time dominates
                self._c = 0
            return HLCTimestamp(self._l, self._c)


if __name__ == "__main__":
    # Simulate a clock that jumps BACKWARD between calls (NTP step / VM pause).
    seq = iter([100, 100, 100, 99, 105])   # note the backward step to 99
    clk = HybridLogicalClock(phys=lambda: next(seq))

    ts = [clk.now() for _ in range(4)]
    print("local events:", [str(t) for t in ts])
    # 100.0, 100.1, 100.2  (counter advances while phys frozen),
    # then phys=99 (BACKWARD) -> stays at l=100, c=3  (monotonic!)
    assert ts == sorted(ts), "HLC must be monotonic even under backward clock"

    # Receive a message from a node that is 'ahead'
    incoming = HLCTimestamp(110, 4)
    out = clk.update(incoming)
    print("after recv 110.4:", out)         # l jumps to 110, c=5
    assert out > incoming and out > ts[-1]
    print("OK: HLC monotonic, causal, near-physical")
```

## 6. Google TrueTime & Spanner

TrueTime (Corbett et al., OSDI 2012) takes the opposite, *physical* tack: instead of avoiding wall clocks, **make their uncertainty explicit and bounded** using GPS receivers and atomic clocks in every datacenter.

The API does not return a timestamp; it returns an **interval**:

```
TT.now() -> [earliest, latest]   such that the true absolute time t_abs
            satisfies  earliest <= t_abs <= latest,  guaranteed.
ε  = (latest - earliest) / 2     # the uncertainty; typically ~1-7 ms, sawtooth
```

**Commit-wait — how Spanner achieves external (linearizable, "strict serializable") consistency:**

To assign a commit timestamp `s` to a transaction and guarantee that any transaction that *starts after* this one commits sees a *strictly greater* timestamp:

1. Coordinator picks `s = TT.now().latest` at commit.
2. It then **waits** until `TT.now().earliest > s` — i.e., it sleeps for roughly `2ε` — before releasing locks / making the commit visible.

By waiting out the uncertainty, Spanner guarantees that real time has *definitely* passed `s` everywhere before the commit is observable. Therefore if T1 commits before T2 begins (in real time), `s1 < s2`. This gives globally-consistent reads and lock-free snapshot reads at a timestamp.

```
   t  ──────────────────────────────────────────────►
        pick s = now().latest
        │
        │◄────── commit-wait ~2ε ──────►│ release locks / visible
        │                               │
   true time guaranteed past s ─────────┘
```

**The trade-off:** correctness is bought with **latency** — every read-write transaction pays ≈ `2ε` of commit-wait. Smaller `ε` (better clocks) → lower latency. That is *why Google invested in GPS + atomic clocks*: ε directly throttles transaction throughput/latency. HLC vs TrueTime is the central design fork: HLC needs no special hardware but provides causal (not external) consistency by default; TrueTime needs the hardware but provides external consistency.

---

# Part II — Hashing: Mapping Keys to Nodes

## 7. The Problem and the Imbalance of Consistent Hashing

Naïve sharding `node = hash(key) % N` remaps almost every key when `N` changes — catastrophic for caches/storage. **Consistent hashing** (Karger et al., STOC 1997, the Akamai paper) places nodes and keys on a ring `[0, 2^m)`; a key belongs to the next node clockwise. Adding/removing a node moves only `~K/N` keys.

**But the basic ring is badly imbalanced.** With `N` nodes placed at random, the largest arc can be `~ (ln N)/N` of the ring — load variance is high. The standard fix is **virtual nodes** (each physical node gets `V` points on the ring, `V` ≈ 100–200), which reduces variance to roughly `1/√V` but costs memory (a sorted structure of `N·V` points) and complicates weighting. Two algorithms do better for common cases: **rendezvous hashing** and **jump consistent hash**.

## 8. Rendezvous (Highest-Random-Weight, HRW) Hashing

Thaler & Ravishankar (1996/1998). For a key, compute a hash of `(key, node)` for **every** node and pick the node with the **highest score**. No ring, no virtual nodes.

Properties:
- **Minimal disruption:** when a node is removed, only its keys move (to their *next-highest* node); no other key is affected. Same when adding.
- **No data structure to maintain** beyond the node set; supports arbitrary node sets and easy **weighting** (HRW with weights: scale the score by a function of weight — the standard is `-w / ln(h)` mapping the hash to a uniform(0,1)).
- **Cost:** O(N) per lookup (must score all nodes). Fine for small N (replica selection, small cache fleets). For large N use a *skeleton/hierarchical* HRW to get O(log N).
- **k-replica selection is trivial:** take the top-k by score — naturally consistent and ordered.

### Working Python — Rendezvous (HRW)

```python
"""Rendezvous / Highest-Random-Weight (HRW) hashing.
Thaler & Ravishankar, 1998. O(N) per key; minimal key movement on changes;
clean top-k replica selection and weight support.
"""
import hashlib, math
from typing import Iterable

def _score(key: str, node: str) -> float:
    h = hashlib.blake2b(f"{key}\x00{node}".encode(), digest_size=8).digest()
    # map the 64-bit hash to a uniform (0,1) value
    u = int.from_bytes(h, "big") / 2**64
    u = min(max(u, 1e-18), 1 - 1e-18)
    return u

def hrw_node(key: str, nodes: Iterable[str]) -> str:
    return max(nodes, key=lambda n: _score(key, n))

def hrw_topk(key: str, nodes: Iterable[str], k: int) -> list[str]:
    return sorted(nodes, key=lambda n: _score(key, n), reverse=True)[:k]

def hrw_weighted(key: str, weighted_nodes: dict[str, float]) -> str:
    # Weighted HRW: score = -weight / ln(uniform_hash). Higher weight -> larger score.
    def w_score(n: str) -> float:
        return -weighted_nodes[n] / math.log(_score(key, n))
    return max(weighted_nodes, key=w_score)


if __name__ == "__main__":
    nodes = [f"node-{i}" for i in range(5)]
    keys = [f"key-{i}" for i in range(20000)]

    base = {k: hrw_node(k, nodes) for k in keys}

    # distribution should be ~ uniform (20000/5 = 4000 each)
    from collections import Counter
    print("distribution:", Counter(base.values()))

    # Remove node-2 -> ONLY keys that mapped to node-2 should move.
    survivors = [n for n in nodes if n != "node-2"]
    moved = sum(1 for k in keys if hrw_node(k, survivors) != base[k])
    on_node2 = sum(1 for v in base.values() if v == "node-2")
    print(f"moved={moved}  were_on_node2={on_node2}")
    assert moved == on_node2, "HRW must move ONLY the removed node's keys"

    print("top-3 replicas for key-7:", hrw_topk("key-7", nodes, 3))
    print("OK: minimal disruption + top-k replica selection")
```

## 9. Jump Consistent Hash (Google)

Lamping & Veach, *A Fast, Minimal Memory, Consistent Hash Algorithm* (Google, 2014). It maps a 64-bit key to a bucket in `[0, num_buckets)` with **O(1) memory, O(ln N) time**, and *optimally even* distribution and minimal movement — using **zero data structures**, just arithmetic and a PRNG seeded by the key.

The idea: simulate, very fast, the sequence of bucket-count increases. As the number of buckets grows from `b+1` to `b+2`, the probability that a given key *jumps* to the new last bucket is exactly `1/(b+2)` (so that load stays balanced). The algorithm leaps directly from one jump to the next using a deterministic PRNG, skipping all the no-jump steps.

Constraints / when it wins:
- Buckets are numbered `0..N-1` and you can only **add/remove from the end** (no arbitrary node removal). Perfect for **sharding by shard-count** (resharding a fixed, contiguously-numbered shard set). *Not* suited to arbitrary node IDs joining/leaving — for that use HRW or a ring.
- Beats a ring on memory (none) and balance (provably optimal), with no virtual nodes.

### Working Python — Jump Consistent Hash (the reference algorithm)

```python
"""Jump Consistent Hash — Lamping & Veach, Google, 2014.
O(1) memory, O(ln n) time, optimal balance, minimal moves.
Constraint: buckets are 0..n-1; can only grow/shrink at the tail.
Direct port of the paper's C reference (with explicit 64-bit masking for Python).
"""
MASK64 = (1 << 64) - 1

def jump_consistent_hash(key: int, num_buckets: int) -> int:
    assert num_buckets > 0
    b, j = -1, 0
    while j < num_buckets:
        b = j
        # LCG step exactly as in the paper (constants from the reference impl)
        key = (key * 2862933555777941757 + 1) & MASK64
        # next jump target
        j = int((b + 1) * ((1 << 31) / float((key >> 33) + 1)))
    return b


if __name__ == "__main__":
    from collections import Counter
    N = 10
    keys = list(range(200000))
    dist = Counter(jump_consistent_hash(k, N) for k in keys)
    print("distribution over", N, "buckets:", dict(sorted(dist.items())))
    spread = (max(dist.values()) - min(dist.values())) / (len(keys) / N)
    print(f"max-min spread = {spread:.3%} of mean (should be tiny)")
    assert spread < 0.05

    # Grow 10 -> 11 buckets: only keys that move should move to the NEW bucket 10.
    moved, moved_to_new = 0, 0
    for k in keys:
        before = jump_consistent_hash(k, 10)
        after = jump_consistent_hash(k, 11)
        if before != after:
            moved += 1
            assert after == 10, "a moved key must land on the newly added bucket"
            moved_to_new += 1
    frac = moved / len(keys)
    print(f"moved {moved} keys ({frac:.3%}); ideal ~ 1/11 = {1/11:.3%}")
    assert abs(frac - 1/11) < 0.01
    print("OK: optimal balance + minimal, correct movement")
```

## 10. Maglev Hashing (brief)

Eisenbud et al., *Maglev: A Fast and Reliable Software Network Load Balancer* (Google, NSDI 2016). Maglev's consistent-hashing scheme builds a fixed-size **lookup table** (a permutation of size `M`, a prime ≫ N, e.g. 65537) by having each backend "claim" slots according to per-backend permutation sequences. Lookups are O(1) table reads. It deliberately trades a *little* extra disruption on backend changes for **near-perfect load balance** and **tiny lookup cost** — exactly what an L4 load balancer needs to spread millions of flows evenly while keeping connection→backend affinity mostly stable. The table is also trivially shareable across LB nodes for consistent routing.

## 11. Choosing — When Each Beats a Ring

| Algorithm | Memory | Lookup | Balance | Arbitrary node remove? | Weights | Best for |
|---|---|---|---|---|---|---|
| Ring + vnodes | O(N·V) | O(log NV) | good (∝ 1/√V) | yes | via vnode count | general cache/storage, heterogeneous churn |
| **Rendezvous (HRW)** | O(N) | O(N) (O(log N) skeleton) | excellent | **yes (only its keys move)** | clean | small N, replica/top-k selection, GFS chunk placement |
| **Jump hash** | **O(1)** | O(ln N) | **optimal** | **no (tail only)** | no | fixed, tail-growing shard counts |
| Maglev | O(M) table | **O(1)** | near-perfect | yes (small disruption) | via slot counts | L4/L7 software load balancers, millions of flows |

Heuristics:
- **Need top-k replicas, weights, arbitrary removal, small N?** → HRW.
- **Sharding a contiguous shard-count you only grow at the end, zero memory?** → Jump hash.
- **L4 load balancer needing O(1) lookup + great balance across huge fleets?** → Maglev.
- **General-purpose distributed cache/DB with heterogeneous nodes and churn?** → ring with vnodes (Dynamo/Cassandra).

---

## 12. Key Takeaways

1. There is **no global now**; reason about **causality (happens-before)**, not wall-clock simultaneity.
2. **Lamport clocks** give a causal *total order* but cannot detect concurrency; **vector clocks** detect concurrency (conflicts) at O(N) cost — the basis of Dynamo-style conflict resolution.
3. **HLC** fuses physical and logical time into one monotonic timestamp that is both causally correct *and* near-wall-clock — used by CockroachDB/Yugabyte. **TrueTime** instead bounds physical-clock uncertainty (`ε`) and pays `~2ε` of **commit-wait** to buy *external consistency* in Spanner.
4. **Clock skew is a correctness hazard** (NTP step-back, GC/VM pauses, leap seconds, LWW data loss) — never gate correctness on raw wall-clock ordering.
5. The basic hash ring is **imbalanced**; vnodes fix balance at a memory cost.
6. **Rendezvous (HRW)** gives minimal disruption, weights, and free top-k replica selection at O(N). **Jump consistent hash** gives optimal balance with O(1) memory but only tail-growth. **Maglev** gives O(1) lookups with near-perfect balance for load balancers. Pick by churn pattern, N, and lookup-cost budget.

---

## References

- Leslie Lamport, *Time, Clocks, and the Ordering of Events in a Distributed System*, CACM 1978.
- Colin Fidge / Friedemann Mattern, *Vector clocks* (Timestamps in message-passing systems), 1988.
- S. Kulkarni, M. Demirbas, et al., *Logical Physical Clocks and Consistent Snapshots in Globally Distributed Databases* (HLC), 2014.
- J. Corbett et al., *Spanner: Google's Globally-Distributed Database* (TrueTime), OSDI 2012.
- D. Karger et al., *Consistent Hashing and Random Trees*, STOC 1997.
- D. Thaler & C. Ravishankar, *Using Name-Based Mappings to Increase Hit Rates* (Rendezvous/HRW), IEEE/ACM ToN 1998.
- J. Lamping & E. Veach, *A Fast, Minimal Memory, Consistent Hash Algorithm* (Jump hash), Google, 2014.
- D. Eisenbud et al., *Maglev: A Fast and Reliable Software Network Load Balancer*, NSDI 2016.
- G. DeCandia et al., *Dynamo: Amazon's Highly Available Key-value Store*, SOSP 2007.
