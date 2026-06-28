# MapReduce

> *"MapReduce: Simplified Data Processing on Large Clusters"* — Jeffrey Dean, Sanjay Ghemawat. OSDI 2004.

---

## Overview

MapReduce is a **programming model and an execution framework** for processing and generating large datasets on clusters of commodity machines. Its central idea: let programmers express a computation as two simple functions — **`map`** and **`reduce`** — and have the framework automatically handle the hard distributed-systems parts: **parallelization, data distribution, load balancing, and fault tolerance**.

It was built at Google to make it tractable for ordinary engineers (not distributed-systems experts) to write large-scale data processing jobs — computing inverted indexes, web-graph structures, per-host page counts, and so on — over the web crawl stored in GFS.

| | |
|---|---|
| **Built by** | Google |
| **Seminal paper** | OSDI 2004 |
| **Runs on** | GFS (input/output), commodity clusters |
| **Open-source descendant** | Apache Hadoop MapReduce |

---

## The Problem It Solved

Google had embarrassingly parallel computations over huge data, but each one required hand-rolling the same hard machinery: splitting work across hundreds/thousands of machines, dealing with machines that crash mid-job, dealing with slow machines, moving data around, retrying. This logic dwarfed the actual computation and had to be rebuilt for every job.

The insight: **most of these computations have the same shape.** You apply a function to every input record to produce intermediate key/value pairs, then aggregate all values sharing a key. If you factor that shape into a reusable framework, the framework can own all the distributed-systems complexity, and the user writes only two trivial functions.

**Constraints:** commodity hardware (frequent failures), data lives in GFS (so leverage data locality), and the model must be simple enough for non-experts.

---

## The Programming Model

```
map(k1, v1)        -> list(k2, v2)
reduce(k2, list(v2)) -> list(v2)        // or list(v3)
```

The framework guarantees that **all intermediate values for a given key `k2` are grouped and delivered to the same reduce invocation**, sorted by key. That grouping/sorting step in the middle is the **shuffle**.

**Canonical example — word count:**

```python
def map(doc_name, contents):
    for word in contents.split():
        emit_intermediate(word, "1")

def reduce(word, counts):
    emit(word, str(sum(int(c) for c in counts)))
```

Other examples from the paper: distributed grep, URL access frequency, reverse web-link graph, inverted index, distributed sort.

```mermaid
flowchart LR
    I[(Input\nGFS files)] --> S[Split into M pieces\n~16-64MB each]
    S --> MAP[M map tasks\nuser map()]
    MAP --> P[Partition by\nhash(key) mod R\n+ optional combiner]
    P --> SH{{Shuffle\nsort + group by key}}
    SH --> RED[R reduce tasks\nuser reduce()]
    RED --> O[(Output\nR files in GFS)]
```

---

## Architecture & Execution Framework

One **master** coordinates many **workers**. The user specifies `M` map tasks and `R` reduce tasks (and the output partition function, default `hash(key) mod R`).

```mermaid
flowchart TB
    U[User program] -->|fork| MASTER[Master\nassigns tasks, tracks state,\nstores intermediate locations]
    U -->|fork| W1[Worker]
    U -->|fork| W2[Worker]

    subgraph MapPhase
      W1 -->|read split\n(prefer local GFS replica)| GFS1[(GFS input)]
      W1 -->|write R partitions| L1[Local disk\nbuffered + sorted]
    end

    subgraph ReducePhase
      W2 -->|RPC read intermediate\nfrom map workers' local disks| L1
      W2 -->|write| GFS2[(GFS output)]
    end

    MASTER -. pings / detects failure .-> W1
    MASTER -. assigns reduce + intermediate file locations .-> W2
```

**Execution flow (from the paper):**

1. The MapReduce library **splits input into `M` pieces** (typically 16–64 MB, aligned with GFS chunks) and starts copies of the program across the cluster.
2. One copy is the **master**; the rest are **workers**. The master assigns idle workers map or reduce tasks.
3. A **map worker** reads its split, parses key/value pairs, runs the user's `map`, and buffers emitted pairs in memory.
4. Periodically the buffered pairs are **written to local disk, partitioned into `R` regions** by the partition function. The **master is told the locations** of these `R` regions.
5. When a **reduce worker** is told the locations, it uses **RPC to read** the relevant intermediate data from the map workers' **local disks**. After reading all its data, it **sorts by key** so equal keys are grouped (an external sort if the data is too big for memory).
6. The reduce worker iterates the sorted data, and for each unique key calls the user's `reduce` with the key and its set of values. Output is appended to a **final output file** for this reduce partition (in GFS).
7. When all map and reduce tasks are done, the master wakes the user program. Output is `R` files (one per reduce task), typically fed to another MapReduce or to GFS consumers.

**Intermediate data lives on map workers' local disks, not GFS.** This is a deliberate cost decision: writing the (huge) intermediate shuffle data through GFS with 3× replication would be wasteful, since it's transient and re-derivable by re-running the map task.

---

## How It Works — Key Mechanisms

### Fault tolerance via re-execution (the heart of it)

MapReduce's fault tolerance is built on the fact that `map` and `reduce` are **deterministic functions of their input**, so any failed work can simply be **re-executed**.

- **Worker failure:** the master pings workers periodically. A worker that doesn't respond is marked failed. Any **map tasks** it completed are **re-executed** from scratch — because their output sat on the dead worker's **local disk** and is now unreachable; reduce tasks that hadn't yet read from it are notified of the new location. **In-progress** map or reduce tasks on the failed worker are reset to idle and rescheduled. Completed **reduce** tasks do *not* need re-execution because their output is already in (replicated) GFS.
- **Master failure:** rare (single master). The paper's simple answer: the master checkpoints its data structures; on failure the whole job can be restarted from the last checkpoint, but in practice they just aborted and re-ran the job.
- **Atomic commits guard against duplicates:** because failures cause re-execution, the same task may run more than once. The framework relies on **atomic rename** of output files so that exactly one completion "wins": a completed reduce task atomically renames its temporary output to the final name (GFS guarantees the rename atomicity), and the master ignores duplicate completion messages. This yields **exactly-once semantics for the output** even with at-least-once execution — *provided* `map`/`reduce` are deterministic. (Non-deterministic functions only get weaker guarantees.)

### Stragglers and backup tasks

A common cause of long job tails is a **straggler** — a machine that, due to a bad disk, contention, or a misconfiguration, takes unusually long on its last few tasks. Even one straggler can dominate total job time.

The fix: when a job is **near completion**, the master schedules **backup (speculative) executions** of the remaining in-progress tasks. Whichever copy — primary or backup — finishes first **wins**, and the other is killed. This costs a few percent of extra resources but can **dramatically** cut total completion time (the paper reports a sort job taking ~44% longer with the backup mechanism disabled). Speculative execution became one of the most copied ideas in big-data systems.

### Data locality

Network bandwidth is the scarce resource. Because input lives in **GFS with 3× replication**, the master tries to **schedule each map task on a machine that already holds a replica of its input split** — or, failing that, on a machine *near* one (same rack/switch). The result: most input is read from **local disk, consuming no network bandwidth**. This "**move the computation to the data**" principle is one of MapReduce's most influential ideas.

### Combiners

When a map produces many records with the same key that `reduce` will aggregate (e.g., thousands of `("the", 1)`), the user can supply a **combiner** — typically the same code as `reduce` — that does **partial aggregation on the map side** before the shuffle. This cuts the volume of intermediate data sent over the network. Combiners are valid only for commutative+associative reductions.

### Tuning M and R

- `M` and `R` are chosen so individual tasks are small (good for load balancing and fast failure recovery — a failed worker's many small tasks spread across the cluster).
- The master makes O(M+R) scheduling decisions and holds O(M×R) state (where each map sends to each reduce), so M and R can't be unbounded.
- `R` is often a small multiple of the number of worker machines.

---

## Key Innovations / What Made It Special

1. **Restriction as power.** By constraining computation to the map/reduce shape, the framework could *automatically* parallelize, distribute, schedule near data, and recover from failures — things impossible to do generically for arbitrary programs.
2. **Fault tolerance by deterministic re-execution** rather than complex checkpointing/replication of in-flight state. Simple and robust at scale.
3. **Backup tasks** to defeat stragglers — treating the long tail as a first-class problem.
4. **Locality-aware scheduling** ("ship code to data") built on top of GFS replication.
5. **Democratization:** it let thousands of engineers run cluster-scale jobs without knowing anything about distributed systems.

---

## Data Model / APIs

The user provides:

```c
// Conceptual C++-ish interface from the paper
class Mapper   { void Map(const string& key, const string& value); };  // emits via EmitIntermediate
class Reducer  { void Reduce(const string& key, Iterator* values); };  // emits via Emit

// Plus optional:
//   - partition function: hash(key) mod R   (override for custom sharding)
//   - combiner function
//   - input/output reader/writer formats (text, GFS records, etc.)
//   - comparator for key sort order
```

The framework also offers conveniences: **counters** (distributed aggregate stats), skipping of records that repeatedly crash `map`/`reduce` (to survive bad records/bugs in third-party libs), and status pages.

---

## Trade-offs & Limitations

| Limitation | Detail |
|---|---|
| **Rigid two-stage shape** | Many algorithms (graph, iterative ML) don't map cleanly to one map+reduce; you chain many jobs. |
| **Materialize-to-disk between stages** | Each job writes output to GFS; **iterative** algorithms (e.g., PageRank, gradient descent) re-read from disk every iteration — extremely slow. |
| **High latency / batch-only** | Job startup and disk materialization make it unsuitable for interactive or low-latency work. |
| **Shuffle is expensive** | The all-to-all sort/transfer is often the dominant cost. |
| **Awkward for joins & multi-input** | Expressing joins requires contortions. |
| **Single master** | Coordinator is a (mitigated) SPOF. |

---

## Influence & Legacy — and why it faded

- **Apache Hadoop MapReduce** was the open-source clone that, with HDFS, launched the entire "big data" industry (2006–~2014). It put cluster computing in the hands of every company.
- **Higher-level languages** grew on top because raw MapReduce was tedious: **Apache Pig** (dataflow), **Apache Hive** (SQL → MapReduce), and Google's own **Sawzall**, **FlumeJava**, and **Tenzing**.
- **Apache Spark** (2012, *Resilient Distributed Datasets*) directly attacked MapReduce's biggest weakness — disk materialization between stages — by keeping intermediate data **in memory** and modeling computation as a **DAG** of transformations (not just one map+reduce). For iterative ML and interactive analytics, Spark was often **10–100× faster**, and it largely displaced MapReduce.
- **Dataflow / DAG engines** (Spark, Apache Flink, Apache Beam, Google's Dataflow/MillWheel) generalized the model to arbitrary operator DAGs and streaming.

**Why MapReduce faded:** its strength (a rigid, disk-materializing two-stage model) was also its ceiling. Once memory got cheap and workloads turned iterative, interactive, and streaming, more general **DAG-based, in-memory** engines dominated. But the *ideas* — deterministic re-execution, data locality, speculative execution, partition-by-key shuffle — live on inside all of them. Google itself moved internal pipelines onto FlumeJava/Dataflow.

---

## Lessons for Architects

1. **Constrain the model to enable the platform.** A narrower programming interface let the framework do enormous work automatically. Restricting expressiveness is sometimes the highest-leverage design move.
2. **Determinism buys you cheap fault tolerance.** If your unit of work is a pure function of its input, "just re-run it" beats elaborate state replication. Design for re-executability.
3. **The tail is the system.** At scale, the slowest 1% of tasks determine wall-clock time. Plan for stragglers (backup tasks, hedged requests) explicitly — this lesson recurs in Dapper-era latency work and in "The Tail at Scale."
4. **Move computation to data** when bandwidth is the bottleneck; co-design scheduling with your storage layer's replica placement.
5. **Idempotency + atomic commit = exactly-once *effects*** even on at-least-once execution. Use atomic rename / transactional commit at the boundaries.
6. **Every great abstraction has a lifespan.** MapReduce's assumptions (batch, disk-cheap, two-stage) were eventually invalidated; the successors generalized the same primitives. Build the abstraction the workload needs *now*, and expect to generalize it later.
