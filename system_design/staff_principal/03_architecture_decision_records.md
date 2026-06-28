# 03 — Architecture Decision Records (ADRs)

> "Why on earth is it built like *this*?" — every engineer who joins a codebase, about a decision made two years ago by someone who has since left. The ADR is the answer to that question, written down on purpose.

An **Architecture Decision Record (ADR)** is a short document capturing **one significant architectural decision**, the **context** that forced it, and its **consequences**. The concept and its now-standard format come from **Michael Nygard's 2011 post "Documenting Architecture Decisions."** This document covers what ADRs are and why they matter, gives you copy-pasteable templates (Nygard's and the MADR variant), explains when to write one, how to run an ADR log, the decision lifecycle, and three worked examples.

---

## 1. What an ADR is, and why

A design doc (see [02](02_design_docs_and_rfcs.md)) explains *how to build a whole thing*. An ADR captures *one decision* and — crucially — **its rationale**. The two complement each other: a big RFC often produces several ADRs.

The single most important thing an ADR preserves is **the "why."** Code shows you *what* was built. Comments show *how* it works. Almost nothing in a normal codebase records *why this option was chosen over the alternatives that were on the table at the time.* That "why" is the most expensive knowledge to reconstruct and the first to evaporate when people leave.

Why bother:

- **Defeats the "Mysterious Decision" problem.** Future you (and new hires) can see the reasoning instead of guessing — or worse, "fixing" something that was a deliberate trade-off.
- **Prevents re-litigating settled debates.** "We already evaluated MongoDB; here's the ADR and why we passed" ends the same argument for the fourth time.
- **Makes consequences explicit.** Forcing yourself to write the downsides surfaces them while you can still change course.
- **Onboards people cheaply.** Reading the ADR log is the fastest way to understand *how a system came to be the way it is.*
- **Lightweight by design.** Nygard's whole point: ADRs are short (a page or two), plain-text, and live *in the repo next to the code* — not in a wiki that rots. Cheap enough that people actually write them.

> **ADRs record decisions, not designs.** If you're describing *how the system works*, that's documentation/a design doc. If you're answering *"why did we decide X instead of Y, given the situation Z?"* — that's an ADR.

---

## 2. The Nygard ADR template

Nygard's original format has just four substantive sections. Its power is its brevity. Copy this:

```markdown
# ADR-NNN: <Short noun phrase naming the decision>

## Status
Proposed | Accepted | Deprecated | Superseded by ADR-MMM
(Date: YYYY-MM-DD)

## Context
The forces at play: technical, business, political, team. What is the
situation that *requires* a decision? Describe these as value-neutral
facts. This is where the "why this is even a question" lives. Include
constraints, requirements, and the relevant assumptions.

## Decision
The decision, stated in active voice: "We will <do X>."
Be specific and unambiguous. One decision per ADR.

## Consequences
What becomes easier and what becomes harder as a result — the good,
the bad, and the neutral. Include follow-on work this decision creates,
new risks it introduces, and what it now constrains. Be honest about
the downsides; an ADR with only upsides is a sales pitch, not a record.
```

That's it. The discipline is in writing **Context as neutral forces** (not a justification for the answer you already picked) and **Consequences honestly** (including what got *harder*).

---

## 3. The MADR variant

**MADR (Markdown Architectural Decision Records)** is a popular, richer template that adds explicit options and decision drivers — useful when the decision was genuinely contested and you want the alternatives on record (closer to a mini-RFC). Use it when Nygard's four sections feel too thin for a high-stakes call.

```markdown
# ADR-NNN: <Title>

- Status: proposed | accepted | rejected | deprecated | superseded by ADR-MMM
- Date: YYYY-MM-DD
- Deciders: <people involved>
- Tags: <e.g. data, security>

## Context and Problem Statement
2–3 sentences. What problem are we deciding on? Frame as a question.

## Decision Drivers
- <driver 1, e.g. must support strong consistency>
- <driver 2, e.g. team already operates Postgres>
- <driver 3, e.g. p99 < 10ms>

## Considered Options
- Option A: <name>
- Option B: <name>
- Option C: <name>

## Decision Outcome
Chosen option: "<Option X>", because <justification tied to the drivers>.

### Consequences
- Good: <…>
- Bad: <…>
- Neutral / follow-up: <…>

## Pros and Cons of the Options
### Option A
- Good: …
- Bad: …
### Option B
- Good: …
- Bad: …
### Option C
- Good: …
- Bad: …

## More Information
Links to the RFC, benchmarks, related ADRs.
```

| | Nygard | MADR |
|--|--------|------|
| **Sections** | Status, Context, Decision, Consequences | + Decision Drivers, Considered Options, per-option pros/cons |
| **Best for** | Most decisions; speed; broad adoption | Contested decisions where alternatives must be on record |
| **Length** | Half a page | One to two pages |
| **Risk** | Can hide that alternatives were weighed | More ceremony; can drift toward a full RFC |

> Pick **one** format per repo/org and stick to it. Consistency makes the log scannable. Most teams start with Nygard and reach for MADR only on the big calls.

---

## 4. When to write an ADR

Write an ADR for a decision that is **architecturally significant** — Nygard's heuristic: decisions that affect the **structure, non-functional characteristics, dependencies, interfaces, or construction techniques** of the system. Practically:

**Write one when the decision is:**

- **Significant & durable** — choice of database, language, framework, messaging style, auth model.
- **Hard or expensive to reverse** — a one-way door.
- **Cross-cutting** — affects multiple components or teams.
- **Likely to be questioned later** — anything where "why is it like this?" is predictable.
- **A deliberate trade-off** — especially when you chose the *non-obvious* option (someone *will* try to "fix" it).

**Don't write one for:**

- Routine, easily reversible, local implementation choices.
- Decisions with no real alternative.
- Things better captured as code, tests, or design docs.

> A useful test: *"If a smart new engineer might undo this without realizing it was deliberate, write an ADR."*

---

## 5. Running an ADR log / repo

The format is worthless without a place it lives and a habit of writing.

**Conventions that work:**

- **Location:** `docs/adr/` (or `docs/decisions/`) **inside the repository** the decision affects, version-controlled with the code. Org-wide decisions can live in a central `architecture-decisions` repo.
- **Naming:** `NNNN-short-title.md`, zero-padded, monotonically increasing — e.g. `0007-use-postgresql-over-mongodb.md`. Numbers are immutable IDs; never renumber.
- **Index:** maintain a `README.md` table of all ADRs with status (or generate it). Tools like **`adr-tools`** (Nygard's own CLI), **`log4brains`**, or **`adr-manager`** automate creation, numbering, and the index.
- **Immutability:** **ADRs are append-only.** You do *not* edit an accepted ADR to change the decision. You write a **new** ADR that supersedes it, and mark the old one `Superseded by ADR-NNN`. The historical record is the point — preserving wrong-in-hindsight decisions *with their original context* is valuable.
- **Lightweight review:** ADRs go through normal PR review. Proposed → discussed in the PR → merged as Accepted. This keeps them cheap and close to where engineers already work.

**Example index:**

```markdown
# Architecture Decision Log

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted | 2026-01-12 |
| [0007](0007-use-postgresql-over-mongodb.md) | Use PostgreSQL over MongoDB | Accepted | 2026-02-03 |
| [0011](0011-adopt-event-driven-integration.md) | Adopt event-driven integration | Accepted | 2026-04-18 |
| [0014](0014-sessions-in-redis.md) | Store sessions in Redis | Superseded by 0021 | 2026-05-02 |
```

> **Pro move:** make your *first* ADR be `0001-record-architecture-decisions.md` — an ADR deciding to *use* ADRs. It's Nygard's own example, it documents the convention, and it bootstraps the habit.

---

## 6. Lifecycle: proposed → accepted → superseded

An ADR moves through statuses; the status is the only part you change after acceptance (and even then via supersession, not rewriting the body).

```
   ┌──────────┐  review &   ┌──────────┐
   │ Proposed │ ─────────▶  │ Accepted │
   └──────────┘  agreement  └────┬─────┘
        │                        │ time passes,
        │ rejected               │ situation changes
        ▼                        ▼
   ┌──────────┐            ┌─────────────┐    write   ┌──────────┐
   │ Rejected │            │ Deprecated  │ ─────────▶ │Superseded│
   └──────────┘            │ (no longer  │  new ADR   │ by N+k   │
                           │  recommended)│            └──────────┘
                           └─────────────┘
```

| Status | Meaning |
|--------|---------|
| **Proposed** | Drafted, under review, not yet agreed. |
| **Accepted** | Agreed and in force. This is the decision of record. |
| **Rejected** | Considered and explicitly decided against (still valuable to keep — shows it was weighed). |
| **Deprecated** | No longer the recommended approach, but no direct replacement decision yet. |
| **Superseded by ADR-N** | Replaced by a newer decision. The old ADR stays, linked forward; the new one links back. |

> When superseding, **cross-link both ways**: old gets `Superseded by ADR-0021`, new gets `Supersedes ADR-0014`. A reader landing on the old one must be able to follow the thread forward.

---

## 7. Worked example ADRs

### Example A — Nygard format

```markdown
# ADR-0007: Use PostgreSQL over MongoDB for the orders service

## Status
Accepted (2026-02-03)

## Context
The new orders service stores orders, line items, and payments. These
entities are highly relational and we require multi-row transactional
consistency (an order and its payment must commit atomically). Our team
already operates PostgreSQL for two other services and has on-call
runbooks and backup tooling for it. We evaluated MongoDB because an
adjacent team uses it and the document model superficially fits an
"order" aggregate. Projected scale is < 5k writes/sec for the next two
years — well within a single Postgres primary with read replicas.

## Decision
We will use PostgreSQL as the primary datastore for the orders service,
modeling orders/line-items/payments as related tables and using
transactions for atomic order+payment writes.

## Consequences
- Easier: strong multi-row ACID transactions out of the box; reuse of
  existing operational tooling, backups, and team expertise; rich
  querying and JOINs for reporting; schema enforced at the DB.
- Harder: schema migrations require discipline (mitigated with a
  migration tool); horizontal write-scaling beyond a single primary
  would need future sharding work — acceptable given projected scale.
- Follow-up: revisit if write volume approaches the single-primary
  ceiling; that would warrant a new ADR (sharding or a different store).
```

### Example B — MADR format (contested decision)

```markdown
# ADR-0011: Adopt event-driven integration between order and fulfillment

- Status: accepted
- Date: 2026-04-18
- Deciders: Platform guild, Orders lead, Fulfillment lead
- Tags: integration, architecture

## Context and Problem Statement
Orders and Fulfillment are owned by separate teams and currently
integrate via synchronous REST calls. Fulfillment outages cause order
failures, and the tight coupling slows independent deploys. How should
the two services integrate going forward?

## Decision Drivers
- Decouple availability: an order should succeed even if fulfillment is
  briefly down.
- Independent deploys and scaling per team.
- Auditable history of order lifecycle events.
- Team operates Kafka already for analytics.

## Considered Options
- Option A: Keep synchronous REST.
- Option B: Asynchronous events over Kafka (publish OrderPlaced, etc.).
- Option C: Shared database between the two services.

## Decision Outcome
Chosen option: "B — Asynchronous events over Kafka", because it directly
satisfies the decoupling and auditability drivers and reuses existing
Kafka operational expertise.

### Consequences
- Good: order flow tolerates fulfillment downtime; natural event log /
  audit trail; teams deploy and scale independently.
- Bad: introduces eventual consistency — UIs must handle "pending"
  states; debugging spans async hops (needs distributed tracing);
  requires idempotent consumers and a schema-evolution policy.
- Neutral: a dead-letter + replay strategy is now required (follow-up).

## Pros and Cons of the Options
### Option A — Synchronous REST
- Good: simple, strongly consistent, easy to reason about.
- Bad: tight availability coupling (the core problem); cascading failures.
### Option B — Events over Kafka
- Good: decoupled, scalable, auditable, reuses existing infra.
- Bad: eventual consistency; operational/observability complexity.
### Option C — Shared database
- Good: no integration code; immediate consistency.
- Bad: destroys team ownership boundaries; worst long-term coupling. Rejected.

## More Information
See RFC-039 (Order/Fulfillment decoupling). Supersedes the integration
section of ADR-0004.
```

### Example C — a superseding ADR (lifecycle in action)

```markdown
# ADR-0021: Move session storage to client-side signed tokens

## Status
Accepted (2026-06-15) — Supersedes ADR-0014 (sessions in Redis)

## Context
ADR-0014 externalized sessions to Redis to make the web tier stateless.
That succeeded, but Redis is now a hard dependency on the login path and
its outages have caused two login incidents this quarter. Our session
data is now small and we no longer require pre-expiry revocation for the
public site (a product decision in Q2). The original driver for a
server-side store (revocation) no longer holds.

## Decision
We will store sessions as short-lived signed tokens (JWT) in a secure
cookie, removing the Redis dependency from the login path. Token TTL =
15 min with silent refresh; sensitive actions re-verify server-side.

## Consequences
- Easier: removes a critical-path dependency; web tier fully stateless;
  no session-store capacity to manage.
- Harder: cannot revoke a token before its (short) TTL — accepted given
  the Q2 product decision and short TTL; token rotation/refresh logic
  added; secret-key rotation procedure now required.
- Note: ADR-0014 is marked "Superseded by ADR-0021". The Redis cluster
  it introduced is decommissioned in FUL-1187.
```

Notice example C reopens a decision **honestly** — it states *what changed* (a product decision removed the original driver) rather than implying ADR-0014 was wrong. The earlier ADR was *correct for its context*; the context changed. That's exactly what the lifecycle is for.

---

## Key Takeaways

- An ADR captures **one significant decision, its context, and its consequences** — above all, **the "why,"** which is the most expensive knowledge to reconstruct and the first to evaporate.
- Use **Nygard's four sections** (Status, Context, Decision, Consequences) by default; reach for **MADR** (adds drivers + options + per-option pros/cons) on contested calls. Pick one format per repo.
- Write **Context as neutral forces** and **Consequences honestly** (including what got *harder*) — an all-upside ADR is a sales pitch, not a record.
- Write an ADR when a decision is **significant, durable, hard to reverse, cross-cutting, or a non-obvious trade-off** — i.e., when a future engineer might undo it without realizing it was deliberate.
- Keep the log **in the repo**, numbered and immutable; **never edit an accepted ADR — supersede it** with a new one and cross-link both ways. Bootstrap with `0001-record-architecture-decisions.md`.
- The lifecycle is **proposed → accepted → (deprecated) → superseded**; reopening a decision honestly (the *context changed*) is a feature, not a failure.
