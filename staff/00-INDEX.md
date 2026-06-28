# Staff Data Engineer — 20-Year Scenario & Decision Compendium
## 00 · Master Index

This is a catalog of the scenarios, problems, and architecture decisions a Staff-level data engineer accumulates over a 20-year career — the situations you should be able to narrate, the forks you should be able to defend, and the judgment that separates Staff from Senior.

Every entry follows the same shape:
- **Problem** — the situation as it actually shows up.
- **The fork** — the real decision underneath it (rarely "which tool," usually "which trade-off").
- **What you weigh** — the factors and the common pitfalls.
- **Seasoned call** — the judgment an experienced engineer tends to land on, and why.

There is rarely one correct answer. The Staff signal is being able to reason to the right answer *for a given context* and defend it.

---

### The documents

1. **01 — Ingestion & Integration** — getting data in: batch, CDC, streaming, APIs, schema drift, late/duplicate data, backfills at the source.
2. **02 — Storage, Modeling & Table Formats** — how data is laid out and modeled; file/table formats; the Iceberg/Delta/Hudi decision; modeling paradigms.
3. **03 — Processing: Batch & Streaming** — Spark internals and tuning; streaming semantics; exactly-once; batch vs streaming; the unification question.
4. **04 — Warehouse, Lakehouse, Query & Cost** — warehouse internals; lakehouse architecture; query optimization; the cost decisions that define platform economics.
5. **05 — Orchestration, Reliability, Quality & Observability** — pipelines as software; backfills; SLAs; data contracts; observability; incidents and on-call.
6. **06 — Platform Architecture, Scale & Migrations** — designing whole platforms; centralized vs mesh; multi-region; build vs buy; the migrations that define careers.
7. **07 — Security, Governance, Privacy & Compliance** — access control at scale; PII; GDPR/deletion; lineage; cataloging; governance as an enabler.
8. **08 — AI/ML & LLM Data Infrastructure** — feature stores; ML pipelines; the 2025–2026 frontier: RAG/vector/embedding pipelines and data for agents.
9. **09 — Staff-Level, Org & Cross-Team Decisions** — the non-code scenarios: influence, technical strategy, design docs, postmortems, the decisions that make you Staff.

---

### A 2026 currency snapshot (so the decisions reflect today, not 2015)

A few things have settled or shifted recently that change how several of these decisions are made now:

- **Open table formats have a front-runner.** Apache Iceberg has become the de facto open standard, backed by broad multi-engine and cloud support (Snowflake, BigQuery, AWS S3 Tables, Databricks all support it; Databricks acquired Iceberg's originating company). Delta Lake remains the natural choice inside Databricks; Hudi holds an edge for high-frequency upsert/CDC workloads. Interop layers (XTable, Delta UniForm) increasingly make the "format war" a non-decision.
- **Streaming is a default, not an exotic.** Kafka + Flink (or a managed equivalent) as the real-time backbone is now baseline expectation for many use cases, with hybrid batch+streaming the norm rather than the exception.
- **Data contracts have moved from theory to practice.** "Contract-first," schema-and-SLO-as-code in CI/CD, and shift-left validation are now mainstream answers to the schema-drift problems that used to be tolerated.
- **Data mesh has reached hard-won maturity.** The winning pattern is hybrid: a strong central platform owning the "plumbing," with decentralized domain ownership of the "last mile" — not pure decentralization.
- **AI is now part of the job.** Data engineering is the bottleneck for most AI projects. RAG/vector/embedding pipelines, feature stores for agents, and governance over embeddings and prompts are now squarely data-engineering scope — and the strongest current differentiator for a senior profile.

> Use this as a map. The depth lives in the nine documents. If you can walk someone through 60–70% of these scenarios with real conviction and defend the forks, you're operating at Staff level.
