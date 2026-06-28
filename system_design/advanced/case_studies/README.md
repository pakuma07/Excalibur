# System Design Case Studies — Seminal Large-Scale Systems

A collection of rigorous, paper-faithful deep-dives into the systems that defined modern large-scale infrastructure. Written for **staff/principal engineers**: each study covers the problem, architecture, key mechanisms (data flow, metadata, consistency, replication, failure handling), innovations, trade-offs, legacy, and the durable lessons for architects.

> **Status legend:** ✅ available now · 🚧 planned (indexed for the full curriculum)

---

## The Case Studies

| # | System | One-liner | Status |
|---|--------|-----------|--------|
| 1 | [**GFS**](./gfs.md) | Google's append-mostly distributed file system on commodity disks; single master + chunkservers + 64 MB chunks. | ✅ |
| 2 | [**MapReduce**](./mapreduce.md) | A programming model (map/shuffle/reduce) + framework that auto-handles parallelism and fault tolerance via re-execution. | ✅ |
| 3 | [**Bigtable**](./bigtable.md) | A sparse, sorted, wide-column map over GFS+Chubby, with an LSM (SSTable/memtable) storage engine. | ✅ |
| 4 | [**Spanner**](./spanner.md) | Google's globally-distributed, externally-consistent SQL database using **TrueTime** (GPS + atomic clocks). | 🚧 |
| 5 | [**Dynamo**](./dynamo.md) | Amazon's always-writable, eventually-consistent, leaderless KV store: consistent hashing, vector clocks, quorums. | 🚧 |
| 6 | [**Kafka**](./kafka.md) | A distributed, partitioned, replicated **commit log** repurposed as a real-time streaming platform. | 🚧 |
| 7 | [**Cassandra**](./cassandra.md) | Bigtable's data model fused with Dynamo's leaderless distribution — a decentralized wide-column store. | 🚧 |
| 8 | [**ZooKeeper & Chubby**](./zookeeper_chubby.md) | Strongly-consistent coordination/lock services (the "kernel" other distributed systems are built on). | 🚧 |
| 9 | [**Dapper**](./dapper.md) | Google's always-on distributed tracing system; the ancestor of OpenTelemetry/Zipkin/Jaeger. | 🚧 |
| 10 | [**S3**](./s3.md) | Amazon's planet-scale object store: flat keyspace, 11-nines durability, the move to strong consistency (2020). | ✅ |
| 11 | [**Borg / Kubernetes**](./borg_kubernetes.md) | Google's cluster manager (Borg) and its open-source successor's lineage (Kubernetes). | 🚧 |

---

## Concept → System Map

Use this to study a *concept* across the systems that best illustrate it.

| Concept | Best illustrated by |
|---|---|
| **Single-master / centralized metadata (off the data path)** | GFS, Bigtable, MapReduce, Borg |
| **Sharding / partitioning** | Bigtable (tablets), Dynamo (consistent hashing), Kafka (partitions), S3 (prefix partitions), Spanner (splits) |
| **Replication & quorums** | Dynamo (sloppy quorums, R+W>N), Kafka (ISR), Spanner (Paxos groups), GFS (3× chunks) |
| **Consensus / coordination** | ZooKeeper (ZAB), Chubby (Paxos), Spanner (Paxos), Kafka (controller / KRaft) |
| **Consistency models** | Spanner (external/linearizable), Dynamo (eventual), S3 (eventual → strong), GFS (relaxed), Bigtable (single-row atomic) |
| **LSM-tree storage engine** | Bigtable (SSTable/memtable/compaction), Cassandra |
| **Log-structured / commit log as a primitive** | Kafka (the log *is* the system), Bigtable (WAL), MapReduce (re-execution from durable input) |
| **Fault tolerance via re-execution / idempotency** | MapReduce (deterministic re-run + atomic rename), Spark lineage |
| **The tail / stragglers** | MapReduce (backup tasks), Dapper (latency analysis) |
| **Clocks & time** | Spanner (TrueTime), Dynamo/Cassandra (vector clocks / last-write-wins), Kafka (offsets as logical time) |
| **Data locality ("move compute to data")** | MapReduce + GFS |
| **Durability via erasure coding / multi-AZ** | S3, Colossus (GFS successor) |
| **Wide-column / NoSQL data model** | Bigtable, Cassandra |
| **Cluster scheduling & bin-packing** | Borg / Kubernetes |
| **Distributed tracing / observability** | Dapper |
| **Leaderless / AP design (CAP)** | Dynamo, Cassandra |
| **Leader-based / CP design (CAP)** | Spanner, ZooKeeper, Chubby, Kafka partitions |

---

## Suggested Reading Order

The curriculum builds from storage primitives up to global databases and cluster management. Each tier assumes the previous one.

```
Tier 1 — Storage & batch foundations (the "Google stack")
   GFS  ─►  MapReduce  ─►  Bigtable
   (Why: Bigtable is built ON GFS; MapReduce reads/writes GFS. Read in this order
    to see the layers compose.)

Tier 2 — Coordination (the kernel everything leans on)
   ZooKeeper & Chubby
   (Why: Bigtable already used Chubby; understanding consensus/locks here unlocks
    Spanner, Kafka, Cassandra failure handling, and Borg.)

Tier 3 — The two great philosophies of distributed data
   Dynamo (AP, eventual, leaderless)  ║  Spanner (CP, strong, leader+Paxos+TrueTime)
   (Why: read as a contrasting pair — the CAP spectrum in two real systems.)

Tier 4 — Descendants & specializations
   Cassandra (= Bigtable model + Dynamo distribution)
   Kafka     (the commit log as a platform)
   S3        (object storage; eventual → strong, durability engineering)

Tier 5 — Operating the fleet & seeing inside it
   Borg / Kubernetes (scheduling)   Dapper (tracing)
```

**Fast path (the 5 most foundational):** GFS → MapReduce → Bigtable → Dynamo → Spanner.

---

## Original Papers & Primary Sources

| System | Citation |
|---|---|
| **GFS** | Ghemawat, Gobioff, Leung. *The Google File System.* SOSP 2003. |
| **MapReduce** | Dean, Ghemawat. *MapReduce: Simplified Data Processing on Large Clusters.* OSDI 2004. |
| **Bigtable** | Chang et al. *Bigtable: A Distributed Storage System for Structured Data.* OSDI 2006. |
| **Chubby** | Burrows. *The Chubby Lock Service for Loosely-Coupled Distributed Systems.* OSDI 2006. |
| **Dynamo** | DeCandia et al. *Dynamo: Amazon's Highly Available Key-value Store.* SOSP 2007. |
| **ZooKeeper** | Hunt, Konar, Junqueira, Reed. *ZooKeeper: Wait-free Coordination for Internet-scale Systems.* USENIX ATC 2010. |
| **Dapper** | Sigelman et al. *Dapper, a Large-Scale Distributed Systems Tracing Infrastructure.* Google Technical Report, 2010. |
| **Kafka** | Kreps, Narkhede, Rao. *Kafka: a Distributed Messaging System for Log Processing.* NetDB 2011. |
| **Cassandra** | Lakshman, Malik. *Cassandra — A Decentralized Structured Storage System.* LADIS 2009 / SIGOPS 2010. |
| **Spanner** | Corbett et al. *Spanner: Google's Globally-Distributed Database.* OSDI 2012. |
| **Borg** | Verma et al. *Large-scale cluster management at Google with Borg.* EuroSys 2015. |
| **Kubernetes / Borg lineage** | Burns et al. *Borg, Omega, and Kubernetes.* ACM Queue, 2016. |
| **S3** | No single paper. AWS docs, *Amazon Builders' Library*, AWS re:Invent talks, and the Dec 2020 strong-consistency announcement. Conceptual ancestor: the *Dynamo* paper above. |

---

*Each file follows the same structure: Overview · The Problem It Solved · Architecture (diagram) · How It Works · Key Innovations · Data Model / APIs · Trade-offs & Limitations · Influence & Legacy · Lessons for Architects.*
