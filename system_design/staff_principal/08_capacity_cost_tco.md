# 08 — Capacity Planning, Cloud Cost & TCO (FinOps for Architects)

> **Audience:** Staff/Principal engineers who are expected to put a *dollar figure* next to an architecture, defend it in a review, and not get a surprise 7-figure cloud bill six months later.
>
> **The shift in altitude:** junior engineers optimize latency and correctness. Staff+ engineers also own **unit economics** — "what does one request cost us, and does that cost shrink or explode as we grow?" An architecture you can't cost is an architecture you can't defend.

---

## 1. Back-of-envelope capacity modeling

Before any cost work, you need a defensible **load model**. The goal isn't precision — it's the right order of magnitude, fast, on a whiteboard.

### The standard recipe

1. **Start from the business metric.** DAU, orders/day, events/day — whatever finance tracks.
2. **Convert to average rate.** `avg_rps = daily_requests / 86,400`.
3. **Apply a peak factor.** Real traffic is peaky. `peak_rps = avg_rps × peak_factor` (typically **3–10×** for consumer; spikier for events/sales).
4. **Size each resource** (compute, storage, bandwidth, DB) from peak.
5. **Add headroom** (target ~50–70% utilization, never 100%) and **project growth** (e.g. 2× /yr).

### Numbers worth memorizing (for whiteboard math)

| Quantity | Value |
|---|---|
| Seconds per day | ~86,400 (**~10⁵**) |
| Seconds per month | ~2.6M (**~2.6 × 10⁶**) |
| 1M req/day | ~**12 rps** average |
| 1B req/day | ~**11,600 rps** average |
| L1 cache ref | ~1 ns |
| Main memory ref | ~100 ns |
| SSD random read | ~100 µs |
| Network round trip within region | ~0.5 ms |
| Disk seek (HDD) | ~10 ms |
| RTT US↔EU | ~80–150 ms |

### Worked capacity example — "social feed service"

**Given:** 10M DAU, each user makes ~50 feed requests/day, peak factor 5×, each request needs ~10 ms of CPU on one core.

```
daily_requests = 10M × 50            = 500M req/day
avg_rps        = 500M / 86,400       ≈ 5,787 rps
peak_rps       = 5,787 × 5           ≈ 28,935 rps  (~29K rps)

CPU-seconds at peak = 28,935 rps × 0.010 s/req = 289 CPU-s per wall-second
                    → need ~289 busy cores at peak
At 60% target utilization: 289 / 0.6 ≈ 482 cores
On 16-vCPU instances:      482 / 16  ≈ 31 instances at peak

Storage (feed cache): 10M users × 200 items × 1 KB = 2 TB hot set
Bandwidth: 28,935 rps × 50 KB/resp ≈ 1.45 GB/s ≈ 11.6 Gb/s egress at peak
```

> **Always carry one extra digit of skepticism than the estimate deserves.** The point of BOTE is to catch the 10× error before the design review, not to be exact.

---

## 2. Unit economics — cost per request

This is the single most useful financial model an architect can build. **Cost-per-request (or per-order, per-user, per-GB-processed)** tells you whether your business *gets cheaper or more expensive as it grows*.

```
cost_per_request = total_monthly_infra_cost / monthly_requests
```

The healthy signal: as you scale, fixed costs amortize and `cost_per_request` **falls** (or holds flat with margin). The alarm: it **rises** — usually egress, a per-seat SaaS, or a chatty managed service.

### Worked unit-economics spreadsheet — "API platform, 500M req/month"

| Cost component | Driver | Qty | Unit price | Monthly \$ | \$/1M req |
|---|---|---|---|---:|---:|
| Compute (EC2/EKS, on-demand baseline + autoscale) | ~32 × 16-vCPU avg | 32 inst | ~\$500/inst-mo | \$16,000 | \$32.00 |
| Load balancer | ALB + LCU | — | — | \$800 | \$1.60 |
| Database (managed Postgres, primary + replica) | r6g.2xlarge ×2 + storage | — | — | \$3,200 | \$6.40 |
| Cache (managed Redis) | 3 × r6g.large | — | — | \$1,100 | \$2.20 |
| Object storage | 40 TB | 40,000 GB | \$0.023/GB | \$920 | \$1.84 |
| **Data egress** | 30 TB out to internet | 30,000 GB | \$0.085/GB | **\$2,550** | **\$5.10** |
| Logging / observability (SaaS) | ingest + retention | — | — | \$4,000 | \$8.00 |
| NAT gateway + misc | data processing | — | — | \$1,400 | \$2.80 |
| **Total** | | | | **\$29,970/mo** | **\$59.94/1M** |

**Interpretation:**
- Cost per request ≈ **\$30K / 500M = \$0.00006 (≈ \$0.06 per 1,000 requests)**.
- If you charge \$2 per 1,000 API calls, gross infra margin ≈ **97%** — healthy.
- **Biggest non-compute line is observability (\$8/1M)** — a classic SaaS-overage trap. **Egress (\$5.10/1M)** is the second; it grows with traffic and is invisible until the bill lands.
- **Lever ranking:** rightsize/RI the compute (biggest absolute), cap log ingestion (fastest ROI), put a CDN in front to cut egress.

> **Build this table for every major service.** When someone asks "can we cut the cloud bill 30%?", you answer in minutes, not weeks.

---

## 3. Cloud cost drivers — where the money actually goes

| Driver | Typical share | The trap |
|---|---|---|
| **Compute** | 40–60% | Over-provisioned, 24/7 dev/staging, no autoscaling, on-demand instead of committed |
| **Storage** | 10–20% | Old snapshots, unattached volumes, hot tier for cold data, no lifecycle policy |
| **Data transfer / egress** | 10–30% (often the surprise) | Cross-AZ, cross-region, internet egress; **NAT gateway data-processing**; chatty microservices |
| **Managed-service premium** | 10–25% | Convenience tax: managed DB/queue/search cost 2–4× the raw compute for the operational savings |
| **Observability / SaaS tooling** | 5–15% | Usage-based logging/metrics/APM bills that scale with traffic, uncapped |

### Egress deserves its own callout

Egress is the cost driver engineers forget because **ingress is usually free and egress is metered**:
- **Internet egress:** ~\$0.05–\$0.09/GB (volume-tiered, region-dependent).
- **Cross-region:** ~\$0.02/GB.
- **Cross-AZ (in-region):** ~\$0.01/GB *each way* — a chatty service mesh across 3 AZs can quietly cost five figures/month.
- **NAT gateway:** ~\$0.045/GB *processed* on top of egress — a frequent silent line item.

> **Architectural fix > config fix:** keep traffic in-AZ where possible, put a CDN in front of egress-heavy assets (CDN egress is far cheaper and cache hits cost ~\$0), and use VPC/private endpoints to avoid NAT. A CDN turning a 70% hit-rate cuts origin egress ~70%.

---

## 4. Cost-optimization levers (ranked by typical ROI)

| Lever | Mechanism | Typical saving | Effort |
|---|---|---|---|
| **Kill waste** | Delete idle/unattached resources, shut off-hours dev/staging | 10–30% | Low |
| **Rightsizing** | Match instance size to actual util (most are <50% used) | 15–40% on compute | Low–Med |
| **Committed-use (RI / Savings Plans)** | 1–3 yr commit for steady baseline | **~30–60% off on-demand** | Low (financial) |
| **Spot / preemptible** | Interruptible capacity for fault-tolerant/batch work | **~60–90% off on-demand** | Med (needs resilience) |
| **Autoscaling** | Scale to load instead of provisioning for peak | 20–50% on variable load | Med |
| **Caching** | CDN + app cache to cut DB load & egress | Avoids DB scale-up; cuts egress | Med |
| **Tiered storage / lifecycle** | Hot→warm→cold→archive by age | 50–80% on cold data | Low |
| **Data lifecycle / retention** | Expire logs & old data instead of keeping forever | Large on log/storage bills | Low |

### Worked: committed-use + spot blend

Baseline 30 instances 24/7 on-demand @ \$500/mo = **\$15,000/mo**.

```
Strategy: cover the steady 20-instance baseline with a 3-yr Savings Plan (~45% off),
          run the variable 0–10 instances on Spot (~70% off), keep 2 on-demand for safety.

20 baseline × \$500 × 0.55       = \$5,500
avg 6 variable × \$500 × 0.30    = \$900
2 on-demand × \$500              = \$1,000
                          Total  ≈ \$7,400/mo  → ~51% reduction
```

> **Sequence matters:** rightsize and kill waste *first*, then commit. Buying a 3-yr Savings Plan on over-provisioned instances locks in the waste for 3 years.

---

## 5. TCO modeling

TCO = **all** costs to own a capability over a horizon (usually 3 years): infra + people + tooling + migration + opportunity. Don't compare list prices; compare TCO.

### 5.1 Cloud vs on-prem (3-yr)

| Cost element | Cloud (3 yr) | On-prem (3 yr) |
|---|---:|---:|
| Servers / hardware | — | \$600,000 (capex, ~50% depreciation tail) |
| Cloud compute + storage + egress | \$1,080,000 (\$30K/mo) | — |
| Data-center / colo / power / cooling | — | \$216,000 |
| Network / bandwidth | (in cloud cost) | \$90,000 |
| Ops/infra staff | \$300,000 (0.5 FTE, abstracted infra) | \$900,000 (1.5 FTE racking, patching, DR) |
| Software/licenses | \$60,000 | \$180,000 |
| **3-yr TCO** | **\$1,440,000** | **\$1,986,000** |

> **The crossover lesson:** cloud usually wins on *variable, spiky, or uncertain* workloads (you pay for what you use, no capex, elastic). On-prem can win for **large, steady, predictable** workloads at scale — the per-unit cost of owned hardware undercuts cloud once utilization is high and constant. This is why some hyperscale consumers (e.g. companies running huge stable fleets) repatriate. **Decide per workload, not per company.**

### 5.2 Build vs buy over 3 years (ties to file 07)

| | Build in-house | Buy SaaS |
|---|---:|---:|
| Yr-0 build (4 eng × 6 mo @ \$250K/yr) | \$500,000 | — |
| Integration | — | \$40,000 |
| Annual maintenance/ops (0.75 FTE) | \$187,500/yr | — |
| SaaS subscription | — | \$120,000/yr (growing 25%/yr) |
| **3-yr cumulative** | \$500K + \$562.5K = **\$1,062,500** | \$40K + (\$120K+\$150K+\$187.5K) = **\$497,500** |

Buy wins by ~2× here — *unless* this is core (file 07, axis 1), in which case the strategic value, not the TCO, decides.

---

## 6. FinOps practices

FinOps = bringing financial accountability to the variable spend of cloud, as a continuous **Inform → Optimize → Operate** loop with engineering, finance, and product in the same room.

| Practice | What it is | Why it matters |
|---|---|---|
| **Tagging / cost allocation** | Mandatory tags: `team`, `service`, `env`, `cost-center` | Without it, you can't attribute spend — the root of all FinOps failure |
| **Showback** | Report each team's cloud spend (visibility, no billing) | Creates awareness; cheap to start |
| **Chargeback** | Actually bill teams' budgets for their spend | Creates real incentive; politically heavier |
| **Budgets & alerts** | Per-team/per-service budgets with threshold alerts (e.g. 80%, 100%, forecast-to-exceed) | Catches runaway spend in hours, not at month-end |
| **Anomaly detection** | Alert on sudden spend deltas | Catches the runaway test job / leaked loop |
| **Unit-cost KPIs** | Track \$/request, \$/customer, \$/order over time | The metric that tells you if scaling is healthy |

> **The tagging discipline is the foundation.** Enforce it in IaC (deny untagged resources via policy). Untagged spend in a mature org should be <5%.

---

## 7. The cost ↔ reliability ↔ performance triangle

```
            Performance
               /\
              /  \
             /    \
            /      \
   Reliability ---- Cost
```

You optimize two at the expense of the third. **Pick explicitly — don't let it happen by accident.**

| You want… | You pay with… | Example |
|---|---|---|
| High reliability (multi-region, replicas, redundancy) | **Cost** (2–3× infra) and sometimes latency | Active-active across regions |
| High performance (low latency everywhere, big caches, premium tiers) | **Cost** | Edge compute, provisioned IOPS, over-provisioning |
| Low cost | **Reliability and/or performance** | Single-AZ, spot, smaller fleet |

> The **SLO is the budget knob.** Three nines vs five nines can be a 3–5× cost difference. The staff move: make reliability/performance a *deliberate, costed* choice tied to the SLO and the revenue at risk — not a default of "make it as reliable and fast as possible."

---

## 8. Architecture cost-review checklist

Run this in every design review (it's as important as the security review):

**Load & capacity**
- [ ] Stated load model: avg + peak rps, peak factor, 1–3 yr growth assumption
- [ ] Target utilization & headroom defined (not provisioned for 100%)

**Unit economics**
- [ ] Cost-per-request (or per-order/user) estimated
- [ ] Does unit cost *fall* or *rise* with scale? If rise — why, and is it acceptable?

**Cost drivers**
- [ ] Compute: autoscaling? committed-use plan for baseline? spot for batch?
- [ ] Storage: lifecycle/tiering policy? retention defined? orphaned volumes/snapshots?
- [ ] **Egress: cross-AZ/region/internet quantified? CDN in front? NAT gateway cost checked?**
- [ ] Managed-service premium justified vs self-host?
- [ ] Observability/SaaS usage-based bills capped/sampled?

**Reliability vs cost**
- [ ] SLO stated and the redundancy cost is *deliberate* (single vs multi-AZ vs multi-region)

**FinOps**
- [ ] All resources tagged (team/service/env/cost-center)
- [ ] Budget + alert configured for this service
- [ ] Non-prod environments scheduled to shut down off-hours

---

## Key Takeaways

1. **Start from the business metric** → avg rps → peak (×3–10) → resources → headroom → growth. BOTE catches 10× errors before the review.
2. **Own the unit economics.** Build a cost-per-request table for every major service; the healthy signal is unit cost *falling* with scale.
3. **Egress is the silent killer.** Cross-AZ, internet egress, and NAT-gateway processing are invisible until the bill lands — fix them architecturally (in-AZ traffic, CDN, private endpoints).
4. **Optimization sequence:** kill waste → rightsize → commit (RI/SP, ~30–60% off) → spot (~60–90% off) → autoscale → cache → tier storage. Never commit on over-provisioned resources.
5. **Compare TCO, not list price.** 3-yr, fully loaded (infra + people + opportunity). Cloud wins on variable/uncertain; on-prem can win on large/steady/predictable.
6. **FinOps is a loop, and tagging is its foundation.** Showback → chargeback; budgets + anomaly alerts; track \$/unit as a first-class KPI.
7. **Cost ↔ reliability ↔ performance is a triangle.** The SLO is the budget knob — make the trade-off explicit and costed, never accidental.
