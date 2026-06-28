# Reliability & Availability

> **Scope:** Reliability vs availability, the nines (SLA/SLO/SLI, error budgets), redundancy & failover, single points of failure, fault tolerance & graceful degradation, resilience patterns (circuit breaker, retries+backoff+jitter, timeouts, bulkheads), health checks, chaos engineering, and disaster recovery (RTO/RPO, backups).

---

## 1. Reliability vs Availability

These terms are often conflated. They measure different things.

| | **Reliability** | **Availability** |
|---|---|---|
| Question | Does it work *correctly* over time? | Is it *up and reachable* right now? |
| Measures | Correct results, no data loss/corruption | Fraction of time the system serves requests |
| Metric | MTBF (mean time between failures) | Uptime % (the "nines") |
| Failure example | Returns wrong balance | Returns nothing / 503 |

A system can be **available but unreliable** (always responds, but with wrong data) or **reliable but unavailable** (correct when up, but frequently down). You want both.

Related terms:
- **MTBF** — Mean Time Between Failures (higher is better).
- **MTTR** — Mean Time To Repair/Recover (lower is better).
- **Availability ≈ MTBF / (MTBF + MTTR)** — reducing MTTR (faster recovery) often beats chasing higher MTBF.

---

## 2. The Nines

Availability is quoted as a percentage of uptime. Each extra "nine" is ~10× harder.

| Availability | "Nines" | Downtime / year | Downtime / month | Downtime / day |
|---|---|---|---|---|
| 90% | one nine | 36.5 days | ~72 h | 2.4 h |
| 99% | two nines | 3.65 days | ~7.2 h | 14.4 min |
| 99.9% | three nines | 8.77 h | 43.8 min | 1.44 min |
| 99.95% | | 4.38 h | 21.9 min | 43 s |
| 99.99% | four nines | 52.6 min | 4.38 min | 8.6 s |
| 99.999% | five nines | 5.26 min | 26.3 s | 0.86 s |

> Each nine costs disproportionately more (redundancy, automation, on-call). Pick a target that matches business need — five nines for a hobby app is waste.

### 2.1 SLI, SLO, SLA, Error Budgets

These come from Google SRE practice.

| Term | Definition | Example |
|---|---|---|
| **SLI** (Indicator) | A *measured* metric of service health | "% of HTTP requests with status < 500 and latency < 300 ms" |
| **SLO** (Objective) | The *internal target* for an SLI | "99.9% of requests succeed over 30 days" |
| **SLA** (Agreement) | A *contract* with consequences (penalties/credits) if breached | "99.5% uptime or 10% bill credit" |
| **Error Budget** | The allowed unreliability = `100% − SLO` | 0.1% of requests/time may fail |

```
SLA  ⊇  SLO  ⊇  SLI
(promise)  (goal)  (measurement)

Set SLA looser than SLO so you have internal headroom before breaching the contract.
```

**Error budget** turns reliability into a *currency*:

```
Monthly requests: 100,000,000
SLO: 99.9% success  =>  allowed failures = 0.1% = 100,000 requests/month
If you've burned 80,000 by mid-month, slow down risky deploys.
If budget is healthy, ship features faster.
```

This aligns dev (wants velocity) and ops (wants stability): **as long as the error budget isn't exhausted, you can keep shipping.** When it's spent, freeze risky changes and focus on reliability. SLO-based *alerting* is covered in `16_observability.md`.

---

## 3. Redundancy & Failover

**Redundancy** = having spare components so the failure of one doesn't take down the system. It's the primary tool for eliminating single points of failure.

### 3.1 Active-Active vs Active-Passive

```
ACTIVE-PASSIVE                          ACTIVE-ACTIVE
 ┌────────┐                              ┌────────┐
 │ Client │                              │ Client │
 └───┬────┘                              └───┬────┘
     │                                    ┌──┴──┐  (load balanced)
 ┌───▼────┐   replicate   ┌────────┐   ┌──▼─┐ ┌─▼──┐
 │ ACTIVE │ ────────────► │ STANDBY│   │ N1 │ │ N2 │  both serve traffic
 └────────┘   (idle until │ (warm/ │   └────┘ └────┘
              failover)    cold)   │     both replicate to each other
                          └────────┘
```

| | Active-Passive | Active-Active |
|---|---|---|
| Standby utilization | Idle (wasted capacity) | All nodes serve traffic |
| Failover time | Seconds–minutes (promote standby) | Near-instant (remove failed node) |
| Capacity after failure | Full (standby takes over) | Reduced (survivors absorb load) |
| Complexity | Simpler | Harder (concurrent writes, conflict resolution) |
| Cost efficiency | Lower (paying for idle) | Higher |

**Standby variants:** *cold* (must boot/restore), *warm* (running, partially synced), *hot* (fully synced, instant takeover).

**Failover** = detecting failure and switching traffic to a healthy component. **Failback** = returning to the original after recovery. Automate failover — manual failover means MTTR measured in human reaction time.

---

## 4. Single Points of Failure (SPOF)

A **SPOF** is any component whose failure brings down the whole system. The reliability discipline is: *find every SPOF and add redundancy or remove the dependency.*

Common SPOFs and remedies:

| SPOF | Remedy |
|---|---|
| Single app server | Multiple instances behind a load balancer |
| The load balancer itself | Redundant LBs (e.g., active-passive with VIP/Anycast) |
| Single database | Replicas + automatic failover |
| Single availability zone | Multi-AZ / multi-region deployment |
| Single DNS provider | Secondary DNS |
| Shared config service | Cluster it (etcd/Consul quorum) |

> Redundancy only helps if failures are **independent**. Two replicas in the same rack share the rack's power supply — a correlated failure. Spread across failure domains (host → rack → AZ → region).

---

## 5. Fault Tolerance & Graceful Degradation

- **Fault tolerance:** the system keeps operating *correctly* despite component failures (via redundancy and the patterns below).
- **Graceful degradation:** when fully working isn't possible, shed non-essential features and keep the core alive instead of crashing entirely.

Example: an e-commerce site under stress disables the "recommended products" widget (calls a struggling ML service) but **keeps checkout working**. Degrade the periphery; protect the core. A common implementation is serving stale cache or a static fallback when a dependency is down.

---

## 6. Resilience Patterns

These patterns prevent a single failing dependency from cascading into a full outage.

### 6.1 Timeouts

**Never wait forever.** A call without a timeout will, under failure, hold a thread/connection until exhaustion — turning a downstream slowdown into your own outage. Always set connect and read timeouts, and keep them tighter than the caller's timeout (so retries have room).

### 6.2 Retries with exponential backoff + jitter

Retry transient failures — but naive retries cause a **retry storm** (a *thundering herd*) that hammers a recovering service. Fix:
- **Exponential backoff:** wait `base × 2^attempt` (1s, 2s, 4s, ...).
- **Jitter:** add randomness so clients don't all retry in lockstep.
- Cap the number of retries and total delay; only retry **idempotent**/safe operations.

```python
import random, time

def retry_with_backoff(fn, max_attempts=5, base=0.1, cap=10.0):
    for attempt in range(max_attempts):
        try:
            return fn()
        except TransientError:
            if attempt == max_attempts - 1:
                raise
            backoff = min(cap, base * (2 ** attempt))
            sleep = random.uniform(0, backoff)   # "full jitter"
            time.sleep(sleep)
```

> "Full jitter" (`random(0, backoff)`) spreads retries best (AWS Architecture Blog). Without jitter, synchronized clients create periodic load spikes.

### 6.3 Circuit Breaker

Wraps a call to a failing dependency. After enough failures it **trips open** and fails fast (returning an error or fallback immediately) instead of waiting on timeouts — giving the dependency time to recover and protecting your own resources.

**Three states:**

```
            failures >= threshold
   ┌────────┐ ───────────────────► ┌────────┐
   │ CLOSED │                        │  OPEN  │  (fail fast, don't call)
   │ (normal│ ◄─────────────────┐    └───┬────┘
   │  calls)│   success in        │       │ after cooldown timer
   └────────┘   HALF_OPEN         │       ▼
        ▲                         │  ┌───────────┐
        │      failure in HALF_OPEN└──│ HALF_OPEN │ (allow trial calls)
        └─────────────────────────────└───────────┘
```

- **CLOSED:** calls pass through; count failures.
- **OPEN:** calls short-circuit (fail fast / fallback) for a cooldown period.
- **HALF_OPEN:** after cooldown, allow a few trial calls. Success → CLOSED; failure → OPEN again.

```python
import time

class CircuitBreaker:
    def __init__(self, fail_max=5, reset_timeout=30):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.state = "CLOSED"
        self.opened_at = None

    def call(self, fn, *args, fallback=None):
        if self.state == "OPEN":
            if time.time() - self.opened_at >= self.reset_timeout:
                self.state = "HALF_OPEN"      # time to probe
            else:
                return self._fail(fallback)   # fail fast

        try:
            result = fn(*args)
        except Exception:
            self._on_failure()
            return self._fail(fallback)
        else:
            self._on_success()
            return result

    def _on_success(self):
        self.failures = 0
        self.state = "CLOSED"

    def _on_failure(self):
        self.failures += 1
        if self.failures >= self.fail_max or self.state == "HALF_OPEN":
            self.state = "OPEN"
            self.opened_at = time.time()

    def _fail(self, fallback):
        if fallback is not None:
            return fallback()
        raise RuntimeError("Circuit open")
```

Real-world: Netflix Hystrix (now in maintenance), **resilience4j**, Envoy/Istio outlier detection.

### 6.4 Bulkheads

Named after a ship's watertight compartments: **isolate resources** so a flood in one section doesn't sink the ship.

```
WITHOUT BULKHEADS                 WITH BULKHEADS
shared thread pool (100)          per-dependency pools
 ├ Service A (slow!) ──┐           ┌ A: pool of 30 (slow A fills only this)
 ├ Service B           ├ all 100   ├ B: pool of 30
 └ Service C           ┘ consumed   └ C: pool of 40  (B & C still work)
   => whole app stalls               => failure contained
```

Implementations: separate thread pools / connection pools / queues per dependency, or separate compute (separate service instances) for different tenants/workloads.

### 6.5 How the patterns combine

```
client → [Timeout] → [Bulkhead pool] → [Circuit breaker] → [Retry+backoff] → dependency
                                                                  └─ on exhausted: fallback (graceful degradation)
```

---

## 7. Health Checks

Used by load balancers and orchestrators (Kubernetes) to route traffic only to healthy instances and to restart broken ones.

| Probe | Question | If it fails |
|---|---|---|
| **Liveness** | Is the process alive (not deadlocked)? | Restart the container |
| **Readiness** | Can it serve traffic *now* (deps warm, not overloaded)? | Remove from load-balancer pool (don't restart) |
| **Startup** | Has a slow-starting app finished booting? | Hold off other probes until done |

> **Anti-pattern:** a readiness check that calls a downstream dependency. If that dependency blips, *every* instance reports unready at once → total outage. Health checks should reflect *this instance's* health, not the whole world's. Use shallow checks for liveness/readiness; reserve deep checks for monitoring dashboards.

---

## 8. Chaos Engineering

The practice of **deliberately injecting failures in production-like environments** to verify the system survives them — turning unknown weaknesses into known, tested ones.

Process:
1. Define **steady state** (a metric of normal behavior, e.g., orders/sec).
2. Hypothesize: "killing one DB replica won't change steady state."
3. Inject the fault (kill node, add latency, drop packets, fill disk).
4. Observe; if steady state breaks, you found a weakness to fix.
5. Minimize blast radius; expand confidence gradually.

Pioneered by Netflix's **Chaos Monkey** (randomly terminates instances) / Simian Army; modern tools: Gremlin, AWS Fault Injection Service, Litmus, Chaos Mesh.

---

## 9. Disaster Recovery (DR)

DR is the plan for recovering from large-scale failures (region outage, data corruption, ransomware). Two defining objectives:

| Metric | Question | Meaning |
|---|---|---|
| **RTO** (Recovery Time Objective) | How long can we be **down**? | Max acceptable time to restore service |
| **RPO** (Recovery Point Objective) | How much **data** can we lose? | Max acceptable data loss, measured in time |

```
        last backup/replication        DISASTER          service restored
 ───────────┼──────────────────────────────┃────────────────────┃──────────►
            └──────── RPO ─────────────────┘                     │
                   (data lost)             └──────── RTO ────────┘
                                                  (downtime)
```

Lower RTO/RPO = higher cost. Choose per workload.

### 9.1 DR strategies (cost vs RTO/RPO)

| Strategy | RTO | RPO | Cost |
|---|---|---|---|
| **Backup & Restore** | Hours+ | Hours | $ |
| **Pilot Light** (minimal core always on, scale up on disaster) | 10s of min | Minutes | $$ |
| **Warm Standby** (scaled-down full copy running) | Minutes | Seconds | $$$ |
| **Multi-Site Active-Active** (full capacity in 2+ regions) | ~0 | ~0 | $$$$ |

### 9.2 Backups — the 3-2-1 rule

> **3** copies of data, on **2** different media, with **1** copy off-site (and ideally offline/immutable to survive ransomware).

- Test **restores**, not just backups — an untested backup is a hope, not a plan.
- Distinguish **full / incremental / differential** backups (trade restore speed vs storage).
- Replication ≠ backup: replication faithfully copies corruption/deletions too. You need point-in-time backups to recover from logical errors.

---

## 10. Key Takeaways

- **Reliability** = correctness over time; **availability** = uptime now. `Availability ≈ MTBF / (MTBF + MTTR)` — fast recovery (low MTTR) is often the cheapest lever.
- The **nines** quantify uptime; each extra nine costs ~10× more. Define **SLIs** (measure), **SLOs** (target), **SLAs** (contract), and run on an **error budget** to balance velocity and stability.
- Eliminate **SPOFs** with **redundancy** across independent failure domains; choose **active-active** (instant, costlier) vs **active-passive** (idle standby) per need, and **automate failover**.
- Contain failures with **timeouts, retries (exponential backoff + jitter), circuit breakers, and bulkheads**; degrade gracefully to protect the core.
- Use shallow **liveness/readiness** checks; avoid deep dependency checks that cause correlated outages.
- **Chaos engineering** proves resilience by injecting real failures.
- Plan **DR** around **RTO** (downtime tolerance) and **RPO** (data-loss tolerance); follow **3-2-1 backups** and *test restores*.

---
*Related: `14_distributed_systems.md` (failover/leader election, idempotent retries), `16_observability.md` (SLO alerting, health metrics).*
