# 03 · Processing: Batch & Streaming

This is where the distributed-systems depth shows. The scenarios below separate engineers who *use* Spark/Flink from those who understand *why* they behave the way they do.

---

**1. Data skew in distributed joins/aggregations**
*Problem:* One Spark task runs for hours while the rest finish in minutes; a few keys hold most of the data.
*The fork:* Salting the skewed key vs broadcast join (if one side is small) vs adaptive query execution handling it vs isolating hot keys.
*What you weigh:* Skew is the single most common cause of "my job is mysteriously slow." You must read the DAG/stage metrics to spot the straggler partition. AQE helps but doesn't solve all skew.
*Seasoned call:* Diagnose from the stage metrics first; broadcast the small side when possible; salt genuinely skewed keys; enable adaptive execution. The Staff signal is diagnosing skew from the physical plan, not by guessing.

**2. Broadcast vs sort-merge vs shuffle-hash join**
*Problem:* A join is spilling to disk and shuffling huge volumes.
*The fork:* Broadcast (small side fits in memory — avoids shuffle) vs sort-merge (large-large, robust) vs shuffle-hash.
*What you weigh:* Size of each side, available executor memory, broadcast threshold. Forcing a broadcast on too-large a table causes OOMs; missing a broadcast opportunity causes needless shuffles.
*Seasoned call:* Let the optimizer broadcast when the side is genuinely small; tune the threshold deliberately; for large-large joins ensure good partitioning and pre-bucketing where it pays off. Understand *why* the planner chose what it chose.

**3. Spark memory tuning and OOMs**
*Problem:* Executors keep dying with out-of-memory errors under load.
*The fork:* More executor memory (cost) vs better partitioning (more, smaller tasks) vs reducing per-task data (avoid wide rows/explosions) vs spill tuning.
*What you weigh:* Executor memory layout (execution vs storage), partition count, skew, and operations that explode rows. Throwing memory at it is the expensive non-answer.
*Seasoned call:* Right-size partitions so each task's working set fits comfortably; fix skew and row-explosions first; add memory only when the data genuinely warrants it. Reading the Spark UI's stage/task metrics is the core skill.

**4. Shuffle cost and partition count**
*Problem:* Jobs spend most of their time shuffling; or you have 200 default partitions on a tiny job, or too few on a huge one.
*The fork:* Tune shuffle partitions to data size vs rely on defaults vs use adaptive coalescing.
*What you weigh:* Too many partitions = scheduling overhead and tiny files; too few = huge tasks and spills. The right number tracks data volume, not a fixed default.
*Seasoned call:* Use adaptive execution to coalesce post-shuffle partitions; otherwise size shuffle partitions to target ~100s of MB per partition. Minimize shuffles by structuring joins/aggregations well.

**5. Batch vs streaming for a given use case**
*Problem:* A stakeholder asks for "real-time" data; you need to decide if that's warranted.
*The fork:* Batch (simple, cheap, robust, higher latency) vs micro-batch (near-real-time, moderate complexity) vs true streaming (lowest latency, highest complexity and cost).
*What you weigh:* What latency does the *decision* actually need? "Real-time" is often a want, not a requirement. Streaming multiplies operational complexity (state, ordering, recovery).
*Seasoned call:* Push back to find the real latency requirement. Use batch unless the business value of low latency clearly justifies the operational cost. Hybrid (streaming for the few latency-critical paths, batch for the rest) is usually right.

**6. Exactly-once stream processing**
*Problem:* A failure mid-stream must not double-count or drop events.
*The fork:* Checkpointing + transactional sinks (true exactly-once within the pipeline) vs at-least-once + idempotent sink vs accepting at-least-once.
*What you weigh:* Exactly-once requires coordinated checkpointing, replayable sources, and transactional/idempotent sinks — all three. It's achievable in Flink/Structured Streaming but costs throughput and complexity.
*Seasoned call:* Aim for effectively-once via at-least-once delivery + idempotent writes keyed on event id; reserve full transactional exactly-once for genuinely intolerant cases. Know the difference between "the framework says exactly-once" and "your end-to-end pipeline is exactly-once."

**7. Event time vs processing time + watermarks**
*Problem:* Windowed aggregations are wrong because events arrive late or out of order.
*The fork:* Process by event time with watermarks and allowed lateness (correct) vs processing time (simple, wrong for analytics).
*What you weigh:* Watermark lag vs completeness: aggressive watermarks close windows early and drop late data; conservative watermarks add latency. This is a fundamental correctness/latency trade-off.
*Seasoned call:* Use event time with a watermark tuned to real observed lateness, plus an allowed-lateness window and a side-output for very late data. Be able to explain the Dataflow model's completeness-vs-latency trade-off from first principles.

**8. Stateful streaming and state-store growth**
*Problem:* A streaming job's state keeps growing until it falls over.
*The fork:* TTL/state expiry vs bounded windows vs offloading state vs accepting unbounded state (don't).
*What you weigh:* What's the legitimate lifetime of state? Unbounded keyed state is a slow-motion outage. State backend (RocksDB) tuning and checkpoint sizing matter.
*Seasoned call:* Always bound state with TTL or windowing; monitor state size as a first-class metric; size checkpoints and state backends deliberately. Unbounded state is one of the most common streaming production failures.

**9. Backpressure and throughput limits**
*Problem:* A spike upstream overwhelms the stream processor; latency balloons or it crashes.
*The fork:* Backpressure propagation (slow the source) vs buffering vs scaling out vs load shedding.
*What you weigh:* Whether the source can be slowed (Kafka can, a webhook can't), buffer limits, and whether dropping data is acceptable.
*Seasoned call:* Rely on backpressure to a durable buffer (Kafka) as the shock absorber; scale consumers horizontally; only shed load where data loss is genuinely acceptable. Design the buffer to absorb realistic spikes.

**10. Reprocessing / backfill of a streaming pipeline**
*Problem:* A bug shipped; you need to reprocess weeks of historical events with corrected logic.
*The fork:* Replay from the source log (Kafka retention permitting) vs Lambda-style batch correction vs Kappa-style single-codebase replay.
*What you weigh:* Source retention, whether logic is identical for batch and stream, and the cost of double-processing. Kappa (one streaming codebase, replay history through it) avoids the dual-codebase Lambda tax.
*Seasoned call:* Keep enough source retention to replay; prefer a single processing codebase that can run over both live and historical data so backfills use the same tested logic. Avoid maintaining separate batch and stream implementations of the same transform.

**11. Spark vs Flink vs warehouse-native vs Ray**
*Problem:* Choosing the processing engine for a workload.
*The fork:* Spark (batch + micro-batch, huge ecosystem) vs Flink (true low-latency streaming, strong state) vs warehouse-native SQL/ELT (simplest if data's already in the warehouse) vs Ray (ML/Python-centric distributed compute).
*What you weigh:* Latency needs, team skills, where the data already lives, and whether SQL suffices. The cheapest processing is often the one you don't run — push transforms into the warehouse (ELT) when it's already there.
*Seasoned call:* Default to warehouse-native ELT for warehouse-resident analytics; Spark for heavy lake-side batch; Flink for genuine low-latency stateful streaming. Match the engine to the latency and data-locality reality, not to fashion.

**12. ETL vs ELT**
*Problem:* Deciding whether to transform before loading or load raw and transform in the warehouse.
*The fork:* ETL (transform before load — control, but rigid and compute outside the warehouse) vs ELT (load raw, transform in-warehouse with SQL/dbt — flexible, leverages cheap warehouse compute).
*What you weigh:* Modern cloud warehouses make ELT compelling: raw data is preserved (replayable), transformations are versioned SQL, and compute scales elastically. ETL still wins where heavy non-SQL processing or pre-load PII handling is needed.
*Seasoned call:* ELT as the default in a modern cloud stack (raw landing + dbt-style transforms); ETL for cases needing pre-load redaction, heavy custom compute, or strict pre-load validation.

**13. UDFs and pushing logic into the engine**
*Problem:* Python UDFs are making a Spark/SQL job crawl.
*The fork:* Native/built-in functions and SQL expressions (optimizable) vs UDFs (opaque to the optimizer, serialization overhead) vs vectorized/pandas UDFs.
*What you weigh:* UDFs defeat Catalyst optimization and add serialization cost; native expressions stay in optimized columnar execution.
*Seasoned call:* Express logic in native functions/SQL wherever possible; use vectorized UDFs when you must drop to Python; treat a row-at-a-time Python UDF in a hot path as a red flag.

---

*Cross-references: streaming ingestion in 01; cost of compute in 04; reliability/replay discipline in 05.*
