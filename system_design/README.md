# System Design 🏗️

A complete, from-scratch system design reference covering **fundamentals**, **enterprise-grade architectures**, and **interview preparation** with detailed solutions and code.

This material is language-agnostic; code snippets use **Python** for algorithms, **SQL** for schemas, and pseudocode/config where clearer. Diagrams use Mermaid/ASCII.

---

## 📁 Structure

### 1. [`concepts/`](concepts/README.md) — Fundamentals from scratch
Every building block of modern distributed systems, explained from first principles with examples and code: networking, scalability, load balancing, caching, databases, replication & sharding, CAP/consistency, consistent hashing, messaging & streaming, API design, microservices, storage & CDN, search/indexing, distributed-systems theory (consensus, transactions), reliability, observability, security, probabilistic data structures, rate limiting, and architecture patterns.

### 2. [`ent_system_design/`](ent_system_design/README.md) — Enterprise-level system design
Real enterprise architecture: patterns (DDD, hexagonal, event-driven, CQRS), enterprise integration, multi-tenancy, high availability & disaster recovery, identity & access management, data platforms, compliance & governance, cloud architecture, observability/SRE, and modernization/migration — plus **10 end-to-end enterprise scenarios** (banking/payments, e-commerce, healthcare, insurance, supply chain, trading, telecom billing, CRM, ERP/HR, streaming/media) in [`scenarios/`](ent_system_design/scenarios/).

### 3. [`interv/`](interv/README.md) — Interview scenarios & solutions
A repeatable interview framework plus **20+ classic system-design questions**, each with requirements, capacity estimation, API design, data model, high-level design, deep dives, trade-offs, and **working code** for the key components (URL shortener, rate limiter, key-value store, ID generator, web crawler, notifications, news feed, chat, Twitter, Instagram, YouTube, Google Drive, typeahead, Uber, distributed cache, payments, Ticketmaster, proximity service, job scheduler, ad aggregator, leaderboard, Google Maps, and an OOD parking lot).

### 4. [`advanced/`](advanced/README.md) — Staff/Principal depth layer
The deep internals, theory, and history that distinguish staff/principal engineers: **storage-engine internals** (LSM-tree/B-tree/WAL/MVCC, with a working mini-LSM), **performance & queueing theory** (Little's Law, Universal Scalability Law, Amdahl), **tail latency** (the "tail at scale", hedged requests), **data encoding & schema evolution** (Protobuf/Avro/Thrift), **advanced clocks & hashing** (HLC, TrueTime, rendezvous & jump hashing), **modern networking** (QUIC/HTTP3, gRPC, service mesh, eBPF), **security & threat modeling** (STRIDE, supply chain, crypto), **ML/AI infrastructure** (feature stores, model serving, vector DBs, RAG), and **distributed consensus deep** (FLP, Paxos, Raft, BFT, linearizability). Includes [`case_studies/`](advanced/case_studies/README.md) — faithful deep-dives of the seminal systems: **GFS, MapReduce, Bigtable, Spanner, Dynamo, Kafka, Cassandra, ZooKeeper/Chubby, Dapper, S3, Borg/Kubernetes**.

### 5. [`staff_principal/`](staff_principal/README.md) — Technical leadership & process
The non-coding skills that define the staff/principal role: **the staff-engineer role & archetypes**, writing **design docs / RFCs** (+ Amazon 6-pager/PR-FAQ), **Architecture Decision Records (ADRs)**, **architecture review & trade-off frameworks**, **technical strategy & vision** (Rumelt's kernel), **platform thinking & paved roads**, **build-vs-buy**, **capacity / cost / TCO / FinOps**, and **leading large-scale migrations** — with copy-pasteable templates and worked examples.

---

## 🎯 How to Use

| Goal | Start here |
|------|-----------|
| Learn the fundamentals | [`concepts/`](concepts/README.md) in order |
| Design real enterprise systems | [`ent_system_design/`](ent_system_design/README.md) |
| Prepare for interviews | [`interv/00_interview_framework.md`](interv/00_interview_framework.md), then the problems |
| Go deep for staff/principal | [`advanced/`](advanced/README.md) + [`advanced/case_studies/`](advanced/case_studies/README.md) |
| Grow into the staff/principal *role* | [`staff_principal/`](staff_principal/README.md) |

## 📐 The Core Trade-offs (the recurring themes)

- **Consistency vs Availability** (CAP) — and latency (PACELC)
- **Latency vs Throughput**
- **Read-heavy vs Write-heavy** workloads
- **Strong vs Eventual** consistency
- **Normalization vs Denormalization**
- **Vertical vs Horizontal** scaling
- **Synchronous vs Asynchronous** communication
- **Stateful vs Stateless** services
- **Cost vs Performance vs Reliability**

> There is rarely one "right" answer in system design — only trade-offs justified by the requirements. Always start from requirements and constraints.
