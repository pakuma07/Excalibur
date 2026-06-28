# Architecture Patterns

## Introduction

An **architecture pattern** is a proven, reusable way of organizing the major pieces of a software system — its components, their responsibilities, and how they communicate. Where a *design pattern* (Strategy, Observer, ...) structures code *within* a module, an *architecture pattern* structures the system *as a whole*: where state lives, how requests flow, where the boundaries are, and how the system scales and evolves.

Choosing an architecture is about **trade-offs**, not "best." Every pattern optimizes for some qualities (scalability, decoupling, evolvability, simplicity) at the expense of others (complexity, consistency, operational burden). This document surveys the major patterns with diagrams, when to use each, and what you give up.

> **There is no free lunch.** Each pattern below moves complexity somewhere — into the network, into eventual consistency, into operations, or into the code. The skill is choosing *which* complexity your problem can best absorb.

---

## 1. Layered / N-Tier

**Idea:** Organize code into horizontal layers, each depending only on the layer below. Classic tiers: presentation → business logic → data access → database.

```
+------------------------+
|   Presentation (UI)    |
+------------------------+
|   Business Logic       |
+------------------------+
|   Data Access          |
+------------------------+
|   Database             |
+------------------------+
   (each layer calls only the one beneath it)
```

**When to use:** The default for most business applications and monoliths. Great when the domain maps cleanly to "screen → rules → storage."

**Trade-offs:** + Simple, familiar, easy onboarding, clear separation of concerns. − Can become a rigid monolith; the "sinkhole anti-pattern" (requests pass through layers doing nothing); deploys and scaling are all-or-nothing.

---

## 2. Client-Server

**Idea:** A central server provides resources/services; many clients request them. Asymmetric: the server is authoritative.

```
[Client] --request-->  [ Server ]  <--request-- [Client]
[Client] <-response--  (shared,    --response--> [Client]
                        stateful)
```

**When to use:** Almost all web and mobile apps, databases, email. The foundational model of the internet.

**Trade-offs:** + Centralized control, security, and data consistency. − The server is a bottleneck and single point of failure; scaling requires replication/load balancing.

---

## 3. Peer-to-Peer (P2P)

**Idea:** No central server — every node is both client and server, sharing resources directly with peers.

```
   [Peer]---------[Peer]
     |  \         /  |
     |   \       /   |
   [Peer]---[Peer]---[Peer]
```

**When to use:** File sharing (BitTorrent), blockchains, some real-time/mesh systems where decentralization and resilience matter.

**Trade-offs:** + No single point of failure, scales with participants, censorship-resistant. − Hard to secure, coordinate, and guarantee consistency; discovery and trust are difficult.

---

## 4. Event-Driven Architecture (EDA)

**Idea:** Components communicate by **producing and consuming events** through a broker, rather than calling each other directly. Producers emit events ("OrderPlaced") and don't know who, if anyone, consumes them. Consumers react asynchronously.

```
[Order Svc] --OrderPlaced--> [ Event Broker ] --> [Inventory Svc]
                             (Kafka/RabbitMQ)  --> [Email Svc]
                                               --> [Analytics Svc]
```

Two common topologies:
- **Broker (pub/sub):** events fan out to interested subscribers; highly decoupled.
- **Mediator:** a central orchestrator coordinates a multi-step workflow.

**When to use:** Real-time pipelines, decoupled microservices, fan-out notifications, anything where producers and consumers should evolve independently.

**Trade-offs:** + Extreme decoupling, scalability, easy to add new consumers, natural async. − Hard to reason about end-to-end flow; debugging and testing are harder; eventual consistency; you must handle duplicate/out-of-order events (idempotency).

---

## 5. CQRS (Command Query Responsibility Segregation)

**Idea:** Split the model that **writes** data (commands) from the model that **reads** it (queries). Each side gets a data model optimized for its job, often backed by separate stores kept in sync via events.

```
            commands (writes)            queries (reads)
[Client] ----------------> [Write Model] ... events ...> [Read Model] <---------------- [Client]
                           (normalized,                  (denormalized,
                            transactional)                fast for queries)
```

**When to use:** Read and write workloads differ hugely (e.g., read-heavy systems), complex domains, or when paired with event sourcing. Often only applied to a subsystem, not the whole app.

**Trade-offs:** + Independently scalable/optimized reads and writes; read models tailored per use case. − Significant added complexity; the read side is eventually consistent with the write side; two models to maintain. Don't apply it where a simple CRUD model suffices.

---

## 6. Event Sourcing

**Idea:** Instead of storing current state, store the **full sequence of events** that produced it. Current state is derived by replaying events. The event log is the source of truth.

```
Events (append-only log):
  AccountOpened(balance=0)
  Deposited(100)
  Withdrew(30)
=> replay => current balance = 70
```

**When to use:** When you need a complete audit trail, temporal queries ("what was the state last Tuesday?"), or to rebuild/repair state. Pairs naturally with CQRS (events feed read models).

**Trade-offs:** + Perfect audit log, time-travel, can rebuild any projection, debug by replay. − Event schema evolution is hard; replaying long histories needs **snapshots**; querying current state requires projections; a real mindset shift for the team.

---

## 7. Saga (Distributed Transactions)

**Idea:** Manage a transaction that spans multiple services **without** a distributed lock/2PC. A saga is a sequence of local transactions; if one step fails, **compensating transactions** undo the prior steps. *(Cross-reference: see doc 14 on distributed transactions / consistency.)*

```
Order saga:
  1. Create Order        -> ok
  2. Reserve Payment     -> ok
  3. Reserve Inventory   -> FAIL
  => compensate: Refund Payment, Cancel Order
```

Two coordination styles:
- **Choreography:** each service listens for events and emits the next; no central coordinator.
- **Orchestration:** a central saga orchestrator tells each service what to do and triggers compensations.

**When to use:** Business processes spanning multiple microservices/databases where you need data consistency without locking across services.

**Trade-offs:** + Maintains consistency across services without 2PC; resilient. − No isolation (intermediate states are visible); you must design compensating actions for every step; complex failure handling; only **eventual** consistency.

---

## 8. Sidecar / Ambassador

**Idea:** Attach a helper process (the **sidecar**) alongside each service instance to handle cross-cutting concerns — logging, TLS, metrics, retries — without changing the service. An **ambassador** is a sidecar specialized for *outbound* network access (proxying calls to remote services).

```
+--------- Pod / Host ----------+
|  [ Main Service ] <--local--> [ Sidecar ]  --> network
+-------------------------------+   (proxy: TLS, retries, metrics)
```

**When to use:** Service meshes (Envoy/Istio), polyglot microservices that need uniform networking/observability, legacy apps you can't modify.

**Trade-offs:** + Language-agnostic reuse of infra concerns; keeps services focused on business logic; upgrade infra independently. − Extra resource overhead and latency per instance; operational complexity of the mesh.

---

## 9. Strangler Fig

**Idea:** Incrementally replace a legacy system by routing traffic through a façade that gradually redirects more functionality to new services — until the old system is "strangled" and removed. Named after the strangler fig vine that grows around a tree.

```
            [ Façade / Router ]
            /                 \
  (old routes)              (migrated routes)
  [ Legacy Monolith ]       [ New Service(s) ]
   (shrinks over time)       (grows over time)
```

**When to use:** Migrating a large legacy system to a new architecture safely, without a risky "big bang" rewrite.

**Trade-offs:** + Low-risk, incremental, deliver value continuously, easy rollback per slice. − The façade and dual-running period add temporary complexity; can drag on if not driven to completion.

---

## 10. Backends-for-Frontends (BFF)

**Idea:** Instead of one general-purpose API serving all clients, build a **dedicated backend per frontend** (web, iOS, Android), each tailored to that client's needs.

```
[ Web App ] -> [ Web BFF ] -\
[ iOS App ] -> [ iOS BFF ] ---> [ Downstream Microservices ]
[Android]   -> [Android BFF]-/
```

**When to use:** Multiple frontends with very different data/shape/latency needs; when a one-size-fits-all API forces awkward over-/under-fetching.

**Trade-offs:** + Each client gets an optimized, simple API; teams own their BFF; reduces chatty clients. − Code duplication across BFFs; more services to maintain; risk of business logic leaking into the BFF.

---

## 11. API Gateway

**Idea:** A single entry point in front of many backend services that handles cross-cutting concerns: routing, authentication, rate limiting, TLS termination, request aggregation, and protocol translation.

```
            +---------------------+
[Clients]-->|     API Gateway     |--> [Service A]
            | auth, rate-limit,   |--> [Service B]
            | routing, aggregation|--> [Service C]
            +---------------------+
```

**When to use:** Almost any microservices system — it gives clients one stable endpoint and centralizes policy. (BFF is essentially a per-client specialization of this idea.)

**Trade-offs:** + Centralizes auth/rate-limiting/routing; clients see one endpoint; hides internal topology. − Can become a bottleneck and single point of failure (must be HA); risk of becoming a bloated "god" component if it absorbs business logic.

---

## 12. Microkernel / Plugin

**Idea:** A minimal **core** provides only essential functionality; everything else is added via **plugins** that conform to a defined contract. The core knows nothing about specific plugins.

```
        +-------------------+
        |   Core (kernel)   |
        +-------------------+
         |     |     |     |
      [Plugin][Plugin][Plugin]   (independently developed/deployed)
```

**When to use:** Products with a stable core and evolving, customer-specific features: IDEs (VS Code), browsers, ETL tools, e-commerce platforms with extensions.

**Trade-offs:** + Highly extensible; third parties add features without touching the core; features ship independently. − Plugin contract/registry must be carefully designed and versioned; plugin interactions can be hard to test; not built for high-throughput scaling on its own.

---

## 13. Lambda and Kappa (Data Architectures)

These address **big-data processing**: how to serve both real-time and historical analytics.

**Lambda:** Run two parallel paths — a **batch layer** (accurate, recomputed over all data) and a **speed/stream layer** (fast, approximate, recent data) — and merge them at query time in a serving layer.

```
                 +--> [ Batch Layer ]  --\
[ Raw Data ] ----|                         >--> [ Serving Layer ] --> queries
                 +--> [ Speed Layer ]  --/
```

**Kappa:** Simplify by dropping the batch layer — treat **everything as a stream**. Reprocess history by replaying the event log through the same stream code.

```
[ Event Log ] --> [ Stream Processing ] --> [ Serving Layer ] --> queries
   (replayable)        (one code path)
```

**When to use:** Lambda when you genuinely need batch-grade accuracy plus low-latency views and can afford two codebases. Kappa when a single streaming codebase suffices and you want to avoid maintaining batch + stream logic in parallel — increasingly the default.

**Trade-offs:** Lambda: + robust, accurate; − **two codebases** to keep in sync (its central pain point). Kappa: + one codebase, simpler; − relies on a durable, replayable log; reprocessing huge histories can be costly.

---

## 14. Hexagonal (Ports and Adapters)

**Idea:** Put the **domain/business logic at the center**, isolated from the outside world. The core defines **ports** (interfaces); the outside connects through **adapters** that implement those ports. The application doesn't know whether it's driven by HTTP or a CLI, or whether it persists to Postgres or memory.

```
        [HTTP Adapter] [CLI Adapter]   <- driving (inbound) adapters
                 \        /
            +---- ports (in) ----+
            |    Domain / Core   |   (pure business logic, no I/O deps)
            +---- ports (out) ---+
                 /        \
        [Postgres Adapter] [Email Adapter]   <- driven (outbound) adapters
```

**When to use:** Domain-rich applications where you want business logic testable in isolation and infrastructure (DB, messaging, UI) swappable without touching the core. Underpins "clean"/"onion" architectures.

**Trade-offs:** + Highly testable (mock the adapters); infrastructure-agnostic core; tech choices deferred and replaceable. − More upfront abstraction and boilerplate; overkill for simple CRUD apps.

---

## Summary Comparison

| Pattern | Primary goal | Communication | Coupling | When to use | Main cost |
| --- | --- | --- | --- | --- | --- |
| Layered / N-tier | Organize a monolith | In-process calls | Medium | Most business apps | Rigidity; all-or-nothing deploy |
| Client-server | Centralized service | Request/response | Medium | Web/mobile, DBs | Server is bottleneck/SPOF |
| Peer-to-peer | Decentralization | Direct peer | Low | File sharing, blockchain | Hard to secure/coordinate |
| Event-driven | Decouple via events | Async events | Very low | Real-time, reactive systems | Hard to trace; eventual consistency |
| CQRS | Optimize read vs write | Commands/queries | Medium | Read/write skew, complex domains | Two models; eventual consistency |
| Event sourcing | Audit / rebuildable state | Append-only events | Medium | Audit, time-travel | Schema evolution; needs snapshots |
| Saga | Cross-service consistency | Events/orchestration | Low–medium | Distributed transactions | Compensations; no isolation |
| Sidecar / ambassador | Reuse infra concerns | Local proxy | Low | Service mesh, polyglot | Per-instance overhead |
| Strangler fig | Safe legacy migration | Routing façade | n/a | Incremental rewrites | Temporary dual-running |
| BFF | Per-client API | Request/response | Medium | Many distinct frontends | Duplication across BFFs |
| API gateway | Single entry / policy | Request/response | Medium | Microservices front door | Bottleneck/SPOF if not HA |
| Microkernel / plugin | Extensibility | Plugin contract | Low | Extensible products (IDEs) | Contract design; testing combos |
| Lambda / Kappa | Big-data analytics | Batch + stream / stream | n/a | Real-time + historical analytics | Two codebases (Lambda) / replay cost (Kappa) |
| Hexagonal | Isolate domain logic | Ports & adapters | Low | Domain-rich, testable cores | Abstraction overhead |

---

## Key Takeaways

- **Architecture is the art of choosing trade-offs**, not finding a single "best." Each pattern relocates complexity (to the network, to eventual consistency, to operations, or to code) — pick the one whose costs your problem tolerates.
- **Start simple.** A layered monolith with a clear client-server boundary serves most systems well. Reach for distributed patterns (EDA, CQRS, saga) only when scale, team structure, or domain complexity justifies their cost.
- **Decoupling patterns (event-driven, CQRS, event sourcing, saga) almost always trade away strong consistency for scalability and flexibility** — you accept eventual consistency, idempotency handling, and harder debugging.
- **Structural patterns (gateway, BFF, sidecar, microkernel, hexagonal, strangler fig) manage boundaries and evolution** — they let independent teams, clients, and infrastructure concerns change without stepping on each other.
- **Patterns compose.** Real systems layer them: an API gateway in front of event-driven microservices, each built hexagonally, with CQRS + event sourcing in one bounded context, sagas across services, and a strangler fig migrating off the legacy monolith.
- **Lambda vs Kappa** is the data-side echo of the same theme: Lambda buys accuracy with two codebases; Kappa buys simplicity by betting on a replayable stream.
