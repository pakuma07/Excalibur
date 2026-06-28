# 00 — System Design Interview Framework

A reusable, repeatable framework for any system-design interview. The goal of a
system-design round is **not** to produce the "correct" architecture (there isn't
one) — it is to demonstrate **structured thinking**, **sound trade-off reasoning**,
and **breadth + depth**. Drive the conversation; don't wait to be quizzed.

A typical 45–60 minute round budgets roughly:

| Phase | Time | Goal |
|-------|------|------|
| 1. Clarify requirements / scope | 3–5 min | Agree on *what* to build |
| 2. Functional & non-functional reqs | 3–5 min | Pin down behavior + SLOs |
| 3. Back-of-envelope estimation | 3–5 min | Size the system (QPS, storage) |
| 4. API design | 3–5 min | Define the contract |
| 5. High-level design + data model | 8–12 min | Boxes, arrows, schema |
| 6. Deep dives | 12–18 min | 2–3 interesting subproblems |
| 7. Bottlenecks & trade-offs | 3–5 min | Justify choices, find weak spots |
| 8. Wrap up | 2 min | Recap, future work |

---

## The 8 Steps

### 1. Clarify requirements / scope
Never start drawing. Spend the first few minutes turning a vague prompt
("design Twitter") into a bounded problem. Ask:
- **Who** are the users and how many? (consumers vs. internal)
- **What** are the 2–4 core features in scope? Explicitly cut the rest.
- **Read vs. write ratio?** Latency-sensitive or throughput-sensitive?
- Any constraint that changes the design? (global vs. single-region, mobile,
  strong consistency, cost ceiling, compliance like PCI/GDPR.)

Write the agreed scope in a corner of the board. This protects you later:
"I've descoped DMs as we agreed."

### 2. Functional & non-functional requirements
- **Functional** = what the system *does* (e.g., "shorten a URL", "redirect").
  Phrase as user-visible capabilities, 4–6 bullets max.
- **Non-functional** = qualities/SLOs:
  - **Availability** (e.g., 99.99% → reads should never fail).
  - **Latency** (e.g., p99 < 100 ms for redirect).
  - **Consistency** (strong vs. eventual — pick per-feature).
  - **Scalability / durability / security / cost.**

State the CAP posture early: *"reads favor availability, eventual consistency is
fine; the counter must be strongly consistent."*

### 3. Back-of-envelope capacity estimation
Order-of-magnitude only. Establishes whether you need 1 box or 1000.
1. **Traffic**: DAU × actions/user/day → requests/day → **QPS** (avg & peak).
2. **Storage**: objects/day × bytes/object × retention → TB, then × replication.
3. **Bandwidth**: QPS × payload size → MB/s.
4. **Memory / cache**: apply 80/20 → cache the hot 20%.

Round aggressively. `1 day ≈ 100k seconds`. Peak ≈ 2–3× average.

### 4. API design
Define the contract before internals — it forces clarity on inputs/outputs.
- Prefer **REST** for CRUD-ish; **gRPC** for internal/low-latency; **WebSocket/SSE**
  for push (chat, feeds).
- Show 2–4 key endpoints with method, path, params, response, status codes.
- Mention auth (token in header), pagination (cursor > offset), idempotency keys
  for writes/payments, rate-limit headers.

### 5. High-level design + data model
- Draw **client → LB → service(s) → cache → DB**, plus async path
  (queue + workers) and object store / CDN where relevant.
- Keep it ~6–10 boxes. Name real tech (NGINX, Kafka, Redis, Cassandra, S3).
- **Data model**: pick SQL vs. NoSQL *and justify*. Show the key tables/entities,
  primary keys, and **how you'll shard** (the shard key is often the whole ballgame).

### 6. Deep dives
This is where you earn the rating. Pick the 2–3 *interesting* subproblems —
the ones unique to this problem, not generic CRUD. Examples:
- URL shortener → ID generation strategy, redirect semantics.
- Chat → delivery/ordering, presence, fan-out, offline.
- KV store → consistent hashing, quorum, conflict resolution.

For each: state the problem, give 2–3 options, pick one, justify with the reqs.

### 7. Identify bottlenecks & justify trade-offs
Proactively poke holes in your own design:
- Single points of failure (SPOF) → add redundancy.
- Hot shards / celebrity problem → fan-out-on-read for hot keys.
- Thundering herd / cache stampede → request coalescing, jittered TTL.
- Backpressure when downstream is slow → queues, load shedding, circuit breakers.
Frame everything as a trade-off tied to the requirements from step 2.

### 8. Wrap up
30-second recap: how the design meets each requirement, the biggest risk, and
what you'd build next (monitoring, multi-region, cost optimization).

---

## How to drive the conversation
- **Think out loud.** The interviewer scores reasoning, not silence.
- **State assumptions** and move on; don't stall waiting for permission.
- **Signpost**: "I'll now estimate capacity, then design APIs."
- **Time-box**: if a deep dive runs long, say "I'll park this and return if time."
- **Invite feedback**: "Does this scope look right before I go deeper?"
- **Be decisive, then flexible**: pick an option, justify it, but yield to a
  better idea from the interviewer.

## Common mistakes
- Jumping to architecture before clarifying scope.
- Over-engineering on day one (microservices, multi-region) when not asked.
- Ignoring non-functional requirements / never stating consistency posture.
- Hand-wavy estimation ("it'll be big") with no numbers.
- One giant DB with no sharding/replication story.
- Forgetting the async path (everything synchronous).
- No failure handling — no replicas, no retries, no idempotency.
- Going silent; or conversely, rambling without a structure.
- Not labeling diagram arrows (sync vs. async, protocol).

---

## Cheat sheet — building blocks

| Block | Use it for | Real tech | Watch out for |
|-------|-----------|-----------|---------------|
| **Load balancer** | Spread traffic, health checks, TLS termination | NGINX, HAProxy, ALB/ELB, Envoy | L4 vs L7; sticky sessions break statelessness |
| **API gateway** | Auth, rate limit, routing, aggregation | Kong, AWS API GW, Envoy | Can become a SPOF/bottleneck |
| **Cache** | Cut DB load & latency for hot data | Redis, Memcached | Invalidation, stampede, eviction policy |
| **CDN** | Edge-cache static & media globally | CloudFront, Akamai, Fastly | Cache key design, purge lag |
| **Message queue** | Decouple, buffer spikes, async work | Kafka, RabbitMQ, SQS | Ordering, exactly-once, DLQs, lag |
| **Relational DB** | Transactions, joins, strong consistency | PostgreSQL, MySQL | Scales up, not out, easily |
| **Wide-column / NoSQL** | Massive write scale, flexible schema | Cassandra, DynamoDB, ScyllaDB | Eventual consistency, no joins |
| **Document DB** | Nested/flexible records | MongoDB | Hot keys, unbounded docs |
| **Search index** | Full-text, faceted search | Elasticsearch, OpenSearch | Not a source of truth; index lag |
| **Blob/object store** | Large immutable files, media | S3, GCS | Not for low-latency small reads (use CDN) |
| **Time-series DB** | Metrics, IoT, analytics | InfluxDB, TimescaleDB | Cardinality explosions |
| **Coordination** | Leader election, config, locks | ZooKeeper, etcd | Don't put it on the hot path |

### Scaling patterns
- **Vertical** (bigger box) → simple, limited ceiling.
- **Horizontal** (more boxes) → needs statelessness + LB.
- **Replication** → read replicas (read scaling) and failover (availability).
- **Sharding/partitioning** → range, hash, or consistent hashing. Pick a shard
  key with high cardinality and even distribution; beware hot shards.
- **CQRS** → split read/write models when their needs diverge.
- **Async everything non-critical** → queues + workers; return fast, process later.

---

## Estimation reference

### Powers of 2 / data sizes
| Power | ≈ | Name |
|------|-----|------|
| 2^10 | 1 thousand | 1 KB |
| 2^20 | 1 million | 1 MB |
| 2^30 | 1 billion | 1 GB |
| 2^40 | 1 trillion | 1 TB |
| 2^50 | 1 quadrillion | 1 PB |

Typical object sizes: char/int ≈ 1–8 B, UUID ≈ 16 B, short URL row ≈ 100 B–1 KB,
tweet ≈ 300 B, web page ≈ 100 KB–2 MB, photo ≈ 200 KB–2 MB, 1 min 1080p video ≈ 50 MB.

### Time → QPS
- 1 day ≈ 86,400 s ≈ **10^5 s** (round up).
- QPS = requests per day / 10^5.
- **Peak QPS ≈ 2–3 × average.**

Worked example: 100M DAU × 10 reads/day = 1B reads/day ÷ 10^5 ≈ **10k QPS avg**,
~25k QPS peak.

### Latency numbers every engineer should know (ns → human scale)
| Operation | Latency |
|-----------|---------|
| L1 cache reference | 0.5 ns |
| Branch mispredict | 5 ns |
| L2 cache reference | 7 ns |
| Mutex lock/unlock | 25 ns |
| Main memory reference | 100 ns |
| Compress 1 KB (Zippy) | 3 µs |
| Send 1 KB over 1 Gbps | 10 µs |
| Read 4 KB from SSD | 150 µs |
| Round trip within datacenter | 0.5 ms |
| Read 1 MB sequentially from memory | 0.25 ms |
| Read 1 MB sequentially from SSD | 1 ms |
| Disk seek (HDD) | 10 ms |
| Read 1 MB sequentially from HDD | 30 ms |
| Round trip CA ↔ Netherlands | 150 ms |

Takeaways: **memory ≫ SSD ≫ disk**; **stay in one datacenter** when you can;
a cross-continent RTT (~150 ms) dominates everything — cache at the edge.

### Availability ("nines") → downtime/year
| SLA | Downtime/year |
|-----|---------------|
| 99% | 3.65 days |
| 99.9% | 8.76 hours |
| 99.99% | 52.6 minutes |
| 99.999% | 5.26 minutes |

### Rules of thumb
- A single commodity SQL box: ~10k–50k simple QPS, low TB.
- Redis: ~100k+ ops/s per node.
- Kafka: ~MB–GB/s per broker with partitioning.
- Apply **80/20**: 20% of data serves 80% of traffic → size the cache for the hot 20%.
