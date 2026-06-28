# The Google File System (GFS)

> *"The Google File System"* — Sanjay Ghemawat, Howard Gobioff, Shun-Tak Leung. SOSP 2003.

---

## Overview

The Google File System (GFS) is a **scalable distributed file system for large distributed data-intensive applications**, built by Google around 2000-2003 to run on cheap, commodity hardware. It was the storage substrate underneath early Google: the web crawl, the indexing pipeline, and later MapReduce and Bigtable.

GFS is *not* a POSIX filesystem. It was a purpose-built system whose design was driven entirely by the observed workload of Google's batch-processing applications, and it deliberately broke with decades of filesystem orthodoxy in order to optimize for that workload at scale.

| | |
|---|---|
| **Built by** | Google |
| **Seminal paper** | SOSP 2003 |
| **Successor** | Colossus (next-gen GFS, ~2010, never formally published) |
| **Open-source descendant** | Apache HDFS |

---

## The Problem It Solved

By 2000, Google was running thousands of machines to crawl and index the web. Existing distributed filesystems (NFS, AFS, Lustre, etc.) did not fit. Google re-derived its design from four observations about its actual environment:

1. **Component failures are the norm, not the exception.** With thousands of commodity disks and machines, *something* is always broken. Fault tolerance, monitoring, and automatic recovery must be built into the system rather than bolted on.
2. **Files are huge by traditional standards.** Multi-GB files are the common case (e.g., the entire web crawl). Managing billions of KB-sized files is the wrong design target. This justified a **large chunk/block size**.
3. **Most writes are appends, not overwrites.** Files are mutated by appending; random writes within a file are practically nonexistent. Once written, files are mostly read sequentially. This made **append the optimized fast path** and random writes a slow, rare case.
4. **Co-designing the application and the filesystem wins.** Because Google controlled both the applications *and* the filesystem, it could relax the consistency model (and expose append semantics) in ways a general-purpose filesystem never could, simplifying the whole system.

**Constraints/goals:** high *sustained bandwidth* matters more than low *latency*; the system must scale to hundreds of TB across thousands of disks and serve hundreds of clients concurrently.

---

## Architecture

A GFS cluster is a **single master** plus **many chunkservers**, accessed by **many clients**. Files are split into fixed-size **chunks** (default **64 MB**), each identified by a globally unique, immutable 64-bit **chunk handle**. Each chunk is replicated (default **3×**) across chunkservers.

```mermaid
flowchart TB
    subgraph Client
      C[GFS Client library]
    end

    M[("GFS Master\n(file namespace, chunk→location map,\nleases, GC, rebalancing)")]
    SM[Shadow Masters\n(read-only replicas)]

    subgraph Chunkservers
      CS1[Chunkserver 1\nchunks as Linux files]
      CS2[Chunkserver 2]
      CS3[Chunkserver 3]
      CSn[Chunkserver N]
    end

    OPLOG[(Operation Log\n+ periodic checkpoints\nreplicated to remote machines)]

    C -- "1. (filename, chunk index)\nmetadata only" --> M
    M -- "2. chunk handle +\nreplica locations" --> C
    C -- "3. bulk DATA read/write\n(no metadata path)" --> CS1
    C --> CS2
    C --> CS3
    M -. "HeartBeat:\nchunk reports, instructions" .-> CS1
    M -. heartbeat .-> CS2
    M -. heartbeat .-> CS3
    M --> OPLOG
    M -. replays log .-> SM
```

**Key structural decision: the data path never goes through the master.** The master serves only *metadata* (which chunkservers hold which chunks); all bulk data flows directly between clients and chunkservers. This keeps the single master from becoming a throughput bottleneck.

---

## How It Works

### Metadata and the single master

The master maintains three kinds of metadata, **all in memory** for speed:

1. The **file and chunk namespaces** (directory tree, file→chunk mapping).
2. The **mapping from files to chunks**.
3. The **locations of each chunk's replicas**.

The first two are persisted to an **operation log** (a write-ahead log) that is replicated to multiple remote machines; the master replays it on restart and periodically takes a compact B-tree-like **checkpoint** so recovery doesn't replay the entire log.

The third — **chunk locations — is NOT persisted.** The master does not store which chunkserver holds which chunk on disk. Instead, it asks the chunkservers at startup and keeps itself updated via periodic **HeartBeat** messages. The reasoning: the chunkservers are the ground truth (a chunkserver ultimately decides what chunks it has), and trying to keep a persistent, consistent copy on the master would be fragile as machines join, leave, fail, restart, and get renamed.

Because metadata lives in RAM, the master is fast — but the amount of metadata is bounded by master memory. The 64 MB chunk size is partly what makes this feasible: fewer, larger chunks means less metadata per byte stored. (Roughly 64 bytes of metadata per 64 MB chunk.)

### Why 64 MB chunks?

- **Reduces client-master interaction** — one metadata lookup serves a large region of a file; for sequential reads a client can cache all chunk locations for a multi-GB file cheaply.
- **Reduces master metadata size**, enabling the in-memory design.
- **Enables persistent TCP connections** to a chunkserver for many operations on the same large chunk.

The downside: small files become **hot spots** (a 1-chunk file read by many clients hammers 3 chunkservers). GFS mitigated this with higher replication for hot small files and client batching.

### Reads (data flow)

1. Client translates `(filename, byte offset)` → `(filename, chunk index)` using the fixed chunk size.
2. Client asks the **master**; gets back the **chunk handle** and the **list of replica locations**. Client caches this.
3. Client sends the read request **directly to the nearest replica** (a chunkserver), reading the byte range.

### Writes & the lease / primary mechanism

To keep mutations consistent across replicas *without* routing data through the master, GFS uses **leases**:

- The master grants a **lease** (default 60s, extendable via heartbeats) for a chunk to one replica, making it the **primary**.
- The **primary serializes all mutations** to that chunk and assigns them a **mutation order**; secondaries apply mutations in that same order. This gives a consistent global ordering of writes per chunk while keeping the master off the data path.

**Decoupling of control flow and data flow** is a signature idea. The write protocol:

```
1. Client asks master for primary + secondaries for the chunk (caches it).
2. Client PUSHES the data to ALL replicas — but not necessarily in any
   particular order, and the data is buffered in an internal LRU cache
   on each chunkserver (not yet written). Data is pipelined along a
   CHAIN of chunkservers chosen by network topology (each forwards to
   the *nearest* next replica) to fully use each machine's outbound
   bandwidth.
3. Once all replicas ACK receipt, client sends a WRITE request to the
   PRIMARY. The primary assigns a serial number / order to this and any
   concurrent mutations, and applies them locally in that order.
4. Primary forwards the write order to all SECONDARIES; each applies the
   mutations in the same serial order.
5. Secondaries ACK to primary; primary replies to client. Any replica
   error is reported to the client, which retries.
```

Separating "push the bytes" (step 2, bandwidth-bound, topology-aware pipelining) from "commit in order" (steps 3-4, latency-bound, serialized by primary) lets GFS use the full network bisection bandwidth while still getting a consistent order.

### Atomic Record Append — the workload-defining operation

Most GFS clients append concurrently to the same file (e.g., many producers writing to one log, or a merged-results file). GFS provides **`record append`**, where the *client specifies only the data*, and **GFS chooses the offset** and returns it.

- GFS guarantees the record is appended **atomically at least once** as a contiguous run of bytes, at an offset of GFS's choosing.
- If a replica fails mid-append, the client **retries**, which can leave **duplicates** and **padding** between records.

This is the crux of GFS's relaxed model: it trades exact-once, byte-identical replicas for a much simpler, lock-free, high-concurrency append path. Applications cope by writing **self-identifying, checksummed records** and filtering duplicates with record IDs on read — pushing a little complexity into the (Google-controlled) application in exchange for enormous simplicity in the filesystem.

### The relaxed consistency model

GFS classifies file region states after a mutation:

| State | Meaning |
|---|---|
| **Consistent** | All clients see the same data, regardless of which replica they read. |
| **Defined** | Consistent **and** clients see the mutation in its entirety (they can tell what each write wrote). |
| **Inconsistent** | Different replicas may show different data (a failed mutation). |

| Operation | Result |
|---|---|
| Serial success (write) | **Defined** |
| Concurrent successes (write) | **Consistent but undefined** (interleaved fragments) |
| **Record append** (success) | **Defined interspersed with inconsistent** (padding/dup regions between defined records) |
| Failure | **Inconsistent** |

GFS does *not* give you POSIX semantics. It gives you "your appended record will appear, intact, at least once, somewhere." Applications were designed around exactly that.

### Failure handling

- **Chunkserver failure:** detected via missed heartbeats. The master notices a chunk is under-replicated and triggers **re-replication**, prioritizing chunks far below their replication goal. It also **rebalances** replicas for disk/load.
- **Stale replicas:** each chunk has a **version number**, bumped on each new lease. A chunkserver that missed mutations (e.g., was down) has an old version; the master detects this on heartbeat and treats the replica as stale, refusing to hand it to clients and garbage-collecting it.
- **Data integrity:** each chunkserver verifies its own data with **32-bit checksums per 64 KB block**, independently of other replicas (it can't trust that replicas are byte-identical, by design). Corruption is caught on read and repaired from a good replica.
- **Garbage collection:** deletes are lazy. A deleted file is renamed to a hidden name; the master reclaims it during regular namespace scans and tells chunkservers (via heartbeat) which orphaned chunks to delete. Lazy GC is simpler and more robust than eager deletion in a system where messages get lost and machines come and go.

### Single master: bottleneck or feature?

A single master radically **simplifies** the design — global knowledge enables smart chunk placement and replication decisions. The risks (throughput bottleneck, SPOF) are mitigated by:

- Keeping the master **off the data path** (metadata only).
- Clients **caching** metadata so they rarely re-contact the master.
- **Operation log replication + checkpoints** for fast recovery; a new master can be started from the replicated state.
- **Shadow masters**: read-only replicas that lag slightly and can serve **read-only** metadata access, improving read availability when the primary is down (they may return slightly stale data).

---

## Key Innovations / What Made It Special

1. **Workload co-design.** GFS is the canonical example of designing the storage system *around* a known application workload rather than chasing generality.
2. **Relaxed consistency + atomic record append.** Sacrificing POSIX semantics for a simple, lock-free, high-concurrency append path.
3. **Decoupling control flow from data flow** with leases and topology-aware data pipelining.
4. **Single in-memory master** with chunk locations kept non-authoritative (rebuilt from chunkservers).
5. **Treating failure as routine**, with checksums, versioning, lazy GC, and automatic re-replication as first-class mechanisms.

---

## Data Model / APIs

GFS exposes a familiar-looking but **non-POSIX** interface (no kernel VFS integration; a user-space library):

```
create, delete, open, close, read, write    // familiar
snapshot       // copy-on-write copy of a file or directory tree, cheap
record append  // atomic, GFS-chosen offset, at-least-once
```

`snapshot` uses copy-on-write at chunk granularity: the master revokes outstanding leases (forcing the next write to go through it), duplicates metadata, and lets chunkservers copy the chunk locally only on the next mutation.

---

## Trade-offs & Limitations

| Trade-off | Consequence |
|---|---|
| Single master | Simple & globally optimal placement, **but** metadata throughput and total file count limited by master RAM; latency of the metadata hop. |
| 64 MB chunks | Cheap metadata & great for big sequential files, **but** small files create hot spots and waste. |
| Relaxed consistency | Lock-free, high-throughput appends, **but** apps must tolerate duplicates/padding and do their own dedup/checksumming. |
| Optimized for throughput | Great for batch (MapReduce), **bad for latency-sensitive** and for many small files. |
| Append-centric | Random writes are slow and rare-path. |

By ~2010 these limits (especially the **single-master file-count ceiling** and the bad fit for low-latency, small-file, interactive workloads behind products like Gmail) drove Google to **Colossus**, which sharded the metadata layer (a distributed, scalable metadata service backed by Bigtable/Spanner) and used **Reed-Solomon erasure coding** instead of plain 3× replication to cut storage cost.

---

## Influence & Legacy

- **Apache HDFS** is a near-direct open-source reimplementation: GFS master → **NameNode**, chunkservers → **DataNodes**, chunks → **blocks** (128 MB default), 3× replication, single-namenode design (later HA via standby NameNode + journal nodes — echoing shadow masters). HDFS became the storage layer of the entire Hadoop ecosystem.
- It validated the **"big block, append-mostly, throughput-over-latency, failure-as-normal"** design pattern that underlies essentially every modern data-lake / big-data storage system.
- The **decouple-metadata-from-data** pattern recurs in object stores and many distributed filesystems.
- Internally it spawned **Colossus** and was the foundation MapReduce and Bigtable were built on.

---

## Lessons for Architects

1. **Know your workload, then break orthodoxy deliberately.** GFS's biggest wins came from rejecting POSIX, small blocks, and strong consistency *because the workload didn't need them*. Generality has a cost; pay it only when required.
2. **Push complexity to where it's cheapest.** Moving dedup/checksum logic into applications let the filesystem stay dramatically simpler. With co-designed systems, the "right" layer for a guarantee isn't always the storage layer.
3. **A single coordinator is fine if it's off the hot path.** Centralized metadata gives you global decisions and simplicity; just make sure bulk traffic bypasses it and clients cache aggressively.
4. **Make failure a normal code path, not an exception.** Heartbeats, versioning, checksums, lazy GC, and auto-re-replication should be designed in from day one at scale.
5. **Separate control flow from data flow** to independently optimize for ordering/consistency vs. raw bandwidth.
6. **Designs have a lifespan.** GFS's assumptions (huge files, batch, throughput) eventually mismatched interactive products — and Google rebuilt it. Re-examine founding assumptions as the business changes.
