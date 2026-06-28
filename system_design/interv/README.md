# System Design Interview — Solutions & Framework

A curated set of worked system-design interview problems plus a reusable framework
for driving the conversation. Each problem follows the **same structure** so you
can practice the *process*, not just memorize answers.

> The architecture is never "the point." The point is structured thinking and
> trade-off reasoning. Use these as practice scaffolding, not gospel.

## How to use this folder
1. **Read [`00_interview_framework.md`](00_interview_framework.md) first.** Internalize
   the 8 steps, the estimation reference, and the building-blocks cheat sheet.
2. **Pick a problem at your level** (see grouping below). Cover the page, then try
   to drive the whole flow yourself out loud / on a whiteboard.
3. **Compare** your structure to the write-up — focus on *what you missed*, not on
   matching word-for-word.
4. **Do the deep dives by hand.** The code is for the *key components* only; type
   it out to make sure you understand the mechanics (consistent hashing, token
   bucket, base62, quorum).
5. **Time-box** yourself to ~45 min end-to-end. Re-do problems where you ran over.

## Every problem uses this structure
**Problem & Clarifications** · **Functional Requirements** · **Non-Functional
Requirements** · **Capacity Estimation** · **API Design** · **Data Model / Schema**
· **High-Level Design** (Mermaid) · **Deep Dives** · **Bottlenecks & Trade-offs**
· **Code** (working Python/SQL for the key parts) · **Summary**

---

## Index

### Framework
| Doc | What it covers |
|-----|----------------|
| [00_interview_framework](00_interview_framework.md) | The 8-step method, how to drive the conversation, common mistakes, building-blocks cheat sheet, and an estimation reference (QPS, storage, latency, nines). |

### Easier / foundational
| # | Problem | One-liner |
|---|---------|-----------|
| [01](01_url_shortener.md) | URL Shortener (TinyURL) | Map long URLs to short keys and redirect; base62 vs hash vs KGS, 301/302, read scaling. |
| [02](02_rate_limiter.md) | Rate Limiter | Throttle requests fairly across a distributed fleet; token bucket, sliding window, Redis counters. |
| [04](04_unique_id_generator.md) | Unique ID Generator | Generate sortable, unique 64-bit IDs at scale; Snowflake-style timestamp+machine+sequence. |
| [13](13_typeahead.md) | Typeahead / Autocomplete | Rank and serve search suggestions in <100 ms; tries, prefix caching, top-k. |
| [21](21_leaderboard.md) | Leaderboard | Real-time ranked scores for millions of players; Redis sorted sets, sharding by rank. |
| [23](23_parking_lot_ood.md) | Parking Lot (OOD) | Object-oriented design of a parking lot; classes, interfaces, pricing strategy. |

### Core distributed-systems
| # | Problem | One-liner |
|---|---------|-----------|
| [03](03_key_value_store.md) | Key-Value Store (Dynamo) | Highly available distributed KV store; consistent hashing, quorum (R/W/N), vector clocks, hinted handoff. |
| [15](15_distributed_cache.md) | Distributed Cache | Shared in-memory cache layer; consistent hashing, eviction, replication, stampede protection. |
| [19](19_job_scheduler.md) | Distributed Job Scheduler | Run cron/one-off jobs reliably at scale; leader election, at-least-once, dedup. |
| [18](18_proximity_service.md) | Proximity Service | "Find businesses near me"; geohashing/quadtrees, spatial indexing. |
| [22](22_google_maps.md) | Google Maps | Routing + ETA over a road graph; tiling, contraction hierarchies, traffic. |

### Large-scale consumer apps
| # | Problem | One-liner |
|---|---------|-----------|
| [05](05_web_crawler.md) | Web Crawler | Politely crawl billions of pages; frontier, dedup, politeness, freshness. |
| [06](06_notification_system.md) | Notification System | Fan out push/SMS/email reliably; queues, provider abstraction, rate limits. |
| [07](07_news_feed.md) | News Feed | Build and serve a personalized feed; fan-out-on-write vs read, ranking. |
| [08](08_chat_system.md) | Chat System | 1:1 and group messaging with presence; WebSocket, delivery/ordering, offline. |
| [09](09_twitter.md) | Twitter | Tweets, timelines, follows at scale; hybrid fan-out, the celebrity problem. |
| [10](10_instagram.md) | Instagram | Photo sharing + feed; media pipeline, CDN, feed generation. |
| [11](11_youtube_netflix.md) | YouTube / Netflix | Video upload, transcode, and stream; ABR, CDN, recommendations. |
| [12](12_google_drive.md) | Google Drive | File sync & sharing; chunking, dedup, metadata, conflict resolution. |
| [14](14_uber.md) | Uber | Match riders to nearby drivers; location ingest, dispatch, geosharding. |
| [20](20_ad_click_aggregator.md) | Ad Click Aggregator | Aggregate billions of click events; streaming, exactly-once, idempotency. |

### Transactional / specialized
| # | Problem | One-liner |
|---|---------|-----------|
| [16](16_payment_system.md) | Payment System | Charge/refund money correctly; ledgers, idempotency, reconciliation, exactly-once. |
| [17](17_ticketmaster.md) | Ticketmaster | Sell limited seats under spikes; reservations, locking, no double-booking. |

---

## Difficulty legend & suggested order
- **Start here:** 00 (framework) → 01 → 02 → 04.
- **Then distributed fundamentals:** 03 → 15 → 19.
- **Then consumer scale:** 07 → 09 → 08 → 11.
- **Then specialized/transactional:** 16 → 17 → 20.
- **OOD round:** 23 (different style — classes & interfaces, not boxes & arrows).

> Currently fully authored in this folder: **00 (framework), 01, 02, 03.**
> The remaining entries are listed as the planned index; author them using the
> same 11-section structure.

## Tips
- Practice **out loud** and **on a timer** — the interview tests communication.
- Always **state the consistency posture** and **shard key** explicitly.
- Memorize the **estimation reference** in `00` cold; numbers buy credibility.
- For each problem, know its **one signature deep dive** (e.g., URL shortener =
  ID generation; chat = delivery/ordering; KV store = quorum + conflict resolution).
