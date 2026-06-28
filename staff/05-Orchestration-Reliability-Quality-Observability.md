# 05 · Orchestration, Reliability, Quality & Observability

Pipelines are software. This document covers running them like software — and the reliability, quality, and on-call scenarios that define whether a platform is trusted.

---

**1. Orchestration: task-centric vs asset-centric**
*Problem:* Choosing/standardizing an orchestrator as the platform grows.
*The fork:* Task-centric (Airflow — mature, ubiquitous, imperative DAGs) vs asset/data-aware (Dagster-style — models data assets and lineage natively) vs lightweight (Prefect) vs warehouse-native scheduling.
*What you weigh:* Team familiarity, how much you care about data-asset lineage vs task execution, and operational maturity. Airflow's ubiquity vs newer tools' data-awareness.
*Seasoned call:* Airflow remains the safe default for broad task orchestration; asset-centric tools shine when lineage and data-product thinking are central. Pick one and standardize — orchestrator sprawl is its own problem.

**2. Idempotency and safe re-runs**
*Problem:* A retried or backfilled task corrupts or double-writes data.
*The fork:* Idempotent design (partition-overwrite, upsert-by-key, deterministic outputs) vs append-and-pray vs manual cleanup.
*What you weigh:* Idempotency is the foundation of painless operations; without it every retry is a risk and every backfill is a manual ordeal.
*Seasoned call:* Every task must be safely re-runnable by design. This is the highest-leverage reliability discipline there is.

**3. Backfills at scale**
*Problem:* You must reprocess two years of history after a logic fix — without melting the cluster or the bill.
*The fork:* One giant backfill (risky, expensive, blocks the cluster) vs partitioned/throttled backfill (safe, slower) vs shadow-then-swap.
*What you weigh:* Resource contention with live pipelines, cost, and correctness verification. Big-bang backfills are a classic incident source.
*Seasoned call:* Backfill in bounded, idempotent partitions with throttling and verification at each step; isolate backfill compute from production; validate before swapping in. Never backfill a huge range in one unthrottled job.

**4. Dependency management and DAG complexity**
*Problem:* The DAG has grown into a tangled mess; one upstream delay cascades unpredictably.
*The fork:* Tightly coupled mega-DAG vs decoupled pipelines triggered by data-availability/events vs scheduled-with-sensors.
*What you weigh:* Coupling makes failures cascade and changes risky; event/data-driven triggering decouples but adds complexity. Time-based scheduling alone causes brittle "hope it finished" dependencies.
*Seasoned call:* Decouple via data-availability signals/sensors rather than fragile time offsets; keep DAGs modular with clear ownership boundaries. A mega-DAG that one team can't reason about is a reliability liability.

**5. SLAs / SLOs for data**
*Problem:* Consumers don't know when data will be ready or how fresh it is, and complain when it's late.
*The fork:* Define explicit freshness/availability SLOs (and own them) vs implicit best-effort (constant friction).
*What you weigh:* What latency/freshness do consumers actually need? SLOs turn vague expectations into measurable commitments and let you prioritize.
*Seasoned call:* Publish freshness and availability SLOs per data product, monitor against them, and alert on SLO breaches — not on every internal hiccup. SLOs are how you make reliability a contract rather than a complaint.

**6. Data quality testing (shift-left)**
*Problem:* Bad data reaches dashboards and erodes trust before anyone notices.
*The fork:* Test at the pipeline (unit/contract/expectation tests, fail fast) vs monitor outputs only (catch after the fact) vs no testing.
*What you weigh:* Catching issues before they propagate vs the effort of writing/maintaining tests. The 2026 norm is shift-left: validate schemas and expectations early, ideally before data lands.
*Seasoned call:* Embed quality tests (schema, nullity, ranges, referential integrity, freshness) into pipelines and CI; fail or quarantine on breach for critical data. Shift validation as far left as possible — catching a break before deploy beats explaining a wrong dashboard.

**7. Data contracts**
*Problem:* Upstream changes keep silently breaking downstream consumers.
*The fork:* Formal data contracts (schema + SLOs as code, enforced in producer CI) vs informal coordination vs defensive downstream handling.
*What you weigh:* Contracts shift ownership of breakage to producers and prevent a large share of incidents, but require org buy-in and tooling (contract-as-code, registries).
*Seasoned call:* Drive contract-first for critical producer→consumer boundaries: versioned schema + guarantees enforced automatically in the producer's pipeline. This is now a mainstream, high-ROI practice — most data downtime traces to unexpected schema change a contract would have caught.

**8. Data observability**
*Problem:* You find out data is broken when an executive sees a wrong number.
*The fork:* Proactive observability (freshness, volume, distribution, schema-drift anomaly detection) vs reactive firefighting.
*What you weigh:* Observability tells you *what* is wrong and *where*, not just *that* something is. Increasingly AI-assisted anomaly detection reduces alert tuning.
*Seasoned call:* Instrument freshness, volume, schema, and distribution monitors on key tables with anomaly detection and lineage-aware alerting. The goal is to detect issues before consumers do — observability is to data what monitoring is to services.

**9. Alerting and avoiding alert fatigue**
*Problem:* On-call is drowning in alerts; the real incidents get lost in the noise.
*The fork:* Alert on symptoms users feel (SLO breaches) vs alert on every internal anomaly vs under-alert.
*What you weigh:* Signal-to-noise. Too many alerts train people to ignore them; too few miss real incidents. Alert ownership and runbooks matter as much as thresholds.
*Seasoned call:* Page only on consumer-impacting SLO breaches with clear runbooks; route everything else to dashboards/tickets. Ruthlessly prune noisy alerts — alert fatigue is itself a reliability risk.

**10. Incident response and the postmortem**
*Problem:* A bad data incident reaches production; how you respond defines team trust.
*The fork:* Blameless root-cause + systemic fix vs blame + quick patch vs silent fix.
*What you weigh:* The immediate fix (stop the bleeding, communicate, restate data) vs the durable fix (the systemic gap that allowed it). Blameless culture surfaces real causes.
*Seasoned call:* Communicate early and honestly, restate affected data, then run a blameless postmortem that produces a concrete systemic action — not just "be more careful." Repeated incidents with the same root cause are an org failure to learn.

**11. CI/CD for data pipelines**
*Problem:* Pipeline changes are deployed by hand and occasionally break production.
*The fork:* Full CI/CD (version control, tests, staged deploys, rollback) vs manual deploys vs notebook-driven changes straight to prod.
*What you weigh:* Pipelines are software and deserve software rigor: PRs, tests, environments, rollback. Notebook-to-prod is fast and dangerous.
*Seasoned call:* Treat pipelines as code: version-controlled, tested in CI, promoted through environments, with safe rollback and data-aware deployment (blue/green or shadow runs for risky changes). This is non-negotiable at scale.

**12. Pipeline failure semantics: fail fast vs degrade gracefully**
*Problem:* A non-critical source is missing — should the whole pipeline stop?
*The fork:* Fail the whole run (safe, blocks everything) vs continue with partial data (available, risky) vs quarantine the bad part and proceed.
*What you weigh:* Criticality of the missing piece and the cost of stale-but-complete vs fresh-but-partial. One-size failure policy is wrong.
*Seasoned call:* Define criticality per dependency: hard-fail on critical inputs, quarantine-and-continue on non-critical ones with clear flagging. Make the degradation behavior explicit and visible, never silent.

**13. Reprocessing and restatement of historical data**
*Problem:* A metric definition changed; historical reports must be restated consistently.
*The fork:* Restate history (consistent, expensive, may confuse consumers expecting stable numbers) vs apply change going forward only (stable past, inconsistent series) vs versioned metrics.
*What you weigh:* Consumer expectations, auditability, and cost. Silently restating closed financials is dangerous; never restating breaks trend analysis.
*Seasoned call:* Decide restatement policy explicitly with stakeholders; version metric definitions and communicate changes; treat restatement of reported/financial numbers as a governed, audited event.

---

*Cross-references: idempotency underpins 01/03 backfills; SLAs feed cost trade-offs in 04; contracts originate at the boundary in 01.*
