# 07 — Build vs Buy vs Adopt Open-Source

> **Audience:** Staff/Principal engineers who own a recommendation that a VP will sign off on, finance will model, and an on-call rotation will live with for 3+ years.
>
> **The core mistake** engineers make: treating this as a *technical* decision ("can we build it? of course we can"). It is an *economic and strategic* decision. The right question is never "can we build it" — it's "**should we spend our scarcest resource (senior engineering attention) on this, given everything else we could build instead?**"

---

## 1. The decision in one sentence

> **Build what differentiates you. Buy/adopt everything else — then revisit only when scale or strategy changes the math.**

Almost every regret falls into one of two buckets:

- **Built a commodity** (an auth system, a job queue, a feature-flag service) and spent 3 years maintaining undifferentiated infrastructure.
- **Bought a core differentiator** and discovered you couldn't customize the one thing customers actually paid you for, and the vendor owned your moat.

Your job is to keep the team out of both buckets.

---

## 2. The decision framework (6 axes)

Run every candidate through these six questions. They are ordered: the first two can end the conversation by themselves.

| # | Axis | Question | If "yes/high" → lean |
|---|------|----------|----------------------|
| 1 | **Core / differentiating** | Does this *directly* create the value customers pay us for? Would doing it 2x better win deals? | **Build** |
| 2 | **TCO over 3 yr** | Fully loaded, what's cheaper — including salaries, on-call, opportunity cost? | Whichever is lower |
| 3 | **Time-to-market** | How long until value is in users' hands? Is speed a competitive lever right now? | Fast path (usually **Buy**) |
| 4 | **Control / lock-in** | How painful is exit? Do we need to customize internals, hit exotic SLAs, or own data residency? | High control need → **Build/OSS** |
| 5 | **Team capacity & expertise** | Do we have the people *and* the slack to build *and operate* it for years — not just ship v1? | No slack → **Buy** |
| 6 | **Maturity of options** | Is there a mature, well-supported product/OSS project, or is the market immature/fragmented? | Mature → **Buy/OSS**; immature → **Build** |

### The "core" test (axis 1) — be honest

Most things you think are core are not. A useful filter:

- **Core:** the ranking algorithm at a search company; the risk model at a lending company; the matching engine at an exchange. *Building it badly loses customers.*
- **Context (commodity):** auth, billing, email delivery, logging, CI, feature flags, the CDN, the database engine itself. *Building it perfectly wins no deals — it's table stakes.*

> Geoffrey Moore's framing: **Core** activities differentiate you and create competitive advantage. **Context** activities must be done well but confer no advantage. **Spend innovation on core; spend efficiency on context.** Most build-vs-buy regret is building context.

---

## 3. The hidden costs (this is where the analysis is won or lost)

Junior estimates compare "license fee" vs "two sprints to build it." That comparison is wrong by an order of magnitude. The real costs are hidden.

### 3.1 Hidden cost of BUILDING

| Cost | Description | Rough magnitude |
|------|-------------|-----------------|
| **v1 build** | The part everyone estimates | The *small* part — often 20–30% of lifetime cost |
| **Maintenance** | Bug fixes, dependency upgrades, security patches, OS/lib churn | ~**15–25% of build cost per year**, forever |
| **On-call / operations** | Pages, incidents, capacity, backups, DR drills | 0.2–1.0 FTE ongoing for a real service |
| **Feature catch-up** | The vendor ships features quarterly; your "done" v1 slowly rots | Continuous |
| **Opportunity cost** | The roadmap features those engineers *didn't* build | Usually the **biggest** cost; hard to see |
| **Bus factor / docs / onboarding** | Tribal knowledge, ramp time for new hires | Compounds with attrition |

> **Rule of thumb:** the engineer-year is the unit that matters. A **fully-loaded senior engineer ≈ \$200K–\$300K/yr** (US; salary + benefits + overhead + equity). Two engineers for a year building + 0.5 FTE/yr maintaining is **\$400K + \$125K/yr** — before opportunity cost. Compare *that* to the license.

### 3.2 Hidden cost of BUYING

| Cost | Description | Watch for |
|------|-------------|-----------|
| **Integration** | Wiring it into auth, data model, observability, CI | Often 2–6 weeks even for "plug-and-play" |
| **Per-seat / per-unit scaling** | Price that's great at 50 users and brutal at 50,000 | Model the price *at your 3-yr scale*, not today's |
| **Lock-in** | Proprietary APIs, data formats, workflows your org reshapes around | The deeper the integration, the higher the switching cost |
| **Exit cost** | Data export, re-platforming, retraining when you leave | Estimate it *before* signing |
| **Vendor risk** | Acquisition, EOL, price hikes at renewal, outages you can't fix | Renewal is where leverage flips to them |
| **Limited customization** | The 10% they don't support is sometimes the 10% you need | Verify the hard requirements in a POC |
| **Data egress / overage** | Metered usage that balloons | Read the pricing fine print |

### 3.3 Hidden cost of ADOPTING OPEN-SOURCE

OSS is not free — it's "buy with money, or buy with engineering time." You trade license fees for operational ownership.

| Cost | Description |
|------|-------------|
| **Operate it yourself** | You are now the SRE for Postgres/Kafka/Keycloak. Patching, scaling, upgrades, backups — all yours. |
| **Expertise** | You need real depth, or you're one bad upgrade from an outage. |
| **Support gap** | Community support ≠ a 24/7 SLA. Paid support tiers narrow the cost gap vs SaaS. |
| **Security / supply chain** | CVE tracking, transitive deps, license compliance (GPL/AGPL traps). |
| **Fork risk / abandonment** | The maintainer burns out; the project relicenses (see HashiCorp→BSL, Elastic→SSPL, Redis). |

---

## 4. SaaS vs Self-host vs OSS — the three operating models

This is often the *real* decision once you've decided not to build from scratch.

| Dimension | **SaaS (managed)** | **Self-host commercial / OSS** | **Build from scratch** |
|-----------|--------------------|--------------------------------|-----------------------|
| Time-to-value | Days–weeks | Weeks–months | Months–quarters |
| Up-front cost | Low | Medium | High |
| Marginal cost at scale | **High** (per-seat/usage) | Medium (infra + ops staff) | Low marginal, high fixed |
| Operational burden | Vendor's | **Yours** | Yours |
| Control / customization | Low | High | Total |
| Data residency / compliance | Vendor-dependent | **You control** | You control |
| Lock-in | High | Medium (OSS = low) | None (you own it) |
| Best when | Speed > control; commodity; small-mid scale | Compliance/residency/cost-at-scale need; have ops muscle | It's your core moat |

> **The crossover pattern:** SaaS wins early (cheap to start, no ops). Self-host/OSS wins at scale (the per-seat curve crosses the fixed ops cost). Watch for the **crossover point** — see the worked TCO in §6 and file `08`.

---

## 5. Weighted scoring template

Use a transparent weighted-decision matrix. It doesn't *make* the decision — it **exposes the assumptions** so stakeholders argue about the right things (weights and scores), not vibes.

### Template

1. List the **decision criteria** (use the 6 axes + any org-specific ones).
2. Assign a **weight** to each (must sum to 100).
3. Score each option **1–5** per criterion (5 = best).
4. Weighted score = Σ(weight × score). Highest wins — but **sanity-check the winner against the TCO model**.

```
Weighted Score(option) = Σ_criteria ( weight_i × score_i )
```

### Worked example — "Feature-flag system: build vs buy (LaunchDarkly) vs OSS (Unleash, self-hosted)"

**Context:** Mid-size SaaS, ~80 engineers, ~30 services, no flag system today. Need targeting, gradual rollouts, kill switches, audit log. This is **context, not core** (we sell logistics software, not flag infra).

| Criterion | Weight | Build (in-house) | Buy (LaunchDarkly SaaS) | OSS (Unleash self-host) |
|-----------|:-----:|:---:|:---:|:---:|
| Time-to-market | 20 | 1 | 5 | 3 |
| 3-yr TCO | 25 | 2 | 3 | 4 |
| Control / customization | 10 | 5 | 3 | 4 |
| Lock-in / reversibility | 10 | 5 | 2 | 4 |
| Operational burden (low = good) | 15 | 1 | 5 | 3 |
| Feature maturity / completeness | 15 | 2 | 5 | 3 |
| Team capacity fit | 5 | 1 | 5 | 3 |
| **Weighted total** | **100** | **2.05** | **3.95** | **3.45** |

**Computation (weighted total = Σ weight×score / 100):**

- Build: (20·1 + 25·2 + 10·5 + 10·5 + 15·1 + 15·2 + 5·1)/100 = (20+50+50+50+15+30+5)/100 = **2.20**
- Buy:   (20·5 + 25·3 + 10·3 + 10·2 + 15·5 + 15·5 + 5·5)/100 = (100+75+30+20+75+75+25)/100 = **4.00**
- OSS:   (20·3 + 25·4 + 10·4 + 10·4 + 15·3 + 15·3 + 5·3)/100 = (60+100+40+40+45+45+15)/100 = **3.45**

> Recompute carefully and publish the arithmetic — stakeholders *will* re-add it. (Build = 2.20, Buy = 4.00, OSS = 3.45.)

**Decision:** **Buy** (LaunchDarkly) now for speed and maturity; **re-evaluate at the cost crossover** (see below). Build was never close — feature flags are pure context for this org. The interesting fight is **Buy vs OSS**, and it's a **TCO timing** question.

### The Buy-vs-OSS crossover (numbers)

| | Buy (LaunchDarkly) | OSS (Unleash self-host) |
|---|---|---|
| Up-front (integration) | 2 eng-wk ≈ \$20K | 5 eng-wk ≈ \$50K |
| Recurring | ~\$1,500/mo at 80 seats → growing with headcount | Infra \$300/mo + 0.25 FTE ops ≈ \$60K/yr |
| Yr-1 total | \$20K + \$18K = **\$38K** | \$50K + \$64K = **\$114K** |
| Yr-3 cumulative @ 2x headcount | ~\$20K + (\$18K+\$30K+\$45K) ≈ **\$113K** | \$50K + ~\$192K ≈ **\$242K** |

Here SaaS stays cheaper through year 3 because flag-tooling seats scale sub-linearly and the ops FTE for self-host is a fixed tax. **Crossover would only arrive at much larger scale or with aggressive seat-based price hikes** — exactly the renewal risk to watch.

---

## 6. When to build, when to buy

| Build when… | Buy when… | Adopt OSS when… |
|-------------|-----------|------------------|
| It's your **core differentiator** | It's a **commodity / context** | It's commodity **and** you need control, data residency, or scale economics |
| No mature option exists | A mature, well-supported option exists | A mature OSS project exists *and* you have ops muscle |
| You need control of internals / exotic SLAs | Speed-to-market matters more than control | License/exit risk of SaaS is unacceptable |
| You have **slack** to build *and operate* for years | Your team is capacity-constrained | You want to avoid per-seat scaling but accept ops cost |
| The cost of getting it wrong is borne by *your* customers (so it must be yours) | Vendor's economies of scale beat yours | You'd otherwise build the same thing badly |

---

## 7. The "Buy then Build" path (the pragmatic default)

You rarely have to decide forever. The strongest staff-level move is often **sequencing**:

1. **Buy/adopt now** to get to market and validate the need with real usage.
2. **Instrument** so you learn the real requirements (usage, scale, cost curve, the customizations you actually need).
3. **Revisit at a pre-agreed trigger** — a cost crossover, a renewal, a scale threshold, or a strategic shift that turns this from context into core.
4. **Build only if** the trigger fires and the TCO + strategic case now favors it.

Variants:
- **Buy then build:** SaaS first, replace with in-house once it becomes core or the bill justifies it (classic at scale: companies leave SaaS observability/CDN/queues once the bill hits 7–8 figures).
- **OSS then SaaS:** start self-hosting, move to managed when ops toil outweighs the seat cost (or vice-versa).
- **Wrap, don't fork:** prefer extending via supported APIs/plugins over forking — forking transfers all maintenance to you.

> **Pre-commit the trigger.** "We'll revisit when monthly spend exceeds \$X or seats exceed Y" turns a fuzzy future argument into an automatic review. Without a trigger, inertia decides for you.

---

## 8. Reversibility & exit strategy

The most senior thing you can add to any build-vs-buy doc is an **exit plan written before you commit**. Tie this to **one-way vs two-way doors** (Bezos): two-way-door decisions should be made fast; one-way doors deserve the heavy analysis.

**Exit-readiness checklist (require before signing/committing):**

- [ ] **Data portability:** Can we export *all* our data in an open format, on demand, without vendor cooperation?
- [ ] **API openness:** Are we integrating against open/standard APIs or proprietary ones? How much of our code assumes this vendor?
- [ ] **Abstraction layer:** Have we wrapped the vendor behind our own interface so a swap touches one module, not 200 call sites?
- [ ] **Exit cost estimate:** Roughly how many eng-weeks to migrate off? (If you can't estimate it, you don't understand the lock-in.)
- [ ] **Contractual:** Renewal terms, price-increase caps, data-return clause, termination notice, escrow for source if vendor dies.
- [ ] **Second source:** Is there a credible alternative we could move to? (No alternative = maximum lock-in.)

| Lock-in level | Signal | Mitigation |
|---------------|--------|------------|
| Low | Standard protocols, easy export, behind an abstraction | Fine — proceed |
| Medium | Proprietary API but data exportable | Wrap it; budget exit eng-weeks |
| High | Proprietary data format, deep workflow embedding, no export | Negotiate hard or reconsider; this is a one-way door |

---

## 9. Anti-patterns

| Anti-pattern | Why it hurts | Do instead |
|--------------|--------------|------------|
| **"We can build it in a weekend"** | Ignores the 70–80% lifetime cost (maintenance, ops, opportunity) | Estimate 3-yr TCO including operate-forever cost |
| **Resume-driven development** | Building for novelty/CV, not value | Tie the decision to differentiation + TCO, in writing |
| **NIH (Not Invented Here)** | Rebuilding mature commodities out of pride/distrust | Default to buy/adopt for context |
| **Buy-everything / "no engineers needed"** | Glue code + integration + vendor sprawl becomes the new complexity | Count integration + ops cost honestly |
| **Building your core on a black-box SaaS** | You can't differentiate on something you don't control | Build the core; buy the context |
| **No exit plan** | Lock-in discovered at renewal, with zero leverage | Write the exit plan before committing |
| **Comparing license fee to v1 build cost** | Apples to oranges; ignores ops + opportunity | Compare fully-loaded 3-yr TCO both sides |
| **Ignoring the scaling curve** | Per-seat SaaS great at 50, ruinous at 50K | Model price at 3-yr projected scale |
| **One person decides quietly** | No shared assumptions; relitigated forever | Publish the weighted matrix + TCO; let people fight the numbers |

---

## 10. The 1-page decision record (template)

```
BUILD vs BUY DECISION RECORD
Title:            <capability> — build vs buy vs OSS
Date / Author:    <date> / <staff eng>
Decision:         BUILD | BUY (<vendor>) | ADOPT OSS (<project>) | BUY-THEN-BUILD

1. Capability & need (1 paragraph)
2. Core or context?            <core / context — and why>
3. Options considered           <build | vendor A | vendor B | OSS X>
4. Weighted scoring matrix      <table — weights + scores, arithmetic shown>
5. 3-yr TCO comparison          <table — both sides, fully loaded>
6. Reversibility / exit plan    <lock-in level + exit cost in eng-weeks>
7. Decision + rationale         <2–3 sentences>
8. Revisit trigger              <"re-evaluate when spend > $X or seats > Y or by <date>">
9. Risks & mitigations          <vendor death, price hike, scale, attrition>
```

---

## Key Takeaways

1. **Build core, buy context.** The default for anything that doesn't differentiate you is buy/adopt. Most regret is building commodities.
2. **The decision is economic, not technical.** "Can we build it?" is the wrong question. "Should senior attention go here vs the roadmap?" is the right one.
3. **The v1 build is the small part.** Maintenance (~15–25%/yr), on-call, and opportunity cost dominate the 3-yr TCO. Model the engineer-year, not the license.
4. **Nothing is free.** SaaS = pay with money + lock-in. OSS = pay with engineering time + ops ownership. Build = pay with opportunity cost forever.
5. **Use a weighted matrix to expose assumptions, then sanity-check it against the TCO model.** Publish the arithmetic.
6. **Model price at your 3-yr scale**, not today's — per-seat SaaS curves cross fixed self-host costs at some point. Find the crossover.
7. **"Buy then build" is usually the smart sequence** — get to market, learn the real requirements, and pre-commit a revisit trigger.
8. **Write the exit plan before you commit.** Reversibility (two-way door) lets you decide fast; irreversibility (one-way door) earns the heavy analysis.
