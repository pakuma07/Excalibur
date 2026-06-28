# Google Dapper — Distributed Tracing at Scale

## Overview

**What it is.** Dapper is Google's production **distributed tracing** system: infrastructure that follows a single request as it fans out across many services and machines, stitching the per-service work into one end-to-end **trace** so engineers can see causality, latency breakdown, and where time goes in a request that touches dozens or hundreds of servers.

**Who built it & the seminal paper.** Built at Google starting ~2004 and described in the 2010 technical report **"Dapper, a Large-Scale Distributed Systems Tracing Infrastructure"** by **Benjamin H. Sigelman, Luiz André Barroso, Mike Burrows, Pat Stephenson, Manoj Plakal, Donald Beaver, Saul Jaspan, and Chandan Shanbhag**. It built on ideas from earlier systems (Magpie, X-Trace) but proved them at Google's scale and, crucially, in **continuous production use**.

**Why it mattered.** In a service-oriented architecture, a single user request (e.g., a web search) triggers a tree of RPCs across many teams' services. No single engineer understands the whole path, and the latency culprit is often buried deep. Dapper made the *entire* distributed call graph observable — with **low overhead** and **near-ubiquitous, transparent** instrumentation — which is exactly what made it usable as always-on infrastructure rather than a debugging toy.

---

## The Problem It Solved

A modern request looks like a tree, not a line:

```
            user query
                │
          ┌─────▼─────┐
          │ Frontend  │
          └─────┬─────┘
        ┌───────┼───────────┐
        ▼       ▼           ▼
   ┌────────┐ ┌──────┐  ┌──────────┐
   │ Search │ │ Ads  │  │ Spell    │
   └───┬────┘ └──────┘  └──────────┘
       │ (fans out to 100s of leaf shards)
   ┌───┼───┬───┬─── ... ────┐
   ▼   ▼   ▼   ▼             ▼
  shard shard shard ...    shard
```

Without tracing you face:
- **No end-to-end visibility** — logs are per-machine; you can't reconstruct one request's path across services owned by different teams.
- **Latency attribution is impossible** — is the slow p99 the frontend, a backend, a slow shard, network, or queueing?
- **Heterogeneity** — many languages, many teams, services you don't own. You can't ask every team to manually instrument.

Dapper's three explicit design goals:
1. **Low overhead** — must be cheap enough to run **always-on in production**; if it slows things down, teams disable it and it becomes useless.
2. **Application-level transparency** — engineers should get tracing **for free**, without modifying their code. Achieved by instrumenting the *shared infrastructure* (threading, control-flow, RPC libraries).
3. **Scalability** — work across Google's entire fleet for years.

A fourth, operational goal: traces should be available for analysis **quickly** (within minutes) to be useful in incidents.

---

## Architecture

### The trace / span model

```
Trace  (one request; identified by a globally-unique traceId)
└── is a TREE of Spans, linked by parent/child span ids

  [Span A: Frontend.handle]                         traceId=T
  | annotations: cs ─────────────────────── cr |
        │
        ├── [Span B: rpc Frontend→Search]   parentId=A, spanId=B
        │     | cs   sr ........ ss   cr |
        │            └── server side timing
        │
        ├── [Span C: rpc Frontend→Ads]      parentId=A, spanId=C
        │
        └── [Span D: rpc Frontend→Spell]    parentId=A, spanId=D

  cs = Client Send   sr = Server Recv   ss = Server Send   cr = Client Recv
  (network time ≈ (sr - cs) and (cr - ss); service time ≈ (ss - sr))
```

A **span** is a single unit of work (typically one RPC, but can be any block). Each span carries:
- a **trace id** (shared by all spans in the request),
- a **span id**,
- a **parent span id** (root span has none),
- **timestamps** and **annotations**.

Spans nest to form the **causal tree** of the request.

### Collection pipeline

```
   Instrumented binaries (RPC lib emits spans)
            │  write spans to LOCAL log files (async, off the request path)
            ▼
   ┌──────────────────┐   pull
   │  Dapper daemon   │◀──────  reads local trace logs on each machine
   │  (on every host) │
   └────────┬─────────┘
            │ ship
            ▼
   ┌──────────────────────────┐
   │ Collectors → Bigtable     │   one row per trace; columns = spans
   │ ("Dapper depot")          │
   └────────────┬──────────────┘
                │
        ┌───────┴────────┐
        ▼                ▼
  Dapper UI / API   Aggregation/analysis (MapReduce, DAPI)
```

Three-stage out-of-band pipeline: (1) spans written to **local logs**, (2) a per-host **Dapper daemon** collects them, (3) collectors write to **Bigtable**, where each trace is a single row keyed by trace id and each span is a column. The median collection latency was on the order of minutes (often ~15s of pipeline plus log-flush intervals). Critically, **trace data leaves the request path asynchronously** — the request never blocks on trace export.

---

## How It Works

### 1. Transparent instrumentation via shared libraries
Google's near-universal use of a **common RPC framework**, **common threading libraries**, and **common control-flow primitives** was the key enabler. Dapper instrumented *those* libraries, so:
- When a thread handles a request, the **trace context** is stored in thread-local storage and carried through callbacks/continuations.
- When code makes an RPC, the RPC library **propagates the trace context** to the callee and creates a child span automatically.

Result: most code is traced **without any change**. Only a tiny amount of common code needed instrumentation; application engineers got tracing for free. (Where teams wanted richer detail, a small annotation API let them add custom data.)

### 2. Trace context propagation
The unit that must travel with the request is the **trace context**: `(traceId, spanId, sampling flag)`. On every RPC, the client side attaches this context to the outgoing call metadata; the server side reads it, creates a child span with `parentId = caller's spanId`, and continues propagation downstream. This *propagation* — in-process (thread-local) and cross-process (RPC metadata) — is the mechanism that makes the distributed tree reconstructable. This is the direct ancestor of today's **W3C Trace Context** (`traceparent`) HTTP header.

### 3. The annotation model
Spans carry two kinds of annotations:
- **Timestamped (timing) annotations** — the canonical four RPC events: **cs** (client send), **sr** (server receive), **ss** (server send), **cr** (client receive). From these you derive network time vs. server time. Applications can also log their own timestamped events (e.g., "cache miss").
- **Key–value (tag) annotations** — arbitrary metadata (e.g., request size, query string, a feature flag). Dapper imposed limits to bound overhead.

### 4. Sampling — the linchpin of low overhead
Tracing *every* request at Google scale would generate prohibitive data volume and runtime cost. Dapper uses **head-based sampling**: the **sampling decision is made once, at the root** of the trace, and propagated as part of the trace context so the *entire* trace is consistently either fully traced or not traced at all (you never get partial trees).

- Early Dapper sampled a uniform fraction (e.g., **1 in 1024** requests for high-traffic services) and found this captured enough traces to be statistically useful while keeping overhead negligible.
- For **low-traffic** services a fixed low rate yields too few traces, so Dapper used **adaptive sampling**: target a desired *rate of sampled traces per unit time* rather than a fixed probability, sampling a higher fraction of low-volume services.
- A **second-tier sampling** stage at the collection/Bigtable write path further controlled total storage independent of the runtime sampling rate.

The deep insight: for **aggregate** performance analysis (where does latency come from across millions of requests), a small, statistically representative sample is sufficient; you do **not** need every request. This is *head-based* sampling — decide up front — as opposed to **tail-based** sampling (decide after seeing the whole trace, e.g., keep all slow/errored traces), which later systems added.

### 5. Overhead in practice
- **Runtime overhead** came from span creation and annotation; measured as small (a few percent on a microbenchmark, negligible end-to-end with sampling).
- **Trace collection** (writing logs, daemon, Bigtable) was kept off the critical path and bounded by sampling, so it consumed a small, controllable fraction of fleet resources and network.

### 6. Using the data
- **Dapper UI** for inspecting individual traces (the timeline/tree view every tracing UI now imitates).
- **DAPI (Dapper API)** and MapReduce over the Bigtable depot for fleet-wide aggregate analyses — e.g., "what's the service-time distribution of service X," "which dependency contributes to frontend p99," cost attribution, and service-dependency discovery.

---

## Key Innovations

1. **Transparent, infrastructure-level instrumentation** — trace for free by instrumenting shared RPC/threading/control-flow libraries instead of application code. The single biggest reason Dapper achieved ubiquity.
2. **Consistent head-based sampling propagated through the trace** — the whole trace is sampled-or-not as a unit, keeping overhead low while preserving complete trees. Made always-on production tracing feasible.
3. **The trace/span tree + annotation model** — a clean, durable data model (traceId, spanId, parentId, timing annotations, k/v tags) that the entire industry adopted essentially unchanged.
4. **Out-of-band, asynchronous collection** — request path never blocks on tracing; local logs → daemon → Bigtable, with results available in minutes.
5. **Aggregate analysis over sampled traces** — reframing tracing from "debug one request" to "statistically characterize the fleet."

---

## Data Model / APIs

### Conceptual span record
```text
Span {
  traceId      : 128-bit (conceptually) global id, shared across the request
  spanId       : id of this span
  parentSpanId : id of the span that caused this one (null for root)
  name         : e.g. "Frontend.Search.RPC"
  annotations  : [
     { ts: 1500ns, event: "cs" },        // client send
     { ts: 1900ns, event: "sr" },        // server receive
     { ts: 6200ns, event: "ss" },        // server send
     { ts: 6600ns, event: "cr" },        // client receive
     { ts: 3100ns, event: "cache_miss" } // app annotation
  ]
  tags         : { "query.size": 42, "shard": "doc-073" }
}
```

### Propagated context (the bytes that travel on each RPC)
```text
TraceContext { traceId, spanId, sampled(bool) }   // attached to RPC metadata
```

### Application annotation API (illustrative — the small "opt-in" surface)
```cpp
// Add a custom timestamped event to the current span (thread-local context):
tracer->Annotate("cache_miss");

// Attach key/value metadata:
tracer->SetTag("query.size", request.size());
```

### Modern equivalents (lineage)
```text
W3C Trace Context header:
  traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
               │  └ trace-id (16 bytes) ──────────┘ └ span-id ────┘ └ flags (sampled)

OpenTelemetry Span ≈ Dapper Span:
  TraceId, SpanId, ParentSpanId, Name, StartTime/EndTime,
  Events (≈ timestamped annotations), Attributes (≈ k/v tags), Status, Links
```

---

## Trade-offs & Limitations

| Decision | Benefit | Cost / Limitation |
|---|---|---|
| **Head-based sampling** (decide at root) | Low overhead; complete trees | You may **miss rare slow/error traces** — the decision is made before you know the outcome (motivated later **tail-based** sampling) |
| 1/1024 default sample | Negligible runtime cost | Poor for low-traffic or rare events; needed adaptive sampling |
| Transparent instrumentation via shared libs | Ubiquity for free | Depends on a **homogeneous infrastructure**; gaps where teams use non-instrumented code, async/queue/streaming paths, or kernel/network internals not captured |
| Out-of-band async collection | No request-path latency | Trace data is **eventually** available (minutes), not real-time; some loss acceptable |
| RPC-centric span model | Clean tree for synchronous RPC fan-out | Awkward for **fan-in, async messaging, batch, pub/sub** where causality isn't a simple tree (later systems add span *links*) |
| Sampling-based | Cheap aggregate stats | Not a complete audit log; can't guarantee any specific request was captured |
| Bounded annotation volume | Controls overhead/storage | Limits how much per-span detail you can attach |

Dapper also notes **security/PII** concerns (annotations can leak sensitive payloads) and that getting truly complete coverage of every code path is an ongoing, never-finished effort.

---

## Influence & Legacy

Dapper is the **direct intellectual ancestor of the entire distributed-tracing / observability industry**:

- **Zipkin** (Twitter, 2012) — the first widely-used open-source Dapper clone; introduced the now-standard B3 propagation headers and the trace-timeline UI.
- **Jaeger** (Uber, 2017; CNCF) — Go-based, OpenTracing-native; added adaptive sampling and a scalable collection backend.
- **OpenTracing** and **OpenCensus** — competing instrumentation API standards, both descended from Dapper's model, which **merged into OpenTelemetry (OTel)** — now the CNCF de-facto standard for traces, metrics, and logs. OTel's `Span`, `TraceId`, `SpanId`, events, attributes, and context propagation are recognizably Dapper's model, generalized and vendor-neutral.
- **W3C Trace Context** (`traceparent`/`tracestate`) — standardized cross-vendor propagation, the formalization of Dapper's "propagate the context on every hop."
- **Commercial APM/observability** — Lightstep (co-founded by Dapper's lead author Ben Sigelman), Honeycomb, Datadog APM, New Relic, AWS X-Ray, Google Cloud Trace — all implement the trace/span/sampling model.
- **Tail-based sampling**, **service maps / dependency graphs**, and **trace-driven SLO analysis** are all extensions of ideas Dapper raised.

---

## Lessons for Architects

1. **Make observability free to adopt.** Dapper's defining decision was instrumenting **shared infrastructure**, not asking every team to add code. Adoption is the hard part of any cross-cutting tool — design so the default path is instrumented automatically.
2. **Overhead is a feature.** A monitoring system that's expensive gets turned off, which makes it worthless. "Cheap enough to always be on" was a hard constraint that shaped every design choice (especially sampling).
3. **Sampling is legitimate — and the *kind* matters.** For aggregate performance questions, a small representative sample suffices. Decide consciously between **head-based** (cheap, may miss rare events) and **tail-based** (keeps anomalies, costs more) — they answer different questions.
4. **Context propagation is the core primitive.** The whole edifice rests on carrying a tiny `(traceId, spanId, sampled)` context through threads and across RPCs. Get propagation right and the rest follows; gaps in propagation are where traces break.
5. **A clean, minimal data model wins.** Trace/span/parent + timing annotations + k/v tags has survived ~20 years and an industry's worth of reimplementation largely unchanged. Invest in the model.
6. **Keep telemetry off the critical path.** Asynchronous, out-of-band collection (local logs → daemon → store) means tracing can fail or lag without harming the traced system.
7. **Homogeneity is leverage.** Dapper's transparency depended on Google's uniform RPC/threading stack. In heterogeneous environments you must invest in standards (OTel, W3C Trace Context) to recover that leverage.
