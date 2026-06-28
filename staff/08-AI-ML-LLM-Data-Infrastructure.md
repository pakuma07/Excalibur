# 08 · AI/ML & LLM Data Infrastructure

The fastest-moving frontier and the strongest current differentiator. Data engineering is now the bottleneck for most AI projects — the algorithms are a sliver, the data infrastructure is the bulk. These scenarios span classic ML data infra and the 2025–2026 LLM/agent stack.

---

**1. Feature store: build vs buy vs none**
*Problem:* ML teams keep recomputing the same features inconsistently, and training features don't match serving features.
*The fork:* Adopt/build a feature store (consistency, governance, infra cost) vs ad hoc feature pipelines (fast, inconsistent) vs none.
*What you weigh:* The core problem a feature store solves is train/serve consistency and reuse. It's worth it once multiple models/teams share features; overkill for a single model.
*Seasoned call:* Introduce a feature store when feature reuse and train/serve skew become real pain across teams; don't impose the infrastructure on a single-model shop. Owning train/serve consistency is a high-value data-engineering responsibility.

**2. Training/serving skew**
*Problem:* A model performs well in training but poorly in production.
*The fork:* Unify feature computation across train and serve (one code path) vs separate pipelines (drift-prone) vs accept skew.
*What you weigh:* Skew usually comes from features computed differently online vs offline. Point-in-time correctness (no future leakage) is subtle and easy to get wrong.
*Seasoned call:* Compute features through a single shared definition for both training and serving, with point-in-time-correct historical features. This is a data-engineering correctness problem more than a data-science one.

**3. Point-in-time correctness / data leakage**
*Problem:* A model is "too good" because training features accidentally include future information.
*The fork:* Strict point-in-time joins (correct, complex) vs naive joins (leaky, optimistic) vs ignore it.
*What you weigh:* Leakage invalidates models silently and is caught only in production underperformance. Building leakage-free training sets requires temporal discipline in the data layer.
*Seasoned call:* Enforce point-in-time-correct feature retrieval (as-of joins on event time) so training only sees what was knowable then. The data engineer is the guardian against leakage.

**4. ML pipeline orchestration and reproducibility**
*Problem:* Nobody can reproduce how a deployed model's training data was built.
*The fork:* Versioned, reproducible pipelines (data + code + config versioned) vs notebook-driven one-offs (irreproducible) vs partial tracking.
*What you weigh:* Reproducibility requires versioning data snapshots, feature definitions, and pipeline code together. This is MLOps-adjacent data engineering.
*Seasoned call:* Version data snapshots, feature logic, and pipeline code so any model's training data is reconstructable; treat training-data production as a first-class, governed pipeline. Reproducibility is a data-lineage problem.

**5. RAG ingestion pipeline (the new ETL)**
*Problem:* You must make a company's documents queryable by an LLM application.
*The fork:* The pipeline stages each have decisions — ingestion (which sources), chunking (how to split), embedding (which model/dimensions), indexing (which vector store), retrieval (vector vs hybrid).
*What you weigh:* RAG is fundamentally a data pipeline: ingest → chunk → embed → index → retrieve. Most quality problems trace to chunking and retrieval, not the LLM. Offline indexing and online retrieval should be separate pipelines.
*Seasoned call:* Treat RAG as a production data pipeline with the same rigor as any other: separate offline indexing from online serving, version your chunking/embedding choices, and instrument retrieval quality. This is squarely data-engineering work now.

**6. Chunking strategy**
*Problem:* Retrieval returns irrelevant or fragmented context, hurting answer quality.
*The fork:* Fixed-size chunks (simple, blind to structure) vs semantic/structure-aware chunking (better, complex) vs hierarchical/parent-child chunking.
*What you weigh:* Chunk size vs retrieval precision vs context-window cost. Structure-aware chunking and metadata enrichment usually beat naive fixed windows.
*Seasoned call:* Use structure-aware chunking with metadata enrichment, tuned to the document type and the embedding model's window; measure its effect on retrieval quality rather than guessing. Chunking is the most underrated lever in RAG quality.

**7. Embedding model and re-embedding strategy**
*Problem:* You chose an embedding model; now a better one exists, or your model is deprecated.
*The fork:* Re-embed the whole corpus (cost, downtime) vs version embeddings and migrate incrementally vs stay put.
*What you weigh:* Embeddings are tied to a specific model; changing models means re-embedding everything. Dimensionality, cost, and index compatibility matter. This is a recurring maintenance reality.
*Seasoned call:* Version embeddings by model, design the pipeline so re-embedding is a routine batch operation, and plan for it as inevitable maintenance. Don't hard-couple your system to one embedding model forever.

**8. Vector database choice and scaling**
*Problem:* Choosing where to store and search embeddings at scale.
*The fork:* Dedicated vector DB (Pinecone/Milvus/Qdrant/Weaviate — purpose-built ANN) vs vector-capable existing store (Postgres/pgvector, OpenSearch, warehouse-native) vs library (FAISS as a research/batch primitive).
*What you weigh:* Scale, latency, filtering needs, operational overhead, and whether you can avoid a new system by using vector support in a store you already run. Wrong choice has real cost/performance consequences at scale.
*Seasoned call:* Use vector support in an existing store for modest scale and to avoid new infrastructure; adopt a dedicated vector DB when scale, latency, and hybrid-search needs justify it. Match the ANN index (e.g., HNSW) and the store to your real recall/latency/scale requirements.

**9. Hybrid search and reranking**
*Problem:* Pure vector search misses exact-match and keyword-critical results.
*The fork:* Vector-only (semantic, misses keywords) vs keyword-only (exact, misses meaning) vs hybrid (vector + lexical + reranking).
*What you weigh:* Recall vs precision vs latency/cost of an added reranking stage. Hybrid retrieval plus a reranker is now the strong default for quality.
*Seasoned call:* Combine vector and lexical (BM25-style) retrieval with a reranking stage for production-quality results; measure retrieval quality (groundedness/relevance) as a pipeline metric. Retrieval quality, not the LLM, is usually the bottleneck.

**10. RAG vs alternatives (GraphRAG, agentic retrieval, fine-tuning)**
*Problem:* Basic RAG struggles with multi-hop, multi-source, or relationship-heavy questions.
*The fork:* Classic RAG (good for static single-source retrieval) vs GraphRAG (relationships, multi-hop) vs agentic/iterative retrieval (complex queries, higher cost/latency) vs fine-tuning (bakes knowledge in, costly to update).
*What you weigh:* Query complexity vs cost/latency vs freshness needs. The space is evolving fast; "RAG is dead" claims are overstated but enhanced approaches are rising for complex cases.
*Seasoned call:* Match the retrieval approach to the query type: classic RAG for static knowledge, graph/agentic approaches for multi-hop and multi-source, fine-tuning only for stable, high-value knowledge that rarely changes. Evaluate per use case rather than adopting one approach dogmatically.

**11. Data for AI agents (context engineering)**
*Problem:* Autonomous agents need fresh, relevant context delivered in milliseconds, not batch-prepared datasets.
*The fork:* Treat agent context as a first-class real-time system vs reuse batch analytics data vs ad hoc.
*What you weigh:* Agents are emerging as primary data consumers alongside humans; they need streaming, low-latency, governed context and memory. This is a new architectural surface.
*Seasoned call:* Architect context delivery (retrieval + memory + freshness) as its own low-latency, governed system — "context engineering" — distinct from batch analytics. This is where data-engineering scope is expanding most right now; leading here is a strong differentiator.

**12. RAG/AI pipeline evaluation and quality**
*Problem:* You can't tell if a change to the pipeline made answers better or worse.
*The fork:* Systematic evaluation (groundedness, relevance, accuracy on a test set) vs vibes-based spot checks vs no evaluation.
*What you weigh:* AI pipelines need the data-quality discipline of any pipeline, plus AI-specific metrics. Without evaluation, every change is a guess.
*Seasoned call:* Build an evaluation harness with groundedness/relevance/accuracy metrics on a curated set, run it in CI for pipeline changes, and monitor in production. Bring data-engineering rigor (testing, observability) to AI pipelines — that rigor is exactly what most AI projects lack.

**13. Governing the AI data stack**
*Problem:* Embeddings, prompts, and agent retrieval bypass traditional data governance and can leak sensitive data.
*The fork:* Extend governance to AI assets (unified control plane) vs ungoverned AI data (fast, risky) vs siloed AI governance.
*What you weigh:* An agent can surface confidential data through retrieval that row/column permissions never contemplated. Embeddings can encode sensitive content. Governance must extend to these.
*Seasoned call:* Bring embeddings, vector indexes, prompts, and agent access paths under the same classification, lineage, and access-policy regime as structured data. (Deep-dive in 07 #11.) This gap exists in most organizations today.

---

*Cross-references: AI-asset governance in 07; pipeline rigor/evaluation parallels 05; the org case for investing here is in 09.*
