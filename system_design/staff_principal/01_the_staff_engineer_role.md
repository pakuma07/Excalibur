# 01 — The Staff Engineer Role

> "The senior engineer fixes the problem in front of them. The staff engineer makes sure the org is working on the *right* problems — and then helps it solve them faster than it could without them." — paraphrasing the recurring theme in Larson and Reilly.

This document is about what staff and principal engineering *actually is* — not the ladder rubric your company publishes, but the lived shape of the work. If you're a strong senior wondering "what's next, and is it even something I want?", or a freshly minted staff engineer wondering "why does this feel so different and uncomfortable?", start here.

---

## 1. The senior → staff transition

Senior is, in most companies, the **terminal level**: you can stay there your whole career and be respected and well paid. Staff is not "senior, but more." It is a **change in kind**, along three axes:

| Axis | Senior | Staff+ |
|------|--------|--------|
| **Scope** | One team's systems and roadmap. | Multiple teams, an org, a domain, or a company-wide concern. |
| **Autonomy** | Given well-formed problems; trusted to solve them well. | Trusted to *find and frame* the problems; decides what's worth doing. |
| **Impact** | Direct: you ship the thing. | Indirect & leveraged: you make *others* ship the right things, faster. |
| **Time horizon** | This sprint to this quarter. | This quarter to multiple years. |
| **Primary medium** | Code & technical execution. | Writing, conversation, and judgment. Code is now a *tool*, not the output. |
| **Definition of done** | The feature works in production. | The org made a good decision and can sustain it without you. |

The uncomfortable part: the **feedback loop gets longer and noisier**. As a senior you knew you had a good day because tests went green. As a staff engineer a good day might be a 45-minute conversation that prevented a bad architecture — and you'll only know it was good a year later. Many strong seniors bounce off staff because the dopamine of shipping disappears and isn't immediately replaced.

> **Calibration question.** "If I disappeared for a month, what would break or drift?" At senior, the answer is *my project slips*. At staff+, the answer should be *several teams lose alignment, a key decision stalls, a migration loses momentum* — your absence is felt across boundaries.

### Promotion vs. operating

A subtle trap: getting *promoted* to staff and *operating* as a staff engineer are different problems. Promotion is often won by a single high-visibility "staff project." Operating at staff is a sustained mode. People who optimized purely for the promotion packet often struggle in the role because they built a highlight reel, not a habit.

---

## 2. The four archetypes (Larson)

Will Larson identifies four common shapes the role takes. Most people lean toward one or two; the archetype is shaped by both your strengths and your org's needs. None is "more staff" than another.

| Archetype | What they do | Where they sit | Strength | Watch-out |
|-----------|--------------|----------------|----------|-----------|
| **Tech Lead** | Guides execution of a specific team or initiative; pairs with a manager. | Embedded in one team. | Closest to delivery; deep context. | Scope can stay too narrow to be "staff"; can become a glorified senior. |
| **Architect** | Owns direction and quality for a critical area/domain across teams. | Spanning a domain (e.g. data platform, payments). | Deep, durable technical direction. | Can drift into ivory-tower "architecture astronaut" disconnected from code. |
| **Solver** | Parachutes into the gnarliest current problem and drives it to resolution. | Roams to wherever the fire is. | Enormous value on ambiguous, high-stakes problems. | Always firefighting; little durable system-building; burnout risk. |
| **Right Hand** | Extends a senior leader's (Director/VP) capacity; operates with borrowed scope. | Attached to an exec. | Org-wide leverage and information. | Influence is positional; dependent on the relationship; can lose technical depth. |

**Worked calibration.** A payments org with a creaky core might need an **Architect** to set direction; the same org mid-incident-storm needs a **Solver**; a fast-growing org where the VP is the bottleneck needs a **Right Hand**. Reading *which archetype your org needs right now* — and being willing to flex — is itself a staff-level skill.

---

## 3. Influence without authority

You almost never have formal authority over the people whose work you need to change. You cannot *order* the search team to adopt your event schema. Influence at staff+ is built from a small number of durable assets:

1. **Credibility.** A track record of being right *and* of admitting when you were wrong. Credibility is your bank account; every confident-but-wrong statement is a withdrawal.
2. **Trust & relationships.** People follow those they trust. Build relationships *before* you need them — the time to know the search team's lead is not the day you need their buy-in.
3. **Clarity.** A crisp written argument that makes the right path obvious is more persuasive than positional power. This is why writing dominates the role.
4. **Reciprocity & generosity.** Help people, sponsor people, make others look good. Influence compounds when people *want* to work with you.

### Tactics that work

- **Disagree-and-commit, visibly.** Model it. If you lose an argument, commit loudly and help it succeed. People remember, and your future disagreements carry more weight.
- **Bring data and options, not verdicts.** "Here are three approaches and the trade-offs" invites collaboration; "do it my way" invites resistance.
- **Find the actual decision-maker.** Influence is wasted if aimed at the wrong person. Map who decides, who advises, who is affected.
- **Let others have the idea.** Plant a seed in a 1:1, let someone champion it as their own. Lower ego, higher leverage.
- **Pre-wire decisions.** Never let a big meeting be the first time anyone hears your proposal. Socialize it 1:1 first; the meeting should ratify, not debate.

> **Anti-pattern: the title-thumper.** "I'm a principal engineer, so we're doing it this way." This works exactly once and corrodes your influence permanently. Authority you have to invoke is authority you don't really have.

---

## 4. Technical judgment & taste

Judgment is the staff+ superpower that's hardest to teach. It's the ability to make a good call with incomplete information, under time pressure, and to be *calibrated* about your own uncertainty.

Components of good engineering judgment:

- **Taste for the boring.** The senior engineer reaches for the interesting tool; the staff engineer reaches for the *durable, well-understood, operable* one — and can articulate *why* "boring" is usually correct. (See Dan McKinley's "Choose Boring Technology.")
- **Right-sizing.** Knowing when 80% is the correct answer and gold-plating is waste, versus when the 20% tail is where all the risk lives. Over-engineering and under-engineering are both judgment failures.
- **Reversibility awareness.** Bezos's "one-way vs two-way doors." Spend judgment-budget on irreversible decisions (data model, public API, core dependency); move fast and delegate on reversible ones.
- **Cost of being wrong.** Calibrate rigor to blast radius. A reversible internal decision deserves a Slack thread; an irreversible cross-org one deserves a design doc and a review.
- **Knowing what you don't know.** "I'd want to load-test that before committing" is a stronger statement than false confidence.

> **Taste is built by reps + reflection + exposure.** Read post-mortems (yours and others'). Study why systems you admire made the choices they did. Pay attention when your predictions are wrong and ask why.

---

## 5. Glue work & leverage

Tanya Reilly's well-known talk "Being Glue" names the unglamorous coordination work that holds projects together: noticing the unowned risk, writing the doc nobody wanted to write, unblocking the cross-team dependency, onboarding the new hire, running the incident review.

The tension: **glue work is essential and high-impact, but often invisible and under-credited** — and it disproportionately falls on women and underrepresented engineers, sometimes derailing *their* technical growth. Reilly's prescription is nuanced:

- For a **staff engineer**, glue work is often exactly the job — leverage *is* coordination. Lean in, but **make it visible** (write the doc, get named as driver) so it counts.
- If glue work is being silently expected of *you as a senior or below* and crowding out your technical growth, name it explicitly with your manager and renegotiate.

### Leverage: the core mental model

> **Leverage = impact ÷ effort.** Staff+ work is the relentless search for high-leverage actions.

| Activity | Leverage |
|----------|----------|
| Fixing one bug | Low (1×) |
| Fixing the class of bug (lint rule, type, framework guard) | High (N×) |
| Writing a design doc that aligns 3 teams | Very high |
| Unblocking a stuck cross-team decision | Very high |
| Sponsoring an engineer who then leads an area | Compounding |
| Heroic solo crunch on one project | Low and *negative* (creates a bus factor of 1) |

The hardest discipline is **resisting the pull of low-leverage work you're good at**. Shipping that feature yourself feels great and is fast — but a staff engineer who hoards the satisfying work starves the team of growth and caps the org at their personal throughput.

---

## 6. Sponsoring & mentoring

These are different and both are part of the job:

| | Mentoring | Sponsoring |
|--|-----------|-----------|
| **What** | Giving advice, sharing knowledge, answering questions. | Spending your own credibility/capital to create opportunities for someone. |
| **Where it happens** | In the room *with* the person. | In the room *they're not in* ("she should lead the migration"). |
| **Risk to you** | Low — your time. | Real — your reputation is attached to their success. |
| **Who benefits most** | Anyone. | People who get overlooked for high-visibility work. |
| **Example** | "Here's how I'd approach that design." | "I recommended you to drive the design review; I'll back you." |

Sponsorship is the higher-leverage and rarer act. A staff engineer's lasting legacy is often the **people they grew**, not the systems they built — systems get rewritten; engineers carry the lessons forward. Practical move: keep a mental (or literal) list of people ready for a stretch, and *actively place them* when opportunities arise.

---

## 7. How staff+ is evaluated

The performance conversation changes shape. You are no longer primarily judged on output you personally produced. Common evaluation dimensions:

- **Technical direction** — Did you set or meaningfully shape direction for an area, and was it sound?
- **Impact through others / leverage** — Are teams better, faster, or safer because of your work, even where you didn't write the code?
- **Judgment** — When you made calls, did they hold up? Were you calibrated?
- **Force multiplication** — Did you grow people, raise the bar, leave reusable artifacts (docs, paved roads, standards)?
- **Cross-org effectiveness** — Can you operate across boundaries and align people you don't manage?
- **Communication** — Are your written artifacts clear enough to align an org? Are your verbal updates trusted by leadership?

> **Documenting your impact is itself part of the job**, because so much of it is indirect and would otherwise be invisible. Keep a running "brag doc" / impact log. This isn't ego — it's the only reliable record of leveraged work, and it makes promo and perf calibration honest.

---

## 8. Common failure modes

| Failure mode | What it looks like | Fix |
|--------------|--------------------|-----|
| **The hero / solo throughput trap** | Personally shipping all the hard stuff; bus factor of 1; team learns nothing. | Delegate the satisfying work; measure yourself by the team's output, not yours. |
| **Architecture astronaut** | Beautiful diagrams disconnected from delivery reality; "ivory tower." | Stay close to code and to on-call pain; prototype your own proposals. |
| **The title-thumper** | Wins arguments by invoking seniority. | Win with clarity and data; reserve authority for genuine emergencies. |
| **Glue-work martyr** | Drowns in invisible coordination; no durable technical artifacts; gets passed over. | Make glue work visible; balance it with direction-setting that's clearly yours. |
| **Disconnected oracle** | Pronounces on systems they no longer understand; advice is stale. | Maintain technical currency; pair, review code, do occasional hands-on work. |
| **Scope mismatch** | Operating like a senior with a staff title (or sprawling with no focus). | Pick a clear area of responsibility; align it with your manager and org needs. |
| **Says yes to everything** | Spread across ten initiatives, deep on none, drives none to done. | Pick the 2–3 things only you can do; explicitly drop or hand off the rest. |
| **Avoids conflict** | Lets bad decisions slide to keep the peace. | Disagree respectfully and on the record; that's literally what you're paid for. |

---

## 9. Worked example: a week in the life

A composite week for a staff engineer (Architect-leaning) in a mid-size company:

- **Mon** — Wrote a one-page diagnosis of why the checkout team and the inventory team keep duplicating state, and circulated it to both leads 1:1 (pre-wiring a future RFC). *Leverage: framing a cross-team problem.*
- **Tue** — Reviewed a junior engineer's design doc; left questions, not answers; afterward told their manager the engineer is ready to drive the next review (sponsorship). *Leverage: growing a person + raising doc quality.*
- **Wed** — In an architecture review, talked a team *out* of building a bespoke workflow engine, pointing them at the existing paved road. *Leverage: prevented months of duplicated work.*
- **Thu** — Paired for two hours on a thorny concurrency bug to stay technically current and earn credibility. *Leverage: low directly, high for trust.*
- **Fri** — Drafted the "guiding policy" section of next quarter's data-platform strategy; got the director's early reaction before writing the full memo. *Leverage: shaping multi-quarter direction.*

Note how little of this is "writing production code," and how much is **framing problems, aligning people, growing others, and writing**.

---

## Key Takeaways

- Staff+ is a **change in kind**, not degree: bigger scope, more autonomy, *indirect* leveraged impact, longer feedback loops, and writing/judgment as your primary medium.
- Know the **four archetypes** (Tech Lead, Architect, Solver, Right Hand) and read which one your org needs now.
- You lead through **influence, not authority** — built on credibility, trust, clarity, and generosity. Invoking your title is a confession that you've run out of influence.
- **Judgment and taste** — especially a taste for boring, durable, operable choices and an instinct for reversibility — are the hardest and most valuable skills.
- **Leverage = impact ÷ effort.** Resist the pull of satisfying low-leverage work. Make glue work visible. Sponsor, don't just mentor.
- You'll be evaluated on **direction, leverage, judgment, and force-multiplication** — so document your indirect impact, because no one else will.
- The lasting legacy is usually the **people you grew and the artifacts you left**, not the systems (which get rewritten).
