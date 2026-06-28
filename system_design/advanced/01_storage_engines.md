# 01 — Storage Engines from Scratch

> **Audience:** staff/principal. You know what an index is. This doc is about *how a storage engine actually stores and retrieves bytes on disk*, and the amplification trade-offs that decide which engine you reach for.
>
> **Primary sources:** DDIA ch. 3 (Kleppmann); O'Neil et al., *The Log-Structured Merge-Tree* (1996); Bayer & McCreight, *Organization and Maintenance of Large Ordered Indexes* (1972, B-trees); Mohan et al., *ARIES* (1992, WAL/recovery); the RocksDB, LevelDB, and InnoDB internals docs.

---

## 1. Why this matters at scale

The storage engine is the layer that turns `PUT(k, v)` / `GET(k)` into device I/O. Two decisions made here dominate the cost and performance envelope of the whole system:

1. **How is data laid out on disk?** This sets your read/write/space *amplification* — the ratio between logical work requested and physical work performed. At scale you do not pay for queries; you pay for amplification. A 3× write amplification means your SSDs wear out 3× faster and your write throughput ceiling is 3× lower.
2. **How do you survive a crash mid-write?** This is the **durability** half of ACID. Get it wrong and you have silent corruption — the most expensive class of bug in a data system.

Everything else (replication, sharding, query planning) sits *on top* of a storage engine. Picking InnoDB (B-tree) vs RocksDB (LSM) changes your write ceiling, your read latency distribution, and your space cost. Staff engineers are expected to reason about that choice from amplification first principles, not vibes.

### The two families

```
                       Storage engines
                      /               \
         Page-oriented                 Log-structured
         (update-in-place)             (append-only)
              |                              |
          B-tree / B+tree                LSM-tree
          InnoDB (MySQL)                 LevelDB, RocksDB
          PostgreSQL heap+btree          Cassandra, ScyllaDB
          SQLite                         HBase, Bigtable
                                          (and WiredTiger does both)
```

- **Page-oriented (B-tree):** the database is a set of fixed-size pages; updates **overwrite pages in place**. Reads are cheap and predictable; writes do random I/O.
- **Log-structured (LSM):** all writes are **appended to a log** and never modified in place; data is reorganized in the background by *compaction*. Writes are cheap (sequential); reads may touch several files.

---

## 2. The three amplifications (the master trade-off)

Every storage engine is a point on a triangle. You cannot minimize all three.

| Amplification | Definition | Hurts |
|---|---|---|
| **Write amplification (WA)** | bytes written to device ÷ bytes written by app | SSD wear, write throughput |
| **Read amplification (RA)** | I/Os (or bytes read) per logical read | read latency, page-cache pressure |
| **Space amplification (SA)** | bytes on disk ÷ bytes of live data | storage cost |

> **The RUM conjecture** (Athanassoulis et al., 2016): for **R**ead, **U**pdate, **M**emory(space) overheads, optimizing two forces the third to suffer. The amplification triangle is the practical face of RUM.

- **B-trees**: low RA (≈ tree height, ~3–4 I/Os), low SA, but **high WA** — every write rewrites a whole page (plus the WAL → ~2× immediately) and may split pages.
- **LSM-trees**: low WA on the write path (sequential append), but **higher RA** (a key may live in several SSTables) and tunable SA (deleted/overwritten data lingers until compaction). Compaction *moves* the write cost to the background and is itself a major source of WA.

We will derive these numbers below.

---

## 3. B-tree / B+tree — the page-oriented standard

Invented by Bayer & McCreight (1972). The default index structure of essentially every relational database (InnoDB, PostgreSQL, SQLite, Oracle, SQL Server). DDIA: "B-trees are the most widely used indexing structure."

### 3.1 Structure

A B+tree (the variant DBs actually use) is a balanced, high-fanout search tree over fixed-size **pages** (typically 4 KiB–16 KiB; InnoDB uses 16 KiB):

- **Internal pages** store *only* keys + child pointers (page numbers). They are pure routing.
- **Leaf pages** store the actual keys + values (or key + row pointer). In a B+tree, **all values live at the leaves**, and leaves are linked in a sorted doubly-linked list → cheap range scans.

```
                         [ • | 50 | • | 100 | • ]          <- root (internal)
                        /         |          \
            [• |20| •|35|•]   [•|65|•|80|•]   [•|140|•]      <- internal
            /    |    \          ...
   [10:v 15:v]<->[20:v 28:v]<->[35:v 42:v]<-> ...            <- leaves (linked list)
```

**Fanout** = number of child pointers per page. With a 16 KiB page, 8-byte keys and 8-byte child pointers (16 bytes/entry), fanout ≈ 16384 / 16 ≈ **1000**. That is why trees are shallow:

```
keys addressable = fanout ^ height
1000^1 = 10^3,  1000^2 = 10^6,  1000^3 = 10^9,  1000^4 = 10^12
```

So **4 levels index a trillion keys**. A point lookup is ~4 page reads — and the top 1–2 levels are almost always in the page cache, so it's effectively 1–2 *disk* I/Os.

> Tree height: `h = ceil(log_fanout(N))`. The shallow height is the whole point — it bounds read amplification at `O(log_b N)` with a large base `b`.

### 3.2 Writes, splits, and rebalancing

To insert key `k`:
1. Walk from root to the target leaf.
2. If the leaf has room, insert in sorted order, rewrite the leaf page.
3. If the leaf is **full**, **split** it: allocate a new page, move half the entries over, and **push the median key up** into the parent. If the parent is now full, it splits too — recursively, possibly up to the root (the only way the tree grows in height).

Deletes can trigger **merges/rebalancing** when a page drops below ~half full, though many engines defer this and just leave pages under-full (cheaper, and they refill).

**This is why B-trees have high write amplification:** a single logical row write rewrites an entire page (16 KiB to change 100 bytes), and a split rewrites *multiple* pages. Combined with the WAL (§5), a tiny write hits the disk at least twice and often more.

### 3.3 Why DBs love B+trees

- **Reads are predictable**: bounded height, in-place data, no merging at read time → tight latency distribution (good tails — see [03](03_tail_latency.md)).
- **Range scans are excellent**: linked leaves → sequential scan after one seek. Critical for SQL `WHERE x BETWEEN ... ORDER BY`.
- **One copy of each key** → low space amplification.
- **Each key exists in exactly one place** → simpler concurrency (page latches) and a natural fit for MVCC version chains (§6).

### 3.4 Concurrency: latch crabbing

B-trees are mutated in place by concurrent threads, so they need **latches** (short-lived physical locks on pages, distinct from transaction *locks*). The classic technique is **latch crabbing / coupling**: latch the parent, latch the child, and release the parent only once you've confirmed the child won't split up into it. This is delicate — a major reason LSM engines (no in-place mutation) are simpler to make concurrent and lock-free on the write path.

---

## 4. LSM-tree — the log-structured standard

From O'Neil, Cheng, Gawlick & O'Neil, *The Log-Structured Merge-Tree* (1996). Popularized by Google's Bigtable → LevelDB → RocksDB, and used by Cassandra, ScyllaDB, HBase, InfluxDB, CockroachDB (Pebble), and others.

**Core idea:** never update in place. Buffer writes in memory; flush sorted runs to disk; merge runs in the background.

### 4.1 The components

```
         WRITE PATH                                READ PATH
  put(k,v)                                  get(k)
     |                                         |
     v   (1) append                            v  check newest -> oldest
  +--------+  durability                  +----------+
  |  WAL   |  (replayed on crash)         | memtable | (in-RAM, sorted)
  +--------+                              +----------+
     |                                         | miss
     v                                         v
  +----------+   flush when full         +-------------------+
  | memtable |  ----------------------->  | SSTable L0 (new)  |  bloom + index
  | (sorted) |                            +-------------------+
  +----------+                                 | miss
                                               v
                                          +-------------------+
                  background compaction    | SSTable L1...Ln   |  (older)
                  merges & drops tombstones+-------------------+
```

- **Memtable**: an in-memory sorted structure (skip list in LevelDB/RocksDB; we'll use a sorted dict). All writes go here first. A delete writes a **tombstone** marker — you cannot remove a key that lives in an immutable on-disk file, so you shadow it.
- **WAL (write-ahead log)**: before the memtable is touched, the op is appended to an on-disk log so an in-memory memtable can be recovered after a crash (§5).
- **SSTable (Sorted String Table)**: when the memtable hits a size threshold it is **flushed**: written out as an immutable, sorted file. Because it's sorted, it carries a sparse index (every Nth key → offset) and a **bloom filter** over its keys.
- **Compaction**: a background process that merges SSTables, keeping only the newest version of each key and dropping tombstoned keys. This is where read/space amplification is reclaimed at the cost of write amplification.

### 4.2 The read path and why bloom filters are essential

A `get(k)` checks the memtable, then SSTables newest→oldest, returning the first hit. Without help, a key that **doesn't exist** would force a read of *every* SSTable — catastrophic read amplification.

A **bloom filter** (Bloom, 1970) per SSTable answers "is `k` *possibly* in this file?" with no false negatives and a tunable false-positive rate. If the filter says "no", we skip the file entirely with zero disk I/O. This turns the common "key not present in this level" case from a disk read into a few bit tests.

> Bloom filter math: with `m` bits, `n` keys, `k` hash functions, optimal `k = (m/n) ln 2`, giving false-positive rate `p ≈ (1 - e^{-kn/m})^k`. RocksDB defaults to ~10 bits/key → `p ≈ 1%`. Cost: ~1.44·log2(1/p) bits per key. (See the probabilistic-data-structures concept doc for the full derivation.)

### 4.3 Compaction strategies — the central tuning knob

| Strategy | How | Optimizes | Pays in |
|---|---|---|---|
| **Size-tiered (STCS)** | When ~T SSTables of similar size exist, merge them into one bigger one. | **Write** amplification (fewer merges) | **Space** (up to ~2× — a key can be duplicated across many tiers during merge) and **read** (more files to check) |
| **Leveled (LCS)** | Levels L0..Ln, each ~10× the previous. Each level (except L0) holds **non-overlapping** key ranges. Compaction picks an SSTable and merges it into the next level's overlapping SSTables. | **Read** & **space** amplification (≤2 files per level overlap; ~10% space overhead) | **Write** amplification (a key may be rewritten ~once per level → WA ≈ number of levels) |

- **Cassandra** defaults to STCS (write-heavy, time-series friendly); offers LCS for read-heavy.
- **RocksDB/LevelDB** default to **leveled** compaction.

**Write amplification of leveled compaction** is roughly the level fanout `T` times the number of levels: `WA ≈ T · L` where `L = log_T(DB_size / L0_size)`. With `T=10` and 5 levels you can see WA in the tens — the dominant cost of an LSM under sustained writes, and why RocksDB exposes so many compaction knobs.

### 4.4 LSM vs B-tree, settled

| | B-tree | LSM-tree |
|---|---|---|
| Write path | random page writes + WAL → **high WA** | sequential append → **low WA on ingest**, deferred WA in compaction |
| Read (point) | ~tree height, predictable → **low, tight RA** | memtable + bloom-gated SSTables → **higher, more variable RA** |
| Range scan | excellent (linked leaves) | good but must merge across levels |
| Space | low (one copy) | higher; tunable; tombstones/old versions linger until compaction |
| Write throughput | bounded by random I/O | **higher** (sequential), the reason LSMs dominate write-heavy stores |
| Tail latency | tight | **compaction can cause latency spikes** (write stalls when L0 backs up) — a real ops concern |
| Concurrency | latch crabbing (hard) | append-only writes (easier, lock-free friendly) |

**Rule of thumb:** write-heavy / high-ingest → LSM (Cassandra, RocksDB). Read-heavy with strong range/transaction needs and predictable latency → B-tree (PostgreSQL, InnoDB). WiredTiger (MongoDB) implements both and lets you choose per collection.

---

## 5. WAL & crash recovery

A write is **durable** only once it survives a crash. But fsync'ing the actual data structure on every write is slow (random I/O for B-trees; the whole point of buffering for LSMs). The universal answer is **write-ahead logging**:

> **WAL rule:** append the change to a sequential, append-only log and `fsync` it **before** applying the change to the main structure (or the in-memory buffer).

Because the log is append-only it's a *sequential* write (fast, fdatasync-friendly). On restart, the engine **replays** the log from the last checkpoint to reconstruct the lost in-memory state (LSM memtable) or to finish/undo half-applied page writes (B-tree).

### 5.1 ARIES (Mohan et al., 1992) — the canonical recovery protocol

The recovery algorithm behind essentially every serious relational engine. Three phases on restart:

1. **Analysis** — scan the log forward from the last checkpoint to determine which transactions were in-flight (the "loser" transactions) and which dirty pages need attention.
2. **Redo** — replay *all* logged changes (even uncommitted ones) to bring pages to their crash-time state. ARIES is **redo-everything** ("repeating history").
3. **Undo** — roll back the loser transactions using the log's before-images, writing **compensation log records (CLRs)** so the undo itself is idempotent if we crash again mid-recovery.

Key ARIES principles staff engineers cite:
- **WAL**: log record on stable storage *before* the corresponding data page.
- **Repeating history**: redo even losers, then undo — simpler and correct.
- **LSN (Log Sequence Number)** stamped on every page so recovery knows whether a page already reflects a given log record (avoid double-redo).

### 5.2 The fsync truth

Durability hinges on `fsync`/`fdatasync` actually pushing to stable media. Lying disks/controllers with volatile write caches, or `fsync` returning before data is durable, have caused real data-loss bugs (the "fsync gate" in PostgreSQL, 2018). At scale, durability is a *storage stack* property, not just a code property.

---

## 6. MVCC & snapshot isolation internals

Locking readers against writers kills concurrency. **Multi-Version Concurrency Control** instead keeps **multiple versions** of each row so that *readers never block writers and writers never block readers*. This is how PostgreSQL, InnoDB, Oracle, and CockroachDB implement **snapshot isolation (SI)**.

### 6.1 The mechanism

Each row version is tagged with the **transaction ID** that created it (`xmin`) and the one that deleted/superseded it (`xmax`). Each transaction gets a **snapshot**: the set of transaction IDs that had committed at its start. A version is **visible** to a transaction iff:

- its `xmin` is committed and ≤ snapshot, **and**
- its `xmax` is either empty or belongs to a transaction not in the snapshot (i.e., not yet committed at snapshot time).

```
row "x" version chain (newest -> oldest):
   v3 (xmin=105, xmax=-)      <- created by txn 105
   v2 (xmin=102, xmax=105)    <- created by 102, deleted by 105
   v1 (xmin= 98, xmax=102)

Txn with snapshot {committed <= 100} reading x  => sees v1
(98 committed & <=100; xmax 102 not in snapshot)
```

- **PostgreSQL** stores versions inline in the heap (each `UPDATE` writes a new tuple), which is why it needs **VACUUM** to reclaim dead tuples — its form of LSM-like garbage. Old tuples = space amplification you must actively collect.
- **InnoDB / Oracle** keep old versions in an **undo log / rollback segment** and reconstruct old versions on demand.

### 6.2 Snapshot isolation is *not* serializable

SI prevents dirty reads, non-repeatable reads, and lost updates, but allows **write skew** (two transactions read an overlapping set, then each writes based on a now-stale premise — e.g., both doctors go off-call because each saw the other on-call). Achieving true serializability adds **SSI (Serializable Snapshot Isolation)** — track read/write dependencies (rw-antidependencies) and abort one transaction in a dangerous cycle. PostgreSQL's `SERIALIZABLE` is SSI (Cahill et al., 2008). DDIA ch. 7 is the reference treatment.

---

## 7. Indexes: clustered, secondary, covering

- **Clustered index**: the table data **is** the leaf of the index — rows are physically stored in primary-key order. InnoDB tables are *always* clustered on the PK (or a hidden rowid). A PK lookup fetches the row directly; no second hop.
- **Secondary (non-clustered) index**: a separate B-tree whose leaves store the indexed column(s) + a pointer back to the row. In InnoDB that pointer is the **primary key**, so a secondary-index lookup that needs non-indexed columns does **two** B-tree traversals (index → PK → clustered index). This is why fat PKs bloat *every* secondary index.
- **Covering index** (a.k.a. index-only scan): the index contains *all* columns the query needs (via composite or `INCLUDE` columns), so the second hop is skipped entirely. The classic, cheap way to fix a hot read.
- **Heap-organized tables** (PostgreSQL): rows live in an unordered heap; *all* indexes (including the PK) are secondary and point at a physical tuple ID (`ctid`). Trade-off: no expensive clustered-index maintenance, but no free range scan on PK.

> Practical rule: design the **clustering key** for your dominant access pattern (range scans, locality), and add **covering** secondary indexes for hot read paths. Each extra index multiplies write amplification.

---

## 8. The page cache

Storage engines do **not** read the disk on every access — the OS (or the engine's own buffer pool) caches pages in RAM.

- **InnoDB buffer pool / PostgreSQL has both shared_buffers + OS page cache**: hot pages live in RAM; eviction is typically LRU-ish (InnoDB uses a midpoint-insertion LRU to resist scan pollution; PostgreSQL uses clock-sweep).
- **The OS page cache** is why log-structured *sequential* writes are so fast: writes coalesce in cache and flush in big sequential batches.
- **Direct I/O vs buffered I/O**: databases that manage their own cache (InnoDB) often use `O_DIRECT` to avoid double-caching. Those that lean on the OS (PostgreSQL historically) accept double buffering for simplicity.

The page cache is also why your **working set fitting in RAM** is the single biggest performance cliff in practice: once it doesn't, every miss is a device I/O and your latency distribution falls off the hockey stick (see [02](02_performance_queueing_theory.md)).

---

## 9. Working code — a tiny LSM-tree in Python

A self-contained, runnable LSM: memtable + WAL + SSTable flush + a real bloom filter on each SSTable + size-tiered compaction + the newest-wins, tombstone-aware read path. It is *correct and illustrative*, not tuned (real engines use mmap, block compression, sparse indexes, and skip lists).

```python
"""
mini_lsm.py — a from-scratch LSM-tree storage engine.

Demonstrates: WAL durability, in-memory sorted memtable, immutable sorted
SSTables with per-file bloom filters, tombstone deletes, newest-wins reads,
and size-tiered compaction. Run: python mini_lsm.py
"""
from __future__ import annotations
import hashlib, json, os, struct
from bisect import bisect_left
from typing import Optional

TOMBSTONE = "\x00__TOMBSTONE__\x00"   # marker value for deletes


# ---------- Bloom filter (Bloom, 1970) ----------
class BloomFilter:
    """Bit-array bloom filter with k hashes derived from a single digest
    (Kirsch-Mitzenmacher double hashing)."""
    def __init__(self, n_items: int, fp_rate: float = 0.01):
        n = max(1, n_items)
        # optimal m and k:  m = -n ln p / (ln2)^2 ,  k = (m/n) ln2
        import math
        self.m = max(8, int(-n * math.log(fp_rate) / (math.log(2) ** 2)))
        self.k = max(1, int(round((self.m / n) * math.log(2))))
        self.bits = bytearray((self.m + 7) // 8)

    def _hashes(self, key: str):
        d = hashlib.sha256(key.encode()).digest()
        h1 = int.from_bytes(d[:8], "big")
        h2 = int.from_bytes(d[8:16], "big") | 1   # ensure odd
        for i in range(self.k):
            yield (h1 + i * h2) % self.m

    def add(self, key: str):
        for bit in self._hashes(key):
            self.bits[bit >> 3] |= (1 << (bit & 7))

    def __contains__(self, key: str) -> bool:
        return all((self.bits[bit >> 3] >> (bit & 7)) & 1 for bit in self._hashes(key))

    def to_dict(self):
        return {"m": self.m, "k": self.k, "bits": self.bits.hex()}

    @classmethod
    def from_dict(cls, d):
        bf = cls.__new__(cls)
        bf.m, bf.k, bf.bits = d["m"], d["k"], bytearray.fromhex(d["bits"])
        return bf


# ---------- SSTable: immutable sorted file + bloom + index ----------
class SSTable:
    """On-disk sorted run. Layout: a JSON sidecar with the bloom filter and a
    sparse index, plus the sorted (key, value) entries. Simplified to one file."""
    def __init__(self, path: str):
        self.path = path
        with open(path) as f:
            self.meta = json.load(f)
        self.bloom = BloomFilter.from_dict(self.meta["bloom"])
        self.keys = self.meta["keys"]       # sorted list of keys
        self.vals = self.meta["vals"]

    @classmethod
    def write(cls, path: str, items: list[tuple[str, str]]) -> "SSTable":
        items = sorted(items)               # sorted run
        bloom = BloomFilter(len(items))
        for k, _ in items:
            bloom.add(k)
        meta = {
            "bloom": bloom.to_dict(),
            "keys": [k for k, _ in items],
            "vals": [v for _, v in items],
            "min": items[0][0] if items else None,
            "max": items[-1][0] if items else None,
        }
        with open(path, "w") as f:
            json.dump(meta, f)
        return cls(path)

    def get(self, key: str) -> Optional[str]:
        # Bloom gate: skip disk work entirely on a definite miss.
        if key not in self.bloom:
            return None
        i = bisect_left(self.keys, key)
        if i < len(self.keys) and self.keys[i] == key:
            return self.vals[i]            # may be TOMBSTONE
        return None

    def items(self):
        return zip(self.keys, self.vals)


# ---------- The LSM engine ----------
class LSMTree:
    def __init__(self, directory: str, memtable_limit: int = 4, tier_trigger: int = 3):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.memtable: dict[str, str] = {}          # logically sorted on flush
        self.memtable_limit = memtable_limit         # flush after this many writes
        self.tier_trigger = tier_trigger             # compact after this many SSTables
        self.sstables: list[SSTable] = []            # index 0 = NEWEST
        self.wal_path = os.path.join(directory, "wal.log")
        self._seq = 0
        self._recover()

    # ----- durability: WAL append before applying -----
    def _wal_append(self, op: str, key: str, value: str = ""):
        with open(self.wal_path, "a") as f:
            f.write(json.dumps({"op": op, "k": key, "v": value}) + "\n")
            f.flush()
            os.fsync(f.fileno())             # the durability point

    def _recover(self):
        # Load existing SSTables (newest first by sequence in filename).
        files = sorted(
            (fn for fn in os.listdir(self.dir) if fn.startswith("sst_")),
            reverse=True,
        )
        self.sstables = [SSTable(os.path.join(self.dir, fn)) for fn in files]
        if files:
            self._seq = max(int(fn.split("_")[1].split(".")[0]) for fn in files) + 1
        # Replay WAL to rebuild the in-memory memtable lost on crash.
        if os.path.exists(self.wal_path):
            with open(self.wal_path) as f:
                for line in f:
                    rec = json.loads(line)
                    if rec["op"] == "put":
                        self.memtable[rec["k"]] = rec["v"]
                    elif rec["op"] == "del":
                        self.memtable[rec["k"]] = TOMBSTONE

    # ----- write path -----
    def put(self, key: str, value: str):
        self._wal_append("put", key, value)
        self.memtable[key] = value
        if len(self.memtable) >= self.memtable_limit:
            self._flush()

    def delete(self, key: str):
        self._wal_append("del", key)
        self.memtable[key] = TOMBSTONE       # tombstone shadows on-disk versions
        if len(self.memtable) >= self.memtable_limit:
            self._flush()

    def _flush(self):
        if not self.memtable:
            return
        path = os.path.join(self.dir, f"sst_{self._seq:06d}.json")
        self._seq += 1
        sst = SSTable.write(path, list(self.memtable.items()))
        self.sstables.insert(0, sst)         # newest at front
        self.memtable.clear()
        # WAL is now redundant: its data is durable in the SSTable.
        open(self.wal_path, "w").close()
        if len(self.sstables) >= self.tier_trigger:
            self._compact()

    # ----- size-tiered compaction: merge all SSTables, newest wins, drop tombstones -----
    def _compact(self):
        merged: dict[str, str] = {}
        # iterate OLDEST -> NEWEST so newer writes overwrite older ones
        for sst in reversed(self.sstables):
            for k, v in sst.items():
                merged[k] = v
        # drop tombstones: a deleted key with no older shadow can disappear
        live = {k: v for k, v in merged.items() if v != TOMBSTONE}
        old_paths = [s.path for s in self.sstables]
        path = os.path.join(self.dir, f"sst_{self._seq:06d}.json")
        self._seq += 1
        new_sst = SSTable.write(path, list(live.items()))
        self.sstables = [new_sst]
        for p in old_paths:
            os.remove(p)

    # ----- read path: memtable, then SSTables newest -> oldest -----
    def get(self, key: str) -> Optional[str]:
        if key in self.memtable:
            v = self.memtable[key]
            return None if v == TOMBSTONE else v
        for sst in self.sstables:            # already newest-first
            v = sst.get(key)
            if v is not None:
                return None if v == TOMBSTONE else v
        return None                          # bloom filters skipped most files


# ---------- demo / self-test ----------
if __name__ == "__main__":
    import shutil, tempfile
    d = tempfile.mkdtemp(prefix="minilsm_")
    try:
        db = LSMTree(d, memtable_limit=4, tier_trigger=3)
        for i in range(20):
            db.put(f"key{i:02d}", f"v{i}")
        db.put("key05", "UPDATED")           # overwrite -> newer SSTable wins
        db.delete("key10")                   # tombstone

        assert db.get("key05") == "UPDATED", "newest version must win"
        assert db.get("key10") is None, "deleted key must read as absent"
        assert db.get("key17") == "v17"
        assert db.get("missing") is None     # bloom-gated negative lookup

        # Durability: simulate process restart by reopening the directory.
        db.put("key99", "survives")          # in memtable + WAL, not yet flushed
        db2 = LSMTree(d, memtable_limit=4, tier_trigger=3)   # "crash" + recover
        assert db2.get("key99") == "survives", "WAL must recover unflushed writes"
        assert db2.get("key05") == "UPDATED"
        print("all assertions passed; sstables on disk:",
              len([f for f in os.listdir(d) if f.startswith('sst_')]))
    finally:
        shutil.rmtree(d, ignore_errors=True)
```

**What each piece teaches:**
- `_wal_append` with `os.fsync` is *the* durability boundary — the write is durable the instant fsync returns, before the memtable flush.
- `delete` writes a **tombstone**, not a removal; the key is only physically gone once compaction sees no older shadow.
- `get` walks **newest → oldest** and the bloom filter (`key not in self.bloom`) short-circuits definite misses with zero disk reads — the single most important LSM read optimization.
- `_compact` is size-tiered: it merges all runs, keeps the newest value per key, and drops tombstones — reclaiming space/read amplification at the cost of the rewrite (write amplification).
- `_recover` shows the full crash story: reload immutable SSTables, then **replay the WAL** to reconstruct the volatile memtable.

---

## 10. Real systems

| System | Engine | Notes |
|---|---|---|
| **LevelDB / RocksDB** | LSM (leveled) | RocksDB is the reference modern LSM; embedded in MySQL (MyRocks), CockroachDB→Pebble, Kafka Streams, TiKV. Exposes every compaction knob. |
| **Apache Cassandra / ScyllaDB** | LSM (STCS default, LCS option) | Write-optimized wide-column; SSTables, memtables, commit log = WAL. |
| **Bigtable / HBase** | LSM | The lineage that started it (Bigtable, 2006). |
| **InnoDB (MySQL)** | B+tree, clustered on PK | Buffer pool, redo log = WAL, undo log for MVCC. |
| **PostgreSQL** | B-tree over heap | Heap tuples, WAL, MVCC inline + VACUUM, SSI for serializable. |
| **WiredTiger (MongoDB)** | both B-tree *and* LSM | Pick per collection. |
| **SQLite** | B-tree | Single-file; WAL mode optional. |

---

## 11. Trade-offs summary

- **WA vs RA vs SA is a triangle (RUM conjecture)** — choose two.
- **B-tree** = read-optimized, predictable tails, high write amplification, mature transactions. Default for OLTP relational.
- **LSM** = write-optimized, sequential I/O, tunable via compaction, but compaction causes background WA and occasional latency spikes. Default for high-ingest / write-heavy.
- **WAL is non-negotiable** for durability and it doubles your minimum write amplification; ARIES is the canonical recovery design.
- **MVCC** buys reader/writer concurrency at the cost of version garbage (VACUUM/undo) and only gives *snapshot* isolation unless you add SSI.
- **Index every hot read path** — but every index multiplies write amplification, so prefer covering indexes over many narrow ones.
- **Keep the working set in RAM.** The page-cache cliff dominates real-world latency more than the choice of engine.

## 12. Key takeaways

1. A storage engine is defined by *how it lays out bytes* and *how it survives a crash* — everything reduces to the **amplification triangle** and the **WAL**.
2. **B+trees**: high fanout → shallow → cheap predictable reads; in-place updates → high write amplification; the OLTP default.
3. **LSM-trees**: append + background compaction → cheap sequential writes; **bloom filters** make the multi-file read path viable; **compaction strategy (leveled vs size-tiered)** is *the* knob trading WA against RA/SA.
4. **Tombstones, newest-wins, and recovery-by-replay** are the three ideas that make append-only storage correct.
5. **MVCC** = multiple timestamped versions for lock-free reads; SI ≠ serializable (write skew); reclaiming dead versions (VACUUM/undo) is mandatory.
6. Choose the engine from the **write/read ratio and tail-latency requirements**, justified by amplification — not by popularity.

> Read next: [02 — Performance & Queueing Theory](02_performance_queueing_theory.md) for *why* the page-cache cliff exists, and [03 — Tail Latency](03_tail_latency.md) for why LSM compaction spikes matter so much at fan-out.
