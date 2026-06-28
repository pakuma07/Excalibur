# Amazon Dynamo (2007)

## Overview

**Dynamo** is Amazon's internal, highly-available, eventually-consistent **key-value** storage system. It was built to keep Amazon's shopping cart (and other "always writeable" services) available even during data-center failures, network partitions, and the chaos of routine operations at scale.

- **Built by:** Amazon (DeCandia, Hastorun, Jampani, et al.).
- **Seminal paper:** *"Dynamo: Amazon's Highly Available Key-value Store"* — **SOSP 2007**.
- **Crucial distinction:** *Dynamo* (the 2007 design, internal) ≠ *Amazon DynamoDB* (the 2012 managed cloud service, which borrowed the name and some lineage but is a different, separately-engineered system).
- **One-line thesis:** For a class of services, **availability and partition tolerance trump strong consistency**. Always accept writes; reconcile conflicts later — choosing **A and P** in CAP terms.

## The Problem It Solved

Amazon's platform is a constellation of stateless services backed by stateful storage, with hard latency SLAs expressed at the **99.9th percentile** (not the mean). The driving requirements:

1. **"Always writeable."** The canonical example is the shopping cart: a customer must always be able to add or remove items, even if disks are failing, nodes are down, or the network is partitioned. **Rejecting a write is a lost sale.**
2. **Incremental scalability** with commodity hardware; failures are the norm, not the exception ("treat failure handling as part of normal operation").
3. **Tight, predictable tail latency** — bound the worst case, not the average.
4. **No relational complexity needed.** Many services only do primary-key access; a full RDBMS (with its consistency, availability, and operational cost) was overkill and a liability.

The deliberate sacrifice: **strong consistency**. Dynamo provides eventual consistency and pushes **conflict resolution to the application** (or uses last-writer-wins as a default), in exchange for an "always-on" experience.

## Architecture

Dynamo is a **decentralized, symmetric, peer-to-peer ring**. There is no master; every node has the same responsibilities. It is a "zero-hop DHT" — each node knows enough routing info to reach the right node in one hop.

```
                       Consistent-hash ring (keyspace wraps around)
                                  ┌───────────┐
                            Node A│  position  │Node B
                          (tokens)│   on ring  │(tokens)
                                  └───────────┘
        ┌──────────────────────────────────────────────────────────┐
        │                                                            │
        │     N9 ───── N1 ───── N2 ───── N3 ───── N4 ───── N5 ...    │
        │      ▲                  │                                  │
        │      │     key k hashes here ──► coordinator = next node   │
        │      │     clockwise; replicate to next N-1 distinct nodes  │
        │      └──────────────────── ring wraps ────────────────────┘
        │                                                            │
        └──────────────────────────────────────────────────────────┘

   For key k with N=3:  preference list = [N2, N3, N4]  (N2 is coordinator)
   N4 may be skipped to the next physical node if N3/N4 share a host/rack.
```

Every node participates equally and runs the same set of components.

## How It Works

Dynamo is best understood as a careful composition of well-known distributed-systems techniques. The paper's lasting contribution is the *combination* and the *production engineering*, summarized in their own table:

| Problem | Technique | Advantage |
|---|---|---|
| Partitioning | **Consistent hashing** (+ virtual nodes) | Incremental scalability, smooth rebalancing |
| High availability for writes | **Vector clocks** + reconciliation at reads | Decouples version count from update rate |
| Handling temporary failures | **Sloppy quorum** + **hinted handoff** | Availability + durability during failures/partitions |
| Recovering from permanent failures | **Anti-entropy with Merkle trees** | Cheap divergence detection, low data transfer |
| Membership & failure detection | **Gossip** protocol | Decentralized, avoids a central registry |

### Partitioning — consistent hashing + virtual nodes

The output range of a hash function is a fixed circular space (a ring). Each node is assigned a random position; a key is stored on the first node found by walking **clockwise** from `hash(key)`.

**Problem with naive consistent hashing:** random placement causes non-uniform load and ignores hardware heterogeneity. **Solution — virtual nodes:** each physical node is assigned **multiple tokens** (multiple positions on the ring). Benefits:

- When a node leaves, its load is **spread evenly** across many other nodes (not dumped on one neighbor).
- When a node joins, it picks up roughly-equal slices from many nodes.
- A more powerful machine can simply own **more virtual nodes**, matching capacity to hardware.

### Replication & the preference list

Each data item is replicated on **N** nodes. The coordinator (first node clockwise) replicates to the **N-1 successors**. The list of nodes responsible for a key is its **preference list**. Crucially, the list is built to **skip positions** so that the N replicas land on **N distinct physical nodes** (and, ideally, distinct racks/data centers) — otherwise virtual nodes could put multiple replicas on one machine.

### Quorums: N, R, W

Dynamo exposes tunable consistency through three numbers:

- **N** — replication factor (e.g., 3).
- **R** — minimum replicas that must respond to a *read*.
- **W** — minimum replicas that must acknowledge a *write*.

```
   If R + W > N  → read and write quorums overlap → a read sees the latest write
                   (under non-failure / strict-quorum conditions).
   Typical Amazon config: (N,R,W) = (3,2,2)  → balanced.
   (3,3,1) → fast writes, slow/consistent reads.
   (3,1,3) → fast reads, durable but slow writes.
```

A common production setting was **(3,2,2)**. Lowering W increases write availability/latency; lowering R does the same for reads. Note: because Dynamo uses **sloppy** quorums (below), even `R+W>N` does not *guarantee* strong consistency.

### Sloppy quorum + hinted handoff (temporary failures)

A **strict** quorum would block writes when the top-N nodes are unavailable — violating "always writeable." Instead, Dynamo uses a **sloppy quorum**: the write goes to the first N *healthy* nodes encountered while walking the ring (which may extend past the normal preference list).

If node B (a normal replica) is down, the write is sent to the next healthy node C with a **hint** in the metadata saying "this really belongs to B." C stores it in a separate local area and, once it detects B has recovered (via gossip), **hands the data off** to B and deletes its local copy. This is **hinted handoff** — it preserves the desired durability (W writes succeed) without waiting for the down node.

### Versioning with vector clocks (high availability for writes)

Because writes are accepted even during partitions, the same key can be updated independently on multiple nodes, producing **divergent versions**. Dynamo treats each version as an **immutable, causally-tagged object** and uses **vector clocks** to track causality.

A vector clock is a list of `(node, counter)` pairs:

```
   put(k, v1) handled by Sx  → D1 ([Sx,1])
   put(k, v2) on D1 by Sx    → D2 ([Sx,2])
   network partition:
     D2 updated on Sy         → D3 ([Sx,2],[Sy,1])
     D2 updated on Sz         → D4 ([Sx,2],[Sz,1])
   later read sees D3 and D4 → NEITHER dominates the other → CONFLICT
   reconciliation (app or LWW) → D5 ([Sx,3],[Sy,1],[Sz,1])
```

Rules:
- If clock A is component-wise ≤ clock B, then A is an **ancestor** of B and can be safely discarded (B subsumes it).
- If neither dominates, the versions are **concurrent / conflicting (siblings)** and must be **reconciled**.

**Reconciliation:**
- **Syntactic reconciliation** — the system resolves it automatically when one version dominates.
- **Semantic reconciliation** — pushed to the application. The shopping cart's classic resolution is a **union/merge** of the divergent carts: a re-added deleted item can resurface, but you never lose an "add." The business decided that a slightly-stale cart is far better than a dropped write.
- A simpler default many later systems use is **last-writer-wins (LWW)** based on timestamps — easy, but can silently drop concurrent updates.

To prevent vector clocks from growing without bound, Dynamo truncates the oldest `(node, timestamp)` entries beyond a threshold (with a small risk of inaccurate reconciliation).

### Anti-entropy with Merkle trees (permanent failures)

To keep replicas converging in the background — and to recover after hinted-handoff hints are lost — replicas run **anti-entropy** using **Merkle trees**. Each node maintains a Merkle (hash) tree per key range:

- Leaves hash individual keys; parents hash their children; the root summarizes the whole range.
- Two replicas compare **root hashes** first; if equal, the ranges are identical — *no data transferred*. If different, they recurse down only the differing subtrees, transferring only the keys that actually diverge.

This makes divergence detection cheap and minimizes data movement. (Downside: when a node's ranges change due to membership changes, trees must be recomputed.)

### Membership & failure detection — gossip

Membership is explicit (an administrator adds/removes nodes to avoid a partition being mistaken for permanent departure), but the membership view, token assignments, and node liveness propagate via a **gossip-based protocol**: each node periodically exchanges state with a random peer, so the whole ring converges on a consistent view. Failure detection is **local and decentralized** — a node marks a peer as down if it fails to respond, no central monitor required. Seed nodes prevent logical partitions of the gossip overlay.

## Key Innovations

1. **A coherent production recipe for AP storage** — the first widely-cited system to assemble consistent hashing + vector clocks + sloppy quorum + hinted handoff + Merkle anti-entropy + gossip into a working, large-scale store.
2. **Tunable consistency via (N, R, W)** — letting each service dial the consistency/latency/durability trade-off per use case.
3. **"Always writeable"** as an explicit design goal, with **conflict resolution moved to read time and to the application.**
4. **Decentralized, masterless, symmetric** design — no single point of failure, every node interchangeable.
5. **Operating against the 99.9th-percentile SLA** — engineering for the tail, not the mean.

## Data Model / APIs

Dynamo's interface is intentionally minimal — a binary-key/opaque-blob store, no schema, no joins, no secondary indexes:

```
get(key)      → ( list of objects [, context] )
                  may return MULTIPLE conflicting versions; client must reconcile
put(key, context, object)
                  context = opaque vector-clock metadata returned by a prior get
```

Example flow (shopping cart):

```
ctx_objs = get("cart:user42")
   # → returns one object, plus context (vector clock) "ctx"

merged = reconcile(ctx_objs)          # app merges siblings if any
merged.add(item)

put("cart:user42", ctx, merged)       # MUST pass ctx so Dynamo can version correctly
```

Keys are hashed with MD5 to place them on the ring. Values are opaque byte arrays (typically < 1 MB). The `context` is the mechanism that ties a `put` back to the version the client read — without it, every write would look concurrent.

## Trade-offs & Limitations

| Aspect | Trade-off |
|---|---|
| **Consistency** | Only **eventual consistency**. Reads may return stale or *multiple conflicting* versions. Not suitable where you need read-your-writes guarantees or invariants across keys. |
| **Conflict burden on apps** | Pushing semantic reconciliation to the client is powerful but **shifts complexity to every application team**. Getting merge logic right is hard; LWW defaults silently drop data. |
| **No transactions / no multi-key atomicity** | Single-key only. No range scans, no secondary indexes (in the original design). |
| **Sloppy quorum ≠ strong** | Even with `R+W>N`, sloppy quorums + hinted handoff mean a read can miss the latest write during failures. The guarantee is probabilistic, not absolute. |
| **Operational tuning** | Achieving good behavior requires per-service tuning of N/R/W, partitioning strategy, and Merkle-tree maintenance — significant operational surface. |
| **Vector clock growth** | Clocks can grow with the number of coordinating nodes; truncation risks losing causal accuracy. |

## Influence & Legacy

Dynamo is arguably the most influential storage paper of the NoSQL era. Its ideas seeded an entire generation of systems:

| System | What it took from Dynamo |
|---|---|
| **Apache Cassandra** | Ring, consistent hashing/v-nodes, gossip, tunable consistency, hinted handoff, read repair (but pairs it with a Bigtable-style storage engine and data model). |
| **Riak** | Almost a direct open-source reimplementation: rings, vector clocks, N/R/W, hinted handoff, Merkle anti-entropy. |
| **Voldemort** | LinkedIn's open-source Dynamo-clone KV store. |
| **Amazon DynamoDB** | The managed service — same lineage and SLA philosophy, but adds strong-consistency option, secondary indexes, and a fully-managed control plane. |

### Contrast with Bigtable

Dynamo and **Bigtable** (Google, OSDI 2006) are the two foundational NoSQL designs and sit at opposite corners:

| Dimension | **Dynamo** | **Bigtable** |
|---|---|---|
| CAP lean | **AP** — always available, eventually consistent | **CP** — strongly consistent per row, can be unavailable under partition |
| Topology | Decentralized, masterless P2P ring | Centralized: master + tablet servers + Chubby for coordination |
| Consistency | Eventual; client-side conflict resolution | Strong single-row atomicity; reads see latest write |
| Data model | Opaque key → blob | Sparse, multi-dimensional sorted map: `(row, column, timestamp) → value`, column families |
| Replication | Quorum across symmetric replicas | Replication handled below, in GFS/Colossus |
| Conflict handling | Vector clocks, siblings, reconciliation | None needed — single writer per tablet |
| Storage engine | Pluggable (BDB, MySQL) per node | LSM: memtable + SSTables + compaction (the engine Cassandra later adopted) |

Cassandra is famously the **synthesis** of the two: Dynamo's distribution/availability with Bigtable's storage engine and richer data model.

## Lessons for Architects

1. **Pick your CAP corner deliberately, per workload.** Dynamo's whole existence is a thesis: for shopping carts, availability beats consistency, and that is a *business* decision encoded in architecture. Don't default to strong consistency reflexively.
2. **Design for the tail.** SLAs at p99.9 force fundamentally different choices than designing for the mean. The worst case is the user experience that gets remembered.
3. **Treat failure as the normal case.** Hinted handoff, sloppy quorums, and gossip all assume nodes are *constantly* failing and recovering — so the system never has a special "failure mode," it just degrades gracefully.
4. **Make consistency a tunable knob, not a fixed property.** (N, R, W) let one engine serve many workloads. Expose the trade-off rather than baking in one answer.
5. **Conflict resolution has to live where the semantics live.** Only the application knows that two carts should be unioned. Generic stores can offer LWW, but real correctness often requires domain logic — budget for it.
6. **Detect divergence cheaply before you fix it.** Merkle trees are the lesson: compare summaries (hashes) before moving data; only transfer what actually differs. This pattern recurs everywhere (rsync, git, blockchain).
7. **Compose known techniques rather than inventing.** Dynamo's contribution was integration and production hardening, not a single new algorithm — a reminder that systems value comes from making proven pieces work together reliably at scale.
