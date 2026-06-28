# 04 — Rebase, Cherry-pick & Rewriting History

> **Audience:** You can branch and merge (see [03 — Branching & Merging](03_branching_merging.md)) and now you keep hearing "just rebase it" and "don't force-push that." This chapter takes you from scratch to confidently *rewriting history* — moving commits, squashing a messy branch, copying a single fix between branches, and surgically scrubbing a leaked secret from every commit. It also teaches you the one rule that keeps this from blowing up your team, and the safety net (`reflog`) that makes the whole thing reversible.

---

## 1. What rebase actually does

Merging *joins* two histories with a new merge commit. Rebasing **replays** your commits — one by one — on top of a new base, as if you had started your work from there.

The key consequence: **rebase creates brand-new commits with brand-new SHAs.** The original commits are not moved; they are *copied* (new author/committer date, new parent, new hash) and the old ones are abandoned.

```bash
# You branched off main, then main moved ahead.
git switch feature
git rebase main          # replay feature's commits on top of current main

# Equivalent explicit form:
git rebase --onto main <old-base> feature
```

```mermaid
gitGraph
    commit id: "A"
    commit id: "B"
    branch feature
    commit id: "C"
    commit id: "D"
    checkout main
    commit id: "E"
    commit id: "F"
```

After `git rebase main`, `C` and `D` are re-created as `C'` and `D'` on top of `F`:

```mermaid
gitGraph
    commit id: "A"
    commit id: "B"
    commit id: "E"
    commit id: "F"
    branch feature
    commit id: "C'"
    commit id: "D'"
```

The history is now **linear** — `main` is a direct ancestor of `feature`, so the eventual merge is a trivial fast-forward with no merge commit cluttering the log.

---

## 2. Rebase vs merge — the big debate

Both integrate `main` into your branch. They differ in *what the history looks like afterward*.

| Aspect | `git merge main` | `git rebase main` |
|---|---|---|
| History shape | Preserves true, branching history | Rewrites into a clean straight line |
| New commits | One merge commit | New copies of *every* replayed commit (new SHAs) |
| `git log` readability | Shows when/how branches diverged | Reads like one continuous story |
| `git bisect` / blame | Accurate but noisier | Clean, but loses real chronology |
| Conflicts | Resolved once, in the merge commit | May resolve per-commit during replay |
| Safe on shared branches? | Yes | **No** — see the Golden Rule (§4) |
| Reversibility | Easy (commits unchanged) | Needs reflog if it goes wrong |

**There is no universally correct answer — it is a team convention.** Common conventions:

- **Rebase your feature branch onto `main` before opening a PR**, then merge (or squash-merge) — gives a clean line *and* a clear integration point. Most popular.
- **Never rebase, always merge** — preserves exactly what happened; favored where audit trails matter.
- **Rebase locally, merge publicly** — rewrite freely while the work is yours; once shared, only merge.

Decide as a team and put it in CONTRIBUTING. The mechanics below work regardless of which you pick.

---

## 3. Interactive rebase — cleaning up before review

`git rebase -i` lets you edit, reorder, combine, and drop commits in a range. This is how you turn a messy "wip", "fix typo", "actually fix it" branch into a small set of coherent commits a reviewer can read.

```bash
# Open the todo editor for the last 4 commits on this branch:
git rebase -i HEAD~4

# Or everything since you diverged from main:
git rebase -i main
```

Git opens a **todo list** — top is oldest, bottom is newest. You change the verb in front of each commit:

```bash
pick   a1b2c3d  Add login form
reword e4f5g6h  Add validation        # edit this commit's message
edit   i7j8k9l  Wire up API call      # stop here to amend the snapshot
squash m0n1o2p  fix validation bug     # fold INTO previous, combine messages
fixup  q3r4s5t  oops typo              # fold INTO previous, DISCARD this message
drop   u6v7w8x  debug console.log      # delete this commit entirely

# Reorder by moving lines up/down. Verbs available:
# p, pick   = keep as-is
# r, reword = keep changes, edit message
# e, edit   = pause to amend content (split, add files)
# s, squash = merge into previous commit, keep both messages
# f, fixup  = merge into previous commit, drop this message
# d, drop   = remove commit
# Save & close the editor to run the plan.
```

If you chose `edit`, the rebase stops at that commit. Make your changes, then:

```bash
git add -p
git commit --amend          # or `git rebase --edit-todo` to revise the plan
git rebase --continue
```

### `--autosquash` with `commit --fixup`

Mark fixups as you go, then let Git arrange the todo list automatically:

```bash
# While working, target a fix at an earlier commit by SHA:
git commit --fixup a1b2c3d           # message becomes "fixup! Add login form"

# Later, reorder + mark them automatically:
git rebase -i --autosquash main
# The "fixup!" commit is moved directly under a1b2c3d and pre-set to `fixup`.
```

Set `git config --global rebase.autosquash true` to make `-i` always autosquash.

---

## 4. The Golden Rule of rebasing

> **Never rebase (or otherwise rewrite) commits that other people have already pulled.**

Why it is sacred: rebasing replaces commits with *new SHAs* (§1). If a teammate already has the originals, their history and your rewritten history now share no common commits in that range. When they pull, Git sees two divergent lines containing "the same" work twice — their `git log` "explodes" with duplicated commits and ugly merge conflicts, and someone has to manually untangle it.

**Rewriting is safe when the commits are private:**

- ✅ Commits you have **not pushed** anywhere.
- ✅ Your own **un-shared** feature branch that no one else has based work on.
- ✅ A branch you've pushed but agreed *with the team* that you alone own (you'll still need `--force-with-lease`, see [05 — Remotes & Collaboration](05_remotes_collaboration.md)).

**Rewriting is dangerous when:**

- ❌ The branch is `main`/`develop` or any shared integration branch.
- ❌ Anyone else has pulled or branched from those commits.

When in doubt, don't rewrite — merge instead.

---

## 5. Cherry-pick — copy one commit elsewhere

`git cherry-pick` applies the *change introduced by* a specific commit onto your current branch, creating a new commit (new SHA) with the same diff. The classic use: a bug is fixed on `main` and you need that exact fix on a `release` branch (a **backport** / hotfix), without dragging along everything else.

```bash
git switch release-1.x
git cherry-pick a1b2c3d                 # apply that one commit here

git cherry-pick a1b2c3d e4f5g6h         # several specific commits
git cherry-pick a1b2c3d..e4f5g6h        # a range (EXCLUSIVE of a1b2c3d)
git cherry-pick a1b2c3d^..e4f5g6h       # range INCLUSIVE of a1b2c3d

git cherry-pick -x a1b2c3d              # append "(cherry picked from commit …)"
                                        #   to the message — great for backport traceability
```

```mermaid
gitGraph
    commit id: "A"
    branch hotfix
    commit id: "FIX"
    checkout main
    commit id: "B"
    cherry-pick id: "FIX"
    commit id: "C"
```

**Conflict handling** works like a mini-merge:

```bash
git cherry-pick a1b2c3d
# CONFLICT — fix the files, then:
git add <files>
git cherry-pick --continue              # finish
# or
git cherry-pick --abort                 # bail, restore pre-pick state
git cherry-pick --skip                  # skip this commit, continue the rest
```

---

## 6. History-rewriting tools

### `git commit --amend` (recap)

Rewrites the *most recent* commit — fix the message or fold in forgotten changes. It produces a new SHA, so the Golden Rule applies if it's already pushed.

```bash
git add forgotten-file.js
git commit --amend --no-edit            # same message, new snapshot
git commit --amend -m "Better message"  # rewrite the message
```

### Interactive rebase

The general-purpose tool for rewriting a *range* of recent commits — covered in §3.

### Bulk rewriting with `git filter-repo`

To rewrite **all** of history — every commit, every branch — use [`git filter-repo`](https://github.com/newren/git-filter-repo), the **modern replacement for the slow, footgun-prone `git filter-branch`** (which the Git project itself now discourages). Install it (`pip install git-filter-repo`) and run it on a *fresh clone*.

```bash
# Purge a file from EVERY commit in ALL history (e.g. a committed secret):
git filter-repo --path config/secrets.yml --invert-paths

# Purge a large binary that bloated the repo:
git filter-repo --path assets/huge.psd --invert-paths

# Replace a leaked string everywhere it ever appeared:
git filter-repo --replace-text <(echo 'AKIAIOSFODNN7EXAMPLE==>REDACTED')
```

This rewrites the SHA of every affected commit — the ultimate Golden-Rule violation, so it requires a coordinated force-push and every collaborator must re-clone.

> ⚠️ **Security note:** removing a secret from history does **not** un-leak it. Anyone who cloned still has it, and it may be cached on the remote host. You must **rotate the secret** (revoke and reissue the key/token). Removing the file only stops *future* exposure. See [07 — Advanced Git Internals & Power Tools](07_advanced_internals_power_tools.md) for the full `filter-repo` toolkit.

---

## 7. The reflog — your safety net

Every time `HEAD` moves — commit, checkout, reset, rebase, merge — Git records it in the **reflog**. So even when a rebase or hard reset "loses" commits (they're no longer reachable from any branch), the old SHAs are still written down and recoverable for ~90 days.

This is what makes rewriting safe to *experiment* with: you can almost always get back.

```bash
git reflog                              # list where HEAD has been, newest first
# a1b2c3d HEAD@{0}: rebase (finish): returning to refs/heads/feature
# 9f8e7d6 HEAD@{1}: rebase (pick): Wire up API
# 1122334 HEAD@{2}: checkout: moving from main to feature   <-- pre-rebase state!

# Recover: point your branch back at the good pre-rebase commit.
git reset --hard 1122334                # or HEAD@{2}

# Prefer to inspect first without moving your branch:
git switch -c rescue 1122334
```

See [06 — Undoing & Recovery](06_undoing_recovery.md) for the complete recovery playbook.

---

## 8. Symptom / Cause / Fix

**Symptom:** I rebased a shared branch and now my teammates' history "exploded" with duplicate commits and conflicts.
- **Cause:** You rewrote commits others had already pulled — their originals and your new SHAs diverged (Golden Rule, §4).
- **Fix:** Stop. Coordinate: ideally everyone re-syncs to the rewritten branch (`git fetch` then `git reset --hard origin/<branch>`) after backing up local work. Going forward, only rewrite private commits; integrate shared branches with `merge`.

**Symptom:** I lost commits after `git reset --hard` or a bad rebase.
- **Cause:** The commits became unreachable from any branch, but were not deleted.
- **Fix:** `git reflog`, find the SHA from *before* the bad operation, then `git reset --hard <reflog-sha>` (or branch off it). See §7.

**Symptom:** My force-push clobbered a teammate's push that landed while I was working.
- **Cause:** Plain `git push --force` overwrites the remote unconditionally, including commits you never saw.
- **Fix:** Always use `git push --force-with-lease` — it refuses the push if the remote moved since your last fetch. Details in [05 — Remotes & Collaboration](05_remotes_collaboration.md).

**Symptom:** A password/API key was committed and pushed; I need it gone from all history.
- **Cause:** The secret lives in the snapshot of every commit since it was added.
- **Fix:** **Rotate the secret first** (revoke + reissue). Then `git filter-repo --path <file> --invert-paths` (or `--replace-text`) on a fresh clone, force-push, and have everyone re-clone (§6).

---

> Next: [05 — Remotes & Collaboration](05_remotes_collaboration.md) — pushing, pulling, tracking branches, and how to rewrite shared work *safely* with `--force-with-lease` instead of detonating your teammates' repos.
