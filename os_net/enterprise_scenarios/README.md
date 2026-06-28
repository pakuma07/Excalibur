# Enterprise Incident Scenarios & Runbooks 🚨

> **Audience:** staff/principal on-call. The concept docs in [`../operating_system/`](../operating_system/README.md)
> and [`../comp_networking/`](../comp_networking/README.md) teach *how the machinery
> works*. This folder is what you reach for **at 3am with a pager going off** — the
> real-world incident patterns, the exact commands to triage them, the root cause,
> the immediate mitigation, the permanent fix, and the guardrail that stops the
> repeat. Every scenario is one a staff/principal engineer is expected to have seen,
> diagnosed, and prevented.

These are not toy examples. Each is a production failure mode that has caused real
outages at scale, written as an actionable **runbook**.

---

## 📒 The runbooks

| # | Runbook | Incident classes |
|---|---------|------------------|
| 01 | [CPU & Memory Incidents](01_cpu_memory_incidents.md) | CFS throttling spikes, noisy neighbor, run-queue saturation, IRQ/softirq core theft, OOMKilled, memory leak vs RSS growth, page-cache thrash, NUMA regression, THP stalls, swap death |
| 02 | [I/O & Storage Incidents](02_io_storage_incidents.md) | fsync stalls, disk saturation, write-back stalls, noisy-disk neighbor, inode/space exhaustion, slow-disk tail latency, NFS hangs |
| 03 | [Concurrency Incidents](03_concurrency_incidents.md) | deadlock, lock convoy, false-sharing regression, thundering herd, connection-pool exhaustion, GIL contention |
| 04 | [Network Incidents](04_network_incidents.md) | retransmission storms, ephemeral-port exhaustion, accept-queue/SYN overflow, buffer bloat, DNS outage & stampede, TLS CPU, PMTUD blackhole, LB imbalance, keepalive mismatch, retry-storm metastability |
| 05 | [Cross-Layer Triage](05_cross_layer_triage.md) | "the service is slow" end-to-end method, a request's life latency map, the war-room playbook, postmortem template |

---

## 🧭 The universal triage method

Before any specific runbook, the staff-level loop. Most engineers thrash by
guessing; the discipline below finds root cause fast.

```
   1. SCOPE      What is broken, for whom, since when? (one service? one AZ? all?)
                 -> Check the deploy/change log FIRST. ~70% of incidents are a change.
   2. SIGNALS    Look at the four golden signals + the USE method, top-down:
                 latency / traffic / errors / saturation   (RED, service level)
                 Utilization / Saturation / Errors per resource (USE, host level)
   3. BISECT     Narrow the layer: client -> LB -> network -> host -> process ->
                 syscall -> resource. Halve the search space each step.
   4. MITIGATE   Stop the bleeding BEFORE root-causing: roll back, shed load,
                 fail over, raise a limit. Users first, curiosity second.
   5. ROOT CAUSE Reproduce, instrument (perf/eBPF/tcpdump), confirm causally.
   6. PREVENT    Postmortem -> the SYSTEMIC fix (the guardrail/alert/test that
                 makes this class of incident impossible or auto-caught).
```

> **The first question is always "what changed?"** A config push, a deploy, a
> traffic shift, a dependency's deploy, a cert expiry, a cron job, a scale event.
> Correlate the incident start time against every change feed before theorizing
> about kernel internals.

### The "is it the host or the network?" fork

```
   Symptom: requests are slow / failing
        |
   Is CPU/mem/disk saturated on the host?  --yes--> OS runbooks (01, 02, 03)
        | no
   Are there retransmits / RTT spikes / conn errors on the wire? --yes--> Net (04)
        | no
   Is a DOWNSTREAM dependency slow? (traces) --yes--> follow it; repeat there
        | no
   -> cross-layer triage (05): it's queueing, GC, lock contention, or fan-out tail
```

---

## ⏱️ Latency numbers to anchor every diagnosis

Keep these in your head — they tell you instantly whether a measured latency is
"a cache miss" or "a cross-continent round trip" (full table in
[Net 08](../comp_networking/08_network_performance_tuning.md) / the SRE latency list):

```
L1 ~1ns · mutex ~17ns · RAM ~100ns · SSD read ~16µs · same-DC RTT ~0.5ms
context switch ~1-5µs · disk seek ~2ms · cross-region RTT ~50-150ms · GC/CFS-throttle stall ~10-100ms
```

A 50 ms p99 on a same-DC call (RTT 0.5 ms) is **100× too slow** — the time is going
to queueing, a stall, or a slow dependency, not the wire. That instinct is the whole
game.

---

## 🔧 Have these installed before the incident

| Layer | First-reach tools |
|---|---|
| Host overview | `top`/`htop`, `vmstat 1`, `dstat`, `uptime` (load avg) |
| CPU | `perf top`, `perf record -g`, `pidstat 1`, `mpstat -P ALL 1` |
| Memory | `free -m`, `/proc/meminfo`, `smem`, `numastat`, `slabtop` |
| I/O | `iostat -xz 1`, `iotop`, `biolatency`/`biosnoop` (bcc) |
| Network | `ss -tin`, `ss -s`, `tcpdump`, `nstat`, `ethtool -S`, `mtr` |
| Tracing | `strace -f -T`, `bpftrace`, `funclatency`, flame graphs |
| Containers | `kubectl top`, `cpu.stat` (`nr_throttled`), `crictl stats` |

> If you're installing these *during* the incident, that's the first postmortem
> action item. Bake them into the base image. (See
> [OS 08 — Observability](../operating_system/08_linux_internals_observability.md).)

---

## How to use this folder

- **During an incident:** jump to the matching runbook, match the symptom, run the
  triage commands, apply the mitigation. Don't read top to bottom.
- **Before an incident (the real value):** read them as a catalog of failure modes
  so you *recognize* the pattern in seconds when it happens, and so you build the
  guardrails proactively.
- **In design review:** use the "Prevention" sections as a checklist — most of these
  incidents are designed-in and avoidable.

> Staff/principal engineers are measured not by how fast they type during an
> incident, but by how often the incident *doesn't happen* because they built the
> guardrail a year earlier. That is what the **Prevention** section of every
> scenario is for.
