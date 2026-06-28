# 12 — Time Synchronization (NTP & PTP)

> **Audience:** staff/principal. Every host has a clock; no two agree. This doc is about why "what time is it?" is one of the hardest unsolved problems in distributed systems — how a few hundred milliseconds of clock skew silently corrupts your logs, traces, certificates, and consensus invariants — and the protocols (**NTP**, **PTP/IEEE 1588**) and hardware (GNSS, atomic, NIC PHCs) that bound the disagreement, from millisecond WAN sync down to sub-nanosecond. Time is not a free local read; it's a *distributed agreement problem with no acknowledgements*.
>
> **Primary sources:** RFC 5905 (NTPv4); RFC 8633 (NTP BCP); IEEE 1588-2008/2019 (PTP); RFC 7384 (PTP security/threats); ITU-T G.8275.1/.2 (telecom PTP profiles); IEC 61850-9-3 (power profile); SMPTE ST 2059 (broadcast); Mills, *Computer Network Time Synchronization*; the chrony and linuxptp documentation; Google Spanner/TrueTime (OSDI 2012) & "Time, Clocks…" (Lamport 1978); AWS Time Sync Service & ClockBound; ESA/CERN White Rabbit.

---

## 1. Why time is a silent killer

A clock read looks like the cheapest operation in your program. It is actually a *value asserted by a local oscillator that drifts*, disciplined by a *network protocol with no delivery guarantee*. When two machines disagree on "now," the failure is rarely a crash — it's silent corruption that surfaces hours later as an inexplicable bug. The blast radius is enormous because **almost every distributed primitive secretly assumes synchronized clocks**:

| Subsystem | What it assumes about clocks | Failure when clocks skew |
|---|---|---|
| **Log correlation** | timestamps across hosts are comparable | events interleave wrong; cause appears *after* effect; root-cause analysis is impossible |
| **Distributed tracing** | spans on different hosts share a timeline | child span "starts before" parent; negative durations; Gantt charts lie |
| **TLS / X.509** | `notBefore ≤ now ≤ notAfter` | a *valid* cert is rejected (`certificate not yet valid` / `expired`) on a host whose clock is wrong (§ Symptom/Cause/Fix) |
| **Kerberos / OAuth** | tickets/assertions have tight validity windows (default ±5 min) | auth fails cluster-wide the instant skew exceeds the window |
| **TOTP / MFA** | 30s time-step shared with the server | one-time codes rejected |
| **Ordering / causality** | a later wall-clock timestamp ⇒ a later event | last-writer-wins **silently drops the newer write** (the classic Cassandra/Dynamo data-loss bug) |
| **Leases & timeouts** | a lease granted for *T* seconds expires after *T* | two nodes both believe they hold the lock (split-brain) if their clocks disagree about elapsed time |
| **Financial / regulatory** | timestamps are accurate to a regulated bound | **MiFID II RTS 25** mandates ≤100 µs (HFT) / ≤1 ms divergence from UTC, traceable to UTC; a non-compliant clock is a *reportable* failure |
| **Consensus / databases** | bounded clock error for external consistency | Spanner-style commit-wait math (§9) is *unsound* if uncertainty isn't truly bounded |

The canonical one-liner: **"two servers disagree on now."** A load balancer issues a JWT with `exp = now + 60s`; the validating service, 90 seconds ahead, sees an already-expired token and 401s every request. Nothing logged a clock error — you logged a flood of auth failures. Time sync is infrastructure in the same tier as DNS: invisible until it isn't, and when it breaks, *everything* breaks at once.

---

## 2. Clocks and the actual problem

### 2.1 Wall-clock vs monotonic — and the NTP-step bug

A host exposes (at least) **two** clocks, and conflating them is the single most common time bug in application code:

- **Wall-clock / real-time** (`CLOCK_REALTIME`, `gettimeofday`, `System.currentTimeMillis`): seconds since the Unix epoch, meant to track UTC. It is **steered by NTP/PTP**, so it can **jump backward or forward** at any instant (a *step*), and can repeat or skip values. Use it only for *"what wall-time is it?"* — timestamps, certs, schedules.
- **Monotonic** (`CLOCK_MONOTONIC`, `clock_gettime`, `System.nanoTime`, Go `time.Since`): a counter that only ever increases, with no defined epoch and no relation to UTC. It is **never stepped** (though its *rate* may be disciplined). Use it for *"how much time elapsed?"* — timeouts, retries, latency measurement, rate limiting, lease durations.

The classic bug: a naive timer computes a deadline as `end = wallclock_now() + 30s` and loops until `wallclock_now() >= end`. If NTP steps the wall clock **forward** by an hour mid-wait, the timer fires *immediately* (the lease "instantly" expires → premature failover). If NTP steps it **backward**, the timer **hangs for an hour**. The fix is unconditional: **measure elapsed time with the monotonic clock**, only ever read the wall clock when you genuinely need calendar time.

```text
WRONG (wall clock for elapsed):           RIGHT (monotonic for elapsed):
  deadline = REALTIME_now() + 30s            start = MONOTONIC_now()
  while REALTIME_now() < deadline: ...        while MONOTONIC_now() - start < 30s: ...
  ^ an NTP step makes this fire early/late    ^ immune to wall-clock steps
```

> Well-behaved sync **slews** (gradually adjusts the rate) instead of stepping, precisely to avoid backward jumps. But a step *will* happen on boot, after a large correction, or on a manual `date` set — so never assume the wall clock is monotonic.

### 2.2 Crystal drift, offset, and frequency

Every clock is a counter driven by a **quartz oscillator** whose frequency error is specified in **parts per million (ppm)**. A commodity server crystal is ~±20–50 ppm; a temperature-compensated (TCXO) or oven-controlled (OCXO) oscillator is far better. The intuition for ppm:

> 1 ppm ≈ **0.864 seconds of drift per day**. A cheap ±50 ppm crystal drifts up to **~4.3 seconds/day** if left undisciplined.

The disciplining problem decomposes into two quantities — and conflating them is why naive "just set the clock periodically" approaches oscillate:

- **Offset** (phase / skew): the instantaneous difference between this clock and the reference — *"how wrong am I right now?"* Measured in seconds.
- **Frequency** (rate / drift): how fast this clock's *error grows* — *"how wrong do I get per second?"* Measured in ppm or s/s.

A good sync daemon estimates **both**: it corrects the current offset *and* learns the crystal's frequency error so it can keep the clock accurate *between* polls (and during network outages — **holdover**, §8). Correcting offset alone produces a sawtooth that re-drifts immediately; learning frequency is what lets a host stay sub-millisecond polling a server only every ~1000 s.

---

## 3. NTP — Network Time Protocol

NTP (RFC 5905, NTPv4) is the Internet's time-distribution protocol: UDP/123, request/response, designed to discipline a host to UTC over lossy, variable-latency WAN paths to **single-digit milliseconds** (and sub-millisecond on a quiet LAN).

### 3.1 The stratum hierarchy

NTP scales the same way DNS does — **hierarchy + delegation** (see [05_dns.md](05_dns.md)). *Stratum* is the distance, in hops, from a reference clock:

```text
        Stratum 0   reference clocks (NOT on the network)
                    GPS/GNSS receiver · atomic (Cs/Rb) · radio (DCF77/WWVB)
                          │ (PPS / serial — local hardware link)
        Stratum 1   "primary" servers directly attached to a stratum-0 source
                    ┌─────────────┼─────────────┐
        Stratum 2   servers that sync FROM stratum-1 over the network
                ┌───┴───┐     ┌───┴───┐
        Stratum 3   sync from stratum-2 …            (down to stratum 15;
                                                       16 = "unsynchronized")
```

Stratum is not accuracy — it's topological depth. A well-connected stratum-3 host on a LAN can be more accurate than a distant stratum-2 across the WAN. Each level multiplies the population a single reference clock can serve, exactly like DNS delegation bounds what any one server must know.

### 3.2 The four timestamps: offset and round-trip delay

NTP's core is a four-timestamp exchange that lets the client compute its offset **even though it doesn't know the one-way path delays** — the key trick is *assuming the path is symmetric*:

```text
   Client                              Server
     │  T1 = client send time           │
     │ ───────── request ──────────────►│  T2 = server receive time
     │                                   │  T3 = server transmit time
     │ ◄──────── response ──────────────│
     │  T4 = client receive time         │

   round-trip delay   δ = (T4 − T1) − (T3 − T2)        # total minus server processing
   clock offset       θ = ((T2 − T1) + (T3 − T4)) / 2  # avg of forward & reverse skew
```

`δ` is the network round-trip with the server's own processing removed; `θ` is how far the client is ahead of/behind the server. The estimate is exact **iff the forward and reverse delays are equal**; the error is bounded by half the *asymmetry*. This is why NTP accuracy is limited by **path asymmetry** (a different route or queue depth each way), not by raw latency — and why software NTP plateaus around the millisecond: kernel/network jitter perturbs T1–T4 (PTP attacks this with hardware timestamps, §4).

### 3.3 Filter, select, cluster, combine, discipline

A serious implementation never trusts one sample or one server. The NTP algorithm pipeline:

1. **Clock filter** — keep several recent (θ, δ) samples per server; prefer the one with the *lowest delay* (lowest-delay sample is least corrupted by queueing). Jitter is noise; the shortest path is signal.
2. **Selection / intersection** (Marzullo's algorithm) — across servers, find the largest set of *agreeing* time intervals and discard **falsetickers** (servers that disagree). This is what makes NTP robust to a single lying or broken server — and why you configure **≥4 sources** (so one bad source can be outvoted; with 3 you can detect but not always pick).
3. **Cluster / combine** — weight and merge the survivors into a single offset estimate.
4. **Discipline** — feed the offset into a feedback loop (a PLL/FLL) that adjusts both **phase** (offset) and **frequency** (drift). Small errors are **slewed** (rate-nudged, e.g. ≤500 ppm so the clock stays monotonic-ish); large errors may **step** once.

### 3.4 chrony vs ntpd — and why chrony is the modern default

`ntpd` (the reference implementation) is being displaced by **chrony** as the default on modern Linux (RHEL/Ubuntu). Why chrony wins in practice:

| | **chrony** | **ntpd** (classic) |
|---|---|---|
| Converges after boot | **fast** (large initial corrections, aggressive slew) | slow (conservative) |
| Intermittent / laptop / VM clocks | **excellent** — handles network drop-outs, suspend/resume, asymmetric paths | poor |
| Behavior when offline | tracks frequency, good **holdover** | drifts |
| Steps vs slews | slews aggressively; configurable `makestep` | mostly slews; can be slow |
| Resource use | lighter | heavier |
| Server mode | yes (`allow`) | yes (full-featured, more legacy) |

ntpd still appears where you need its broader feature surface (autokey, broadcast/multicast modes, some appliances), but for a new fleet **chrony is the answer**. (`systemd-timesyncd` is an SNTP *client* — fine for a single desktop, not a server: it polls one source with no filtering/selection, so don't use it where accuracy or robustness matters.)

```bash
# /etc/chrony/chrony.conf
pool 2.pool.ntp.org iburst        # 'pool' = resolve to MANY servers, auto-manage the set
server time.cloudflare.com iburst # 'server' = one specific source; iburst = fast initial sync
makestep 1.0 3                    # allow a STEP only for the first 3 updates if |offset|>1s
rtcsync                           # keep the hardware RTC disciplined too
driftfile /var/lib/chrony/drift   # persist learned frequency across reboots (instant holdover)

# Observe it:
chronyc tracking     # the headline: Stratum, System time offset, RMS offset,
                     # Frequency (ppm drift it has learned), Root delay/dispersion,
                     # Leap status, and Last offset
chronyc sources -v   # per-source: '^*' = current sync peer, '^+' = combined candidate,
                     # 'x' = falseticker (rejected), plus reach bitmask, last sample, offset
chronyc sourcestats  # per-source frequency/offset estimates and their error bounds
chronyc makestep     # force an immediate step (recovery after a big jump)
```

`server` pins one host; `pool` resolves one name to *many* addresses (the NTP Pool Project, `*.pool.ntp.org`, is a volunteer DNS-rotated pool) and chrony manages adding/dropping members to keep ≥ the configured minimum sources alive. Always run **≥4 independent sources** so selection can outvote a falseticker.

---

## 4. PTP — Precision Time Protocol (IEEE 1588)

When milliseconds aren't enough — **sub-microsecond, often sub-100 ns** — you move from NTP to **PTP**. Drivers:

- **Fintech / HFT timestamping** — MiFID II RTS 25 demands ≤100 µs accuracy to UTC for HFT; matching/recording timestamps need PTP-grade precision.
- **Telco / 5G** — radio access (TDD, carrier aggregation, MIMO) needs phase alignment of ±1.5 µs or tighter across base stations; **frequency** sync for the radio itself.
- **Broadcast** — SMPTE ST 2059 replaces genlock/black-burst with PTP so video/audio frames align.
- **Distributed databases / HPC** — tight clock bounds shrink Spanner-style commit-wait (§9) and enable ordering with less coordination.

PTP achieves 100–1000× better than software NTP for one reason: **hardware timestamping**.

### 4.1 The killer feature: NIC hardware timestamping (PHC)

Software NTP timestamps a packet in *userspace/kernel*, so everything between the application and the wire — scheduler delay, interrupt latency, the network stack — adds **jitter** to T1–T4 (§3.2) and caps accuracy near the millisecond. PTP-capable NICs contain a **PTP Hardware Clock (PHC)** that timestamps the packet **at the PHY, on the wire, in hardware**, removing essentially all host-stack jitter. The on-host job then splits in two:

- **`ptp4l`** (linuxptp) speaks PTP on the wire and disciplines the **NIC's PHC** (`/dev/ptp0`).
- **`phc2sys`** copies time **between the PHC and the system clock** (`CLOCK_REALTIME`) — because your applications read the system clock, not the NIC. (When the NIC isn't the time source, you run phc2sys the other way: system → PHC.)

This PHC↔system split is the operational heart of a Linux PTP deployment; getting it backwards is a top cause of "PTP is configured but the system clock is still off."

### 4.2 The message exchange

PTP's offset/delay math is NTP's idea (§3.2) executed with hardware timestamps. The default **delay-request–response** mechanism:

```text
   Master                                   Slave (ordinary clock)
     │  Sync                                   │
     │ ─────────────────────────────────────► │  t2 (HW timestamp on arrival)
     │  t1 (HW timestamp of Sync departure)    │
     │  Follow_Up { carries t1 }               │   ← "two-step" clocks send t1 here
     │ ─────────────────────────────────────► │
     │                                         │
     │            Delay_Req                     │  t3 (HW timestamp on departure)
     │ ◄───────────────────────────────────── │
     │  t4 (HW timestamp on arrival)           │
     │  Delay_Resp { carries t4 }              │
     │ ─────────────────────────────────────► │
     │                                         │
   offset = ((t2 − t1) − (t4 − t3)) / 2     # slave error vs master (symmetry assumed)
   delay  = ((t2 − t1) + (t4 − t3)) / 2     # mean path delay
```

- **One-step vs two-step**: a *one-step* clock writes its departure timestamp *into the Sync message itself* in hardware; a *two-step* clock sends the precise t1 afterward in a **Follow_Up** (simpler hardware, one extra message).
- The **Best Master Clock Algorithm (BMCA)** elects the **grandmaster** automatically from advertised clock quality (priority, class, accuracy, variance) — no static config of "who is the master," and automatic failover if it disappears.

### 4.3 Clock types — and why the *fabric* must be PTP-aware

PTP's precision survives multiple switch hops **only if the switches participate**. The whole point of boundary/transparent clocks is to cancel the residence time and queuing jitter that would otherwise destroy sub-µs accuracy:

| Clock type | Role | Effect on accuracy |
|---|---|---|
| **Ordinary clock (OC)** | endpoint — a single PTP port; is master *or* slave | the leaf node being synced |
| **Grandmaster (GM)** | the root OC, usually GNSS-disciplined (§5); the ultimate time source | top of the PTP tree |
| **Boundary clock (BC)** | a switch that **terminates** PTP on each port: slave to its upstream, master to its downstream | breaks the timing chain into short, well-controlled hops; prevents jitter accumulation across the fabric |
| **Transparent clock (TC)** | a switch that **measures its own residence time** for each PTP packet and writes it into the packet's *correctionField* | the slave subtracts switch dwell time, so queuing delay inside the switch is cancelled |

A plain (PTP-unaware) switch adds variable queuing delay to every Sync/Delay message and silently caps you at "PTP only ms-accurate" — the symptom in §10. **Sub-microsecond PTP requires a PTP-aware network**, end to end.

```bash
# Slave on eth0 using hardware timestamping, telecom-ish defaults:
ptp4l -i eth0 -m -H                 # -H = HARDWARE timestamping (the whole point);
                                    # -S would be software (don't, unless debugging)
ptp4l -i eth0 -m -H -f gPTP.cfg     # -f to load a profile (e.g. 802.1AS / G.8275.1)

# Steer the SYSTEM clock from the NIC PHC that ptp4l disciplines:
phc2sys -s eth0 -w -m               # -s = source PHC of eth0; -w = wait for ptp4l to sync

# Check the NIC actually supports HW timestamping before trusting any of it:
ethtool -T eth0                     # look for "hardware-transmit/receive" + PTP_HW filters
```

---

## 5. Reference clocks — GNSS, atomic, and the grandmaster

Stratum-0 / grandmaster sources are physical references, not network peers:

- **GNSS-disciplined oscillators (GPSDO)** — a GPS/GNSS receiver recovers UTC from the satellite constellation (each satellite carries atomic clocks) and emits a **1 PPS** (one-pulse-per-second) edge plus a time-of-day message. A local **OCXO/rubidium** oscillator is *disciplined* to that PPS: GNSS provides long-term accuracy (to ~tens of ns), the local oscillator provides short-term stability and **holdover**. This GPSDO is the typical stratum-1 / PTP grandmaster.
- **Atomic clocks** (cesium/rubidium) — primary frequency standards; used where GNSS is unavailable or as the holdover oscillator. Caesium defines the SI second.
- **Radio** (DCF77, WWVB, MSF) — legacy long-wave UTC broadcast; lower precision.

**Holdover** is the property that matters operationally: when the reference (GNSS) is lost — antenna fault, jamming, spoofing, an indoor box — the disciplined oscillator must *coast* on its last-learned frequency and stay within spec for hours/days. Holdover quality is set by the oscillator (cheap TCXO drifts fast; rubidium/OCXO holds for hours to days). A **grandmaster** appliance bundles GNSS + a good oscillator + PTP/NTP server, and is the single device you point a whole datacenter's timing at — so it is engineered for redundancy (dual GNSS, dual power) because it is a timing **SPOF**.

> GNSS is also an *attack surface*: spoofing the GPS signal can walk a grandmaster's clock without tripping a "no signal" alarm. Defenses: multi-constellation receivers, holdover with a disciplined atomic oscillator, sanity-bounding against an independent source, and (RFC 7384) PTP/NTP authentication.

---

## 6. TrueTime and cloud time services

The deepest idea in distributed time: **you cannot make clocks perfectly agree, so instead make the *uncertainty explicit and bounded*, then wait it out.**

### 6.1 Spanner / TrueTime

Google **Spanner** achieves *external consistency* (linearizable, globally ordered commits) using **TrueTime**, an API that returns not a timestamp but an **interval** `[earliest, latest]` guaranteed to contain the true UTC time. TrueTime is backed by **GPS + atomic clocks** in every datacenter, with masters cross-checking each other; the interval width `ε` (epsilon) is typically a few milliseconds and is *the modeled, enforced bound on clock error* — not a hope.

The trick is **commit-wait**: to commit a transaction at timestamp `s = TT.now().latest`, Spanner **deliberately sleeps until `TT.now().earliest > s`** — i.e., until it is *certain* the chosen timestamp is in the past everywhere. The cost of clock uncertainty is paid as **explicit latency of ~2ε per commit**. Tighter clocks (smaller ε) ⇒ lower commit latency. This is why Google invests in GPS+atomic in every DC: *clock uncertainty is literally transaction latency.*

```text
   commit timestamp s = TT.now().latest
   ─────────────────────────────────────────────────────►  true UTC
        [   ε   ][   ε   ]
        ^earliest        ^latest = s
   commit-wait:  sleep until TT.now().earliest > s   (≈ 2ε)
   then release locks → no other transaction can have a smaller timestamp.
```

The contrast with NTP: NTP *hides* uncertainty (it just sets the clock); TrueTime *exposes* it and forces callers to account for it. **Bound the uncertainty, then wait it out** is the whole philosophy. (See [../../system_design/](../../system_design/README.md) for external consistency, linearizability, and hybrid logical clocks — HLCs — which approximate this without GPS hardware.)

### 6.2 AWS Time Sync & ClockBound

**AWS Time Sync Service** provides a free, low-jitter NTP/PTP source at the link-local address **`169.254.169.123`** inside every EC2 instance, fed from satellite-connected atomic clocks on the Nitro hardware — microsecond-grade without your running a grandmaster. AWS also exposes **PTP hardware clocks** on supported instances and a **ClockBound** daemon/library: a TrueTime-style API returning `(earliest, latest)` so applications can reason about and *wait out* their own clock error. This brings the Spanner pattern to commodity cloud workloads.

---

## 7. Leap seconds and smearing

UTC is occasionally adjusted by a **leap second** to stay aligned with Earth's (irregular) rotation: at the chosen instant, the clock shows **23:59:60** (a 61-second minute) or, in principle, skips a second. This is a nightmare for software:

- A repeated or non-existent second breaks code that assumes time is **monotonic and continuous** — historically causing kernel/hrtimer lockups (the 2012 leap second took down Reddit, Mozilla, and others) and crashed `kvm`/`hadoop` clusters.
- NTP signals an impending leap with **leap-indicator bits**; the kernel then inserts/deletes the second, producing exactly the discontinuity application code mishandles.

**Leap smearing** is the dominant fix: instead of one abrupt second, **spread the one-second correction smoothly over a window** (Google and AWS smear over ~24 hours; AWS/Google use a 24h linear-ish smear). During the smear, every second is very slightly longer/shorter, the wall clock stays *continuous and monotonic*, and no application ever sees `:60`.

> **Interop caveat:** a smearing server and a non-smearing (standard UTC, step-at-leap) server **disagree by up to ~0.5 s during the smear window**. Never mix smeared and non-smeared sources in the same selection set — the smeared source looks like a falseticker, or worse, a host averages them and is wrong for a day. Pick one regime fleet-wide (and the same smear *shape*). IERS has signaled intent to abolish leap seconds by ~2035, but you must handle them until then.

---

## 8. Advanced

### 8.1 PTP profiles

IEEE 1588 is a toolbox; a **profile** pins the options (message rates, transport, BMCA tweaks, mandatory clock types) for an industry so equipment interoperates:

| Profile | Domain | Notes |
|---|---|---|
| **G.8275.1** | telecom, full timing support | PTP over Ethernet (L2 multicast), **requires boundary clocks at every hop**; for 5G phase sync |
| **G.8275.2** | telecom, partial timing support | PTP over UDP/IP; tolerates non-PTP hops (worse accuracy) for partial deployments |
| **802.1AS (gPTP)** | AVB/TSN, automotive, pro-AV | a tightly constrained 1588 profile for Time-Sensitive Networking |
| **IEC 61850-9-3 / C37.238** | electrical power / substations | ±1 µs for synchrophasors (PMUs) and protection relays |
| **SMPTE ST 2059** | broadcast | aligns video/audio to UTC, replacing black-burst/genlock |
| **Default (Annex J)** | general | the vanilla profile `ptp4l` ships with |

The takeaway: "we run PTP" is underspecified — the *profile* and whether the **fabric is fully timing-aware** (BC at every hop vs partial) determine the accuracy class you actually get.

### 8.2 White Rabbit — sub-nanosecond

**White Rabbit** (born at CERN, now part of the High-Accuracy PTP profile in IEEE 1588-2019) reaches **sub-nanosecond** accuracy and picosecond precision by combining three things PTP alone doesn't: **Synchronous Ethernet (SyncE)** to recover the clock *frequency* from the physical layer, **precise phase measurement (DMTD)** of the recovered clock, and **continuous calibration of the fiber's exact, asymmetric propagation delay**. It is used in particle accelerators, large radio-telescope arrays, and increasingly financial exchanges that need provable, sub-ns ordering. It is the logical end of the line: kill jitter (HW timestamps), kill switch dwell (TC/BC), then kill the *path-asymmetry* assumption itself (measure the fiber).

### 8.3 Clock-uncertainty bounds in distributed databases

The Spanner insight (§6) generalizes: a database that *knows* its clock error bound `ε` can use time for ordering safely; one that doesn't, can't.

- **CockroachDB** runs on commodity NTP and therefore sets a *configured, pessimistic* `max-offset` (default 500 ms). It doesn't commit-wait; instead, a read that lands inside the uncertainty window **restarts at a higher timestamp** (uncertainty restarts). If real clock skew *exceeds* the configured max-offset, **consistency guarantees are violated** — so a node that detects it has drifted beyond `max-offset/2` **self-terminates** rather than risk corruption. Monitoring clock skew is thus a *correctness* requirement, not just hygiene.
- **YugabyteDB** similarly uses a max-clock-skew bound; **Hybrid Logical Clocks (HLCs)** combine a physical clock with a Lamport counter to preserve causality even when physical clocks are slightly off — the cheap approximation of TrueTime for those without GPS.

The principle: **the tighter and more *trustworthy* your clock bound, the less coordination/latency you pay.** Cloud PTP and ClockBound (§6.2) exist to shrink that bound for everyone.

### 8.4 Time sync as a first-class SLI

At staff/principal level, treat clock health like any other reliability signal — monitor it *before* it causes an auth/consensus outage:

- **Export and alert on `chronyc tracking` / PTP offset.** Key SLIs: **system-clock offset** (vs reference), **estimated error / root dispersion** (the *bound*, not just the point estimate), **frequency (ppm) drift** trend, **stratum / sync state** (alert on stratum 16 = unsynchronized, or PTP not in `SLAVE`/`LOCKED`), **reachability** of each source, and **leap status**.
- **Alert thresholds tied to your tightest consumer:** Kerberos (±5 min), TLS (minutes), but for DBs/finance, alert in the **milliseconds/microseconds**. The alert must fire *long before* the bound breaches the consumer that will fail.
- **Diversity & holdover:** ≥4 NTP sources spanning independent paths/providers; for PTP, redundant grandmasters with GNSS holdover; monitor **GNSS lock / satellites in view** to catch jamming/spoofing.
- **Correlate with the kernel.** Clock-step events, `CLOCK_REALTIME` jumps, and slew rate are observable via the kernel — pair this with [../operating_system/08_linux_internals_observability.md](../operating_system/08_linux_internals_observability.md) (`adjtimex`/`ntp_adjtime`, `ntpq`/`chronyc`, tracepoints) to catch a step that just broke your timers (§2.1).

---

## 9. Symptom / Cause / Fix

| Symptom | Likely cause | Fix |
|---|---|---|
| **Logs across hosts don't line up** — event B appears before its cause A; traces show negative span durations | **Clock skew** between hosts; offsets of tens/hundreds of ms aren't corrected | Deploy chrony with ≥4 sources fleet-wide; alert on per-host offset (§8.4); for tracing, never *compare* wall-clock timestamps across hosts without accounting for skew — prefer causal/logical ordering |
| **"Certificate not yet valid" / "expired" though the cert is fine** | The **validating host's wall clock is wrong** (often far in the future/past after an RTC battery death or failed sync); `notBefore/notAfter` check fails locally | Fix the host clock (`chronyc tracking` → big offset; `chronyc makestep`); ensure NTP starts *before* TLS-using services; for fresh VMs, sync before issuing/validating certs |
| **Timer/lease fired early or hung after an NTP correction** | Code used the **wall clock to measure elapsed time**; an NTP step jumped it (§2.1) | Switch all duration/timeout/lease logic to the **monotonic clock** (`CLOCK_MONOTONIC`, `nanoTime`, `time.Since`); reserve the wall clock for calendar timestamps |
| **PTP deployed but only ~ms accurate** (expected sub-µs) | **Software timestamping** (`-S`), a NIC without a PHC, and/or **PTP-unaware switches** adding queuing jitter | `ethtool -T` to confirm HW timestamping; run `ptp4l -H`; ensure `phc2sys` steers the *system* clock the right direction; deploy **boundary/transparent clocks** end-to-end (§4.3) |
| **Auth (Kerberos/JWT/MFA) fails across the cluster, intermittently** | Skew exceeded the credential's validity window (±5 min Kerberos, tight `exp`) | Treat clock sync as a hard dependency of auth; alert well below the window; verify all KDC/app hosts share sync |
| **Two sources disagree by ~0.5s for a day**; one flagged falseticker | Mixing a **leap-smearing** source with a **non-smearing** one (§7) | Standardize the leap regime/shape fleet-wide; never mix smeared and standard-UTC sources in one selection set |

---

## 10. Key takeaways & trade-offs

1. **Time disagreement is silent corruption**, not a crash: it breaks log correlation, tracing, TLS/Kerberos validity, last-writer-wins ordering, leases, and MiFID II timestamps — "two servers disagree on *now*" is a real outage class.
2. Use the **monotonic clock for elapsed time** (never steps) and the **wall clock only for calendar time** (can step); conflating them is the #1 time bug — an NTP step on a wall-clock timer fires it early or hangs it.
3. Discipline corrects **both offset** (current error) *and* **frequency** (drift, ppm); ~1 ppm ≈ 0.86 s/day, so undisciplined crystals drift seconds per day, and learning frequency is what gives accuracy between polls and holdover.
4. **NTP** = software, WAN, **milliseconds**, robust, ~free: stratum hierarchy, a 4-timestamp exchange giving offset+round-trip-delay (assuming path symmetry), filter→select(Marzullo)→combine→discipline, **≥4 sources** to outvote a falseticker; **chrony** is the modern default over ntpd.
5. **PTP (IEEE 1588)** = hardware, LAN/DC, **sub-µs** via **hardware timestamping (NIC PHC)** plus a **PTP-aware fabric** (boundary/transparent clocks); `ptp4l` disciplines the PHC, `phc2sys` bridges PHC↔system clock. Pick the cheapest protocol that meets your *tightest* consumer's bound.
6. **GNSS-disciplined oscillators** are the stratum-0/grandmaster source; **holdover** (a good oscillator coasting when GNSS is lost) and GNSS-spoofing resistance are the operational concerns.
7. NTP *hides* clock error (just sets the clock); **TrueTime/ClockBound expose and bound it**, then *wait it out* (commit-wait ≈ 2ε) — the foundation of Spanner's external consistency. *Clock uncertainty is transaction latency*; tighter clocks = lower latency.
8. **Leap-smear** the leap second (continuous, monotonic), and **never mix smeared with non-smeared** sources; treat **clock sync as a first-class SLI** and a correctness dependency of auth, consensus, and DBs — monitor offset/dispersion/stratum *before* it causes the outage.

> **Related:** [04_transport_tcp_udp.md](04_transport_tcp_udp.md) (NTP/PTP ride UDP; path asymmetry/jitter is why software sync plateaus) · [05_dns.md](05_dns.md) (the same hierarchy + delegation + caching scaling pattern) · [../operating_system/08_linux_internals_observability.md](../operating_system/08_linux_internals_observability.md) (kernel clocks, `adjtimex`, clock-step observability) · [../../system_design/](../../system_design/README.md) (TrueTime, external consistency, HLCs, and bounding uncertainty in distributed databases).
