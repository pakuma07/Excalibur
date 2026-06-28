# 07 — Chaos & Resilience Engineering

> **Audience:** Staff/principal engineers running services where "uptime" is a contractual SLO, not an aspiration. You already have [observability](05_observability_slos.md), CI/CD with fast rollback ([03](03_cicd_pipelines.md)/[06](06_incident_management_postmortems.md)), and resilience patterns in your designs. This chapter is about *proving they work* — before the 3am page does it for you.

At FAANG scale, something is always broken. A disk is failing, a host is being reimaged, a packet is being dropped, a cert is expiring, an AZ is browning out. You cannot prevent all of this. The only honest question is: **does the system survive it?** Chaos engineering answers that question with experiments instead of hope.

---

## 1. The premise: failure is the steady state

Hope is not a strategy, and "we designed it to be resilient" is a hypothesis, not a fact. The difference between a senior team and a staff-level one is that the staff-level team has *evidence*.

> Chaos engineering is the discipline of experimenting on a system in order to build confidence in the system's capability to withstand turbulent conditions in production.

Two reframes that change how you operate:

- **Failure is continuous, not exceptional.** With 10,000 hosts and a 3-year MTBF per host, you lose ~9 hosts a day to hardware alone. Resilience is a property you verify continuously, not a milestone you ship once.
- **You verify in production, carefully.** Staging does not have prod's traffic shape, dependency graph, data volume, cache warmth, or autoscaler behavior. A green staging tells you almost nothing about how prod degrades. (See §7 for why this is "carefully" and not "recklessly.")

---

## 2. The principles of chaos

Adapted from the Principles of Chaos. A valid chaos experiment has all five:

1. **Define the steady-state hypothesis.** A measurable definition of "normal" — your SLOs and golden signals (latency p99, error rate, throughput, saturation). See [05 — Observability & SLOs](05_observability_slos.md). If you can't measure normal, you can't detect abnormal, and you have no business injecting faults.
2. **Hypothesize the steady state holds under a fault.** "If we kill one of three replicas, p99 stays under 200ms and error rate stays under 0.1%." Write it down *before* you run it.
3. **Inject real-world faults.** Not synthetic toy failures — the things that actually happen: hosts die, networks slow, dependencies time out, certs expire (§3).
4. **Minimize the blast radius.** Start with one host, one shard, 1% of traffic. Ramp only after the small experiment passes. The first experiment should be almost boring.
5. **Automate and run continuously.** A one-time game day finds bugs once; continuous chaos catches regressions forever. Earn this with maturity (§4) — don't start here.

```yaml
# A chaos experiment is a hypothesis + a fault + a guardrail, not just "break stuff"
experiment: payment-svc-replica-loss
steady_state:                       # what "normal" means, measured
  - metric: http_p99_latency_ms
    threshold: { max: 200 }
  - metric: http_error_rate
    threshold: { max: 0.001 }
fault:
  type: pod-kill
  selector: { app: payment-svc, shard: canary }   # blast radius: ONE shard
  count: 1
guardrails:
  abort_if:                         # the kill switch — see §7
    - metric: http_error_rate
      breaches: { max: 0.005 }
  max_duration: 5m
```

---

## 3. Fault types to inject

Inject the failures your runbooks already describe. Each row maps to a real failure mode documented in [../os_net/enterprise_scenarios/](../os_net/enterprise_scenarios/README.md) — chaos is how you rehearse those scenarios on purpose.

| Fault | What it simulates | Classic tool / mechanism |
|---|---|---|
| Instance / host kill | Hardware failure, spot reclaim, reimage | Chaos Monkey |
| AZ failure | Availability-zone outage | Chaos Gorilla |
| Region failure | Region evacuation / regional outage | Chaos Kong |
| Network latency | Slow link, congested peer, noisy neighbor | toxiproxy, tc/netem |
| Packet loss | Lossy network, overloaded NIC | tc/netem, Chaos Mesh |
| Network partition | Split-brain, cross-AZ link cut | iptables, Chaos Mesh |
| DNS failure | Resolver outage, stale records | Gremlin, Chaos Mesh |
| CPU / mem / disk pressure | Noisy neighbor, leak, full volume | stress-ng, Chaos Mesh |
| Dependency failure/latency | Downstream service degraded | Istio fault filter, toxiproxy |
| Clock skew | NTP drift, leap second | libfaketime, Chaos Mesh |
| Expired cert | TLS expiry, rotation failure | manual / scheduled |
| Traffic spike | Thundering herd, viral event | load generator |

```bash
# Network faults in tests/staging with toxiproxy — no kernel changes, scriptable
toxiproxy-cli create payments-db -l 127.0.0.1:5433 -u db:5432
toxiproxy-cli toxic add payments-db -t latency -a latency=300 -a jitter=50  # +300ms ±50
toxiproxy-cli toxic add payments-db -t timeout -a timeout=5000              # hard cut at 5s
# ... run your test suite against :5433, assert the circuit breaker opens ...
toxiproxy-cli toxic remove payments-db -n latency_downstream                # always clean up
```

```yaml
# Dependency latency at the mesh layer (Istio/Envoy fault filter) — no app changes
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata: { name: ratings-fault }
spec:
  hosts: [ratings]
  http:
    - fault:
        delay: { percentage: { value: 10.0 }, fixedDelay: 3s }  # 10% of calls +3s
        abort:  { percentage: { value: 1.0 },  httpStatus: 503 } #  1% hard-fail
      route: [{ destination: { host: ratings } }]
```

---

## 4. The maturity path — game days first, automation later

Do not start with continuous automated chaos. Start with a human in a room, a hypothesis, and a kill switch. Automation amplifies whatever discipline you already have — including the lack of it.

| Level | Practice | Cadence | Prereqs |
|---|---|---|---|
| 0 | Ad hoc — "prod broke and we learned" | unplanned | none (this is just outages) |
| 1 | **Game day** — scheduled, manual, hypothesis-driven, whole team in the room | quarterly | observability, SLOs, runbooks |
| 2 | Automated experiments in non-prod / canary | per-deploy | level 1 + abort automation |
| 3 | **Continuous chaos in prod** — randomized, blast-radius-bounded | always-on | level 2 + mature on-call |
| 4 | **Org-wide DiRT / GameDay** — multi-team, multi-region drills | annual/biannual | level 3 across services |

- **Game days** (level 1) are the highest-ROI starting point: a scheduled exercise where the team forms a hypothesis, injects one fault, watches the dashboards, and files the action items. The point is as much to test *people and runbooks* as the system.
- **DiRT** (Google's Disaster Recovery Testing) and **Amazon GameDays** are level-4 org-wide drills: simulate losing a region, a datacenter, a critical service, or a key person — and verify the org, not just the service, copes. These are scheduled events with a control room and a "company-wide" blast radius, run on purpose.

---

## 5. Disaster recovery testing

An untested backup is not a backup; it's a hope with a storage bill. An untested failover *will* fail — the first time you exercise it is the worst time to discover the IAM role is missing or the replica is 6 hours behind.

DR testing is chaos with a wider blast radius and a longer hypothesis:

- **Restore drills.** Actually restore the backup into a fresh environment and validate integrity. Measure the time — that's your real **RPO** (how much data you lose) and restore RTO.
- **Failover drills.** Promote the standby. Cut traffic over. Measure the real **RTO** (time to recovery) against the number in your SLA. Then fail *back*.
- **Region-evacuation drills.** Drain an entire region's traffic to the others and confirm the survivors have the capacity, the autoscaler keeps up, and stateful systems (caches, queues, leader election) don't melt.

```bash
# DR drill: verify RTO/RPO instead of assuming them
START=$(date +%s)
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier prod-payments \
  --target-db-instance-identifier dr-drill-payments \
  --restore-time "$(date -u -d '5 min ago' +%FT%TZ)"   # RPO target: 5 min
aws rds wait db-instance-available --db-instance-identifier dr-drill-payments
echo "Measured RTO: $(( $(date +%s) - START ))s"        # compare to SLA, file gap as action item
./validate-data-integrity.sh dr-drill-payments          # a restore that doesn't validate proves nothing
```

---

## 6. Tooling

| Tool | Domain | Notes |
|---|---|---|
| Chaos Monkey / Simian Army | instance kill, AZ/region (Kong/Gorilla) | The original; Spinnaker-integrated |
| Gremlin | broad (network, resource, state) | Commercial, safety-first UX, big halt button |
| LitmusChaos | Kubernetes-native | CNCF, CRD-driven experiments |
| Chaos Mesh | Kubernetes-native | CNCF, rich network/IO/time faults |
| AWS FIS | AWS-native (EC2, ECS, RDS, AZ) | Managed, IAM-scoped, built-in stop conditions |
| toxiproxy | TCP-level network faults | Great for deterministic faults in tests |
| Istio/Envoy fault filter | mesh-level delay/abort | Inject without touching app code |

Pick by where your blast-radius controls and abort button are easiest to enforce. Managed tools (AWS FIS, Gremlin) ship stop conditions and audit trails — use them over hand-rolled `kill` scripts in prod.

---

## 7. Prerequisites & safety — earn the right to break things

Chaos without prerequisites is just sabotage with extra steps. Before you inject a single fault in production:

- **Observability first ([05](05_observability_slos.md)).** If you can't see the steady state degrade in seconds, you can't detect a failed experiment or abort in time.
- **Fast mitigation first ([03](03_cicd_pipelines.md)/[06](06_incident_management_postmortems.md)).** Rollback and feature flags must be faster than the damage. If recovery takes 30 minutes, don't run experiments that can hurt for 30 minutes.
- **Good SLOs first.** The steady-state hypothesis *is* your SLOs. No SLOs, no hypothesis, no experiment.
- **An abort/halt button.** Every experiment auto-halts on guardrail breach and can be killed manually. Test the kill switch before the fault.
- **Blast-radius controls.** Start at 1%/one host/one shard. Ramp deliberately. Never region-wide on day one.
- **No hypothesis, no run.** "Let's see what happens" is not an experiment; it's an incident you scheduled.

> **The freeze rule:** never run chaos during an active incident, during a change freeze, or during a peak event (Black Friday, launch). Chaos adds signal during calm and only noise during a storm. Suspend automated chaos when an incident is declared.

```yaml
# AWS FIS makes the safety rails first-class — refuse to inject without a stop condition
stopConditions:
  - source: aws:cloudwatch:alarm                 # halt the moment the SLO alarm fires
    value: arn:aws:cloudwatch:...:alarm/payments-error-rate-slo
roleArn: arn:aws:iam::...:role/fis-scoped-to-canary-only   # least privilege, canary scope
```

---

## 8. The resilience patterns being verified

Chaos doesn't *create* resilience — it *proves* the patterns you already designed actually work under fire. Design detail lives in [../system_design/concepts/](../system_design/concepts/README.md); here's what each experiment confirms:

- **Timeouts** — does the caller give up before the user does, or hang forever?
- **Retries with backoff + jitter** — do retries recover transient faults *without* synchronizing into a retry storm?
- **Circuit breakers** — does the breaker open under sustained dependency failure and stop hammering the corpse?
- **Bulkheads** — does one slow dependency exhaust a shared thread pool and take down unrelated traffic?
- **Load shedding** — does the service drop excess load gracefully instead of falling over entirely?
- **Graceful degradation / fallback** — does the feature degrade (stale cache, default response) instead of erroring?

Verify these one at a time. A chaos experiment that injects dependency latency and watches the circuit breaker open is worth more than a paragraph in a design doc claiming it will. See also [02 — Testing Strategy at Scale](02_testing_strategy.md) for where fault injection fits in the test pyramid.

---

## 9. Symptom · Cause · Fix

**Failover that fails when it matters**
- **Symptom:** Real outage hits, failover is triggered, standby doesn't come up — replica was stale, IAM role missing, or DNS TTL too long.
- **Cause:** Failover was *designed* but never *exercised*. The runbook was theoretical.
- **Fix:** Schedule DR drills (§5). Promote the standby on purpose, quarterly. Measure RTO/RPO and file the gaps as action items *before* the outage.

**Retry storm takes down the dependency**
- **Symptom:** A dependency blips for 2 seconds; every client retries simultaneously; the synchronized stampede keeps it down for 20 minutes.
- **Cause:** Retries without jitter and without a retry budget — clients are DDoSing their own backend.
- **Fix:** Backoff + jitter, a retry budget (cap retries as a % of requests), circuit breakers. Then inject the dependency blip with chaos and *prove* the storm doesn't happen (§3, §8).

**Staging was green, prod fell over**
- **Symptom:** Every test passed, the canary looked clean, then prod degraded under real traffic.
- **Cause:** Staging lacks prod's scale, traffic shape, dependency graph, and cache state. Green staging is not evidence about prod.
- **Fix:** Run experiments in production with bounded blast radius (§1, §7). Verify the steady-state hypothesis where the real load lives.

---

The cultural payoff is the whole point: chaos engineering turns *"I think we're resilient"* into *"we proved it on Tuesday, and here are the three bugs we fixed before they paged anyone."* It moves the discovery of failure from 3am, under pressure, with customers watching — to 2pm, with the team in the room, the kill switch ready, and a hypothesis written down.

> Next: [08 — DevSecOps: Security in the SDLC](08_devsecops_security_sdlc.md) — shifting security left, treating threats like the faults in this chapter: surfaced early, by design, before an attacker surfaces them for you.
