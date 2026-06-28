# 02 — Performance & Queueing Theory

> **Audience:** staff/principal. Capacity planning and latency are not guesswork — they obey laws. This doc derives the ones you need to reason about real systems, with worked numbers and runnable Python.
>
> **Primary sources:** Little (1961, the L=λW proof); Kendall (queue notation); Amdahl (1967); Neil Gunther, *Guerrilla Capacity Planning* & *Analyzing Computer System Performance with Perl::PDQ* (the Universal Scalability Law); Gil Tene on coordinated omission; Google SRE book (overload).

---

## 1. Why this matters at scale

Two engineers look at the same dashboard. One says "CPU is at 75%, we're fine." The other says "we're one traffic spike from a latency cliff." The difference is whether you understand **queueing theory**.

At scale, the relationship between load and latency is **non-linear**. Latency stays flat as you add load… and then explodes. Capacity planning, autoscaling thresholds, SLOs, and incident post-mortems all hinge on knowing *where that knee is* and *why it's there*. These laws let you:

- Size a system *before* it falls over (capacity planning).
- Pick a safe utilization target (why "run at 100%" is a category error).
- Predict whether adding machines will actually help (Amdahl/USL — sometimes it makes things *worse*).
- Read latency numbers honestly (percentiles, coordinated omission).

---

## 2. Little's Law — the most useful equation in systems

> **L = λ · W**
> Average number of items *in the system* (`L`) = average arrival rate (`λ`) × average time each item spends in the system (`W`).

Proven by John Little (1961). It is astonishingly general: it makes **no assumptions** about arrival distribution, service distribution, or scheduling discipline. It holds for any stable system in steady state. That generality is why it's the workhorse of capacity planning.

### 2.1 Worked examples

- **Concurrency from throughput × latency.** A service handles `λ = 5000 req/s` and each request takes `W = 20 ms = 0.02 s`. Then the average number of requests *in flight* is `L = 5000 × 0.02 = 100`. So you need a thread pool / connection pool / concurrency limit of **~100** (plus headroom) — not 5000.

- **Sizing a thread pool the other way.** You have `L = 200` worker threads and each job takes `W = 0.5 s`. Max sustainable throughput `λ = L / W = 200 / 0.5 = 400 jobs/s`. Push past 400 and the queue grows without bound.

- **Database connection pool.** Queries average `W = 5 ms`, you serve `λ = 2000 qps`. In-flight queries `L = 2000 × 0.005 = 10`. A pool of 10–20 connections suffices; a pool of 500 just adds context-switching and memory pressure. (This is the HikariCP "small pool" argument made formal.)

> The trap: people size pools by peak QPS. Little's Law says size them by **QPS × latency**. If latency is low, you need a *small* pool even at huge QPS.

### 2.2 Applied recursively

Little's Law nests. Apply it to the *queue* alone (`L_q = λ · W_q`, items waiting) or the *server* alone (`L_s = λ · W_s = utilization`). That decomposition is what M/M/1 below exploits.

---

## 3. Utilization vs latency — the hockey stick

Here is the single most important graph in performance engineering, and the reason "we're only at 80% CPU" is not reassuring.

For an **M/M/1** queue (Poisson arrivals, exponential service, 1 server), the average time in system is:

> **W = W_s / (1 − ρ)**

where `W_s` is the raw service time (time with zero queueing) and `ρ` (rho) is **utilization** = `λ / μ` (arrival rate ÷ service rate), `0 ≤ ρ < 1`.

The `1/(1−ρ)` term is the **latency multiplier from queueing**. Watch it:

```
ρ      1/(1-ρ)   meaning
0.50    2.0      latency is 2x service time
0.70    3.3
0.80    5.0
0.90   10.0      <- you are now 10x slower than an idle system
0.95   20.0
0.99  100.0      <- the cliff
0.999 1000.0
```

```
latency
  |                                              *
  |                                          *
  |                                     *
  |                              *
  |                      *
  |            *  *  *                <- flat, "everything's fine"
  |__*__*__________________________________________  utilization ρ
  0%        50%      70%   80%  90%  95%  99%
                              ^ the knee / hockey stick
```

**Why?** Variability. Requests don't arrive evenly; bursts collide. The closer to 100% utilization, the less spare capacity to absorb a burst, so queues — and latency — blow up. At ρ=1 the queue is infinite.

> **This is why you never run a latency-sensitive service at 100% utilization.** The standard target is **60–80%**, leaving headroom for bursts, deploys, and node failures. Batch/throughput systems (where latency doesn't matter) can run hotter.

This is also the **page-cache cliff** from [01](01_storage_engines.md) and the root of the **tail-latency** problem in [03](03_tail_latency.md): the tail is dominated by the requests that hit a momentary queue.

---

## 4. M/M/1 and M/M/c — queue intuition

**Kendall's notation** `A/S/c`: arrival process / service process / number of servers. `M` = Markovian (memoryless = Poisson arrivals / exponential service).

- **M/M/1** — one server. Simple, pessimistic, great for intuition. `W = W_s/(1−ρ)`.
- **M/M/c** — `c` parallel servers (a thread pool, a fleet behind a load balancer). The key qualitative result: **a single pool of `c` servers crushes `c` separate single-server pools.** Pooling lets any free server take any waiting request, so a momentary burst on one "lane" is absorbed by idle capacity elsewhere. This is the **resource-pooling principle** — the theoretical justification for shared thread pools, shared connection pools, and a single global queue over per-shard queues.

> Practical corollary: **head-of-line blocking is expensive.** If you split one queue into many (per-connection, per-tenant) you lose the M/M/c pooling benefit and your tails get worse. Conversely, when you *want* isolation (bulkheads, [03](03_tail_latency.md)), you knowingly trade pooling efficiency for blast-radius containment.

The Erlang-C formula gives the exact M/M/c waiting probability; for staff-level reasoning the qualitative "pooling wins, and the `1/(1−ρ)` cliff still applies to the pool as a whole" is usually enough.

---

## 5. Amdahl's Law — the ceiling on parallel speedup

Gene Amdahl (1967). If a fraction `p` of the work is parallelizable and `(1−p)` is inherently serial, the speedup with `N` processors is:

> **Speedup(N) = 1 / ( (1 − p) + p/N )**

As `N → ∞`, speedup → **1/(1−p)**. The serial fraction is a hard ceiling.

```
serial fraction (1-p)    max speedup (N=inf)
   1%   (p=0.99)             100x
   5%   (p=0.95)              20x
  10%   (p=0.90)              10x
  25%   (p=0.75)               4x
```

**Implication:** if 5% of your request path is serial (a global lock, a single coordinator, a non-shardable step), no amount of horizontal scaling gets you past **20×**. Find and kill the serial fraction; throwing machines at it is futile beyond the ceiling.

---

## 6. The Universal Scalability Law (USL) — the law that bites at scale

Amdahl assumes adding workers never *hurts*. Reality is worse: coordination between workers (cache coherency, lock contention, cross-talk) can make a system get **slower** past some point. Neil Gunther's **Universal Scalability Law** captures this with two coefficients:

> **C(N) = N / ( 1 + α(N − 1) + β·N·(N − 1) )**

where `C(N)` is throughput (capacity) at `N` workers/load, and:
- **α (alpha) = contention** — the serial/queueing fraction (this is the Amdahl term; α-only USL ≡ Amdahl).
- **β (beta) = coherency** — the cost of keeping workers *consistent* with each other (cache-line bouncing, gossip, crosstalk). Grows as `N(N−1)` — **pairwise**, hence quadratic.

The crucial difference: when **β > 0**, `C(N)` has a **maximum** and then *declines*. There is an optimal concurrency:

> **N* = sqrt( (1 − α) / β )**

Adding workers past `N*` reduces throughput. This is **retrograde scalability** — the thing that turns "add more nodes" into an incident. Real systems with β>0: anything with a shared lock, distributed caches that gossip, databases that do cross-node coordination.

```
throughput C(N)
  |                  ___
  |               _-     -_        <- USL with beta>0: peaks then DECLINES
  |            _-           -_
  |          _-               -_
  |        _-      Amdahl (beta=0): rises then plateaus
  |      _- . . . . . . . . . . . . . . . .
  |    _-.
  |  _-.
  |_-________________________________________ N (concurrency)
              ^ N* = sqrt((1-a)/b)
```

### 6.1 Fitting the USL to real measurements (runnable Python)

You measure throughput at several concurrency levels and fit `α, β`. This is exactly what you do with load-test data to predict the scaling ceiling *before* you hit it.

```python
"""usl_fit.py — fit the Universal Scalability Law to measured throughput and
report the optimal concurrency N*.  Uses SciPy if available, else a tiny
NumPy-only grid+refine fallback so it runs with no extra deps.
Run: python usl_fit.py"""
import numpy as np

def usl(N, alpha, beta, gamma):
    # gamma = throughput of a single worker (C(1)); we normalize it out of the shape.
    return gamma * N / (1.0 + alpha * (N - 1.0) + beta * N * (N - 1.0))

# Synthetic-but-realistic load test: throughput (req/s) at increasing concurrency.
# Note it rises, plateaus, then DECLINES at high N -> classic coherency (beta>0).
N      = np.array([1,   2,    4,    8,    16,   32,   48,   64,   96,   128], float)
through= np.array([100, 195,  365,  640,  980,  1180, 1170, 1080, 880,  690], float)

try:
    from scipy.optimize import curve_fit
    (alpha, beta, gamma), _ = curve_fit(
        usl, N, through, p0=[0.05, 0.0005, 100.0],
        bounds=([0, 0, 1], [1, 1, 1e6]), maxfev=100000,
    )
except ImportError:
    # NumPy-only least-squares: coarse grid over (alpha, beta), then gamma is
    # solved in closed form per candidate (linear in gamma).
    best, params = np.inf, None
    for a in np.linspace(0, 0.2, 201):
        for b in np.linspace(0, 0.005, 201):
            shape = N / (1.0 + a * (N - 1.0) + b * N * (N - 1.0))
            g = float(shape @ through / (shape @ shape))   # least-squares gamma
            err = float(np.sum((g * shape - through) ** 2))
            if err < best:
                best, params = err, (a, b, g)
    alpha, beta, gamma = params

N_star = np.sqrt((1 - alpha) / beta) if beta > 0 else float("inf")
max_through = usl(N_star, alpha, beta, gamma) if np.isfinite(N_star) else None

print(f"contention   alpha = {alpha:.4f}")
print(f"coherency    beta  = {beta:.6f}")
print(f"single-worker gamma= {gamma:.2f} req/s")
print(f"optimal concurrency N* = {N_star:.1f}")
print(f"peak throughput       = {max_through:.0f} req/s")
print(f"Amdahl ceiling (beta=0) would be 1/alpha = {1/alpha:.1f}x single-worker")

# Capacity-planning takeaway: adding workers past N* REDUCES throughput.
for n in (64, 96, 128, 160):
    print(f"  N={n:3d} -> predicted {usl(n, alpha, beta, gamma):.0f} req/s")
```

Running this fits `α` (contention) and `β` (coherency), reports `N*` (the concurrency beyond which you go *backwards*), and shows the Amdahl ceiling `1/α`. **This is how you decide your max pool size / max node count from data instead of folklore.**

---

## 7. Throughput vs latency — they are not the same axis

- **Throughput** = work per unit time (req/s). **Latency** = time per unit of work (s/req). They trade off.
- **Batching** raises throughput and *raises* latency (you wait to fill the batch). Examples: Kafka `linger.ms`, Nagle's algorithm, GPU inference batching ([08]).
- A system can have great throughput and terrible tail latency simultaneously (high ρ → high throughput → long queues → bad p99).
- **Optimize for the one your SLO names.** An interactive API optimizes p99 latency (and runs at lower ρ to protect it); a nightly ETL optimizes throughput (and runs ρ near 1).

By Little's Law these are linked: `L = λ·W`. At fixed concurrency `L`, pushing `λ` (throughput) up forces `W` (latency) up. You cannot independently maximize both at fixed parallelism.

---

## 8. Coordinated omission — the measurement bug that hides your worst latency

A subtle, career-defining trap (Gil Tene). A naive load generator sends a request, **waits for the response**, then sends the next. If the system stalls for 1 second, the load generator *also* stalls — so it simply **doesn't send** the requests it should have sent during the stall. Those missing requests would have been the slow ones. The result: your latency histogram **omits exactly the bad measurements**, and your reported p99 is a fantasy.

```
intended schedule:  | req | req | req | req | req |   (every 20ms)
server stalls 100ms:        [=========STALL=========]
naive client sees:  | req |            ...waits...    | req |
                              ^ never sent the 5 requests it owed during the stall
                              ^ and the requests that WERE in flight should be
                                charged the FULL stall time, not just their own
```

**Two fixes:**
1. **Open-loop / constant-rate** load generation: send on schedule regardless of responses (wrk2, not wrk; the corrected design).
2. **Correct for it in analysis**: any request that completes later than its *intended* start time is back-charged the extra wait (HdrHistogram's `recordValueWithExpectedInterval`).

> Staff-level red flag: if a benchmark uses a closed-loop client and reports a great p99 under overload, **distrust it**. Coordinated omission can hide *orders of magnitude* of tail latency.

---

## 9. Percentiles vs averages — why the mean lies

> The average is the number that describes *nobody's* experience.

- Latency distributions are **right-skewed and long-tailed** (a few very slow requests). The mean is dragged around by outliers and hides the tail; **percentiles** describe actual user experience.
- Report **p50 (median), p90, p99, p99.9**, and **max**. The gap between p50 and p99 is your *consistency*; a p50 of 10 ms with a p99 of 2 s is a different system than 10 ms / 30 ms even at the same mean.
- **Percentiles do not average and do not add naively.** You cannot average p99s across shards to get the fleet p99. To aggregate, merge **histograms** (HdrHistogram, t-digest, DDSketch), not the precomputed percentiles.
- **SLOs are stated in percentiles**: "p99 < 200 ms over 30 days." The error budget is `(1 − SLO target)` and drives release velocity (SRE book).
- At fan-out, the tail is everything: see [03](03_tail_latency.md) — with enough parallel sub-requests, the p99 of *one* becomes the p50 of the *whole*.

---

## 10. Capacity planning from these laws

Put it together into a procedure:

1. **Measure** service time `W_s` and the arrival rate `λ` you must serve (with headroom for peaks — use the peak, not the mean, and apply a peak-to-mean ratio, often 2–4×).
2. **Concurrency needed** (Little): `L = λ · W`. Size pools/threads/connections to `L` + headroom.
3. **Pick a utilization target** below the knee: ρ ≤ 0.7 for latency-sensitive (the `1/(1−ρ)` curve). Servers needed ≈ `λ / (μ · ρ_target)` where `μ = 1/W_s` per server.
4. **Check the scaling ceiling** (USL): fit `α, β` from a load test; confirm your target `N` is well below `N*`. If `N*` is near your target, you have a coherency problem to fix *first* (sharding, less coordination), because more nodes won't help.
5. **Validate with open-loop load testing** (avoid coordinated omission) and read **p99/p99.9**, not the mean.
6. **Add failure headroom**: size so that losing a node/AZ still keeps ρ under the knee (the "N+1" / "N+2" rule).

### Worked capacity plan

> Target: serve **peak 30,000 req/s** at p99 < 150 ms. Measured service time `W_s = 25 ms` ⇒ `μ = 40 req/s` per core-worker. Target `ρ = 0.65` to protect the tail.
>
> - Effective capacity per worker = `μ · ρ = 40 × 0.65 = 26 req/s`.
> - Workers needed = `30000 / 26 ≈ 1154`.
> - Add N+2 AZ headroom (3 AZs, survive losing one) ⇒ provision `1154 × 1.5 ≈ 1731` workers.
> - In-flight concurrency (Little) = `λ · W ≈ 30000 × 0.025 = 750` at the service-time floor; the actual `W` under ρ=0.65 is `W_s/(1−ρ) = 25/0.35 ≈ 71 ms` ⇒ `L ≈ 30000 × 0.071 ≈ 2130`. Pools must accommodate ~2130 in-flight, spread across workers.
> - USL check: fit from load test; ensure `N* >> 1731` per shard, else shard further.

This is the difference between "we guessed 1000 boxes" and a defensible number with failure headroom.

---

## 11. Real systems & where these laws show up

- **HikariCP / connection pools** — the "small pool" guidance is Little's Law: pool size = QPS × query-time, not QPS.
- **Kubernetes HPA / autoscalers** — target CPU 60–70%, *not* 95%, because of the `1/(1−ρ)` knee.
- **Kafka / TCP Nagle / GPU batching** — explicit throughput-vs-latency batching knobs.
- **Google SRE** — overload, load shedding, and the "don't run at 100%" doctrine come straight from queueing theory.
- **wrk2 / HdrHistogram / DDSketch / t-digest** — built specifically to defeat coordinated omission and to merge percentiles correctly.
- **Databases under contention** — the USL β term is exactly what you see when adding more app servers *reduces* DB throughput due to lock/latch coherency.

---

## 12. Key takeaways

1. **Little's Law (L = λW)** is universal: size every pool by `throughput × latency`, never by raw QPS.
2. **The hockey stick (`W = W_s/(1−ρ)`)** is why latency-sensitive systems run at **60–80% utilization** — the last 20% of capacity costs you 5–10× latency.
3. **M/M/c pooling beats many small queues** (resource pooling); head-of-line blocking and per-tenant queues sacrifice that for isolation — a deliberate trade.
4. **Amdahl** caps speedup at `1/(1−p)`; the **USL** is worse — with coherency (β>0), throughput **peaks at N\* = √((1−α)/β) and then declines**. Fit it from data before scaling out.
5. **Throughput and latency are different axes** linked by Little's Law; batching trades one for the other.
6. **Coordinated omission** makes closed-loop benchmarks lie about the tail — use open-loop generators and HdrHistogram.
7. **Report percentiles (p99/p99.9), merge histograms not percentiles**, and write SLOs in percentiles.
8. **Capacity planning is arithmetic**, not folklore: measure, apply Little + the ρ knee + the USL ceiling + failure headroom.

> Read next: [03 — Tail Latency at Scale](03_tail_latency.md) — where the `1/(1−ρ)` tail meets fan-out and becomes the dominant problem in large systems.
