# 06 · Platform Architecture, Scale & Migrations

This is the Staff-defining document: designing whole platforms, choosing organizational architectures, and running the migrations that careers are remembered for.

---

**1. Centralized platform vs data mesh**
*Problem:* The central data team has become a bottleneck; every domain waits in a queue.
*The fork:* Centralized (consistent, governed, but a scaling bottleneck) vs data mesh (domain-owned data products, self-serve platform, federated governance — scalable but a major sociotechnical change) vs hybrid.
*What you weigh:* Org size and maturity, domain capability, and appetite for organizational change. Pure mesh fails without leadership buy-in and producer maturity; pure centralization fails at scale.
*Seasoned call:* The matured 2026 pattern is hybrid: a strong central platform owning the "plumbing" (storage, compute, identity, observability, governance standards) while domains own the "last mile" (their pipelines and data products). Mesh is an org change first, a tech change second — don't "install" it.

**2. Build vs buy**
*Problem:* Deciding whether to build a capability (orchestrator, catalog, quality tool, connectors) or adopt a vendor.
*The fork:* Build (control, fit, but ongoing maintenance and opportunity cost) vs buy (speed, support, but cost and lock-in) vs open-source-and-operate (middle ground, you own the ops).
*What you weigh:* Is this core differentiating IP or commodity plumbing? The true cost of building includes maintenance forever. Most data infrastructure is commodity — building it rarely pays.
*Seasoned call:* Buy/adopt commodity infrastructure; build only what's genuinely differentiating or where no adequate option exists. The classic Staff mistake to prevent is a team lovingly maintaining a homegrown framework that a product does better and cheaper.

**3. The big migration: on-prem/legacy → cloud lakehouse**
*Problem:* You must move a large legacy warehouse (Teradata/Oracle/Hadoop) to a modern cloud platform with no downtime and no lost trust.
*The fork:* Big-bang cutover (fast, terrifying, high blast radius) vs strangler-fig incremental migration (slow, safe, dual-running cost) vs lift-and-shift then optimize.
*What you weigh:* Risk tolerance, dual-running cost, dependency mapping, and how to prove parity. The hardest part is reconciliation: proving the new platform produces identical results.
*Seasoned call:* Incremental strangler-fig: migrate domain by domain, run old and new in parallel, reconcile outputs rigorously, cut consumers over once trust is proven, then decommission. Budget heavily for parallel-run reconciliation — it's where migrations succeed or lose credibility.

**4. Real-time vs batch platform architecture (Lambda vs Kappa)**
*Problem:* You need both historical accuracy and low-latency views.
*The fork:* Lambda (separate batch + speed layers — accurate + fast, but two codebases to keep in sync) vs Kappa (one streaming codebase, replay history through it — simpler, demands streaming maturity) vs streaming-first lakehouse.
*What you weigh:* The dual-codebase maintenance tax of Lambda vs the operational maturity Kappa requires. Modern streaming + replayable logs make Kappa-style increasingly viable.
*Seasoned call:* Avoid maintaining two implementations of the same logic; favor a single processing path that handles live and replayed data (Kappa-leaning) where streaming maturity allows. Use Lambda only when forced.

**5. Multi-region and disaster recovery**
*Problem:* A regional outage could take the platform down, or data residency laws require regional isolation.
*The fork:* Active-active (resilient, complex, consistency challenges) vs active-passive/failover (simpler, RTO/RPO trade-offs) vs single-region (cheap, risky).
*What you weigh:* RTO/RPO requirements, cost of replication, consistency across regions, and residency constraints. Most "we need active-active" requirements are actually "we need a tested failover."
*Seasoned call:* Match the topology to real RTO/RPO and residency needs; for most analytics, active-passive with tested, regular failover drills is sufficient and far cheaper than active-active. Untested DR is the same as no DR.

**6. Scaling from gigabytes to petabytes**
*Problem:* An architecture that worked at small scale falls apart as volume grows 100x.
*The fork:* Re-architect proactively (cost now, avoids the wall) vs scale-up the current design (delays the inevitable) vs fix it under fire later.
*What you weigh:* Where the current design breaks (single-node assumptions, full scans, monolithic jobs, metadata limits) and at what volume. Architectures have scale ceilings; knowing where yours is matters.
*Seasoned call:* Identify the scale ceiling early and re-architect the bottleneck before hitting it — partition, distribute, decouple, and remove single-node assumptions. The Staff signal is predicting the wall before you hit it.

**7. The monolithic pipeline that everything depends on**
*Problem:* One enormous critical pipeline is fragile, slow to change, and owned by one nervous person.
*The fork:* Decompose into modular, independently-deployable pipelines vs keep the monolith (familiar, fragile) vs rewrite.
*What you weigh:* Decomposition cost vs the ongoing risk and change-friction of the monolith. Bus-factor-of-one on a critical pipeline is an org risk.
*Seasoned call:* Incrementally decompose into modular pipelines with clear interfaces and shared ownership; reduce the bus factor. Don't rewrite a working monolith wholesale — strangler-fig it.

**8. Self-serve platform vs gatekeeping**
*Problem:* Every data request funnels through your team, creating a queue and resentment.
*The fork:* Self-serve platform (analysts/domains build their own with guardrails — scales, requires platform investment) vs gatekept (control, bottleneck) vs free-for-all (chaos, no governance).
*What you weigh:* Platform-engineering investment vs the bottleneck cost. Self-serve requires real tooling, templates, and guardrails to not become chaos.
*Seasoned call:* Invest in a self-serve platform with paved roads (templates, standards, automated governance) so domains move fast safely. Treat the internal platform as a product with users. This is how a data team scales beyond its headcount.

**9. Vendor lock-in vs best-of-breed integration**
*Problem:* A single-vendor suite is convenient but locks you in; best-of-breed is flexible but you own the integration.
*The fork:* All-in on one platform (integration, support, lock-in) vs assemble best-of-breed (flexibility, integration burden, open formats).
*What you weigh:* The real cost and probability of needing to switch vs the ongoing integration tax of best-of-breed. Open formats and catalogs reduce lock-in without full best-of-breed pain.
*Seasoned call:* Accept pragmatic lock-in for managed convenience where switching is unlikely, but keep your *data* portable via open table formats and open catalogs. Portability of data matters more than portability of tooling.

**10. Greenfield platform design from scratch**
*Problem:* You're given a blank slate to design a data platform for a company's next decade.
*The fork:* Countless — but the meta-decision is designing for *known* requirements vs over-engineering for *imagined* future scale.
*What you weigh:* Current needs vs realistic growth; openness vs speed; central vs federated; the team's actual capability to operate what you design. Over-engineering for hypothetical scale is as costly as under-designing.
*Seasoned call:* Design for the next 2–3x of realistic growth, not 100x fantasy; favor open formats and decoupled storage/compute; keep it operable by the team you actually have; document the decisions and their trade-offs in an architecture decision record. Make reversible choices quickly and irreversible ones carefully.

**11. Consolidating after an acquisition / merging two platforms**
*Problem:* Your company acquired another; now there are two clouds, two warehouses, two of everything.
*The fork:* Migrate one into the other (consolidation cost, eventual simplicity) vs federate and coexist (faster, permanent complexity) vs rebuild unified.
*What you weigh:* Integration timeline pressure, duplicate cost, team disruption, and which platform is actually better. Coexistence often becomes permanent by default.
*Seasoned call:* Pick a target platform deliberately and migrate toward it on a real timeline; use federation only as a bridge, not a destination. Decide consciously — "we'll consolidate later" usually means "never."

**12. Designing for an unknown future requirement**
*Problem:* The business can't tell you what they'll need in two years, but you must design now.
*The fork:* Flexible/general design (adaptable, more complex now) vs specific/simple (fast now, may need rework) vs modular with clear seams.
*What you weigh:* Reversibility of decisions. Some choices are cheap to change later (transforms, marts); some are expensive (storage format, partitioning, core modeling).
*Seasoned call:* Make the expensive-to-reverse decisions (formats, core layout, catalog) conservatively and flexibly; make the cheap-to-reverse ones (specific marts, transforms) simply and quickly. Optionality where it's cheap, commitment where it's costly.

---

*Cross-references: format/catalog portability in 02; cost of multi-region/multi-cloud in 04; the human side of these decisions in 09.*
