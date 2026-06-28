# Technical Strategy & Vision

> **Audience:** Staff / Principal engineers expected to set direction across teams — to answer "where are we going and how do we get there?" in a way that survives contact with reality and that other people can *execute without you in the room.*
>
> Most engineers can build the thing. Staff+ engineers figure out **which thing is worth building, in what order, and why** — and write it down so an org of 50 people rows in the same direction.

---

## 1. Vision vs. Strategy: stop conflating them

These words get used interchangeably and it causes real confusion. They answer different questions.

| | **Vision** | **Strategy** |
|---|---|---|
| Question it answers | **Where** are we going? | **How** do we get there (given reality)? |
| Time horizon | 2–5 years | 6–18 months |
| Nature | Aspirational, stable, motivating | Concrete, decision-laden, evolving |
| Form | A description of a desirable future | A diagnosis + a set of choices |
| Test of quality | Does it pull people forward? Would they recognize "arrived"? | Does it tell you what to say *no* to? |
| Failure mode | Vague platitude ("be the best platform") | A list of goals with no diagnosis |

**Vision** is the destination you'd be proud to reach. **Strategy** is the route you've chosen *given* your terrain, traffic, and fuel. A vision without strategy is a daydream; a strategy without vision is busywork with momentum.

Larson's framing (from *Staff Engineer* / *An Elegant Puzzle*): a good vision describes a future state in enough concrete detail that people can use it to make decisions on their own. A good strategy makes the *trade-offs* and *constraints* explicit so people understand why the obvious-but-wrong path is wrong.

---

## 2. What good strategy actually is (Rumelt)

Richard Rumelt's *Good Strategy / Bad Strategy* is the most useful mental model here, and Larson explicitly leans on it. A strategy has a **kernel** of three parts:

1. **Diagnosis** — what's *actually* going on? Name the challenge plainly. Most "strategies" skip this and jump to goals. The diagnosis is the hardest and most valuable part because it reframes a messy situation into something addressable.
2. **Guiding policy** — the overall approach for dealing with the diagnosed challenge. It rules things *out*. It's a chosen direction, not a goal.
3. **Coherent actions** — concrete, coordinated steps that actually implement the guiding policy and reinforce each other.

### Bad strategy (the stuff to avoid) — Rumelt's signatures

- **Fluff** — buzzwords masquerading as insight ("leverage synergistic cloud-native paradigms").
- **Failure to face the challenge** — no diagnosis, so the "strategy" can't be evaluated.
- **Mistaking goals for strategy** — "grow 30%" / "be reliable" is a goal, not a plan for *how*.
- **Bad objectives** — a laundry list of everything, with no prioritization. (If everything is strategic, nothing is.)

> **The single best test of a strategy: does it tell you what *not* to do?** A strategy that endorses every reasonable activity is just a budget.

---

## 3. Writing a Technical Vision

A technical vision is a short, durable, opinionated description of the future architecture/state you're driving toward. It should be readable by a new hire and used by a senior engineer to settle an argument.

### Structure (1–3 pages)

```
# Technical Vision: <Area>  (e.g. "Our Data Platform in 2028")

## The future we're building toward
A vivid, concrete description of the desired end state. Present tense
("Teams self-serve a new data pipeline in an afternoon"), not future
tense ("we will enable..."). Specific enough to disagree with.

## Why this future (the value)
What does this unlock for the business / customers / engineers?

## Principles
The 3–7 durable beliefs that constrain how we get there.
(e.g. "Boring technology by default", "Self-service over tickets",
"Every team owns what it ships.")

## What this is NOT
Explicit non-goals. The futures we are deliberately not chasing.

## How we'll know we got there
Observable signals of success. Not OKRs — recognizable end-state markers.
```

### Qualities of a vision that works

- **Concrete and present-tense.** "An engineer ships a new service to production in under a day with zero tickets" beats "improve developer velocity."
- **Opinionated.** A vision everyone agrees with on first read is too vague to be useful. Good visions have *principles you could violate.*
- **Stable.** If you're rewriting it every quarter, it was a roadmap, not a vision.
- **Co-authored / socialized.** A vision you wrote alone is a document. A vision 20 people contributed comments to is a *shared* vision that they'll defend. Larson: write the draft, then spend most of your energy circulating it.

---

## 4. Writing a Technical Strategy (template)

Use Rumelt's kernel. This is the workhorse document.

```
# Technical Strategy: <Challenge>
Author / DRI: …    Status: Draft|Review|Adopted    Date: …    Revisit: …

## 1. Diagnosis  (what's actually going on)
- The core challenge, stated plainly.
- The evidence: data, incidents, costs, velocity metrics, quotes.
- What makes this hard (the real constraints, not the symptoms).
- Reframe: the simplified model of the situation we'll act on.

## 2. Guiding Policy  (our chosen approach)
- The overall direction, in 1–3 sentences.
- What this approach rules OUT (the tempting paths we're declining).
- Why this leverages our specific advantages / constraints.

## 3. Coherent Actions  (what we'll actually do)
- Action 1 … (who, rough when, how it reinforces the policy)
- Action 2 …
- Action 3 …
- Sequencing: what must come first and why.
- Explicit non-actions: what we are choosing NOT to do this cycle.

## 4. Trade-offs & risks
- What we're sacrificing by choosing this path.
- Key risks and how we'll detect/mitigate them.

## 5. How we measure progress
- Leading indicators (early signal) + lagging indicators (outcomes).
```

---

## 5. Worked example: "Our path to multi-region"

A realistic, compressed strategy doc using the kernel.

> ### Diagnosis
> We run single-region in us-east-1. Two incidents in the last year were full regional outages we couldn't escape; both breached our 99.95% SLA and triggered contractual credits. Sales now loses enterprise deals on the "where's your DR story?" question. The real challenge isn't "add a region" — it's that **our data layer assumes a single writable Postgres, and roughly 40 services hold that assumption implicitly.** Naively going multi-region means a multi-quarter rewrite of consistency assumptions across teams that don't have the context to do it safely.
>
> ### Guiding policy
> **Buy regional resilience for the stateless tier now; sequence the stateful tier deliberately; never ask product teams to reason about replication.** We will provide multi-region as a *platform capability*, not a per-team project. This rules out: (a) a big-bang cutover, (b) asking each team to make their own datastore active-active, (c) chasing active-active writes before we've earned active-passive.
>
> ### Coherent actions
> 1. **Stateless first.** Deploy services to a second region behind global load balancing; they're already region-agnostic. *(Platform team, this quarter.)*
> 2. **Active-passive data tier.** Stand up streaming replication to a warm standby in region 2 with a documented, rehearsed failover (target RTO 15 min, RPO < 1 min). *(Data platform, next quarter.)*
> 3. **Abstract the data access.** Ship a paved-road data-access library so teams stop holding the single-writer assumption directly, buying us future optionality. *(Platform, ongoing.)*
> 4. **Game-day the failover** quarterly until it's boring.
> 5. **Non-action:** we are explicitly *not* pursuing active-active multi-master writes this year. The complexity isn't justified until active-passive is proven and a customer requirement demands it.
>
> ### Trade-offs & risks
> We pay ~1.6x infra for the warm standby (accepted: cheaper than the SLA credits + lost deals). Risk: replication lag during failover causes data loss beyond RPO — mitigated by synchronous replication on the critical write path and monitored lag alerts. Risk: failover is never tested and fails when needed — mitigated by mandatory quarterly game days.
>
> ### Measuring progress
> Leading: % of services deployed multi-region; measured RTO/RPO in game days. Lagging: SLA attainment, deals lost to DR objections.

Notice what the diagnosis did: it reframed "build multi-region" (a project) into "40 services hold a single-writer assumption" (the *actual* challenge), and the guiding policy then *rules things out* — that's the difference between this and a goal list.

---

## 6. Finding leverage and the "right" problems

Strategy at this level is mostly about **allocation of scarce attention.** The skill is identifying where a unit of effort produces outsized, compounding return.

**Where leverage hides:**

- **Constraints / bottlenecks.** Find the one thing gating everyone (Theory of Constraints). Optimizing anything that isn't the bottleneck produces zero throughput gain. If every team waits two weeks on a shared deploy pipeline, *that* is the strategy, not your favorite refactor.
- **Decisions that fan out.** A default everyone inherits (the paved road, the standard datastore) is higher-leverage than any single implementation.
- **Things that compound.** CI speed, test reliability, doc quality — each saves a little, every day, forever, for everyone.
- **Recurring pain.** Mine incident reports, on-call gripes, and "why is this so hard" Slack threads. Patterns there are pre-validated problems.
- **Existential / one-way-door risks.** Sometimes the right problem is unsexy (a creaking auth system) but its failure mode is catastrophic.

**Choosing the *right* problem** is itself the highest-leverage act. A brilliant solution to a problem that didn't need solving is negative leverage — it adds maintenance burden and crowds out the real work. Before committing a quarter, ask: *if we nail this, what becomes possible that wasn't? And who, specifically, is in pain today?*

---

## 7. Tech radar & standardization

A **Tech Radar** (popularized by ThoughtWorks) is a lightweight artifact for guiding technology choices across an org without hard mandates. Technologies sit in rings:

| Ring | Meaning | Engineer's takeaway |
|---|---|---|
| **Adopt** | Proven here; default choice | Use it without asking |
| **Trial** | Promising; use on projects that can absorb risk | Pilot it, report back |
| **Assess** | Worth exploring; not for production yet | Spike, don't ship |
| **Hold** | Avoid for new work (legacy or disappointing) | Don't start anything new here |

This is **standardization-as-influence**: it shapes the default without forbidding deviation. Teams *can* go off-radar, but they have to justify it — which is exactly the right friction. Pair it with a clear path: "off-radar choices need a one-pager and staff+ review."

**Standardization trade-off:** every standard you add buys *leverage and consistency* and spends *team autonomy and local fit.* Standardize the **undifferentiated** (logging format, deploy tooling, base images, auth); leave room for choice where **local context dominates** (the algorithm inside a specialized service). The mistake in both directions: anarchy (every team reinvents observability) and tyranny (a mandated framework that fits 60% of teams badly).

---

## 8. Managing tech debt strategically

Tech debt is not a moral failing to be eliminated; it's a financial instrument. The strategic question is never "is there debt?" (always yes) but **"which debt is accruing interest fast enough to pay down, and which should we just service?"**

### A debt-triage framework

| | **Low interest** (rarely touched / stable) | **High interest** (touched often / blocks work / risky) |
|---|---|---|
| **Cheap to fix** | Fix opportunistically (boy-scout rule) | **Fix now** — best ROI on the board |
| **Expensive to fix** | **Live with it.** Document it; don't gold-plate dead code | Strategic project — fund it explicitly, sequence it |

- **Interest rate** = how much this debt slows you down or raises risk *per unit time.* A gnarly module nobody touches has a near-zero interest rate — leave it.
- **Make debt visible.** A debt register or labeled backlog beats tribal knowledge. You can't prioritize what you can't see.
- **Fund paydown explicitly**, not "in spare time" (which doesn't exist). Either a standing capacity allocation (e.g., ~20%) or named projects with real headcount.
- **Tie paydown to feature work.** "We're already in this code for feature X; we pay down the adjacent debt while we're here" is the highest-acceptance pitch to product.
- **Sometimes the right answer is to declare bankruptcy** — rewrite/replace rather than service forever. That's a strategy doc, not a ticket.

> **Anti-pattern:** the perpetual "tech debt sprint" that never comes, and its evil twin, the rewrite-everything crusade that ignores interest rates. Both substitute sentiment for the triage above.

---

## 9. Aligning architecture with org & business (Conway's Law)

> "Organizations design systems that mirror their own communication structure." — Melvin Conway

This is not a curiosity; it's a planning input. Your architecture *will* end up shaped like your org chart whether you intend it or not. So:

- **Inverse Conway Maneuver:** if you want a particular architecture, **shape the teams to match it first.** Want decoupled services? Give teams clear, decoupled ownership boundaries. Want a unified platform? Don't split its ownership across three orgs at war.
- **Architecture follows business priorities.** The strategy doc must connect to business outcomes (revenue, cost, risk, speed). "Multi-region" only matters because it unblocks enterprise sales and stops SLA credits — *say that.* Architecture decisions defended purely on technical elegance lose to architecture decisions defended on business outcomes, every time, and they should.
- **Org boundaries become API boundaries.** A team boundary that cuts across a chatty interface produces a painful API and a perpetual coordination tax. Co-locate ownership of things that change together.

---

## 10. Influencing the roadmap

A staff+ engineer rarely *owns* the roadmap; you *influence* it. Influence without authority is the actual job. How:

- **Bring options, not complaints.** "Here are three paths and the trade-offs" earns a seat; "the architecture is bad" doesn't.
- **Translate to the language of the decider.** PMs hear customer impact and revenue; directors hear risk and cost; engineers hear toil and velocity. Same proposal, three framings.
- **Write it down and circulate early.** The strategy doc *is* the influence vehicle. Pre-socialize with stakeholders one-on-one before any group meeting (Larson: "do the politics in the hallway, ratify in the room") so the meeting confirms rather than debates.
- **Tie technical work to OKRs / business goals** so it competes fairly for capacity instead of being "the engineering tax."
- **Pick your battles by leverage.** You have finite political capital. Spend it on the one-way doors and the genuine bottlenecks; disagree-and-commit on the rest.
- **Make the cost of inaction concrete.** "If we don't do this, here's the incident/cost/lost-deal trajectory" moves roadmaps more than the upside does.

---

## Anti-patterns

| Anti-pattern | Tell | Fix |
|---|---|---|
| **Goals-as-strategy** | A list of targets, no diagnosis or guiding policy | Apply Rumelt's kernel; start with the diagnosis |
| **Fluff strategy** | Buzzwords, nothing falsifiable | Demand "what does this rule out?" |
| **Ivory-tower vision** | Written alone, never circulated | Co-author; spend energy socializing, not drafting |
| **Strategy that says yes to everything** | No non-goals, no sacrifices named | Force explicit trade-offs and non-actions |
| **Debt sentimentality** | "All debt is bad / let's rewrite it all" | Triage by interest rate × cost-to-fix |
| **Architecture vs. org mismatch** | Elegant design fights the org chart forever | Inverse Conway: shape teams first |
| **Tech-elegance defense** | Proposal justified only on technical beauty | Re-anchor to business outcome |
| **Roadmap-by-complaint** | Criticism without options | Bring 2–3 paths with trade-offs |

---

## Key Takeaways

1. **Vision = where (durable, aspirational); strategy = how (concrete, choice-laden).** Don't ship one and call it the other.
2. A real strategy is Rumelt's kernel: **diagnosis → guiding policy → coherent actions.** The diagnosis is the hard, valuable part.
3. **The test of a strategy is what it tells you not to do.** Name your non-goals and non-actions.
4. Write a vision in **concrete present tense**; co-author and socialize it relentlessly.
5. Strategy is **allocation of scarce attention** — hunt for leverage at bottlenecks, fan-out decisions, and compounding wins.
6. Use a **tech radar** to standardize-by-influence; standardize the undifferentiated, allow local choice where context dominates.
7. Triage tech debt by **interest rate × cost-to-fix**; fund paydown explicitly, and sometimes declare bankruptcy.
8. **Conway's Law is a planning input** — shape teams to get the architecture you want, and always tie architecture to business outcomes.
9. You **influence** the roadmap; do the politics in the hallway, bring options not complaints, and make the cost of inaction concrete.
