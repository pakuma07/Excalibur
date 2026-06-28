# ML / AI Infrastructure for Architects

> Staff/Principal deep-dive. Increasingly, "system design" includes ML systems. You don't need to train a transformer from scratch, but you *do* need to reason about feature stores, serving latency, training-serving skew, vector search, RAG, LLM serving economics, and drift. This document treats ML as a *systems* problem.

---

## 1. Intro & Why It Matters

The hard part of production ML is **not the model** — it's everything around it. The famous Sculley et al. paper ("Hidden Technical Debt in Machine Learning Systems," NeurIPS 2015) made the point with a diagram: the ML code is a tiny box surrounded by a vast field of data collection, feature extraction, serving infra, monitoring, configuration, and process-management glue.

```
   ┌────────┐ ┌──────────────┐ ┌───────────────┐ ┌─────────────┐
   │  Data  │ │  Feature     │ │  Data         │ │ Monitoring  │
   │ collect│ │  extraction  │ │  verification │ │             │
   └────────┘ └──────────────┘ └───────────────┘ └─────────────┘
   ┌───────────────────────────────┐  ┌───────────────────────┐
   │   Serving infrastructure       │  │   Resource management │
   └───────────────────────────────┘  └───────────────────────┘
                       ┌──────────┐
                       │ ML CODE  │  ← the part everyone focuses on
                       └──────────┘   (often <5% of total system)
```

For an architect, the failure modes that matter are *systems* failures: **training-serving skew** (the model sees different features in production than in training), **silent data drift** (the model degrades without any error), **latency/cost blowups** in serving (especially LLMs), and **reproducibility** (which data + which code + which params produced this model?). This document gives you the vocabulary and mechanics to design for those.

---

## 2. The ML Lifecycle & MLOps

MLOps applies DevOps discipline to ML, with extra axes: you version **code + data + model + config**, and you must **monitor for statistical decay**, not just crashes.

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │                          MLOps LIFECYCLE                               │
   │                                                                        │
   │  Data ──▶ Feature  ──▶  Train/    ──▶  Evaluate ──▶ Register ──▶ Deploy │
   │  ingest   engineering    Experiment      (offline    (model     (serve)│
   │   ▲          │            (tracked)       metrics)    registry)    │    │
   │   │          ▼                                                     ▼    │
   │   │     Feature Store ◀──────────────────────────────────── Monitor    │
   │   │     (offline+online)                                   (drift,      │
   │   │                                                         quality,    │
   │   └─────────────── retrain trigger ◀────────────────────── perf, cost) │
   └──────────────────────────────────────────────────────────────────────┘
```

Three pillars distinguishing MLOps from plain CI/CD:

1. **Reproducibility / lineage** — every model artifact is traceable to the exact data snapshot (DVC, lakeFS, Delta/Iceberg time travel), code commit, hyperparameters, and environment. Experiment tracking (MLflow, Weights & Biases) records this. Without it, "why did the model regress?" is unanswerable.
2. **Continuous training (CT)** — pipelines retrain on fresh data on a schedule or trigger (drift detected, performance drop). Orchestrated by Airflow, Kubeflow Pipelines, Flyte, or Metaflow.
3. **Continuous monitoring** — beyond uptime: prediction distributions, input drift, label delay, business KPIs.

**Maturity levels** (Google's framing): *Level 0* = manual, notebook-to-prod handoff; *Level 1* = automated training pipeline + continuous training; *Level 2* = full CI/CD/CT automation with automated pipeline deployment and testing.

---

## 3. Feature Stores

A **feature store** is a system for defining, computing, storing, and serving features consistently for both training and inference. It exists to solve one specific, expensive bug class: **training-serving skew**.

### 3.1 Training-serving skew

Skew is any difference between the feature values a model sees at *training* time and *serving* time. Three causes:

1. **Different code paths.** Training computes `avg_purchase_30d` in a Spark batch job (one implementation); serving recomputes it in a Java service (a *different* implementation). The two drift apart. *The feature store fixes this by computing a feature once and serving it to both.*
2. **Time-travel / label leakage.** During training you must reconstruct what a feature value *was at the time of the historical event* — not its current value. Joining "current" feature values onto past labels leaks future information and produces a model that looks great offline and fails online. This is **point-in-time-correct join**.
3. **Skewed pipelines** — data arriving late, different aggregation windows online vs offline.

### 3.2 Online vs offline store

```
                       ┌─────────────────────────────────────┐
   Raw data ──▶ Feature │   FEATURE PIPELINE (compute once)    │
   (streams,   transforms└───────────┬──────────────┬─────────┘
    batch)                           │              │
                              (batch / backfill) (streaming / fresh)
                                      ▼              ▼
        ┌──────────────────────────────────┐  ┌───────────────────────────┐
        │  OFFLINE STORE                    │  │  ONLINE STORE             │
        │  (data warehouse / lake:          │  │  (low-latency KV:         │
        │   BigQuery, Snowflake, S3/Parquet)│  │   Redis, DynamoDB, etc.)  │
        │                                   │  │                           │
        │  • huge, historical               │  │  • latest value per entity│
        │  • point-in-time-correct joins    │  │  • single-digit-ms reads  │
        │  • used for TRAINING & backfill   │  │  • used for SERVING        │
        └──────────────────────────────────┘  └───────────────────────────┘
                       ▲                                 ▲
                       │ build training dataset          │ get_online_features()
              ┌────────┴─────────┐              ┌─────────┴──────────┐
              │ Training pipeline │              │ Inference service  │
              └──────────────────┘              └────────────────────┘
```

- **Offline store**: large columnar storage (Parquet on S3, BigQuery, Snowflake) optimized for *throughput* and historical point-in-time queries. Feeds training and batch scoring.
- **Online store**: low-latency key-value (Redis, DynamoDB, Cassandra, ScyllaDB) holding the *latest* feature vector per entity, optimized for *p99 latency*. Feeds real-time inference.
- The **same feature definition** populates both (often a "dual-write" or materialization job), guaranteeing consistency.

Tools: **Feast** (open-source, store-agnostic), **Tecton** (managed, streaming-first), Databricks Feature Store, Vertex/SageMaker Feature Store. Be aware: feature stores add real operational complexity — they pay off when features are *shared across teams/models* and freshness matters; a single-model batch use case often doesn't need one.

### 3.3 Feature freshness tiers

- **Batch** (hours/days): demographic aggregates, recomputed nightly.
- **Streaming / near-real-time** (seconds/minutes): "transactions in last 5 min," computed via Flink/Spark Streaming/Kafka Streams into the online store.
- **Request-time / on-demand**: computed from the request payload itself (e.g., distance between request lat/long and a stored merchant location). Must use *identical* transform logic offline and online — the feature store provides "on-demand transformations" to enforce this.

---

## 4. Model Serving

### 4.1 Batch vs real-time inference

| | Batch (offline) scoring | Real-time (online) serving |
|---|---|---|
| Pattern | score all rows, write to a table | score one request, return synchronously |
| Latency | minutes–hours (irrelevant) | p99 in ms |
| Throughput | maximize (huge) | bounded by latency SLO |
| Infra | Spark/Beam jobs, scheduled | always-on service, autoscaled |
| Example | nightly churn scores | fraud check at checkout |
| Cost lever | spot/preemptible, big batches | right-sizing, batching, caching |

A common third pattern is **streaming inference** (score events off a Kafka topic with Flink) — real-time semantics, async delivery.

### 4.2 Real-time serving architecture

```
   Client ─▶ API Gateway ─▶ Inference Service ─┬─▶ Feature Store (online) : fetch features
                                │              ├─▶ Model (in-process or remote)
                                │              └─▶ Feature logging (for monitoring + future training)
                                ▼
                          Response (prediction + version + scores)
```

Key concerns:

- **Model server**: dedicated serving runtimes give you batching, versioning, multi-model hosting, metrics, and hardware acceleration out of the box:
  - **NVIDIA Triton Inference Server** — multi-framework (TensorRT, ONNX, PyTorch, TF, Python), **dynamic batching**, concurrent model execution, GPU sharing. The workhorse for GPU serving.
  - **TensorFlow Serving** — mature, TF/SavedModel-focused, gRPC + REST, model version management.
  - **KServe** (formerly KFServing) — Kubernetes-native serving CRD: standardized inference protocol, autoscaling (incl. **scale-to-zero** via Knative), canary rollout, explainability/transformer hooks. The orchestration layer that often *wraps* Triton/TF-Serving.
  - **TorchServe**, **ONNX Runtime**, **Ray Serve**, **BentoML**, **Seldon Core** are other common choices.
- **Model registry**: the source of truth for model artifacts + metadata + stage (`staging`/`production`/`archived`) + lineage. MLflow Model Registry, SageMaker/Vertex registries. Decouples "train" from "deploy" — deployment pulls a *registered, versioned, approved* artifact.

### 4.3 GPU batching (why it dominates serving economics)

GPUs are massively parallel but have high per-call fixed overhead (kernel launch, memory transfer). Serving requests one-at-a-time wastes the device. **Dynamic batching** holds incoming requests for a tiny window (e.g., 5 ms) and runs them as one batch.

The trade-off is **latency vs throughput**:

```
   Throughput
       ▲
       │           ┌──────── saturates (GPU-bound)
       │        ╱
       │      ╱
       │    ╱
       │  ╱
       └────────────────────────▶  batch size
                                    (and queue wait → latency ↑)
```

Larger batch ⇒ higher throughput (better GPU utilization, lower $/inference) but higher tail latency (requests wait to fill the batch + bigger kernels). You tune `max_batch_size` and `max_queue_delay` against your latency SLO. Triton automates this; you set the knobs.

### 4.4 Deploying models safely: A/B, canary, shadow

You never just "replace" a model. Models can be *correct* yet *worse* on the business metric.

- **Shadow / dark launch**: send live traffic to the new model in parallel, *discard* its output, compare offline. Zero user risk; validates latency + prediction distribution before exposure.
- **Canary**: route a small % (1→5→25→100) of traffic to the new model, watch metrics and guardrails, auto-rollback on regression.
- **A/B test**: split traffic between models and measure the *online business metric* (conversion, revenue, fraud caught) with statistical significance — because offline accuracy ≠ online value. This is the only way to *prove* a new model is better.
- **Multi-armed bandit**: adaptively shift traffic toward the better-performing model during the experiment (less regret than fixed A/B).

KServe/Seldon/Istio give you the traffic-splitting primitives; the model registry gives you the versioned artifacts to split between.

---

## 5. Vector Databases & Embeddings

### 5.1 Embeddings

An **embedding** maps an object (text, image, user) into a dense vector in ℝ^d such that *semantic similarity ≈ geometric proximity*. "King" and "queen" land near each other; a query and its relevant documents land near each other. Produced by encoder models (sentence-transformers, CLIP for images, etc.).

Similarity metrics:

- **Cosine similarity** — `cos(a,b) = (a·b) / (‖a‖‖b‖)`. Direction-only; the default for normalized text embeddings.
- **Dot product** — fast; equals cosine when vectors are L2-normalized.
- **Euclidean (L2) distance** — `‖a−b‖`.

### 5.2 The problem: ANN (Approximate Nearest Neighbor)

Exact nearest-neighbor search ("find the k closest of N vectors") is O(N·d) per query — fine for thousands, hopeless for billions. The **curse of dimensionality** also breaks classic spatial indices (kd-trees) in high d. So we accept **approximate** answers (>95% recall) for orders-of-magnitude speedups. Two dominant index families:

#### HNSW (Hierarchical Navigable Small World) — Malkov & Yashunin, 2016

A multi-layer proximity graph. Upper layers are sparse "highways" (long jumps); lower layers dense (local refinement). Search greedily descends, navigating toward the query.

```
   Layer 2:   ●───────────────●            (few nodes, long-range links)
              │               │
   Layer 1:   ●────●──────●────●───●        (more nodes)
              │    │      │    │   │
   Layer 0:   ●─●─●─●─●─●─●─●─●─●─●─●─●      (ALL nodes, short-range links)
                      ▲
              search enters at top, greedily hops closer, drops a layer, repeats
```

- **Search ≈ O(log N)**; excellent recall/latency; in-memory; the default for most workloads.
- Knobs: `M` (links per node — graph degree, memory), `efConstruction` (build quality), `ef`/`efSearch` (search breadth — recall vs latency).
- Cost: high memory (graph + vectors in RAM), slower/heavier inserts.

#### IVF (Inverted File Index) — and IVF-PQ

Partition the space into `nlist` clusters via k-means (each has a centroid). At query time, search only the `nprobe` nearest clusters instead of all N vectors.

```
   space partitioned into Voronoi cells (centroids ●):
       ┌─────┬─────┬─────┐
       │  ●  │  ●  │  ●  │   query q lands near a centroid;
       ├─────┼─────┼─────┤   probe only the nprobe closest cells
       │  ●  │ q●  │  ●  │   (here the center + neighbors)
       ├─────┼─────┼─────┤
       │  ●  │  ●  │  ●  │
       └─────┴─────┴─────┘
```

- `nprobe` trades recall (more cells = higher recall) vs speed.
- **Product Quantization (PQ)** compresses vectors (split into sub-vectors, quantize each to a codebook) so billions of vectors fit in RAM with ~8–16× compression — **IVF-PQ** is the standard for very large, memory-constrained corpora (at some recall cost). FAISS is the canonical library.

**Rule of thumb:** HNSW for best recall/latency when it fits in memory; IVF-PQ for billion-scale / memory-bound; flat (exact) only for small corpora or as a re-ranking step.

### 5.3 Vector databases

Purpose-built stores that handle ANN indexing + persistence + **metadata filtering** ("nearest vectors *where* `tenant=X` and `date>...`") + sharding/replication + CRUD. Examples: **pgvector** (Postgres extension — start here if you already run Postgres), **Qdrant**, **Weaviate**, **Milvus**, **Pinecone** (managed), **Vespa**, plus vector support in Elasticsearch/OpenSearch and Redis. Architect's note: filtered ANN (combining the graph/IVF traversal with a metadata predicate) is the genuinely hard part — evaluate it carefully for your access patterns.

### 5.4 Worked example: embeddings + exact kNN + a tiny HNSW-style graph search

```python
"""
Self-contained vector search demo (numpy only).
1) Toy "embeddings" + cosine kNN  (exact baseline)
2) A minimal greedy graph search illustrating the HNSW navigation idea.
"""
import numpy as np

rng = np.random.default_rng(0)

# ---------- 1. Embeddings + exact cosine kNN ----------
def l2_normalize(X):
    return X / (np.linalg.norm(X, axis=-1, keepdims=True) + 1e-12)

def cosine_knn(query, corpus, k=3):
    """Exact top-k by cosine similarity. O(N*d)."""
    q = l2_normalize(query.reshape(1, -1))
    C = l2_normalize(corpus)
    sims = (C @ q.T).ravel()           # cosine == dot product on normalized vectors
    idx = np.argpartition(-sims, k)[:k]
    idx = idx[np.argsort(-sims[idx])]  # sort the k
    return list(zip(idx.tolist(), sims[idx].tolist()))

N, d = 10_000, 64
corpus = rng.standard_normal((N, d)).astype(np.float32)
query  = corpus[42] + 0.01 * rng.standard_normal(d)   # close to vector #42
print("exact kNN:", cosine_knn(query, corpus, k=3))   # expect #42 on top


# ---------- 2. Minimal navigable-graph (HNSW intuition, single layer) ----------
def build_knn_graph(corpus, M=8):
    """Connect each node to its M nearest neighbors -> a navigable proximity graph."""
    C = l2_normalize(corpus)
    sims = C @ C.T
    np.fill_diagonal(sims, -np.inf)
    neighbors = np.argpartition(-sims, M, axis=1)[:, :M]
    return neighbors

def greedy_search(query, corpus, graph, entry, k=3, ef=16):
    """
    Greedy best-first search over the proximity graph (the core HNSW move):
    keep a candidate frontier, always expand the closest unexpanded node,
    maintain the best `ef` found. Visits O(log N)-ish nodes, not all N.
    """
    C = l2_normalize(corpus)
    q = l2_normalize(query.reshape(1, -1)).ravel()
    def sim(i): return float(C[i] @ q)

    visited = {entry}
    frontier = [(sim(entry), entry)]   # (similarity, node)
    best = list(frontier)
    while frontier:
        frontier.sort(reverse=True)             # best-first
        s, node = frontier.pop(0)
        # stop if current best frontier node is worse than worst kept result
        if best and s < min(b[0] for b in best) and len(best) >= ef:
            break
        for nb in graph[node]:
            if nb not in visited:
                visited.add(nb)
                snb = sim(nb)
                frontier.append((snb, nb))
                best.append((snb, nb))
                best = sorted(best, reverse=True)[:ef]
    return sorted(best, reverse=True)[:k], len(visited)

graph = build_knn_graph(corpus, M=8)
results, visited = greedy_search(query, corpus, graph, entry=0, k=3, ef=32)
print("graph search top-3:", [(i, round(s, 3)) for s, i in results])
print(f"visited {visited} / {N} nodes")   # << N : that's the ANN speedup
```

The takeaway from the code: graph navigation visits a *tiny fraction* of the corpus while still returning the true neighbors with high probability — that's exactly what HNSW does, with the added hierarchy of layers for O(log N) reach.

---

## 6. Retrieval-Augmented Generation (RAG)

RAG (Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks," NeurIPS 2020) grounds an LLM in *retrieved* external knowledge instead of relying solely on parametric memory. It mitigates hallucination, enables fresh/private knowledge without retraining, and provides citations.

```
   ┌───────────────────── INGESTION (offline / batch) ─────────────────────┐
   │  Docs ─▶ Chunk ─▶ Embed (encoder) ─▶ Upsert into Vector DB (+metadata) │
   └───────────────────────────────────────────────────────────────────────┘

   ┌───────────────────────── QUERY (online) ─────────────────────────────┐
   │  User query                                                            │
   │     │ embed                                                            │
   │     ▼                                                                  │
   │  Vector DB  ──top-k──▶  [Re-ranker]  ──▶  build PROMPT                 │
   │  (ANN search)          (cross-encoder)     (context + query +          │
   │                                              system instructions)      │
   │     ▼                                                                  │
   │   LLM ──▶ grounded answer (+ citations to retrieved chunks)            │
   └───────────────────────────────────────────────────────────────────────┘
```

Architect-level design decisions (where RAG quality is won or lost — mostly in *retrieval*, not generation):

- **Chunking**: size/overlap/strategy (fixed-token vs semantic/structural). Too big → diluted relevance + token cost; too small → lost context. Often the single biggest quality lever.
- **Hybrid retrieval**: combine **dense** (vector/semantic) with **sparse** (BM25/keyword) retrieval and fuse (e.g., Reciprocal Rank Fusion). Dense catches paraphrase; sparse nails exact terms/IDs/codes. Hybrid usually beats either alone.
- **Re-ranking**: retrieve top-50 cheaply (bi-encoder ANN), then re-rank to top-5 with a heavier **cross-encoder** that scores (query, doc) jointly. Big precision win.
- **Context window & cost**: stuffing more chunks costs tokens and money and can *hurt* via "lost in the middle" (models attend poorly to mid-context). Retrieve precisely; don't dump.
- **Evaluation**: this is the discipline most teams skip. Measure *retrieval* (recall@k, MRR, nDCG) and *generation* (faithfulness/groundedness, answer relevance, citation correctness) — frameworks like RAGAS, plus LLM-as-judge with human spot-checks. You cannot improve what you don't measure.
- **Advanced patterns**: GraphRAG (knowledge-graph-augmented), agentic/iterative retrieval (the model decides what to fetch next), query rewriting/decomposition, and **freshness/permissions** (retrieval must respect per-user ACLs — a top cause of data-leak incidents in enterprise RAG).

> Provider note: when implementing the generation step against Claude / the Anthropic API (model IDs, prompt caching to cut cost on the large static context, token limits, tool use for agentic retrieval), consult the current API reference rather than relying on memory — model names and pricing change. Prompt caching in particular is a major RAG cost lever since the system prompt + retrieved context are often reused.

---

## 7. LLM Serving

LLM inference is *autoregressive*: the model generates one token at a time, each token attending to all previous tokens. This shape drives the entire serving architecture.

### 7.1 Two phases: prefill vs decode

```
   PREFILL (process the prompt)        │   DECODE (generate output)
   ─────────────────────────────       │   ──────────────────────────
   all prompt tokens in parallel       │   ONE token per step, sequentially
   compute-bound (big matmul)          │   memory-bandwidth-bound
   builds the KV cache                 │   reads/extends the KV cache
   high GPU utilization                │   low per-step utilization (the hard part)
```

### 7.2 KV cache — the central data structure

For each generated token, the model needs the **keys and values** of all prior tokens (attention). Recomputing them every step would be O(n²) wasted work, so they're **cached**: the KV cache stores per-layer, per-head K and V tensors for every token in the sequence.

- KV cache size ≈ `2 (K,V) × layers × seq_len × num_kv_heads × head_dim × dtype_bytes` per request. For long contexts and many concurrent requests, **the KV cache — not the model weights — becomes the dominant memory consumer**, and it's what limits how many requests you can batch.
- **PagedAttention** (Kwon et al., *vLLM*, SOSP 2023) manages the KV cache like an OS manages virtual memory: non-contiguous fixed-size *pages*, eliminating fragmentation and enabling **prefix sharing** (requests with a common prefix — e.g., the same system prompt — share KV pages). This is why **vLLM** dramatically raised serving throughput; it's the de facto open-source LLM server, alongside NVIDIA **TensorRT-LLM**, **TGI**, and **SGLang** (with RadixAttention for prefix reuse).

### 7.3 Continuous (in-flight) batching

Naive batching waits for the whole batch to finish before starting new requests — but LLM outputs have wildly different lengths, so the GPU idles waiting for the longest one. **Continuous batching** (a.k.a. iteration-level scheduling, from the Orca paper, OSDI 2022) injects new requests and evicts finished ones *every decoding step*, keeping the GPU saturated. Combined with PagedAttention, it's the foundation of modern LLM throughput.

### 7.4 Token streaming

Because tokens are produced one at a time, you **stream** them to the client (Server-Sent Events / chunked transfer / gRPC stream) as generated, rather than waiting for the full response. This collapses *perceived* latency: the metric users feel is **TTFT** (time-to-first-token), not total time. Key latency metrics:

- **TTFT** (time to first token) — dominated by prefill (prompt length).
- **TPOT / ITL** (time per output token / inter-token latency) — dominated by decode + batch contention.
- **Total latency** ≈ TTFT + (output_tokens × TPOT).

### 7.5 Cost

LLM serving cost is real and nonlinear. Levers an architect controls:

- **Right-size the model** — use the smallest model that meets quality; route easy queries to cheap models (model cascades/routing).
- **Prompt caching** — cache the KV of stable prompt prefixes (system prompt, RAG context, few-shot examples) so you don't re-prefill them every call. Major cost reduction for repeated long contexts (offered both at the inference-server level via prefix sharing and as a managed API feature).
- **Quantization** — serve in INT8/FP8/INT4 (e.g., AWQ, GPTQ, FP8 on H100) to cut memory + increase throughput, trading a little quality.
- **Batching & autoscaling** — continuous batching for throughput; scale-to-zero for spiky/dev traffic (KServe + Knative).
- **Speculative decoding** — a small draft model proposes several tokens, the big model verifies them in one pass; accepted tokens are "free," reducing latency.
- **Cost accounting** — cost scales with *tokens* (input + output) and *context length* (KV memory). Long contexts are expensive twice: prefill compute and KV memory that crowds out batch concurrency. Retrieve precisely (see RAG) — token discipline is cost discipline.

> When serving managed LLMs (e.g., Claude via the Anthropic API), the dominant cost knobs become model selection, prompt caching, and output-token budget. Verify current model IDs, context limits, and pricing against the live API reference — do not hardcode from memory, as they change.

---

## 8. Monitoring: Drift & Data Quality

A deployed model degrades *silently* — no exception, no 500, just slowly-wrong predictions as the world changes. Production ML monitoring has layers beyond ops:

| Layer | What you watch | Tooling/method |
|---|---|---|
| **Operational** | latency (TTFT/TPOT), throughput, error rate, GPU util, cost | Prometheus/Grafana, standard SRE |
| **Data quality** | schema/type violations, nulls, range/cardinality anomalies, freshness | Great Expectations, Deequ, Soda, TFDV |
| **Drift** | input distribution shifts, prediction distribution shifts | PSI, KL/JS divergence, KS test, Evidently, WhyLabs, Arize, Fiddler |
| **Model performance** | accuracy/AUC/precision once labels arrive (often delayed!) | offline join against ground truth |
| **Business** | conversion, fraud caught, revenue, user satisfaction | product analytics |

### 8.1 Kinds of drift (precise terms)

Let X = features, Y = label, and P(·) the distribution:

- **Data / covariate drift** — P(X) changes; P(Y|X) stays the same. (Your user mix shifted; the relationship is intact.)
- **Concept drift** — P(Y|X) changes; the *mapping from features to outcome* changed. (Fraud patterns evolve; the same features now mean something different.) This is the dangerous one — accuracy drops even if inputs look normal.
- **Label / prior drift** — P(Y) changes (class balance shifts).

You usually can't measure accuracy in real time (labels lag — you learn if a loan defaulted *months* later). So you proxy with **input drift** and **prediction drift** as early-warning signals, then confirm with delayed labels.

### 8.2 Quantifying input drift — PSI (Population Stability Index)

PSI compares a feature's distribution now vs a reference (training) baseline, over bins:

```
PSI = Σ_i ( (actual%_i − expected%_i) × ln(actual%_i / expected%_i) )
```

Rule of thumb: PSI < 0.1 ≈ no significant shift; 0.1–0.25 ≈ moderate (investigate); > 0.25 ≈ major shift (likely retrain). For continuous distributions, KS-test or Jensen-Shannon divergence are common alternatives.

```python
import numpy as np

def psi(expected, actual, bins=10):
    """Population Stability Index between a reference and a current sample."""
    # bin edges from the reference distribution (quantile bins)
    quantiles = np.linspace(0, 100, bins + 1)
    edges = np.percentile(expected, quantiles)
    edges[0], edges[-1] = -np.inf, np.inf
    e_counts, _ = np.histogram(expected, bins=edges)
    a_counts, _ = np.histogram(actual,   bins=edges)
    e_pct = np.clip(e_counts / e_counts.sum(), 1e-6, None)  # avoid log(0)
    a_pct = np.clip(a_counts / a_counts.sum(), 1e-6, None)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))

rng = np.random.default_rng(1)
ref   = rng.normal(0, 1, 10_000)            # training distribution
same  = rng.normal(0, 1, 10_000)            # no drift
drift = rng.normal(0.6, 1.3, 10_000)        # shifted mean + variance
print("PSI no-drift:", round(psi(ref, same), 4))     # ~0.0  (stable)
print("PSI drifted :", round(psi(ref, drift), 4))    # >0.25 (retrain signal)
```

For LLM/RAG systems, "drift" also includes **prompt/usage drift** (users start asking different things), **retrieval quality decay** (corpus grows stale, embeddings outdated), and **output quality** (faithfulness/toxicity), monitored via LLM-as-judge evals and feedback signals — a domain often called "LLM observability."

---

## 9. Key Takeaways

1. **The model is the small part.** Production ML is a *systems* problem — features, serving, monitoring, lineage, cost. Design for those, not just accuracy.
2. **Feature stores exist to kill training-serving skew.** Compute a feature once; serve it to training (offline, point-in-time-correct) and inference (online, low-latency) from one definition. Adopt when features are shared/fresh; skip the complexity when they aren't.
3. **Serving is a latency-vs-throughput-vs-cost optimization.** Batch vs real-time vs streaming; GPU dynamic batching; use a real serving runtime (Triton/TF-Serving/KServe) and a model registry. Deploy with shadow → canary → A/B; prove value on the *online business metric*, not offline accuracy.
4. **Vector search is approximate by necessity.** HNSW (graph, O(log N), best recall/latency, memory-hungry) vs IVF-PQ (clusters + compression, billion-scale, memory-bound). Filtered ANN is the hard part. Cosine on normalized vectors = dot product.
5. **RAG quality is mostly retrieval quality.** Chunking, hybrid (dense+sparse) retrieval, re-ranking with cross-encoders, precise context (avoid lost-in-the-middle), permissions, and *measured* evaluation (recall@k, faithfulness). Generation is the easy 20%.
6. **LLM serving is shaped by autoregression.** The KV cache (not weights) often dominates memory and caps concurrency; PagedAttention + continuous batching (vLLM/TensorRT-LLM/SGLang) are the throughput foundations; stream tokens and optimize TTFT/TPOT; cut cost via prompt caching, quantization, model routing, and token discipline.
7. **Models fail silently — monitor for drift, not just crashes.** Distinguish covariate drift (P(X)) from concept drift (P(Y|X)); use PSI/JS/KS on inputs and predictions as early signals because labels lag; confirm with delayed ground truth. Add data-quality validation and business-KPI monitoring.
8. **Reproducibility is non-negotiable.** Version code + data + model + config; track experiments; make every production model traceable to its exact inputs.

---

## Seminal References

- D. Sculley et al., "Hidden Technical Debt in Machine Learning Systems," NeurIPS 2015.
- Y. Malkov & D. Yashunin, "Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs," IEEE TPAMI 2018 (HNSW).
- H. Jégou, M. Douze, C. Schmid, "Product Quantization for Nearest Neighbor Search," IEEE TPAMI 2011 (PQ; basis of FAISS / IVF-PQ).
- P. Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks," NeurIPS 2020 (RAG).
- W. Kwon et al., "Efficient Memory Management for Large Language Model Serving with PagedAttention," SOSP 2023 (vLLM).
- G. Yu et al., "Orca: A Distributed Serving System for Transformer-Based Generative Models," OSDI 2022 (continuous batching).
- N. Liu et al., "Lost in the Middle: How Language Models Use Long Contexts," TACL 2024.
- A. Vaswani et al., "Attention Is All You Need," NeurIPS 2017 (the architecture the KV cache serves).
```
