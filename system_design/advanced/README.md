# Advanced System Design — Staff/Principal Deep-Dives 🧠

This is the **staff/principal depth layer** of the [system-design reference](../README.md). Where [`concepts/`](../concepts/README.md) builds each topic from scratch for senior engineers, this folder goes **one level deeper**: the underlying theory, the seminal papers, the math, the failure modes, and **working Python** for the core algorithms.

Assume you already know *what* a B-tree, a queue, or a consensus protocol is. These docs explain *how they actually work*, *why they behave the way they do at scale*, and *what you trade off* when you reach for one.

> Audience: engineers who design and operate systems at scale, review architecture, and need to reason precisely about internals and tail behavior — not just name-drop the pattern.

---

## 📚 The nine deep-dives

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [`01_storage_engines.md`](01_storage_engines.md) | Database storage internals from scratch: log-structured vs page-oriented, B/B+trees, LSM-trees + compaction + bloom filters, WAL & crash recovery, MVCC & snapshot isolation, index types, page cache. Includes a working mini-LSM in Python. |
| 02 | [`02_performance_queueing_theory.md`](02_performance_queueing_theory.md) | The math of performance: Little's Law, utilization vs latency (the hockey stick), M/M/1 & M/M/c, Amdahl's Law, the Universal Scalability Law (with a Python fit), coordinated omission, percentiles, capacity planning. |
| 03 | [`03_tail_latency.md`](03_tail_latency.md) | Tail latency at scale: why p99/p999 dominate, fan-out tail amplification math, sources of tails, and the mitigations — hedged/tied requests, load shedding, adaptive concurrency, backpressure, bulkheads. Python fan-out + hedging simulation. |
| 04 | [`04_data_encoding_evolution.md`](04_data_encoding_evolution.md) | Data encoding & schema evolution: row vs column formats, Protobuf/Thrift/Avro, forward/backward compatibility, the dataflow modes (DB, RPC, message-passing). |
| 05 | [`05_advanced_clocks_hashing.md`](05_advanced_clocks_hashing.md) | Time, ordering & hashing: physical vs logical clocks, Lamport & vector clocks, hybrid logical clocks, TrueTime, consistent hashing variants, rendezvous hashing, jump hash. |
| 06 | [`06_modern_networking.md`](06_modern_networking.md) | Modern networking: TCP internals & congestion control, QUIC/HTTP3, TLS 1.3, gRPC, service mesh & sidecars, load-balancer internals, kernel-bypass. |
| 07 | [`07_advanced_security_threat_modeling.md`](07_advanced_security_threat_modeling.md) | Threat modeling (STRIDE/PASTA), zero-trust, crypto primitives, key management & rotation, token security, supply-chain security. |
| 08 | [`08_ml_ai_infrastructure.md`](08_ml_ai_infrastructure.md) | ML/AI infra: feature stores, training vs serving skew, vector databases & ANN indexes, model serving, LLM-serving (KV-cache, batching), RAG architecture. |
| 09 | [`09_distributed_consensus_deep.md`](09_distributed_consensus_deep.md) | Consensus from scratch: FLP, Paxos (single-decree & multi), Raft internals, Zab, quorum systems, leases, Byzantine fault tolerance. |

> All nine deep-dives share the same rigor and format: theory, diagrams, working Python, the math, real systems, trade-offs, and key takeaways.

### 📂 [`case_studies/`](case_studies/) — applied deep-dives

End-to-end teardowns that combine the above (e.g. how a specific database, queue, or globally-distributed system is built and why). Each case study cites the engineering blogs/papers it draws from.

---

## 🎓 Reading list — the seminal sources

These docs cite the primary literature directly. The canon every staff/principal engineer should have read or skimmed:

**Books**
- Martin Kleppmann — *Designing Data-Intensive Applications* (**DDIA**), 2017. The single best map of this territory; ch. 3 (storage), ch. 4 (encoding), ch. 5–9 (replication, partitioning, transactions, consistency & consensus).
- Tanenbaum & van Steen — *Distributed Systems*.
- Cathy O'Neil & others on capacity — plus Neil Gunther, *Guerrilla Capacity Planning* (the Universal Scalability Law).
- *Site Reliability Engineering* (Google SRE book) — esp. the chapters on load, overload, and addressing cascading failures.

**Papers**
- Lamport — *Time, Clocks, and the Ordering of Events in a Distributed System* (1978).
- Lamport — *The Part-Time Parliament* (Paxos, 1998) and *Paxos Made Simple* (2001).
- Ongaro & Ousterhout — *In Search of an Understandable Consensus Algorithm* (**Raft**, 2014).
- Fischer, Lynch, Paterson — *Impossibility of Distributed Consensus with One Faulty Process* (**FLP**, 1985).
- O'Neil, Cheng, Gawlick, O'Neil — *The Log-Structured Merge-Tree (LSM-Tree)* (1996).
- Bayer & McCreight — *Organization and Maintenance of Large Ordered Indexes* (B-trees, 1972).
- Mohan et al. — *ARIES: A Transaction Recovery Method...* (1992) — WAL & crash recovery.
- Dean & Barroso — *The Tail at Scale* (CACM, 2013).
- Bloom — *Space/Time Trade-offs in Hash Coding with Allowable Errors* (1970).
- Karger et al. — *Consistent Hashing and Random Trees* (1997).
- Corbett et al. — *Spanner: Google's Globally-Distributed Database* (2012) — TrueTime.
- Little — the original M/M/1 result; Gunther — the USL derivation.

---

*This material is educational and language-agnostic; algorithms are in Python for clarity, not production tuning.*
