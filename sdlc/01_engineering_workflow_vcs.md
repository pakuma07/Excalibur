# 01 — Engineering Workflow & Version Control at Scale

> **Audience:** Staff/principal engineers responsible for the health of a shared codebase touched by hundreds-to-thousands of engineers. At this scale, version control is not a tooling detail — it is the substrate that determines how fast the org can move *without* the codebase rotting. The default failure mode is not "we shipped a bug"; it is "integration debt compounds until main is permanently semi-broken and nobody trusts a green build." This chapter is opinionated: trunk-based development, merge queues, and code ownership are the load-bearing choices, and the costs of getting them wrong scale superlinearly.

---

## 1. The goal: velocity *and* stability for 10,000 engineers

The naive framing is "velocity vs stability — pick a point on the curve." At scale this is wrong. A red main blocks *every* engineer, so instability directly destroys velocity. The real objective:

- **Main is always releasable.** A clean checkout of `main` builds, passes tests, and could ship. This is an invariant, not an aspiration.
- **Integration is continuous, not episodic.** Code merges in small increments many times/day, so divergence between any two engineers' work is measured in hours, not weeks.
- **The cost of a change is bounded by the change, not the codebase size.** Build, test, and review must stay sub-linear via caching, sharding, and ownership scoping.

Everything below is in service of these three invariants.

---

## 2. Version control model: trunk-based development wins

**Trunk-based development (TBD)** is the FAANG default: every engineer commits to (or merges short-lived branches into) a single `main` trunk, multiple times per day. Branches live hours, not weeks.

| Dimension | Trunk-based | GitFlow / long-lived feature branches |
|---|---|---|
| Branch lifetime | Hours–2 days | Days–weeks |
| Integration frequency | Many/day | At "merge time" (rare, painful) |
| Merge conflicts | Small, frequent, trivial | Large, rare, catastrophic |
| Releasable main | Always | Only on `release/*` branches |
| Incomplete work | Hidden behind feature flags | Hidden on a branch (rots) |
| Scales to 1000s of engineers | Yes | No (combinatorial merge hell) |

**Why long-lived branches fail at scale:** a 2-week-old branch has diverged from a trunk that received thousands of commits. The merge is no longer a textual conflict resolution — it is a *semantic re-integration* of refactors, renamed APIs, and moved files. This is **integration debt**: deferred, it accrues interest.

```bash
# RIGHT: trunk-based loop
git switch -c pk/add-rate-limiter main   # short-lived branch off fresh main
# ... small change, ~1 reviewable unit ...
git push -u origin pk/add-rate-limiter   # open PR, merge within hours via queue
git switch main && git pull --ff-only    # delete the branch immediately after merge

# WRONG: the slow-bleed branch
git switch -c feature/big-rewrite        # lives 3 weeks, 4000 commits land on main
git merge main                           # daily "keep up" merges = noise + still rots
```

**Releasing from a trunk:** you don't ship `main` directly to prod. You cut **release branches** (a.k.a. *release trains*) at a cadence — `release/2026.06`. Fixes land on `main` first, then are **cherry-picked** back to the release branch (never the reverse). This keeps the fix in the trunk permanently and the release branch minimal.

```bash
# Hotfix flow: fix forward on main, then cherry-pick to the live train
git switch main && git commit -m "fix: clamp retry backoff (sev2)"
git switch release/2026.06
git cherry-pick -x <sha>          # -x records the source SHA in the message
```

> Decoupling *deploy* from *release* — so incomplete work can live on `main` safely — is done with **feature flags**, covered in depth in [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md). Use flags, not branches, to hide unfinished features.

---

## 3. Monorepo vs polyrepo

A **monorepo** is one repository for many projects with a unified history and tooling. Google, Meta, and (much of) Microsoft run enormous monorepos. This is a deliberate trade, not laziness.

| | Monorepo | Polyrepo |
|---|---|---|
| Cross-project atomic change | One commit, one review | N PRs, N deploys, version skew |
| Tooling | Unified, one source of truth | Duplicated/drifting per repo |
| Code visibility & reuse | Everything discoverable | Siloed, copy-paste proliferates |
| Dependency management | One version of everything ("live at HEAD") | Diamond-dependency / version hell |
| Build/CI scale | Requires heavy investment | Naturally bounded per repo |
| Checkout/clone cost | Huge — needs VFS/sparse | Trivial |
| Access control | Coarser (path-based) | Repo-level boundaries |
| Blast radius of bad tooling | Org-wide | Per repo |

**The killer monorepo feature is the atomic cross-cutting change:** rename an API and update all 900 callers in *one* commit that is either fully applied or not at all. In polyrepo, that same change is a multi-week, multi-PR migration with intermediate broken states.

**How Google/Meta make it work** — a monorepo is unusable without three things:
1. **A scalable build system** (Bazel, Buck2) with content-addressed, remotely-cached, hermetic builds. You build *only* what your change affects, and you reuse everyone else's cached artifacts.
2. **Sparse checkout / virtual filesystem.** You never have all 100M files locally. Google's `srcfs`/Piper, Meta's EdenFS, and Microsoft's GVFS/Scalar lazily materialize files on access. With Git: `git sparse-checkout set <dirs>` plus partial clone (`--filter=blob:none`).
3. **Path-scoped code ownership** (see §5) so review and access don't require global authority.

```bash
# Cone-mode sparse checkout of a giant monorepo
git clone --filter=blob:none --no-checkout git@host:monorepo.git
cd monorepo
git sparse-checkout init --cone
git sparse-checkout set services/payments libs/common   # only these trees hydrate
git checkout main
```

**When polyrepo wins:** strong org/security boundaries (separate companies, regulated isolation), genuinely independent release cadences with stable public API contracts (e.g., open-source libraries you version and publish), or when you simply cannot fund the build/VFS investment. For a 50-engineer startup, polyrepo is fine. The monorepo tax is only worth paying when atomicity and unified tooling outweigh build-scale engineering cost.

---

## 4. Merge queues & the not-rocket-science rule

The classic race: two PRs are each green against `main`, but their *combination* breaks `main` (semantic conflict CI never saw). Whoever merges second poisons the trunk.

**The not-rocket-science rule (NRSR):** *never merge a change until you have tested the exact state that will result from the merge.* A **merge queue** enforces this:

1. PR is approved and enqueued.
2. The queue creates the *prospective post-merge commit* (PR rebased onto current `main`).
3. CI runs against that prospective state.
4. Only if green does it fast-forward `main`. The next PR re-bases onto the new `main` and repeats.

This makes "green on branch, broken on main" structurally impossible.

```yaml
# GitHub merge-queue gate (.github/workflows/merge-queue.yml)
on:
  merge_group:            # fires on the queue's prospective merge commit
jobs:
  required-checks:
    runs-on: ci-fleet
    steps:
      - uses: actions/checkout@v4
      - run: bazel test //... --config=ci   # validate the *post-merge* state
```

Serial queues are correct but slow (throughput = 1 / CI-duration). Production queues scale with:
- **Batching:** test N enqueued PRs together as one batch. If green, land all N. On failure, **bisect** the batch to evict the culprit and re-batch the rest.
- **Speculative execution:** optimistically build the likely-success futures (assume PR #1 passes and start testing #1+#2 in parallel) so the queue isn't idle waiting on each result. Meta, GitHub, and Aviator/Mergify queues all do this.

---

## 5. Code ownership & review gating

Ownership decouples "anyone can propose a change anywhere" from "the right people must approve it." Without it, review either bottlenecks on a few humans or degenerates into rubber-stamping.

- **CODEOWNERS / OWNERS files:** path globs map directories to required reviewers. A PR touching `services/payments/**` *cannot merge* without a payments owner's approval — enforced by branch protection.

```
# .github/CODEOWNERS  (last matching pattern wins)
*                       @org/eng-leads
/services/payments/     @org/payments-team
/libs/crypto/           @org/security @org/crypto-owners
*.bzl                   @org/build-infra
```

Google's `OWNERS` files nest hierarchically down the tree; the closest file governs, and approval can come from any listed owner up the chain.

- **Readability / certification systems:** Google gates *language-level* quality separately from domain ownership. A change in Go also needs sign-off from someone with **Go readability** until the author earns it themselves. This scales code-health standards without a central style police. Treat it as a per-language quality gate orthogonal to CODEOWNERS.

Review mechanics, comment etiquette, and code-health metrics live in [04 — Code Review & Code Health](04_code_review_code_health.md).

---

## 6. Large-scale changes (LSC)

An **LSC** is a single logical change too big to review or land as one PR — e.g., migrating an API used in 40,000 files. You do *not* hand-edit. The workflow:

1. **Author a codemod**, not a diff. Use AST-based transformers: [Rector](https://getrector.com) (PHP), [OpenRewrite](https://docs.openrewrite.org) (Java/JVM), [jscodeshift](https://github.com/facebook/jscodeshift) (JS/TS), `clang-tidy`/ClangMR (C++), `gofmt -r`/`go fix` (Go). AST transforms survive formatting and don't false-match strings/comments the way `sed` does.

```bash
# RIGHT: AST codemod — understands scope, types, imports
jscodeshift -t codemods/rename-getUser.js --extensions=ts,tsx src/

# WRONG: regex over millions of files — matches comments, strings, breaks on wrap
grep -rl 'getUser(' . | xargs sed -i 's/getUser(/fetchUser(/g'
```

2. **Shard + auto-land.** A tool (Google's Rosie, Meta's Codemod, or homegrown) splits the global diff into thousands of small per-owner PRs, routes each to its CODEOWNERS, and **auto-lands** each once CI is green and an owner approves. The LSC author never babysits 4,000 PRs individually.

3. **Make it backward-compatible and multi-phase.** Never break callers in one shot. Use **expand → migrate → contract** (add-migrate-remove):
   - **Add:** introduce the new API alongside the old; old delegates to new. Nothing breaks.
   - **Migrate:** codemod all callers from old → new (sharded LSC). Mark old `@deprecated`; optionally add a lint/CI guard to block *new* uses.
   - **Remove:** once usages hit zero, delete the old API in a final small LSC.

This sequence is the only safe way to deprecate an API across an entire monorepo, because at every intermediate commit `main` is fully buildable.

> An LSC of any consequence ships with a one-page design/RFC first. The design-doc and RFC process is owned by [../system_design/staff_principal/](../system_design/staff_principal/README.md) — don't duplicate it here; link your codemod plan to that doc.

---

## 7. Branch protection, history, and commit hygiene

**Branch protection on `main`** (non-negotiable):
- Require PR + required reviews (incl. CODEOWNERS).
- Require status checks via the **merge queue** (§4).
- Disallow force-push and deletion.
- Require **signed commits** (`git commit -S`, or sigstore `gitsign`) so authorship is verifiable in regulated/supply-chain-sensitive repos.

**Linear vs merge history:** prefer a **linear history** (squash-merge or rebase-merge, fast-forward only). It makes `git bisect`, `git revert`, and blame trivial — one commit = one landed change. Merge commits create a tangled DAG that is painful to bisect across. The trade: you lose intra-PR commit granularity (acceptable — the PR is the unit).

**Commit hygiene:**
- **Atomic commits:** one logical change per commit. Don't bundle a refactor with a behavior change — reviewers can't separate them and a revert takes out both.
- **The reviewable unit:** target ~200–400 lines of diff. Large PRs get worse reviews (reviewers skim) and block longer. Split aggressively; stack PRs if needed.
- **Good messages:** imperative subject ≤ 50 chars, blank line, *why* in the body, link the issue/ticket. The body is the durable record future archaeologists read.

```
fix: clamp exponential backoff to 30s ceiling

Unbounded backoff let a retrying client sleep 17 min after
6 failures, masking the outage in dashboards. Cap at 30s.

Refs: INC-4821
```

---

## 8. Symptom / Cause / Fix

**"Integration hell" — merges are dreaded, multi-hour ordeals.**
- *Cause:* long-lived branches; integration deferred to the end. Divergence accumulated as integration debt.
- *Fix:* trunk-based development (§2). Branches < 1 day. Hide incomplete work behind feature flags, not branches. Merge small and often.

**"Main is red" — the trunk is broken and everyone's blocked.**
- *Cause:* PRs tested only against their own branch state, not the post-merge state; two independently-green PRs collide.
- *Fix:* a merge queue enforcing the not-rocket-science rule (§4). No direct pushes to `main`; all checks run on the prospective merge commit.

**"The 2-week-old branch won't merge."**
- *Cause:* the branch diverged from a fast-moving trunk; conflicts are now semantic, not textual.
- *Fix:* don't get here — keep branches short. To recover: rebase frequently, split the branch into already-landable atomic pieces, land the safe parts immediately behind a flag, and stop treating the branch as a holding pen.

---

> Next: [02 — Testing Strategy at Scale](02_testing_strategy.md) — how a healthy trunk stays green: the test pyramid under load, hermetic/flaky-test management, test selection & sharding so CI cost stays sub-linear, and why your merge queue is only as trustworthy as the tests it gates on.
