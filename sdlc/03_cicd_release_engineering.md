# 03 — CI/CD & Release Engineering

> **Audience:** Staff/principal engineers and release owners who design the path to production for hundreds of services and thousands of deploys a day. This chapter is opinionated: build once, promote the same artifact, deploy progressively, and let golden signals — not humans — pull the rollback lever.

---

## 1. CI vs CD vs Continuous Deployment

Three terms, three commitments. People conflate them and then argue past each other.

| Term | What it means | What it requires |
|---|---|---|
| **Continuous Integration (CI)** | Every commit merges to trunk and is built + tested automatically, many times a day. | Trunk-based dev, fast tests, green builds (see [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md)). |
| **Continuous Delivery (CD)** | Every green build is *deployable* to prod at any time, on a button press. | Immutable artifacts, automated gates, a human approval step. |
| **Continuous Deployment** | Every green build is *automatically* deployed to prod. No human in the loop. | Everything CD needs **plus** progressive delivery + automated rollback + strong observability. |

Continuous *Delivery* keeps a human gate; continuous *Deployment* removes it. You earn the right to remove the human only after canary analysis and auto-rollback are trustworthy. Most orgs should target Delivery and let mature, well-instrumented services graduate to Deployment.

**The pipeline is the only path to production.** No SSH, no manual `kubectl apply`, no "I just patched it." If it isn't in the pipeline it didn't happen:

```
commit → build → test → package → deploy → verify
   │        │       │        │        │        └─ probes + canary analysis on golden signals
   │        │       │        │        └─ progressive rollout (canary → waves)
   │        │       │        └─ immutable, signed, versioned artifact
   │        │       └─ lint → unit → integration → e2e (fast-feedback order)
   │        └─ hermetic, reproducible, cached
   └─ trunk, small, reviewed
```

---

## 2. Pipeline Design

### 2.1 Fast-feedback ordering

Order stages by `cost × failure_probability`. Cheap, high-signal checks first so a typo fails in 30 seconds, not 30 minutes.

```yaml
# .ci/pipeline.yaml — stages run in order; jobs WITHIN a stage run in parallel
stages:
  - name: static          # ~30s   lint, format, typecheck, secret-scan
    fail_fast: true
  - name: unit            # ~2min  parallel-sharded across 20 workers
    fail_fast: true
  - name: build           # ~3min  hermetic build + remote cache
  - name: integration     # ~8min  spins ephemeral deps (db, queue)
  - name: e2e             # ~20min slow, flaky-prone — LAST
    required: false       # advisory until it's proven non-flaky (see ch.02)
```

> **WRONG:** Run the 25-minute E2E suite first, in serial, before lint. Engineers wait half an hour to learn they have an unused import.

> **RIGHT:** Lint + unit gate the merge in under 3 minutes. E2E runs in parallel and post-merge while it earns trust.

### 2.2 Required vs advisory gates

A **required gate** blocks the merge/deploy. An **advisory gate** reports but does not block. New or flaky checks start advisory; they become required only once they are reliable. A flaky *required* gate trains engineers to hit "re-run" and erodes trust in the whole pipeline — see [02 — Testing Strategy at Scale](02_testing_strategy.md) on flake quarantine.

### 2.3 Fail-fast & parallelism

- **Fail-fast** within a critical stage: kill remaining jobs the moment a required one fails — don't burn 200 CPU-minutes confirming a doomed build.
- **Parallelism / sharding**: split the unit suite by timing across N workers; wall-clock should stay flat as the suite grows.
- **Idempotent & reproducible builds**: re-running a stage with the same inputs yields the same result. Non-determinism (timestamps, random ordering, network during build) is a bug — it poisons caches and makes failures unrepeatable.

---

## 3. Build at Scale

### 3.1 Hermetic & reproducible

A **hermetic** build depends *only* on declared inputs — no ambient `$PATH`, no `apt-get` at build time, no clock. Same inputs → byte-identical output. This is the foundation for caching, supply-chain trust, and killing "works on my machine."

```python
# BUILD — Bazel: inputs are explicit; the toolchain is pinned, not ambient
cc_binary(
    name = "checkout",
    srcs = ["checkout.cc"],
    deps = ["//lib/payments", "@grpc//:grpc++"],
    # no network, no system compiler — the toolchain is part of the graph
)
```

### 3.2 Build graphs, remote caching & remote execution

Tools like **Bazel/Buck** model the repo as a DAG of targets keyed by a content hash of all inputs.

- **Remote cache:** if `hash(inputs)` was built before — by anyone, on any machine — download the output instead of rebuilding. A clean checkout builds in seconds.
- **Remote execution (RBE):** fan thousands of actions out to a build cluster; a 40-minute laptop build finishes in 90 seconds across 500 cores.
- **Incremental builds:** only targets whose transitive inputs changed get rebuilt.

> **Why reproducibility matters:** (1) **Cache correctness** — a non-hermetic input means the cache returns a stale/wrong artifact, the worst kind of bug. (2) **Supply chain** — independent rebuilds must match the signed artifact (see §8). (3) **Debugging** — a reproducible build reproduces the failure.

---

## 4. Artifacts: Build Once, Promote Everywhere

```bash
# RIGHT — build the artifact ONCE, tagged by immutable content/commit
docker build -t registry.corp/checkout:git-9f3c1a .
cosign sign registry.corp/checkout@sha256:abcd...        # sign the digest
# promote the SAME digest through environments — never rebuild
promote checkout@sha256:abcd... --to staging
promote checkout@sha256:abcd... --to prod-canary
promote checkout@sha256:abcd... --to prod
```

> **WRONG:** `docker build` in the staging job, then `docker build` again in the prod job. Now prod runs a *different* artifact than the one you tested — a different base image, a different transient dependency, a different bug. This is the §11 "rebuilt artifact behaves differently in prod" outage.

Rules:
- **Immutable + versioned:** tag by content digest or commit SHA, never `:latest`. An artifact is never mutated; a new build is a new artifact.
- **Registries:** containers (OCI), packages (npm/Maven/PyPI mirror) — internal, authenticated, retention-policied.
- **Promotion = the same bytes** moving across environments. Environments differ only by *config* injected at deploy time, never by rebuild.
- **Provenance** travels with the artifact (who/what/when/from-which-source — §8).

---

## 5. Progressive Delivery — the Heart of the Chapter

Don't replace 100% of fleet at once. Expose new code to a growing slice, watch the signals, and roll back automatically before most users notice.

### 5.1 Strategies

| Strategy | How | Rollback speed | Blast radius | Cost | Use when |
|---|---|---|---|---|---|
| **Rolling** | Replace instances in batches | Slow (roll back batch-by-batch) | Grows with each batch | Low (no extra capacity) | Default for stateless services |
| **Blue-green** | Stand up full new env, flip the LB | **Instant** (flip back) | All-or-nothing per flip | High (2× capacity briefly) | Risky releases needing instant abort |
| **Canary** | Route 1%→5%→25%→100%, **auto-analyze** golden signals | Fast (shift traffic back) | Tiny initially | Medium | The default for user-facing prod |
| **Shadow / dark** | Mirror real traffic to new version, **discard responses** | N/A (never serves users) | Zero user impact | Medium (duplicate compute) | Validating perf/correctness pre-release |
| **Ring / wave** | Internal → beta → region A → global, in waves | Stop the wave | Bounded per ring | Low | Org-wide / multi-region rollout |

### 5.2 Canary with automated analysis

The point of a canary is **automation**, not "a human stares at a dashboard." Compare canary vs baseline on the **golden signals** — latency, errors, traffic, saturation (see [05 — Observability & SLOs](05_observability_slos.md)) — and let the system decide.

```yaml
# canary.yaml — automated analysis; humans don't watch graphs
canary:
  steps:
    - setWeight: 5
    - analysis:                      # statistical compare vs baseline
        metrics:
          - name: error-rate
            failOn: "> baseline + 0.5%"
          - name: p99-latency
            failOn: "> baseline * 1.2"
        interval: 2m
    - setWeight: 25
    - analysis: { interval: 5m }
    - setWeight: 100
  rollbackOnFailure: true            # auto-abort, shift traffic back to stable
```

This is the control that would have *caught the §11 outage*: the canary error rate spikes at 5%, analysis fails, traffic shifts back — blast radius 5% for 2 minutes instead of a full-fleet incident.

---

## 6. Feature Flags — Decouple Deploy from Release

**Deploy** = code is running in prod (behind a flag). **Release** = users can see it. Separating them lets you deploy at noon on Tuesday and release behind a flag on Thursday, then dial back instantly without a redeploy.

| Flag type | Lifespan | Purpose | Owner |
|---|---|---|---|
| **Release** | Days–weeks | Hide in-progress work; enable on launch | Feature team |
| **Ops / kill-switch** | Permanent | Instantly disable a feature/dependency under load | SRE/on-call |
| **Experiment (A/B)** | Weeks | Measure variants against a metric | Product/DS |
| **Permission / entitlement** | Permanent | Gate features by plan/tenant/role | Product |

```python
if flags.enabled("new_pricing_engine", user=ctx.user, default=False):
    return new_pricing(ctx)   # dark-launched; rolled out 1% → 100% via config
return legacy_pricing(ctx)
```

- **Progressive rollout %:** ramp `new_pricing_engine` 1% → 10% → 50% → 100% by changing config, not by deploying.
- **Flags are the alternative to long-lived branches** (ties to [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md)): merge to trunk daily behind a dark flag instead of accumulating a 3-week merge-hell branch.
- **The flag-debt problem:** every release flag is temporary tech debt. Give it an owner and an expiry; a flag that has been 100%-on for 6 months is a stale `if` and a latent footgun. Track flag age and fail CI on flags past their kill-by date.

---

## 7. Deployment Safety

```yaml
deploy:
  readinessProbe:                 # gate traffic until the pod is truly ready
    httpGet: { path: /readyz, port: 8080 }
    initialDelaySeconds: 5
  bakeTime: 10m                   # let a version "soak" before next wave
  autoRollback:
    on: [readiness_fail, error_budget_burn > 2x, p99 > slo]
  freeze:
    when: "error_budget_remaining < 10%"   # error-budget-aware freeze (ch.05)
```

- **Health/readiness gates:** never send traffic to a pod that fails `/readyz`; a failing probe blocks the rollout.
- **Automated rollback triggers:** wire rollback to objective signals (probe failure, error-budget burn rate, SLO breach) — not to a human noticing.
- **Bake time:** hold each wave long enough to surface slow leaks (memory, connection exhaustion) before widening.
- **Error-budget-aware freezes:** when the SLO budget is spent, the pipeline *automatically* freezes non-critical deploys until the budget recovers.
- **The "deploy on Friday" debate:** the real question isn't the calendar — it's *confidence*. With canaries + auto-rollback + on-call coverage, Friday is fine. Without them, *no day* is safe. Ban Friday deploys only as a stopgap while you build the safety net. Honor deployment windows where downstream/regulatory constraints demand them.

---

## 8. Database & Schema Migrations

> Decoupling the schema change from the code deploy is the **#1 cause of deploy-coupled outages**. Code and schema deploy at different times and roll back independently, so they must be *mutually compatible at every step*.

Use **expand/contract** (a.k.a. parallel change):

```sql
-- WRONG: rename in one shot — old code (still running mid-rollout) breaks instantly
ALTER TABLE orders RENAME COLUMN amt TO amount_cents;

-- RIGHT: expand → migrate → contract, each step backward-compatible
-- 1. EXPAND: add the new column, nullable; deploy code that writes BOTH
ALTER TABLE orders ADD COLUMN amount_cents BIGINT NULL;
-- 2. BACKFILL online, in batches (no full-table lock)
-- 3. Deploy code that READS amount_cents; stop writing amt
-- 4. CONTRACT: drop amt only after no code references it
ALTER TABLE orders DROP COLUMN amt;
```

- **Backward-compatible only:** every migration must work with both the currently-running and the about-to-deploy code, because during a rolling deploy *both run at once*.
- **Online schema change:** for large tables use tooling (gh-ost, pt-online-schema-change, or native online DDL) that copies/shadows instead of taking a `LOCK`. A naive `ALTER` on a 500M-row table holds a metadata lock and stalls every query — the §11 "migration locked the table" outage.
- **Migration runs as its own pipeline stage**, separate from and *before* the code that depends on it. Never ship schema + dependent code in the same atomic deploy.

---

## 9. Supply-Chain Security in the Pipeline

Full coverage is in [08 — DevSecOps](08_devsecops_security_sdlc.md); here is the pipeline's share.

```bash
# Generate an SBOM, sign the artifact, attach provenance
syft registry.corp/checkout@sha256:abcd... -o spdx-json > sbom.json
cosign sign --yes registry.corp/checkout@sha256:abcd...      # keyless via Sigstore/OIDC
cosign attest --predicate sbom.json --type spdxjson registry.corp/checkout@sha256:abcd...
# Admission control: prod refuses any image without a valid signature + provenance
cosign verify --certificate-identity-regexp '.*@corp' registry.corp/checkout@sha256:abcd...
```

- **SBOM:** a manifest of every dependency in the artifact — answers "are we exposed to CVE-X?" in seconds, not days.
- **Signing (Sigstore/cosign):** sign the digest so prod admission control can reject unsigned or tampered images.
- **Provenance / attestation (SLSA):** cryptographic record of *how* the artifact was built. SLSA levels climb from "scripted build" (L1) to "hardened, hermetic, non-falsifiable provenance" (L3+).
- **Dependency pinning:** lockfiles with hashes; no floating `^1.2` ranges that pull a fresh transitive dep — and a fresh supply-chain risk — on every build.
- **Hermetic builds as a security property:** a build with no network can't be poisoned by a compromised mirror mid-build (§3 reproducibility pays off again).

---

## 10. DORA Metrics — the Scoreboard

You manage what you measure. The four DORA metrics measure the *delivery org*, not individuals.

| Metric | What it measures | Elite |
|---|---|---|
| **Deployment frequency** | How often you ship to prod | On-demand, multiple/day |
| **Lead time for changes** | commit → running in prod | < 1 hour |
| **Change failure rate** | % of deploys causing a prod failure | 0–15% |
| **MTTR** | Time to restore after a failure | < 1 hour |

The throughput pair (frequency, lead time) and the stability pair (change-fail rate, MTTR) **move together** in healthy orgs — small, frequent, progressively-delivered changes are *both* faster and safer. If someone trades stability for speed, the practices in this chapter are missing.

---

## 11. Symptom / Cause / Fix

**A deploy caused an outage a canary would have caught**
- **Symptom:** Full-fleet error spike minutes after a 100%-at-once rollout; rushed manual rollback.
- **Cause:** No progressive delivery — the change hit every user simultaneously with no automated gate.
- **Fix:** Canary with automated golden-signal analysis (§5.2) and `rollbackOnFailure`. Blast radius drops from 100% to ~5% for ~2 minutes.

**A rebuilt artifact behaves differently in prod**
- **Symptom:** Passed in staging, breaks in prod with "it's the same code!"
- **Cause:** The prod job *rebuilt* the artifact — different base image / transitive dep than staging tested.
- **Fix:** Build once, sign the digest, **promote the same bytes** (§4). Config differs per env; the artifact never does.

**A schema migration locked the table**
- **Symptom:** All queries stall; site-wide latency/timeouts during a migration.
- **Cause:** A blocking `ALTER`/rename on a huge table shipped atomically with dependent code.
- **Fix:** Expand/contract + online schema change, run as a separate pre-deploy stage (§8). Every step backward-compatible.

---

## 12. See Also

- [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md) — trunk-based dev; flags vs long branches.
- [02 — Testing Strategy at Scale](02_testing_strategy.md) — what gates the pipeline; flake quarantine.
- [05 — Observability & SLOs](05_observability_slos.md) — golden signals for canary analysis; error budgets.
- [08 — DevSecOps](08_devsecops_security_sdlc.md) — full supply-chain & security coverage.
- [../modern_os/linux/16_fleet_config_management.md](../modern_os/linux/16_fleet_config_management.md) — fleet rollout mechanics underneath the strategies above.
- [../os_net/enterprise_scenarios/](../os_net/enterprise_scenarios/README.md) — what a bad deploy actually does to a production estate.

---

> Next: [04 — Code Review & Code Health](04_code_review_code_health.md) — the human gate in the pipeline: how review catches what tests can't, and how to keep a large codebase healthy without slowing the path to production.
