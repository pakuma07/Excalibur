# 05 — Cross-Layer Triage: "The Service Is Slow"

> **Audience:** staff/principal incident commander. The previous runbooks are
> per-layer. This one is the **end-to-end method** for the vaguest, most common page —
> *"the service is slow"* — where you don't yet know the layer. It ties OS
> ([01](01_cpu_memory_incidents.md), [02](02_io_storage_incidents.md),
> [03](03_concurrency_incidents.md)) and network ([04](04_network_incidents.md))
> together, plus the war-room playbook and the postmortem template.

---

## 1. A request's life — where latency hides

Every layer adds latency; "slow" lives in one (or the sum) of these. Knowing the map
tells you where to instrument.

```
 client ──DNS──▶ TCP/TLS handshake ──▶ LB/proxy ──▶ network ──▶ server NIC
   |                                                                  |
   |                                                          softirq/accept queue
   |                                                                  v
   |                                                          app thread pool / queue
   |                                                                  |
   |                                       ┌──────────────────────────┤
   |                                       v          v               v
   |                                  CPU (sched)   memory        syscalls / I/O
   |                                  GC/throttle   page faults   disk / page cache
   |                                       |          |               |
   |                                       └────▶ downstream calls (DB, cache, RPC) ◀──┐
   |                                                  | (recurse: this whole map again) |
   ◀──────────────────── response ◀──────────────────┴─────────────────────────────────┘

 Latency budget at each hop (same-DC, healthy):
   DNS (cached) ~0      TLS full handshake ~1 RTT    LB ~sub-ms     net RTT ~0.5ms
   accept/queue ~0      CPU sched delay ~µs          syscall ~µs    page-cache read ~µs
   disk read ~16µs-2ms  downstream RPC ~its own map  GC/throttle stall ~10-100ms  ◀ tail killers
```

The two biggest **tail** contributors are almost always **(a) a stall** (GC, CFS
throttle, lock, fsync, page fault) and **(b) a slow downstream** (which recurses into
this same map on another host). Start by deciding which.

---

## 2. The top-down drill (USE + RED together)

Work **outside-in**: service signals first, then host resources, then the specific
subsystem. Don't start with `strace` on a random PID.

```
STEP 0  What changed?  deploy / config / traffic / dependency / cert / cron  (check first!)

STEP 1  SERVICE level (RED):  latency (p50/p99/p999), error rate, request rate
        - p99 bad but p50 fine?   -> a TAIL problem: stalls, fan-out, a slow shard
        - all percentiles bad?     -> systemic: saturation or a slow dependency
        - errors, not latency?     -> different runbook (5xx/timeouts/resets)

STEP 2  Is it US or a DEPENDENCY?  (distributed tracing is the fast path)
        - Trace a slow request: which span dominates?  our CPU? a downstream? the DB?
        - No tracing? -> compare our latency to each dependency's latency dashboard.
        -> If a downstream dominates: RECURSE this drill on THAT service.

STEP 3  HOST level (USE), per resource:  Utilization / Saturation / Errors
        - CPU:    mpstat / load vs cores / PSI cpu        -> runbook 01
        - Memory: free / PSI memory / RSS-vs-limit        -> runbook 01
        - Disk:   iostat await / PSI io                   -> runbook 02
        - Net:    ss -tin retrans / nstat / mtr           -> runbook 04
        - The PRESSURE STALL signals (/proc/pressure/*) are the fastest "is this
          resource the bottleneck?" check — they measure TIME LOST, not just usage.

STEP 4  PROCESS level:  where is the time IN the process?
        - on-CPU profile (perf/py-spy) -> a hot function?  (CPU-bound)
        - off-CPU profile (off-CPU flame graph) -> blocked on what? lock? I/O? (waiting)
        - thread dump -> all threads blocked on a downstream? (pool exhaustion, 03/04)

STEP 5  SYSCALL level (last):  strace -f -T / bpftrace
        - which syscall is slow (fsync? futex? recvfrom? mmap fault?) -> pinpoints the layer
```

> **The single most useful modern signal: PSI** (`/proc/pressure/{cpu,memory,io}`). It
> directly answers "how much time did we lose stalled on this resource?" — cutting
> STEP 3 from minutes of staring at utilization graphs to one `cat`.

---

## 3. The decision tree

```
"Service is slow"
  |
  ├─ p50 fine, p99/p999 bad ───────────────► TAIL problem
  |     ├─ periodic (~100ms cadence)?  ──► CFS throttling (01.1) or GC pauses
  |     ├─ clustered on some hosts?    ──► slow disk (02.5) / bad NIC path (04.3)
  |     ├─ on fan-out endpoints?       ──► one slow shard/replica; use HEDGING
  |     └─ lock/queue wait?            ──► off-CPU profile -> convoy/pool (03.2/03.5)
  |
  ├─ all percentiles bad ──────────────────► SYSTEMIC
  |     ├─ a resource saturated? (PSI)  ──► that resource's runbook (01/02/04)
  |     ├─ a downstream slow? (trace)   ──► recurse on the downstream
  |     └─ retry amplification?         ──► metastable; SHED LOAD (04.10)
  |
  ├─ errors (5xx/timeouts/resets), not latency
  |     ├─ resets / "reset by peer"     ──► idle-timeout ladder (04.4)
  |     ├─ "pool exhausted"             ──► slow downstream draining pool (03.5)
  |     ├─ OOMKilled / crashes          ──► memory (01.5)
  |     └─ connection refused/dropped   ──► accept queue (04.2) / capacity
  |
  └─ slow ONLY after a change
        └─ deploy/config/failover/scale ──► ROLL BACK first, diagnose second
```

---

## 4. Worked example — "checkout p99 went from 80ms to 900ms at 14:05"

A staff-level walk-through using the drill:

```
STEP 0  Change log: a config push to the pricing service at 14:03.  (suspect #1)
STEP 1  RED: p50 still 70ms, p99 900ms, errors flat.  -> TAIL problem, not systemic.
STEP 2  Trace a slow checkout: 800ms of the 900ms is in the `pricing.getQuote` span.
        -> It's the pricing DEPENDENCY. Recurse there.
STEP 3  On pricing hosts: CPU fine, but PSI io `full avg10` = 35% (high!). iostat
        await jumped to 40ms.  -> pricing is I/O-bound now.
STEP 4  pricing on-CPU profile: nothing hot. off-CPU: blocked in fsync.
STEP 5  strace: fsync() taking 30-50ms.  Root cause: the 14:03 config enabled
        synchronous logging of every quote to disk -> fsync per request (02.2).
MITIGATE: roll back the config (back to async/batched logging). p99 -> 80ms by 14:18.
PREVENT: durability/sync-write changes require a load test + an fsync-latency alert;
        add PSI io to the pricing dashboard; "fsync per request" is a design red flag.
```

Note how the drill localized a *checkout* symptom to an *fsync in a downstream
dependency* in five steps — without guessing.

---

## 5. The war-room playbook (incident command)

When it's a real, multi-person incident:

```
ROLES
  Incident Commander (IC)  — coordinates, decides, NOT in the weeds. Owns comms.
  Ops/Investigators        — run the drill (§2), report findings to IC.
  Comms                    — status page, stakeholders, on a cadence.
  Scribe                   — timestamps every action/finding (-> postmortem).

LOOP (every few minutes, IC-driven)
  1. State current impact + hypothesis out loud.
  2. MITIGATE first (roll back / shed / fail over) — restore users before root cause.
  3. Assign ONE investigation per person; avoid everyone chasing the same thread.
  4. Re-measure after every action; keep or revert.
  5. Update comms on the cadence.

RULES
  - Mitigate before diagnose. Users first.
  - One change at a time, announced — so you know what worked.
  - The latest deploy is the prime suspect; rolling back is cheap and reversible.
  - No blame in the channel. Capture facts; judgment comes later.
  - If you've been stuck >N minutes on a hypothesis, the IC reassigns it.
```

> **Mitigate, then diagnose** is the hardest discipline to hold and the most
> important. Curiosity ("but *why*?") during an active outage costs user-minutes —
> stop the bleeding, *then* satisfy curiosity in the postmortem.

---

## 6. Blameless postmortem template

```
# Postmortem: <short title>     Date: <date>   Severity: <SEV1/2/3>

## Impact
  Who/what was affected, for how long, magnitude (req lost, $, SLO burn).

## Timeline (UTC)
  14:03  config push to pricing (the trigger)
  14:05  checkout p99 alert fires
  14:09  IC engaged; identified TAIL problem via tracing
  14:14  rolled back pricing config (mitigation)
  14:18  p99 recovered
  ...

## Root cause
  The technical chain of causation (the "5 whys"), ending at the SYSTEMIC gap —
  not "a person made a mistake" but "the system allowed the mistake to reach prod
  and stay undetected for N minutes."

## What went well / poorly
  Detection time, mitigation time, tooling gaps, comms.

## Action items (each: owner, due date, tracked)
  [ ] Add fsync-latency + PSI-io alert to pricing            (owner, date)
  [ ] Require load test for any durability/sync-write change (owner, date)
  [ ] Make "fsync per request" a lint/review check           (owner, date)
  [ ] Reduce detection time: alert on p99 not just p50       (owner, date)
```

The measure of a good postmortem is whether the **action items make this entire
*class* of incident impossible or auto-detected** — not whether they fix the one bug.

---

## 7. The prevention mindset (what separates staff/principal)

The runbooks in this folder exist so you can *fix* incidents fast — but the real
staff/principal contribution is that the incident **doesn't happen**:

| Reactive (good) | Proactive (staff/principal) |
|---|---|
| Diagnose CFS throttling at 3am | Alert on throttle ratio; design review flags CPU limits on latency pods |
| Find the fsync stall | fsync-latency SLO; durability changes require a load test |
| Discover port exhaustion | Connection pooling is a platform default |
| Trace a retry storm | Retry budgets + circuit breakers are library defaults |
| Notice the expired cert | Automated rotation; alert weeks ahead |
| Localize the slow disk | SMART monitoring + auto-drain; hedged reads |

> Every "Prevention" line in runbooks 01–04 is a guardrail you can build **today**,
> before the page. A staff engineer's incident count goes *down* over time not because
> they're luckier, but because each incident becomes a guardrail. That is the job.

---

## Key takeaways

1. **Start with "what changed?"** — most incidents are a change; the latest deploy is
   the prime suspect, and rollback is cheap.
2. **Work top-down: RED (service) → trace (us vs dependency) → USE/PSI (host) →
   profile (process) → strace (syscall).** Don't start deep.
3. **PSI (`/proc/pressure/*`) is the fastest bottleneck signal** — it measures *time
   lost*, not just utilization.
4. **p99-bad-but-p50-fine = a tail problem** (stall or fan-out); **all-percentiles-bad
   = systemic** (saturation or slow dependency) — the split picks your path.
5. **Mitigate before you diagnose** — roll back, shed, fail over; restore users, then
   root-cause.
6. **Run a disciplined war room** (IC, one change at a time, scribe) and a **blameless
   postmortem** whose action items kill the whole *class* of incident.
7. **The job is prevention** — turn every incident into a guardrail (alert, default,
   review check, test) so it can't recur silently.

> Back to the [runbook index](README.md) · concept depth in
> [`../operating_system/`](../operating_system/README.md) and
> [`../comp_networking/`](../comp_networking/README.md).
