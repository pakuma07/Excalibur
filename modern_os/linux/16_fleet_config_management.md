# 16 — Fleet & Configuration Management at Scale

> **Audience:** Staff/principal engineers operating fleets of Linux hosts at FAANG scale. This is the **zoom-out** chapter — and the final one in this series. Chapters 01–10 taught the Bash language; 11–15 taught single-host operational craft (systemd, security, storage, packaging, debugging). This chapter sits *above* the single host: how do you converge, deploy to, and keep sane **10 / 1,000 / 100,000** hosts at once? It sits *below* distributed-systems architecture — see the sibling [system_design](../../system_design/README.md) reference for the layer above.

---

## 1. The problem: the ssh loop does not scale

You learned this loop in [04 — Control Flow](04_control_flow.md):

```bash
# WRONG at any serious scale
for host in $(cat hosts.txt); do
  ssh "$host" 'sudo apt-get install -y nginx && sudo systemctl enable --now nginx'
done
```

It looks fine on 3 hosts. On 1,000 it is a liability. Why it fails:

- **Serial.** 1,000 hosts × 2 s round-trip = 30+ minutes, one slow host blocks the rest.
- **Not idempotent.** Re-running re-does work; a partial failure leaves half-installed state. Compare to [10 — Advanced & Enterprise](10_advanced_enterprise.md) where idempotency is the discipline.
- **No error aggregation.** Exit code of the loop is the *last* host's. You have no idea which 17 hosts failed without scraping stdout.
- **No rollout safety.** It hits all hosts at full speed — a bad command takes down the whole fleet at once (zero blast-radius control).
- **Drifts.** Nothing records desired state. Six months later, half the hosts have `nginx` 1.18 and half 1.24, and nobody knows why.

The rest of this chapter is the disciplined answer, in four tiers: **ad-hoc ssh → declarative config mgmt → immutable images → orchestrator**. Pick the lowest tier that solves the problem.

---

## 2. SSH at scale — the imperative tier (for one-offs only)

Sometimes you genuinely need to ask 500 hosts a question *right now* (an incident, a forensic sweep). That is the legitimate use of parallel ssh. It is **never** the way to manage durable config.

| Tool | Strength |
|---|---|
| `pdsh` | Classic HPC fan-out, dynamic host ranges (`node[01-50]`), `dshbak` to coalesce output |
| `pssh` / `parallel-ssh` | Simple, Python, per-host output files, easy concurrency cap |
| `clush` (ClusterShell) | Node groups, `--diff` across hosts, gather mode — best for "are these all the same?" |
| `xargs -P` / GNU `parallel` + ssh | No extra tooling; you build it from primitives ([10 — Advanced & Enterprise](10_advanced_enterprise.md)) |

**Safe parallel-ssh with a concurrency cap** (don't open 100k sockets at once):

```bash
# -p 50: at most 50 concurrent connections (rate-limit your own fan-out)
# -t 10: 10s connect timeout so one dead host can't hang the batch
# -i:    inline output; -o dir/ to keep per-host stdout for grepping failures
pssh -h hosts.txt -p 50 -t 10 -i 'uptime'

# Aggregate exit codes from a parallel run — the part the for-loop never did:
parallel -j 50 --tag --joblog run.log \
  ssh -o ConnectTimeout=10 {} 'rpm -q openssl' :::: hosts.txt
awk -F'\t' 'NR>1 && $7!=0 {print $NF, "FAILED rc="$7}' run.log   # surface the failures
```

```bash
# clush: query a node group and show which hosts DIFFER (drift detector, ad hoc)
clush -g web -b 'rpm -q nginx'   # -b = coalesce identical output, highlights outliers
```

### Bastion / jump-host + SSH certificate authorities

At fleet scale you do **not** push `authorized_keys` and curate `known_hosts` per host — that itself is a config-drift problem. Use an **SSH CA** (cross-ref [12 — Security & Access Control](12_security_access_control.md)):

```bash
# Hosts trust a CA public key (one line in sshd_config), not N user keys:
#   TrustedUserCAKeys /etc/ssh/ca_user.pub
# Clients trust the host CA (one line in known_hosts), not N host keys:
#   @cert-authority *.prod.example.com ssh-ed25519 AAAA...

# Engineers get a SHORT-LIVED cert from the CA (e.g. 8h) instead of a static key:
ssh-keygen -s ca_user -I "parveen@takeda" -n ops -V +8h id_ed25519.pub
# Jump through the bastion transparently:
ssh -J bastion.prod.example.com web0042.prod.example.com
```

This removes the per-host key lifecycle entirely: hosts come and go, certs expire, nothing to garbage-collect.

> **When ad-hoc ssh is appropriate:** one-off queries, emergency mitigation, forensic gathering. **When it's a foot-gun:** anything you'd want to be true *tomorrow*. If you `ssh` in and edit a file by hand, you've created drift (§9). Change the **code**, not the host.

---

## 3. Configuration management: declarative, idempotent, desired-state

The core mental shift from §1: stop writing *steps* (imperative), declare *desired state* (declarative). The tool figures out the diff and converges. Re-running is safe and a no-op when already converged.

**Push vs pull** is the first architectural fork:

| | **Push** (Ansible) | **Pull** (Puppet / Chef / Salt) |
|---|---|---|
| Transport | SSH, **agentless** | Agent + central server/master |
| Trigger | Operator/CI runs a playbook | Agent polls (e.g. every 30 min) and self-converges |
| Bootstrap | Nothing on the host but sshd + Python | Must install + manage the agent |
| Scale ceiling | 100s–low 1000s per run (SSH fan-out) | Tens of thousands, always-on, continuous |
| Drift handling | Only when you run it | Continuous re-convergence every interval |
| Best for | The dominant default; CI-driven rollouts, cloud | Very large, long-lived, always-on fleets; strict continuous compliance |

Ansible's agentless push model won the mainstream because it has near-zero bootstrap cost and fits GitOps/CI naturally. Pull systems (Puppet/Chef/Salt) earn their keep on huge always-on fleets where you want *continuous* self-healing convergence without an operator pressing go. The rest of this chapter uses Ansible as the worked example.

---

## 4. Ansible in depth — the practical workhorse

### 4.1 Inventory

```ini
# inventory.ini — static, grouped
[web]
web[01:50].prod.example.com

[web:vars]
nginx_workers=4

[canary]
web01.prod.example.com   # a named subset for first-blast rollout (§4.5)
```

```yaml
# group_vars/web.yml — variables scoped to the 'web' group
nginx_workers: 8
listen_port: 443
# host_vars/web01.prod.example.com.yml — per-host overrides
```

**Dynamic inventory** is mandatory once hosts are ephemeral (auto-scaling). Don't maintain a text file the cloud invalidates hourly:

```yaml
# inventory.aws_ec2.yml — hosts discovered live from the AWS API
plugin: aws_ec2
regions: [us-east-1]
keyed_groups:
  - key: tags.Service        # group by EC2 tag => groups like "_web", "_cache"
    prefix: ""
filters:
  tag:Environment: prod
```

### 4.2 Playbooks, tasks, modules — and idempotency

A **module** converges to a desired state; it is not a shell command. This is the whole point.

```yaml
# WRONG — shell module: imperative, not idempotent, always "changed"
- shell: apt-get install -y nginx        # re-runs every time, can't report drift

# RIGHT — apt module: declarative; "present" is a state, runs once, reports honestly
- ansible.builtin.apt:
    name: nginx
    state: present
```

Every task reports one of: **`ok`** (already in desired state), **`changed`** (it converged it), **`failed`**. A clean run on a converged fleet is *all green, zero changed* — that is your drift signal (§9). A shell script can never tell you "nothing needed doing."

### 4.3 Roles, handlers, variables, templates, vault, dry-run, tags

```
roles/nginx/
  tasks/main.yml        # the work
  handlers/main.yml     # notified actions (restart)
  templates/nginx.conf.j2
  defaults/main.yml     # overridable variables
```

- **Roles** package reusable, parameterized units.
- **Handlers** run *once at the end* only if notified — so config change → single restart, not one per task.
- **Jinja2 templates** (`template:` module) render config from variables.
- **Ansible Vault** encrypts secrets at rest in Git: `ansible-vault encrypt group_vars/web/secrets.yml`.
- **`--check` / `--diff`** is your safety net: a dry run that reports what *would* change, with a line-level diff. Run it before every real apply.
- **Tags** (`--tags config`) and **`--limit`** (`--limit canary`) scope a run to a slice of tasks/hosts.

### 4.4 `become` — privilege escalation

```yaml
- hosts: web
  become: true            # escalate via sudo (cross-ref ch.12 sudoers policy)
  become_user: root
```

`become` maps onto the sudo policy from [12 — Security & Access Control](12_security_access_control.md): grant the automation principal exactly the commands it needs, log it, don't hand out blanket `NOPASSWD: ALL`.

### 4.5 Safe rollout — canary then ramp

The hard-won lesson: **don't converge a broken playbook onto 10,000 hosts simultaneously.** A green `--check` does not prove the *result* is healthy. Roll in batches and watch.

```yaml
- hosts: web
  become: true
  serial:                       # rolling batches: 1 host, then 10%, then 50%
    - 1
    - "10%"
    - "50%"
  max_fail_percentage: 20       # abort the whole run if >20% of a batch fails
  any_errors_fatal: false       # (true => first failure halts everything)
  pre_tasks:
    - name: Drain from load balancer            # delegate the LB call off-host
      community.general.haproxy: { state: disabled, host: "{{ inventory_hostname }}" }
      delegate_to: lb01.prod.example.com
  roles:
    - nginx
  post_tasks:
    - name: Re-add to load balancer
      community.general.haproxy: { state: enabled, host: "{{ inventory_hostname }}" }
      delegate_to: lb01.prod.example.com
```

`serial:` bounds the **blast radius**; `max_fail_percentage` is the automatic kill-switch; the pre/post `delegate_to` drains each host from the LB before touching it and re-adds after. This is the difference between "a deploy" and "an outage."

### 4.6 A complete small example

`roles/myapi/templates/myapi.service.j2` (a unit per [11 — systemd: Service Authoring & Operations](11_systemd_services.md)):

```ini
[Unit]
Description=myapi
After=network-online.target
[Service]
ExecStart=/usr/bin/myapi --workers {{ myapi_workers }} --port {{ myapi_port }}
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

`roles/myapi/tasks/main.yml`:

```yaml
- name: Install package
  ansible.builtin.apt: { name: myapi, state: present }

- name: Render unit from template
  ansible.builtin.template:
    src: myapi.service.j2
    dest: /etc/systemd/system/myapi.service
  notify: restart myapi          # only fires handler if the file changed

- name: Enable + start
  ansible.builtin.systemd:
    name: myapi
    enabled: true
    state: started
    daemon_reload: true
```

`roles/myapi/handlers/main.yml`:

```yaml
- name: restart myapi
  ansible.builtin.systemd: { name: myapi, state: restarted }
```

Run it — **dry run, then canary, then ramp**:

```bash
ansible-playbook site.yml --check --diff                 # 1. prove what changes
ansible-playbook site.yml --limit canary                 # 2. one box; observe metrics
ansible-playbook site.yml                                 # 3. full, governed by serial:/max_fail_percentage
```

### 4.7 Testing config code

Config code is code — gate it in CI like any other.

```bash
ansible-lint site.yml          # style + correctness + anti-patterns
molecule test                  # spin role in a container, converge, ASSERT, idempotence-check
```

**Molecule** is the key tool: it converges the role in a throwaway container, runs assertions, then converges *again* and fails if the second run reports any `changed` — i.e. it **machine-verifies idempotency**. Wire `ansible-lint` + `molecule test` into the PR pipeline; nothing merges red.

---

## 5. Immutable infrastructure — replace, don't mutate

At FAANG scale, the dominant paradigm largely **replaces** in-place config management: **cattle, not pets.** You don't log in and fix a host; you destroy it and boot a fresh one from a known image.

```
WRONG (mutable / pets):  long-lived host  --ansible converge--> ...months of patches, hotfixes, drift...
RIGHT (immutable/cattle): Packer bakes golden image v42  -->  boot N identical instances  -->  v43 ready? replace them
```

**Bake a golden image with Packer**, using Ansible/scripts as the provisioner **at build time** (not runtime):

```hcl
# myapi.pkr.hcl — Packer builds a versioned AMI; Ansible runs ONCE, at bake time
source "amazon-ebs" "base" {
  source_ami    = "ami-base-ubuntu-2204"
  instance_type = "t3.medium"
  ami_name      = "myapi-{{timestamp}}"      # versioned, immutable artifact
}
build {
  sources = ["source.amazon-ebs.base"]
  provisioner "ansible" {                     # the SAME role from §4.6, at build time
    playbook_file = "site.yml"
  }
}
```

```bash
packer build myapi.pkr.hcl     # produces ami-0abc... — a frozen, tested artifact
```

Then **roll the fleet by replacing instances**, not converging them: update the launch template to the new AMI and trigger an **Auto Scaling Group instance refresh** (or blue-green: stand up a parallel ASG, shift traffic, tear down the old). 

**Why this kills configuration drift** (§9, the silent killer): there is no in-place mutation, so hosts *cannot* diverge over months. Every host of version v42 is bit-identical because it came from the same image. Rollback = deploy the previous AMI. The image is tested once and runs everywhere.

| | **Mutable (config mgmt in place)** | **Immutable (rebake + replace)** |
|---|---|---|
| Change applied by | Converging the running host | Building a new image, replacing host |
| Drift | Accumulates; needs continuous convergence | Impossible by construction |
| Rollback | Re-converge old state (often messy) | Boot the previous image |
| Best for | Bare metal, stateful, slow-to-boot | Cloud, stateless, fast-scaling fleets |

---

## 6. cloud-init / user-data — the per-instance glue

Even immutable images need a *tiny* amount of first-boot, per-instance dynamics: inject a secret, fetch config, join the cluster, register with the orchestrator. That's **cloud-init** (consuming the instance's user-data).

```yaml
#cloud-config
# Runs once on first boot. Keep it MINIMAL — everything static is already in the image.
write_files:
  - path: /etc/myapi/instance.env
    content: |
      REGION={{ region }}
      CLUSTER_TOKEN_PATH=/run/secrets/token
runcmd:
  - cloud-init-fetch-secret > /run/secrets/token   # per-instance secret injection
  - systemctl start myapi
  - register-with-orchestrator --service myapi      # join the fleet
```

**The boundary:** golden image = everything static and slow (packages, the binary, base config). cloud-init = the few things that are unique per instance (identity, secrets, cluster membership). If your cloud-init is doing package installs, you've put logic in the wrong tier — move it into the Packer build.

---

## 7. The modern synthesis — GitOps

The convergence of all of the above: **everything lives in Git, is reviewed by PR, and applied by automation. The host is disposable.**

- **Config and infra in Git.** Playbooks/roles, *and* infrastructure via **Terraform** (the ASG, launch template, LB, networking). Nothing changes by hand.
- **PR review + CI.** Lint, `molecule test`, `terraform plan`, policy checks — all gate the merge.
- **Automation applies.** Merge to main triggers the bake + rollout; no human runs `ssh`.
- **The orchestrator maintains desired count.** Kubernetes or an ASG keeps N healthy instances; a dead host is replaced automatically — you never fix it.

**Where config management still earns its place:** base/golden images, **bare metal**, network gear, databases and other stateful pets, and any non-containerized workload. **Where containers + orchestrators have taken over:** stateless services, fast-scaling fleets — see [../../os_net/operating_system/07_virtualization_containers.md](../../os_net/operating_system/07_virtualization_containers.md) for that path, and [system_design](../../system_design/README.md) for the architecture that sits above both.

---

## 8. Drift, convergence & safety

**Drift** = hosts silently diverging from the declared state (a hotfix here, a manual edit there) until "identical" hosts behave differently and nobody can reproduce the bug.

- **Symptom:** two "identical" web hosts return different responses; one has a config file the others don't.
- **Cause:** someone `ssh`'d in and edited `/etc/...` out-of-band during an incident and never put it in the code.
- **Fix:** detect and re-converge. **Discipline: change the code, not the host.**

```bash
# Scheduled drift detection — a check-mode run that should report ZERO changed:
ansible-playbook site.yml --check --diff
# Any 'changed' line = drift. Alert on it. (Pull systems do this continuously by design.)
```

The danger of out-of-band manual fixes: in a pull/immutable world they get **wiped** on the next converge or rebake (good — but surprising if undocumented); in a push world they **persist as silent drift** (worse). Either way the fix is the same: codify it, PR it, let automation apply it. Use **change windows** for risky fleet-wide changes, and apply **blast-radius discipline** everywhere — rate-limit fan-out, canary, ramp with `serial:`, and put **observability on the rollout itself** (watch error rate / latency per batch, not just "the playbook exited 0").

---

## 9. End-to-end: how a change reaches 10,000 hosts safely

```
1. PR            engineer edits role/template/Terraform in Git, opens PR
2. CI            ansible-lint + molecule test + terraform plan + policy gates  → must be green
3. Build/bake    Packer builds a new versioned golden image (Ansible as build-time provisioner)
4. Canary        deploy image to 1 host / 1%  (--limit canary or 1-instance refresh)
5. Observe       watch SLOs on the canary: error rate, latency, saturation — auto-halt on regression
6. Ramp          instance refresh / serial: [10%, 50%] with max_fail_percentage kill-switch
7. Full          remainder; LB drain pre / re-add post each batch
8. Verify        scheduled --check run reports zero drift; rollback = previous image
```

Never skip 4–6. A green CI proves the *code* is valid; only the canary proves the *result* is healthy in production.

### Which tier to use — rule of thumb

| Need | Tier |
|---|---|
| One-off query / emergency mitigation | **Ad-hoc parallel ssh** (§2) — never for durable config |
| Bare metal, stateful pets, slow-boot, base images | **Config management** (§3–4, push or pull) |
| Cloud, stateless, fast-scaling, kill drift dead | **Immutable images** (§5) + cloud-init glue (§6) |
| Stateless services at high churn | **Container orchestrator** ([07 — Virtualization & Containers](../../os_net/operating_system/07_virtualization_containers.md)) |

Always reach for the **lowest tier that solves the problem** — but bias toward immutable + orchestrated for anything stateless at scale. The discipline that ties all four together: **declare desired state in Git, converge with safety rails, and never fix a host by hand.**

---

> **Related:** [README](README.md) (the Linux series index) · [../../os_net/](../../os_net/README.md) (OS internals & incident runbooks — the layer beneath) · [../../system_design/](../../system_design/README.md) (distributed-systems architecture — the layer above). *This is the final chapter of the series.*
