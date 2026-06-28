# 04 — Code Review & Code Health

> **Audience:** Staff and principal engineers responsible for the review culture, code health, and long-term maintainability of large codebases. This chapter is opinionated: it assumes monorepo-scale velocity, thousands of engineers, and CI that gates merges. It tells you what to review, in what order, how to keep diffs small and reviews fast, what to automate, and how to manage technical debt and API deprecation without breaking the org.

---

## 1. Why code review exists (and what it is *not*)

Code review is the single most reliable lever for code quality that scales with people rather than tooling. But it is constantly misused. Get the purpose right first.

Code review **exists for**:

- **Correctness** — catch bugs, edge cases, race conditions, and security holes before they ship.
- **Design / architecture fit** — does this change belong here? Does it fit the system's seams?
- **Knowledge sharing** — at least two people now understand this code. The bus factor goes up.
- **Consistency** — the codebase reads as if written by one careful engineer.
- **Mentorship** — reviews are the highest-bandwidth teaching channel you have.

Code review is **NOT for**:

- **Gatekeeping** — review is collaborative, not a toll booth. The default posture is "how do we get this in," not "why should I let this in."
- **Style-policing** — if a human is arguing about tabs, brace placement, or import order, your tooling has failed. **Automate style entirely** (§4).

### The Google "readability" model

Google decouples "is this code correct and well-designed?" (any reviewer) from "is this idiomatic for this language?" (a **readability**-certified reviewer for that language). Readability is a per-language certification earned by writing clean CLs and getting feedback; a granted reviewer can approve the language-idiom dimension. The lesson for you: **separate substance from idiom**, name who owns each, and make idiom learnable and bounded — not an endless subjective gauntlet.

---

## 2. What to review — in priority order

Review top-down. If you find a correctness or design problem, you often don't need to comment on naming yet — the code may not survive. Spending your first pass on nits is the classic anti-pattern.

| Priority | Dimension | Question the reviewer asks |
|---|---|---|
| 1 | **Correctness / bugs** | Does it do what it claims? Edge cases, error paths, concurrency, security? |
| 2 | **Design / architecture fit** | Right layer? Right abstraction? Does it fit existing seams or fight them? |
| 3 | **Tests** | Are the right things tested at the right level? Would these tests catch a regression? See [02 — Testing Strategy at Scale](02_testing_strategy.md). |
| 4 | **Readability / maintainability** | Can the next engineer understand and safely change this in a year? |
| 5 | **Naming** | Do names reveal intent? Are they consistent with the domain? |
| 6 | **Style** | Formatting, layout — **and this should be automated, not reviewed by a human.** |

> **Rule of thumb:** the higher the priority, the more a human is irreplaceable. The lower, the more a machine should own it. If your humans spend their time at the bottom of this table, your tooling is the bug.

The reviewer's job is to approve a CL that **improves the overall code health of the system**, even if it isn't perfect. This is the key calibration (§3).

---

## 3. Review culture & norms

### 3.1 Small CLs/PRs — the single biggest lever

Big diffs get rubber-stamped. A 2,000-line PR gets "LGTM" because no human can hold it in their head; a 150-line PR gets a real review. **Smallness is the highest-leverage habit you can instill.**

- A CL should be **one self-contained, reviewable change**. Split refactors from behavior changes.
- Use stacked diffs / dependent PRs so a large feature lands as a sequence of small, individually reviewable steps.
- Big mechanical changes (rename, API migration) are *large-scale changes* — automate and split them; see [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md).

### 3.2 Fast review SLAs

Turnaround time is an org-wide multiplier: a slow reviewer blocks an author who blocks their dependents. Target: **respond within one business day** (not necessarily finish — at least pick it up). Treat review as an interrupt worth taking, not something that waits for a quiet afternoon.

### 3.3 How to give feedback

- **Ask, don't demand.** "What do you think about pulling this into a helper?" invites; "Pull this into a helper" commands. Reserve imperatives for correctness.
- **Label nits.** Prefix optional polish with `Nit:` — the author may resolve or ignore.
- **Send the CL in the direction of improvement, not perfection.** Approve net-positive changes; file follow-ups for the rest.
- **Explain the principle**, not just the fix — that's the mentorship dimension.

### 3.4 LGTM with comments

Approve and unblock while leaving small comments you trust the author to handle. This is the default for routine changes — it removes a round-trip and signals trust.

```
# Reviewer leaves:
Approved (LGTM)
Nit: rename `tmp` -> `pendingDeletes` for clarity.       # optional
Please add a test for the empty-input case before merge. # trusted to the author
```

### 3.5 Author responsiveness & disagreement

- **Authors** reply to every comment (resolve, push back, or "done"). Silence stalls reviews.
- **Disagreement escalation:** discuss in the thread → quick synchronous chat → if still stuck, escalate to a tech lead / the team's style arbiter / the relevant decision-maker. Never let a CL rot in a stalemate. Decisions favor what improves long-term code health, not who is more senior or more stubborn.

### 3.6 Async vs synchronous

Default to **async** review — it respects focus time and creates a written record. Switch to **synchronous** (call, pairing) when a thread exceeds ~3 round-trips or the disagreement is conceptual rather than concrete. Synchronous resolves fast; just write the conclusion back into the thread.

---

## 4. Automate the mechanical

Humans review substance; machines own everything deterministic. Every tool below runs as a **CI gate** (and ideally a pre-commit hook for the fast ones).

### 4.1 Formatters — end the style debate

```bash
# One formatter per language, zero config arguments, runs in CI as a gate.
gofmt -l .            # Go      — fails if any file is unformatted
black --check .       # Python
clang-format --dry-run --Werror $(git ls-files '*.cc' '*.h')   # C/C++
prettier --check .    # JS/TS/CSS/Markdown
```

There is exactly one correct format: whatever the formatter emits. Style discussions in review are now impossible by construction — that is the point.

### 4.2 Linters / static analysis

```bash
ruff check .          # Python lint (fast)
clang-tidy ...        # C++ static analysis
# Java: SpotBugs (bug patterns) + Error Prone (compile-time checks)
```

### 4.3 Type checkers & pre-commit

```yaml
# .pre-commit-config.yaml — fast checks before the commit even leaves the laptop
repos:
  - repo: local
    hooks:
      - id: format
        name: format
        entry: make fmt
        language: system
      - id: typecheck
        name: typecheck
        entry: mypy .          # or tsc --noEmit
        language: system
        pass_filenames: false
```

### 4.4 Pre-submit vs post-submit analysis

- **Pre-submit (gate):** fast, deterministic, low-false-positive checks block the merge — formatters, type errors, high-confidence linters.
- **Post-submit (advisory):** expensive or noisier analysis (deep static analysis, fuzzing, large security scans) runs after merge and files findings/tickets instead of blocking. **Never gate on a flaky or high-false-positive check** — engineers learn to ignore the gate, and then it protects nothing.

---

## 5. Ownership, certification & bots

- **CODEOWNERS / required reviewers:** path-based ownership auto-requests the right reviewers and enforces approval from a code owner before merge. This is how you scale review in a monorepo without a bottleneck team. See [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md).

```
# .github/CODEOWNERS
/services/billing/    @org/billing-team
/libs/auth/           @org/security @alice
*.proto               @org/api-council    # API changes get extra eyes (§8)
```

- **Readability certification:** a granted reviewer signs off the language-idiom dimension (§1); routes idiom questions to people who own them and makes the bar learnable.
- **Bots** automate the rest of the mechanical review surface:
  - **Size labelers** — tag `size/XL` to nudge authors toward splitting (§3.1).
  - **Coverage diff** — comment the per-PR coverage delta; flag drops.
  - **Automated nit comments** — a bot leaves the `Nit:` so a human doesn't have to.

---

## 6. Code health as a continuous discipline

Code health is not a project; it is a habit applied to every change.

- **The Boy Scout Rule:** leave the code a little better than you found it. Not a rewrite — one extracted function, one clarified name, one added test, in the diff you were already making.
- **Measure health, not LOC.** Lines of code is an anti-metric (it rewards verbosity and punishes deletion). Better signals: review turnaround, change-failure rate, time-to-onboard on a module, % of changes touching a hotspot, test flakiness. Deleting code is a *win*.
- **Readable code principles:** optimize for the reader, not the writer; reveal intent through names; keep functions short and single-purpose; make the common path obvious and the edge cases explicit; minimize surprise.
- **Complexity metrics, used cautiously:** cyclomatic complexity, file churn × complexity (hotspot analysis) point you at *where to look* — they are smoke detectors, not verdicts. Never gate a merge on a complexity number; you'll get gamed code, not simpler code.

---

## 7. Technical debt management

Debt is not inherently bad — it is borrowing against future velocity. The staff-engineer skill is **making it visible and deciding deliberately**.

### 7.1 The Fowler quadrant

| | **Prudent** | **Reckless** |
|---|---|---|
| **Deliberate** | "Ship now, refactor next sprint — we know the cost." *(Acceptable, if tracked.)* | "No time for design." *(The dangerous one.)* |
| **Inadvertent** | "Now we know how we should have done it." *(Learning; expected.)* | "What's layering?" *(Skill/knowledge gap — address via mentorship.)* |

The only universally bad cell is *deliberate + reckless*. *Deliberate + prudent* is normal engineering — **as long as it is tracked**.

### 7.2 Track debt as work items

Untracked debt is invisible debt, and invisible debt compounds silently. Every deliberate shortcut gets a ticket linked from a `// TODO(ticket)` in the code. No orphan TODOs.

### 7.3 The interest metaphor

Debt charges **interest**: every feature built on a shaky foundation costs more and ships slower. You pay down principal when the interest (recurring slowdown, bug rate, on-call pain) exceeds the cost of the fix. Some debt you **never** pay — code that is stable, isolated, and rarely touched accrues no interest. Don't refactor museums.

### 7.4 Budgeting fix-it time

- **20% / fix-it weeks:** carve out explicit, defended capacity for health work, or it never happens. A standing fraction of every sprint, or periodic fix-it weeks, both work.
- **Pay down vs live with:** pay down when the module is hot (high churn × high pain) and the fix is bounded. Live with it when it's cold, isolated, or the fix is open-ended.

### 7.5 Make debt visible to leadership

This is the staff/principal move. Translate debt into the language of risk, velocity, and dollars: "This subsystem causes ~30% of our P1s and adds two weeks to every feature touching it." A debt register tied to incident and velocity data turns "engineers want to refactor" into a business case. For framing influence and the RFC/design-review process behind larger remediations, see [../system_design/staff_principal/](../system_design/staff_principal/README.md).

---

## 8. API evolution & deprecation

Breaking an API breaks your callers — and at scale your callers are other teams. This is the most common gap in otherwise-strong review cultures. The governing rule: **never break a caller silently.**

### 8.1 Compatibility & semantic versioning

- **Backward compatible:** old callers keep working against the new version.
- **Forward compatible:** newer callers degrade gracefully against older servers (ignore unknown fields — protobuf does this well).
- **SemVer (`MAJOR.MINOR.PATCH`)** communicates intent, but its limits are real: a major bump doesn't migrate anyone, behavioral (non-signature) breaks slip through, and "just bump major" is a cop-out when thousands of callers can't move in lockstep. Prefer *not breaking* over *versioning the break*.

### 8.2 Expand–contract (parallel-change)

Never mutate an API in place. Add the new shape, migrate callers, then remove the old shape.

```diff
# 1. EXPAND — add the new alongside the old; both work.
  def get_user(id):            ...          # old, still supported
+ def get_user_by_uuid(uuid):  ...          # new

# 2. MIGRATE — move every caller to the new (automated; see ch. 01 LSC tooling).
- user = get_user(123)
+ user = get_user_by_uuid(uuid)

# 3. CONTRACT — only after zero callers remain on the old path.
- def get_user(id):           ...           # now safe to delete
```

### 8.3 Deprecation lifecycle

| Stage | What you do | Caller experience |
|---|---|---|
| **Announce** | Mark deprecated, document the replacement + timeline | Sees docs/annotation; nothing breaks |
| **Warn** | Emit deprecation warnings, log usage, add `Sunset` header | Warned at build/runtime; still works |
| **Migrate** | Drive callers over (ideally with automated LSC) | Moved for them where possible |
| **Remove** | Delete only after usage hits zero | No surprise — they were moved or warned |

```bash
# Signal the sunset date to HTTP callers (RFC 8594) — machine-readable, not a surprise.
Deprecation: true
Sunset: Sat, 31 Oct 2026 23:59:59 GMT
Link: <https://api.example.com/docs/migrate-v2>; rel="sunset"
```

```python
import warnings
def get_user(id):
    warnings.warn("get_user(id) is deprecated; use get_user_by_uuid(). "
                  "Removal: 2026-10-31.", DeprecationWarning, stacklevel=2)
    ...
```

**Support old + new during migration**, and instrument old-path usage so "is it safe to remove?" is a query, not a guess. Tie removals to the large-scale-change tooling in [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md) so *you* migrate callers rather than asking 40 teams to.

---

## 9. Symptom / Cause / Fix

**"PRs sit for 3 days, blocking everyone."**
- *Symptom:* authors idle waiting on review; work piles up in flight.
- *Cause:* no review SLA; review treated as low-priority background work.
- *Fix:* set a one-business-day pickup SLA (§3.2); track turnaround as a team metric; rotate a "review duty" owner; let CODEOWNERS spread the load so no one person bottlenecks.

**"A huge diff got rubber-stamped and a bug shipped."**
- *Symptom:* LGTM on a 1,500-line PR; defect found in prod days later.
- *Cause:* the diff was too big to review meaningfully (§3.1).
- *Fix:* enforce small CLs; stacked/dependent PRs; size-labeler bot; reviewers may decline oversized PRs and ask for a split.

**"We keep bikeshedding style in reviews."**
- *Symptom:* threads about formatting, imports, brace placement.
- *Cause:* style is being reviewed by humans (§4.1).
- *Fix:* adopt one formatter per language as a CI gate; ban style comments — point to the formatter; reserve human attention for priorities 1–4 (§2).

**"A deprecation broke 40 downstream teams."**
- *Symptom:* an API removal cascades into 40 broken builds and an incident.
- *Cause:* in-place break with no expand–contract, no warning window, no usage tracking (§8).
- *Fix:* expand–contract; announce→warn→migrate→remove with `Sunset` headers; instrument old-path usage and remove only at zero; migrate callers yourself via LSC tooling ([01](01_engineering_workflow_vcs.md)).

---

> Next: [05 — Observability & SLOs](05_observability_slos.md) — once code is reviewed, healthy, and shipped (see [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md)), how do you *know* it's behaving in production? SLIs, SLOs, error budgets, and the instrumentation that turns "it feels slow" into a number you can act on.
