# Enterprise System Design

This folder collects **enterprise-grade architecture** references for senior/staff engineers and architects. The focus is not "how to build a service" but **how to build, integrate, secure, operate, and govern a portfolio of systems at organizational scale** — where the constraints are regulatory, financial, organizational, and multi-decade rather than purely technical.

Enterprise systems differ from greenfield startups in predictable ways:

- **Heterogeneity is permanent.** You will never have a single language, cloud, or datastore. Mainframes, SAP, Salesforce, three clouds, and a fleet of microservices coexist for years.
- **Conway's Law dominates.** Architecture mirrors org structure; you design systems and teams together.
- **Compliance is a first-class requirement**, not a feature — SOX, PCI-DSS, HIPAA, GDPR, SOC 2, Basel, etc.
- **Change is continuous and risky.** You are almost always migrating *from* something while running it in production.
- **Total cost of ownership** (licensing, FinOps, headcount) frequently outweighs raw engineering elegance.

---

## Core Docs

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Enterprise Architecture Patterns](./01_enterprise_architecture_patterns.md) | Layered/n-tier, hexagonal/clean, DDD, microservices, EDA, CQRS/ES, modular monolith, strangler fig, Conway's Law & team topologies |
| 02 | [Enterprise Integration](./02_enterprise_integration.md) | EIP, ESB vs API-led, API gateways, Kafka/MQ/RabbitMQ, B2B/EDI, webhooks, ACL, idempotency, outbox |
| 03 | [Multi-Tenancy](./03_multi_tenancy.md) | Isolation models (silo/pool/bridge), tenant routing & context, RLS, noisy-neighbor, metering & billing, onboarding |
| 04 | [High Availability & DR](./04_high_availability_dr.md) | RTO/RPO, redundancy, multi-AZ/region, failover, backup strategies, chaos engineering |
| 05 | [IAM & Security](./05_iam_security.md) | AuthN/AuthZ, OAuth2/OIDC/SAML, zero trust, secrets, encryption, network security |
| 06 | [Data Platform](./06_data_platform.md) | Lakehouse, warehouse, streaming, governance, lineage, master data management |
| 07 | [Compliance & Governance](./07_compliance_governance.md) | SOX/PCI/HIPAA/GDPR, audit, data residency, retention, policy as code |
| 08 | [Cloud Architecture](./08_cloud_architecture.md) | Landing zones, multi-account/subscription, networking, IaC, FinOps, hybrid/multi-cloud |
| 09 | [Observability & Ops](./09_observability_ops.md) | Metrics/logs/traces, SLO/SLI/error budgets, incident management, on-call, AIOps |
| 10 | [Migration & Modernization](./10_migration_modernization.md) | 6 R's, mainframe offload, data migration, cutover, dual-run, risk management |

> Docs 04–10 are scoped in this index; 01–03 are authored in full here.

## Scenarios

Worked end-to-end designs that apply the core patterns to a concrete domain. See [`scenarios/`](./scenarios/):

| Scenario | Dominant concerns |
|----------|-------------------|
| `banking_payments` | Strong consistency, idempotency, PCI-DSS, audit, settlement |
| `ecommerce_platform` | Elastic scale, catalog/search, cart/checkout, peak events |
| `healthcare_records` | HIPAA/PHI, interoperability (HL7/FHIR), consent, longevity |
| `insurance_claims` | Workflow/BPM, document/ML, fraud, regulatory reporting |
| `supply_chain_logistics` | EDI/B2B, event tracking, partner integration, IoT telemetry |
| `trading_platform` | Ultra-low latency, ordering, market data, risk limits |
| `telecom_billing` | High-volume rating/charging, mediation, revenue assurance |
| `crm_platform` | Multi-tenancy, customization, integration sprawl |
| `erp_hr` | Master data, process integration, data residency, GDPR |
| `streaming_media` | CDN/edge, DRM, recommendation, concurrency at scale |

---

## Enterprise Concerns Checklist

Use this as a gate review for any enterprise design. A design is not "done" until each row has an explicit, defensible answer.

### Scalability
- [ ] Stated load profile (steady-state + peak) and growth horizon (3–5 yrs)
- [ ] Horizontal scaling path; no hidden single-writer bottleneck
- [ ] Statelessness where possible; partitioning/sharding strategy defined
- [ ] Back-pressure and load-shedding behavior under overload

### Availability
- [ ] Target SLA (e.g. 99.95%) with supporting SLOs/SLIs
- [ ] Multi-AZ by default; multi-region for tier-1 systems
- [ ] No single points of failure; graceful degradation paths
- [ ] Dependency failure modes (timeouts, circuit breakers, bulkheads)

### Security
- [ ] AuthN/AuthZ model (OIDC/SAML, RBAC/ABAC), zero-trust posture
- [ ] Encryption in transit (TLS 1.2+) and at rest (KMS/HSM)
- [ ] Secrets management (Vault/KMS), key rotation
- [ ] Network segmentation, WAF, threat model, SBOM/supply-chain controls

### Compliance
- [ ] Applicable regimes identified (SOX, PCI, HIPAA, GDPR, SOC 2…)
- [ ] Data classification + residency + retention/deletion policies
- [ ] Audit trail: immutable, time-synced, queryable
- [ ] Evidence collection automated (policy-as-code where feasible)

### Cost (FinOps)
- [ ] Unit economics (cost per tenant / transaction / GB)
- [ ] Tagging/showback/chargeback model
- [ ] Reserved/committed vs on-demand strategy; egress costs accounted
- [ ] Build-vs-buy and licensing TCO evaluated

### Observability
- [ ] Metrics, logs, traces with correlation IDs; standardized via OpenTelemetry
- [ ] Dashboards + alerting tied to SLOs, not vanity metrics
- [ ] Distributed tracing across service and integration boundaries
- [ ] Synthetic monitoring + real-user monitoring

### Disaster Recovery
- [ ] RTO/RPO defined per system tier and agreed with business
- [ ] Backups: scope, frequency, encryption, **tested restores**
- [ ] DR runbooks and regular game-days/failover drills
- [ ] Cross-region/cross-account isolation of backups (ransomware resilience)

### Governance
- [ ] Architecture decision records (ADRs) and review cadence
- [ ] Standards: golden paths, paved roads, approved tech radar
- [ ] Ownership model (every system has an owning team + on-call)
- [ ] Change management, dependency/version policy, deprecation process

---

## How to use this set

1. Start with **01** to pick the architectural style appropriate to the domain and team topology.
2. Use **02** when systems must talk — almost always — and to avoid integration anti-patterns.
3. Use **03** if you are building SaaS or any shared platform.
4. Layer in **04–10** for the cross-cutting "ilities".
5. Validate against a scenario in `scenarios/` and the checklist above.

### Key Takeaways
- Enterprise architecture is a **portfolio and organizational** discipline, not a single-system one.
- The hard parts are integration, compliance, and change — not writing services.
- Every design decision is a **trade-off**; record it (ADRs) and revisit it.
- "Boring," well-understood technology usually beats novelty at enterprise scale.
