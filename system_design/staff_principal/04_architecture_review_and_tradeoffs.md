# Architecture Review & Trade-off Analysis

> **Audience:** Staff / Principal engineers who run or sit on design reviews, and who are expected to make the *reasoning* behind a design legible to everyone else.
>
> The job at this level is not to have the best opinion in the room. It is to make trade-offs **explicit**, tie them to **requirements**, and create a process where the *right* decision survives whether or not you happen to be present. A great architecture review produces a *decision and a paper trail*, not a winner.

---

## 1. Why design review exists

Most bad architecture is not the result of someone choosing the wrong option. It is the result of nobody noticing a choice was being made. Design review is the institutional habit of **surfacing implicit decisions before they calcify into code.**

Three things a good review produces:

1. **A decision** — chosen approach, explicitly, with an owner.
2. **The rejected alternatives** — and *why* they were rejected, so the question stays closed.
3. **The conditions that would reopen it** — "we'll revisit if write QPS exceeds 50k or if we add a second region."

If a review ends with a vibe instead of those three artifacts, it failed, no matter how smart the conversation was.

---

## 2. The Architecture Review Board (ARB) vs. lightweight design review

There is a spectrum. Pick the lightest weight that gives you the safety you need.

| | Lightweight design review | Architecture Review Board (ARB) |
|---|---|---|
| **Trigger** | Any non-trivial change; author opts in | Crosses a threshold: new datastore, new external dependency, cross-team blast radius, security/compliance surface, >1 quarter of work, hard-to-reverse |
| **Attendees** | Author + 1–3 relevant engineers | Standing board (staff+/principal across domains) + author team |
| **Cadence** | On demand, async-first | Scheduled (weekly/biweekly), or on-demand for big items |
| **Output** | Comments on a doc, a decision | RFC/ADR sign-off, recorded decision, conditions to revisit |
| **Failure mode** | Skipped because "it's small" | Becomes a gate / bottleneck / theater |

**Opinion:** Default to the lightweight version. An ARB is justified for **one-way-door** decisions and **high-blast-radius** decisions (see §6). If everything goes to the ARB, the ARB becomes a queue, the queue becomes a delay, and teams start routing around it. The board's real product is *judgment and consistency*, not approval stamps.

### Who should be in the room

- **Author / DRI** (Directly Responsible Individual) — owns the doc and the decision.
- **A skeptic** — explicitly assigned to argue the strongest case *against* the proposal. This is the single highest-leverage role; assign it on purpose.
- **Domain depth** — someone who has operated this kind of system at scale (storage, networking, ML serving, whatever applies).
- **Adjacent-team reps** — anyone downstream of the blast radius.
- **A facilitator** — keeps time, drives to a decision, prevents bikeshedding. Often *not* the author.

Keep it small. 4–7 people. Beyond that, attendance becomes audience, and audiences don't decide.

### What "good" looks like in the room

- The author can state the **decision** they want made in one sentence in the first two minutes.
- Reviewers read the doc **before** the meeting (pre-read; consider a Bezos-style silent read for the first 10 minutes if pre-reads aren't happening).
- Discussion is anchored to **requirements**, not preferences ("does this meet the 99.9% availability target?" not "I'd use Kafka here").
- Disagreements end in one of: agreement, an explicit *disagree-and-commit*, or a concrete experiment/spike to resolve the unknown.
- The meeting ends with **action items, an owner, and a date.**

---

## 3. The trade-off analysis framework

Engineers love to debate options. Staff+ engineers force the debate into a shape that can actually be resolved. The shape is:

> **Requirements → Options → Trade-offs against those requirements → Decision + conditions.**

### Step 1 — Make requirements explicit and ranked

You cannot evaluate a trade-off without knowing what you're trading *for*. Write requirements down and **rank or weight** them. Distinguish:

- **Constraints** (hard, non-negotiable): "must store EU user data in EU," "p99 < 200ms."
- **Goals** (soft, prioritized): "cheap to operate," "easy for new teams to adopt."
- **Non-goals** (explicitly out of scope): "we are *not* optimizing for >1M QPS this year."

Naming non-goals is a power move. Half of all architecture arguments are someone optimizing for a requirement that isn't real yet.

### Step 2 — Enumerate genuine options

At least 2, ideally 3. One must usually be **"do nothing / extend what we have."** If you only have one option, you have a proposal, not a decision. If your alternatives are obviously strawmen, reviewers will (correctly) distrust the whole analysis.

### Step 3 — Make trade-offs explicit and tie them to requirements

For each option, state what it's *good at* and what it *costs* — phrased against the ranked requirements, not in the abstract.

### Step 4 — Decide, and record what would change your mind

Pick. State the chosen option, the headline reason, and the **revisit conditions.**

---

## 4. Weighted decision matrices (with a worked example)

A weighted decision matrix is a tool for *structuring* a conversation, **not** a machine for producing answers. The number it spits out is a prompt for discussion ("huh, why did the option I hate win?"), not a verdict. Use it, then sanity-check it against your gut — and if they disagree, figure out which one is wrong.

### How to build one

1. List the **criteria** (derived from your ranked requirements).
2. Assign each a **weight** (e.g., 1–5, or percentages summing to 100%).
3. Score each option per criterion on a fixed scale (e.g., 1–5).
4. Multiply, sum, and **discuss the result.**

### Worked example: choosing a datastore for a new audit-log service

**Requirements (ranked):** must be append-heavy and durable (constraint); cheap at scale; queryable by time range and user; low operational burden for our small team; reversible if we're wrong.

| Criterion | Weight | Postgres | DynamoDB | S3 + Parquet + Athena |
|---|:---:|:---:|:---:|:---:|
| Write throughput (append-heavy) | 5 | 3 | 5 | 5 |
| Cost at 10TB/yr scale | 4 | 2 | 3 | 5 |
| Query flexibility (time + user) | 3 | 5 | 3 | 3 |
| Operational burden (small team) | 4 | 3 | 5 | 4 |
| Team familiarity | 2 | 5 | 3 | 3 |
| **Weighted total** | | **53** | **70** | **74** |

*Calculation for Postgres:* (5×3)+(4×2)+(3×5)+(4×3)+(2×5) = 15+8+15+12+10 = **53**.

**Reading the result:** S3+Athena edges out DynamoDB on raw score, driven by cost. But the matrix surfaces the real conversation: Athena query latency (seconds-to-minutes) may violate an *unstated* requirement ("on-call needs to grep the audit log during an incident in < 5s"). That's the point — **the matrix exposed a missing criterion.** Add "interactive query latency," re-weight, and DynamoDB likely wins. The matrix didn't decide; it made the team articulate what they actually cared about.

**Anti-patterns with matrices:**
- *Reverse-engineering the weights* until your favorite wins. If you catch yourself doing this, you already know the answer — just argue for it honestly.
- *False precision.* Scoring to two decimals over made-up 1–5 inputs. Round numbers, loose scale, honest uncertainty.
- *Equal weights.* If everything is weight 3, you haven't done the prioritization, which was the whole job.

---

## 5. The recurring trade-off axes

Most architecture decisions are a re-skin of a handful of fundamental tensions. Knowing the canonical axes lets you name the trade-off fast and avoid re-deriving it every time.

| Axis | The tension | Lean one way when… | Lean the other when… |
|---|---|---|---|
| **Consistency vs. Availability** (CAP / PACELC) | Under partition you can't have both; even without partition, consistency costs latency | Money, inventory, auth — correctness beats uptime | Feeds, analytics, caches — stale is fine, downtime isn't |
| **Latency vs. Throughput** | Batching/queueing raises throughput but adds latency | Interactive, user-facing paths | Bulk/ETL/async pipelines |
| **Cost vs. Performance vs. Reliability** | Pick two cheaply; the third gets expensive | Reliability is non-negotiable (pay) | Internal/best-effort tooling (cut cost) |
| **Build vs. Buy** | Control & fit vs. speed & maintenance burden | It's your core differentiator | It's undifferentiated heavy lifting (auth, billing, observability) |
| **Simplicity vs. Flexibility** | Generality has a carrying cost forever | You don't yet know the requirements (YAGNI) | Requirements are known and varied, churn is high |
| **Coupling vs. Autonomy** | Shared platform vs. team independence (Conway's Law) | Consistency/leverage matters | Team velocity & local fit matter |

### A word on Build vs. Buy

The honest version of this analysis includes the **total cost of ownership**, not the sticker price. "Build" almost always looks cheaper in the planning meeting and almost always costs more over three years once you account for on-call, security patching, feature requests, and the opportunity cost of the engineers maintaining it. Default to **buy/adopt for undifferentiated work**, build only where it's a genuine differentiator. Larson's framing: spend your innovation tokens deliberately, and keep most of your stack boring.

---

## 6. Reversible vs. irreversible ("one-way door") decisions

Borrowed from Amazon's Bezos framing, refined for engineering:

- **Two-way door (reversible):** you can undo it cheaply. *Decide fast, decide locally, don't over-review.* Speed of iteration beats correctness.
- **One-way door (irreversible / expensive to reverse):** undoing it is costly or impossible. *Slow down, raise the review bar, bring it to the ARB.*

| Decision | Door | Why |
|---|---|---|
| Internal API shape (not yet published) | Two-way | Refactor it next sprint |
| Feature-flag a new code path | Two-way | Flip it off |
| Public API contract with external partners | One-way | Breaking it breaks customers |
| Choice of primary datastore at scale | ~One-way | Migrating 50TB of live data is brutal |
| Data model / schema with no versioning story | One-way-ish | Backfills are expensive forever |
| Programming language for a new core service | One-way-ish | Hiring + ecosystem lock-in |

**The leadership move:** explicitly classify the decision *before* deciding how much process to apply. The most common failure is applying one-way-door rigor to two-way-door decisions — that's how teams spend three weeks choosing a logging library. The second most common is treating a one-way door as reversible because it *feels* small ("it's just the schema").

> **Make irreversible decisions reversible when you can.** Add a versioning layer to an API, an abstraction over a vendor, a migration path to a datastore. Buying back optionality on a one-way door is often worth a surprising amount of engineering.

---

## 7. Risk assessment

For each significant decision, name the risks and decide what to *do* about each one. A risk you've named and accepted is fine; a risk nobody mentioned is a future incident.

**A lightweight risk register:**

| Risk | Likelihood | Impact | Mitigation | Owner | Detection |
|---|---|---|---|---|---|
| Vendor X has a regional outage | Med | High | Multi-region failover; degrade gracefully | A. Patel | Synthetic probes, SLO alert |
| Write QPS exceeds capacity by EOY | Med | Med | Sharding plan documented, not built | DRI | Dashboard at 70% threshold |
| New team can't operate this | High | Med | Runbook + paved-road template | Platform | On-call survey |

**Risk-handling vocabulary** (be explicit about which you're choosing): **Avoid** (change the design), **Mitigate** (reduce likelihood/impact), **Transfer** (insurance/vendor SLA), **Accept** (name it, move on). The classic mistake is silent acceptance — the risk was real, nobody decided to accept it, and "nobody decided" reads exactly like an incident postmortem's root cause.

---

## 8. Giving and receiving design feedback

This is where staff+ engineers most visibly model the culture. The technical content of your feedback matters less than whether people leave the review wanting to come back.

### Giving feedback

- **Anchor to requirements and risks, not taste.** "This won't hit the p99 target under burst load" >> "I wouldn't do it this way."
- **Separate severity tiers explicitly.** Borrow the convention:
  - **Blocking** — must change before we proceed (violates a constraint or introduces unacceptable risk).
  - **Strong suggestion** — I think you're wrong, but I'll commit either way.
  - **Nit / optional** — take it or leave it; label it so it doesn't get equal airtime.
  Labeling severity is the single biggest improvement most reviewers can make. An unlabeled nit and a blocking concern look identical in a comment thread.
- **Ask questions before asserting.** "What happens to in-flight requests during failover?" surfaces the gap *and* lets the author own the fix.
- **Steelman before you critique.** State the strongest version of their idea first. It earns trust and stops you from attacking a misunderstanding.
- **Praise specifically.** "The non-goals section saved us an hour" reinforces the behavior you want.

### Receiving feedback

- **Don't defend — understand.** Your job in the room is to extract the maximum signal, not to win.
- **Distinguish the objection from the proposed fix.** Often the reviewer is right about the problem and wrong about the solution. Acknowledge the problem; negotiate the fix.
- **"Disagree and commit" out loud.** When you've heard the case and still choose differently, say so explicitly and record it. Silent override breeds resentment; explicit override is leadership.
- **Capture every blocking item as an action with an owner.** Feedback that isn't written down didn't happen.

---

## 9. Avoiding analysis paralysis & bikeshedding

**Bikeshedding** (Parkinson's law of triviality): groups spend time proportional to how *easy* a topic is to have an opinion on, not how *important* it is. The datastore choice gets 5 minutes; the metric naming convention gets 40.

Defenses:

- **Timebox by blast radius.** Allocate discussion time proportional to reversibility and impact, and say so up front.
- **Name it in the room.** "We're bikeshedding the enum names — let's let the author decide and move on." Giving people permission to stop is a facilitation skill.
- **Default to the DRI on low-stakes calls.** Not everything is a consensus decision. Many things are "the owner picks; reviewers advise."
- **For analysis paralysis: set a decision deadline.** "We decide Friday with the information we have." A reversible decision made on Friday beats a perfect decision made never. Tie this to the door classification (§6): two-way doors deserve a short clock.
- **Spike, don't speculate.** When the disagreement hinges on an unknown ("will the index be fast enough?"), the answer is a one-day prototype, not a one-hour argument.

---

## 10. Design-review checklist / rubric

Copy-paste this into your RFC template or use it as the reviewer's scorecard.

### Reviewer rubric (score each: ✅ solid / ⚠️ gap / ❌ missing)

| Dimension | What I'm checking | Score |
|---|---|:---:|
| **Problem clarity** | Is the actual problem stated, separate from the solution? | |
| **Requirements** | Constraints, goals, *and non-goals* explicit and ranked? | |
| **Alternatives** | ≥2 real options, including "do nothing"? Strawmen avoided? | |
| **Trade-offs** | Tied to requirements, not abstractions? | |
| **Scale & limits** | Capacity numbers, growth assumptions, where it breaks? | |
| **Failure modes** | What happens when each dependency fails? Blast radius? | |
| **Data** | Schema, migration path, retention, consistency model, PII? | |
| **Operability** | Observability, runbook, on-call story, rollback plan? | |
| **Security/compliance** | Authn/z, data residency, audit, threat surface? | |
| **Cost** | TCO including ops, not just infra sticker price? | |
| **Reversibility** | One-way vs two-way door classified? | |
| **Decision** | Chosen option + headline reason + revisit conditions? | |

### Author's pre-submit checklist

- [ ] I can state the decision I want in one sentence.
- [ ] Requirements are ranked; non-goals are listed.
- [ ] I have a real "do nothing" alternative.
- [ ] Every dependency has a "what if it's down" answer.
- [ ] There's a rollback / migration plan.
- [ ] I've classified this as a one-way or two-way door.
- [ ] I've named the risks I'm *accepting*.

---

## Anti-patterns

| Anti-pattern | What it looks like | Fix |
|---|---|---|
| **HiPPO decides** | Highest-paid person's opinion wins regardless of analysis | Anchor to requirements; assign a skeptic |
| **Solution in search of a problem** | "Let's use Kafka" before stating the need | Start the doc with the problem, ban tech names from the title |
| **Strawman alternatives** | Two real options and three obviously-bad ones | Reviewers should be able to *want* any listed option |
| **Review theater** | ARB rubber-stamps everything | Reserve the board for one-way doors; track its reject rate |
| **Silent risk acceptance** | Real risk, nobody decided to accept it | Risk register with explicit accept/mitigate |
| **Reversibility blindness** | Three weeks to pick a logging lib | Classify the door first, then size the process |
| **Matrix worship** | The spreadsheet "decided" | Treat the number as a discussion prompt, sanity-check vs gut |
| **Unlabeled feedback** | Nits and blockers in one undifferentiated thread | Tier every comment: blocking / suggestion / nit |

---

## Key Takeaways

1. A review's product is a **decision, its rejected alternatives, and its revisit conditions** — not a meeting.
2. **Match process weight to reversibility.** One-way doors get the ARB; two-way doors get a fast local call.
3. Make trade-offs **explicit and tied to ranked requirements.** Name your non-goals.
4. Decision matrices **structure conversation; they don't make decisions.** When the number surprises you, you've found a missing requirement.
5. Most decisions reduce to a few **canonical axes** — name the axis to skip re-deriving it.
6. **Tier your feedback** (blocking / suggestion / nit) and anchor it to requirements, not taste.
7. **Name and assign every risk**; silent acceptance is a future postmortem.
8. Fight bikeshedding with **timeboxes proportional to blast radius** and a willingness to say "the DRI decides."
