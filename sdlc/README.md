# SDLC & Engineering Excellence 🛠️🚦

> **Audience:** staff/principal engineers and the teams they lead. The language books
> teach you to *write* the code; `os_net/` and `system_design/` teach you what runs
> *beneath* and *above* it. This folder is the **process spine** that turns code into
> a reliable service and a team of engineers into a delivery org that ships fast
> *without* breaking things. It is the part of the principal job that isn't an
> algorithm — testing strategy, the path to production, review culture, observability,
> incident response, resilience, and security — woven through every stage of the
> software development lifecycle.

A principal is measured less by the code they write than by the **systems and habits
they leave behind**: the test suite that lets a junior refactor fearlessly, the
pipeline that makes a bad deploy a non-event, the SLO that turns "is it down?" into a
number, the blameless postmortem that makes an outage a lesson instead of a witch
hunt. That is what this folder is about. The technical knowledge elsewhere in this
repo is necessary but not sufficient; **this is the other half of the job.**

---

## 📚 Chapters

| # | Doc | What it covers |
|---|-----|----------------|
| 01 | [Engineering Workflow & Version Control](01_engineering_workflow_vcs.md) | trunk-based dev vs GitFlow, monorepo vs polyrepo, merge queues, **large-scale changes** (codemods), CODEOWNERS, branch protection, commit hygiene |
| 02 | [Testing Strategy at Scale](02_testing_strategy.md) | pyramid vs trophy, unit/integration/**contract**/E2E, test doubles & over-mocking, **flaky-test** policy, hermetic tests, **mutation testing**, property-based, perf/load, test infra |
| 03 | [CI/CD & Release Engineering](03_cicd_release_engineering.md) | pipeline design, **hermetic/reproducible builds** & remote caching, immutable artifacts, **progressive delivery** (canary/blue-green/rolling), **feature flags**, schema migrations, SLSA/SBOM/signing, DORA |
| 04 | [Code Review & Code Health](04_code_review_code_health.md) | what to review (in priority order), small-CL culture, automating style (formatters/linters as gates), **tech-debt** management, **API deprecation/evolution** (expand-contract) |
| 05 | [Observability & SLO-Driven Operations](05_observability_slos.md) | metrics/logs/**traces** (OpenTelemetry), golden signals/RED/USE, **percentiles not averages**, **SLI/SLO/error budgets**, multi-burn-rate alerting, alert fatigue, on-call design |
| 06 | [Incident Management & Postmortems](06_incident_management_postmortems.md) | severity levels, the **Incident Command System**, mitigate-first, comms discipline, **blameless postmortems** & RCA, action items that actually happen, learning culture |
| 07 | [Chaos & Resilience Engineering](07_chaos_resilience_engineering.md) | steady-state hypothesis, blast-radius control, fault injection, **game days / DiRT**, DR testing (RTO/RPO), tooling, the resilience patterns being verified |
| 08 | [DevSecOps: Security in the SDLC](08_devsecops_security_sdlc.md) | shift-left, threat modeling, **SAST/DAST/SCA/secret-scanning** gates, **software supply-chain** (SBOM/SLSA/signing), secrets management, compliance-as-code, CVE response |

---

## 🎯 How the pieces fit (the path from commit to confidence)

```
   write ──▶ review (04) ──▶ CI: test (02) + security gates (08) ──▶ build artifact (03)
                                                                         │
   observe (05) ◀── deploy progressively (03) ◀── ────────────────────┘
        │                                            (canary analyzed on signals 05)
        ├──▶ all good → ramp to 100%
        └──▶ regression → auto-rollback (03) ──▶ incident (06) ──▶ postmortem ──▶ guardrail
                                                                                     │
                          prove the guardrail works proactively ──▶ chaos (07) ◀────┘
```

The loop is the point: every chapter feeds the next, and incidents/chaos feed back
into tests, gates, and SLOs so the same failure can't recur.

---

## 🧵 The through-lines

- **Decouple deploy from release.** Trunk-based dev (01) + feature flags + progressive
  delivery (03) means shipping code and exposing a feature are separate, reversible
  acts. This single idea underpins safe velocity.
- **Automate the mechanical, reserve humans for judgment.** Formatters, linters, type
  checkers, and security scanners as CI gates (02/04/08) free review (04) to focus on
  correctness and design.
- **Symptoms over causes, budgets over absolutes.** Alert on SLO burn, not CPU% (05);
  spend an error budget on velocity until it's gone. Reliability becomes a number both
  dev and ops agree on.
- **Blameless or blind.** Blame makes people hide information; the postmortem (06) and
  the chaos game day (07) only work in a culture where failure is data, not fault.
- **Shift left, sign everything.** Security is cheapest at design/code time and the
  supply chain is now the front line (08).

> Staff/principal engineers are the ones who install these loops *before* they're
> needed — the test that catches the bug, the canary that catches the bad deploy, the
> SLO that catches the regression, the chaos drill that catches the untested failover.
> The work that doesn't show up in a diff is most of the job.

---

## 🔗 Where this connects

- **Technical incident triage** (the exact `perf`/`ss`/`iostat` commands behind a
  SEV1) lives in [`../os_net/enterprise_scenarios/`](../os_net/enterprise_scenarios/README.md);
  this folder is the *process* around those runbooks.
- **Design docs, RFCs, ADRs, migrations, and the staff/principal soft skills** live in
  [`../system_design/staff_principal/`](../system_design/staff_principal/README.md);
  workflow (01) and review (04) reference them rather than duplicate them.
- **Fleet rollout mechanics** (Ansible, immutable images, canary at the host level) are
  in [`../modern_os/linux/16_fleet_config_management.md`](../modern_os/linux/16_fleet_config_management.md).
- **Language-specific testing & security** depth is in each book
  ([python](../python_book/README.md) · [java](../java_book/README.md) · [cpp](../cpp_book/README.md)).
- **Architecture above** ([`../system_design/`](../system_design/README.md)) and **the
  substrate below** ([`../os_net/`](../os_net/README.md)) bracket this folder.
