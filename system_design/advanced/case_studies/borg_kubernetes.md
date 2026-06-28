# Google Borg & Kubernetes — Cluster Management and the Declarative Control Plane

## Overview

**What they are.** **Borg** is Google's internal cluster manager: a system that runs essentially all of Google's workloads (both long-running services and batch jobs) across tens of thousands of machines per cluster, packing them efficiently and keeping them alive despite failures. **Kubernetes (K8s)** is the open-source container orchestrator — Borg's intellectual successor — that generalized those ideas for the industry and became the de-facto standard control plane for containerized infrastructure.

**Who built them & the seminal papers.**
- **Borg** — built at Google starting ~2003–2004; publicly described in **"Large-scale cluster management at Google with Borg"** by **Abhishek Verma, Luis Pedrosa, Madhukar Korupolu, David Oppenheimer, Eric Tune, and John Wilkes (EuroSys 2015)**. Borg had run Google in production for over a decade before the paper.
- **Omega** — an intermediate research effort: *"Omega: flexible, scalable schedulers for large compute clusters"* (Schwarzkopf et al., EuroSys 2013). Omega explored **shared-state, optimistic-concurrency** scheduling to fix Borg's monolithic-scheduler scaling limits.
- **Kubernetes** — open-sourced by Google in 2014 (Brendan Burns, Joe Beda, Craig McLuckie and team), donated to the **CNCF** in 2015. The lineage and the lessons are captured in *"Borg, Omega, and Kubernetes"* (Burns, Grant, Oppenheimer, Brewer, Wilkes, ACM Queue / CACM 2016).

**The big idea.** Treat the datacenter as a single pool of resources, and let users **declare the desired state** of their workloads; the system continuously **reconciles** reality toward that declared state via **control loops**. Operators stop issuing imperative commands ("start this process on that machine") and instead describe **what** they want; the control plane figures out **how** and keeps it true.

---

## The Problem It Solved

Google ran thousands of distinct applications across enormous fleets. Managing this **imperatively** — humans (or scripts) deciding which process runs on which machine — fails on every axis at scale:

| Problem | Imperative/manual failure |
|---|---|
| **Utilization** | Static machine assignment leaves resources stranded; Google could not afford to waste a fleet's worth of capacity |
| **Failure is constant** | At fleet scale, machines die continuously; something must *automatically* reschedule work |
| **Mixed workloads** | Latency-sensitive services + throughput batch jobs must **co-exist** on the same machines to drive utilization up |
| **Deployment / scaling** | Rolling out, scaling, and rescheduling thousands of tasks by hand is impossible |
| **Isolation** | Co-located tenants must not interfere (noisy-neighbor) |
| **Operational overhead** | Naming, load balancing, health checking, restarting — must be automated and uniform |

Borg's answer: a centralized cluster manager that **bin-packs** diverse workloads onto shared machines, **isolates** them with Linux containers (cgroups), keeps them running through failures, and **overcommits** by mixing high-priority services with preemptible batch — pushing utilization far above what static allocation allows.

---

## Architecture

### Borg

```
                       ┌──────────────────────────────────────────┐
   borgcfg / CLI ─────▶│              BORGMASTER                   │
   (job config:        │  (logically one, replicated 5× via Paxos) │
    declarative)       │  ┌────────────────────────────────────┐  │
                       │  │ Main: API, state machine, RPC       │  │
                       │  │ replicated store (Paxos) = state    │  │
                       │  └────────────────────────────────────┘  │
                       │  ┌────────────────────────────────────┐  │
                       │  │ SCHEDULER (separate process):       │  │
                       │  │  - feasibility (which machines fit?) │  │
                       │  │  - scoring (best machine?)           │  │
                       │  └────────────────────────────────────┘  │
                       └───────────────┬──────────────────────────┘
                                       │ assign tasks
            ┌──────────────────────────┼───────────────────────────┐
            ▼                          ▼                            ▼
     ┌────────────┐            ┌────────────┐               ┌────────────┐
     │  Borglet   │            │  Borglet   │     ...       │  Borglet   │
     │ (per node) │            │ (per node) │               │ (per node) │
     │ starts/stops│           │ runs tasks │               │            │
     │ tasks, cgrp │           │ in cgroups │               │            │
     │ reports state│          │            │               │            │
     └────────────┘            └────────────┘               └────────────┘
```

- **Borgmaster** — the brain: holds cluster state in a **Paxos-replicated** store (5 replicas), serves the API, runs the state machine. The **scheduler** is a *separate* process that decides task placement.
- **Borglet** — an agent on every machine that starts/stops tasks, manages their containers (cgroups), and reports machine/task state back to the master.
- **Job / Task / Alloc** — a **Job** is a set of identical **Tasks**; an **Alloc** reserves a slice of a machine into which tasks run.

### Omega (the intermediate step)
Borg's **monolithic** scheduler became a scaling bottleneck. **Omega** replaced it with a **shared-state** model: cluster state in a versioned, transactional **central store**, and *multiple* schedulers operating concurrently with **optimistic concurrency control** (each grabs resources optimistically; conflicts resolved at commit). This decouples scheduler logic and lets specialized schedulers coexist — ideas that fed back into both later Borg and Kubernetes.

### Kubernetes

```
                         CONTROL PLANE
   ┌─────────────────────────────────────────────────────────────┐
   │   ┌─────────────┐        ┌──────────────────────────────┐    │
   │   │  etcd        │◀──────▶│  kube-apiserver               │    │
   │   │ (consistent  │  only  │  (the ONLY thing that talks   │    │
   │   │  KV store,   │  writer│   to etcd; REST + watch;      │    │
   │   │  Raft)       │        │   validation, auth, the hub)  │    │
   │   └─────────────┘        └───────┬───────────────┬───────┘    │
   │                          watch & │ update        │ watch       │
   │              ┌───────────────────▼──┐   ┌─────────▼─────────┐  │
   │              │ kube-scheduler        │   │ controller-manager│  │
   │              │ (binds Pods→Nodes)    │   │ (Deployment, RS,  │  │
   │              └──────────────────────┘   │  Node, Job, ...   │  │
   │                                          │  reconcile loops) │  │
   │                                          └───────────────────┘  │
   └──────────────────────────────┬──────────────────────────────────┘
            watch assigned Pods    │ (everything via the API server)
        ┌──────────────────────────┼───────────────────────────────┐
        ▼                          ▼                                ▼
   ┌──────────────┐         ┌──────────────┐               ┌──────────────┐
   │ Node          │        │ Node          │      ...      │ Node          │
   │ ┌──────────┐  │        │ ┌──────────┐  │               │              │
   │ │ kubelet  │  │        │ │ kubelet  │  │               │ kubelet      │
   │ │  ↓ CRI   │  │        │ └──────────┘  │               │ kube-proxy   │
   │ │ container│  │        │  kube-proxy   │               │              │
   │ │ runtime  │  │        │               │               │              │
   │ │ [Pod:    │  │        │  [Pods...]    │               │  [Pods...]   │
   │ │  ctr,ctr]│  │        │               │               │              │
   │ └──────────┘  │        └──────────────┘               └──────────────┘
   └──────────────┘
```

Component mapping (Borg → K8s): Borgmaster store → **etcd**; Borgmaster API/state machine → **kube-apiserver**; Borg scheduler → **kube-scheduler**; Borglet → **kubelet**; Job/Task → **Deployment/Pod**; Borg's built-in naming/LB → **Service**.

---

## How It Works

### 1. The declarative desired-state model
You don't tell Kubernetes *to do* things; you submit an **object** describing the **desired state** (e.g., "I want 3 replicas of this container image"). The system stores that spec and is responsible for making the world match. Each object has two halves:
- **`spec`** — what the user wants (desired state),
- **`status`** — what the system currently observes (actual state).

The control plane's entire job is to drive `status` toward `spec`. This is **declarative**, not imperative — the same submission is idempotent and self-healing.

### 2. Control loops / reconciliation (the core pattern)
A **controller** runs an endless loop:

```
for ever:
    desired  = read spec   (from API server / etcd)
    observed = read status (actual cluster state)
    diff     = desired − observed
    take actions to reduce diff   (create/delete/update objects)
```

This **reconciliation loop** is the heart of both Borg and Kubernetes. It's a **level-triggered** control system (it acts on the *current gap*, not on a one-time *edge* event), which makes it robust: a dropped message, a crashed controller, or a stale cache just gets corrected on the next loop. Examples:
- **ReplicaSet controller**: desired=3 pods, observed=2 → create 1; observed=4 → delete 1.
- **Node controller**: node stops heartbeating → mark NotReady, evict/reschedule its pods.
- **Deployment controller**: orchestrates rolling updates by managing ReplicaSets.

Controllers don't talk to each other or to nodes directly — they **watch** the API server and **write** desired changes back, and *other* controllers/agents react. This loose coupling via shared state is straight from Omega.

### 3. The API server as the hub; etcd as the source of truth
**kube-apiserver** is the only component that reads/writes **etcd** (a Raft-backed, strongly-consistent KV store — the analogue of Borg's Paxos store). Everything else — scheduler, controllers, kubelets — interacts **only** through the API server's REST + **watch** interface. This gives:
- a single, validated, authenticated, audited point of truth,
- **watch streams** so components react to changes without polling,
- **optimistic concurrency** via per-object resource versions (compare-and-swap), exactly Omega's model.

### 4. The scheduler & bin-packing
The **scheduler** assigns unscheduled Pods to Nodes in two phases (mirroring Borg's feasibility + scoring):
1. **Filtering (feasibility):** which nodes *can* run this pod? Check resource requests vs. available, node selectors/affinity, taints/tolerations, etc.
2. **Scoring (ranking):** among feasible nodes, which is *best*? Spread vs. bin-pack, balance resource usage, prefer affinity, etc.

This is a **bin-packing** problem: fit many workloads of differing CPU/memory shapes onto machines to maximize utilization without overcommitting beyond tolerance. Borg famously drove utilization up by mixing **latency-sensitive** services with **preemptible batch**, and by distinguishing a task's **resource request** (reserved) from its **resource limit** and actual usage (enabling **overcommit** and reclaiming unused reservations).

### 5. Resource isolation (cgroups / containers)
Both systems isolate co-located workloads using **Linux containers**: **cgroups** (control groups) cap and account CPU, memory, and IO, and **namespaces** isolate process/network/filesystem views. Borg pioneered this at scale (its container tech was a precursor to **cgroups**, which Google contributed to Linux, and to the broader container movement). In Kubernetes the kubelet drives a container runtime via the **CRI (Container Runtime Interface)** to enforce these limits per container.

Resource model:
- **requests** — guaranteed/reserved amount the scheduler uses for placement,
- **limits** — hard cap enforced by cgroups,
- QoS classes (Guaranteed / Burstable / BestEffort) determine eviction order under pressure.

### 6. Priority & preemption
Not all work is equal. Each workload has a **priority**. When a high-priority pod can't be scheduled because resources are full, the scheduler **preempts** (evicts) lower-priority pods to make room — Borg did exactly this to let production services reclaim machines from batch jobs. This is what makes high overcommit safe: batch soaks up spare capacity but yields instantly to user-facing services.

### 7. Pods, Services, Controllers (the K8s primitives)
- **Pod** — the atom of scheduling: one or more **co-located, co-scheduled containers** sharing network namespace (one IP) and volumes. Pods are **mortal and disposable** — you don't repair them, you replace them.
- **Controllers** (ReplicaSet, Deployment, StatefulSet, DaemonSet, Job/CronJob) — manage *sets* of pods toward desired state, handling scaling, rolling updates, and self-healing.
- **Service** — a stable virtual IP + DNS name fronting an ephemeral set of pods (selected by **labels**), with load balancing; decouples clients from pod churn. (kube-proxy / IPVS implements the data path.)
- **Labels & selectors** — the loosely-coupled glue: objects are tagged with key/value **labels**, and controllers/services target them via **selectors** rather than hard references. This indirection is what makes the declarative model composable.

### 8. Extensibility (the lasting design win)
Kubernetes exposes the **same machinery to users**: **Custom Resource Definitions (CRDs)** let you add new object types, and the **Operator pattern** lets you write your own reconciliation controller for them. The control plane is thus a *generic* desired-state engine, not a fixed set of features — arguably K8s's most consequential design choice.

---

## Key Innovations

1. **Declarative desired-state + reconciliation** as the universal control primitive — robust, idempotent, self-healing, level-triggered. (Borg's biggest lesson, perfected in K8s.)
2. **Treat the datacenter as one machine** — a shared resource pool with automatic bin-packing, instead of statically assigned hosts.
3. **High utilization via workload mixing + overcommit + priority/preemption** — co-locating latency-sensitive and batch work and reclaiming unused reservations.
4. **Container-based isolation at scale** — Borg's container tech directly seeded **cgroups** and the modern container ecosystem.
5. **Shared-state, optimistic-concurrency control plane** (Omega → K8s): all components coordinate through a single consistent store (etcd) via watch + CAS, not by talking to each other.
6. **The Pod abstraction** — co-scheduled containers as the scheduling atom, separating "unit of deployment" from "unit of isolation."
7. **Label/selector indirection + Services** — decoupling identity, grouping, and networking from individual mortal instances.
8. **A generic, extensible API machine** — CRDs + Operators turn the orchestrator into a platform for building platforms.

---

## Data Model / APIs

### Borg job (conceptual config; BCL)
```text
job hello_svc {
  runtime = { cell = "cc" }
  task    = { cpu = 0.5, ram = 256MB, disk = 1GB }
  count   = 10000          // 10k identical tasks
  priority = production     // priority band drives preemption
}
```

### Kubernetes — a Deployment (desired state) + Service
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 3                      # DESIRED state — controller reconciles to this
  selector:
    matchLabels: { app: web }      # label selector — which pods this manages
  template:
    metadata:
      labels: { app: web }
    spec:
      priorityClassName: high-priority   # priority/preemption
      containers:
        - name: web
          image: registry/web:1.4.2
          resources:
            requests: { cpu: "250m", memory: "256Mi" }   # scheduler uses requests
            limits:   { cpu: "500m", memory: "512Mi" }   # cgroups enforce limits
---
apiVersion: v1
kind: Service
metadata: { name: web }
spec:
  selector: { app: web }           # fronts pods by label, stable VIP+DNS
  ports: [{ port: 80, targetPort: 8080 }]
```

### Declarative usage & the reconciliation contract
```bash
kubectl apply -f web.yaml      # submit desired state (idempotent)
kubectl scale deploy/web --replicas=5   # change desired state → controller reconciles
kubectl delete pod web-abc12   # pod vanishes → ReplicaSet recreates one (self-healing)
kubectl get deploy web -o yaml # observe spec (desired) vs status (actual)
```

### Watch API (how components stay in sync)
```text
GET /api/v1/pods?watch=true&resourceVersion=12345
  → stream of ADDED / MODIFIED / DELETED events
  (scheduler, controllers, kubelets all consume watches; no polling)
```

---

## Trade-offs & Limitations

| Decision | Benefit | Cost / Limitation |
|---|---|---|
| Centralized control plane / consistent store (Paxos/etcd) | Single source of truth, strong consistency | etcd is a **scaling & blast-radius bottleneck**; write throughput and object counts are bounded; etcd ops are delicate |
| Monolithic Borg scheduler | Simple, good global decisions | Scaling ceiling → motivated Omega's shared-state schedulers |
| Declarative + reconciliation | Self-healing, idempotent | **Eventual** convergence (not instantaneous); debugging "why hasn't it converged?" across many controllers is hard; controller bugs cause fight-loops |
| High overcommit + preemption | Great utilization | Preemption disrupts low-priority work; tuning requests/limits is an ongoing burden; misconfigured limits → OOMKills/throttling |
| Container isolation (cgroups) | Density | **Not hard multi-tenant security isolation** — shared kernel; hostile tenants need VMs/sandboxing |
| Pods are mortal | Simple, resilient model | Stateful workloads are awkward (needed StatefulSet, PV/PVC, operators); identity/storage are bolt-ons to a stateless-first model |
| Everything-through-the-API-server | Uniform, auditable, extensible | API server is a hot path; large clusters strain watch/list; etcd coupling |
| Maximal flexibility/extensibility (K8s) | A platform to build platforms | **Notorious complexity** — K8s is hard to learn/operate; "you need a platform team to run your platform" |
| Single-cluster scope | Manageable consistency domain | Multi-cluster / federation remains an unsolved-by-core, ecosystem problem |

A candid point from the *Borg, Omega, Kubernetes* paper: Borg's hardest lessons were about **what NOT to do** — e.g., conflating *job* and *naming/IP* concerns, lacking first-class labels early, and an inflexible API — which Kubernetes deliberately fixed (labels everywhere, IP-per-pod, an extensible API surface).

---

## Influence & Legacy

- **Kubernetes won the orchestration wars** (vs. Docker Swarm, Apache Mesos/Marathon, Nomad) and became the **industry-standard control plane**; every major cloud offers a managed K8s (GKE, EKS, AKS).
- **The reconciliation / controller pattern** escaped containers entirely: it's now the dominant paradigm for **infrastructure control planes** — the **Operator pattern**, **GitOps** (Argo CD, Flux: Git as the declared desired state), **Crossplane** (cloud infra via CRDs), service meshes (Istio/Linkerd), and cluster-API. "Declare desired state, reconcile" is the default mental model for modern infra.
- **Containers & cgroups:** Borg's container work fed Linux **cgroups**, which (with namespaces) underpins **Docker** and the whole container movement; **CRI/OCI** standards followed.
- **CNCF ecosystem:** K8s anchored the Cloud Native Computing Foundation and a sprawling ecosystem (Helm, Prometheus, Envoy, etcd, containerd, etc.).
- **Direct heritage:** Borg → Omega → Kubernetes is one of the clearest "research/production → open-source standard" lineages in systems history; the public papers let the industry adopt a decade of internal Google learning.

---

## Lessons for Architects

1. **Declarative beats imperative at scale.** Describe the desired end state and build a system that continuously reconciles toward it. The result is idempotent, self-healing, and far simpler to reason about than orchestrated sequences of commands.
2. **Level-triggered, not edge-triggered.** Act on the *current gap* between desired and actual, not on one-shot events. This tolerates lost messages, restarts, and stale caches — the system simply self-corrects on the next loop. This single property is why reconciliation is so robust.
3. **Coordinate through a single consistent store, not point-to-point.** Components reading/writing shared state (etcd) via watch + optimistic concurrency decouples them and avoids the combinatorial mess of services calling each other. (Omega's core insight.)
4. **Separate desired from observed (`spec` vs `status`).** Make the gap an explicit, first-class thing your controllers operate on.
5. **Design for failure as the normal case.** At fleet scale, machines die continuously; automatic rescheduling, health-checking, and replacement (not repair) must be built in from the start. Treat instances as **cattle, not pets**.
6. **Utilization is a design goal, not an afterthought.** Bin-packing, request/limit/usage distinctions, overcommit, and priority/preemption are how Google reclaimed a fleet's worth of waste — but they demand strong isolation and clear priority semantics.
7. **Indirection via labels/selectors enables loose coupling.** Don't hard-wire references to mortal instances; target *sets* by attribute and let a stable abstraction (Service) absorb churn.
8. **Build a generic engine, then expose it.** Kubernetes' lasting win is that CRDs + controllers let users extend the *same* declarative machinery. A well-designed control plane is a platform for building platforms — but beware: that generality is also the source of its operational complexity, so budget for the people to run it.
9. **Containers give density, not security boundaries.** Shared-kernel isolation is for resource management; hostile multi-tenancy still needs stronger boundaries (VMs/sandboxes).
