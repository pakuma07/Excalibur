# 05 — Observability & SLO-Driven Operations

> **Audience:** Staff/principal engineers and SREs running services at scale. This chapter is the **application/service + SRE process** layer: the telemetry you *build into* services and the SLO discipline you *run the org by*. The low-level host/network layer (perf/eBPF/USE on a single box) lives in the os_net reference — see [../os_net/operating_system/08_linux_internals_observability.md](../os_net/operating_system/08_linux_internals_observability.md). Don't conflate the two: a green CPU graph tells you nothing about whether checkout works.

---

## 1. Monitoring vs Observability

**Monitoring** answers *known-unknowns*: questions you knew to ask in advance, encoded as dashboards and alerts ("is error rate > 1%?"). **Observability** answers *unknown-unknowns*: questions you didn't anticipate, answered by exploring high-cardinality, high-dimensional telemetry after the fact ("why are requests from API key `X` in region `eu-west-1` on app version `4.2.1` slow *only* when hitting shard 7?").

Monoliths rarely needed observability: one process, one stack trace, one log file, `top` on one box. **Microservices forced the shift.** A single user request now fans out across 20–200 services owned by 20 teams. No one holds the whole picture in their head; the failure mode is *emergent*. You can no longer pre-enumerate the dashboards you'll need, so you must be able to slice raw telemetry by *any* dimension you didn't think to graph.

> **Rule of thumb:** Monitoring tells you *that* something is wrong (and pages you). Observability lets you ask *why* without shipping new code to add a log line. You need both. SLOs (Section 6) are monitoring; high-cardinality traces are observability.

---

## 2. The Three Pillars (+ a Fourth)

| Pillar | What it is | Cost model | Cardinality | Best for |
|--------|------------|------------|-------------|----------|
| **Metrics** | Aggregated numeric time-series | Cheap; cost scales with *unique series* | **Low** — keep label sets bounded | Dashboards, alerts, SLOs, trends |
| **Logs** | Discrete structured events | Expensive at volume; sample/index cost | High (free-text) | Forensics, audit, the "what exactly happened" |
| **Traces** | Causal request graph across services | Moderate; sampling-driven | Very high (per-request) | "Why was *this one* request slow?" |
| **Profiles** | CPU/heap/lock attribution over time | Moderate (continuous profiling) | High | "Which *line of code* burned the CPU?" |

### 2.1 Metrics — counters, gauges, histograms

```python
# RIGHT: bounded label cardinality + a histogram for latency
from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter(
    "http_requests_total", "Total HTTP requests",
    ["method", "route", "status"],   # route = TEMPLATE "/users/{id}", NOT the raw path
)
INFLIGHT = Gauge("http_inflight_requests", "In-flight requests")
LATENCY = Histogram(
    "http_request_duration_seconds", "Request latency",
    ["route"],
    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10),  # tuned to YOUR SLO
)

@LATENCY.labels(route="/users/{id}").time()
def handle(req):
    REQUESTS.labels(req.method, "/users/{id}", "200").inc()
```

```python
# WRONG: cardinality bomb — user_id and raw path explode the series count
REQUESTS = Counter("http_requests_total", "...", ["user_id", "full_url"])
# 10M users x 1000 paths = 10B series. Your TSDB OOMs; your bill detonates.
```

> **Cardinality is the metrics tax.** Cost scales with *number of unique label combinations*, not request volume. Never label with unbounded values (user IDs, emails, full URLs, timestamps). Those belong in traces/logs.

### 2.2 Logs — structured, sampled, correlated

```python
# RIGHT: structured JSON + correlation/trace IDs, so logs join to traces
log.info("payment_authorized", extra={
    "trace_id": ctx.trace_id, "span_id": ctx.span_id,
    "order_id": order.id, "amount_cents": amount, "psp": "stripe",
})
# WRONG: log.info(f"Authorized payment for order {order.id} amount {amount}")
#   -> ungreppable, unqueryable, no trace linkage.
```

At scale, logging *everything* is the most common runaway cost line. **Sample** (e.g. keep 100% of errors, 1% of success), **structure** (JSON, not prose), and always carry a **correlation ID** (propagate the trace ID) so a request's logs across N services join up.

### 2.3 Traces — spans, context propagation, sampling

A **trace** is a tree of **spans**; each span = one operation with start/end + attributes. **Context propagation** carries the trace ID across process/network boundaries (HTTP headers, message metadata via W3C `traceparent`). Sampling:

- **Head-based:** decide at trace start (cheap, simple) — but you decide *before* you know if the request was interesting (errored/slow). You'll drop the failures you most want.
- **Tail-based:** buffer spans, decide *after* the trace completes (keep all errors + slow traces, sample the boring ones). More valuable, needs a collector with memory/buffering. Preferred for production debugging.

### 2.4 Correlation is the whole point

Pillars are near-useless in isolation; their value is the *joins*. **Exemplars** attach a trace ID to a specific histogram bucket sample — click the p99 spike on a metrics dashboard, jump straight to an exemplar trace of a slow request. Trace IDs in logs let you pivot trace → logs → metrics in one investigation.

**Profiling** is the emerging *fourth pillar* (continuous profiling: pprof/eBPF-based). It answers "which code path burned CPU/allocated memory during the incident window" — the host-level mechanics live in [../os_net/operating_system/08_linux_internals_observability.md](../os_net/operating_system/08_linux_internals_observability.md).

---

## 3. OpenTelemetry — the vendor-neutral standard

**OpenTelemetry (OTel)** is the CNCF standard for *generating* and *shipping* telemetry: one set of APIs/SDKs + a wire protocol (OTLP) + the **Collector**. Instrument once against OTel; switch backends (Prometheus, Tempo, Jaeger, vendor X) without touching app code.

```yaml
# otel-collector-config.yaml — receive OTLP, tail-sample, fan out to backends
receivers:
  otlp: { protocols: { grpc: {}, http: {} } }
processors:
  batch: {}
  tail_sampling:                  # keep all errors + slow; sample the rest
    policies:
      - name: errors,   type: status_code, status_code: { status_codes: [ERROR] }
      - name: slow,     type: latency,     latency: { threshold_ms: 500 }
      - name: sample-ok type: probabilistic, probabilistic: { sampling_percentage: 5 }
exporters:
  prometheus: { endpoint: "0.0.0.0:8889" }   # metrics
  otlphttp:   { endpoint: "http://tempo:4318" }  # traces
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [tail_sampling, batch], exporters: [otlphttp] }
    metrics: { receivers: [otlp], processors: [batch], exporters: [prometheus] }
```

- **Auto-instrumentation:** language agents/bytecode hooks instrument common libs (HTTP servers, DB drivers, gRPC) with zero code. Get this for free first — instant baseline traces.
- **Manual instrumentation:** add spans/attributes for *your* business logic (the queue depth, the cache hit, the tenant ID). Auto gets you 80%; the incident-cracking detail is always manual.

> **Opinion:** Run the Collector as a sidecar/agent + gateway tier. Never let app code talk to a vendor's API directly — the Collector is your seam for sampling, redaction, and backend swaps.

---

## 4. Golden Signals, RED, USE

Three lenses on the same goal — pick by *what you're measuring*:

| Framework | Signals | Scope | Use when |
|-----------|---------|-------|----------|
| **Golden Signals** | Latency, Traffic, Errors, Saturation | Any user-facing system | The canonical starting four for a service |
| **RED** | **R**ate, **E**rrors, **D**uration | **Per request-driven service** | Microservice request handlers — your default |
| **USE** | **U**tilization, **S**aturation, **E**rrors | **Per resource** (CPU, disk, queue, pool) | Hardware/resources/finite pools |

- **RED** is your default for the application layer — every service emits rate/errors/duration, and you get a uniform dashboard template across the fleet.
- **USE** is resource-oriented (a disk, a thread pool, a connection pool). Host-level USE (perf/eBPF) is covered in [../os_net/operating_system/08_linux_internals_observability.md](../os_net/operating_system/08_linux_internals_observability.md); apply the *same* USE thinking to app-level resources (DB connection pool saturation, worker queue depth).
- **Golden Signals** ≈ RED + Saturation. Saturation ("how full") is the leading indicator RED misses — a service can have great rate/errors/duration right up until the pool saturates and falls off a cliff.

See [../os_net/enterprise_scenarios/05_cross_layer_triage.md](../os_net/enterprise_scenarios/05_cross_layer_triage.md) for golden signals applied top-to-bottom during live triage.

---

## 5. Percentiles, Not Averages

**Averages lie.** A 50ms *mean* latency can hide a p99 of 4s — and at scale, p99 *is* a large fraction of your users. With fan-out, it's worse: a request touching 100 services, each with a 1% chance of a slow response, is **~63%** likely to hit *at least one* slow backend. **Tail latency dominates the user experience** of fan-out systems.

| Percentile | Reads as | Why it matters |
|-----------|----------|----------------|
| p50 | typical request | sanity baseline; do not alert on it |
| p95 | most users' worst | UX target for many SLOs |
| p99 | unhappy 1% | the standard reliability target |
| p999 | rarest tail | matters at billions of req (and for whales) |

### 5.1 Histograms over pre-computed percentiles

**You cannot average percentiles.** `avg(p99 of A, p99 of B) ≠ p99(A ∪ B)`. If your agents export a *pre-computed* p99 per box, you can never correctly aggregate across the fleet or re-window. Export **histograms** (bucketed counts) and compute percentiles at query time — they're additive:

```promql
# RIGHT: aggregate raw histogram buckets across all instances, THEN take the quantile
histogram_quantile(0.99,
  sum by (le, route) (rate(http_request_duration_seconds_bucket[5m])))

# WRONG: averaging a pre-aggregated p99 gauge -> statistically meaningless
avg(http_request_p99_seconds)
```

### 5.2 Coordinated omission

Many load-test/latency tools only measure requests that *got in*. When the system stalls, the requests that *would have been slow* are never sent or never timed — so the tool reports a rosy tail while real users wait. **Coordinated omission** systematically under-reports the worst latencies. Use tools that correct for it (back-pressure-aware, e.g. HdrHistogram-based) and measure from the *client's* perspective.

---

## 6. SLI / SLO / SLA / Error Budgets — the SRE core

| Term | Definition | Owner |
|------|------------|-------|
| **SLI** | A *measured* indicator: good events / valid events (e.g. % requests < 300ms) | Engineering |
| **SLO** | The *internal target* for an SLI (e.g. 99.9% over 28 days) | Eng + Product |
| **SLA** | The *contractual* promise to customers (+ penalties); always looser than the SLO | Legal/Sales |
| **Error budget** | `1 − SLO` — the allowed unreliability | Shared currency |

### 6.1 Good SLIs

- **Request-based:** `good / valid` requests (the workhorse). Define "good" precisely (status 2xx/3xx **and** latency < threshold) and "valid" (exclude health checks, exclude requests you don't control).
- **Journey-based:** measure the *user journey* ("checkout succeeds"), not just one endpoint. A 99.9% per-service SLO can still mean a broken multi-step flow.
- **Symptom-oriented:** measure what the *user* feels, not internal proxies.

### 6.2 The "how many nines" cost curve

| SLO | Downtime / 30 days | Error budget | Reality |
|-----|--------------------|--------------|---------|
| 99% | ~7.2 h | 1% | cheap; fine for internal tools |
| 99.9% | ~43 min | 0.1% | typical good SaaS target |
| 99.99% | ~4.3 min | 0.01% | expensive; needs redundancy + automation |
| 99.999% | ~26 s | 0.001% | rarely justified; cost goes vertical |

> **Each extra nine is roughly an order-of-magnitude more cost.** Don't pick nines by vanity — pick the lowest SLO your users won't notice, and spend the saved engineering elsewhere. An SLO of 100% is a bug: it forbids all change and all maintenance.

### 6.3 The error budget — the shared currency

The error budget reframes reliability vs velocity from a religious war into an **accounting problem**. Budget remaining? **Ship features, take risks, run chaos experiments.** Budget exhausted? **Freeze risky launches, pour effort into reliability** until you're back in budget. It aligns dev and SRE incentives on one number instead of arguing about feelings.

### 6.4 Burn-rate alerts (multi-window, multi-burn-rate)

Static thresholds ("error rate > 1%") are bad alerts: too tight = flapping, too loose = you miss slow burns. Alert instead on **how fast you're burning the error budget**. *Burn rate* = (rate of bad events) / (rate allowed by the SLO). Burn rate 1 = exactly on pace to exhaust the budget over the window; burn rate 14.4 = you'll exhaust a 30-day budget in ~2 days.

| Severity | Burn rate | Long window | Short window (guard) | Budget consumed |
|----------|-----------|-------------|----------------------|-----------------|
| **Page** (fast burn) | 14.4 | 1 h | 5 m | 2% in 1 h |
| **Page** (medium) | 6 | 6 h | 30 m | 5% in 6 h |
| **Ticket** (slow burn) | 1 | 3 d | 6 h | 10% in 3 d |

The **short window must also be firing** before you page — that confirms the problem is *still happening* and prevents alerting on a burn that already stopped.

```yaml
# Prometheus: page on fast burn (14.4x) — multi-window, multi-burn-rate
# error budget for a 99.9% SLO => budget = 0.001
- alert: ErrorBudgetFastBurn
  expr: |
    (job:slo_errors:ratio_rate1h{job="checkout"}  > (14.4 * 0.001))
      and
    (job:slo_errors:ratio_rate5m{job="checkout"}  > (14.4 * 0.001))
  labels: { severity: page }
  annotations:
    summary: "Checkout burning error budget 14.4x (2% in 1h)"
    runbook: "https://runbooks.internal/checkout/slo-burn"
```

---

## 7. Alerting Done Right

- **Alert on symptoms, not causes.** Page on *SLO burn* (users hurting), not on "CPU > 80%" (a cause that may be harmless). High CPU with a healthy SLO is not an emergency.
- **Page only on user-impacting AND actionable.** If there's nothing the on-call can do *right now*, it's not a page — it's a ticket or a dashboard.
- **Every page should be novel and actionable.** If the same alert fires nightly and the action is always "ack and ignore," it is noise — delete it or fix the root cause.
- **Runbook link in every alert.** A page at 3am without a runbook is cruelty and a slow MTTR.
- **Severity tiers:** `page` (wake a human, SLO at risk) → `ticket` (next business day) → `info` (dashboard/log only).

> **Alert fatigue is the silent reliability killer.** When on-call gets 100 pages/night, they stop reading them — and the *one* real page drowns. Fatigue is a P1 reliability risk, not a nuisance. Track pages-per-shift and treat a noisy alert as an incident in its own right.

```yaml
# WRONG: cause-based, non-actionable, no runbook -> fatigue factory
- alert: HighCPU
  expr: node_cpu_usage > 0.8
  for: 1m
# RIGHT: see Section 6.4 — symptom-based SLO burn with runbook + short-window guard
```

---

## 8. On-Call Practice

- **Rotation design:** **follow-the-sun** across regions so no one is paged at 3am routinely. If you can't, compensate night shifts.
- **Sustainable load:** target a hard cap (e.g. **≤ 2 incidents per shift**). Above that, the rotation is a bug — stop feature work and fix the noise. Burnout destroys retention and reliability.
- **Primary/secondary:** primary responds; secondary backs up missed pages and handles parallel incidents. Clear, automated **escalation policy** (page → secondary → manager) with bounded timeouts.
- **Handoff:** explicit shift handoff covering open incidents, ongoing risks, and silenced alerts. Nothing should fall through the cracks at the boundary.
- **Compensation & sustainability:** on-call is real work — compensate it. Unpaid heroics are not a strategy.
- **The response path:** **alert → runbook → mitigate → escalate if needed.** Mitigate first (stop the bleeding: roll back, drain, fail over), diagnose second. Deep incident handling, comms, and postmortems are [06 — Incident Management & Postmortems](06_incident_management_postmortems.md).

A clean alert → runbook → rollback path depends on safe, reversible releases — see canary analysis in [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md).

---

## 9. Capacity & Cost Observability

You can't operate sustainably if you can't see **utilization**, **headroom**, and **who's paying**.

- **Utilization vs saturation:** utilization (how busy) trends toward capacity; saturation (queueing/waiting) is the cliff. Watch both — saturation is the leading indicator (Section 4, USE).
- **Headroom:** maintain explicit headroom (e.g. operate at ≤ 60% of peak capacity) so a region failover or traffic spike doesn't tip you over. Track *time-to-saturation* at current growth.
- **Cost attribution:** tag telemetry and spend by team/service/tenant. Per-request and per-tenant cost turn "the cloud bill is too high" into an actionable, ownable number — and surfaces the cardinality/log-volume offenders from Section 2.

---

## 10. Symptom / Cause / Fix

**"We average our latency and it looks fine, but users are angry."**
- *Symptom:* Mean latency green; support tickets red.
- *Cause:* The average hides the tail; p99 (where real users live, amplified by fan-out) is awful.
- *Fix:* Alert and SLO on **p99/p999 from histograms** (Section 5). Add exemplars to jump from the p99 spike to a slow trace.

**"100 alerts/night and we ignore all of them."**
- *Symptom:* Pager floods; on-call acks reflexively; real incidents get missed.
- *Cause:* Cause-based, non-actionable, threshold alerts (alert fatigue, Section 7).
- *Fix:* Delete cause-based alerts. Page only on **SLO burn-rate** (symptom + actionable + runbook). Track pages/shift; treat a noisy alert as a bug.

**"We have logs but can't answer why *one* request was slow."**
- *Symptom:* Mountains of logs, no causal story across services.
- *Cause:* No distributed tracing; no correlation IDs; logs don't join.
- *Fix:* OpenTelemetry tracing with **context propagation** + **trace IDs in structured logs** (Sections 2–3). Tail-sample to keep the slow/errored traces.

---

> Next: [06 — Incident Management & Postmortems](06_incident_management_postmortems.md) — when the burn-rate alert fires, the budget is gone, and the page wakes you: incident command, severity, comms, mitigation under pressure, and the blameless postmortem that turns one outage into systemic learning.
