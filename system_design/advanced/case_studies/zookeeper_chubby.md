# Coordination Services: Google Chubby & Apache ZooKeeper

## Overview

Large distributed systems repeatedly need the same small set of hard primitives: **elect a leader**, **agree on configuration**, **acquire a lock**, **discover live members**, **detect failures**. Solving each from scratch with consensus is error-prone, so the industry converged on dedicated **coordination services** — small, strongly-consistent, highly-available stores whose job is to be the **one trustworthy source of truth** that everything else leans on.

Two systems define this category:

- **Google Chubby** — a coarse-grained **distributed lock service** (and small file store) built at Google.
  - *Paper:* Mike Burrows, *"The Chubby Lock Service for Loosely-Coupled Distributed Systems,"* **OSDI 2006**.
  - Used as the foundation for **GFS** and **Bigtable** master election, and — to Google's mild surprise — became the company-wide **name service and config store**.
- **Apache ZooKeeper** — an open-source coordination kernel originally from Yahoo!.
  - *Paper:* Hunt, Konar, Junqueira, Reed, *"ZooKeeper: Wait-free coordination for Internet-scale systems,"* **USENIX ATC 2010**. Consensus protocol: *"ZAB"* (Junqueira, Reed, Serafini, 2011).
  - Underpins Hadoop, HBase, Kafka (historically), Solr, and countless others.

- **One-line thesis:** Don't build consensus into every service. Build it **once**, correctly, into a coordination kernel, and have everyone else use its simple primitives.

## The Problem It Solved

Consensus (Paxos/Raft) is famously subtle to implement correctly. Most engineers should never write it. Yet nearly every distributed system needs:

1. **Leader election** — pick exactly one primary among replicas, and let everyone agree on who it is.
2. **Configuration / metadata** — a consistent place to publish "the current cluster layout," "the active schema version," "feature flags."
3. **Locks / mutual exclusion** — coordinate access to a shared resource across machines.
4. **Group membership & failure detection** — know which nodes are currently alive.

The insight (Chubby's, then ZooKeeper's): provide these as a **reliable service with a familiar, file-system-like API**, backed by a replicated consensus log, so that hundreds of other systems can share one well-tested coordination substrate instead of each rolling their own (and getting it wrong).

## Architecture

Both run a small **ensemble** (typically **3 or 5 nodes**) of replicas. One is the **leader**; the rest are **followers**. All writes funnel through the leader and are committed by **majority quorum** via a consensus protocol. Clients keep a **session** with the ensemble.

```
   Chubby cell (5 replicas)                  ZooKeeper ensemble (5 servers)
   ┌───────────────────────────┐            ┌───────────────────────────┐
   │  replica  replica  MASTER  │            │  follower follower LEADER  │
   │  replica  replica          │            │  follower follower         │
   │   └── Paxos-replicated ──┘  │            │   └──── ZAB-replicated ──┘ │
   │       local DB (log+snap)   │            │       in-mem DB + WAL+snap │
   └──────────────▲──────────────┘            └──────────────▲────────────┘
                  │ all WRITES → master                       │ all WRITES → leader
   clients ───────┘                            clients ───────┘  (reads served by any
   (acquire lease/locks, read/write files)       server, locally, from its replica)
```

Key shared properties:
- **Writes are linearized** through the leader and committed by majority — survives a minority of failures.
- A cluster of `2f+1` nodes tolerates `f` failures.
- **Sessions** tie a client to the service with timeouts/heartbeats; session loss is the universal "you may have lost your coordination state" signal.

### Chubby specifics
- A deployment is a **cell** (usually 5 replicas, one master). The master holds a **master lease**; it's renewed via Paxos and clients are directed to it.
- Chubby is built directly on a **Paxos**-based replicated log (the work that later produced *"Paxos Made Live"*, the famous account of how hard production Paxos is).
- Designed for **coarse-grained** locking (locks held for hours/days, e.g., electing a GFS master), explicitly **not** fine-grained high-churn locking.
- Heavy **client-side caching** of file data and metadata, kept consistent by the master via **invalidations** (the master blocks a modification until it has invalidated cached copies on all clients holding them).

### ZooKeeper specifics
- An **ensemble** of servers running **ZAB (ZooKeeper Atomic Broadcast)** — a leader-based, primary-order atomic broadcast protocol (Paxos-like but purpose-built so that state changes apply in strict order). ZAB phases: **leader election → discovery → synchronization → broadcast**.
- The entire data tree is held **in memory** (with a write-ahead transaction log + periodic fuzzy snapshots on disk for recovery) → very fast reads and watches.
- **Reads are served locally by any server** (so reads scale with the ensemble size) and may be **slightly stale**; a client can call **`sync()`** before a read to get an up-to-date view. Writes always go through the leader and ZAB.

## How It Works

### The data model: a tiny hierarchical namespace

Both expose a **file-system-like tree**. In ZooKeeper the nodes are called **znodes**; in Chubby they're small files/directories. The store is **not** for bulk data — values are small (Chubby caps files at ~256 KB; ZooKeeper znode data ~1 MB default but meant to be far smaller).

```
   /
   ├── service-a/
   │     ├── config        (znode holding small config blob)
   │     ├── leader        (ephemeral znode → current primary's id)
   │     └── members/
   │           ├── node-0000000012   (ephemeral sequential)
   │           ├── node-0000000013
   │           └── node-0000000014
   └── locks/
         └── resource-x/
               ├── lock-0000000031   (ephemeral sequential)
               └── lock-0000000032
```

**znode flavors (ZooKeeper):**
| Type | Behavior |
|---|---|
| **Persistent** | Exists until explicitly deleted. |
| **Ephemeral** | Tied to the creating client's **session**; auto-deleted when the session ends (crash/timeout/disconnect). The basis of failure detection. |
| **Sequential** | ZooKeeper appends a monotonically increasing counter to the name. Combine with ephemeral → **ephemeral sequential**, the workhorse for locks/election. |

### Sessions, ephemeral nodes, and failure detection

A client establishes a **session** with a negotiated timeout, kept alive by heartbeats. If the client crashes or the network drops long enough, the session **expires**, and the service **automatically deletes all ephemeral nodes** that session created. This single mechanism gives you **liveness / failure detection for free**: "this node's ephemeral znode disappeared" ≡ "this node is gone."

### Watches — the notification primitive

Instead of polling, a client sets a **watch** on a znode (on its data or its children). When that znode changes, the client gets a **one-time** notification. Watches are:
- **One-shot** (you must re-register after firing),
- **ordered** (a client sees the watch event before it sees the changed data via subsequent reads — ZooKeeper's ordering guarantees make this safe),
- the mechanism that turns the store from a passive registry into a **reactive coordination bus** (config changes, membership changes, lock availability all push to interested clients).

Chubby's analogue is **event notifications** on files/directories (file-contents-modified, child-added/removed, lock-acquired, etc.), layered on top of its cache-invalidation machinery.

### Consistency & ordering guarantees (ZooKeeper)

ZooKeeper deliberately provides a specific (not fully linearizable) model that is easy to reason about:
- **Linearizable writes** — all writes are totally ordered by ZAB and stamped with a monotonically increasing **zxid** (ZooKeeper transaction id).
- **FIFO client order** — a single client's operations are applied in the order it issued them.
- **Reads are not linearizable by default** (may be stale, since served locally); `sync()` + read closes the gap when needed.

This "wait-free for reads, linearizable for writes" choice is what lets reads scale horizontally while writes stay strongly consistent — a pragmatic trade that suits coordination workloads (read-heavy, write-rare).

## The Recipes (what people actually build)

These are the standard patterns the primitives compose into. The **herd-avoiding lock/election recipe** is the most important idea.

### Leader election (correct, herd-free)

```
   Each candidate: create  /service/election/n_   as EPHEMERAL SEQUENTIAL
        → it is assigned, e.g., n_0000000017
   The candidate with the LOWEST sequence number is the leader.
   A non-leader sets a WATCH on the node with the next-lower sequence
        (NOT on the leader, NOT on the parent) and waits.
   If a node disappears (session dies → ephemeral deleted), ONLY the single
        successor wakes up, re-checks, and possibly becomes leader.
```

Watching only your immediate predecessor avoids the **"herd effect"** — a naive design where everyone watches the same lock node and all wake up on every change, stampeding the ensemble.

### Distributed lock — identical structure

Create an ephemeral-sequential child under `/locks/resource`; you **hold the lock** iff you have the lowest sequence number; otherwise watch your predecessor. Releasing = deleting your znode (or just dying — the ephemeral node vanishes, so locks are self-healing). This is the ZooKeeper analogue of Chubby's `Acquire()/Release()` on a lock file (Chubby locks come in **shared** (read) and **exclusive** (write) modes and carry **sequencers** — see below).

### Configuration management

Store config in a persistent znode (`/service/config`). Every instance reads it at startup and **sets a watch**. On change, the leader writes the new blob; all watchers are notified and re-read → **consistent, push-based config distribution** with no polling.

### Group membership / service discovery

Each live instance creates an **ephemeral** znode under `/service/members`. To discover peers, read the children of `/service/members` and watch for changes. Instances that die have their ephemeral nodes removed automatically → the membership list is always current.

### The lock-correctness caveat: sequencers / fencing tokens

A subtle, critical lesson from Chubby: a lock holder can be **paused** (GC, slow network) long enough that its session expires and the lock is granted to someone else — yet the original holder, on waking, still *thinks* it holds the lock and issues a write. To prevent this, Chubby provides **sequencers**: the lock comes with a monotonically increasing number that the client passes to the protected resource, which **rejects stale sequencers**. (The same idea is widely called a **fencing token**.) Coordination services prevent *races at the service*, but the **protected resource must also validate the token** — coordination alone is not sufficient for safety.

## Key Innovations

1. **Coordination-as-a-service.** Solve consensus once in a shared kernel; expose simple, file-like primitives so application teams never touch Paxos directly. (Burrows: people wanted a *lock service*, even though a Paxos *library* was the more "fundamental" offering — usability won.)
2. **Ephemeral nodes + sessions = automatic failure detection and self-healing locks.** State tied to liveness is the elegant core idea.
3. **Watches** turn a consistent store into a push-based event system, eliminating polling.
4. **Wait-free reads + linearizable writes (ZooKeeper)** — scaling reads across the ensemble while keeping a simple, totally-ordered write model.
5. **Herd-free recipes** (ephemeral-sequential + watch-your-predecessor) — composing tiny primitives into correct, scalable election/locking.
6. **Sequencers / fencing tokens** — acknowledging that a lock service cannot, by itself, stop a paused-then-revived client; the resource must validate a token.

## Data Model / APIs

**ZooKeeper API (illustrative):**

```
create(path, data, flags)   flags ∈ {PERSISTENT, EPHEMERAL, SEQUENTIAL, ...}
delete(path, version)
exists(path, watch?)        → stat (and optionally register a watch)
getData(path, watch?)       → (data, stat)
setData(path, data, version)   # version = optimistic concurrency check
getChildren(path, watch?)   → [child names]
sync(path)                  # ensure this client sees latest committed state
```

Optimistic concurrency: every znode has a `stat` with a `version`; `setData`/`delete` take an expected version and fail if it changed — a built-in compare-and-set.

Leader-election sketch:

```python
me = zk.create("/svc/election/n_", ephemeral=True, sequential=True)  # -> /svc/election/n_0000000017
children = sorted(zk.get_children("/svc/election"))
if me == "/svc/election/" + children[0]:
    become_leader()
else:
    pred = predecessor_of(me, children)
    zk.exists("/svc/election/" + pred, watch=on_pred_gone)  # wake only when predecessor dies
```

**Chubby API (illustrative):** `Open()/Close()` handles to files; `GetContentsAndStat()/SetContents()`; `Acquire()/TryAcquire()/Release()` for locks (shared/exclusive); `GetSequencer()/CheckSequencer()` for fencing; event subscriptions for change notifications.

## Trade-offs & Limitations

| Aspect | Trade-off |
|---|---|
| **Not a database / not for bulk data** | Small values only (KB scale). It stores *coordination metadata*, never your application's primary data. |
| **Writes don't scale horizontally** | Every write goes through the leader + quorum. Throughput is bounded; these systems are **read-heavy by design**. (ZooKeeper observers and Chubby proxies/caching mitigate read load, not write load.) |
| **Stale reads (ZooKeeper)** | Local reads can lag; you must use `sync()` (or rely on watch ordering) where you need freshness. Chubby instead keeps caches strictly consistent via invalidation, at the cost of write-side blocking. |
| **Session/timeout tuning is hard** | Too-short timeouts cause spurious session loss (and ephemeral-node deletion → spurious failovers); too-long delays real failure detection. Long GC pauses are a classic trap. |
| **Locks are advisory + need fencing** | The service grants the lock; it cannot force a paused client to stop. Without sequencers/fencing tokens validated by the resource, safety is not guaranteed. |
| **Ensemble sizing** | Larger ensembles tolerate more failures but make writes slower (bigger quorum). 5 is a common sweet spot; even numbers add no fault tolerance and cost latency. |
| **It's critical infrastructure** | Because *everything* depends on it (Chubby became Google's de facto name service), an outage is catastrophically broad — a single point of *organizational* dependence even when internally HA. |

## Influence & Legacy

- **Chubby → ZooKeeper → etcd** is the lineage of coordination services.
- **etcd** is the modern successor and the most important descendant: a distributed key-value store using the **Raft** consensus protocol (Raft was explicitly designed to be more understandable than Paxos/ZAB). etcd is the **brain of Kubernetes**, storing all cluster state (and supports leases, watches, and compare-and-swap — the same primitives, modernized with a flat key space + range queries and a gRPC API).
- **HashiCorp Consul** (Raft-based) brought the same primitives to service discovery and config with a service-centric model and health checks.
- **Apache Kafka** historically used ZooKeeper for controller election and metadata; **KRaft** later moved that into Kafka itself using a Raft quorum — illustrating both how essential coordination is and the trend toward **embedding** consensus rather than depending on an external service.
- The recipes (ephemeral-sequential election/locks, watch-based config) are now textbook distributed-systems patterns.

## Lessons for Architects

1. **Never hand-roll consensus.** Use a coordination service (ZooKeeper/etcd/Consul) or a proven library. Paxos/Raft "look simple" and are not — *Paxos Made Live* exists precisely because production consensus is brutal.
2. **Separate coordination from data.** Keep your high-volume application data in your database; keep the tiny, must-be-consistent metadata (who's leader, what's the config, who's alive) in the coordination kernel. Don't conflate the two.
3. **Tie state to liveness.** Ephemeral nodes + sessions are the elegant trick: failure detection, self-healing locks, and accurate membership all fall out of "state disappears when the session dies."
4. **Push, don't poll.** Watches/events make coordination reactive and cheap. Design for notification, not periodic scanning.
5. **A lock service is necessary but not sufficient for safety.** A paused client can wake up believing it still holds a lock. Use **fencing tokens / sequencers** and have the *protected resource* reject stale ones. Mutual exclusion at the coordinator does not guarantee mutual exclusion at the resource.
6. **Expect it to become load-bearing for the whole org.** Chubby's story — built as a lock service, adopted as the universal name/config service — is the norm. Plan for the coordination service to be a dependency of nearly everything, and protect it accordingly (capacity, isolation, careful upgrades, avoid making it a thundering-herd target).
7. **Choose your consistency model on purpose.** ZooKeeper's "linearizable writes, possibly-stale local reads + `sync()`" is a deliberate scalability trade. Know exactly what your coordination layer guarantees before you build correctness on top of it.
