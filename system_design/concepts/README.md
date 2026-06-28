# System Design Concepts — From Scratch

A language-agnostic, from-first-principles tour of the building blocks of large-scale systems. Each file teaches one concept to someone new: what problem it solves, how it works, diagrams, concrete examples, code/config snippets, comparison tables, trade-offs, and key takeaways. Code samples use Python and SQL where they help.

These notes are vendor-neutral but use real technologies (NGINX, Redis, PostgreSQL, Kafka, etc.) and realistic numbers so you can reason about actual systems.

---

## Concept Index (01–20)

| # | File | One-line description |
|---|------|----------------------|
| 01 | [01_fundamentals.md](01_fundamentals.md) | Client-server model, request lifecycle, DNS, IP/TCP/UDP, HTTP/HTTPS/TLS, WebSockets, REST vs gRPC, and back-of-the-envelope estimation. |
| 02 | [02_scalability.md](02_scalability.md) | Vertical vs horizontal scaling, stateless vs stateful services, sessions, scaling reads vs writes, single-server → distributed evolution, capacity planning. |
| 03 | [03_load_balancing.md](03_load_balancing.md) | L4 vs L7 load balancing, algorithms, health checks, sticky sessions, GSLB/anycast, LB high availability, NGINX/HAProxy/ELB. |
| 04 | [04_caching.md](04_caching.md) | Cache layers, patterns (cache-aside/read-through/write-through/write-back), eviction, invalidation, stampede protection, Redis vs Memcached. |
| 05 | [05_databases.md](05_databases.md) | Relational vs NoSQL families, indexing, transactions/ACID, normalization, query patterns, choosing a data store. |
| 06 | [06_replication_sharding.md](06_replication_sharding.md) | Leader-follower & multi-leader replication, sync vs async, partitioning/sharding strategies, rebalancing, hotspots. |
| 07 | [07_cap_consistency.md](07_cap_consistency.md) | CAP and PACELC theorems, consistency models (strong → eventual), quorums, isolation levels. |
| 08 | [08_consistent_hashing.md](08_consistent_hashing.md) | The hash ring, virtual nodes, why it minimizes reshuffling, use in caches and sharded stores. |
| 09 | [09_messaging_streaming.md](09_messaging_streaming.md) | Queues vs logs, pub/sub, Kafka vs RabbitMQ, delivery semantics, backpressure, event-driven design. |
| 10 | [10_api_design.md](10_api_design.md) | REST/gRPC/GraphQL, versioning, pagination, idempotency, error contracts, API gateways. |
| 11 | [11_microservices.md](11_microservices.md) | Monolith vs microservices, service boundaries, inter-service comms, saga/transactions, service mesh. |
| 12 | [12_storage_cdn.md](12_storage_cdn.md) | Block/file/object storage, blob stores, CDNs, edge caching, media delivery. |
| 13 | [13_search_indexing.md](13_search_indexing.md) | Inverted indexes, tokenization, ranking, Elasticsearch/Lucene, search system architecture. |
| 14 | [14_distributed_systems.md](14_distributed_systems.md) | Clocks, consensus (Raft/Paxos), leader election, distributed transactions, failure detection. |
| 15 | [15_reliability_availability.md](15_reliability_availability.md) | SLA/SLO/SLI, redundancy, failover, retries, circuit breakers, graceful degradation, the nines. |
| 16 | [16_observability.md](16_observability.md) | Logs, metrics, traces, the RED/USE methods, alerting, SLO-based monitoring. |
| 17 | [17_security.md](17_security.md) | AuthN/AuthZ, OAuth2/OIDC/JWT, encryption in transit/at rest, secrets, common attacks & defenses. |
| 18 | [18_probabilistic_structures.md](18_probabilistic_structures.md) | Bloom filters, Count-Min Sketch, HyperLogLog — trading accuracy for space. |
| 19 | [19_rate_limiting.md](19_rate_limiting.md) | Token bucket, leaky bucket, fixed/sliding window, distributed rate limiting, throttling. |
| 20 | [20_architecture_patterns.md](20_architecture_patterns.md) | Layered, event-driven, CQRS, event sourcing, hexagonal, strangler fig, and common system blueprints. |

---

## Suggested Reading Order

The files are numbered in a deliberate dependency order — reading 01 → 20 builds knowledge progressively. If you want a faster path tailored to a goal:

**Foundations (read first, in order):**
`01_fundamentals` → `02_scalability` → `03_load_balancing` → `04_caching` → `05_databases`

**Data at scale:**
`06_replication_sharding` → `07_cap_consistency` → `08_consistent_hashing`

**Communication & services:**
`09_messaging_streaming` → `10_api_design` → `11_microservices`

**Specialized infrastructure:**
`12_storage_cdn` → `13_search_indexing` → `18_probabilistic_structures` → `19_rate_limiting`

**Operating systems in production:**
`14_distributed_systems` → `15_reliability_availability` → `16_observability` → `17_security`

**Putting it together:**
`20_architecture_patterns`

```mermaid
graph LR
    A[01 Fundamentals] --> B[02 Scalability]
    B --> C[03 Load Balancing]
    C --> D[04 Caching]
    D --> E[05 Databases]
    E --> F[06 Replication & Sharding]
    F --> G[07 CAP & Consistency]
    G --> H[08 Consistent Hashing]
    H --> I[09 Messaging]
    I --> J[10 API Design]
    J --> K[11 Microservices]
    K --> L[12–19 Specialized]
    L --> M[20 Architecture Patterns]
```

---

## Latency Numbers Every Engineer Should Know

Approximate, order-of-magnitude figures (originally popularized by Jeff Dean / Peter Norvig). Hardware improves, but the *ratios* are what matter — they're stable across years and are the backbone of back-of-the-envelope estimation.

| Operation | Time | Relative scale |
|-----------|------|----------------|
| L1 cache reference | 0.5 ns | — |
| Branch mispredict | 5 ns | 10× L1 |
| L2 cache reference | 7 ns | 14× L1 |
| Mutex lock/unlock | 25 ns | — |
| Main memory (RAM) reference | 100 ns | 200× L1 |
| Compress 1 KB with Snappy/Zstd | ~2,000 ns (2 µs) | — |
| Send 1 KB over 1 Gbps network | ~10,000 ns (10 µs) | — |
| Read 4 KB randomly from SSD | ~150,000 ns (150 µs) | 1,500× RAM |
| Read 1 MB sequentially from RAM | ~250,000 ns (250 µs) | — |
| Round trip within same datacenter | ~500,000 ns (0.5 ms) | — |
| Read 1 MB sequentially from SSD | ~1,000,000 ns (1 ms) | 4× RAM-1MB |
| Disk seek (spinning HDD) | ~10,000,000 ns (10 ms) | 20× DC round trip |
| Read 1 MB sequentially from HDD | ~20,000,000 ns (20 ms) | — |
| Network round trip CA → Netherlands → CA | ~150,000,000 ns (150 ms) | — |

**Mental shortcuts:**

- **L1 ≈ 1 ns, RAM ≈ 100 ns, SSD random ≈ 100 µs, DC round trip ≈ 0.5 ms, disk seek ≈ 10 ms, cross-continent ≈ 150 ms.**
- Memory is ~100,000× faster than a disk seek. This is *why* we cache.
- A cross-region round trip (~150 ms) dwarfs almost everything else — minimize chatty cross-region calls.
- Reading sequentially is dramatically faster than random access on every storage tier.

### Powers of two (for storage/throughput math)

| Power | Exact | Approx | Name |
|-------|-------|--------|------|
| 2^10 | 1,024 | 1 thousand | 1 KB |
| 2^20 | 1,048,576 | 1 million | 1 MB |
| 2^30 | 1,073,741,824 | 1 billion | 1 GB |
| 2^40 | 1.1 × 10^12 | 1 trillion | 1 TB |
| 2^50 | 1.13 × 10^15 | 1 quadrillion | 1 PB |

### Time, for QPS math

| Unit | Seconds |
|------|---------|
| 1 day | 86,400 (≈ 10^5) |
| 1 month | ~2.6 × 10^6 |
| 1 year | ~3.15 × 10^7 |

> Rule of thumb: **1 million requests/day ≈ 12 requests/second** (86,400 s/day). See `01_fundamentals.md` for full worked estimation examples.

---

## How to use these notes

1. Skim the diagram and "Key Takeaways" first to get the shape.
2. Read top to bottom for depth.
3. Re-read "When to use / trade-offs" before a design interview or design review — that's where judgment lives.

Each document is self-contained; cross-references point you to the deeper treatment elsewhere.
