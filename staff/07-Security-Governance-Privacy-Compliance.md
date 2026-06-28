# 07 · Security, Governance, Privacy & Compliance

At scale and over a long career, governance stops being paperwork and becomes architecture. These scenarios recur in every regulated or large organization.

---

**1. Access control at scale (RBAC vs ABAC vs policy-as-code)**
*Problem:* Managing who can see what across thousands of tables and hundreds of users has become unmanageable.
*The fork:* Role-based (RBAC — simple, coarse, role explosion at scale) vs attribute-based (ABAC — fine-grained, flexible, complex) vs policy-as-code (rules version-controlled and auto-enforced).
*What you weigh:* Granularity needs, auditability, and maintenance. RBAC role explosion is a real failure mode; ABAC/policy-as-code scales but needs tooling and discipline.
*Seasoned call:* RBAC for coarse structure, ABAC for fine-grained needs (row/column/tag-based), expressed as policy-as-code so rules are versioned, testable, and automatically enforced. Manual, ticket-driven access management doesn't scale past a point.

**2. PII handling and classification**
*Problem:* Sensitive data is scattered across the platform with no consistent protection.
*The fork:* Classify-and-protect at ingestion (shift-left, consistent) vs protect at consumption (flexible, leaky) vs ad hoc.
*What you weigh:* Where PII enters, how it's tagged, and how protection (masking, tokenization, encryption) is applied and enforced. Discovering PII after the fact is far harder than tagging it at the door.
*Seasoned call:* Classify and tag sensitive data at ingestion, drive masking/tokenization/access from those tags via central policy, and run automated discovery to catch what slips through. Make PII protection a property of the platform, not of each pipeline.

**3. The "right to be forgotten" / GDPR deletion**
*Problem:* A user requests deletion; their data is replicated across raw lakes, warehouses, backups, and immutable formats.
*The fork:* Hard delete everywhere (compliant, fights immutability and breaks reproducibility) vs crypto-shredding (delete the key, render data unreadable) vs tombstoning/soft delete.
*What you weigh:* Immutable formats and append-only logs resist deletion; finding every copy is the hard part. Crypto-shredding (per-subject encryption keys, delete the key) elegantly handles immutable stores.
*Seasoned call:* Design deletion-by-design: maintain lineage of where subject data lands, use crypto-shredding for immutable layers, and have a tested deletion workflow across all stores including backups within retention. Retrofitting deletion onto a platform that never planned for it is brutal — design it in.

**4. Data residency and sovereignty**
*Problem:* Regulations require certain data to stay within a country/region.
*The fork:* Regional isolation (compliant, complex, fragmented) vs global with controls vs data localization per regulation.
*What you weigh:* Which data is subject to which law, cross-border transfer rules, and the architectural cost of regional partitioning. Getting this wrong is a legal, not just technical, problem.
*Seasoned call:* Partition data by residency requirements at the architecture level, keep processing in-region where mandated, and maintain a clear map of what's governed by what. Treat residency as a hard architectural constraint, not a config flag.

**5. Encryption (at rest, in transit, in use)**
*Problem:* Defining the encryption and key-management posture.
*The fork:* Platform-managed keys (simple) vs customer-managed keys (control, complexity) vs bring-your-own-key / HSM; plus emerging needs for encryption-in-use.
*What you weigh:* Compliance requirements, key-management overhead, and the trust boundary with the cloud provider. Key management is usually the hard part, not encryption itself.
*Seasoned call:* Encrypt at rest and in transit by default; use customer-managed keys where compliance or trust boundaries require; invest in disciplined key management and rotation. Encryption is easy; key governance is where it goes wrong.

**6. Data lineage and impact analysis**
*Problem:* Nobody can answer "where did this number come from?" or "what breaks if I change this table?"
*The fork:* Automated end-to-end lineage (catalog/observability-derived) vs manual documentation (rots immediately) vs none.
*What you weigh:* Lineage powers debugging, impact analysis, compliance, and trust. Manual lineage is always out of date; automated lineage requires tooling integration.
*Seasoned call:* Invest in automated, column-level lineage via the catalog/observability layer. Lineage is foundational infrastructure — it pays back in every incident, audit, and migration. Manual lineage is theater.

**7. Data cataloging and discoverability**
*Problem:* People can't find trustworthy data, so they rebuild duplicates, multiplying inconsistency.
*The fork:* Central catalog with ownership/quality metadata vs tribal knowledge vs scattered docs.
*What you weigh:* A catalog turns a data swamp into a navigable product set, but only if it's populated, owned, and trusted. An empty or stale catalog is worse than none.
*Seasoned call:* Stand up a catalog with clear ownership, descriptions, freshness, and quality signals; make registration part of the data-product lifecycle. Discoverability is what stops the endless duplication of "is this table right?"

**8. Audit logging and compliance evidence**
*Problem:* An auditor asks who accessed sensitive data and how a regulated dataset was produced.
*The fork:* Comprehensive immutable audit logs (compliant, storage cost) vs minimal logging (cheap, fails audits) vs reconstruct-on-demand (fragile).
*What you weigh:* What regulators require, retention duration, and tamper-resistance. Audit needs are predictable — build for them rather than scrambling at audit time.
*Seasoned call:* Capture access and lineage audit trails in immutable, retained logs aligned to your compliance regime, queryable for evidence. Make audit a designed capability, not a fire drill.

**9. Governance as enabler vs blocker**
*Problem:* Governance has become a committee that slows everything down, so teams route around it.
*The fork:* Governance-as-code (automated, embedded, fast) vs committee-based approval (thorough, slow, bypassed) vs no governance (fast, chaotic).
*What you weigh:* Friction drives shadow IT. Automated, embedded guardrails enforce policy without becoming a queue; committees become bottlenecks people evade.
*Seasoned call:* Shift governance into automated, codified guardrails embedded in the platform (policy-as-code, automated classification, contract enforcement) so the compliant path is the easy path. Governance that blocks gets bypassed; governance that enables gets adopted.

**10. Multi-tenancy and data isolation**
*Problem:* One platform serves many teams/customers who must not see each other's data.
*The fork:* Hard isolation (separate stores/compute per tenant — safe, costly, fragmented) vs logical isolation (shared infra, policy-enforced separation — efficient, requires airtight controls) vs hybrid.
*What you weigh:* Blast radius of a leak, cost of duplication, and the strength of your logical controls. The cost of a cross-tenant leak can be existential.
*Seasoned call:* Logical isolation with rigorously tested policy enforcement for internal multi-tenancy; hard isolation where a leak would be catastrophic or contractually forbidden. Test isolation as adversarially as you'd test security.

**11. Governing AI/ML assets (the new frontier)**
*Problem:* Governance was built for rows and columns, but now embeddings, prompts, models, and autonomous agents access data.
*The fork:* Extend the governance control plane to cover AI assets (unified, forward-looking) vs treat AI data as ungoverned (fast, risky) vs separate AI governance silo.
*What you weigh:* An agent retrieving a confidential document via a RAG pipeline can leak data that traditional column permissions never anticipated. Governance must now cover vector stores, embeddings, prompts, and agent access.
*Seasoned call:* Extend lineage, classification, and access policy to embeddings, vector indexes, and agent retrieval paths through a unified governance layer. This is an emerging gap in most orgs and a strong place for a Staff engineer to lead. (Continues in 08.)

**12. Retention and legal hold**
*Problem:* You must delete old data for privacy but preserve specific data under legal hold.
*The fork:* Blanket retention policy vs per-dataset retention with legal-hold overrides vs keep-everything (privacy and cost risk).
*What you weigh:* Competing obligations — minimization (delete) vs preservation (hold) — and the precedence between them. These can directly conflict.
*Seasoned call:* Implement retention policies with explicit legal-hold overrides that suspend deletion for held data, documented and auditable. Resolve the delete-vs-preserve conflict with legal explicitly, in policy-as-code, not engineer-by-engineer.

---

*Cross-references: classification feeds access in 02/04; deletion ties to immutable formats in 02; AI-asset governance continues in 08; governance-as-enabler is an org theme in 09.*
