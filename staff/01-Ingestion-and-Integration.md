# 01 · Ingestion & Integration

Getting data *in* reliably is where most data platforms first break. These are the scenarios you accumulate over years of wiring source systems to a platform.

---

**1. Batch full-load vs incremental extraction**
*Problem:* A source table has 500M rows; nightly full extracts are getting too slow and expensive.
*The fork:* Full reload (simple, idempotent, wasteful) vs incremental by watermark (efficient, but fragile if the source lacks a reliable updated-at or has hard deletes).
*What you weigh:* Does the source have a trustworthy modification timestamp? Are there deletes? Can you tolerate eventual reconciliation? Incremental needs a periodic full-refresh safety net to catch drift.
*Seasoned call:* Incremental for volume, with a scheduled full reconciliation (e.g., weekly) to self-heal. Never trust an incremental watermark alone for years without a reconciliation path.

**2. Change Data Capture (CDC) vs query-based extraction**
*Problem:* You need near-real-time replication of an operational database without hammering it.
*The fork:* Log-based CDC (reads the DB transaction log — low source impact, captures deletes, ordered) vs query-based polling (simple, but misses deletes and loads the source).
*What you weigh:* Log-based CDC (Debezium-style) is the right answer at scale but adds operational complexity, needs careful handling of schema changes, snapshots, and the initial bootstrap. Query-based is fine for small, append-mostly tables.
*Seasoned call:* Log-based CDC for anything operational and high-value; budget for the operational maturity it demands. Don't poll a primary OLTP database at scale.

**3. The CDC initial snapshot + streaming handoff**
*Problem:* You start CDC on a huge existing table — how do you get history without losing in-flight changes?
*The fork:* Lock-and-snapshot (consistent but blocks the source) vs incremental/watermark-based snapshot that runs concurrently with the live stream.
*What you weigh:* The hard part is the seam: guaranteeing no event is lost or double-applied between the snapshot and the live log position. Modern incremental snapshotting solves this but you must understand the watermarking to trust it.
*Seasoned call:* Use incremental snapshotting with explicit low/high watermarks; verify the seam with row counts before trusting it in production.

**4. Schema drift at the source**
*Problem:* An upstream team adds/renames/drops a column without warning and your pipeline breaks (or silently corrupts) at 2 a.m.
*The fork:* Defensive ingestion that tolerates additive change vs strict validation that fails fast vs a formal data contract that prevents the change.
*What you weigh:* Tolerating everything hides real breakage; failing on everything creates noise and 2 a.m. pages. The 2026 answer is to shift this left into a **data contract** enforced in the producer's CI.
*Seasoned call:* Auto-handle additive changes, fail loudly on breaking ones, and push for contract-first producers so the change is caught before it ships — not after it lands.

**5. Exactly-once vs at-least-once ingestion**
*Problem:* Network blips cause retries; you're getting duplicate records downstream.
*The fork:* True exactly-once (expensive, needs transactional or idempotent sinks) vs at-least-once + idempotent/dedup downstream (simpler, usually sufficient).
*What you weigh:* "Exactly-once" end-to-end is often a myth sold as a feature; what you usually want is at-least-once delivery + idempotent processing keyed on a stable business/event id.
*Seasoned call:* Design for at-least-once and make consumers idempotent (dedup on event key + upsert). Reserve true transactional exactly-once for cases where duplicates are genuinely unacceptable (e.g., financial postings).

**6. Late-arriving and out-of-order data**
*Problem:* Events arrive hours late (mobile offline, retries); your daily aggregates are wrong after the fact.
*The fork:* Event-time processing with watermarks and allowed lateness (correct, complex) vs processing-time (simple, wrong for analytics) vs late-data reprocessing windows.
*What you weigh:* How late can data legitimately arrive? What's the cost of restating a closed period? You need an explicit lateness policy and a restatement strategy.
*Seasoned call:* Use event time with a defined watermark and allowed-lateness window, plus a documented restatement process for data beyond the window. Make the lateness policy a product decision, not an accident.

**7. Third-party / SaaS API ingestion**
*Problem:* You depend on a vendor API with rate limits, pagination quirks, and occasional outages.
*The fork:* Build bespoke connectors vs adopt a managed connector platform (Fivetran/Airbyte-style) vs vendor's own export.
*What you weigh:* Build-vs-buy on connectors is mostly an economics and maintenance question — connectors rot as APIs change. Managed platforms save engineering but cost money and reduce control.
*Seasoned call:* Buy connectors for commodity SaaS sources; build only where the source is core, high-volume, or has no good off-the-shelf option. Engineers maintaining a long tail of brittle custom connectors is a classic waste of senior time.

**8. Idempotent ingestion and replayability**
*Problem:* A pipeline failed mid-run; rerunning it double-counts or corrupts the target.
*The fork:* Make every ingestion run idempotent (re-runnable safely) vs rely on manual cleanup.
*What you weigh:* Idempotency (upsert by key, partition-overwrite, deterministic output paths) is the foundation that makes backfills and recovery painless. Non-idempotent pipelines are a permanent operational tax.
*Seasoned call:* Idempotency is non-negotiable; design every pipeline so any run can be safely replayed. This single discipline prevents a huge class of incidents.

**9. Streaming ingestion to the lake (Kafka → table)**
*Problem:* You're landing high-volume Kafka topics into the lake and creating millions of tiny files.
*The fork:* Micro-batch into compacted files vs continuous streaming with a format built for it vs a streaming-to-lake product (Kafka→Iceberg/Hudi directly).
*What you weigh:* Small-files problem kills query performance and metadata. Merge-on-read formats (Hudi, Iceberg with deletion vectors) and compaction strategy matter. Newer Kafka-to-table integrations reduce the custom Flink/Spark glue.
*Seasoned call:* Land into a table format with a deliberate compaction/clustering strategy; for high-frequency upserts, Hudi MoR is strong; otherwise Iceberg with scheduled compaction. Never let raw streaming writes create unbounded tiny files.

**10. Multi-source identity resolution / integration**
*Problem:* The same customer exists in five systems with different keys and inconsistent attributes.
*The fork:* Deterministic matching (rules on shared keys) vs probabilistic/ML entity resolution vs a master-data system.
*What you weigh:* Accuracy vs complexity vs explainability. Probabilistic matching scales but is hard to audit; deterministic is auditable but brittle.
*Seasoned call:* Start deterministic with a surrogate master key; layer probabilistic matching only where deterministic fails and the value justifies the auditability cost. Always keep lineage back to source keys.

**11. Bootstrapping a brand-new source under deadline**
*Problem:* A business team needs a new source onboarded "by Friday."
*The fork:* Quick tactical pipeline (fast, becomes tech debt) vs the proper templated/contracted onboarding (slower, sustainable).
*What you weigh:* The tactical one always becomes permanent. The cost of a one-off is paid forever in maintenance.
*Seasoned call:* Have a standard ingestion template/framework so "fast" and "proper" are the same path. The Staff move is investing in the framework *before* the Friday deadline so there's no tactical-vs-proper choice to make.

**12. Push vs pull and the producer-ownership question**
*Problem:* You're constantly chasing upstream teams when their data breaks.
*The fork:* Data team owns extraction (pull, you absorb all the breakage) vs producers publish to a contract (push, ownership shifts left).
*What you weigh:* Pull centralizes pain on the data team and doesn't scale; push requires organizational buy-in and producer maturity.
*Seasoned call:* Drive toward producers owning published, contracted data products. This is as much an org change as a technical one — and it's the durable fix for the "always chasing upstream" problem.

---

*Cross-references: schema drift and contracts continue in 05; streaming semantics deepen in 03; source-deletion/GDPR propagation in 07.*
