# 02 · Storage, Modeling & Table Formats

How data is physically laid out and logically modeled determines cost, performance, and how painful the next five years will be. This is one of the most decision-heavy and currently-active areas.

---

**1. File format choice (Parquet vs ORC vs Avro vs JSON)**
*Problem:* You're standardizing how data is stored across the platform.
*The fork:* Columnar (Parquet/ORC — great for analytics scans) vs row-based (Avro — great for streaming/record-by-record and schema evolution) vs raw JSON/CSV (avoid for anything at scale).
*What you weigh:* Read patterns drive this. Analytics = columnar. Streaming/serialization = Avro. Compression, predicate pushdown, and schema-evolution support differ.
*Seasoned call:* Parquet as the analytical default; Avro for streaming/transport. Never leave high-volume data in JSON/CSV at rest — the scan cost compounds forever.

**2. The open table format decision: Iceberg vs Delta vs Hudi**
*Problem:* You're choosing the table format for a new lakehouse and the choice locks in years of tooling.
*The fork:* Iceberg (broadest multi-engine support, vendor-neutral governance, partition evolution, now the de facto standard) vs Delta (deepest Spark/Databricks integration, simplest model) vs Hudi (best for high-frequency upserts/CDC via merge-on-read).
*What you weigh:* Engine ecosystem (are you all-in on Databricks/Spark, or multi-engine with Snowflake/BigQuery/Trino/Flink?), workload (append-heavy vs upsert-heavy), and operational maturity. Interop layers (XTable, Delta UniForm) increasingly soften the lock-in.
*Seasoned call:* For a new engine-agnostic platform in 2026, Iceberg is the safe default given universal support. Inside Databricks, Delta is excellent and you shouldn't fight it. For streaming-heavy upsert/CDC, Hudi MoR still earns its place. The format itself is becoming a thinner decision than the catalog and table-maintenance strategy around it.

**3. Copy-on-write vs merge-on-read**
*Problem:* Update-heavy workload is causing huge write amplification (rewriting whole files for small changes).
*The fork:* Copy-on-write (clean files, fast reads, expensive writes) vs merge-on-read (cheap writes via delta logs, reads pay a merge cost until compaction).
*What you weigh:* Write frequency vs read latency requirements, and whether you can run reliable background compaction. MoR shifts cost from write-time to read-time.
*Seasoned call:* CoW for read-heavy, lower-churn tables; MoR for high-frequency upserts (CDC mirrors), with a disciplined compaction schedule. Mismatching these is a common, expensive error.

**4. Partitioning strategy**
*Problem:* Queries are slow and scanning far too much data; or you've over-partitioned into millions of tiny partitions.
*The fork:* Partition by query-filter columns (often date) vs over-partition (metadata explosion, tiny files) vs under-partition (full scans).
*What you weigh:* Cardinality, query predicates, file sizes. Iceberg's hidden partitioning and partition evolution let you change schemes without rewrites — a genuine advantage when requirements shift.
*Seasoned call:* Partition by the dominant filter (usually time) at a granularity that keeps files reasonably sized (hundreds of MB); avoid high-cardinality partition columns. Lean on clustering/Z-ordering for secondary access patterns rather than more partitions.

**5. File sizing and the small-files problem**
*Problem:* Streaming/frequent writes produce millions of tiny files; query planning and listing become the bottleneck.
*The fork:* Compaction (scheduled or inline) vs larger write batches vs accepting the cost.
*What you weigh:* Read performance and metadata overhead vs compaction compute cost. This is a perennial operational chore that must be designed in, not bolted on.
*Seasoned call:* Treat compaction/clustering as a first-class scheduled maintenance job from day one. Target file sizes in the 100s of MB. Unmanaged small files are one of the most common quiet performance killers.

**6. Dimensional (Kimball) vs Data Vault vs One-Big-Table**
*Problem:* You're choosing a modeling paradigm for the warehouse.
*The fork:* Star schema/Kimball (BI-friendly, intuitive, well-understood) vs Data Vault (auditable, agile to source change, complex, verbose) vs OBT/wide denormalized (fast for modern columnar warehouses, simple, redundant).
*What you weigh:* Consumers (BI tools love stars), source volatility (Data Vault absorbs change well), and the warehouse engine (columnar engines make wide tables cheap). Modern cloud warehouses have made OBT more viable than it used to be.
*Seasoned call:* Kimball star schemas remain the workhorse for BI consumption; Data Vault where auditability and many volatile sources justify the overhead; OBT for specific high-performance serving layers. Often you layer them: Vault/normalized core, star/OBT marts on top.

**7. Slowly Changing Dimensions (SCD)**
*Problem:* A customer's attributes change over time and you need history (or don't).
*The fork:* SCD Type 1 (overwrite, no history) vs Type 2 (versioned rows with validity ranges, full history) vs Type 3 (limited prior-value columns).
*What you weigh:* Do downstream consumers need point-in-time correctness? Type 2 is powerful but adds join complexity and storage; getting effective-dating right is subtle.
*Seasoned call:* Type 2 wherever history matters for analysis (most dimensions in serious analytics); Type 1 for attributes nobody needs history on. Get the effective-date/current-flag logic bulletproof — it's a classic source of silent bugs.

**8. Normalization vs denormalization**
*Problem:* Joins are expensive at query time; or denormalized tables are drifting out of sync.
*The fork:* Normalize (less redundancy, more joins) vs denormalize (fewer joins, faster reads, update anomalies and storage cost).
*What you weigh:* Columnar warehouses make wide denormalized tables cheap to scan but expensive to keep consistent. Read frequency vs update frequency.
*Seasoned call:* Denormalize at the serving/mart layer for read performance; keep a normalized or modeled core as the source of truth. Don't denormalize the system of record.

**9. Catalog and metadata layer**
*Problem:* Multiple engines need a consistent view of tables; you're deciding how tables are registered and governed.
*The fork:* Engine-specific catalog (lock-in) vs open REST catalog (Iceberg REST/Polaris-style) vs a unified governance catalog (Unity Catalog, Snowflake Horizon, Purview).
*What you weigh:* Multi-engine interoperability, governance/lineage features, and lock-in. The catalog is increasingly the real strategic choice (more than the table format).
*Seasoned call:* Favor an open, multi-engine catalog if you're serious about avoiding lock-in; adopt a unified governance catalog when lineage, access control, and AI-asset governance matter at org scale. Decide the catalog deliberately — it outlives the format debate.

**10. Storage tiering and lifecycle**
*Problem:* Storage costs are climbing because everything lives in hot storage forever.
*The fork:* Aggressive lifecycle policies (cold/archive tiers, expiry) vs keep-everything-hot (simple, expensive) vs delete (risky if compliance needs it).
*What you weigh:* Access frequency, retrieval-cost on cold tiers, and retention/compliance requirements. Time travel and snapshot retention also quietly accumulate cost.
*Seasoned call:* Tier by access pattern, set explicit retention/snapshot-expiry policies, and reconcile against compliance requirements. Unmanaged snapshot and time-travel history is a sneaky cost line.

**11. Schema evolution policy**
*Problem:* Schemas must change over time without breaking consumers or requiring full rewrites.
*The fork:* Permissive additive-only evolution vs strict versioned schemas vs format-native evolution (Iceberg's column-rename-without-rewrite).
*What you weigh:* Backward/forward compatibility guarantees, consumer impact, and rewrite cost. Iceberg's schema/partition evolution is a real advantage here.
*Seasoned call:* Standardize on additive, backward-compatible evolution; use a format that supports safe column operations; never silently change semantics of an existing column — add a new one.

**12. Data lake zones (raw / cleaned / curated)**
*Problem:* The lake has become a swamp — nobody knows which table is trustworthy.
*The fork:* Enforce a layered medallion-style architecture (raw → conformed → curated/serving) vs let teams write wherever.
*What you weigh:* Discoverability, trust, and reprocessing ability vs the discipline cost. Raw retention enables reprocessing; curated layers provide trust.
*Seasoned call:* Enforce explicit zones with clear ownership and quality guarantees per layer. Keep immutable raw for replay, expose only curated, governed data products to consumers. The "swamp" is always a governance failure, not a storage one.

---

*Cross-references: cost angle expands in 04; governance/lineage of these assets in 07; compaction as an operational job in 05.*
