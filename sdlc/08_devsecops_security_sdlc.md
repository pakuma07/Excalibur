# 08 — DevSecOps: Security in the SDLC

> **Audience:** Staff/principal engineers wiring security into the way an org *ships software*, not bolting it on at the end. This is the **process integration** chapter — how security threads through design → code → build → deploy → run. Language-specific security (memory safety, injection, deserialization) lives in the language books' security chapters; host/network hardening lives in os_net; IAM and threat-modeling *architecture* live in system_design. Here we cover the **SDLC plumbing**: gates, automation, supply chain, secrets, and response.

---

## 1. The shift: gate-at-the-end vs shift-left

The legacy model: build for two quarters, then hand the artifact to a security team for a pre-release pen test. The findings arrive when the design is frozen, the deadline is tomorrow, and fixing the root cause means re-architecting. Security becomes adversarial, slow, and the thing everyone routes around.

**Shift-left** means security work happens at the *earliest stage where it is cheapest*: threat modeling at design-doc time, secure-by-default libraries at code time, automated scanning in CI, signed provenance at build time, least-privilege at deploy time, and audit logging at run time.

> **WRONG:** "Security reviews the release in the final week."
> **RIGHT:** "Every stage has an automated security control; the human review is reserved for novel risk, not for catching `eval()` in a diff."

The lever is **paved roads**: a golden CI template, a vetted base image, a secrets client, an auth library — so the *easy* path is the *secure* path. Security is everyone's job precisely because automation makes the default safe and the deviation loud. See [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md) for the pipeline these gates live in.

---

## 2. Threat modeling in design

Threat modeling is "think like an attacker" applied at design-doc time, before code exists. The cheapest vulnerability is the one the design never created.

**STRIDE** is the workhorse mnemonic — one threat class per letter:

| Letter | Threat | Defends property | Example mitigation |
|--------|--------|------------------|--------------------|
| **S** | Spoofing | Authentication | mTLS, OIDC, signed tokens |
| **T** | Tampering | Integrity | signatures, checksums, WORM logs |
| **R** | Repudiation | Non-repudiation | audit logs, signed actions |
| **I** | Information disclosure | Confidentiality | encryption, least privilege |
| **D** | Denial of service | Availability | rate limits, quotas, autoscale |
| **E** | Elevation of privilege | Authorization | RBAC, capability drops |

**Process:** draw a **data-flow diagram** (external entities → processes → data stores), mark **trust boundaries** (where data crosses a privilege level — internet→edge, service→DB, tenant→tenant), then walk each element through STRIDE.

- **Heavyweight** threat modeling (a multi-day workshop, full DFD) fits a new payments system or a multi-tenant boundary.
- **Lightweight / continuous** threat modeling fits everything else: a short "Security Considerations" section in every design doc answering *"what's the worst an attacker can do here, and what stops them?"*, revisited when the trust boundaries change.

Link [../system_design/](../system_design/README.md) for IAM, authn/authz architecture, and tenancy isolation patterns — that's the *architecture*; this chapter is the *ritual* of doing it every design cycle.

---

## 3. Secure coding & the gate in code review

Code review is the human gate. It is not where you re-derive cryptography — it's where you confirm the **secure default** was used and the paved road wasn't bypassed.

What reviewers actually check:

- **Secure defaults**: parameterized queries (not string-built SQL), the org auth middleware (not a hand-rolled token check), the vault client (not `os.environ["DB_PASSWORD"]`).
- **OWASP Top 10 awareness**: broken access control, injection, SSRF, security misconfiguration, vulnerable components. Reviewers don't memorize CVEs; they pattern-match the *categories*.
- **The diff's blast radius**: does this touch a trust boundary? Does it deserialize untrusted input? Does it add a new dependency?

Depth on *how* each language gets injection/memory/deserialization wrong (and right) lives in the language security chapters — link, don't duplicate: [../python_book/35_security_supply_chain/](../python_book/35_security_supply_chain/README.md) · [../java_book/32_security_supply_chain/](../java_book/32_security_supply_chain/README.md) · [../cpp_book/27_security_supply_chain/](../cpp_book/27_security_supply_chain/README.md).

The reviewer is a backstop. The real coverage comes from automation (§4) running on every push so the human sees a clean diff, not a haystack.

---

## 4. Static & dynamic analysis in CI

The automated tiers. Each catches a different class, runs at a different stage, and produces a different false-positive profile.

| Tier | What it scans | When | Tools |
|------|---------------|------|-------|
| **SAST** | Source code for vuln patterns | pre-merge (PR) | CodeQL, Semgrep, SonarQube |
| **SCA** | Dependencies for known CVEs | pre-merge + nightly | Dependabot, Snyk, OSV-Scanner |
| **Secret scan** | Committed credentials | pre-commit + pre-merge | gitleaks, trufflehog |
| **IaC scan** | Terraform/k8s misconfig | pre-merge | tfsec, checkov, KICS |
| **Container scan** | Image OS/lib CVEs | post-build | Trivy, Grype |
| **DAST** | Running app (black-box) | staging / nightly | OWASP ZAP, Burp |
| **IAST** | Instrumented app at test time | integration tests | Contrast, runtime agents |

SAST/SCA/secret/IaC scans are fast and **shift-left to the PR**. DAST needs a deployed target, so it runs against staging. IAST sits in between — an agent inside the app during integration tests, correlating runtime data flow with code.

```yaml
# .github/workflows/security.yml — gates on every PR
name: security
on: [pull_request]
jobs:
  sast:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Semgrep SAST
        run: semgrep ci --config auto --severity ERROR   # fail only on ERROR
  secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }       # full history so we scan all commits
      - run: gitleaks detect --redact --exit-code 1       # any leak fails the build
  sca:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: OSV-Scanner (deps)
        run: osv-scanner --lockfile=poetry.lock --fail-on=critical
```

### The gating policy

The single most important config decision is **what fails the build vs what files an issue**. Get this wrong and either you ship CVEs (gate too loose) or engineers route around the scanner (gate too tight).

> **WRONG:** "Fail the build on any finding." → 400 medium findings, devs add `// nosemgrep` everywhere, the gate is dead within a month.
> **RIGHT:** "Fail on **critical/high with a known exploit path**; auto-file a tracked issue (with SLA) for everything else; allow a time-boxed, signed-off suppression for false positives."

```yaml
# gating policy as data — consumed by the CI step
gate:
  fail_build_on: [CRITICAL, HIGH]      # block merge
  ticket_only:   [MEDIUM, LOW]         # tracked, SLA'd, non-blocking
  suppressions:
    require_expiry: true               # every waiver expires (max 90d)
    require_approver: security-team     # signed-off, not self-served
```

**Noise management is the job.** A scanner at 30% false-positive rate trains engineers to ignore it. Tune rulesets, baseline existing findings (only gate on *new* ones), and treat suppression sprawl as a tracked metric.

---

## 5. Software supply-chain security

The defining threat of the last several years: you no longer just defend *your* code, you inherit the risk of every dependency and every build step. **SolarWinds** (compromised build pipeline injected a backdoor into signed releases), **Log4Shell** (a ubiquitous logging lib turned one log line into RCE), and **xz/liblzma** (a multi-year social-engineering campaign to plant a backdoor in a core compression library) are the canonical case studies.

The threat surface:

- **Dependency confusion**: your private package name resolves to a public registry where an attacker published a higher version.
- **Typosquatting**: `reqeusts`, `python-dateutil` lookalikes.
- **Malicious / hijacked packages**: a maintainer account compromised, a malicious post-install script.

The defenses, layered:

| Control | What it gives you |
|---------|-------------------|
| **Lockfiles + pinning** (hashes, not ranges) | reproducible, tamper-evident resolution |
| **SBOM** (CycloneDX / SPDX) | a machine-readable inventory: "what is in this artifact?" |
| **Signing** (Sigstore / cosign) | "this artifact came from our pipeline, unmodified" |
| **Provenance / attestation** (in-toto, SLSA) | "here is *how and where* it was built" |
| **Hermetic / reproducible builds** | no network at build time → byte-identical output |
| **Vendoring + upstream verification** | you control and review the source of truth |

**SLSA** (Supply-chain Levels for Software Artifacts) frames build integrity as a ladder:

| Level | Requirement | Roughly means |
|-------|-------------|---------------|
| **L1** | Provenance exists | build emits *some* metadata |
| **L2** | Signed provenance, hosted build | tamper-evident, not on a laptop |
| **L3** | Hardened, isolated build platform | provenance is non-forgeable |

```bash
# Generate an SBOM and sign the image with provenance at build time
syft packages dir:. -o cyclonedx-json > sbom.json     # inventory
cosign attest --predicate sbom.json \
  --type cyclonedx $IMAGE                              # attach signed SBOM
cosign sign --yes $IMAGE                               # keyless (OIDC) signature

# At deploy time, verify before admitting
cosign verify $IMAGE \
  --certificate-identity-regexp '.*@ourorg\.com' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The pipeline mechanics (where build steps live, artifact promotion) are in [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md). The depth on per-ecosystem pinning and registry config is in the language supply-chain chapters linked in §3.

---

## 6. Secrets management

The rule: **secrets never live in code, and never in an env file committed to the repo.** A secret in git history is a secret leaked forever, even after you delete the line.

The progression, worst to best:

1. Hardcoded in source — catastrophic, scanned for in §4.
2. `.env` in the repo — same problem, slightly hidden.
3. Injected env var from a CI secret store — better, but long-lived and broadly readable.
4. **Vault** (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager, cloud KMS) with **dynamic, short-lived** secrets — a DB credential that's minted on request and expires in an hour.
5. **Workload identity / OIDC federation** — *no stored secret at all*. The workload presents a signed identity token; the cloud trades it for short-lived credentials.

> **WRONG:** a static `AWS_SECRET_ACCESS_KEY` stored in CI, copied to three repos, rotated never.
> **RIGHT:** GitHub Actions OIDC federation — the job assumes a role with no long-lived key in existence.

```yaml
# OIDC federation: zero long-lived cloud keys
permissions:
  id-token: write       # let the job mint an OIDC token
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: arn:aws:iam::111122223333:role/deploy
      aws-region: us-east-1
      # no access-key / secret-key fields exist — that's the point
```

**Rotation** is non-negotiable: short TTLs make rotation automatic; long-lived secrets need a scheduled, tested rotation job (an untested rotation path *is* an outage). When a secret leaks, the response is **rotate first, investigate second** — see §9.

---

## 7. Runtime & infra security (brief — link out)

Once the artifact is deployed, the controls shift to limiting blast radius. Kept brief here; the depth is in os_net and modern_os.

- **Least privilege**: every process/role gets the minimum it needs. Linux user/capability/namespace hardening → modern_os Linux security chapter; see [../os_net/](../os_net/README.md).
- **Container / k8s security**: drop capabilities, `seccomp` profiles, read-only root FS, non-root UID, no privileged pods → os_net.
- **Network policy / zero-trust**: default-deny east-west traffic, mTLS between services, identity-based (not IP-based) authorization → os_net.
- **Admission control / policy-as-code**: OPA/Gatekeeper or Kyverno reject non-compliant manifests *at deploy time* (no `:latest` tags, no unsigned images, no privileged containers).
- **Audit logging**: every privileged action is logged immutably — the substrate for both forensics and the Repudiation defense from §2.

```rego
# OPA/Gatekeeper: refuse images that aren't signed by our pipeline
deny[msg] {
  input.request.kind.kind == "Pod"
  img := input.request.object.spec.containers[_].image
  not startswith(img, "registry.ourorg.com/")
  msg := sprintf("image %q is not from a trusted registry", [img])
}
```

---

## 8. Compliance & governance as code

Compliance frameworks — **SOC 2** (service org controls), **ISO 27001**, **PCI DSS** (cardholder data), **HIPAA** (health data) — all reduce to the same demand: *prove your controls exist and operate*. The losing move is treating the annual audit as a scramble; the winning move is making evidence a **byproduct of the pipeline**.

- **Policy-as-code**: the control ("all prod images are signed", "all infra changes are peer-reviewed") is enforced in CI/admission (§4, §7), so compliance is *continuous* not *point-in-time*.
- **Audit trails**: signed commits, PR approvals, immutable deploy logs — these *are* the evidence. An auditor's "show me the change-management control" is answered by the git/CI history, not a Confluence doc.
- **Evidence automation**: nightly jobs snapshot control state (who has prod access, which images are unsigned, which CVEs are open) into the evidence store.

> **WRONG:** two engineers spend three weeks before the SOC 2 audit screenshotting dashboards.
> **RIGHT:** the controls are CI gates; the evidence is collected automatically; the audit is a query.

Map each control to the pipeline mechanism that already enforces it — then the audit is a read of systems you run anyway.

---

## 9. Vulnerability response

When a CVE drops against something you ship, response speed is a function of preparation. The "Log4Shell at 2am" drill: a critical RCE in a ubiquitous library is announced, and you have hours.

**Triage flow:**

1. **Are we affected?** This is where the **SBOM** (§5) pays off — query "which artifacts contain log4j-core <2.17?" and get an answer in minutes, not a week of grep.
2. **Is it reachable / exploitable** in our context? Reachability analysis from SCA tooling cuts the list.
3. **Patch via the upgrade pipeline** — bump the pin, let CI re-verify, promote through environments.

**SLAs by severity** (publish them, enforce them):

| Severity | Triage | Patch in prod |
|----------|--------|---------------|
| Critical (active exploit) | < 1 hour | < 24 hours |
| High | < 1 day | < 7 days |
| Medium | < 1 week | < 30 days |
| Low | next cycle | best-effort |

**Coordinated disclosure**: if *you* find a vuln in someone else's software (or a researcher finds one in yours), there's a `security.txt` / disclosure policy, an embargo window, and a fix-then-announce sequence. Don't drop a 0-day in a public issue.

A live exploitation is an incident — run it through [06 — Incident Management & Postmortems](06_incident_management_postmortems.md): incident commander, comms, timeline, blameless postmortem feeding back into the gates above.

---

## 10. Symptom / Cause / Fix

**A known-CVE dependency shipped to production.**
- *Symptom:* a customer scanner (or an attacker) flags a vulnerable library you've been running for months.
- *Cause:* no **SCA** gate in CI; dependency CVEs were never surfaced at merge or nightly.
- *Fix:* add OSV/Dependabot/Snyk scanning (§4) gating on critical/high; nightly re-scan of *deployed* artifacts (new CVEs land against old code); enforce lockfile pinning (§5).

**An AWS key was committed and used by attackers.**
- *Symptom:* a surprise bill / crypto-mining instances; the key appears in public git history.
- *Cause:* no **secret scanning**, a long-lived static key, no rotation.
- *Fix:* gitleaks/trufflehog pre-commit + pre-merge (§4); migrate to OIDC workload identity with no stored key (§6); on leak, **rotate first** then investigate; alert on anomalous use.

**We can't tell what's in our build when a CVE drops.**
- *Symptom:* Log4Shell lands and you spend three days grepping repos to find where the vulnerable version is.
- *Cause:* no **SBOM**; no inventory of what each artifact contains.
- *Fix:* generate and store a CycloneDX/SPDX SBOM per build (§5); make it queryable; wire it into the vuln-response triage step (§9) so "are we affected?" is a one-minute query.

---

> **Related:** [README](README.md) (sdlc index) · [../os_net/](../os_net/README.md) (host/network security + runbooks) · [../system_design/](../system_design/README.md) (IAM / threat-modeling architecture) · language security chapters: [../python_book/35_security_supply_chain/](../python_book/35_security_supply_chain/README.md) · [../java_book/32_security_supply_chain/](../java_book/32_security_supply_chain/README.md) · [../cpp_book/27_security_supply_chain/](../cpp_book/27_security_supply_chain/README.md) · within sdlc: [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md) · [06 — Incident Management & Postmortems](06_incident_management_postmortems.md).
