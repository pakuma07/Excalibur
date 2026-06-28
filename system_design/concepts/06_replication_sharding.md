# 06 — Replication & Sharding: Scaling Data

> A single database node has limits: it can only hold so much data, serve so many reads,
> absorb so many writes, and it is a single point of failure. **Replication** (copy the data
> to more nodes) and **sharding/partitioning** (split the data across nodes) are the two
> fundamental moves for scaling and surviving failure. This doc covers both, plus
> federation, the celebrity/hot-spot problem, cross-shard queries, and how SQL vs. NoSQL
> systems approach scaling.

---

## 1. The two axes of scaling data

```
                  REPLICATION                         SHARDING (PARTITIONING)
              (copy the SAME data)                    (split DIFFERENT data)

         ┌──────┐  ┌──────┐  ┌──────┐            ┌────────┐ ┌────────┐ ┌────────┐
         │ A-Z  │  │ A-Z  │  │ A-Z  │            │  A-H   │ │  I-Q   │ │  R-Z   │
         └──────┘  └──────┘  └──────┘            └────────┘ └────────┘ └────────┘
          full copy  full copy full copy           shard 1    shard 2    shard 3

  Helps: read scale, availability,             Helps: write scale, total storage,
         fault tolerance, geo-locality                throughput beyond one node
```

They are **complementary**: real systems usually shard *and* replicate each shard.

---

## 2. Replication

**Replication** keeps copies of the same data on multiple nodes. Benefits:

- **Read scaling** — spread reads across copies.
- **High availability** — a replica takes over when the primary dies.
- **Geo-locality** — serve users from a nearby copy (lower latency).
- **Durability** — a copy survives if one disk/datacenter fails.

The central design question: **who can accept writes, and how do changes propagate?**

### 2.1 Single-leader (a.k.a. master–slave, primary–replica)

One node (the **leader/primary**) accepts all writes. It streams its change log to
**followers/replicas**, which serve reads. This is the most common setup (PostgreSQL,
MySQL, MongoDB replica sets).

```
                     writes
        client ───────────────► ┌─────────┐
                                 │ LEADER  │
                                 └────┬────┘
                  replication stream  │  (WAL / binlog)
                      ┌───────────────┼───────────────┐
                      ▼               ▼               ▼
                 ┌─────────┐    ┌─────────┐     ┌─────────┐
        reads ──►│ replica │    │ replica │     │ replica │◄── reads
                 └─────────┘    └─────────┘     └─────────┘
```

- ✅ Simple; no write conflicts (one writer).
- ✅ Read replicas scale reads horizontally.
- ❌ The leader is a write bottleneck and a failover risk.

### 2.2 Multi-leader

Multiple nodes accept writes and replicate to each other. Common across **datacenters**
(one leader per region) or for offline-capable clients.

```
   DC-East                         DC-West
  ┌─────────┐   bi-directional    ┌─────────┐
  │ LEADER  │◄───────────────────►│ LEADER  │
  └─────────┘   replication       └─────────┘
   ▲     │                          ▲     │
 writes reads                     writes reads
```

- ✅ Lower write latency per region; survives a whole-region outage.
- ❌ **Write conflicts**: the same row updated in two regions concurrently must be
  reconciled (see CRDTs / LWW / version vectors in doc 07).

### 2.3 Leaderless

Any replica accepts writes; the client (or a coordinator) writes to several nodes and reads
from several, using **quorums** to stay consistent. Dynamo, Cassandra, Riak work this way.

```
                ┌────┐
   write to ───►│ R1 │
   W nodes  ───►│ R2 │   read from R nodes; if R + W > N, reads
            ───►│ R3 │   overlap writes -> see the latest value
                └────┘   (quorum; covered in doc 07)
```

- ✅ No single point of failure; tunable consistency; great write availability.
- ❌ Client/coordinator must handle conflicts and read repair.

### 2.4 Synchronous vs. asynchronous replication

| Mode             | Leader waits for replica before ack? | Consequence                                              |
|------------------|--------------------------------------|----------------------------------------------------------|
| **Synchronous**  | Yes                                  | No data loss on failover, but **slower writes**; a slow/down replica blocks writes. |
| **Asynchronous** | No (ack immediately, ship later)     | **Fast writes**, but a crash can lose un-shipped writes (data loss window). |
| **Semi-sync**    | Wait for *one* replica, others async | Pragmatic middle ground (e.g., MySQL semi-sync).         |

```
SYNC:   write ──► leader ──► replica (ack) ──► leader acks client   [durable, slower]
ASYNC:  write ──► leader (acks client immediately) ····► replica    [fast, lossy window]
```

### 2.5 Replication lag & read-after-write

With async replication, a replica is briefly **behind** the leader — **replication lag**
(often single-digit ms, but can spike to seconds under load or network trouble). This
breaks user expectations:

> A user posts a comment (write → leader), then refreshes (read → lagging replica) and the
> comment isn't there yet.

**Mitigations:**

- **Read-your-writes:** route a user's reads to the leader for a short window after they write.
- Track a per-user **log position**; only read from replicas caught up past it.
- Read from the leader for data the user just modified; replicas for everything else.

---

## 3. Partitioning / Sharding

**Sharding** splits one logical dataset across multiple nodes (**shards**), each holding a
*subset* of the data. This scales **writes** and **total storage** beyond a single machine —
something replication alone cannot do.

The key decision is the **partition key** and the **partitioning strategy**.

### 3.1 Range partitioning

Assign contiguous key ranges to shards.

```
  user_id 1–999999     -> shard A
  user_id 1000000–1999999 -> shard B
  user_id 2000000+     -> shard C
```

- ✅ Efficient **range scans** (`WHERE id BETWEEN ...`) hit one shard; keys stay ordered.
- ❌ **Hot spots**: monotonically increasing keys (timestamps, autoincrement IDs) send all
  new writes to the *last* shard. The "today" shard burns while older shards idle.

### 3.2 Hash partitioning

Apply a hash to the key and use it (often `hash(key) % N`, better: consistent hashing) to
pick a shard.

```
  shard = hash(user_id) % 3
```

- ✅ **Even distribution**; no write hot spot from sequential keys.
- ❌ Range queries must hit **all** shards (scatter-gather); related keys scatter.
- ❌ Naïve `% N` reshuffles almost everything when N changes — see §3.5 and doc 08
  (consistent hashing).

### 3.3 Directory (lookup-table) partitioning

A lookup service maps each key (or key range) to its shard. Flexible — you can move data and
just update the directory.

```
        ┌──────────────┐
key ───►│  Directory   │── "key X lives on shard B" ──► shard B
        │ (lookup map) │
        └──────────────┘
```

- ✅ Maximum flexibility; rebalance by editing the map.
- ❌ The directory is an extra hop and a potential **single point of failure/bottleneck**
  (so it gets cached/replicated).

### 3.4 Comparison

| Strategy   | Distribution | Range queries | Rebalancing | Hot-spot risk            |
|------------|--------------|---------------|-------------|--------------------------|
| Range      | Can be uneven| Excellent     | Easy-ish    | High (sequential keys)   |
| Hash       | Even         | Poor (scatter)| Hard with `%N`; easy with consistent hashing | Low (unless one key is hot) |
| Directory  | Flexible     | Depends       | Easiest     | Manageable               |

### 3.5 Resharding

When a shard fills up or gets hot, you add shards and redistribute. The pain depends on the
scheme:

- Naïve `hash % N` → changing N **remaps almost every key** (mass data movement, cache
  misses). Don't do this at scale.
- **Consistent hashing** (doc 08) → adding/removing a node moves only ~`1/N` of keys.
- **Pre-sharding / virtual shards:** create many more logical shards (e.g., 1024) than
  physical nodes up front, then move whole logical shards between nodes. (Used by Redis
  Cluster's 16384 hash slots, Vitess, etc.)

### 3.6 The celebrity / hot-key problem

Even with perfect hashing, a **single very popular key** overwhelms its shard. A celebrity
with 50M followers, or a viral product, concentrates reads/writes on one partition.

```
         even key distribution ...        ... but ONE key gets 90% of traffic
  shard A  shard B  shard C  shard D       shard A  shard B  shard C  shard D
   [▓▓]     [▓▓]     [▓▓]     [▓▓]           [▓▓]     [▓▓▓▓▓▓▓▓▓]  [▓▓]   [▓▓]
                                                       ▲ celebrity = HOT SHARD
```

**Mitigations:**

- **Key splitting / salting:** append a random suffix (`celebrity_id:0..9`) to spread one
  logical key across 10 physical sub-keys; fan out reads and merge.
- **Dedicated cache** for the hot key (CDN/Redis) so it never reaches the shard.
- **Replicate the hot shard** more heavily to spread its reads.

### 3.7 Cross-shard queries & joins

The hard part of sharding: an operation touching multiple shards.

- **Scatter-gather:** send the query to all shards, merge results in the app/coordinator.
  Expensive; latency is bounded by the *slowest* shard; `ORDER BY`/`LIMIT`/aggregates get tricky.
- **Cross-shard joins:** generally **not supported** natively. Strategies:
  - **Co-locate** related data via the same shard key (e.g., shard orders by `customer_id`
    so a customer's orders sit on one shard with the customer).
  - **Denormalize** so the join is unnecessary.
  - Join in the **application layer**.
- **Cross-shard transactions:** require 2-phase commit or sagas — slow and complex. Designs
  try hard to keep a transaction within one shard.

```
SELECT * FROM orders WHERE total > 100;     -- no shard key in filter
        │
   scatter ─► shard A ─┐
           ─► shard B ─┤──► coordinator merges/sorts ──► client
           ─► shard C ─┘    (latency = slowest shard)
```

---

## 4. Federation (functional partitioning)

Split databases **by feature/function** rather than by rows. Each service owns its own DB.

```
   ┌────────────┐   ┌──────────────┐   ┌─────────────┐
   │ users DB   │   │ products DB  │   │ orders DB   │
   └────────────┘   └──────────────┘   └─────────────┘
   (auth service)   (catalog svc)      (checkout svc)
```

- ✅ Smaller, independently scalable DBs; less write contention; clean ownership.
- ❌ Cross-feature joins now span services (app-side joins / API calls); harder to do a
  transaction spanning features. This is the database side of the microservices pattern
  ("database per service").

---

## 5. SQL vs. NoSQL scaling

| Aspect                 | Relational (SQL)                                   | NoSQL                                            |
|------------------------|----------------------------------------------------|--------------------------------------------------|
| Default scaling move   | **Vertical** (bigger box) first                    | **Horizontal** (more nodes) by design            |
| Read scaling           | Read replicas (mature, easy)                       | Replicas / quorum reads                           |
| Write scaling          | Sharding = significant manual effort; or Vitess/Citus | Auto-sharding is built in (Cassandra, Dynamo, Mongo) |
| Joins after sharding   | Lost / app-side                                    | Usually never had them; model around it          |
| Consistency under scale| Strong, but harder to keep across shards           | Often tunable/eventual to favor availability      |
| Rebalancing            | Often manual / tooling-assisted                    | Frequently automatic (consistent hashing)         |

**Takeaway:** relational databases scale *reads* easily (replicas) but scaling *writes*
(sharding) is real work and you lose cross-shard joins/transactions. NoSQL systems bake
horizontal scale in — but you give up joins, ad-hoc queries, and often strong consistency to
get it. Choose based on which you can afford to lose.

---

## 6. Putting it together — shard + replicate

A production layout typically shards for capacity and replicates each shard for HA:

```
                         ┌──────────────── Shard 1 ────────────────┐
         users A–H  ───► │  leader  ──► replica ──► replica        │
                         └─────────────────────────────────────────┘
                         ┌──────────────── Shard 2 ────────────────┐
         users I–Q  ───► │  leader  ──► replica ──► replica        │
                         └─────────────────────────────────────────┘
                         ┌──────────────── Shard 3 ────────────────┐
         users R–Z  ───► │  leader  ──► replica ──► replica        │
                         └─────────────────────────────────────────┘
```

Writes go to the shard's leader (picked by partition key); reads can fan out to that shard's
replicas. If a leader dies, a replica is promoted.

---

## 7. Key Takeaways

- **Replication = copies of the same data**; it scales **reads** and provides **availability**,
  but a single leader can't scale writes.
- **Single-leader** is the simple default (no conflicts); **multi-leader** helps multi-region
  writes but creates conflicts; **leaderless** (Dynamo/Cassandra) uses quorums.
- **Sync** replication = no data loss but slower writes; **async** = fast writes with a
  data-loss window and **replication lag** (mitigate with read-your-writes routing).
- **Sharding = splitting different data**; it scales **writes** and **storage**. Choose
  **range** (good ranges, hot-spot risk), **hash** (even, bad ranges), or **directory** (flexible, extra hop).
- The hard parts of sharding are **resharding** (use consistent hashing / pre-sharding),
  **hot keys / the celebrity problem** (salt, cache, replicate), and **cross-shard queries/joins**
  (co-locate by shard key, denormalize, or join in the app).
- **Federation** splits DBs by feature — clean ownership at the cost of cross-feature joins.
- Real systems **shard *and* replicate**: pick a shard by key, replicate each shard for HA.
- **SQL scales reads cheaply, writes painfully; NoSQL scales out by design but drops joins
  and often strong consistency.** Pick what you can afford to lose.
