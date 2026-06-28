# Observability

> **Scope:** The three pillars (logs, metrics, traces), structured logging, metric types & methodologies (RED/USE, Prometheus), distributed tracing (spans, trace context, OpenTelemetry/Jaeger), dashboards & alerting (Grafana), SLO-based alerting, and correlation IDs.

---

## 1. Introduction: Monitoring vs Observability

**Monitoring** tells you *whether* the system is broken — you watch predefined dashboards and alerts for *known* failure modes ("is CPU > 90%?").

**Observability** is the property of being able to ask *new* questions about your system's internal state from its external outputs, **without shipping new code** — letting you debug *unknown* failure modes ("why are checkout requests from EU users on the new mobile app slow only after 6pm?").

Distributed systems fail in unpredictable ways, so you need observability, not just monitoring. It rests on **three pillars**.

```
        ┌──────────────────────────────────────────────┐
        │                OBSERVABILITY                   │
        ├──────────────┬───────────────┬────────────────┤
        │    LOGS      │    METRICS     │     TRACES     │
        │ what happened│ how much/how   │ where time went│
        │ (events)     │ fast (numbers) │ (request path) │
        └──────────────┴───────────────┴────────────────┘
```

---

## 2. The Three Pillars

| Pillar | Answers | Data shape | Cost / cardinality | Example tools |
|---|---|---|---|---|
| **Logs** | "What exactly happened in this event?" | Discrete, timestamped records (text/JSON) | High volume; cheap per item but grows fast | ELK/Opensearch, Loki, Splunk |
| **Metrics** | "How many / how fast / how full, over time?" | Numeric time series, aggregated | Cheap, low cardinality (don't put unbounded IDs in labels!) | Prometheus, Datadog |
| **Traces** | "Where did time go across services for *this* request?" | Causally linked spans per request | Often sampled | Jaeger, Tempo, Zipkin |

They complement each other: a **metric** alert fires ("error rate up"), a **trace** localizes which service/hop is slow or failing, and **logs** for that trace give the exact error and context.

---

## 3. Logging

### 3.1 Structured logging

Unstructured logs (`"User 42 failed login from 1.2.3.4"`) are human-readable but a nightmare to query at scale. **Structured logging** emits machine-parseable records (usually JSON) with consistent fields.

```python
import logging, json, time

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        # merge structured context (e.g., extra={"trace_id": ...})
        if hasattr(record, "context"):
            log.update(record.context)
        return json.dumps(log)

# Example output:
# {"ts":1719100000.1,"level":"ERROR","msg":"login failed",
#  "logger":"auth","user_id":42,"ip":"1.2.3.4","trace_id":"abc123","reason":"bad_password"}
```

Benefits: you can filter/aggregate by field (`level=ERROR AND user_id=42`), and downstream systems parse reliably.

### 3.2 Logging best practices

- **Log levels:** `DEBUG < INFO < WARN < ERROR < FATAL`. Production usually at INFO; raise to DEBUG temporarily.
- **Include a correlation/trace ID** in every log line (§7) so you can stitch a request across services.
- **Never log secrets/PII** (passwords, tokens, full card numbers) — see `17_security.md`. Redact.
- Prefer **sampling** high-volume debug logs; keep all errors.
- Logs are append-only events; emit them, don't try to make them metrics (use counters for counting).

---

## 4. Metrics

Metrics are numeric measurements aggregated over time. Four core types:

| Type | Behavior | Example | Operations |
|---|---|---|---|
| **Counter** | Only goes up (resets on restart) | `http_requests_total` | rate(), increase() |
| **Gauge** | Goes up and down | `memory_used_bytes`, `queue_depth` | current value, min/max/avg |
| **Histogram** | Buckets observations to compute distributions/percentiles | `request_duration_seconds` | quantiles (p50/p95/p99) |
| **Summary** | Client-side computed quantiles | latency summary | pre-computed quantiles |

> **Why histograms for latency?** Averages lie. A mean of 100 ms can hide that 1% of requests take 5 s. Track **p95/p99 percentiles** — the tail is what users feel. Histograms let you compute percentiles server-side and aggregate across instances; summaries cannot be aggregated.

### 4.1 Prometheus

The de-facto open-source metrics system. Model:
- **Pull-based:** Prometheus *scrapes* a `/metrics` HTTP endpoint on each target periodically.
- Time series identified by a **metric name + labels** (key/value dimensions).
- Queried with **PromQL**.

```python
# Python client (prometheus_client)
from prometheus_client import Counter, Histogram, start_http_server

REQUESTS = Counter("http_requests_total", "Total HTTP requests",
                   ["method", "status"])           # labels = dimensions
LATENCY  = Histogram("http_request_duration_seconds", "Request latency",
                     ["endpoint"])

@LATENCY.labels(endpoint="/checkout").time()        # times the block
def handle_checkout():
    REQUESTS.labels(method="POST", status="200").inc()
    ...

start_http_server(8000)   # exposes GET /metrics for Prometheus to scrape
```

```promql
# Request rate per second over 5m, by status:
sum(rate(http_requests_total[5m])) by (status)

# p99 latency from a histogram:
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))
```

> **Cardinality warning:** labels multiply time series. Putting `user_id` or `request_id` (unbounded values) in a label can create millions of series and blow up Prometheus. Keep label values low-cardinality (status code, endpoint, region) — high-cardinality identifiers belong in logs/traces.

### 4.2 RED and USE methods

Two complementary frameworks for *what to measure*.

**RED** — for **request-driven services** (what users experience):
- **R**ate — requests per second.
- **E**rrors — failed requests per second.
- **D**uration — latency distribution (p50/p95/p99).

**USE** — for **resources** (what's saturated):
- **U**tilization — % time the resource is busy.
- **S**aturation — queued/waiting work (the resource can't keep up).
- **E**rrors — error count for the resource.

```
RED  → services / APIs / endpoints      (the "symptoms" users feel)
USE  → CPU, memory, disk, network, pools (the "causes" / resource bottlenecks)
```

Use RED to detect user-facing problems and USE to find the resource causing them.

---

## 5. Distributed Tracing

In microservices a single user request fans out across many services. Tracing reconstructs that journey.

### 5.1 Spans and traces

- A **trace** = the whole journey of one request, identified by a **trace ID**.
- A **span** = one unit of work (one service handling part of the request), with a start/end time, a **span ID**, a **parent span ID**, and **attributes/tags** (e.g., `http.status_code`, `db.statement`).
- Spans form a tree/DAG via parent-child links.

```
Trace abc123 (total 220ms)
└─ span: api-gateway          [0ms ─────────────────────── 220ms]
   └─ span: order-service     [ 10ms ──────────── 200ms]
      ├─ span: payment-service[ 20ms ── 90ms]   ← 70ms here
      └─ span: db query       [ 95ms ─ 180ms]   ← 85ms here  (the slow hop!)
```

This waterfall instantly shows *where the time went* — something logs/metrics alone can't.

### 5.2 Trace context propagation

For spans to link across services, the trace context must travel with each request — typically via the **W3C Trace Context** standard HTTP header:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             │  └─ trace-id (32 hex) ──────────┘ └─ parent span-id┘ └ flags
             version
```

Each service reads `traceparent`, creates a child span, and passes an updated header downstream. Async systems propagate context through message headers too.

### 5.3 OpenTelemetry & Jaeger

- **OpenTelemetry (OTel):** the vendor-neutral CNCF standard — a set of APIs, SDKs, and a Collector for generating and exporting traces, metrics, and logs. Instrument once with OTel, export anywhere.
- **Jaeger / Zipkin / Tempo:** backends that store and visualize traces (the waterfall UI).

```python
from opentelemetry import trace
tracer = trace.get_tracer("order-service")

def place_order(req):
    with tracer.start_as_current_span("place_order") as span:
        span.set_attribute("order.id", req.order_id)
        charge_payment(req)        # creates a child span automatically
        # context auto-propagates via instrumented HTTP client
```

### 5.4 Sampling

Tracing every request is expensive at scale. **Sampling** keeps a subset:
- **Head-based:** decide at request start (e.g., keep 1%). Simple, but may miss the rare errors.
- **Tail-based:** decide after the trace completes (e.g., keep all errors + slow traces). Smarter, more infrastructure.

---

## 6. Dashboards & Alerting

### 6.1 Grafana dashboards

**Grafana** visualizes metrics (from Prometheus and others), logs (Loki), and traces (Tempo/Jaeger) on shared dashboards. Good dashboards are organized by RED/USE and answer "is the service healthy?" at a glance. Pair them with **runbooks**.

### 6.2 Alerting principles

- **Alert on symptoms, not causes.** Page on "users see errors / high latency" (RED), not on "CPU is 85%" — high CPU may be harmless. Resource metrics inform diagnosis, not paging.
- **Every page must be actionable** and **urgent**. If a human can't or needn't act now, it's a ticket or a dashboard, not a page.
- **Avoid alert fatigue.** Too many noisy alerts train responders to ignore them. Tune aggressively.
- Separate severities: **page** (wake someone) vs **ticket/warning** (handle in hours).

### 6.3 SLO-based alerting & burn rate

Instead of static thresholds, alert based on **error-budget burn rate** (see SLO/error budget in `15_reliability_availability.md`). The burn rate is how fast you're consuming the budget relative to "sustainable."

```
Burn rate = (observed error rate) / (allowed error rate from SLO)

burn rate = 1   → you'll exactly exhaust the 30-day budget in 30 days (OK)
burn rate = 14.4→ you'll exhaust a 30-day budget in ~2 days (URGENT, page now)
```

**Multi-window, multi-burn-rate** alerts (Google SRE workbook) combine a fast-burn alert (e.g., 14.4× over 1h → page) with a slow-burn alert (e.g., 3× over 6h → ticket). This catches both sudden outages and slow leaks while minimizing false pages.

| Burn rate | Time to exhaust 30d budget | Action |
|---|---|---|
| 1× | 30 days | none |
| 3× | ~10 days | ticket / investigate |
| 14.4× | ~2 days | page |

---

## 7. Correlation IDs

A **correlation ID** (a.k.a. request ID / trace ID) is a unique identifier attached to a request at the **edge** (gateway/load balancer) and propagated through **every** service, log line, and message it touches.

```
Client → [Gateway: generate correlation_id=abc] 
           → header X-Correlation-ID: abc → Service A (logs include abc)
             → Service B (logs include abc) → Queue (msg header abc) → Worker (logs abc)
```

Why it matters: when a user reports "my order failed at 14:03," you grep one ID across all services and reconstruct the full story. Without it, you're correlating timestamps across disjoint logs by hand.

- If you have tracing, the **trace ID serves as the correlation ID** — put it in every structured log line (the `trace_id` field from §3.1). This is the glue that unifies the three pillars: a trace links to its logs and metrics via shared IDs.

---

## 8. Key Takeaways

- **Monitoring** watches known failure modes; **observability** lets you investigate unknown ones from logs + metrics + traces — the **three pillars**.
- Use **structured (JSON) logs** with consistent fields and a trace ID; never log secrets.
- Know the **metric types** (counter, gauge, histogram, summary); use **histograms for latency percentiles** because averages hide the tail. Keep label **cardinality low** in Prometheus.
- Measure services with **RED** (Rate, Errors, Duration) and resources with **USE** (Utilization, Saturation, Errors).
- **Distributed tracing** (spans + propagated **W3C trace context**, via **OpenTelemetry** → Jaeger) shows where time goes across services; sample to control cost.
- Build **Grafana dashboards** around RED/USE; **alert on symptoms**, keep pages actionable, and prefer **SLO burn-rate** alerting to static thresholds.
- A propagated **correlation/trace ID** is the thread that ties logs, metrics, and traces into one coherent story.

---
*Related: `15_reliability_availability.md` (SLO/error budgets, health checks), `14_distributed_systems.md` (requests spanning many nodes).*
