# 07 — CAP, Consistency Models & Quorums

> Once data lives on more than one node, you face an unavoidable question: when the network
> breaks or nodes disagree, do you keep answering (and risk staleness) or refuse to answer
> (and stay correct)? This doc covers the **CAP theorem**, its sharper successor **PACELC**,
> the spectrum of **consistency models**, **quorum** math (R + W > N) with a worked example,
> and **conflict resolution** (last-write-wins, vector clocks, CRDTs), ending with **tunable
> consistency** as implemented in Dynamo/Cassandra.

---

## 1. The CAP theorem

In any distributed data store you can have at most **two** of these three at the same time:

- **C — Consistency** (here, *linearizability*): every read sees the most recent write; all
  nodes agree. (Note: this "C" is **not** the ACID "C".)
- **A — Availability:** every request to a non-failing node gets a (non-error) response.
- **P — Partition tolerance:** the system keeps working despite the network dropping/delaying
  messages between nodes.

### The key insight

Network partitions **will** happen — cables cut, switches fail, packets drop. So **P is not
optional** in a distributed system. CAP therefore reduces to a real-time choice *during a
partition*: **C or A?**

```
                    Network partition splits the cluster:

        ┌──────────┐        X  (link down)  X        ┌──────────┐
        │  Node 1  │ ─────────────/ /───────────────►│  Node 2  │
        │ (newest) │                                 │ (stale)  │
        └────┬─────┘                                 └────┬─────┘
   client reads here                            client reads here

   CP choice: Node 2 refuses/errors (won't serve stale)  -> consistent, NOT available
   AP choice: Node 2 answers with its stale value        -> available, NOT consistent
```

### CP vs. AP with examples

| Choice | During a partition…                       | Good for                         | Example systems                       |
|--------|-------------------------------------------|----------------------------------|---------------------------------------|
| **CP** | Reject requests that can't be made consistent (sacrifice availability) | Banking, inventory, anything where a wrong answer is worse than no answer | HBase, MongoDB (majority), ZooKeeper, etcd, Spanner |
| **AP** | Keep serving, possibly stale; reconcile later (sacrifice consistency) | Shopping carts, social feeds, metrics, DNS — availability beats freshness | Cassandra, DynamoDB, Riak, CouchDB    |

> **Misconception:** CAP is *not* "pick 2 of 3 forever." Partitions are rare; the choice
> only bites *during* one. The rest of the time you can have both C and A — which is exactly
> what PACELC captures.

---

## 2. PACELC — the sharper statement

CAP is silent about the normal case (no partition). **PACELC** fills the gap:

> **If** there is a **P**artition, choose between **A**vailability and **C**onsistency;
> **E**lse (normal operation), choose between **L**atency and **C**onsistency.

```
                ┌─────────────── PACELC ───────────────┐
                │                                       │
   Partition?  ──► YES ──► trade  A  vs  C              │
                │                                       │
                └─► NO  ──► trade  L (latency) vs  C    │
                                                        │
   Even with the network healthy, enforcing strong consistency
   costs latency (coordinate across replicas before answering).
```

| System          | On Partition (PA/PC) | Else (EL/EC) | Reading                                |
|-----------------|----------------------|--------------|----------------------------------------|
| Dynamo/Cassandra| **PA**               | **EL**       | Available + low-latency; eventual      |
| MongoDB         | **PC**               | **EC**       | Consistent both cases (with majority)  |
| Google Spanner  | **PC**               | **EC**       | Strong consistency (pays latency)      |
| PNUTS (Yahoo)   | **PC**               | **EL**       | Consistent on partition, fast otherwise|

The latency point is the practical one: **strong consistency is never free** — even with a
healthy network you pay coordination latency on every operation.

---

## 3. Consistency models

A **consistency model** is the contract a store makes about *what a read can observe*.
Ordered from strongest (most intuitive, most expensive) to weakest (fastest, most surprising):

| Model               | Guarantee                                                              | Cost / availability |
|---------------------|------------------------------------------------------------------------|---------------------|
| **Strong / Linearizable** | A read always returns the latest committed write; the system behaves as if there were one copy. | Expensive; CP |
| **Causal**          | Operations with a cause→effect relationship are seen in order by everyone; concurrent ops may differ. | Moderate |
| **Read-your-writes**| You always see your *own* prior writes (others may lag).               | Cheap, per-session  |
| **Monotonic reads** | You never see time go *backwards* — once you've read a value, later reads won't show older data. | Cheap, per-session |
| **Eventual**        | If writes stop, all replicas *eventually* converge. No timing promise. | Cheapest; AP        |

### Why the weaker session guarantees matter

```
EVENTUAL but NOT read-your-writes:
   You post "Hello!" (write to leader) → refresh (read from lagging replica) → it's gone.   😖

EVENTUAL but NOT monotonic reads:
   Read 1 (fresh replica): 12 likes → Read 2 (stale replica): 9 likes → count went BACKWARD. 😖
```

`read-your-writes` and `monotonic-reads` are cheap **session-level** guarantees that fix the
most jarring user-visible eventual-consistency glitches without paying for full linearizability.

### Causal consistency example

```
Causal: a reply must never appear before the comment it replies to.

   Alice: "Anyone free Friday?"   (event e1)
   Bob:   "Yes!"  (reply to e1)   (event e2, depends on e1)

   Every observer sees e1 before e2.
   But two UNRELATED top-level posts may appear in different orders to different users.
```

---

## 4. Quorums (R + W > N)

In a **leaderless** / replicated store, define:

- **N** = number of replicas holding each piece of data.
- **W** = nodes that must **acknowledge a write** before it's considered successful.
- **R** = nodes that must **respond to a read** before returning an answer.

### The rule

> If **R + W > N**, the read set and write set are guaranteed to **overlap** in at least one
> node — so any read sees at least one replica that has the latest write. This gives **strong
> consistency**.

```
   N = 3 replicas:    [R1] [R2] [R3]

   Write with W=2:    write lands on R1, R2  (▓ = has new value)
                      [▓R1] [▓R2] [ R3]

   Read with R=2:     read any 2 of 3. Pigeonhole: at least one is R1 or R2,
                      so the read SEES the new value.   R+W = 4 > 3 ✅
```

### Worked example

Let **N = 3**, **W = 2**, **R = 2** → R + W = 4 > 3 = strong consistency.

1. Client writes `x = 42`. Coordinator sends to R1, R2, R3.
2. R1 and R2 ack quickly (W=2 satisfied) → **write succeeds**. R3 is slow/down; it lags.
   - State: `R1=42, R2=42, R3=old`.
3. Client reads `x`. Coordinator queries 2 nodes, say **R2 and R3**.
   - R2 returns `42` (version 7), R3 returns `old` (version 6).
   - Coordinator compares versions, returns the **newest (42)**, and triggers **read repair**
     to update R3.
4. Even though we hit the stale R3, the overlap guaranteed R2 carried the latest. ✅

### Tuning R and W

| Configuration         | Property                                            | Use case                         |
|-----------------------|-----------------------------------------------------|----------------------------------|
| `W=N, R=1`            | Fast reads, slow/fragile writes                     | Read-heavy, rarely written data  |
| `W=1, R=N`            | Fast writes, slow reads                             | Write-heavy logging              |
| `R + W > N` (e.g. 2/2)| Strong consistency, balanced                        | The usual quorum choice          |
| `R + W ≤ N` (e.g. 1/1)| **Eventual** consistency, maximum availability/speed| Caches, metrics                  |

Higher W = more durable but more likely to block if a node is down. Higher R = fresher reads
but slower. Quorums are the knob behind **tunable consistency**.

---

## 5. Conflict resolution

In multi-leader/leaderless systems, two clients can update the same key concurrently. You
need a rule to converge.

### 5.1 Last-Write-Wins (LWW)

Attach a timestamp to each write; the highest timestamp wins. Simple but **lossy** —
concurrent updates silently discard one, and clock skew across nodes makes "last" unreliable.

```
   t=100  Node A: cart = {apple}
   t=101  Node B: cart = {banana}       LWW keeps {banana}; {apple} is LOST.
```

Used by Cassandra by default. Fine when losing a concurrent write is acceptable.

### 5.2 Vector clocks

Each node keeps a counter; a write carries a **vector** of counters `[A:2, B:1, ...]`.
Comparing vectors tells you whether one write *happened-before* another or whether they are
**concurrent** (genuinely conflicting), letting the system detect — not silently lose —
conflicts.

```
   v1 = [A:2, B:1]   v2 = [A:2, B:2]   ->  v1 happened-before v2 (B advanced)  → keep v2
   v1 = [A:2, B:1]   v2 = [A:1, B:2]   ->  neither dominates    → CONCURRENT → conflict!
```

On a detected conflict, the system surfaces **both versions (siblings)** for the application
(or user) to merge — e.g., Amazon's shopping cart historically merged carts by **union** so
nothing a customer added was lost. Used by Dynamo/Riak.

### 5.3 CRDTs (Conflict-free Replicated Data Types)

Data types whose merge function is **commutative, associative, and idempotent**, so replicas
**always converge** to the same value regardless of update order — no coordination, no manual
resolution.

```
   G-Counter (grow-only counter): each node counts its own increments; merge = element-wise MAX/sum.
   OR-Set, LWW-Register, RGA (text) ... building blocks for collaborative apps.

   Node A: +3      Node B: +5      merge -> 8   (order doesn't matter)
```

Used by Redis (CRDTs in Active-Active), Riak, and collaborative editors (Automerge, Yjs).

### Comparison

| Strategy      | Detects conflicts? | Loses data?         | Complexity | Best for                          |
|---------------|--------------------|---------------------|------------|-----------------------------------|
| LWW           | No                 | Yes (silently)      | Low        | Where losing concurrent writes is OK |
| Vector clocks | Yes                | No (surfaces siblings) | Medium  | App can merge (carts, docs)       |
| CRDTs         | N/A (auto-merges)  | No                  | High       | Counters, sets, collaborative editing |

---

## 6. Tunable consistency (Dynamo / Cassandra)

Dynamo-style stores expose N, R, W (and consistency *levels*) **per query**, so the same
database can be CP-ish or AP-ish depending on the operation.

Cassandra consistency levels (for a keyspace with **replication factor N = 3**):

| Level          | Nodes required        | Effect                                          |
|----------------|-----------------------|-------------------------------------------------|
| `ONE`          | 1                     | Fast, weakest; may read stale                    |
| `QUORUM`       | ⌊N/2⌋ + 1 = **2**     | Strong if used for **both** read & write (R+W>N) |
| `LOCAL_QUORUM` | quorum within local DC| Strong locally, avoids cross-DC latency          |
| `ALL`          | N = 3                 | Strongest, but any node down fails the op        |

```cql
-- Per-statement consistency in Cassandra (CQL):
CONSISTENCY QUORUM;
INSERT INTO orders (id, total) VALUES (91, 240.00);   -- W=QUORUM
CONSISTENCY QUORUM;
SELECT total FROM orders WHERE id = 91;                -- R=QUORUM  => R+W>N strong
```

```python
# DataStax driver: choose consistency per request
from cassandra.cluster import Cluster
from cassandra import ConsistencyLevel
from cassandra.query import SimpleStatement

session = Cluster(["10.0.0.1"]).connect("shop")

write = SimpleStatement(
    "INSERT INTO orders (id, total) VALUES (%s, %s)",
    consistency_level=ConsistencyLevel.QUORUM,   # W = 2 of 3
)
session.execute(write, (91, 240.00))

read = SimpleStatement(
    "SELECT total FROM orders WHERE id = %s",
    consistency_level=ConsistencyLevel.QUORUM,   # R = 2 of 3  -> R+W=4 > 3 = strong
)
print(session.execute(read, (91,)).one())
```

This is CAP/PACELC made operational: dial QUORUM/QUORUM for correctness on critical data,
ONE/ONE for speed and availability on tolerant data — **in the same cluster**.

---

## 7. Key Takeaways

- **CAP:** during a network **partition** (which is inevitable, so P is mandatory) you must
  choose **Consistency or Availability**. CP refuses stale answers; AP keeps serving them.
- **PACELC** refines it: even with **no** partition, strong consistency costs **latency** —
  consistency is never free.
- **Consistency models** form a spectrum: strong/linearizable → causal → read-your-writes →
  monotonic reads → eventual. The cheap **session guarantees** (read-your-writes, monotonic
  reads) fix most user-facing eventual-consistency glitches.
- **Quorums:** with N replicas, **R + W > N** forces read/write sets to overlap, giving strong
  consistency. Tune R and W to trade read freshness, write durability, latency, and availability.
- **Conflict resolution:** **LWW** is simple but loses data; **vector clocks** detect concurrent
  writes so the app can merge; **CRDTs** merge automatically and always converge.
- **Tunable consistency** (Dynamo/Cassandra) exposes N/R/W per query — one cluster can be
  strongly or eventually consistent depending on the operation's needs.
