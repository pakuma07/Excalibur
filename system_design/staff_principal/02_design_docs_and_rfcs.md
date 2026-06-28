# 02 — Design Docs & RFCs

> If you can only build one staff+ skill, build this one. The engineer who can take an ambiguous problem and turn it into a clear, persuasive, reviewable document is the engineer who shapes what the org builds.

A **design doc** (a.k.a. **RFC** — Request for Comments — in many companies) is a written proposal for a non-trivial piece of work, circulated *before* implementation, so that the right people can poke holes in it while changes are still cheap. This document explains why writing is the staff+ superpower, gives you a complete copy-pasteable template, covers the review process, contrasts Amazon's narrative formats (6-pager, PR-FAQ), and shows how to drive consensus.

---

## 1. Why writing is the staff+ superpower

Code changes one system. A document changes what *every future PR* becomes — and aligns people you'll never pair with. Writing is the highest-leverage tool a staff engineer has, for concrete reasons:

- **Writing forces thinking.** You cannot write a clear design doc for a design you don't actually understand. The act of writing *finds the holes* — the hand-wavy "and then it scales" paragraph is where your design is broken. This is the real value; the artifact is almost a side effect.
- **It scales your reasoning across time and space.** A meeting persuades the people in the room, once. A doc persuades async, repeatedly, including the new hire six months from now asking "why is it built this way?"
- **It makes decisions reviewable and reversible-on-paper.** Catching a flaw in review costs an afternoon. Catching it in production costs a quarter.
- **It distributes the design, not just the decision.** Good docs let *more* people contribute meaningfully, which is the whole point of leverage.

> Google's well-known design-doc culture treats the doc as the central artifact of a project's design phase: lightweight, informal in tone, but rigorous in content — written, reviewed, and then used as the record. *Software Engineering at Google* describes them as the primary tool for surfacing design issues early and building organizational consensus.

---

## 2. When to write one (and when not to)

Don't write a doc for everything; that's its own failure mode (ceremony tax). Calibrate to **cost-of-being-wrong** and **number-of-stakeholders**.

**Write a design doc / RFC when:**

- The work is **non-trivial** (multiple weeks) or touches **multiple teams**.
- The decision is **hard to reverse** — data models, public APIs, a new core dependency, a storage choice, a security boundary.
- There are **multiple plausible approaches** and the choice is contested.
- You need **alignment or sign-off** from people outside your team.
- It's a **risky or novel** area where you want many eyes before committing.

**Don't write one (or write a one-paragraph note) when:**

- The change is small, local, and easily reversible.
- There's only one sensible approach and no one disagrees.
- You're using it to *delay* a decision that should just be made (analysis paralysis).

| Signal | Lightweight (Slack/short note) | Full design doc / RFC |
|--------|-------------------------------|------------------------|
| Reversibility | Two-way door | One-way door |
| Stakeholders | One team | Multiple teams / org |
| Effort | Days | Weeks+ |
| Contention | None | Real disagreement |

---

## 3. The design-doc / RFC template

Copy this. Delete sections that genuinely don't apply (and say *why* you deleted them — e.g. "Security: N/A, no new data or surface area"). The **first three sections (context, goals, non-goals)** are the most important and the most often skipped; they're where alignment actually happens.

```markdown
# [RFC-NNN] <Short, specific title>

| | |
|----------------|----------------------------------------|
| **Status**     | Draft / In Review / Approved / Rejected / Superseded |
| **Author(s)**  | Name <email>                           |
| **Reviewers**  | Required: …  Optional: …               |
| **Created**    | YYYY-MM-DD                             |
| **Last update**| YYYY-MM-DD                             |
| **Tracking**   | JIRA-1234 / link                       |

## 1. Context & Problem Statement
What is the situation today? What problem are we solving, and why now?
Assume the reader is a competent engineer with NO context on this area.
Include the cost of doing nothing. (1–3 paragraphs, no solutions yet.)

## 2. Goals
Bullet list of what success looks like. Measurable where possible.
- e.g. p99 checkout latency < 300ms under 2x current peak.

## 3. Non-Goals
Explicitly out of scope. This section prevents scope creep and review
derailment more than any other. Be generous here.
- e.g. We are NOT redesigning the payments ledger in this work.

## 4. Requirements & Constraints
Functional requirements, non-functional requirements (SLOs, scale,
compliance), and hard constraints (budget, deadlines, existing systems,
team skills, regulatory).

## 5. Proposed Design
The recommended approach, in enough detail that a reader could start
building. Include:
- Architecture diagram (boxes/arrows; sequence diagram if flow matters).
- Key components and their responsibilities.
- Data model / schema changes.
- API / interface changes (request/response shapes).
- How data flows through the system.
- Failure modes and how the design handles them.

## 6. Alternatives Considered
For EACH realistic alternative (including "do nothing" and "buy"):
- Brief description.
- Pros / cons.
- Why we did NOT choose it.
This section is what separates a proposal from a decision. Reviewers
trust a recommendation far more when they see the discarded options.

## 7. Trade-offs
What are we explicitly trading away by choosing the proposed design?
(e.g. "We accept higher write latency in exchange for strong consistency.")
Be honest — every design has costs; hiding them destroys trust.

## 8. Risks & Mitigations
| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| …    | L/M/H     | L/M/H  | …          |

## 9. Rollout Plan
- Phasing / milestones.
- Migration & backfill strategy (if applicable).
- Feature flags, canary, % rollout.
- Backward compatibility & deprecation plan.
- Rollback plan (how do we undo this safely?).

## 10. Observability
- Key metrics, logs, traces, dashboards we'll add.
- Alerts and their thresholds.
- How we'll know the rollout is healthy / unhealthy.

## 11. Security & Privacy
- New data collected/stored; classification (PII?).
- AuthN/AuthZ changes; new trust boundaries.
- Threat considerations; compliance (GDPR/HIPAA/SOC2) impact.
- (If genuinely none: state "No new data, surface, or trust boundary.")

## 12. Cost & Capacity
- Estimated infra cost (storage, compute, network, licenses).
- Capacity assumptions and headroom.
- Cost at launch vs. at projected scale.

## 13. Open Questions
Things you genuinely don't know yet. Listing these builds trust and
invites help — do NOT hide your uncertainty.

## 14. Appendix
Benchmarks, detailed schemas, links, prior art.
```

> **Tip on ordering:** lead with Context → Goals → Non-Goals, then jump to the Proposed Design. Reviewers should grasp *what you're recommending and why* within the first page. Detailed appendices go at the back.

---

## 4. The RFC review process

A doc nobody reviews is a diary. The review *is* the product. A healthy process:

1. **Draft.** Author writes; gets one or two trusted colleagues to read it privately first (catch the embarrassing stuff before it's public).
2. **Pre-wire.** Talk 1:1 with the key stakeholders and likely objectors *before* the formal review. The review meeting should ratify alignment, not be the first exposure. (This single habit prevents most review disasters.)
3. **Circulate for comment.** Set a clear deadline ("comments by Thu EOD"). Name **required** vs **optional** reviewers explicitly — ambiguity means everyone assumes someone else will review.
4. **Async comment pass.** Reviewers leave inline comments. Author replies to *every* comment — even "good point, addressed in §5" or "intentional, see Non-Goals."
5. **Optional review meeting.** Only if there's unresolved disagreement. Walk the contested points, not the whole doc (everyone's read it — enforce that).
6. **Decide & record.** Mark the doc Approved/Rejected. Capture the *why* — often as an [ADR](03_architecture_decision_records.md). Note dissent and the disagree-and-commit.
7. **Keep it as the record.** Link it from the code/runbook. Update status if it's later superseded.

### Roles to name explicitly

| Role | Responsibility |
|------|----------------|
| **Author / driver** | Owns the doc, drives to a decision, integrates feedback. |
| **Required reviewers** | Must approve; usually owners of affected systems + a senior/staff for rigor. |
| **Optional reviewers / FYI** | Can comment; informed but not blocking. |
| **Decision-maker / approver** | Who actually signs off (sometimes the author; sometimes a lead/architect). |

---

## 5. Amazon's narrative formats: the 6-pager & PR-FAQ

Amazon famously **banned slide decks** for substantive decisions in favor of written narratives. Two formats are worth knowing because they encode a different philosophy.

### The 6-pager

A **6-page narrative memo** (prose, not bullets), read **silently in the room** at the start of the meeting (typically ~20 minutes of silent reading), then discussed. The logic:

- **Prose forces complete thinking.** Bullets let you hide gaps behind a confident voice in the room; full sentences expose muddy logic. Bezos: *"The narrative structure of a good memo forces better thought and better understanding of what's more important than what."*
- **Silent reading levels the field.** Everyone absorbs the same argument at their own pace; the loudest voice doesn't dominate; remote and introverted participants aren't disadvantaged.
- **Six pages is a forcing function for prioritization** — you must decide what matters.

### The PR-FAQ ("Working Backwards")

For new products/features, Amazon starts from the **future press release** plus an **FAQ** — *before* building anything. You literally write the announcement as if the product already shipped.

```markdown
# PR-FAQ: <Product / Feature name>

## PRESS RELEASE  (write as if launch day; ~1 page)
**Headline:** <Customer-benefit-focused, plain language>
**Subhead:** <One sentence, who it's for and the benefit>
**Date / Location:** <Future launch date>

[Para 1] The problem customers face today.
[Para 2] The new product/feature and the single biggest benefit.
[Para 3] How it works, in plain customer language (no jargon).
[Para 4] Quote from a company leader on why this matters.
[Para 5] Quote from a (hypothetical) delighted customer.
[Para 6] How to get started / call to action.

## FAQ
### Customer FAQ
- What is it? Who is it for? How much does it cost? How do I start?
- What are the most likely customer objections, and our answers?

### Internal / Stakeholder FAQ
- Why should *we* build this? What's the size of the opportunity?
- What's hard about it? What could go wrong?
- What are the key dependencies and risks?
- What do we need to believe for this to succeed?
- What are the alternatives and why this one?
- What metrics define success?
```

> **Why "working backwards" works:** starting from the customer-facing outcome ruthlessly exposes whether the idea is actually valuable *before* a line of code is written. If you can't write a compelling press release, the product probably isn't compelling. It's the cheapest possible prototype — words.

**When to use which:** PR-FAQ for *new product/feature ideation* (is this worth doing?); the engineering design-doc/RFC template (§3) for *how to build it once you've decided*; the 6-pager style for *any substantive decision memo* you want read carefully. They compose: PR-FAQ to decide *what*, RFC to decide *how*.

---

## 6. Driving consensus & handling feedback

Writing the doc is half the job; **getting an org to align around it** is the other half.

### Driving consensus

- **Pre-wire relentlessly.** (Said twice on purpose.) Surprise in a review meeting reads as disrespect and triggers defensiveness.
- **Separate "I disagree" from "I'm blocked."** Make it safe to disagree-and-commit. Capture dissent in writing so people feel heard even when overruled.
- **Timebox the decision.** Open-ended reviews rot. "We decide Friday; raise blocking concerns by Thursday."
- **Distinguish reversible from irreversible.** For two-way doors, push for speed: "Let's try it; we can change it." Reserve heavy consensus for one-way doors.
- **Escalate cleanly when stuck.** If two teams can't agree, name the disagreement crisply and take the *decision* (not the *fight*) to the person empowered to break the tie.

### Handling feedback gracefully

- **Reply to every comment.** Silence reads as dismissal. "Good catch, fixed" / "Intentional — see Non-Goals §3" / "Let's discuss live."
- **Distinguish the critique from your ego.** The doc is a tool to find the best answer, not a monument to you. A reviewer who breaks your design saved you, publicly thank them.
- **Steelman objections.** Restate the strongest version of a concern before responding. People who feel understood concede gracefully.
- **Know when to fold.** If a senior reviewer is right, change the doc fast and visibly. Stubbornness on a losing position spends credibility you'll want later.
- **Don't gold-plate in response to every comment.** Not all feedback must be incorporated; "noted, but out of scope for this RFC" is a complete and respectable answer.

> **Anti-pattern: the bikeshed.** Reviews drown in trivial-but-easy topics (naming, formatting) while the hard architectural question gets no attention. As driver, *steer*: "Let's park the naming in a comment thread and focus the next 20 minutes on the consistency model." Owning the agenda is part of authoring.

---

## 7. Worked example (abbreviated)

```markdown
# [RFC-042] Move session storage from sticky-session memory to Redis

| Status | In Review |  | Author | A. Rivera |  | Created | 2026-06-10 |

## 1. Context & Problem
Today user sessions live in each web node's memory, requiring sticky
sessions at the load balancer. This blocks zero-downtime deploys (we
drain nodes slowly), and a single node loss logs out ~1/8 of active
users. We're about to double node count for a launch, making both
problems worse. Cost of doing nothing: degraded deploy velocity and
visible logout incidents during the launch.

## 2. Goals
- Stateless web tier (any node serves any request).
- Zero-downtime deploys, no session loss on single-node failure.
- p99 session read < 5ms.

## 3. Non-Goals
- Not changing the auth/token format.
- Not addressing cross-region session replication (separate RFC).

## 5. Proposed Design
Externalize sessions to a managed Redis cluster (cluster mode, 3
shards, 1 replica each). Web nodes read/write session by token key.
TTL = 24h sliding. Remove LB stickiness. [diagram]

## 6. Alternatives Considered
- **Sticky sessions + faster drain (do nothing-ish):** cheapest, but
  doesn't solve node-loss logouts. Rejected.
- **Signed client-side session (JWT in cookie, stateless):** no store
  needed, but can't revoke before expiry and bloats every request.
  Rejected on revocation requirement.
- **Postgres-backed sessions:** reuses existing infra, but adds write
  load to the primary and ~15ms p99 — fails latency goal. Rejected.

## 7. Trade-offs
We accept a new operational dependency (Redis) and its failure mode
(store unavailable = no logins) in exchange for a stateless tier and
fast revocation. Mitigated by HA cluster + circuit breaker (§8).

## 9. Rollout
Dual-write to Redis behind a flag; read from memory. Flip reads per-%;
monitor; then remove memory path. Rollback = flip the flag.
```

Note how the **Alternatives** section does the persuasive heavy lifting — a reader sees the author considered the cheap option and the trendy option and rejected each for a *stated reason tied to a goal*.

---

## Key Takeaways

- **Writing is the staff+ superpower** because it forces clear thinking and scales your reasoning across time and people. The artifact is often a side effect of the thinking it forces.
- Use the **design-doc/RFC template** (§3); the most-skipped, most-valuable sections are **Context, Goals, Non-Goals, and Alternatives Considered**.
- Calibrate rigor to **reversibility × stakeholders**. Don't write a doc for two-way doors; don't skip one for one-way doors that span teams.
- The **review process is the product**: pre-wire 1:1, name required reviewers, set deadlines, reply to every comment, record the decision (often as an ADR).
- Amazon's **6-pager** (prose, silent reading) and **PR-FAQ / working backwards** (start from the press release) are powerful for forcing complete thinking and validating *what* to build before *how*.
- Drive consensus by **pre-wiring, timeboxing, separating disagree from blocked, and escalating the decision (not the fight)**. Handle feedback by steelmanning, replying to everything, and folding fast when you're wrong.
- Steer reviews away from **bikeshedding** toward the genuinely hard questions — that's the author's job.
