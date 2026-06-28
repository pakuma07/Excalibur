# Distributed Consensus & Coordination — Deep Dive

> Staff/Principal deep-dive. This is the theoretical bedrock under every system that claims "strong consistency," "linearizable reads," "leader election," or "exactly-once." We go from the impossibility results through Paxos and Raft to BFT, with precise consistency definitions and working pseudocode for the Raft RPCs.

---

## 1. The Problem & Why It Matters

**Consensus**: a set of processes must *agree* on a single value (or a sequence of values), despite failures and an asynchronous network. It must satisfy:

- **Agreement** — no two correct processes decide different values.
- **Validity (Integrity)** — the decided value was proposed by some process.
- **Termination** — every correct process eventually decides.

This abstract problem is the foundation for the things you actually build:

- **Leader election** (who is the primary?)
- **Replicated state machines** (keep N replicas of a database/config in lockstep)
- **Distributed locks / leases** (mutual exclusion across machines)
- **Atomic commit** / consistent membership / configuration (etcd, ZooKeeper, Consul)
- **Total-order broadcast** (everyone sees the same operations in the same order) — *equivalent* to consensus.

Why a staff engineer must understand this from scratch: nearly every "we just need strong consistency" requirement reduces to consensus, and consensus has **fundamental limits** (FLP, CAP) and **subtle correctness traps** (split-brain, stale leaders, lost fencing tokens) that cause the worst production incidents — silent data divergence, double-execution, and split-brain corruption that no amount of retry logic fixes.

---

## 2. The System Model (be precise — it changes everything)

Three failure/timing models, in increasing difficulty:

| Model | Timing | Failures |
|---|---|---|
| **Synchronous** | known bounds on message delay & clock drift | easiest; rarely realistic |
| **Asynchronous** | *no* bound on delays (a message may take arbitrarily long) | realistic for the internet; FLP applies |
| **Partially synchronous** | eventually-bounded delays after some unknown GST (Global Stabilization Time) | what real protocols (Raft/Paxos) assume |

Failure types:

- **Crash-stop / fail-stop** — a process halts and stays halted. (Paxos, Raft assume this.)
- **Crash-recovery** — may crash and recover with persistent state. (Real Paxos/Raft persist to disk for this.)
- **Omission** — drops messages.
- **Byzantine** — arbitrary/malicious behavior: lies, sends conflicting messages, colludes. (PBFT, blockchains assume this.)

The model dictates the impossibility results and the quorum sizes. **Crash-fault tolerance** needs `2f+1` nodes to tolerate `f` failures (majority quorum). **Byzantine-fault tolerance** needs `3f+1`.

---

## 3. FLP Impossibility

> **Fischer, Lynch, Paterson (1985):** In an *asynchronous* system, there is **no deterministic** consensus algorithm that guarantees both *safety* and *termination* if even **one** process may crash.

### 3.1 The intuition

In a fully asynchronous network you **cannot distinguish a crashed process from a slow one** (or a slow message). Any algorithm forced to decide could be driven through a sequence of message delays that keeps it in a "bivalent" state (a configuration from which either decision is still possible) forever — the adversarial scheduler always delays the one message that would force a decision. So you can't guarantee termination without risking disagreement.

```
   "Is it dead, or just slow?"  ← in async, indistinguishable.
   A protocol that decides quickly risks disagreeing if the node was only slow.
   A protocol that waits to be sure risks never deciding.
   FLP: you can't have both, deterministically, with even 1 crash.
```

### 3.2 How real systems sidestep FLP

FLP says *guaranteed* termination is impossible — it does **not** say consensus is useless. Real systems give up *guaranteed* liveness while keeping *safety* always, and recover liveness in practice:

1. **Partial synchrony + timeouts (failure detectors).** Assume the network is *eventually* well-behaved. Use timeouts to *suspect* failures. This makes a node behave like a (possibly wrong) failure detector — Chandra & Toueg (1996) showed the weakest detector for consensus is `◇W` ("eventually weak"). Raft's election timeout and Paxos's leader timeout are exactly this. Safety holds *always*; liveness holds *once the network stabilizes* (after GST).
2. **Randomization.** Ben-Or's algorithm and modern BFT (HoneyBadger) use coin flips to escape bivalence with probability 1 — terminating in expected finite time, dodging the *deterministic* clause of FLP.
3. **Stronger model.** If you truly have synchrony bounds, the impossibility doesn't apply (but you've made a strong, often false, assumption).

The practical upshot: **Raft and Paxos are always safe (never disagree), and live whenever the network is eventually stable.** During a partition or pathological asymmetry they may stall (no progress) — and that's the *correct* CAP trade-off (choose C over A), not a bug.

> **CAP corollary (Gilbert & Lynch, 2002, formalizing Brewer):** under a network **P**artition, you must choose **C** (linearizable consistency, refuse some requests) or **A** (stay available, risk inconsistency). Consensus systems are **CP**: they sacrifice availability of the minority partition to preserve consistency. (Note CAP is a coarse model; the **PACELC** extension adds: *else* (no partition) you trade **L**atency vs **C**onsistency.)

---

## 4. Replicated State Machines (the application of consensus)

The dominant pattern for building fault-tolerant services (Schneider, 1990):

> If every replica starts in the same state and applies the **same sequence of deterministic commands in the same order**, then all replicas end in the same state.

```
   clients ─▶ ┌──────────────────────────────────────────────┐
              │  Consensus layer agrees on a TOTAL ORDER of    │
              │  commands → an append-only replicated LOG      │
              └──────────────┬──────────────┬──────────────────┘
                             │              │              │
            ┌────────────────▼┐  ┌──────────▼─┐  ┌──────────▼─┐
            │ Replica A: log → │  │ Replica B  │  │ Replica C  │
            │ apply in order → │  │ same log → │  │ same log → │
            │ state machine    │  │ same state │  │ same state │
            └──────────────────┘  └────────────┘  └────────────┘
```

So **consensus = agreeing on the log = total-order broadcast**. Paxos, Raft, ZAB, VR are all engines for building this replicated log. The state machine on top must be **deterministic** (no `now()`, no unseeded random, no map-iteration-order dependence) — a classic source of replica divergence.

---

## 5. Paxos

Lamport's Paxos (TR 1989; "The Part-Time Parliament" 1998; "Paxos Made Simple" 2001) was the first proven-correct consensus protocol for asynchronous crash-fault systems. It's famously hard to understand and even harder to implement correctly.

### 5.1 Roles

- **Proposer** — proposes values.
- **Acceptor** — votes; the "memory" of the system. A **majority quorum** of acceptors decides.
- **Learner** — learns the chosen value.

(One node typically plays multiple roles.)

### 5.2 Single-decree (Basic) Paxos — two phases

Each proposal has a globally **unique, monotonically increasing proposal number** `n` (e.g., `(counter, server_id)`).

```
 PHASE 1 — PREPARE / PROMISE
   Proposer → all Acceptors:  Prepare(n)
   Acceptor: if n > highest prepare it has seen:
                promise not to accept any proposal < n;
                reply Promise(n, (n_accepted, v_accepted))  // its last accepted, if any
             else: reject

 PHASE 2 — ACCEPT / ACCEPTED
   Proposer: once it has Promises from a MAJORITY:
       if any acceptor returned an accepted value, it MUST reuse the value
          with the highest n_accepted  (this is the key safety rule);
       else it may choose its own value v.
   Proposer → all Acceptors:  Accept(n, v)
   Acceptor: if it has not promised a higher number than n:
                accept (n, v); reply Accepted(n, v)
   When a MAJORITY accepts (n, v), v is CHOSEN.
```

### 5.3 Why it's correct (the heart)

Two majorities always **intersect** in at least one acceptor. That intersecting acceptor "remembers" any previously chosen value and forces later proposers (via the Phase-1 "reuse highest-accepted value" rule) to propose the same value. Hence **once a value is chosen, no different value can ever be chosen** — Agreement holds.

```
   Majority Q1 = {A, B, C}        Majority Q2 = {C, D, E}
                    └──────┬───────┘
                  C is in both → C carries the chosen value forward
```

### 5.4 Why it's hard

- **Dueling proposers / livelock**: two proposers keep preempting each other with higher `n` (P1 prepares 5, P2 prepares 6 invalidating P1's accept, P1 prepares 7, …) — never deciding. FLP in the flesh. Fix: elect a **distinguished proposer** (a leader) so only one proposes; back off randomly.
- Basic Paxos decides **one** value with **two round trips**. Real systems need a *log* of thousands of values/sec.

### 5.5 Multi-Paxos

To agree on a *sequence* (a log), run a Paxos instance per log slot. The optimization: **elect a stable leader once**, then it skips Phase 1 for subsequent entries (Phase 1 is really "establish leadership"; once you're the established leader you only need Phase 2 per entry). This collapses steady state to **one round trip per command** — the same efficiency as Raft. Google's Chubby, Spanner, and Megastore are Multi-Paxos in production; the engineering reality ("Paxos Made Live," Chandra et al., 2007) documents how much is left unspecified by the papers (log compaction, membership, disk corruption, master leases) — which is precisely the gap Raft set out to close.

---

## 6. Raft — In Depth

Ongaro & Ousterhout, "In Search of an Understandable Consensus Algorithm" (USENIX ATC 2014). Same guarantees as Multi-Paxos, designed for **understandability** by decomposing into three sub-problems: **leader election**, **log replication**, **safety**. Raft is the basis of etcd, Consul, CockroachDB, TiKV, RethinkDB, HashiCorp Raft, and more.

### 6.1 Server states & terms

```
            times out, starts election
   ┌────────┐ ──────────────────────▶ ┌───────────┐
   │FOLLOWER│                          │ CANDIDATE │
   └────────┘ ◀────────────────────── └───────────┘
       ▲   discovers leader / higher term  │  wins majority of votes
       │                                   ▼
       │        steps down (sees higher  ┌────────┐
       └─────── term)                    │ LEADER │
                                         └────────┘
```

**Term** = a logical clock / monotonically increasing integer. Each term has **at most one leader**. Terms partition time; every RPC carries a `term`, and **any server that sees a higher term immediately reverts to follower and updates its term** — this single rule eliminates whole classes of stale-leader bugs.

### 6.2 Leader election

- Followers expect periodic **heartbeats** (empty `AppendEntries`) from the leader.
- If a follower hears nothing within its **election timeout** (randomized, e.g., 150–300 ms — randomization breaks symmetry to avoid split votes), it becomes a **candidate**, increments its term, votes for itself, and sends `RequestVote` to all peers.
- A candidate wins if it gets votes from a **majority**. It then sends heartbeats to assert leadership.
- Each server grants **at most one vote per term** (first-come) — ensuring at most one leader per term (two leaders would each need a majority, but majorities intersect → contradiction).
- Split vote → no majority → everyone times out (at randomized times) → retry next term.

### 6.3 Log replication

```
 index:    1     2     3     4     5
 leader  [x=1][y=2][x=3][z=7][y=9]      ← leader appends client cmd at next index
 term:     1    1    2    3    3

 1. Client sends command → leader appends to its log (uncommitted).
 2. Leader sends AppendEntries(term, prevLogIndex, prevLogTerm, entries[], leaderCommit)
    to followers IN PARALLEL.
 3. A follower appends only if its log matches at (prevLogIndex, prevLogTerm)
    — the LOG MATCHING property; otherwise it rejects and leader backs up.
 4. Once an entry is replicated on a MAJORITY, the leader marks it COMMITTED,
    applies it to its state machine, and returns success to the client.
 5. Leader tells followers the new commitIndex (piggybacked); they apply too.
```

Each entry stores `{term, index, command}`. **Log Matching Property:** if two logs contain an entry with the same index *and* term, then (a) they store the same command, and (b) all preceding entries are identical. This is maintained by the consistency check in step 3 and gives Raft its strong, easy-to-reason structure.

### 6.4 Safety — the subtle, critical parts

Two rules prevent a stale or under-informed server from clobbering committed data:

1. **Election restriction (up-to-date check).** A voter grants its vote only if the candidate's log is **at least as up-to-date** as its own. "Up-to-date" = higher last-log *term* wins; if equal terms, longer log wins. This guarantees the winner's log contains *all committed entries* (because a committed entry is on a majority, and the new leader needs a majority of votes — the sets intersect on a node that has the entry, which would refuse to vote for a candidate lacking it).
2. **Leaders never commit entries from previous terms by counting replicas alone.** A leader only marks an entry committed when an entry **from its own current term** reaches a majority (which, by Log Matching, carries the older entries with it). This rules out the famous "Figure 8" scenario where an older entry, though replicated on a majority, could otherwise be overwritten by a future leader. **This is the single most-often-botched part of Raft implementations** — get it wrong and you silently lose acknowledged writes.

### 6.5 Cluster membership changes

You cannot atomically switch every server's configuration at once — during the switch two disjoint majorities (old config vs new config) could each elect a leader → **split brain**. Raft offers:

- **Joint consensus** (original paper): a transitional config `C_old,new` that requires majorities of *both* old and new configurations for elections and commits. Because every decision needs both majorities, no two leaders can be elected. Once `C_old,new` is committed, transition to `C_new`.
- **Single-server changes** (later, simpler, used by etcd): add/remove one server at a time. Adding/removing a single node guarantees the old and new majorities overlap, so split brain is impossible without the joint-consensus machinery.

### 6.6 Log compaction / snapshots

The log can't grow forever. Each server independently **snapshots** its state machine, storing `{lastIncludedIndex, lastIncludedTerm, state}`, then discards log entries up to that point. A leader that has already discarded entries a slow follower needs sends an **`InstallSnapshot`** RPC instead of `AppendEntries`. Snapshotting must be careful about concurrency (snapshot while serving) — copy-on-write helps.

### 6.7 Raft RPCs — pseudocode

```text
# ============ PERSISTENT state (survives crashes; fsync before responding) ============
#   currentTerm  : latest term server has seen (init 0)
#   votedFor     : candidateId voted for in currentTerm (or None)
#   log[]        : entries {term, index, command}; index starts at 1
# ============ VOLATILE state (all servers) ============
#   commitIndex  : highest log index known committed (init 0)
#   lastApplied  : highest log index applied to state machine (init 0)
# ============ VOLATILE state (leaders; reinit after election) ============
#   nextIndex[p] : next log index to send to peer p (init = last log index + 1)
#   matchIndex[p]: highest index known replicated on peer p (init 0)

def RequestVote(args):                 # invoked by candidates to gather votes
    # args: term, candidateId, lastLogIndex, lastLogTerm
    if args.term > currentTerm:
        step_down(args.term)           # see higher term → become follower, update term
    if args.term < currentTerm:
        return (currentTerm, voteGranted=False)        # stale candidate

    up_to_date = (args.lastLogTerm  > last_log_term()) or \
                 (args.lastLogTerm == last_log_term() and
                  args.lastLogIndex >= last_log_index())   # §6.4 rule 1
    if (votedFor in (None, args.candidateId)) and up_to_date:
        votedFor = args.candidateId
        persist()                       # fsync currentTerm + votedFor
        reset_election_timer()
        return (currentTerm, voteGranted=True)
    return (currentTerm, voteGranted=False)


def AppendEntries(args):               # heartbeat (empty) or log replication
    # args: term, leaderId, prevLogIndex, prevLogTerm, entries[], leaderCommit
    if args.term > currentTerm:
        step_down(args.term)
    if args.term < currentTerm:
        return (currentTerm, success=False)            # reject stale leader

    reset_election_timer()             # valid leader → defer our own election
    current_leader = args.leaderId

    # LOG MATCHING consistency check
    if args.prevLogIndex > last_log_index() or \
       (args.prevLogIndex > 0 and log[args.prevLogIndex].term != args.prevLogTerm):
        return (currentTerm, success=False)            # gap/mismatch → leader backs up nextIndex

    # Append new entries, deleting any conflicting suffix
    for i, entry in enumerate(args.entries):
        idx = args.prevLogIndex + 1 + i
        if idx <= last_log_index() and log[idx].term != entry.term:
            truncate_log_from(idx)     # delete conflicting entry and all that follow
        if idx > last_log_index():
            log.append(entry)
    persist()

    if args.leaderCommit > commitIndex:
        commitIndex = min(args.leaderCommit, last_log_index())
    apply_committed_to_state_machine()                  # advance lastApplied → commitIndex
    return (currentTerm, success=True)


def on_election_timeout():             # follower heard no heartbeat
    currentTerm += 1
    state = CANDIDATE
    votedFor = me
    persist()
    votes = 1
    reset_election_timer()             # randomized timeout
    for p in peers:
        r = send RequestVote(term=currentTerm, candidateId=me,
                             lastLogIndex=last_log_index(), lastLogTerm=last_log_term())
        if r.term > currentTerm: step_down(r.term); return
        if r.voteGranted: votes += 1
    if votes > len(cluster) // 2:      # MAJORITY
        become_leader()                # reinit nextIndex/matchIndex; send heartbeats now


def leader_replicate():                # leader loop, per peer
    for p in peers:
        if last_log_index() >= nextIndex[p]:
            entries = log[nextIndex[p]:]
            r = send AppendEntries(term=currentTerm, leaderId=me,
                    prevLogIndex=nextIndex[p]-1, prevLogTerm=log[nextIndex[p]-1].term,
                    entries=entries, leaderCommit=commitIndex)
            if r.term > currentTerm: step_down(r.term); return
            if r.success:
                matchIndex[p] = nextIndex[p] + len(entries) - 1
                nextIndex[p]  = matchIndex[p] + 1
            else:
                nextIndex[p] -= 1      # back up and retry (consistency repair)
    advance_commit_index()


def advance_commit_index():
    # Find highest N such that N replicated on a majority AND log[N].term == currentTerm.
    for N in range(last_log_index(), commitIndex, -1):
        replicas = 1 + sum(1 for p in peers if matchIndex[p] >= N)
        if replicas > len(cluster)//2 and log[N].term == currentTerm:   # §6.4 rule 2 !!
            commitIndex = N
            break
    apply_committed_to_state_machine()
```

### 6.8 A concrete Raft walkthrough (the Figure-8 hazard, why §6.4 rule 2 exists)

```
 Cluster {S1..S5}.  S1 is leader in term 2, replicates entry @index2 (term 2) to S1,S2.
   S1:[_, e2²]   S2:[_, e2²]   S3:[_]   S4:[_]   S5:[_]      (e2² NOT yet on a majority)

 S1 crashes.  S5 becomes leader in term 3 (votes from S3,S4,S5 — their logs lack index2,
   but that's allowed: e2² wasn't committed). S5 writes its own e2³ at index2.

 If a NEW leader were allowed to commit e2² merely because it later reaches 3 nodes,
   then e2² could be declared committed — and *then* be overwritten by e2³ from term 3.
   That would LOSE A COMMITTED ENTRY.  Catastrophe.

 Raft's rule: a leader only commits index2 once it has replicated an entry FROM ITS
 CURRENT TERM on a majority. Committing the current-term entry drags index2 along with
 it (Log Matching), at which point it is safe and permanent.
```

This is why the `log[N].term == currentTerm` guard in `advance_commit_index()` is non-negotiable. Implementations that count replicas without this check have lost acknowledged data in the wild.

---

## 7. ZAB & Viewstamped Replication (brief)

### 7.1 ZAB — ZooKeeper Atomic Broadcast

ZooKeeper's protocol (Junqueira, Reed, Serafini, 2011). Like Raft, it's leader-based total-order broadcast, but it was designed around ZooKeeper's **primary-backup** model and **two ordering guarantees** clients rely on:

- A leader ("primary") assigns each transaction a monotonically increasing **zxid** = `(epoch, counter)` — directly analogous to Raft's `(term, index)`.
- Phases: **leader election → discovery → synchronization → broadcast.** Broadcast is a 2-phase (propose → ack-from-majority → commit) pipeline, like Raft's replicate-then-commit.
- Distinctive: ZAB guarantees **prefix ordering** and that a new leader's history is a superset of any committed transactions — it must "catch up" before broadcasting (the synchronization phase). The `epoch` change on each new leadership is the ZAB analogue of a Raft term bump.

Used by ZooKeeper (and thus historically Kafka, HBase, Hadoop HA for coordination/metadata).

### 7.2 Viewstamped Replication (VR)

Oki & Liskov (1988), revised "VR Revisited" (Liskov & Cowling, 2012). *Predates and is essentially equivalent to Multi-Paxos*, discovered independently from a state-machine-replication angle. A **view** ≈ a term; the **primary** ≈ the leader; a **view change** ≈ leader election. VR makes the RSM framing primary (Paxos came from the agreement angle). The three are deeply related — Raft, Multi-Paxos, ZAB, and VR are all leader-based, term/epoch/view-numbered, majority-quorum, log-replication protocols; they differ in emphasis and engineering detail, not in their core guarantees.

---

## 8. Linearizability vs Serializability (get these *exactly* right)

These are constantly confused. They are **different guarantees about different things** and are *orthogonal* (you can have one, the other, both, or neither).

### 8.1 Linearizability (Herlihy & Wing, 1990) — a *recency* / single-object property

> Every operation appears to take effect **atomically at some single instant between its invocation and its response**, and that effective order is consistent with **real time**: if operation A *completes* before operation B *begins* (in wall-clock time), then A's effect is ordered before B's.

It is a **consistency model for a single register/object** (extended to a single replicated system). Key consequences:

- It's the "C" in CAP and the strongest single-object guarantee.
- **A read returns the value of the most recently completed write** — no stale reads. This is why "linearizable" ⇒ "after my write returns, everyone sees it."
- Consensus systems (Raft/Paxos via the leader, or quorum reads) provide linearizable operations. (In Raft, naive reads from the leader can be *stale* if a new leader was elected during a partition — you must confirm leadership via a heartbeat/`ReadIndex` quorum or a lease before serving a "linearizable" read.)

```
   real time ──────────────────────────────────────────────▶
   C1:  |── write(x=1) ──|
   C2:                       |── read(x) ──|   MUST return 1 (write completed before read began)
   C3:        |── read(x) ─────|               MAY return 0 or 1 (overlaps the write)
```

### 8.2 Serializability — a *transaction-isolation* / multi-object property

> The result of executing a set of **transactions** (each possibly touching many objects) is **equivalent to some serial (one-at-a-time) execution** of those transactions.

It is the gold-standard **isolation** level for ACID databases (the "I"). Crucially, serializability says **nothing about real-time order** — the equivalent serial order may reorder transactions arbitrarily, including putting an earlier-completed transaction *after* a later one.

### 8.3 Strict Serializability = Serializability + Linearizability

The combination — transactions are serializable *and* the serial order respects real-time order. This is what **Google Spanner** provides ("external consistency"), and what CockroachDB targets. It's the strongest practical guarantee and the most expensive.

```
                       single object        many objects (transactions)
   no real-time order:  (sequential cons.)   SERIALIZABILITY
   real-time ordered:   LINEARIZABILITY      STRICT SERIALIZABILITY
```

| | Concerns | Orders by real time? | Scope |
|---|---|---|---|
| **Linearizability** | recency of single-object ops | **yes** | one object |
| **Serializability** | isolation of transactions | **no** | many objects |
| **Strict serializability** | both | **yes** | many objects (transactions) |

The interview-killer summary: *Linearizability is about **time** (single object, no stale reads). Serializability is about **transactions** (multi-object isolation, any equivalent serial order). They compose into strict serializability.*

---

## 9. Quorum Systems & Flexible Quorums

### 9.1 Majority quorums

For `N` replicas, a **read quorum R** and **write quorum W** guarantee strong consistency iff:

- `W + R > N`  (every read intersects every write → reads see the latest write)
- `W > N/2`    (every two writes intersect → no two conflicting writes commit concurrently)

`N=3, W=2, R=2` is the classic strongly-consistent setup (tolerates 1 failure). Dynamo-style systems (`R + W > N` *not* required) deliberately allow `W+R ≤ N` for higher availability, accepting **eventual** consistency + conflict resolution (vector clocks, CRDTs, last-write-wins).

### 9.2 Flexible Paxos (Howard, Malkhi, Spiegelman, 2016)

A beautiful refinement: Paxos/Raft don't actually need *every* phase to use a majority. They only need the **Phase-1 quorum (Q1, leader election)** and the **Phase-2 quorum (Q2, replication)** to **intersect**:

```
   |Q1| + |Q2| > N         (instead of requiring each to be a strict majority)
```

This lets you tune the trade-off: e.g., with `N=10`, choose `Q2=3` (fast, cheap commits — only 3 acks needed per write) at the cost of `Q1=8` (expensive, rare leader elections must contact 8). Since steady-state replication is frequent and elections are rare, this can dramatically cut write latency/cost. The insight underlies modern protocols that decouple the two quorum sizes.

### 9.3 The "sloppy quorum" pitfall

Some AP systems use *sloppy quorums + hinted handoff* (write to *any* W reachable nodes during a partition, hand off later). This is **not** a true quorum — it does **not** guarantee read-your-writes and can resurrect stale data. Know the difference: a strict quorum guarantees intersection; a sloppy one trades that guarantee for availability.

---

## 10. Byzantine Fault Tolerance

All of the above assume **crash faults** (nodes fail by stopping, never lie). **Byzantine faults** (Lamport, Shostak, Pease, "The Byzantine Generals Problem," 1982) model **arbitrary/malicious** behavior — a node can send different, contradictory messages to different peers, forge, or collude.

### 10.1 The bound: 3f+1

To tolerate `f` Byzantine nodes you need **`N ≥ 3f+1`** (vs `2f+1` for crash faults). Intuition: with up to `f` liars *and* up to `f` unreachable honest nodes, you must still find an honest majority among the `N−f` you can hear — requiring quorums of `2f+1` out of `3f+1` so any two quorums intersect in at least `f+1` nodes (at least one honest node in common).

### 10.2 PBFT intuition (Castro & Liskov, 1999)

Practical Byzantine Fault Tolerance made BFT efficient enough for real systems. A primary orders requests; replicas cross-check via a **three-phase** commit so that no malicious primary can make honest replicas diverge:

```
   client ─▶ PRIMARY
     PRE-PREPARE :  primary assigns sequence n, multicasts (request, n)
     PREPARE     :  each replica multicasts PREPARE; on 2f matching PREPAREs
                    a replica is "prepared" (agrees on ordering for view v)
     COMMIT      :  each replica multicasts COMMIT; on 2f+1 matching COMMITs
                    it executes and replies to the client
   client accepts the result after f+1 matching replies (≥1 from an honest node)
   View change: if the primary is faulty/slow, replicas elect a new primary (new view).
```

The two all-to-all rounds (PREPARE, COMMIT) are what defeat a lying primary: an honest replica won't commit unless it sees `2f+1` agreement, which necessarily includes honest nodes that would refuse to endorse a forked order. Cost: O(N²) messages per request — which limited classic PBFT to small `N` (tens of nodes).

### 10.3 Where BFT is actually used

- **Permissioned blockchains / consortium ledgers** — Hyperledger Fabric, Tendermint/CometBFT (Cosmos), and many "L1" chains use PBFT-derived BFT for finality among a known validator set.
- **Permissionless blockchains** — Bitcoin's Nakamoto consensus (Proof-of-Work) and Ethereum's Proof-of-Stake (Gasper/Casper FFG + LMD-GHOST) solve BFT consensus in *open membership* (anyone can join, Sybil-resistant via cost/stake) — a harder setting than PBFT's fixed validator set, traded against probabilistic vs deterministic finality.
- **Scalable BFT**: HotStuff (Yin et al., 2019 — used by Diem/LibraBFT) reduced PBFT's communication to *linear* per view with a rotating leader and pipelined phases; it underlies many modern BFT chains.

> Reality check for enterprise architects: **you almost never need BFT internally.** Inside your own datacenter, nodes don't lie maliciously — crash-fault Raft/Paxos is the right tool. BFT's cost (3f+1 nodes, O(N²) messaging) is justified only when participants are *mutually distrusting* (cross-organization ledgers, public chains).

---

## 11. Leases & Fencing Tokens

Even with perfect consensus, a subtle, lethal bug lurks in *using* a lock/leadership: the **stale leader / stale lock-holder** problem.

### 11.1 The problem

A node acquires a lock/lease, then **pauses** (GC pause, VM migration, long syscall, network partition). Its lease *expires*; the system elects a new holder. The paused node **wakes up believing it still holds the lock** and writes to shared storage — corrupting data. No timeout alone fixes this, because the paused node can't know time passed.

```
   t0  Client A acquires lease (TTL 10s) ─▶ starts writing
   t1  A stalls (15s GC pause) ............................
   t11 Lease expires; system grants lease to Client B ─▶ B writes
   t16 A wakes, STILL THINKS it holds the lease ─▶ A writes  ❌ CORRUPTION (split-brain write)
```

### 11.2 The fix: fencing tokens

The lock service issues a **monotonically increasing token** with every lock grant. Every write to the protected resource carries its token, and **the resource rejects any write with a token lower than the highest it has already seen.**

```
   Lock service grants:  A → token 33     (later)  B → token 34
   A (stale) writes with token 33 ─▶ storage saw 34 already ─▶ REJECTED ✅
   B writes with token 34 ─▶ accepted
```

This converts a timing problem (unsolvable with clocks alone in async systems) into a monotonicity check (always correct). Raft's `term`, ZAB's `epoch`/`zxid`, and Paxos's proposal number are all **fencing tokens** — and the protected resource (or storage layer) **must actually check them**. This is the practical reason "just use a distributed lock" (e.g., naive Redis SETNX locks without fencing) is dangerous: the lock can be correct yet the *use* of it corrupts data. (This is the crux of the well-known Kleppmann–Redlock debate — fencing tokens, not lock TTLs, are what make distributed locking safe.)

### 11.3 Leader leases (read optimization)

In Raft/Paxos, serving linearizable reads normally requires a round trip to a quorum (to confirm you're still leader). A **leader lease** lets the leader serve reads locally for the lease duration *without* a round trip — but this trades correctness for clock assumptions: it's only safe if clock drift is bounded (a synchrony assumption). Spanner's **TrueTime** (GPS + atomic clocks giving bounded-uncertainty timestamps) is the rigorous version — it waits out the uncertainty interval (`commit-wait`) to guarantee external consistency without violating safety under clock skew.

---

## 12. Key Takeaways

1. **FLP is the floor.** Deterministic async consensus can't guarantee both safety and termination with even one crash. Real systems (Raft/Paxos) keep **safety always** and recover **liveness** under partial synchrony (timeouts) — and may correctly *stall* during partitions (CP in CAP).
2. **Consensus = the replicated log = total-order broadcast.** Build fault-tolerant services as deterministic replicated state machines fed by an agreed log.
3. **Paxos is correct but hard** (dueling proposers, single-decree; Multi-Paxos adds a stable leader for one-RTT steady state). **Raft is Multi-Paxos made understandable** via leader election + log replication + safety, with explicit membership-change and snapshot mechanisms.
4. **The two Raft safety rules are where data is lost if you're sloppy:** the up-to-date election restriction, and *never commit a previous term's entry by replica-count alone* — only commit once a current-term entry reaches a majority (Figure-8). The `log[N].term == currentTerm` check is mandatory.
5. **ZAB, VR, Multi-Paxos, Raft are siblings:** leader-based, term/epoch/view-numbered, majority-quorum log replication. Different framing, same guarantees.
6. **Linearizability ≠ serializability.** Linearizability = recency of single-object ops, *respects real time*. Serializability = transaction isolation, *ignores real time*. Strict serializability = both (Spanner). Confusing these is a classic senior-vs-staff tell.
7. **Quorums must intersect.** `W+R>N` and `W>N/2` for strong consistency. **Flexible Paxos**: only the election and replication quorums must intersect (`|Q1|+|Q2|>N`), enabling cheap frequent commits at the cost of expensive rare elections. Beware sloppy quorums — they aren't real quorums.
8. **BFT needs 3f+1** (vs 2f+1 for crash) and PBFT's two all-to-all rounds (O(N²)); it's for *mutually distrusting* participants (consortium/public blockchains), **not** your internal datacenter — use crash-fault Raft there.
9. **Consensus gives you a fencing token; you must use it.** Term/epoch/proposal numbers are monotonic tokens — the protected resource must reject lower tokens, or a stalled stale leader corrupts data. Timeouts alone never fix split-brain; fencing tokens do.

---

## Seminal References

- M. Fischer, N. Lynch, M. Paterson, "Impossibility of Distributed Consensus with One Faulty Process," JACM 1985 (FLP).
- L. Lamport, "The Part-Time Parliament," ACM TOCS 1998; "Paxos Made Simple," 2001.
- T. Chandra, R. Griesemer, J. Redstone, "Paxos Made Live," PODC 2007.
- D. Ongaro & J. Ousterhout, "In Search of an Understandable Consensus Algorithm (Extended Version)," USENIX ATC 2014 (Raft).
- F. Schneider, "Implementing Fault-Tolerant Services Using the State Machine Approach," ACM Computing Surveys 1990.
- B. Oki & B. Liskov, "Viewstamped Replication," PODC 1988; B. Liskov & J. Cowling, "Viewstamped Replication Revisited," MIT-CSAIL-TR 2012.
- F. Junqueira, B. Reed, M. Serafini, "Zab: High-performance broadcast for primary-backup systems," DSN 2011.
- M. Herlihy & J. Wing, "Linearizability: A Correctness Condition for Concurrent Objects," ACM TOPLAS 1990.
- E. Brewer, CAP conjecture (PODC 2000 keynote); S. Gilbert & N. Lynch, "Brewer's Conjecture and the Feasibility of Consistent, Available, Partition-Tolerant Web Services," SIGACT News 2002.
- L. Lamport, R. Shostak, M. Pease, "The Byzantine Generals Problem," ACM TOPLAS 1982.
- M. Castro & B. Liskov, "Practical Byzantine Fault Tolerance," OSDI 1999.
- M. Yin et al., "HotStuff: BFT Consensus with Linearity and Responsiveness," PODC 2019.
- H. Howard, D. Malkhi, A. Spiegelman, "Flexible Paxos: Quorum Intersection Revisited," OPODIS 2016.
- T. Chandra & S. Toueg, "Unreliable Failure Detectors for Reliable Distributed Systems," JACM 1996.
- J. Corbett et al., "Spanner: Google's Globally-Distributed Database," OSDI 2012 (TrueTime, external consistency).
```
