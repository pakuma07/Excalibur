# Probabilistic Data Structures

## Introduction

A **probabilistic data structure** answers a question *approximately* in exchange for using dramatically less memory (and often less time) than an exact structure would. They trade a small, *bounded*, *tunable* error for orders-of-magnitude savings in space.

At small scale you never need them: a Python `set` answers "have I seen this?" exactly. But at **internet scale** — billions of items, thousands of requests per second, data that won't fit in RAM — exact structures become impossible or ruinously expensive. Probabilistic structures are the standard tool for these problems.

> **The core bargain:** "I will answer your question using a tiny fraction of the memory, and in return I may be wrong in a specific, predictable way — but never in the dangerous direction, and you can dial down the error rate by spending a little more memory."

---

## The Problem They Solve

Consider three real questions a large system asks constantly:

| Question | Exact structure | Cost at 1 billion items |
| --- | --- | --- |
| "Have I seen this URL before?" (membership) | Hash set | ~32–100 GB |
| "How many times has this IP hit us?" (frequency) | Hash map of counters | Tens of GB |
| "How many *distinct* users visited today?" (cardinality) | Hash set | Tens of GB |

The exact answers all require storing every distinct item, so memory grows **linearly with the number of items**. The probabilistic answers below use **sublinear or constant** memory:

| Question | Probabilistic structure | Typical cost for 1 billion items |
| --- | --- | --- |
| Membership | **Bloom filter** | ~1.7 GB at 1% error (≈ 9.6 bits/item) |
| Frequency | **Count-Min Sketch** | A few MB (fixed-size grid) |
| Cardinality | **HyperLogLog** | ~1.5 KB for ±2% error |

The savings are not marginal — they are the difference between "fits in L3 cache" and "needs a distributed database."

---

## Why Approximation Is Acceptable

The key insight: **many decisions tolerate small errors.**

- A web crawler that occasionally re-crawls a page it already saw wastes a little bandwidth — harmless.
- A cache that occasionally reports "this key might be cached" and is wrong just does one extra lookup.
- A dashboard reporting "≈ 4.2 million unique visitors" is just as useful as "4,217,338" — and nobody can act on the last three digits anyway.

The art is matching the *direction* of the error to the *safety* of the use case. Bloom filters, for example, can give false positives but **never** false negatives — so they're safe as a "definitely not present" pre-filter.

---

## 1. Bloom Filter — Approximate Membership

### What problem it solves

A Bloom filter answers **"is this element in the set?"** It can return:

- **"definitely not in the set"** — always correct (no false negatives), or
- **"possibly in the set"** — usually correct, but sometimes a **false positive**.

It does this without storing the elements themselves — only a bit array. This makes it ideal as a cheap gatekeeper in front of an expensive lookup.

### How it works

A Bloom filter is:
- A bit array of `m` bits, all initially `0`.
- `k` independent hash functions, each mapping an element to a position in `[0, m)`.

**Insert(x):** hash `x` with all `k` functions, set those `k` bits to `1`.

**Query(x):** hash `x` with all `k` functions. If **any** of those bits is `0`, `x` is *definitely* not present. If **all** are `1`, `x` is *probably* present (those bits could have been set by other elements — a false positive).

```
m = 12 bits, k = 3 hash functions

Insert "cat":  h1=1, h2=4, h3=9
index:  0  1  2  3  4  5  6  7  8  9 10 11
bits:  [0][1][0][0][1][0][0][0][0][1][0][0]

Insert "dog":  h1=4, h2=7, h3=10
bits:  [0][1][0][0][1][0][0][1][0][1][1][0]

Query "cat":   bits 1,4,9 all 1  -> "possibly present"  (correct)
Query "fox":   h1=2 -> bit 2 is 0 -> "definitely NOT present" (correct, cheap exit)
Query "owl":   h1=1, h2=7, h3=9 all happen to be 1 -> "possibly present" (FALSE POSITIVE)
```

### The math: choosing `m` and `k`

Given `n` expected elements and a target false-positive probability `p`:

- Optimal number of bits: `m = -(n * ln p) / (ln 2)^2`
- Optimal number of hash functions: `k = (m / n) * ln 2`

The achieved false-positive rate after inserting `n` items is approximately:

```
p ≈ (1 - e^(-k*n/m))^k
```

Intuition for `k`: too few hash functions and you don't distinguish elements well; too many and you fill the array with `1`s too fast. The optimum balances these and lands around half the bits set to `1`.

### Working Python implementation

```python
import math
import hashlib


class BloomFilter:
    """A space-efficient probabilistic set with no false negatives."""

    def __init__(self, expected_items: int, false_positive_rate: float = 0.01):
        if expected_items <= 0:
            raise ValueError("expected_items must be positive")
        if not (0 < false_positive_rate < 1):
            raise ValueError("false_positive_rate must be in (0, 1)")

        self.n = expected_items
        self.p = false_positive_rate

        # Optimal size m and hash count k from the standard formulas.
        self.m = self._optimal_m(expected_items, false_positive_rate)
        self.k = self._optimal_k(self.m, expected_items)

        # Bit array stored as a Python bytearray (8 bits per byte).
        self.bits = bytearray((self.m + 7) // 8)
        self.count = 0  # number of items inserted

    @staticmethod
    def _optimal_m(n: int, p: float) -> int:
        return max(1, int(math.ceil(-(n * math.log(p)) / (math.log(2) ** 2))))

    @staticmethod
    def _optimal_k(m: int, n: int) -> int:
        return max(1, int(round((m / n) * math.log(2))))

    def _hashes(self, item):
        """Generate k positions using double hashing (Kirsch-Mitzenmacher).

        Two independent base hashes are combined as h1 + i*h2 to derive k
        positions, which behaves like k independent hash functions.
        """
        data = str(item).encode("utf-8")
        h1 = int.from_bytes(hashlib.sha256(data).digest()[:8], "big")
        h2 = int.from_bytes(hashlib.md5(data).digest()[:8], "big")
        for i in range(self.k):
            yield (h1 + i * h2) % self.m

    def add(self, item) -> None:
        for pos in self._hashes(item):
            self.bits[pos // 8] |= (1 << (pos % 8))
        self.count += 1

    def __contains__(self, item) -> bool:
        """Return False => definitely absent. True => probably present."""
        for pos in self._hashes(item):
            if not (self.bits[pos // 8] & (1 << (pos % 8))):
                return False
        return True

    def current_false_positive_rate(self) -> float:
        """Estimated FP rate given how many items have actually been added."""
        return (1 - math.exp(-self.k * self.count / self.m)) ** self.k


if __name__ == "__main__":
    bf = BloomFilter(expected_items=10_000, false_positive_rate=0.01)
    print(f"Allocated m={bf.m} bits ({bf.m // 8} bytes), k={bf.k} hashes")

    present = {f"user_{i}" for i in range(5_000)}
    for item in present:
        bf.add(item)

    # No false negatives: every inserted item must report present.
    assert all(item in bf for item in present), "Bloom filter had a false negative!"

    # Measure false positives on items we never inserted.
    false_pos = sum(1 for i in range(5_000, 15_000) if f"user_{i}" in bf)
    print(f"Observed false positives: {false_pos / 10_000:.4f}")
    print(f"Predicted FP rate:        {bf.current_false_positive_rate():.4f}")
```

### When to use

- **CDN / cache front-door:** "Is this key possibly in cache?" Skip a network round-trip on a "no."
- **Databases (Cassandra, HBase, RocksDB):** avoid disk reads for keys not in an SSTable.
- **Web crawlers:** "Have I already queued this URL?"
- **Spam / malware lists:** check a URL against billions of known-bad entries cheaply.

### Trade-offs and limitations

| Property | Behavior |
| --- | --- |
| False negatives | **Never** — a "no" is always correct |
| False positives | Possible; rate rises as the filter fills past `n` |
| Deletion | **Not supported** (clearing bits would corrupt other items) — use a *Counting Bloom Filter* if you must delete |
| Resizing | Not possible in place; you must rebuild |
| Space | `~1.44 * log2(1/p)` bits per item, independent of item size |

---

## 2. Count-Min Sketch — Approximate Frequency

### What problem it solves

A Count-Min Sketch (CMS) answers **"approximately how many times have I seen item x?"** in fixed memory, regardless of how many distinct items exist. It's the frequency analog of a Bloom filter.

It **overestimates** (never underestimates) counts due to hash collisions, and the error is bounded.

### How it works

A CMS is a 2-D grid of `d` rows × `w` columns of integer counters, with one independent hash function per row.

**Update(x, c):** for each row `i`, hash `x` to a column and add `c` to that counter.

**Query(x):** for each row, read the counter `x` hashes to; return the **minimum** across rows. Taking the minimum cancels much of the over-counting caused by collisions in any single row.

```
d = 3 rows, w = 5 columns

After adding "A" x3, "B" x2 (collides with A in row 0):
        col0 col1 col2 col3 col4
row0  [  5 ][ 0 ][ 0 ][ 0 ][ 0 ]   <- A and B both hashed here: 3+2=5
row1  [  3 ][ 0 ][ 2 ][ 0 ][ 0 ]   <- A->col0=3, B->col2=2
row2  [  0 ][ 3 ][ 0 ][ 0 ][ 2 ]   <- A->col1=3, B->col4=2

Query "A": min(row0=5, row1=3, row2=3) = 3   (exact, collision cancelled)
Query "B": min(row0=5, row1=2, row2=2) = 2   (exact)
```

### The math

Choose width and depth from your error tolerance:

- `w = ceil(e / epsilon)` — controls the *error magnitude* (`epsilon`)
- `d = ceil(ln(1 / delta))` — controls the *probability* (`delta`) of exceeding that error

Then with probability `1 - delta`, the estimate exceeds the true count by at most `epsilon * N`, where `N` is the total count of all updates.

### Working Python implementation

```python
import math
import hashlib


class CountMinSketch:
    """Approximate frequency counts in fixed memory. Never underestimates."""

    def __init__(self, epsilon: float = 0.001, delta: float = 0.01):
        # epsilon: error as a fraction of total count.  delta: failure prob.
        self.w = max(1, int(math.ceil(math.e / epsilon)))
        self.d = max(1, int(math.ceil(math.log(1 / delta))))
        self.table = [[0] * self.w for _ in range(self.d)]
        self.total = 0

    def _columns(self, item):
        data = str(item).encode("utf-8")
        h1 = int.from_bytes(hashlib.sha256(data).digest()[:8], "big")
        h2 = int.from_bytes(hashlib.md5(data).digest()[:8], "big")
        for i in range(self.d):
            yield i, (h1 + i * h2) % self.w

    def add(self, item, count: int = 1) -> None:
        for row, col in self._columns(item):
            self.table[row][col] += count
        self.total += count

    def estimate(self, item) -> int:
        return min(self.table[row][col] for row, col in self._columns(item))


if __name__ == "__main__":
    import random
    from collections import Counter

    cms = CountMinSketch(epsilon=0.001, delta=0.01)
    print(f"Grid: {cms.d} rows x {cms.w} cols = {cms.d * cms.w} counters")

    truth = Counter()
    for _ in range(100_000):
        item = f"ip_{random.randint(1, 2_000)}"
        cms.add(item)
        truth[item] += 1

    # Estimates are >= truth, and close for heavy hitters.
    for item, true_count in truth.most_common(3):
        est = cms.estimate(item)
        print(f"{item}: true={true_count}, est={est}, over={est - true_count}")
        assert est >= true_count, "CMS underestimated — impossible!"
```

### When to use

- **Heavy hitters / top-K:** find the most frequent search terms, hottest cache keys, noisiest IPs.
- **Streaming analytics:** count events in an unbounded stream without storing per-key state.
- **Rate limiting at scale:** approximate per-client request counts (see doc 19).
- **Network monitoring:** detect DDoS sources by approximate packet counts.

### Trade-offs

| Property | Behavior |
| --- | --- |
| Error direction | Always **overestimates** (collisions only add) |
| Memory | Fixed `d * w` counters, **independent of the number of distinct keys** |
| Weakness | Low-frequency items get the most relative error (collisions with heavy hitters) |
| Variant | **Count-Min-Mean** subtracts estimated noise for better low-count accuracy |

---

## 3. HyperLogLog — Approximate Cardinality

### What problem it solves

HyperLogLog (HLL) answers **"how many *distinct* items have I seen?"** (cardinality) using just a couple of kilobytes — even for billions of distinct items. A `set` would need gigabytes.

### The intuition (this is the clever part)

Imagine flipping a fair coin and recording the **longest run of leading heads** you ever see. If the longest run you've seen is 3 heads in a row (HHH...), you've *probably* flipped around `2^3 = 8` times. Long rare runs only show up when you've made many attempts.

HLL applies this to hashes. Hash each item to a uniformly random bit string. Count the number of **leading zeros** in the hash. A hash with `k` leading zeros occurs with probability `1/2^(k+1)`, so seeing a maximum of `k` leading zeros suggests you've hashed roughly `2^k` distinct items.

Two refinements make it accurate:

1. **Stochastic averaging:** Using a single max-leading-zeros estimate is wildly noisy. So split the hash: use the first `p` bits to pick one of `m = 2^p` "registers" (buckets), and track the max leading-zeros within each bucket separately. Averaging across many buckets stabilizes the estimate.
2. **Harmonic mean + bias correction:** HLL combines the registers using a *harmonic mean* (which suppresses outliers) times a constant `alpha_m` and `m^2`. Small- and large-range corrections handle the extremes.

```
Hash of item -> 0110 1101 0101 ...
                ^^^^ first p=4 bits = register index (here 0110 = 6)
                     ^^^^^... remaining bits: count leading zeros, store max in register[6]
```

Because each register only stores a small max-leading-zeros value (a 5–6 bit number), `m` registers fit in `m * 6 / 8` bytes. With `m = 16384` (p=14), that's ~12 KB for a ~0.8% standard error.

### Working (simplified) Python implementation

```python
import hashlib
import math


class HyperLogLog:
    """Estimate the number of DISTINCT items in ~constant memory."""

    def __init__(self, p: int = 14):
        # p bits select the register; m = 2^p registers.
        if not (4 <= p <= 16):
            raise ValueError("p should be between 4 and 16")
        self.p = p
        self.m = 1 << p
        self.registers = [0] * self.m
        self.alpha = self._alpha(self.m)

    @staticmethod
    def _alpha(m: int) -> float:
        if m == 16:
            return 0.673
        if m == 32:
            return 0.697
        if m == 64:
            return 0.709
        return 0.7213 / (1 + 1.079 / m)

    def _hash64(self, item) -> int:
        data = str(item).encode("utf-8")
        return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")

    def add(self, item) -> None:
        x = self._hash64(item)
        # First p bits = register index.
        idx = x >> (64 - self.p)
        # Remaining 64 - p bits: position of the leftmost 1-bit (rank).
        remaining = x & ((1 << (64 - self.p)) - 1)
        rank = self._leading_zeros_rank(remaining, 64 - self.p)
        if rank > self.registers[idx]:
            self.registers[idx] = rank

    @staticmethod
    def _leading_zeros_rank(value: int, bits: int) -> int:
        # rank = (number of leading zeros in the `bits`-wide value) + 1
        if value == 0:
            return bits + 1
        return bits - value.bit_length() + 1

    def count(self) -> int:
        # Raw harmonic-mean estimate.
        raw = self.alpha * self.m ** 2 / sum(2.0 ** -r for r in self.registers)

        # Small-range correction: linear counting when many registers are empty.
        if raw <= 2.5 * self.m:
            zeros = self.registers.count(0)
            if zeros != 0:
                return int(round(self.m * math.log(self.m / zeros)))
        return int(round(raw))

    def merge(self, other: "HyperLogLog") -> None:
        """HLLs are mergeable: take the elementwise max of registers."""
        if self.p != other.p:
            raise ValueError("cannot merge HLLs with different precision")
        self.registers = [max(a, b) for a, b in zip(self.registers, other.registers)]


if __name__ == "__main__":
    hll = HyperLogLog(p=14)
    true_n = 1_000_000
    for i in range(true_n):
        hll.add(f"user_{i}")

    est = hll.count()
    error = abs(est - true_n) / true_n
    print(f"True distinct: {true_n}")
    print(f"HLL estimate:  {est}")
    print(f"Relative error: {error:.4%}")  # typically well under 1%
    print(f"Memory used:   ~{hll.m} bytes (vs ~{true_n * 16 // 1_000_000} MB for a set)")
```

### When to use

- **Unique visitor / unique user counts** on huge dashboards (Redis `PFADD`/`PFCOUNT` is HLL).
- **Distinct query counts** across massive logs.
- **Merging across shards:** count distinct users across 100 servers by computing one HLL per server and merging — a property exact sets share but at far higher cost.

### Trade-offs

| Property | Behavior |
| --- | --- |
| Error | Relative standard error ≈ `1.04 / sqrt(m)` (e.g. ~0.8% at `m=16384`) |
| Memory | Constant — a few KB regardless of cardinality |
| Mergeable | Yes (elementwise max) — great for distributed counting |
| Cannot do | Cannot test membership or list the items, only count distinct |

---

## Related "Clever Structures" (brief)

### Skip List

A **skip list** is a probabilistic alternative to a balanced binary search tree. It is a linked list with multiple "express lane" levels: each node is promoted to a higher level with probability ~0.5, creating shortcuts that let search skip large chunks.

```
L3:  HEAD --------------------------> 9 ----------> NIL
L2:  HEAD --------> 4 --------------> 9 ----------> NIL
L1:  HEAD --> 2 --> 4 --> 6 -------> 9 --> 12 ----> NIL
L0:  HEAD --> 2 --> 4 --> 6 --> 7 -> 9 --> 12 -> 15 NIL
```

Expected `O(log n)` search/insert/delete, much simpler to implement than red-black trees, and easy to make concurrent. Used by Redis sorted sets and LevelDB's memtable.

### Merkle Tree

A **Merkle tree** (hash tree) is a tree where each leaf is the hash of a data block and each internal node is the hash of its children. The single **root hash** summarizes all the data.

```
            root = H(H_AB + H_CD)
           /                      \
     H_AB = H(H_A + H_B)    H_CD = H(H_C + H_D)
       /        \              /        \
   H_A=H(A)  H_B=H(B)     H_C=H(C)   H_D=H(D)
```

It lets two parties verify they hold identical data by comparing one root hash, and *locate* differences in `O(log n)` by descending only into subtrees whose hashes differ. Used in Git, Bitcoin/blockchains, Cassandra/DynamoDB anti-entropy repair, and IPFS. Not "approximate," but in the same family of structures that buy huge efficiency through a clever hashing trick.

---

## Comparison Summary

| Structure | Question answered | Error direction | Memory | Mergeable | Deletes? |
| --- | --- | --- | --- | --- | --- |
| Bloom filter | Is x present? | False positives only | ~9.6 bits/item @ 1% | Yes (OR) | No |
| Count-Min Sketch | How often is x? | Overestimate only | Fixed grid | Yes (add) | No (basic) |
| HyperLogLog | How many distinct? | ± relative error | ~KB constant | Yes (max) | No |
| Skip list | Ordered set / map | Exact | ~linear | n/a | Yes |
| Merkle tree | Do datasets match? | Exact | ~linear | n/a | n/a |

---

## Key Takeaways

- **Probabilistic structures trade bounded, tunable error for huge memory savings** — essential once data no longer fits in RAM or exact storage is too expensive.
- **Match the error direction to the use case.** Bloom filters never give false negatives (safe as a "definitely-not-present" gate); Count-Min Sketch never underestimates; HyperLogLog is symmetric but tiny.
- **Bloom filter** = membership; **Count-Min Sketch** = frequency; **HyperLogLog** = cardinality. These three cover the most common "at-scale" counting questions.
- **You tune the trade-off explicitly:** more bits/registers → lower error. The formulas (`m`, `k` for Bloom; `w`, `d` for CMS; `m=2^p` for HLL) let you pick a precise operating point.
- **Mergeability matters in distributed systems:** Bloom (OR), CMS (add), and HLL (max) can each be computed per-shard and combined, enabling map-reduce-style aggregation.
- **Skip lists and Merkle trees** round out the "clever structures" toolkit: probabilistic balancing and hash-based data verification, respectively.
