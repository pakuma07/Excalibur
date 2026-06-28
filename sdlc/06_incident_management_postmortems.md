# 06 — Incident Management & Postmortems

> **Audience:** Staff/principal engineers, on-call leads, and EMs who own the *process* of responding to and learning from production failures at scale. This chapter is about the **incident-management discipline** — declaring, commanding, communicating, and learning. The exact *technical* triage (CPU/mem/IO/network commands, war-room runbook, postmortem template) lives in the sibling [../os_net/enterprise_scenarios/](../os_net/enterprise_scenarios/README.md); we link to it rather than duplicate it.

---

## 1. What is an incident?

An **incident** is any unplanned event that degrades or threatens a service in a way users (or the business) notice or soon will. It is not "a bug" and not "an alert" — it is a *situation that requires a coordinated response right now*.

The defining traits:

- **User or business impact** (or imminent impact) — errors, latency, data loss, unavailability, security exposure.
- **Urgency** — it cannot wait for the normal backlog.
- **Coordination** — it usually needs more than one person, or at least a deliberate response, not a casual fix-in-passing.

The single most important cultural move: **make declaring an incident cheap and routine.** The dominant failure mode across the industry is *under-declaring* — engineers quietly poke at a problem for 40 minutes, it gets worse, and only then does anyone pull the cord. See §4.

---

## 2. Severity levels

Severity (SEV1–SEV4, or P0–P3) is a shared vocabulary. Its purpose is **consistent, fast response calibration**: everyone hears "SEV1" and instantly knows the expected urgency, staffing, comms cadence, and escalation. Inconsistent sev definitions are corrosive — one team's "SEV2" is another's shrug, and pages get ignored or over-escalated.

| Sev | Alias | Definition (impact & scope) | Response expectation |
|-----|-------|------------------------------|----------------------|
| **SEV1** | P0 | Critical: major user-facing outage, data loss/corruption, security breach, or revenue-stopping. Broad scope (all/most users, core flow). | Immediate, all-hands. IC + full ICS. Exec + status-page comms. 24/7 until mitigated. |
| **SEV2** | P1 | Major: significant degradation — a key feature broken, a region down, elevated error rate breaching SLO for many users. | Page on-call immediately, assemble ICS, status updates on cadence. |
| **SEV3** | P2 | Minor: limited or degraded impact — non-critical feature, single tenant, workaround exists, slow burn of error budget. | Handle in business hours / by on-call. Lightweight tracking. |
| **SEV4** | P3 | Low: negligible user impact — cosmetic, internal-only, or a near-miss worth recording. | Ticket; fold into normal work. |

Anchor severity to **observable impact**, not to guesses about cause. "We don't yet know why" is never a reason to lower severity. Tie thresholds to your SLOs and error budgets — see [05 — Observability & SLOs](05_observability_slos.md). When in doubt, declare *higher*; downgrading later is free, while a delayed escalation is not.

---

## 3. The Incident Command System (ICS)

Borrowed from emergency services, ICS is the org structure of a response. Its **key insight: separate command from debugging.** The person coordinating must not be heads-down in a stack trace, and the people debugging must not be juggling exec updates.

| Role | Owns | Does NOT do |
|------|------|-------------|
| **Incident Commander (IC)** | Coordinates the response, holds the big picture, makes decisions, drives toward mitigation, assigns work, calls escalations. | **Does NOT debug.** The moment the IC opens a terminal, command is lost. |
| **Operations / Ops Lead** | The hands-on responders. Investigate, run mitigations (rollback, failover, flags), report findings to IC. | Decide priorities solo, talk to execs, manage comms. |
| **Communications Lead** | Status page, stakeholder/exec updates, internal channel summaries on a cadence. The single external voice. | Debug or make technical calls. |
| **Scribe** | Maintains the timeline in real time — decisions, actions, timestamps. Feeds the postmortem. | Get pulled into debugging. |

**Why separation works:** debugging is *narrow and deep* (one engineer, one hypothesis, full focus). Command is *broad and shallow* (who's doing what, are we mitigating, who needs to know). One brain cannot do both well under pressure. The IC keeps options open ("can we just roll back while you investigate?") while engineers go heads-down.

**Single-responder vs major-incident.** Not every incident needs four people. For a SEV3, the on-call engineer is IC, Ops, Comms, and Scribe at once — that's fine. The structure *scales up*: the trigger to formalize roles is when you notice you're context-switching between commanding and debugging, or when a second responder joins. For SEV1, fill every role explicitly and *say the names out loud*: "I'm IC, Priya is Ops lead, Sam has comms."

---

## 4. The response lifecycle

```
detect → declare → assemble → MITIGATE → communicate → resolve → follow-up
                                  ↑ (loop: diagnose only enough to mitigate)
```

1. **Detect.** Alert fires, customer reports, dashboard turns red. Good detection (SLO burn alerts, synthetics) shortens MTTD — see [05 — Observability & SLOs](05_observability_slos.md).
2. **Declare.** Open the incident, assign a number, set initial severity, page the IC. **Lower the bar to declare.** A declared incident that turns out minor costs five minutes; an under-declared one costs an outage. Reward people who pull the cord.
3. **Assemble.** Spin up the ICS roles and the incident channel. Get the right Ops responders, not *all* responders (see the swarm anti-pattern, §5).
4. **Mitigate FIRST, diagnose later.** **Stop the bleeding before satisfying curiosity.** Roll back the deploy, fail over to a healthy region, shed load, flip the feature flag off. *Users before root cause.* You do not need to understand *why* to make it stop — you need to know *what changed* and *how to undo it*. Full root-cause analysis happens in the postmortem, calmly, afterward.
5. **Communicate.** Post to the status page, brief stakeholders, hold a cadence (§5).
6. **Resolve.** Impact is gone and confirmed (metrics back to baseline, no recurrence). Resolution ≠ root cause found — it means the *bleeding stopped*.
7. **Follow-up.** Schedule the postmortem, capture action items (§7–8).

> **The cardinal rule:** *Mitigation is not the same as a fix.* A rollback that restores service is a complete, correct first move even if you never learn the cause that night. Curiosity is a postmortem activity.

The mitigation levers come from your release and platform tooling — see §6.

---

## 5. Communication discipline

Under pressure, communication is where incidents are won or lost. The goal is a **single source of truth** and a steady rhythm so nobody has to ask "what's happening?"

- **One internal incident channel.** All response chatter lives there. The IC and Scribe keep it the record of truth. No side-DMs that fragment context.
- **Regular cadence updates.** Comms Lead posts on a fixed interval (e.g. every 15–30 min for SEV1) *even if the update is "still investigating, no change."* Silence breeds a swarm of "any update?" pings.
- **External status page.** Honest, plain-language, no internal jargon, updated on cadence. Customers forgive outages; they don't forgive silence.
- **Exec / stakeholder comms.** Impact, current action, ETA-to-next-update (never a fake ETA-to-fix). The Comms Lead shields Ops from this.

**Avoid the swarm / too-many-cooks.** A SEV1 attracts a crowd. Twenty people in the channel each asking questions *is itself an outage* — of the responders' attention. The IC actively manages this: "Thanks all — Ops is Priya and Sam, everyone else please observe; I'll post updates every 15." Pull people *in by name* when needed; everyone else watches.

**Incident-channel update template:**

```
[INC-1234] SEV1 — Checkout 5xx spike    Status: MITIGATING
Time: 14:32 UTC  (next update 14:47)
Impact: ~40% of checkout requests failing, all regions, since 14:08.
Current action: Rolling back deploy v812 (Ops: Priya). Flag `new_cart` killed.
Working theory: bad config in v812. NOT confirmed — mitigating regardless.
IC: @parveen  Comms: @sam  Scribe: @lee
```

---

## 6. Mitigation playbook

The standard levers — reach for these *before* deep diagnosis. Each ties to platform capabilities you must build *ahead* of time:

- **Rollback** the last deploy. The single highest-value lever; most incidents correlate with a recent change. Requires fast, safe rollback — see [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md).
- **Feature-flag kill switch.** Disable the suspect feature without a deploy. Instant, surgical. See [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md).
- **Failover.** Shift traffic to a healthy region/replica/cell.
- **Load shedding.** Drop or queue low-priority traffic to protect the core flow.
- **Scale out.** Add capacity if the cause is saturation (buys time, rarely the real fix).
- **Traffic drain.** Cordon a bad node/AZ and let it bleed off gracefully.

> The *how* — exact commands to confirm a saturated CPU, a thrashing disk, a packet-loss path, or a deadlock, and which lever the symptom points to — is the technical triage runbook in [../os_net/enterprise_scenarios/](../os_net/enterprise_scenarios/README.md), with the cross-layer war-room playbook in [../os_net/enterprise_scenarios/05_cross_layer_triage.md](../os_net/enterprise_scenarios/05_cross_layer_triage.md). Don't reinvent it; link to it during the incident.

---

## 7. Blameless postmortems — the heart

A postmortem (a.k.a. retrospective, learning review) is the artifact that turns an incident into organizational knowledge. Its quality is determined almost entirely by one thing: **whether it is blameless.**

**The philosophy.** *Systems and conditions fail, not people.* When a competent, well-intentioned engineer made a decision that contributed to an outage, the interesting question is never "why were they careless?" but **"what about the system made that the reasonable thing to do at the time?"** — the **second story**. The first story is "Sam pushed a bad config." The second story is "the config had no validation, the canary didn't cover that path, and the rollback took 20 minutes — *any* engineer would have hit this." Always **assume good intent**: nobody comes to work to break prod.

**Why blame destroys learning.** The instant people fear punishment, they **hide information** — they soften timelines, omit the embarrassing detail, stop volunteering for on-call. You lose exactly the data you need. A blameful postmortem is a *one-time* event: it produces one scapegoat and a permanently quieter, less honest org. Blameless is an *investment* in people telling you the truth next time.

**Postmortem skeleton:**

```markdown
# Postmortem: INC-1234 — Checkout 5xx spike (2026-06-23)

## Summary
One-paragraph plain-language description. What broke, blast radius, how long.

## Impact
Quantified: users affected, requests failed, revenue/SLA, error-budget burn.

## Timeline (all times UTC)
14:08  Deploy v812 rolls out to prod.
14:11  Checkout error rate begins climbing.
14:19  SLO burn alert fires; on-call paged.
14:24  Incident declared SEV1; IC assigned.   ← 13 min detect→declare gap
14:31  Working theory: v812 config. Rollback started.
14:38  Error rate returns to baseline. MITIGATED.
15:02  Rollback confirmed stable. RESOLVED.

## Root-cause analysis
Causal chain + contributing factors (see §8). Not a single "root cause."

## What went well
Fast rollback once decided; clean comms cadence.

## What went poorly / where we got lucky
13-min gap before declaring. Canary didn't exercise the checkout path.

## Action items
| # | Action | Owner | Due | Priority | Ticket |
|---|--------|-------|-----|----------|--------|
| 1 | Add config schema validation in CI | @priya | 2026-07-07 | P1 | JIRA-901 |
| 2 | Extend canary to cover checkout flow | @lee   | 2026-07-14 | P1 | JIRA-902 |

## Lessons
Generalizable takeaways for other teams.
```

The **timeline** and **action items** are non-negotiable. A postmortem without dated, owned action items is a diary, not a learning tool.

---

## 8. Root-cause analysis techniques

- **5 Whys.** Ask "why" repeatedly to push past the surface symptom. Useful, *with a sharp limit:* it implies a single linear chain to one root. Real failures at scale almost never have one root.
- **It's never just one cause.** Production incidents are the intersection of **multiple contributing factors** — a latent bug, a gap in test coverage, an alert that fired late, a runbook that was stale. Remove any *one* and the incident likely doesn't happen. Hunt for the *set*, not the single culprit.
- **Causal chains & contributing factors.** Map the chain of events and, at each node, the conditions that allowed it. This surfaces systemic fixes (validation, coverage, faster rollback) instead of "be more careful."
- **Counterfactual pitfall.** Beware "if only X had done Y, this wouldn't have happened." Counterfactuals feel like analysis but are really blame in disguise and rely on hindsight the responder didn't have. Ask what *information and tooling* were available *at the time*, not what an omniscient observer would have done.

---

## 9. Action items that actually happen

Finding the cause is the easy half. The hard half is **changing the system so it can't recur** — and this is where most orgs fail.

- **SMART:** Specific, Measurable, Assigned, Realistic, Time-bound. "Improve monitoring" is not an action item. "Add a checkout-flow synthetic check with a 2% error SLO alert, owned by @lee, due 7/14" is.
- **Owned by a person**, not a team. A team owns nothing.
- **Tracked** in the same backlog as features, with a due date, reviewed until closed.
- **Prioritized against features.** This is the real, organizational discipline problem: reliability work loses to shiny features unless leadership protects it (this is partly what **error budgets** are for — see [05 — Observability & SLOs](05_observability_slos.md)).

**Postmortem theater** is the failure where you write beautiful postmortems and *nothing changes* — action items rot, the same incident recurs. Prevent it: review open action items in the *same* meeting that reviews new incidents; report aging action items to leadership; treat an unactioned postmortem as an incident in itself.

---

## 10. Learning from incidents / resilience culture

The framing to internalize: **every incident is a lesson the system taught you — and you already paid for it.** Wasting it is the only unforgivable failure.

- **Incident review meetings.** A recurring forum where postmortems are walked through blamelessly, action items tracked, and patterns spotted across incidents.
- **Share across the org.** Publish postmortems widely. One team's outage is every team's free lesson. Build a searchable corpus.
- **Near-miss reporting.** Capture the incidents that *almost* happened (SEV4s, "we got lucky"). Near-misses are free signal — the failure that didn't bite yet.
- **Track trends, not just incidents.** Watch **MTTD** (mean time to detect), **MTTM/MTTR** (mitigate/resolve), incident frequency by service, recurring causal themes. A rising MTTD points at observability gaps; a rising MTTR points at tooling/runbook gaps.
- **Proactive resilience.** Stop waiting for incidents to find weaknesses — go break things on purpose. Chaos engineering injects controlled failure to validate your mitigations before a real outage tests them. That's [07 — Chaos & Resilience Engineering](07_chaos_resilience_engineering.md).

---

## 11. Symptom / Cause / Fix

**"Everyone debugging, nobody coordinating."**
- *Symptom:* 15 people in the channel, three conflicting theories, no one knows who's doing what, mitigations collide.
- *Cause:* No Incident Commander; command and debugging not separated.
- *Fix:* Declare an IC immediately and *say it out loud*. The IC stops debugging, assigns Ops by name, and manages the swarm (§3, §5).

**"We found the root cause but it happened again."**
- *Symptom:* Same incident recurs months later; the old postmortem described it perfectly.
- *Cause:* Action items were never done — postmortem theater.
- *Fix:* SMART, owned, dated action items tracked against features; review aging items every incident meeting (§9).

**"People are afraid to declare incidents."**
- *Symptom:* Problems simmer for 30+ minutes before anyone declares; long detect→declare gaps in timelines.
- *Cause:* Declaring is treated as a big deal or a personal failure.
- *Fix:* Lower the bar; make declaring cheap and *praised*; downgrading is free, late escalation is not (§2, §4).

**"Blameful postmortem → people stopped being honest."**
- *Symptom:* Timelines get vague, key details missing, on-call sign-ups drop.
- *Cause:* Someone got blamed; the org learned that honesty is punished.
- *Fix:* Blameless by policy and by leadership behavior — chase the second story, assume good intent, fix the system not the person (§7).

---

> Next: [07 — Chaos & Resilience Engineering](07_chaos_resilience_engineering.md) — stop waiting for incidents to teach you. Inject failure deliberately, validate your mitigations and ICS muscle under controlled conditions, and turn the lessons of this chapter into game days and automated fault injection before prod runs the experiment for you.
