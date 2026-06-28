# Apache Cassandra

## Overview

**Apache Cassandra** is a distributed, wide-column NoSQL database engineered for **linear scalability**, **high availability with no single point of failure**, and **multi-data-center / geo-distributed** operation. It is the canonical **hybrid** of two seminal designs: it borrows its **distribution and availability model from Amazon Dynamo** and its **storage engine and data model from Google Bigtable**.

- **Built by:** Avinash Lakshman (a co-author of the Dynamo paper) and Prashant Malik at **Facebook** (~2008, to power Inbox Search). Open-sourced 2008; an Apache top-level project since 2010.
- **Reference paper:** *"Cassandra — A Decentralized Structured Storage System,"* Lakshman & Malik, 2009 (LADIS / SIGOPS).
- **One-line thesis:** Take Dynamo's masterless ring (so any node can serve any request and there's no SPOF) and put a Bigtable LSM storage engine and column-family data model on top — then expose **tunable consistency** so each query chooses its own CAP point.

## The Problem It Solved

Facebook's Inbox Search needed to index and query billions of messages with:

1. **Very high write throughput** — every message, label, and interaction is a write. The system had to be **write-optimized**.
2. **Always-on availability** across multiple data centers; a node, rack, or whole DC failing should not take the system down.
3. **Linear, incremental scale on commodity hardware** — add nodes, get proportionally more capacity, with no manual resharding.

Dynamo solved availability and distribution but had a thin KV model and pluggable per-node storage. Bigtable solved the storage engine and rich column model but depended on a central master + Chubby + GFS (a CP design with coordination components). Cassandra fused the **best half of each**: Dynamo's symmetry and availability, Bigtable's write path and schema.

## Architecture

```
        Multi-DC token ring (masterless; every node is a peer)
   ┌──────────────────────────── DC1 ────────────────────────────┐
   │     N1 ────── N2 ────── N3 ────── N4 ────── N5  (vnodes)      │
   │      ▲ coordinator for this request                          │
   └──────┼───────────────────────────────────────────────────────┘
          │ gossip + replication (NetworkTopologyStrategy)
   ┌──────┼───────────────────────────── DC2 ────────────────────┐
   │     M1 ────── M2 ────── M3 ────── M4 ────── M5                │
   └───────────────────────────────────────────────────────────────┘

   Client → connects to ANY node → that node becomes the COORDINATOR
            coordinator hashes partition key → finds replicas via the
            partitioner + replication strategy → fans out R/W to replicas
            → enforces the requested CONSISTENCY LEVEL → replies

   Per-node storage engine (Bigtable-style LSM):
        write → commit log (durability) → memtable (RAM)
                                            │ flush when full
                                            ▼
                                   SSTables (immutable, on disk)
                                            │ compaction merges/GCs
                                            ▼
                                   fewer, larger SSTables
```

- **No master.** All nodes are equal peers; clients connect to any node (the **coordinator** for that request). This eliminates the SPOF that Bigtable's master/Chubby dependency represents.
- **Gossip** disseminates membership, token ownership, and node state, exactly as in Dynamo.
- **Partitioner** (default `Murmur3Partitioner`) hashes the partition key onto the ring. Modern Cassandra uses **virtual nodes (vnodes)** — each physical node owns many small token ranges — for smoother balancing and faster rebuild.
- **Snitch** tells Cassandra the network topology (rack/DC) so replicas are placed across failure domains.

## How It Works

### Replication strategy

Cassandra replicates each partition to **RF** (replication factor) nodes. With `NetworkTopologyStrategy`, RF is specified **per data center** (e.g., `{DC1: 3, DC2: 3}`), and replicas within a DC are placed on distinct racks where possible. There is no "primary" replica — all replicas are equal.

### Tunable consistency — CQL consistency levels

Consistency is chosen **per query** (not per cluster). For a read or write, you specify how many replicas must respond:

| Consistency Level | Meaning |
|---|---|
| `ONE` / `TWO` / `THREE` | That many replicas must ack. |
| `QUORUM` | Majority across **all** replicas (`floor(RF/2)+1`). |
| `LOCAL_QUORUM` | Majority within the **local** DC (avoids cross-DC latency; the most common production choice). |
| `EACH_QUORUM` | Quorum in **every** DC (writes only). |
| `LOCAL_ONE` | One replica in the local DC. |
| `ALL` | Every replica — strongest, lowest availability. |
| `ANY` | (writes) succeeds even if only a hinted handoff is stored — maximum write availability. |
| `SERIAL` / `LOCAL_SERIAL` | Used with lightweight transactions (Paxos). |

**Strong (read-your-writes) consistency** is obtained the Dynamo way: choose levels so **R + W > RF**. The classic recipe is `QUORUM` (or `LOCAL_QUORUM`) for both reads and writes with RF=3 → W=2, R=2, 2+2 > 3. This guarantees the read set and write set overlap on at least one up-to-date replica.

### The write path (write-optimized, append-only)

```
  1. Write arrives at coordinator → routed to RF replicas.
  2. On each replica:
       a. Append to the COMMIT LOG on disk  (durability / crash recovery)
       b. Update the MEMTABLE in memory      (sorted by clustering key)
     → write is acknowledged once CL replicas confirm (a)+(b).
  3. When a memtable fills (or on interval), it is FLUSHED to an
     immutable SSTABLE on disk; the corresponding commit-log segment
     can then be recycled.
  4. COMPACTION periodically merges SSTables: combines rows, applies
     the newest cell timestamps, drops tombstones past gc_grace, and
     reduces the number of files a read must consult.
```

Why this is fast: **writes never do a read-modify-write and never seek** — they are an in-memory insert plus a sequential append. Updates and deletes are just new timestamped writes; nothing is mutated in place. **Deletes are tombstones** (markers with a timestamp), reconciled and physically removed during compaction after `gc_grace_seconds`.

**Compaction strategies** (a major operational tuning lever):
- **STCS (SizeTieredCompactionStrategy):** merges similarly-sized SSTables; write-friendly, default; can cause read amplification and large temporary space spikes.
- **LCS (LeveledCompactionStrategy):** maintains levels of non-overlapping SSTables; bounds reads to few SSTables; great for read-heavy / update-heavy workloads at the cost of more write I/O.
- **TWCS (TimeWindowCompactionStrategy):** groups data by time window; ideal for time-series / TTL data where old windows are dropped whole.

### The read path & read repair

```
  Read at coordinator (CL = R):
   1. For each replica queried, the local read merges:
        memtable  +  relevant SSTables
      Acceleration structures: per-SSTable BLOOM FILTERS (skip SSTables
      that can't contain the key), partition index + key cache, row cache.
   2. Across the R replicas, the coordinator reconciles by CELL TIMESTAMP
      (last-write-wins at the cell level) → returns the newest value.
   3. READ REPAIR: if replicas disagree, the coordinator pushes the
      newest data back to the stale replicas (foreground if they were in
      the read set; background/probabilistic for the rest).
```

Anti-entropy beyond read repair:
- **Hinted handoff:** if a replica is down at write time, the coordinator stores a *hint* and replays it when the node returns (durability during transient failures; same idea as Dynamo).
- **Merkle-tree anti-entropy repair** (`nodetool repair`): replicas build Merkle trees over token ranges and exchange only the differing data — the explicit, operator-driven mechanism that guarantees eventual convergence. Running repair within `gc_grace_seconds` is essential to avoid **zombie data** (deleted rows resurrecting because a tombstone was compacted away on some replicas but the original write survived on others).

### Data model & query-first modeling

Cassandra's logical model (CQL) is a hybrid of Bigtable's wide rows and a relational-looking schema. The **primary key** has two parts:

```
   PRIMARY KEY ( (partition key) , clustering column(s) )
                  └ which node      └ sort order WITHIN a partition
```

- **Partition key** → hashed by the partitioner to decide which node(s) own the data. All rows with the same partition key live together on the same replicas. This is the unit of distribution and of single-request atomicity.
- **Clustering columns** → determine the **sort order of rows within a partition**, enabling efficient range scans and slices inside a partition.

The cardinal rule is **query-first / denormalize-for-reads**: you design tables around the queries you must serve, because Cassandra cannot do efficient cross-partition joins or arbitrary `WHERE` filtering. You typically write the same data into multiple tables, one per query pattern. **Avoid:** unbounded partitions (hot/huge partitions), high-cardinality `ALLOW FILTERING` scans, and queues-in-Cassandra (tombstone-heavy anti-pattern).

### Lightweight transactions (LWT) — Paxos

For the rare cases needing linearizable compare-and-set (e.g., "register this username only if it doesn't exist"), Cassandra offers **lightweight transactions** implemented with **Paxos** over the replicas:

```sql
INSERT INTO users (username, email) VALUES ('alice','a@x.com') IF NOT EXISTS;
UPDATE users SET email='b@x.com' WHERE username='alice' IF email='a@x.com';
```

These run at `SERIAL`/`LOCAL_SERIAL` consistency and use a 4-round Paxos (prepare/promise, read, propose/accept, commit). They are **much slower** than normal writes and are meant for isolated, low-frequency contention points — not general transactional workloads.

## Key Innovations

1. **The Dynamo + Bigtable synthesis** — masterless availability with a write-optimized LSM engine and a richer wide-column model, in one system.
2. **Per-query tunable consistency (CQL consistency levels)** — every read/write picks its own CAP trade-off, and `LOCAL_QUORUM` makes multi-DC strong-ish consistency practical.
3. **First-class multi-DC replication** via `NetworkTopologyStrategy` + snitches, designed in from the start.
4. **A genuinely write-optimized path** (commit log + memtable + immutable SSTables + compaction) that sustains very high ingest rates with no in-place updates.
5. **No single point of failure** — symmetric peers, gossip membership — operationally simpler to keep available than master-based designs.

## Data Model / APIs (CQL)

```sql
CREATE KEYSPACE social
  WITH replication = {'class':'NetworkTopologyStrategy','DC1':3,'DC2':3};

-- Query-first: "fetch a user's posts, newest first"
CREATE TABLE social.posts_by_user (
  user_id    uuid,
  posted_at  timestamp,
  post_id    timeuuid,
  body       text,
  PRIMARY KEY ( (user_id), posted_at, post_id )
) WITH CLUSTERING ORDER BY (posted_at DESC);

-- Efficient: single-partition slice, ordered by clustering key
SELECT * FROM social.posts_by_user
  WHERE user_id = 8f3...   -- partition key REQUIRED
    AND posted_at >= '2026-06-01'
  LIMIT 20;

-- Write with explicit consistency
INSERT INTO social.posts_by_user (user_id, posted_at, post_id, body)
  VALUES (8f3..., toTimestamp(now()), now(), 'hello')
  USING CONSISTENCY LOCAL_QUORUM;   -- (set per-statement / per-session)
```

Notes that reflect the engine: every column value carries a **write timestamp** (used for LWW reconciliation); collections, TTLs, counters, and (in Cassandra 5.0) **SAI secondary indexes** and **vector search** exist but should be used within the partition-aware model.

## Trade-offs & Limitations

| Aspect | Trade-off |
|---|---|
| **Consistency model** | Eventual by default; "strong" only via `R+W>RF`, and never the global serializability of an RDBMS. No referential integrity. |
| **No joins / limited queries** | Must denormalize and model per query; ad-hoc analytics need a separate system (Spark, search). `ALLOW FILTERING` is a foot-gun. |
| **Transactions** | Only single-partition atomicity for normal writes; cross-partition/multi-row transactions require slow Paxos LWTs. |
| **Tombstones & repair** | Deletes are deferred; misconfigured `gc_grace` + skipped repairs → zombie data or tombstone-scan performance cliffs. Operationally, **repair is mandatory hygiene**. |
| **Hotspots** | A bad partition-key choice (low cardinality, monotonic, or unbounded partitions) destroys the linear-scale promise. Data modeling expertise is required, not optional. |
| **Read amplification** | A single read may merge memtable + many SSTables; compaction strategy must match the workload or reads degrade. |
| **JVM / GC** | Historically a tuning burden (heap, GC pauses, off-heap memtables, compaction throughput). |

## Influence & Legacy

- Became one of the most widely-deployed NoSQL databases for **write-heavy, always-on, geo-distributed** workloads (time series, messaging, IoT, feeds, fraud, recommendations) at companies like Netflix, Apple, Instagram, and Uber.
- **ScyllaDB** reimplemented the Cassandra design in C++ with a shard-per-core (seastar) architecture for far lower latency and higher per-node throughput, while staying CQL/protocol compatible — a direct testament to the soundness of the model.
- **DataStax Enterprise**, **Amazon Keyspaces** (managed CQL), and **Azure Cosmos DB**'s Cassandra API all build on or emulate it.
- Cassandra 4.x/5.0 added incremental/zero-copy streaming, better repair, virtual tables, **storage-attached indexes (SAI)**, and **vector search**, and the project added **Accord**, a research-grade protocol aiming at fast, general, leaderless multi-partition transactions — addressing the historical transaction gap.

## Lessons for Architects

1. **Combine the right halves of proven designs.** Cassandra's biggest idea is architectural reuse: Dynamo's availability + Bigtable's storage engine. Originality isn't the goal; the right composition is.
2. **Let consistency be a per-operation decision.** Different queries in the same app have different needs. `LOCAL_QUORUM` for the write-then-read path, `ONE` for a metrics fire-and-forget — one cluster, many CAP points.
3. **Model for your reads, not your entities.** In a partitioned, join-less world, the query *is* the schema. Denormalization and write-amplification are deliberate, correct choices here — the opposite instinct from normalized RDBMS design.
4. **Write-optimized means append-only + background cleanup.** LSM (log + memtable + immutable SSTables + compaction) is the recurring pattern for high ingest; understand that you trade read amplification and a compaction budget for write speed.
5. **Eventual consistency is a contract you must operate.** Read repair, hinted handoff, and especially **scheduled `nodetool repair` within `gc_grace`** are not optional extras — they are how "eventually" actually happens. Convergence is an operational responsibility.
6. **Masterless buys availability but moves complexity to data modeling and ops.** There's no master to fail, but there's also no master to enforce global invariants — the burden shifts to partition-key design, repair discipline, and tombstone management.
