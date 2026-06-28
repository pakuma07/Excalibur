# The Data Engineer's Lexicon
## Every term, tool, technique, methodology & buzzword across a 20-year career

A categorized glossary of the vocabulary a senior/Staff data engineer accumulates from the Hadoop era through the 2026 AI-data era. Each entry has a one-line gloss. Within sections, terms run roughly **legacy → modern** so the evolution is visible.

> **How to use it.** (1) *Resume mining* — pull the modern terms you genuinely know into your skills section; lead with current, keep legacy as depth. (2) *Interview recognition* — skim before interviews so no term catches you cold. (3) *Currency signal* — the terms marked ⚠️ *legacy* are ones to de-emphasize as your headline identity; the ones marked ★ *current* are what signal you're up to date.

---

## 1. Eras & Paradigm Shifts (the 20-year arc)

- **Data warehouse era** — centralized, schema-on-write, BI-focused relational warehouses (the 2000s baseline). ⚠️
- **Big Data / Hadoop era** — distributed storage + batch compute on commodity hardware; "schema-on-read." ⚠️
- **Cloud data era** — managed, elastic, decoupled storage/compute warehouses and lakes.
- **Modern Data Stack (MDS)** — cloud warehouse + ELT + dbt + BI, assembled from best-of-breed SaaS tools. ★
- **Lakehouse era** — warehouse features (ACID, governance) on open lake storage. ★
- **AI/Data era** — data engineering as the backbone of AI; RAG, vectors, agents as consumers. ★
- **Schema-on-write vs schema-on-read** — validate structure at load time vs at query time.
- **OLTP vs OLAP** — transactional (operational) vs analytical (reporting) workloads.
- **Batch vs streaming vs near-real-time** — scheduled bulk vs continuous vs micro-batch processing.

## 2. Computer Science & Distributed Systems Foundations

- **Distributed systems** — coordinating computation/storage across many machines.
- **CAP theorem / PACELC** — consistency-availability-partition trade-offs (and latency extension).
- **Consistency models** — strong, eventual, causal, read-your-writes.
- **Consensus** — Paxos, Raft; agreement among distributed nodes.
- **Partitioning / sharding** — splitting data across nodes by key.
- **Replication** — copies for durability/availability; leader-follower, quorum.
- **Idempotency** — an operation safely repeatable with the same result.
- **Eventual consistency** — replicas converge over time.
- **MapReduce** — the foundational distributed batch paradigm (map → shuffle → reduce). ⚠️
- **Shuffle** — redistributing data across nodes between stages (the expensive step).
- **Data locality** — moving compute to data to avoid network transfer.
- **Backpressure** — slowing producers when consumers can't keep up.
- **Fault tolerance / checkpointing** — recovering state after failure.
- **Write amplification** — extra writes caused by rewriting data on update.
- **Throughput vs latency** — volume per time vs delay per item.
- **Horizontal vs vertical scaling** — more machines vs bigger machines.

## 3. Data Modeling & Warehousing Methodologies

- **Dimensional modeling (Kimball)** — star/snowflake schemas of facts and dimensions. ★ (still core)
- **Inmon (CIF)** — top-down, normalized enterprise warehouse. ⚠️
- **Star schema / snowflake schema** — central fact table joined to dimension tables.
- **Fact table / dimension table** — measures vs descriptive context.
- **Grain** — the level of detail one fact row represents.
- **Conformed dimensions** — shared dimensions across facts/marts.
- **Slowly Changing Dimensions (SCD Type 1/2/3)** — overwrite / version with history / limited prior value.
- **Surrogate key vs natural/business key** — system-generated vs source identifier.
- **Data Vault (2.0)** — hubs, links, satellites; auditable, change-resilient modeling.
- **One Big Table (OBT) / wide tables** — denormalized single table for columnar warehouses. ★
- **Normalization / denormalization** — reduce redundancy vs reduce joins.
- **3NF (third normal form)** — classic relational normalization.
- **Medallion architecture (bronze/silver/gold)** — raw → cleaned → curated layering. ★
- **Staging / landing / curated / serving zones** — lake/warehouse layering.
- **Semantic layer / metrics layer** — single governed definitions of business metrics. ★
- **Materialized view / aggregate table** — precomputed results for fast reads.
- **Effective dating / point-in-time** — temporal validity for historical correctness.

## 4. File Formats, Storage & Table Formats

- **CSV / TSV / JSON / XML** — text/serialization formats (avoid at scale). ⚠️
- **Parquet** — columnar file format, analytics default. ★
- **ORC** — columnar format (Hive/Hadoop ecosystem).
- **Avro** — row-based format with strong schema evolution (streaming).
- **Columnar vs row storage** — scan-few-columns vs read-whole-rows.
- **Predicate pushdown** — filter at the storage layer to read less.
- **Compression/encoding** — Snappy, Zstd, gzip, dictionary, run-length.
- **Apache Iceberg** — open table format; de facto standard in 2026; hidden partitioning, partition evolution. ★
- **Delta Lake** — open table format, deepest Databricks/Spark integration; Liquid Clustering, UniForm. ★
- **Apache Hudi** — open table format; merge-on-read, record-level upserts, CDC-strong. ★
- **Copy-on-write (CoW) vs merge-on-read (MoR)** — rewrite files on update vs append delta logs.
- **Time travel / snapshots** — query historical table versions.
- **Schema evolution / partition evolution** — change schema/partitioning without rewrite.
- **Compaction / clustering / Z-ordering / liquid clustering** — reorganizing files for performance.
- **Small-files problem** — too many tiny files killing performance.
- **Partitioning / bucketing** — splitting data by column value / hash for pruning.
- **Object storage (S3 / GCS / ADLS)** — cloud blob storage underpinning lakes.
- **HDFS** — Hadoop Distributed File System. ⚠️
- **Deletion vectors** — mark-deleted rows without rewriting files.
- **XTable / UniForm** — cross-format interoperability between Iceberg/Delta/Hudi. ★

## 5. Processing Engines & Frameworks

- **Apache Hadoop / MapReduce** — original distributed batch framework. ⚠️
- **Apache Hive** — SQL-on-Hadoop. ⚠️
- **Apache Pig** — dataflow scripting on Hadoop. ⚠️
- **Apache Spark** — distributed in-memory processing engine; batch + micro-batch. ★
- **Catalyst optimizer / Tungsten** — Spark's query optimizer / execution engine.
- **Adaptive Query Execution (AQE)** — runtime plan optimization in Spark.
- **RDD / DataFrame / Dataset** — Spark's data abstractions.
- **PySpark** — Python API for Spark. ★
- **Apache Flink** — true low-latency stateful stream processing. ★
- **Apache Beam / Dataflow model** — unified batch+stream programming model.
- **Ray** — distributed Python compute, popular for ML/AI. ★
- **Presto / Trino** — distributed interactive SQL query engines. ★
- **Dask** — parallel Python/pandas-style computing.
- **Apache Storm / Samza** — early stream processors. ⚠️
- **DuckDB** — in-process analytical SQL engine (the "SQLite for analytics"). ★
- **Polars** — fast Rust-based DataFrame library. ★

## 6. Streaming, Messaging & Event Systems

- **Apache Kafka** — distributed log / event streaming backbone. ★
- **Kafka Streams / ksqlDB** — stream processing on Kafka.
- **Amazon Kinesis / Google Pub/Sub / Azure Event Hubs** — managed streaming services.
- **Apache Pulsar** — streaming + queuing system.
- **RabbitMQ / ActiveMQ** — traditional message brokers.
- **Event-driven architecture** — systems reacting to events asynchronously.
- **Event time vs processing time** — when it happened vs when processed.
- **Watermark** — assertion of completeness up to an event-time point.
- **Windowing (tumbling/sliding/session)** — grouping streaming events by time.
- **Allowed lateness / late-arriving data** — handling events that arrive after their window.
- **Exactly-once / at-least-once / at-most-once** — delivery/processing semantics.
- **Stateful stream processing / state store (RocksDB)** — maintaining state across events.
- **Change Data Capture (CDC)** — streaming row-level changes from a database. ★
- **Debezium** — popular open-source log-based CDC. ★
- **Log compaction** — retaining the latest value per key in a topic.
- **Offset / consumer group / partition** — Kafka consumption mechanics.
- **WarpStream / Redpanda** — newer Kafka-compatible streaming platforms. ★
- **Table topics / streaming-to-lakehouse** — Kafka data landing directly as tables. ★

## 7. Ingestion, Integration & ELT/ETL Tooling

- **ETL vs ELT** — transform-then-load vs load-then-transform (ELT is the modern default). ★
- **Informatica PowerCenter** — classic enterprise ETL tool. ⚠️
- **IBM DataStage / Ab Initio / Talend / Pentaho / SSIS** — legacy ETL platforms. ⚠️
- **Apache NiFi** — dataflow automation/routing.
- **Apache Sqoop** — RDBMS↔Hadoop transfer. ⚠️
- **Fivetran / Airbyte / Stitch** — managed connector / ingestion platforms. ★
- **Singer / Meltano** — open-source extract-load specs/tooling.
- **dbt (data build tool)** — SQL-based transformation, the ELT/T standard. ★
- **Reverse ETL** — pushing warehouse data back into operational tools (Hightouch, Census). ★
- **Zero-ETL** — direct integration removing pipeline glue (buzzword). ★
- **Bronze ingestion / raw landing** — immutable first-touch storage.
- **Incremental load / full load / watermark extraction** — change-only vs complete pulls.
- **Upsert / merge** — insert-or-update by key.
- **Entity resolution / identity stitching** — matching records across sources.
- **Idempotent ingestion / replayability** — safely re-runnable loads.

## 8. Orchestration, Workflow & Transformation

- **Cron** — basic time-based scheduling. ⚠️
- **Apache Airflow** — DAG-based workflow orchestration (the incumbent). ★
- **DAG (directed acyclic graph)** — task dependency graph.
- **Dagster** — asset-centric, data-aware orchestrator. ★
- **Prefect** — Pythonic dataflow orchestrator. ★
- **Luigi / Oozie / Azkaban** — earlier orchestrators. ⚠️
- **dbt (as transformation orchestrator)** — model DAGs in SQL. ★
- **Sensors / triggers / data-availability scheduling** — event/data-driven execution.
- **Backfill** — reprocessing historical periods.
- **Idempotent / re-runnable tasks** — safe retries.
- **Paved roads / golden paths** — opinionated standard pipelines. ★

## 9. Databases, Warehouses & Query Engines

- **Oracle / SQL Server / DB2 / PostgreSQL / MySQL** — relational databases (OLTP). 
- **Teradata / Netezza / Greenplum / Vertica / Exadata** — MPP analytical warehouses. ⚠️ (mostly legacy)
- **MPP (massively parallel processing)** — distributed analytical query execution.
- **Amazon Redshift** — cloud MPP warehouse.
- **Google BigQuery** — serverless cloud warehouse. ★
- **Snowflake** — cloud warehouse with decoupled storage/compute, virtual warehouses. ★
- **Databricks (SQL Warehouse / Lakehouse Platform)** — Spark-based lakehouse platform. ★
- **ClickHouse / Apache Druid / Apache Pinot** — real-time OLAP/analytics databases. ★
- **Apache Cassandra / HBase / DynamoDB** — wide-column / NoSQL key-value stores.
- **MongoDB / Couchbase** — document databases.
- **Neo4j / graph databases** — relationship-centric storage.
- **Redis** — in-memory key-value store / cache.
- **Elasticsearch / OpenSearch** — search and log analytics.
- **Decoupled storage and compute** — scale and bill them independently. ★
- **Virtual warehouse / compute cluster** — isolated, right-sized compute.
- **Query optimizer / execution plan** — how a query is planned and run.
- **Vectorized execution** — batch-of-rows columnar processing.

## 10. Cloud, Infrastructure, Containers & IaC

- **AWS / Azure / GCP** — major cloud platforms.
- **IaaS / PaaS / SaaS / serverless** — infrastructure abstraction levels.
- **Infrastructure as Code (IaC)** — Terraform, CloudFormation, Pulumi. ★
- **Docker / containers** — packaged, portable runtime units.
- **Kubernetes (K8s)** — container orchestration. ★
- **Helm / operators** — Kubernetes packaging/automation.
- **CI/CD** — continuous integration/deployment pipelines.
- **GitOps** — Git as the source of truth for infra/deploys.
- **FinOps** — cloud cost management/accountability. ★
- **Auto-scaling / auto-suspend / right-sizing** — elastic, cost-efficient compute.
- **Multi-cloud / hybrid cloud** — spanning multiple/​on-prem+cloud environments.
- **Data gravity** — data attracting services/compute to its location (buzzword).
- **Lift-and-shift vs re-platform vs re-architect** — migration strategies.

## 11. Architecture Patterns & Design Methodologies

- **Lambda architecture** — parallel batch + speed layers. ⚠️ (dual-codebase tax)
- **Kappa architecture** — single streaming codebase with replay. ★
- **Data Lake** — central raw store on object storage.
- **Data Lakehouse** — lake + warehouse features unified. ★
- **Data Mesh** — domain-owned data products, federated governance, self-serve platform. ★
- **Data Fabric** — metadata-driven unified data integration layer. ★
- **Data as a Product** — treating datasets as owned, documented products. ★
- **Data Products / Data Domains** — domain-aligned, contract-backed datasets.
- **Hub-and-spoke / centralized vs federated** — platform ownership topologies.
- **Strangler fig (incremental migration)** — replace legacy piece by piece. ★
- **Medallion / multi-hop architecture** — layered refinement (see §3).
- **Hexagonal / event sourcing / CQRS** — software patterns appearing in data systems.
- **Single source of truth (SSOT)** — one authoritative dataset (buzzword).
- **Microservices / monolith** — service decomposition styles.
- **Architecture Decision Record (ADR) / RFC / design doc** — documented decisions.

## 12. DataOps, Reliability, Quality & Observability

- **DataOps** — DevOps practices applied to data pipelines. ★
- **CD4ML / MLOps / LLMOps** — continuous delivery for ML / LLM systems. ★
- **Data quality dimensions** — completeness, accuracy, consistency, timeliness, validity, uniqueness.
- **Data contracts** — producer-enforced schema + SLO agreements (contract-first). ★
- **Shift-left** — validating/​testing earlier in the pipeline. ★
- **Schema drift / schema registry** — uncontrolled schema change / governed schema store.
- **Data observability** — freshness, volume, distribution, schema, lineage monitoring. ★
- **Great Expectations / Soda / dbt tests / Monte Carlo** — data quality/observability tools. ★
- **SLA / SLO / SLI** — service-level agreement / objective / indicator.
- **Data downtime** — periods of missing/wrong/inaccurate data (buzzword).
- **Freshness / staleness / latency** — how current the data is.
- **Lineage (column-level / table-level)** — data's origin-to-consumption trail.
- **Blameless postmortem / RCA** — incident learning without blame.
- **Anomaly detection / AI-driven observability** — automated issue detection. ★
- **Reconciliation** — proving two systems/outputs match (migrations).
- **Quarantine / dead-letter queue** — isolating bad records.

## 13. Governance, Security, Privacy & Compliance

- **Data governance** — policies for data access, quality, ownership.
- **Data catalog / metadata management** — discoverable inventory of data assets.
- **Unity Catalog / Snowflake Horizon / Microsoft Purview / Amundsen / DataHub / Collibra / Alation** — catalog/governance tools. ★
- **RBAC / ABAC / policy-as-code** — role-/attribute-based access; rules as code. ★
- **PII / PHI / PCI** — sensitive data classes (personal/health/payment).
- **Data classification / tagging / masking / tokenization** — protecting sensitive fields.
- **Encryption at rest / in transit / in use** — data protection states.
- **Crypto-shredding** — deleting by destroying encryption keys (immutable-store deletion). ★
- **GDPR / CCPA / HIPAA / SOX / right to be forgotten** — privacy/compliance regimes.
- **Data residency / sovereignty** — geographic data constraints.
- **Audit logging / legal hold / retention policy** — compliance evidence and lifecycle.
- **Federated / computational governance** — automated, decentralized policy enforcement. ★
- **Data steward / data owner** — accountable governance roles.
- **ODCS (Open Data Contract Standard)** — emerging contract standard. ★

## 14. SQL, Languages & Engineering Skills

- **SQL** — the lingua franca of data (window functions, CTEs, analytic functions). ★
- **Python** — primary data-engineering language. ★
- **Scala / Java** — JVM languages for Spark/Flink internals.
- **Bash / shell scripting** — glue and automation.
- **PL/SQL / T-SQL** — procedural SQL dialects. ⚠️ (legacy-heavy)
- **Window functions / CTEs / recursive queries** — advanced SQL.
- **UDF (user-defined function) / vectorized UDF** — custom logic in engines.
- **pandas / NumPy** — Python data manipulation.
- **Regex** — pattern matching for parsing.
- **Git / version control / code review** — software engineering hygiene.
- **Unit / integration / contract testing** — pipeline test types.

## 15. ML / AI / LLM Data Infrastructure (the current frontier)

- **Feature store** — managed, reusable, consistent ML features (e.g., Feast, Tecton). ★
- **Feature engineering** — deriving model inputs from raw data.
- **Training/serving skew** — features differing between train and production. ★
- **Point-in-time correctness / data leakage** — temporal correctness of training data. ★
- **MLOps** — operationalizing ML lifecycle.
- **Model registry / experiment tracking** — MLflow, Weights & Biases.
- **Vector database** — stores embeddings for similarity search (Pinecone, Milvus, Qdrant, Weaviate, pgvector, FAISS). ★
- **Embeddings** — vector representations of data for semantic search. ★
- **ANN (approximate nearest neighbor) / HNSW** — vector search algorithms/indexes. ★
- **RAG (Retrieval-Augmented Generation)** — grounding LLMs with retrieved data. ★
- **GraphRAG / agentic retrieval** — graph-/agent-enhanced retrieval. ★
- **Chunking / chunk strategy** — splitting documents for embedding/retrieval. ★
- **Hybrid search / reranking** — vector + keyword retrieval with re-scoring. ★
- **Context engineering** — delivering fresh, relevant context to LLMs/agents. ★
- **Semantic cache** — caching LLM/retrieval results.
- **Fine-tuning vs RAG vs prompting** — ways to adapt LLM behavior.
- **Evaluation / groundedness / relevance metrics** — measuring AI pipeline quality. ★
- **AI agents / agentic workflows** — autonomous LLM-driven systems as data consumers. ★
- **Unstructured data pipelines** — PDFs, audio, images ingested for AI. ★

## 16. BI, Analytics & Serving

- **Business Intelligence (BI)** — reporting/dashboards.
- **Tableau / Power BI / Looker / Qlik / Superset / Metabase** — BI/visualization tools.
- **LookML / semantic models** — governed metric definitions in BI.
- **OLAP cube / rollup / drill-down** — multidimensional analysis.
- **Self-service analytics** — empowering non-engineers to query data.
- **Embedded analytics / headless BI** — analytics inside apps / API-served metrics. ★
- **KPI / metric / dimension / measure** — analytics building blocks.
- **Serving layer / key-value serving / online store** — low-latency app-facing data.
- **Federated query / data virtualization** — querying across systems without copying.

## 17. Performance & Optimization Techniques

- **Partition pruning** — skipping irrelevant partitions.
- **Predicate / projection pushdown** — filtering/column-selecting at the source.
- **Broadcast join vs sort-merge vs shuffle-hash join** — join strategies.
- **Data skew handling / salting** — spreading hot keys across partitions.
- **Bucketing / clustering / Z-order / liquid clustering** — physical layout for locality.
- **Caching / result cache / materialization** — reusing computed results.
- **Incremental processing** — only process changed data.
- **Vectorization / columnar execution** — batch processing for speed.
- **Cost-based optimization (CBO)** — choosing plans by estimated cost.
- **Workload isolation / concurrency scaling** — protecting SLAs under load.
- **Query profiling / explain plans** — diagnosing query cost.

## 18. Buzzwords, Jargon & Role Vocabulary

- **Single source of truth, data swamp, data silo, data gravity, data democratization** — common industry buzzwords.
- **Shift-left, data as a product, data mesh, zero-ETL, headless data** — modern movement buzzwords. ★
- **Data-driven / AI-ready / cloud-native / future-proof** — marketing adjectives.
- **Time-to-value, total cost of ownership (TCO), build-vs-buy, vendor lock-in** — decision jargon.
- **Bus factor, tech debt, paved road, golden path, north-star architecture** — engineering culture terms.
- **Blast radius, noisy neighbor, single point of failure (SPOF), bottleneck** — risk/reliability jargon.
- **IC (individual contributor), Staff/Principal, influence without authority, scope** — career-level vocabulary.
- **RFC, ADR, design doc, postmortem, runbook, on-call** — engineering process artifacts.
- **Greenfield vs brownfield** — new build vs existing system.
- **Toil, paved road, self-serve platform, platform-as-a-product** — platform-engineering jargon. ★
- **GCC (Global Capability Center), captive unit** — org/employer types (esp. India market).

---

## Quick "currency" filter for a resume/profile

**Lead with (★ current):** Spark/PySpark, Snowflake/BigQuery/Databricks, Iceberg/Delta, dbt, Airflow/Dagster, Kafka/Flink, CDC/Debezium, data contracts, data mesh/lakehouse, FinOps, Kubernetes/Terraform, feature stores, vector DBs/RAG, data observability.

**Keep as depth, don't headline (⚠️ legacy):** Informatica/DataStage/Ab Initio, Teradata/Netezza, Hadoop/MapReduce/Hive/Pig/Sqoop, Oozie/Luigi, PL/SQL-heavy stored-procedure work, on-prem cron ETL.

**The signal:** showing both says "I have 20 years of depth *and* I'm current" — which is exactly the profile that beats the market. Listing only the legacy column is what gets an experienced engineer filtered out.