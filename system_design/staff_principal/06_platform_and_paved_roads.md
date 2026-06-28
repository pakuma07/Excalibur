# Platform Thinking & Paved Roads

> **Audience:** Staff / Principal engineers building or championing internal platforms — the leverage multiplier that lets one team make hundreds of other engineers faster, safer, and happier.
>
> The core insight: at scale, the highest-leverage engineering is often *not* shipping features. It's building the **paved road** that makes every other team's feature-shipping cheaper and safer. Done right, a platform team's output is multiplied by every team that rides it.

---

## 1. Products vs. Platforms

A **product** solves a problem for an external customer. A **platform** solves a recurring problem for *internal* teams so they don't each solve it (badly, differently) themselves.

| | Product | Platform |
|---|---|---|
| Customer | External users | Internal engineers / teams |
| Success metric | User/business outcomes | Adoption, DX, cognitive load reduced |
| Value model | Direct revenue | Leverage — N teams × time saved |
| Failure mode | Nobody wants it | Everybody routes around it |
| Danger | — | Built for an imagined user, never adopted |

The most important sentence in this whole document:

> **An internal platform is a product, and its users are engineers who can — and will — route around you.** Internal users are *captive in theory and mutinous in practice.* They can build their own thing, copy a competitor's pattern, or just keep using the old way. That is the discipline that keeps good platforms honest.

---

## 2. The Paved Road / Golden Path

The **paved road** (Netflix's term) — equivalently the **golden path** (Spotify) or **paved path** — is the **opinionated, supported, well-lit default way** to build and run something in your org. Pick the paved road and you get CI/CD, observability, security, secrets, deploys, and on-call tooling *for free, integrated, and maintained for you.*

```
        OFF-ROAD                              PAVED ROAD
   (allowed, unsupported)                 (default, supported)
   ───────────────────────               ─────────────────────────
   • roll your own everything            • scaffold a service in minutes
   • you own all the toil                • CI/CD, metrics, logs, traces wired
   • security is your problem            • security/compliance baked in
   • novel = you debug it alone          • paged at 2am? there's a runbook
   • full freedom, full burden           • freedom within guardrails
        │                                          │
        └──────────  you CAN leave the road,  ─────┘
                     but you carry your own water
```

### Why "paved road" beats "mandate"

This is the central, opinionated claim, and it matters:

| Mandate ("you MUST use X") | Paved road ("X is the easy, supported default") |
|---|---|
| Breeds malicious compliance & resentment | Earns genuine adoption |
| Demands enforcement (a cop, a gate, a linter war) | Self-enforcing via convenience |
| Brittle: a bad mandate is stuck | Adapts: a bad road gets abandoned, giving you signal |
| Ignores legitimate edge cases | Edge cases leave the road *honestly* and visibly |
| Adoption = compliance % | Adoption = revealed preference |

> **Make the right thing the easy thing.** If your paved road needs a mandate to get adopted, the road isn't paved well enough yet — the mandate is hiding a product failure. Mandates are a tool of last resort, reserved for genuine non-negotiables (regulatory, security-critical), and even then the *implementation* of the mandate should be a paved road.

**The deal the paved road offers:** "Stay on the road and we carry your operational burden — upgrades, security patches, the 2am page tooling. Leave the road and you're free to, but you carry your own water." That's a *fair, attractive* trade, not a threat. Netflix's "paved road, but freedom and responsibility" is exactly this: you may go off-road, but then it's on you.

---

## 3. Internal Developer Platforms (IDP), self-service & golden paths

An **Internal Developer Platform (IDP)** is the productized layer that delivers the paved road as **self-service.** The defining property is **no tickets**: an engineer gets what they need through an API/portal/CLI in minutes, not by filing a request and waiting on another team.

### Self-service is the whole point

| Ticket-driven (anti-pattern) | Self-service (the goal) |
|---|---|
| File ticket → wait days → human provisions | `platform create service` → running in minutes |
| Platform team is a bottleneck & on-call for requests | Platform team builds capability, not fulfills requests |
| Scales with platform headcount | Scales with usage, not headcount |
| Knowledge in people's heads | Knowledge in the system + docs |

**Golden paths** are the concrete, documented, end-to-end workflows the IDP supports: "create a new backend service," "add a new event consumer," "stand up a cron job." Each golden path = a scaffolded template + the wired-in defaults + the docs. Spotify formalized golden paths as step-by-step, opinionated tutorials backed by supported tooling.

### Backstage & developer portals

**Backstage** (open-sourced by Spotify, now CNCF) is the canonical **developer portal** — the front door to the platform:

- **Software Catalog** — every service, its owner, its docs, its dependencies. (Solves "who owns this and is it on fire?")
- **Software Templates / Scaffolder** — one click instantiates a golden path with all defaults wired.
- **TechDocs** — docs-as-code living next to the service.
- **Plugins** — CI status, on-call, cost, security findings, all in one pane.

The portal's job is to turn "tribal knowledge + 12 internal tools" into one discoverable, self-service surface. (Alternatives/related: Port, Cortex, OpsLevel, Humanitec, or a homegrown portal — the *pattern* matters more than the product.)

---

## 4. Treat internal tooling as a product

The discipline that separates platforms people love from platforms people endure:

- **Have a roadmap and a PM mindset.** Talk to your users (internal engineers). Run interviews. Ship based on demand, not on what's fun to build.
- **Publish SLAs/SLOs for the platform itself.** "The build system is 99.9% available; provisioning completes in < 5 min p95." Your platform's reliability is *your users' baseline* — their reliability can't exceed yours.
- **Docs are a feature, not an afterthought.** For a self-service platform, the docs *are* the product surface. Undocumented capability ≈ nonexistent capability. Invest in getting-started guides, golden-path tutorials, runbooks.
- **Measure Developer Experience (DX)** and treat the numbers as product metrics (see §7).
- **Version and deprecate gracefully.** You have internal customers; breaking them without migration paths is how you lose trust permanently. Provide upgrade tooling, not just changelogs.
- **Dogfood.** The platform team should build something real on its own platform. The fastest way to fix a bad golden path is to walk it yourself.

---

## 5. "You build it, you run it" & cognitive load (Team Topologies)

Werner Vogels (Amazon): **"You build it, you run it."** Teams own their services in production — including the pager. This aligns incentives beautifully: the people who can fix the reliability are the people woken up by its absence.

But taken naively it loads every team with *enormous* cognitive burden — Kubernetes, observability, security, networking, CI/CD, on top of their actual domain. **This is the problem platforms exist to solve.**

### Team Topologies (Skelton & Pais) — the model

Four team types and three interaction modes give a vocabulary for *who carries what*:

| Team type | Purpose |
|---|---|
| **Stream-aligned** | Owns a slice of business value end-to-end (most teams). Fast flow is the goal. |
| **Platform** | Provides internal services/paved road that *reduce stream-aligned teams' cognitive load* |
| **Enabling** | Coaches/upskills other teams on hard capabilities, then leaves |
| **Complicated-subsystem** | Owns a part needing deep specialist expertise (e.g., a pricing engine, a video codec) |

```
   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   │  Stream-    │  │  Stream-    │  │  Stream-    │   ← own business value,
   │  aligned A  │  │  aligned B  │  │  aligned C  │     "build it, run it"
   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
          │  X-as-a-Service │ (self-service, low friction)
          ▼                ▼                ▼
   ┌─────────────────────────────────────────────────┐
   │              PLATFORM (paved road)               │  ← reduces cognitive load
   │   CI/CD · observability · runtime · security     │     so stream teams can
   │   secrets · provisioning · golden paths          │     focus on their domain
   └─────────────────────────────────────────────────┘
```

**The platform's north star: reduce stream-aligned teams' cognitive load** so "you build it, you run it" stays *humane.* The platform team carries the deep operational complexity (how to run a multi-AZ datastore safely) as a **product** so each stream team doesn't have to learn it. The right interaction mode for a mature platform is **X-as-a-Service** (self-service, minimal collaboration); use **Collaboration** mode only temporarily while you discover what to productize, then deliberately wean off it (lingering collaboration mode means the platform isn't self-service yet).

---

## 6. Standardization vs. autonomy

The eternal platform tension, and it has no permanent answer — only a *position you choose deliberately.*

```
  Full autonomy  ◄─────────────────────────────────►  Full standardization
  (every team                  SWEET SPOT                  (one mandated
   picks its own           "paved road + escape hatch"      way for all)
   stack)
  + max local fit          + leverage where it counts      + max consistency
  + max velocity locally   + freedom where it matters      + max leverage
  - chaos, no leverage     - requires real product work    - poor local fit
  - duplicated toil        - the hardest to maintain       - resentment, route-around
  - hiring/oncall sprawl                                    - innovation chilled
```

**The staff+ judgment call:** standardize where consistency creates leverage and the work is *undifferentiated* (logging, deploys, base images, auth, observability); preserve autonomy where *local context dominates* (the core logic of a specialized service, an experimental product team, a domain with genuinely different constraints). The paved road *is* the negotiated answer: a strong, opinionated default (leverage) **plus** a sanctioned escape hatch (autonomy). You get most of the leverage while keeping the system honest, because the people who leave the road give you a signal about where it's inadequate.

---

## 7. Measuring platform success

If you can't measure it, you can't run it as a product or defend its headcount. Mix **adoption**, **DX**, **delivery**, and **reliability**.

### Adoption (is anyone actually using it — by choice?)

- % of services / teams on the paved road (the headline number).
- Trend of *new* projects starting on the road (leading indicator).
- Off-road rate and *reasons* (every off-ramp is a product backlog item).

### Developer Experience (do they like it?)

- **DevEx / DX surveys** — periodic sentiment ("how easy is it to ship a new service?"). The SPACE framework (Satisfaction, Performance, Activity, Communication, Efficiency) is the canonical multi-dimensional model — don't reduce DX to a single number.
- **Time-to-first-deploy** for a new hire / new service (onboarding friction).
- Support burden: ticket/Slack volume per active team (should *fall* as self-service matures).

### Delivery (is it making teams faster?) — DORA

The four **DORA** metrics are the standard outcome measures the platform should move:

| Metric | What it measures |
|---|---|
| **Deployment frequency** | How often teams ship |
| **Lead time for changes** | Commit → production |
| **Change failure rate** | % of deploys causing incidents |
| **Time to restore (MTTR)** | Recovery speed |

A good platform improves all four for the teams that adopt it — that's your causal story for value.

### Reliability of the platform itself

- Platform SLO attainment (your users inherit your reliability ceiling).
- Provisioning latency, build-system uptime, portal availability.

> **The single most honest platform metric is voluntary adoption.** If teams choose the paved road *without being forced*, it's delivering value. If adoption only holds up under mandate, the metric is lying to you.

---

## 8. Migration & adoption: carrots > sticks

You've built the road. Now teams have to actually move onto it — and they have existing systems, deadlines, and skepticism. Adoption is a *change-management* problem, not a technical one.

### The adoption playbook

1. **Land a lighthouse customer.** Get one credible team fully onto the road and make them wildly successful and *visible*. Internal social proof beats any all-hands slide.
2. **Make migration nearly free.** Provide codemods, automated migration tooling, side-by-side runbooks. Every hour of migration toil you remove is an adoption you win. (You are competing against "do nothing," which costs them zero today.)
3. **Make the new path strictly better at the moment of choice.** When standing up a *new* service is dramatically easier on the road, adoption for greenfield work is automatic. Win new work first; migrate legacy second.
4. **Carrots, loudly.** Less toil, free observability, security handled, faster onboarding, no 2am undefined-behavior. Sell the relief.
5. **Sticks, sparingly and last.** When you must deprecate, give a long runway, real migration support, and a clear date — *after* the carrot phase. Reserve hard deadlines for security/compliance.
6. **Measure and publish the migration funnel** so the org sees momentum (and so you spot where teams get stuck — that's a product bug).

> **If you need a big stick, your carrot is broken.** Resistance is product feedback. The teams resisting usually have a real edge case your road doesn't serve — go fix the road, don't escalate to their manager.

---

## Anti-patterns

| Anti-pattern | Tell | Fix |
|---|---|---|
| **Ivory-tower platform** | Built for an imagined user; nobody adopts | Treat it as a product; interviews, lighthouse customer |
| **Mandate-driven adoption** | Adoption only survives because it's required | Improve the road until it wins on convenience |
| **Ticket-ops disguised as a platform** | "Self-service" still means filing a request | Real APIs/portal/CLI; remove humans from the loop |
| **No escape hatch** | Road is mandatory with no off-ramp | Sanction off-road with "you carry your own water" |
| **Cognitive-load dumping** | "You build it, you run it" with no platform support | Platform absorbs the deep operational complexity |
| **Docs as afterthought** | Powerful platform nobody can figure out | Docs are the product surface; fund them |
| **Breaking changes without migration** | Internal users burned by upgrades | Versioning, codemods, deprecation runways |
| **Lingering collaboration mode** | Platform team perpetually pair-debugging with users | Productize into X-as-a-Service; wean off |
| **Vanity adoption metrics** | Counting mandated usage as success | Track *voluntary* adoption + off-road reasons |

---

## Key Takeaways

1. **An internal platform is a product** whose users can and will route around it — that mutiny option keeps it honest.
2. The **paved road / golden path** wins through convenience, not coercion: make the right thing the easy thing.
3. **Paved road beats mandate.** If adoption needs a mandate, the road isn't paved well enough yet.
4. **Self-service (no tickets)** is the defining property of an IDP; portals like **Backstage** are the front door (catalog + scaffolder + docs).
5. Run internal tooling like a product: **roadmap, SLOs, docs, DX metrics, graceful deprecation, dogfooding.**
6. Platforms exist to keep **"you build it, you run it" humane** by *reducing stream-aligned teams' cognitive load* (Team Topologies); aim for **X-as-a-Service**, not perpetual collaboration.
7. The **standardization↔autonomy** answer is a deliberately chosen position; the paved road *is* the compromise (strong default + escape hatch).
8. Measure with a **blend**: voluntary adoption (the most honest), DX/SPACE, DORA delivery metrics, and platform reliability.
9. Drive adoption with **carrots > sticks**: lighthouse customer, free migration, win greenfield first. If you need a big stick, your carrot is broken.
