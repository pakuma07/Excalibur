# 04 · Warehouse, Lakehouse, Query & Cost

At scale, the warehouse/lakehouse layer is where performance meets economics. Staff engineers own the SLAs *and* the bill.

---

**1. Warehouse vs lake vs lakehouse**
*Problem:* Choosing the core analytical architecture.
*The fork:* Cloud warehouse (Snowflake/BigQuery/Redshift — managed, fast, governed, can get pricey) vs data lake (cheap object storage, flexible, needs more engineering) vs lakehouse (open table format on object storage with warehouse-like features).
*What you weigh:* Workload mix (BI vs ML vs ad hoc), openness/lock-in, cost model, and team capability. The lakehouse promises one architecture for BI + ML on open formats; warehouses promise less operational burden.
*Seasoned call:* Lakehouse on an open table format when you want openness, ML+BI on one copy of data, and cost control at scale; managed warehouse when time-to-value and low operational burden matter more than openness. Increasingly these converge (warehouses query open tables; lakehouses add governance).

**2. Decoupling storage and compute**
*Problem:* You can't scale query power without also scaling (and paying for) storage, or vice versa.
*The fork:* Coupled (legacy, simple, inflexible) vs decoupled (independent scaling, elastic compute, the modern default).
*What you weigh:* Decoupling lets you spin compute up/down per workload and pay for storage separately — the foundation of elastic cost control. Nearly all modern platforms assume it.
*Seasoned call:* Decoupled storage/compute is table stakes now. Design so each workload gets its own right-sized, isolated compute and storage is shared and cheap.

**3. Query optimization: why is this query expensive?**
*Problem:* A dashboard query scans terabytes and costs a fortune each refresh.
*The fork:* Pruning/partitioning/clustering (scan less) vs materialization (precompute) vs caching vs rewriting the query.
*What you weigh:* Read the query plan: full scans, missing pruning, exploding joins, lack of clustering. The cheapest query reads the least data.
*Seasoned call:* Make the engine prune (partition/cluster on filter columns), pre-aggregate hot paths into materialized tables, cache where stable, and fix join order/explosions. The Staff skill is reading the plan and knowing which lever applies.

**4. Materialized views / pre-aggregation vs on-the-fly**
*Problem:* Many dashboards recompute the same heavy aggregation repeatedly.
*The fork:* Materialize/precompute (fast reads, storage + refresh cost, staleness) vs compute on demand (always fresh, expensive, slow).
*What you weigh:* Read frequency vs freshness tolerance vs refresh cost. Materialization trades compute-at-read for compute-at-write plus staleness.
*Seasoned call:* Materialize aggregations that are read far more often than the underlying data changes; keep on-demand for low-frequency or freshness-critical queries. Build a metrics/semantic layer so definitions don't fork across dashboards.

**5. The cost-control problem (FinOps for data)**
*Problem:* The warehouse/compute bill is growing faster than usage justifies and leadership is asking why.
*The fork:* Attribution + governance (tag, monitor, chargeback) vs blunt cuts vs over-provisioning for safety.
*What you weigh:* Where is the spend going (runaway queries, idle warehouses, oversized clusters, redundant pipelines)? You can't optimize what you can't attribute.
*Seasoned call:* Instrument cost per workload/team first, then attack the biggest line items: auto-suspend idle compute, right-size, kill redundant pipelines, add query guardrails. Make cost a visible, owned metric — not a quarterly surprise.

**6. Workload isolation and the noisy-neighbor problem**
*Problem:* A heavy ad-hoc query tanks performance for production dashboards on shared compute.
*The fork:* Separate compute per workload class (isolation, more to manage) vs shared with resource governance vs one big pool (cheap, contended).
*What you weigh:* Isolation guarantees SLAs but fragments capacity; sharing is efficient but risks contention. Most modern warehouses make per-workload virtual compute easy.
*Seasoned call:* Isolate production/SLA-bound workloads from ad-hoc/experimental ones on separate compute; use resource governance within shared pools. Don't let exploratory queries threaten production SLAs.

**7. Concurrency and scaling for many users**
*Problem:* At peak, hundreds of concurrent dashboard queries queue and latency spikes.
*The fork:* Auto-scaling concurrency (multi-cluster) vs caching/materialization to reduce load vs queueing.
*What you weigh:* Cost of scaling out for peaks vs reducing the work per query. Concurrency scaling solves the symptom; materialization/caching reduces the cause.
*Seasoned call:* Reduce per-query work (cache, materialize, prune) first, then auto-scale concurrency for genuine peaks. Scaling compute to brute-force inefficient queries is the expensive shortcut.

**8. Query federation vs centralization**
*Problem:* Data lives in many systems; do you move it all to one place or query across them?
*The fork:* Centralize (copy everything into one warehouse/lake — consistent, costly, duplicative) vs federate (query in place across engines — no copies, but performance and governance complexity).
*What you weigh:* Data gravity, freshness, governance, and the cost of duplication vs the performance hit of cross-system queries.
*Seasoned call:* Centralize the high-value, frequently-joined data; federate for occasional cross-system access or where copying is prohibited. Open table formats + a shared catalog increasingly let multiple engines read one copy, easing this.

**9. Caching layers and serving low-latency queries**
*Problem:* An application needs sub-second query responses the warehouse can't reliably hit.
*The fork:* Result/BI cache vs a serving store (key-value, OLAP cube, search/vector index) vs pre-materialized serving tables.
*What you weigh:* Latency requirement, freshness, and whether the access pattern is point-lookup (KV) or aggregate (OLAP). The analytical warehouse is often the wrong tool for app-facing low-latency serving.
*Seasoned call:* Don't serve app-latency traffic directly from the analytical warehouse; precompute into a purpose-built serving store. Match the serving store to the access pattern.

**10. Cost vs performance vs freshness — the eternal triangle**
*Problem:* Stakeholders want fast, fresh, and cheap; you can give two.
*The fork:* Pick the two that match the business need for each workload.
*What you weigh:* Each workload has a different point on the triangle. Treating them uniformly over- or under-serves most of them.
*Seasoned call:* Make the trade-off explicit per data product (e.g., "this mart is hourly-fresh and cheap, not sub-minute"). The Staff move is forcing the conversation about which two matter, rather than silently optimizing for one.

**11. Right-sizing compute and auto-scaling policy**
*Problem:* Clusters/warehouses are over-provisioned "to be safe" and idle much of the time.
*The fork:* Aggressive auto-suspend/auto-scale (cost-optimal, occasional cold-start latency) vs always-on (predictable, wasteful).
*What you weigh:* Cold-start latency tolerance vs idle cost. Most idle compute is pure waste with an easy fix.
*Seasoned call:* Auto-suspend idle compute aggressively, auto-scale to load, keep always-on only for genuinely latency-critical paths. Idle compute is usually the fastest cost win available.

**12. Multi-cloud / multi-warehouse strategy**
*Problem:* Leadership wants to avoid lock-in or you've inherited two clouds via acquisition.
*The fork:* Single-cloud/single-warehouse (simple, lock-in, best integration) vs multi-cloud (resilience, leverage, large complexity and cost tax).
*What you weigh:* The genuine cost of portability (often high) vs the real risk of lock-in (often overstated). Multi-cloud doubles operational surface.
*Seasoned call:* Default to one primary platform done well; pursue multi-cloud only for concrete reasons (regulatory, real negotiating leverage, acquisitions), and lean on open formats/catalogs to keep data portable rather than maintaining two of everything.

---

*Cross-references: table-format and layout decisions in 02; processing-engine choice in 03; governance/catalog in 07.*
