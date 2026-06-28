# Google Spanner (2012)

## Overview

**Spanner** is Google's globally-distributed, synchronously-replicated relational database. It was the first system to provide **externally-consistent** (linearizable) distributed transactions at global scale, with SQL semantics and automatic sharding across data centers and continents.

- **Built by:** Google (originally to replace sharded MySQL behind the AdWords F1 backend).
- **Seminal paper:** *"Spanner: Google's Globally-Distributed Database"* — Corbett, Dean, Epstein, et al., **OSDI 2012**. (A follow-up, *"Spanner: Becoming a SQL System,"* SIGMOD 2017, documents the maturation of its query engine.)
- **Public form:** Cloud Spanner (GCP, 2017).
- **One-line thesis:** With tightly-bounded clock uncertainty (**TrueTime**), you can assign globally-meaningful timestamps to transactions and thereby get external consistency across the planet — choosing **C and P** in CAP terms.

## The Problem It Solved

Google ran AdWords on a manually-sharded MySQL deployment. Resharding it once took **over two years**. The team needed:

1. **Horizontal scale** like a NoSQL/Bigtable system (Bigtable was already in production but only offered single-row atomicity and no cross-row transactions or schema).
2. **Strong transactional semantics** like a relational database — multi-row, multi-table ACID transactions, secondary indexes, and SQL.
3. **Global distribution** with synchronous replication for survivability across data-center and regional failures, plus geo-locality of data.
4. **Consistency that application developers can reason about.** Eventual consistency (à la Dynamo/Megastore-without-it) had repeatedly burned application teams; Google's stance was that it is far cheaper to have engineers deal with performance problems caused by over-strong consistency than to have them reason about the absence of consistency.

No existing system gave all four. Bigtable lacked transactions and schema; Megastore had schema and limited transactions but poor write throughput. Spanner's goal: the scalability and availability of Bigtable plus the semantics of a SQL RDBMS.

## Architecture

A Spanner deployment is a **universe**. A universe is divided into **zones** (the unit of physical isolation and administrative deployment; roughly a data center, and a data center can hold multiple zones).

```
                         ┌──────────────────────────────────────────┐
                         │              Universe                      │
                         │   universemaster · placement driver        │
                         └──────────────────────────────────────────┘
                              │              │               │
                  ┌───────────▼───┐  ┌───────▼───────┐  ┌────▼──────────┐
                  │    Zone A      │  │    Zone B     │  │    Zone C     │
                  │  (US-east)     │  │  (US-central) │  │  (EU-west)    │
                  │                │  │               │  │               │
                  │  zonemaster    │  │  zonemaster   │  │  zonemaster   │
                  │  location proxy│  │  ...          │  │  ...          │
                  │                │  │               │  │               │
                  │  ┌──────────┐  │  │  ┌──────────┐ │  │  ┌──────────┐ │
                  │  │spanserver│  │  │  │spanserver│ │  │  │spanserver│ │
                  │  │spanserver│  │  │  │spanserver│ │  │  │spanserver│ │
                  │  │   ...    │  │  │  │   ...    │ │  │  │   ...    │ │
                  │  └──────────┘  │  │  └──────────┘ │  │  └──────────┘ │
                  └────────────────┘  └───────────────┘  └───────────────┘

  A single Paxos group ("split"/tablet) is replicated ACROSS zones:

        replica (leader)        replica              replica
        Zone A ─────────── Paxos ─── Zone B ─────── Paxos ─── Zone C
           │                          │                        │
        Colossus (GFS2)            Colossus                 Colossus
```

### Inside a spanserver

```
   spanserver
   ├── manages 100–1000 "tablets" (each ≈ a Bigtable tablet: bag of (key,timestamp)->value)
   ├── per tablet: a Paxos state machine
   │      ├── replicated WAL + replicated state, stored in Colossus
   │      ├── long-lived leader (time-based leader leases, ~10s)
   │      └── writes initiate Paxos at the leader; reads served from any up-to-date replica
   ├── leader replica also runs:
   │      ├── a lock table  → 2-phase locking for concurrency control
   │      └── a transaction manager → supports 2-phase commit ACROSS Paxos groups
   └── data is bucketed into "directories" (contiguous key ranges sharing a prefix);
          directories are the unit of data placement / movement between Paxos groups
```

## How It Works

### TrueTime — the core enabler

TrueTime is an API that exposes clock uncertainty explicitly. Instead of returning a single timestamp, `TT.now()` returns an **interval** `[earliest, latest]` and guarantees the true absolute time lies within it.

| Method | Returns | Guarantee |
|---|---|---|
| `TT.now()` | `TTinterval: [earliest, latest]` | true time `t_abs` ∈ `[earliest, latest]` |
| `TT.after(t)` | bool | true if `t` has definitely passed |
| `TT.before(t)` | bool | true if `t` has definitely not yet arrived |

Implementation: each datacenter has **time master** machines, backed by **GPS receivers** and **atomic clocks** (deliberately using two independent failure modes — GPS can fail from antenna/receiver issues or spoofing; atomic clocks fail by drifting). Every machine runs a **timeslave daemon** that polls a variety of masters and applies Marzullo's algorithm to reject liars and compute the interval.

The half-width of the interval is **ε** (epsilon). Between polls ε grows with the **worst-case local clock drift** (Google assumed ~200 µs/s). In production, ε is sawtooth-shaped, typically **1–7 ms**, averaging around 4 ms (mean ε ~4 ms in the paper).

```
   ε
   |    /|    /|    /|       sawtooth: jumps down at each poll (every ~30s),
   |   / |   / |   / |       grows linearly between polls at the drift rate
   |__/__|__/__|__/__|___ t
```

### External consistency via commit-wait

**External consistency** (a.k.a. linearizability for transactions): if transaction T2 starts to commit after T1 finishes committing, then T2's timestamp > T1's timestamp. The global commit order matches real-time order.

Spanner achieves this with two rules at commit time:

1. **Start rule:** the commit timestamp `s` assigned to a transaction is `>= TT.now().latest` (taken after the transaction acquires all locks / at the time the coordinator receives its commit request).
2. **Commit-wait rule:** the coordinator leader **waits until `TT.after(s)` is true** before releasing locks and acknowledging the commit. That is, it sleeps until `s` is guaranteed to be in the past everywhere.

```
   acquire locks ──► pick s = TT.now().latest ──► [ COMMIT WAIT ] ──► release locks, ack client
                                                  wait until TT.after(s)
                                                  (≈ 2ε on average)
```

The commit-wait duration is on the order of **2ε** (the width of the uncertainty interval). This is why shrinking ε matters: commit latency and the throughput of conflicting transactions are directly tied to clock uncertainty. The whole architecture is a bet that you can keep ε in the single-digit-millisecond range cheaply with GPS + atomic clocks.

### Concurrency control

Spanner combines several techniques depending on transaction type:

| Operation | Concurrency control | Timestamp | Locks? |
|---|---|---|---|
| Read-write transaction | Pessimistic 2-phase locking (2PL) + (2PC if multi-group) | commit timestamp via TrueTime | yes (write locks) |
| Read-only transaction | Lock-free MVCC snapshot read | a timestamp `s_read` chosen by the system | no |
| Snapshot read | Lock-free MVCC at client-supplied timestamp / bound | client-supplied | no |

- **Read-write transactions** use wound-wait 2PL at the leader's lock table. Writes are buffered at the client until commit (so reads in the same txn don't see them through Paxos).
- **Read-only transactions** are a major win: they are **lock-free and non-blocking**. The system assigns a read timestamp `s_read` (often `TT.now().latest`) and serves a consistent snapshot. They can be served by **any replica that is sufficiently up-to-date**, including followers — checked via each replica's `t_safe` (the timestamp below which it can safely serve reads). No round-trip to the leader is needed for the data itself.
- **Multi-version storage:** every value is timestamped with its transaction's commit timestamp; old versions are garbage-collected based on a configurable GC horizon.

### Distributed transactions (2PC over Paxos groups)

A transaction touching multiple directories spans multiple Paxos groups. Spanner layers **two-phase commit across the Paxos groups**, where each participant is itself a Paxos-replicated group:

```
   Client
     │ choose one participant leader as COORDINATOR; others are participants
     ▼
  Coordinator Leader ──prepare──► Participant Leader(s)
     │                              each: acquire locks, log "prepared" via Paxos,
     │                              pick prepare timestamp, reply
     ▼
  Coordinator picks commit ts s (>= all prepare ts, >= TT.now().latest, >= its own prev)
     │ logs commit record via Paxos
     │ COMMIT WAIT until TT.after(s)
     ▼
  ──commit(s)──► Participant Leaders apply at timestamp s, release locks
```

Because each "node" in 2PC is a Paxos group rather than a single machine, a participant failure does not block the protocol — a new leader is elected and recovers the prepared state from the replicated log. This solves classic 2PC's "blocking on coordinator/participant crash" problem at the availability layer.

### Paxos groups, leases, and replication

- Each tablet/split is a **Paxos group** with replicas in different zones (commonly 3 or 5 for quorum).
- Spanner uses **long-lived, time-based leader leases** (default ~10 s, renewable) so a leader can serve reads locally without re-running Paxos and can batch writes. Lease intervals are made disjoint using TrueTime so two leaders never overlap.
- The WAL is itself stored in **Colossus** (the successor to GFS), so each replica is durable even before counting cross-zone replication.

## Key Innovations

1. **TrueTime:** turning clock uncertainty from an invisible hazard into a *bounded, queryable API*. This is the single most influential idea — externalizing ε and waiting it out.
2. **Commit-wait for external consistency** without a global coordinator or global clock — just bounded uncertainty plus a deliberate wait.
3. **Lock-free, globally-consistent snapshot reads** served from any sufficiently-current replica via MVCC + `t_safe`.
4. **2PC layered on Paxos groups** — combining strong consistency *within* a shard (Paxos) and *across* shards (2PC) while remaining non-blocking.
5. **Directory-based data placement** decoupled from tablets, enabling fine-grained geo-placement and movement of related data.
6. **A real SQL system at global scale** with schema, secondary indexes, and (later) a full query optimizer/executor.

## Data Model / APIs

Spanner's schema is **relational with interleaving**: tables can be physically co-located via parent-child relationships, so related rows live in the same directory/Paxos group and join/transact cheaply.

```sql
CREATE TABLE Singers (
  SingerId   INT64 NOT NULL,
  FirstName  STRING(1024),
  LastName   STRING(1024),
  SingerInfo BYTES(MAX),
) PRIMARY KEY (SingerId);

CREATE TABLE Albums (
  SingerId   INT64 NOT NULL,
  AlbumId    INT64 NOT NULL,
  AlbumTitle STRING(MAX),
) PRIMARY KEY (SingerId, AlbumId),
  INTERLEAVE IN PARENT Singers ON DELETE CASCADE;
  -- Albums rows are physically co-located with their parent Singer row.
```

Read-write transaction (client-buffered writes, committed atomically):

```sql
BEGIN;                                  -- read-write txn, acquires locks at leaders
SELECT Balance FROM Accounts WHERE Id = 1;   -- read
UPDATE Accounts SET Balance = Balance - 100 WHERE Id = 1;
UPDATE Accounts SET Balance = Balance + 100 WHERE Id = 2;
COMMIT;                                  -- 2PC if rows are in different groups; commit-wait applies
```

Bounded-staleness / exact-staleness snapshot read (cheap, lock-free, can hit a nearby replica):

```sql
-- Read a globally consistent snapshot as of up to 15s ago; can be served locally.
SELECT * FROM Albums
  WHERE SingerId = 1
  -- Cloud Spanner: read options { exact_staleness: 15s }  or  { strong: true }
```

API surface (Cloud Spanner): `Read`, `BufferedMutations`, single-use vs. multi-use read-only transactions, partitioned DML, and standard SQL (GoogleSQL dialect; PostgreSQL dialect added later).

## Trade-offs & Limitations

| Aspect | Trade-off |
|---|---|
| **CAP** | Spanner is **CP**: under a partition it preserves consistency and sacrifices availability for affected groups. In practice availability is very high (~5 nines) because Google's private network rarely partitions, but the theoretical choice is C over A. |
| **Write latency** | Cross-region writes pay Paxos quorum RTT **plus** commit-wait (~2ε). Single-region/single-group writes are fast; globe-spanning read-write transactions are inherently slower. |
| **ε dependence** | The whole model leans on small, bounded clock uncertainty. It demands specialized infrastructure (GPS, atomic clocks, controlled network) that most companies don't have. If ε blows up, latency and throughput degrade and the system must stop assigning timestamps (correctness is preserved, availability is not). |
| **Contention** | Hot rows / hot key ranges still serialize through a single Paxos leader. 2PL means conflicting RW transactions block. |
| **Cost & complexity** | Synchronous global replication and the supporting clock fleet are expensive; it is overkill for problems that tolerate eventual consistency. |
| **Schema/locality coupling** | Getting good performance requires thoughtful interleaving and primary-key design (and avoiding monotonic keys to prevent hotspotting). |

## Influence & Legacy

- **TrueTime** reframed how the field thinks about clocks in distributed systems: uncertainty is real, bound it and expose it.
- **CockroachDB** is the most direct open-source descendant of the *ideas*, but **without** GPS/atomic clocks — it uses HLCs (hybrid logical clocks) and a configurable `max_offset`, trading Spanner's tight ε for commodity hardware and accepting that it provides *serializability* but not the same external-consistency guarantee out of the box.
- **YugabyteDB** similarly implements a Spanner-inspired architecture (Raft groups per tablet, HLCs, distributed transactions) on commodity infra.
- **Cloud Spanner** productized the system for external customers; **F1** (the SQL layer originally built on top) demonstrated a full OLTP/OLAP application database on Spanner.
- Broadly validated the thesis that **"NewSQL"** — horizontal scale *and* SQL/ACID — is achievable, ending the assumption that scale necessarily implies giving up transactions.

## Lessons for Architects

1. **Expose uncertainty instead of hiding it.** Spanner's genius is admitting clocks are imprecise and giving you `[earliest, latest]` plus a wait primitive. Many "impossible" guarantees become possible once error bounds are explicit and bounded.
2. **You can buy your way out of a hard distributed problem with better hardware.** GPS + atomic clocks converted a software impossibility (global synchronized time) into a tractable engineering budget (ε). Know when infrastructure investment beats algorithmic cleverness.
3. **Layer strong primitives.** Within a shard use consensus (Paxos); across shards use 2PC — and make every 2PC participant a consensus group so the classic blocking failure of 2PC disappears.
4. **Make the common read path cheap.** Lock-free MVCC snapshot reads from local replicas mean the expensive consistency machinery is only paid on writes and strong reads.
5. **Strong consistency is a feature for the *organization*, not just the system.** Google's explicit reasoning: it is cheaper to fix performance than to make every application developer reason about anomalies. Defaulting to strong semantics reduces aggregate complexity.
6. **Co-locate what you transact and join together.** Interleaving/directories show that physical data placement is a first-class design lever, not an afterthought.
