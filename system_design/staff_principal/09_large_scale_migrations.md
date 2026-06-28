# 09 — Leading Large-Scale Migrations

> **Audience:** Staff/Principal engineers. Leading a large migration is the **proving ground** for the title — it requires technical depth, multi-quarter execution, cross-team influence without authority, and the judgment to keep the business safe while the engine is rebuilt in flight.
>
> **The defining trait of migrations:** they ship **no new business feature**, take **months to years**, carry **real risk** (you're changing load-bearing systems), and require **dozens of teams to change code they didn't choose to change.** This is why they're hard, and why doing one well is a promotion narrative.

---

## 1. Why migrations are uniquely hard

| Hard because… | Consequence | Implication for the leader |
|---|---|---|
| **No new feature** | Hard to fund; competes with roadmap; teams deprioritize | You must sell the *cost of not migrating* (risk, cost, velocity tax) |
| **Long duration** | Spans reorgs, priority shifts, attrition; loses momentum | Sequence for early wins; build durable forcing functions |
| **High risk** | Touching production load-bearing systems | Incremental, reversible, well-instrumented — never big-bang |
| **Cross-team** | You depend on people you don't manage | Influence, carrots/sticks, make the new path the *easy* path |
| **The long tail** | The last 20% is 80% of the pain (obscure callers, edge cases) | Plan for it explicitly; forcing functions + deprecation deadlines |

> **The cardinal rule: no big-bang cutover.** The history of failed migrations is the history of "flip the switch on the weekend." Migrate **incrementally, reversibly, observably.**

---

## 2. The migration playbook (phases)

```
[1] ASSESS & INVENTORY  →  [2] GOALS & METRICS  →  [3] STOP THE BLEEDING
        →  [4] INCREMENTAL MIGRATE (strangler fig)  →  [5] VERIFY & CUTOVER
        →  [6] DEPRECATE & DECOMMISSION
   (throughout: COMMUNICATION + STAKEHOLDER MGMT + ROLLBACK READY)
```

### Phase 1 — Assess & inventory

You cannot migrate what you can't see. Build the complete inventory **first**.

- Enumerate every consumer/caller, dependency, data store, integration, cron, and obscure batch job.
- Classify: easy / medium / hard / "who even owns this?"
- Identify the **long tail** early — the unknown callers are what kill the "last 20%".
- Capture a **baseline**: current cost, latency, incident rate, the pain you're solving.

### Phase 2 — Set measurable goals

A migration without a number is a migration that never ends. Define **success metrics and a definition of done**:

> *Bad:* "Migrate to the new platform."
> *Good:* "100% of write traffic on the new datastore; p99 ≤ 50 ms; cost ≤ \$X/mo; old cluster decommissioned by Q3; zero data-loss incidents."

### Phase 3 — "Stop the bleeding" first

If the old system is actively growing the problem (more callers daily, more data on the legacy store), **first stop new usage** before migrating existing usage:
- Block new integrations against the old path (lint rule, policy gate, "old path is frozen").
- This caps the problem so the migrating denominator stops growing under you.

### Phase 4 — Incremental migration: the **strangler fig**

Named after the strangler fig vine that grows around a tree until it can stand alone. You **wrap** the old system with a façade/router that **incrementally redirects** functionality to the new system, piece by piece, until the old system is fully replaced and removed.

```
            ┌─────────────────────────┐
  traffic → │   Façade / Router /      │
            │   Proxy (the strangler)  │
            └──────┬────────────┬──────┘
                   │            │
        % routed → │            │ ← % still legacy (shrinks over time)
                   ▼            ▼
            ┌────────────┐  ┌────────────┐
            │ NEW system │  │ OLD system │
            └────────────┘  └────────────┘
```

- Move **one capability / one route / one tenant at a time**.
- Each slice is independently shippable and **independently reversible** (route back to old).
- The percentage on the new path is your progress metric.
- **Feature flags** gate every increment — ramp 1% → 5% → 25% → 50% → 100%, watching SLOs.

### Phase 5 — Data migration: dual-write + CDC + backfill + verify + cutover

(Detailed in §3 — the highest-risk part.)

### Phase 6 — Deprecate & decommission

**The migration isn't done until the old system is OFF.** A half-migrated system is the worst of both worlds: you pay to run both, and complexity doubles.

- Set a hard **deprecation deadline**, communicate it widely, enforce it.
- Verify zero traffic to old path (instrument it — don't trust assumptions).
- Decommission: turn it off, then delete. **Realizing the cost/complexity savings is the payoff** — capture it.

### Throughout — Communication & stakeholder management

- **Migration dashboard** visible to all (see §4) — single source of truth.
- Regular updates to leadership: % done, blockers, risks, the *date*.
- Per-team migration guides + office hours + a Slack channel.
- Name an executive sponsor early (you'll need air cover for the forcing functions).
- Always have a **rollback plan per increment**, tested, with a clear trigger.

---

## 3. Zero-downtime data migration pattern

The canonical, reversible pattern for moving data with no downtime and no data loss:

```
STEP 1  BACKFILL          Copy existing data old → new (batched, throttled,
                          idempotent). Runs in background, no user impact.

STEP 2  DUAL-WRITE        App writes to BOTH old (source of truth) and new.
        (+ CDC option)    New keeps up with live changes. (Or use CDC —
                          Change Data Capture from the old store's log —
                          instead of app-level dual-write.)

STEP 3  SHADOW / READ     Read from old (authoritative), ALSO read from new
        VERIFY            and compare. Log mismatches. Reconcile until the
                          divergence rate ≈ 0.

STEP 4  FLIP READS        Cut reads over to new (still dual-writing). New is
                          now serving; old is the safety net. Watch SLOs.

STEP 5  FLIP WRITES /     New becomes source of truth. Stop writing to old.
        CUTOVER

STEP 6  DECOMMISSION      Stop reading old; verify; delete old store.
```

| Step | Reversible? | Risk | Key safeguard |
|---|---|---|---|
| Backfill | Yes (just re-run) | Low | Idempotent, throttled to protect prod |
| Dual-write / CDC | Yes | Med (write amplification, consistency) | Make new-store write failures non-fatal at first; monitor lag |
| Shadow read + verify | Yes | Low | Compare and log mismatches; **don't cut over until divergence ≈ 0** |
| Flip reads | Yes (flag back) | Med | Feature-flag ramp 1%→100%; watch error budget |
| Flip writes | Harder | **High** | Point of (near) no return — verify exhaustively first |
| Decommission | No | Low (if verified) | Confirm zero traffic before delete |

> **Verification is the step everyone skips and everyone regrets.** Shadow reads with automated comparison are how you cut over with confidence instead of hope. Track the mismatch rate as a first-class metric.

---

## 4. Measuring progress — the migration dashboard

What gets measured gets migrated. Make progress **visible and undeniable**.

| Metric | Why | Example target |
|---|---|---|
| **% migrated** | Headline progress (by traffic, by tenant, by call sites) | 0 → 100% |
| **% traffic on new path** | Real-world progress, not just code | Ramp curve |
| **Remaining callers / call sites** | The long tail, by owning team | Burndown to 0 |
| **Data divergence rate** | Verification health | → ~0 before cutover |
| **Error budget / SLO on new path** | Safety | Within budget |
| **Cost of running both** | The bleeding you're paying until decommission | → 0 at decommission |
| **Projected completion date** | Accountability | Trends toward the deadline |

```
Migration: Legacy-DB → Sharded-DB        ▓▓▓▓▓▓▓▓▓▓░░░░  72%
 Tenants migrated      361 / 500
 Write traffic on new  68%      Read traffic on new  74%
 Data divergence       0.002%   ▼ trending to 0
 Teams remaining       9 (owners tagged)   ETA: 2026-09-15
 Dual-run cost         $14k/mo (ends at decommission)
```

> Pair `% migrated` with a **named owner for every remaining item.** "9 teams left" with names creates accountability; "28% remaining" is just a number.

---

## 5. The "last 20%" problem and forcing functions

The first 80% of callers migrate willingly (or you migrate them). The **last 20% won't move on their own** — they're obscure services, busy teams, "we'll get to it," or owners who left. This tail can take longer than everything before it.

**Forcing functions (escalating):**

1. **Make the new path easier** than the old (the strongest lever — see §6).
2. **Freeze the old path** — no new usage (lint/CI gate).
3. **Public dashboard** of laggards by team (social pressure).
4. **Hard deadline** with executive sponsorship behind it.
5. **Increasing friction on the old path:** deprecation warnings → throttling → scheduled brownouts (turn it off for 1 hr to flush out hidden callers) → off.
6. **Migrate it for them** — sometimes the fastest path is doing the work yourself rather than waiting.

> **Brownouts are a staff-level trick:** schedule short, announced outages of the old system. Hidden callers you never found in the inventory surface immediately (and loudly), and stragglers feel real urgency.

---

## 6. Org tactics — carrots, sticks, and making the right thing easy

You don't have authority over the teams you need. You have **influence + design**.

| Tactic | Type | Example |
|---|---|---|
| **Make the new path the easy path** | Carrot (best) | Migration is one config flag / codemod; new SDK is nicer; auto-PRs |
| **Do the migration for them** | Carrot | Embed/codemod their code so they just review a PR |
| **Better DX on new system** | Carrot | Faster, better docs, better tooling → teams *want* to move |
| **Embedding** | Influence | Sit with a key team for a sprint; unblock + create a reference success |
| **Deprecation deadline** | Stick | Hard date, exec-sponsored |
| **Freeze + friction** | Stick | Old path frozen; brownouts; eventual shutoff |
| **Visibility / leaderboard** | Social | Per-team burndown on a shared dashboard |

> **The golden rule of migrations:** *make the new path easier than the old path.* If migrating is harder than staying, you will fight forever. If the new SDK is faster and the migration is an automated codemod + one-line review, teams migrate themselves. **Invest in the on-ramp before you invest in the sticks.**

---

## 7. Migration-plan template (1-page)

```
MIGRATION PLAN: <from> → <to>
Owner / Sponsor:   <staff eng> / <exec sponsor>
Duration / Target: <start> → <target date>

1. WHY NOW (cost of NOT migrating)
   <risk / cost / velocity tax of the status quo — the business case>

2. SUCCESS METRICS (definition of done)
   - <100% traffic on new; p99 ≤ X; old system decommissioned; cost ≤ $Y>

3. INVENTORY & RISK
   - Consumers/callers: <N>, classified easy/med/hard, owners tagged
   - Long tail / unknown callers: <plan to discover — brownouts?>
   - Baseline metrics: <cost / latency / incident rate today>

4. APPROACH
   - Strangler façade at: <router/proxy location>
   - Increment unit: <route / tenant / capability>
   - Ramp plan: 1% → 5% → 25% → 50% → 100%, gated by flags + SLO

5. DATA MIGRATION (if applicable)
   - backfill → dual-write/CDC → shadow-read+verify → flip reads → flip writes → decommission
   - Verification: <divergence metric + threshold to cut over>

6. ROLLBACK
   - Per-increment trigger + procedure (flag back to old); tested <date>

7. STOP-THE-BLEEDING
   - <how new usage of old path is frozen>

8. DEPRECATION & DECOMMISSION
   - Deadline: <date> | Forcing functions: <freeze, brownout, shutoff>
   - Decommission checklist + cost savings to realize: <$/mo>

9. COMMUNICATION
   - Dashboard: <link> | Updates: <cadence> | Per-team guide: <link>

10. RISKS & MITIGATIONS
    <data loss, dual-write divergence, the long tail, attrition, scope creep>
```

---

## 8. Worked example — Monolith DB → Sharded DB (zero downtime)

**Context:** Single Postgres primary at 90% CPU, 8 TB, 500 tenants, growing 2×/yr. Cannot scale vertically further. Goal: shard by `tenant_id` across 16 shards.

| Phase | Action | Metric / safeguard |
|---|---|---|
| Inventory | Map all 500 tenants + every service writing to the DB; tag owners | 41 services, 6 with raw SQL (the hard tail) |
| Goals | "All tenants sharded; p99 ≤ 50 ms; primary CPU < 50%; zero data-loss; done Q3" | Defined DoD |
| Stop bleeding | New tenants provisioned **directly on shards** from day 1 | Denominator stops growing |
| Abstraction | Introduce a routing/data-access layer keyed on `tenant_id` (the strangler façade) | Single place to flip routing |
| Backfill | Copy tenants old → assigned shard, batched & throttled, idempotent | Prod CPU stays < 70% during backfill |
| Dual-write | Writes go to old + shard (via CDC from WAL or app-level) | Replication lag < 1 s monitored |
| Verify | Shadow-read from shard, compare to old, log mismatches | Divergence 0.01% → reconcile → ~0 |
| Flip reads | Per-tenant flag ramp; reads served from shards | Watch error budget per cohort |
| Flip writes | Shard becomes source of truth per tenant; stop dual-write | Exhaustive verify first (one-way-ish) |
| Long tail | 6 raw-SQL services + 9 stragglers: codemods + a 1-hr brownout of old primary | Hidden callers surfaced immediately |
| Decommission | Confirm zero traffic to old primary; snapshot; delete | Realize \$14K/mo dual-run savings |

**Outcome shape:** primary CPU 90% → ~35% per shard; headroom for ~3× growth; cost of the old monolith primary eliminated after decommission.

> Same playbook applies to **on-prem → cloud**: inventory → strangler façade (route % of traffic to cloud) → dual-run + data replication (backfill + CDC) → verify → ramp cutover by service/region → decommission the data center. The *pattern* is invariant; only the substrate changes.

---

## Anti-patterns

| Anti-pattern | Why it fails | Do instead |
|---|---|---|
| **Big-bang cutover** | One weekend, no rollback, prays | Incremental strangler + reversible increments |
| **No verification before cutover** | Silent data loss/corruption discovered later | Shadow reads + divergence metric → ~0 |
| **No decommission / "90% done forever"** | Pay for both systems; double complexity | Hard deadline + forcing functions; turn it OFF |
| **No inventory** | The long tail (unknown callers) ambushes you | Inventory first; brownouts to flush hidden callers |
| **No measurable goal** | Migration drifts indefinitely | Numeric DoD + dashboard + ETA |
| **Sticks before carrots** | Teams resist; you fight forever | Make the new path *easier* first |
| **Migrating onto a not-ready target** | You strand teams on a worse system | Reach feature/perf parity before ramping |
| **Letting old usage keep growing** | Denominator outruns your progress | Stop the bleeding first |

---

## Key Takeaways

1. **Migrations are the staff/principal proving ground** — long, risky, cross-team, no feature to show. Sell the *cost of not migrating*.
2. **Never big-bang.** Strangler-fig: wrap the old, redirect incrementally, keep every step reversible behind a flag.
3. **Set a numeric definition of done**, or it never ends. Pair it with a visible dashboard and an ETA.
4. **Stop the bleeding first** — freeze new usage of the old path so your denominator stops growing.
5. **Zero-downtime data migration = backfill → dual-write/CDC → shadow-read+verify → flip reads → flip writes → decommission.** Verification (divergence → ~0) is the step that lets you cut over with confidence.
6. **The last 20% is 80% of the pain.** Plan the long tail explicitly: forcing functions, deprecation deadlines, brownouts, and migrating it yourself.
7. **Make the new path easier than the old** — the single most effective org tactic. Carrots before sticks; sticks (freeze, brownout, shutoff) for the tail.
8. **It's not done until the old system is OFF.** Decommission and realize the savings — that's the payoff that justified the whole effort.
