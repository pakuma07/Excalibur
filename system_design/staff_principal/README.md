# Staff & Principal Engineering: Technical Leadership

> The skills that get you *to* senior are not the skills that make you effective *as* a staff+ engineer.

Most system-design material teaches you how to draw boxes and arrows, size a cache, or shard a database. That is necessary, but it is not what distinguishes a staff or principal engineer. At the staff+ level, the bottleneck stops being *"can you design the system?"* and becomes *"can you align an organization around the right system, make the trade-offs legible, and get it shipped through other people?"*

This folder is the **technical-leadership layer** of the reference. It is about the *non-coding* technical skills that define staff+ engineering:

- **Judgment & taste** — choosing the boring, durable option; knowing when good-enough is correct.
- **Influence without authority** — you rarely manage the people you need; you lead through clarity, credibility, and trust.
- **Written architecture artifacts** — design docs, RFCs, ADRs, strategy memos. Writing is the highest-leverage tool you have.
- **Process** — review forums, decision records, migration playbooks that scale beyond any one person.

If `concepts/` and `advanced/` teach you *what* to build, this folder teaches you *how to decide what to build, write it down, and bring people with you.*

---

## Why staff/principal is leadership, not just bigger design

A senior engineer is trusted to deliver a project well. A staff+ engineer is trusted to figure out **which projects are worth doing**, to **de-risk decisions before they're expensive**, and to **multiply the effectiveness of everyone around them**. Your impact is increasingly *indirect*: a doc that aligns three teams, a review that catches a wrong turn six months early, a paved road that makes 50 engineers faster.

Will Larson's framing in *Staff Engineer* is blunt about this: the job is mostly **setting technical direction, mentorship/sponsorship, providing engineering perspective in decisions, and doing the "glue" work** that holds initiatives together. None of that shows up as a green commit graph.

Two consequences worth internalizing early:

1. **Your output is increasingly other people's output.** Leverage beats raw throughput. A great design doc is worth more than a great PR because the doc changes what dozens of PRs become.
2. **Most of your influence is in writing and in conversation, not in code.** Hence this folder's heavy emphasis on *artifacts* (docs/RFCs/ADRs) and *process* (reviews, strategy, migrations).

---

## The 9 documents

| # | Document | What it covers |
|---|----------|----------------|
| 01 | [The Staff Engineer Role](01_the_staff_engineer_role.md) | The senior→staff transition, Larson's archetypes (Tech Lead, Architect, Solver, Right Hand), influence without authority, judgment & taste, glue work, sponsorship, how staff+ is evaluated, failure modes. |
| 02 | [Design Docs & RFCs](02_design_docs_and_rfcs.md) | Why writing is the staff+ superpower; when to write a doc; a complete copy-pasteable design-doc template; the RFC review process; Amazon's 6-pager & PR-FAQ ("working backwards"); driving consensus and handling feedback. |
| 03 | [Architecture Decision Records](03_architecture_decision_records.md) | Capturing the *why* behind decisions; Nygard's ADR format + the MADR variant; when to write one; running an ADR log; lifecycle (proposed/accepted/superseded); worked example ADRs. |
| 04 | [Architecture Review & Trade-offs](04_architecture_review_and_tradeoffs.md) | Running architecture review forums, structured trade-off analysis, making quality attributes explicit, review checklists, avoiding rubber-stamping and bikeshedding. |
| 05 | [Tech Strategy & Vision](05_tech_strategy_and_vision.md) | The difference between vision and strategy; writing a technical strategy that diagnoses, sets direction, and proposes coherent action; getting buy-in; multi-quarter technical roadmaps. |
| 06 | [Platform & Paved Roads](06_platform_and_paved_roads.md) | Platform thinking, "paved roads"/golden paths, internal developer platforms, treating internal tools as products, balancing standardization with autonomy. |
| 07 | [Build vs. Buy](07_build_vs_buy.md) | A decision framework for build/buy/adopt-OSS, TCO of each, core-vs-context, switching costs, vendor risk, and how to write the recommendation up. |
| 08 | [Capacity, Cost & TCO](08_capacity_cost_tco.md) | Capacity planning, cost modeling, unit economics, total cost of ownership, FinOps basics, and presenting cost trade-offs to leadership. |
| 09 | [Large-Scale Migrations](09_large_scale_migrations.md) | Why migrations are a staff+ specialty; the migration playbook (de-risk, enable, finish); strangler-fig and parallel-run patterns; tracking, incentives, and finishing the long tail. |

> Documents 01–03 are authored in full here. Documents 04–09 are indexed above and build on the same artifacts-and-process foundation.

---

## How to use this folder

- **New to staff+?** Read 01 first to calibrate what the job actually is, then 02 — most of your influence will flow through written artifacts.
- **Facing a contentious decision?** 03 (record the *why*) + 04 (run the review) + 02 (write the proposal).
- **Setting multi-quarter direction?** 05, then 06/07/08 for the concrete trade-offs that strategy usually turns on.
- **Inheriting a big mess to untangle?** 09.

---

## Reading list

The opinions here are grounded in a small set of widely respected sources. Read the originals — they are short and high-density.

- **Will Larson — *Staff Engineer: Leadership Beyond the Management Track*** (2021). The canonical text on the role: archetypes, setting direction, the "staff project," and how to operate. Also `staffeng.com` and Larson's blog `lethain.com`.
- **Tanya Reilly — *The Staff Engineer's Path*** (2022). The best practical guide to the day-to-day: big-picture thinking, executing projects through others, and "leveling up" your org. Strong on glue work and influence.
- **Camille Fournier — *The Manager's Path*** (2017). Even on the IC track, understanding the management ladder you parallel is essential; the tech-lead chapters are directly relevant.
- **Michael Nygard — "Documenting Architecture Decisions"** (2011 blog post). The origin of the ADR and its four-section format. See also `adr.github.io` and the MADR project.
- **Google — "Design Docs at Google"** (Malte Ubl / Hacker Noon write-ups; *Software Engineering at Google*, O'Reilly 2020). The widely copied lightweight design-doc culture.
- **Amazon — the 6-pager and PR-FAQ / "Working Backwards"** (Colin Bryar & Bill Carr, *Working Backwards*, 2021). Narrative memos over slides; start from the press release.
- **Gergely Orosz — *The Software Engineer's Guidebook*** and *The Pragmatic Engineer* newsletter. Excellent on RFC culture, design docs, and how senior+ work actually happens in industry.
- **Martin Fowler — `martinfowler.com`** (Strangler Fig, evolutionary architecture, sacrificial architecture). Reference for migration and architecture patterns used in 04 and 09.
- **Richard Rumelt — *Good Strategy / Bad Strategy*** (2011). Not engineering, but the clearest articulation of what a *strategy* is (diagnosis → guiding policy → coherent action). The backbone of doc 05.

---

*Key takeaway: at staff+, the artifact is the work. A clear doc, a well-run review, and a recorded decision outlast any single system you design.*
