# Fintech & Capital-Markets Domain Knowledge for Data Engineers
## What a data engineer lives through at a bank like JPMorgan or Goldman Sachs

Financial services is one of the most distinctive domains a data engineer can work in: extreme data volumes, microsecond-to-millisecond latency in places, a regulatory weight that shapes *every* architecture decision, and data-modeling problems (bitemporality, point-in-time, reconciliation) that barely exist elsewhere. This primer is the domain context behind the engineering — the vocabulary, systems, regulations, and recurring projects — so you can speak the language fluently.

> **How to use it.** Read it as the "business and regulatory layer" that sits on top of your technical skills. In a bank, knowing Spark/Kafka/Iceberg is table stakes; what earns trust is understanding *why* a trade has both a valid-time and a system-time, *why* a risk number must be reproducible to the second three years later, and *what BCBS 239 demands of your pipeline*. The final sections turn this into resume framing and interview talking points.

---

## 1. The shape of a bank: front, middle, back office

Everything in capital markets data flows along an organizational and process spine. Know it cold.

- **Front office** — revenue generators: traders, salespeople, quants/quant-devs, structurers. They want speed, real-time P&L, pricing, and risk. This is where market data and trading systems live.
- **Middle office** — risk management, product control (P&L verification), trade validation, market risk, credit risk. They consume front-office data and demand correctness and reproducibility.
- **Back office** — operations: settlement, clearing, confirmations, reconciliations, the general ledger, "books and records." Lower latency tolerance but absolute correctness and auditability.
- **The data engineer's reality:** data is *produced* in the front office at high speed, *validated/aggregated* in the middle office, and *reconciled/reported* in the back office — and your pipelines usually span all three, which is why **front-to-back lineage and reconciliation** is the recurring theme.

**Business lines you'll hear:** Equities, Fixed Income (Rates & Credit), FX, Commodities, Derivatives (listed and OTC), Prime Brokerage, Securities Services, Asset & Wealth Management, Investment Banking (advisory/underwriting), Treasury.

## 2. The trade lifecycle (the backbone everything hangs off)

Most financial data pipelines model some slice of this lifecycle. Be able to narrate it:

1. **Pre-trade** — market data, pricing, pre-trade risk/limit checks, research.
2. **Order** — order created (often via an **OMS** — Order Management System) and routed (**EMS** — Execution Management System).
3. **Execution** — order filled, partially or fully, generating **fills/executions** (often over the **FIX** protocol).
4. **Booking** — the trade is booked into a trading/risk system, creating a **position**.
5. **Trade capture & enrichment** — reference data attached (instrument, counterparty, legal entity).
6. **Validation / affirmation / confirmation** — middle office checks; counterparties confirm.
7. **Clearing** — a central counterparty (CCP) steps in for cleared products.
8. **Settlement** — exchange of cash and securities (now **T+1** in the US since 2024; pressure toward **T+0**).
9. **Position & P&L** — positions updated, P&L computed (realized/unrealized).
10. **Risk** — exposures and risk metrics recomputed.
11. **Books & records / GL** — posted to the general ledger; end-of-day reconciliation.
12. **Regulatory reporting** — transaction reports, trade reporting, surveillance feeds.

> Engineering implication: a single trade generates events across many systems, each with its own identifier and timing. **Joining the trade's full story across systems — and proving it reconciles — is the central data challenge.**

## 3. Core financial data types & concepts

- **Instrument / security** — the thing traded (a stock, bond, option, swap). Identified by **ISIN, CUSIP, SEDOL, RIC, Bloomberg ticker, FIGI**.
- **Reference data** — relatively static descriptive data: instrument details, **corporate actions** (splits, dividends, mergers), calendars, holidays.
- **Counterparty / legal entity** — who you traded with; identified by **LEI** (Legal Entity Identifier).
- **Market data** — prices/quotes: **tick data** (every quote/trade), **OHLC** bars, **EOD** marks, **bid/ask/mid**, volumes, **yield curves**, **vol surfaces**.
- **Trade / transaction** — an executed deal with price, quantity, timestamps, IDs.
- **Position** — net holding in an instrument at a point in time.
- **P&L (profit & loss)** — realized vs unrealized; **mark-to-market**.
- **Valuation / pricing** — model-derived value of a position (esp. for derivatives).
- **Risk metrics** — **VaR** (Value at Risk), **Greeks** (delta, gamma, vega, theta, rho), **DV01/PV01**, exposures, sensitivities.
- **Corporate actions** — events changing an instrument (a notoriously messy data problem).
- **Settlement instructions (SSIs)** — where/how cash and securities move.

## 4. Market data & reference data (vendors, feeds, the "golden source" problem)

- **Vendors:** **Bloomberg** (BLPAPI, B-PIPE, Data License), **Refinitiv/LSEG** (Eikon, Elektron/Real-Time, DataScope), **ICE Data Services**, **S&P/IHS Markit**, exchange direct feeds.
- **Protocols:** **FIX** (Financial Information eXchange — order/execution messaging), **FAST**, **ITCH/OUCH** (exchange feeds), **SWIFT** (settlement messaging).
- **Securities master / golden source** — the authoritative, deduplicated reference-data store every system trusts. Building/owning one is a classic, high-stakes data-engineering mandate.
- **Symbology mapping** — translating between ISIN/CUSIP/SEDOL/RIC/ticker across systems. Endlessly painful; a recurring pipeline.
- **Corporate-actions processing** — adjusting prices/positions for splits, dividends, mergers. A perennial source of data bugs.
- **Point-in-time reference data** — what did this instrument's attributes look like *on the trade date*? (See bitemporality below — critical.)

## 5. Time-series & tick data (the latency world)

- **Tick data** — every quote and trade, billions of rows per day per venue. Storage and query at this scale is a specialty.
- **kdb+/q (KX)** — the iconic columnar time-series database of capital markets, with its **q** language. Knowing it is a strong signal in trading-floor data roles.
- **Time-series databases** — also InfluxDB, TimescaleDB; but kdb+ dominates front-office tick.
- **Latency tiers** — ultra-low-latency (HFT, microseconds, often C++/FPGA — usually not the data engineer's world), low-latency intraday (milliseconds), and EOD batch.
- **Intraday vs end-of-day (EOD)** — real-time intraday risk/P&L vs the nightly batch that produces official marks and reports.
- **Market-data normalization** — turning many venue/vendor formats into one canonical model.

## 6. Bitemporal data & point-in-time correctness (the defining modeling challenge)

This is the concept that most distinguishes finance data engineering. Master it.

- **Bitemporal modeling** — every fact carries **two time dimensions**: **valid time** (when it was true in the real world) and **system/transaction time** (when the system recorded it). A trade amended yesterday for a deal that happened last week needs both.
- **Why it matters:** regulators, auditors, and risk all ask *"what did we know, and when did we know it?"* You must reproduce any report **as it stood at a past moment**, including data that was later corrected.
- **As-of / point-in-time queries** — "give me positions/prices/risk **as of** end-of-day three Tuesdays ago, using only what was known then." Foundational for backtesting, audit, and dispute resolution.
- **Restatement** — when corrected data forces re-reporting of a prior period; in finance this is a governed, audited event, never a silent overwrite.
- **Engineering implication:** append-only, immutable, fully-versioned storage with effective-dating everywhere. Overwriting data destroys the audit trail — often a regulatory violation.

## 7. Risk data & BCBS 239 (where data engineering meets regulation hardest)

- **Risk types:** **market risk** (price moves), **credit risk** (counterparty default), **liquidity risk**, **operational risk**, **counterparty credit risk (CCR)**.
- **Key metrics:** **VaR / Expected Shortfall (ES)**, **stressed VaR**, **PFE** (potential future exposure), **CVA/DVA/FVA** (valuation adjustments), **RWA** (risk-weighted assets).
- **Risk data aggregation** — pulling positions and market data from across the firm to compute firmwide risk, often overnight, sometimes intraday. Enormous join-and-aggregate pipelines.
- **BCBS 239** — the Basel principles for **risk data aggregation and reporting**. For a data engineer at a big bank this is huge: it mandates **accuracy, completeness, timeliness, adaptability, and — critically — data lineage and governance** for risk data. Your pipelines must *prove* where every number came from. BCBS 239 is often the explicit driver behind lineage, catalog, and reconciliation projects.
- **FRTB (Fundamental Review of the Trading Book)** — overhauled market-risk capital rules (phasing in across jurisdictions); demands far more granular data and historical depth, a major data-engineering burden.
- **CCAR / stress testing** — regulator-mandated stress scenarios requiring massive, auditable data assembly.

## 8. The regulatory & compliance landscape (the constraint that shapes everything)

In finance, regulation is not a side concern — it's a primary architectural input. Know these by name and what they demand of data:

- **Dodd-Frank** (US) — swaps/derivatives reporting, Volcker rule.
- **MiFID II / MiFIR** (EU) — **transaction reporting**, best-execution evidence, vast record-keeping (including communications). A classic source of reporting-pipeline work.
- **Basel III / IV** — capital and liquidity requirements (drives risk data).
- **EMIR** (EU) — derivatives trade reporting to repositories.
- **CAT (Consolidated Audit Trail)** (US) — every order/execution event reported to a central repository; an enormous data pipeline obligation.
- **SEC Rule 17a-4 / WORM storage** — records must be retained immutably (**Write Once Read Many**); shapes storage architecture and retention.
- **AML / KYC** — anti-money-laundering / know-your-customer; drives screening, monitoring, and customer-data pipelines.
- **SOX (Sarbanes-Oxley)** — controls over financial reporting; drives change control, segregation of duties, and auditability of any pipeline feeding the books.
- **SR 11-7 (model risk management)** — governance of models (incl. pricing/risk), requiring documented, reproducible model inputs/outputs — i.e., your data.
- **GDPR / CCPA** — privacy; data residency and deletion (in tension with finance's retention rules).
- **Trade/transaction surveillance** — regulatory expectation to detect market abuse (see §10).

> Engineering implication: **lineage, immutability, reproducibility, retention, and access control are not nice-to-haves — they're legally required.** This is why finance data platforms over-index on governance and audit relative to other industries.

## 9. Reconciliation, books & records, and P&L control

- **Reconciliation** — proving two systems agree (front-office trades vs back-office books vs the GL vs the custodian). The single most common finance data task. Breaks ("recon breaks") must be investigated and explained.
- **Books and records** — the official, regulator-facing record of all positions and transactions.
- **General ledger (GL)** — accounting source of truth; data engineers feed and reconcile against it.
- **Product control / P&L explain** — middle-office function decomposing daily P&L into drivers (market moves, new trades, fees). Requires precise, point-in-time data.
- **T+1 / T+0 settlement** — compressed settlement timelines (US moved to T+1 in 2024) squeeze the time available for these processes, raising the bar on pipeline speed and straight-through processing (**STP**).

## 10. Trade surveillance & financial crime

- **Trade surveillance** — monitoring for market abuse: **spoofing, layering, wash trades, front-running, insider trading**. Pipelines correlate orders, executions, market data, and often communications (e-comms/voice).
- **Transaction monitoring (AML)** — detecting suspicious money movement; generates **SARs** (Suspicious Activity Reports).
- **Sanctions/watchlist screening** — checking counterparties against OFAC and other lists.
- **Engineering implication:** these are large, low-false-positive-sensitive pipelines combining structured trade data with unstructured communications — increasingly an **AI/ML and NLP** surface (a place your modern skills meet the domain).

## 11. The iconic platforms & tools (firm-specific color)

Knowing these signals real domain exposure:

- **Goldman Sachs — SecDB & Slang.** The legendary **Securities Database (SecDB)**, a firmwide object database holding positions/market data, programmed in the in-house **Slang** language; underpins risk and pricing. Also **Marquee** (their client-facing platform) and **Marcus**/consumer efforts. SecDB's single-consistent-risk-view philosophy is a famous data-architecture story.
- **JPMorgan — Athena.** A firmwide **Python-based** cross-asset risk/pricing/trade platform (analogous in spirit to SecDB). JPMC is also known for huge data-platform and cloud investment, and publicly for large-scale data and AI initiatives.
- **Morgan Stanley — Quartz** (similar Python-based platform), for context.
- **BlackRock — Aladdin** — dominant third-party portfolio/risk platform used across the buy-side and some sell-side.
- **Vendor trading/risk platforms:** **Murex (MX.3), Calypso, Summit, OpenLink/ION** — OTC/derivatives trading and risk systems you'll integrate with.
- **Quant libraries:** **QuantLib** (open source pricing), internal pricing libraries.
- **Market data:** Bloomberg (**BLPAPI**), Refinitiv/LSEG, ICE.
- **Messaging/protocols:** **FIX**, **SWIFT**, MQ (IBM MQ is everywhere in banks), Kafka (increasingly).
- **Legacy you'll meet:** **mainframe/COBOL**, **Sybase/DB2/Oracle**, **Autosys/Control-M** (batch schedulers), kdb+ for tick. A 20-year finance career almost always includes a mainframe-or-Sybase chapter.

## 12. Finance-specific data engineering patterns & projects (the "what you went through")

The recurring mandates a finance data engineer accumulates — useful as resume/interview narratives:

- **Market data platform** — capturing, normalizing, storing, and serving real-time + historical market data (often kdb+ for tick).
- **Securities master / reference-data hub** — building the golden source and symbology mapping.
- **Regulatory reporting pipeline** — e.g., MiFID II transaction reporting or CAT: assemble, validate, and submit reports with full audit trail under tight deadlines.
- **Risk data aggregation platform (BCBS 239)** — firmwide overnight (and intraday) risk assembly with lineage and reconciliation.
- **P&L / position reconciliation** — front-to-back recon with break detection and explain.
- **Trade surveillance pipeline** — correlating orders/executions/comms for market-abuse detection.
- **Bitemporal data store** — point-in-time-correct positions/prices for audit and backtesting.
- **Legacy modernization** — migrating mainframe/Sybase/COBOL batch to a modern lakehouse without breaking the books (a massive, reconciliation-heavy effort).
- **End-of-day batch hardening** — making the nightly close faster and more reliable under T+1 pressure.
- **Data lineage & catalog program** — often regulation-driven (BCBS 239 / SOX).
- **Cloud migration under regulatory constraint** — moving to cloud while satisfying residency, retention (WORM), and audit requirements.

## 13. Non-functional realities unique to finance

- **Market-hours criticality** — production issues during trading hours are severe; change freezes around market open/close and month/quarter-end.
- **Reproducibility & audit** — any number may be questioned years later; you must reproduce it exactly with point-in-time inputs.
- **Segregation of duties & change control** — strict separation between who builds and who deploys; heavy approvals (SOX-driven).
- **Immutable retention** — WORM storage, multi-year retention, legal holds.
- **Data residency & entitlements** — market-data licensing is strict (you pay per use and must enforce who can see what); cross-border data rules.
- **Four-eyes / maker-checker** — dual control on sensitive changes.
- **Disaster recovery** — strict RTO/RPO; finance is heavily regulated on resilience.

## 14. Domain vocabulary quick-reference

**Instruments & IDs:** ISIN, CUSIP, SEDOL, RIC, FIGI, LEI, ticker · equity, bond, swap, option, future, forward, repo, ETF · OTC vs listed.
**Trading:** OMS, EMS, FIX, fill/execution, limit/market order, bid/ask/mid, spread, liquidity, market maker, buy-side/sell-side.
**Positions & P&L:** long/short, mark-to-market, realized/unrealized P&L, P&L explain, NAV, exposure.
**Risk:** VaR, Expected Shortfall, Greeks (delta/gamma/vega/theta/rho), DV01/PV01, RWA, CVA/DVA/FVA, PFE, stress test, FRTB, BCBS 239.
**Lifecycle:** front/middle/back office, trade capture, affirmation, confirmation, clearing, CCP, settlement, T+1, STP, custodian, nostro/vostro.
**Reference/market data:** securities master, golden source, symbology, corporate actions, yield curve, vol surface, EOD mark, tick data.
**Reg & compliance:** MiFID II, Dodd-Frank, EMIR, Basel III/IV, CAT, SEC 17a-4, WORM, SOX, AML/KYC, SAR, OFAC, SR 11-7, surveillance, spoofing/layering/wash trades.
**Platforms:** SecDB/Slang (GS), Athena (JPMC), Quartz (MS), Aladdin (BlackRock), Murex/Calypso/Summit, kdb+/q, Bloomberg/Refinitiv, IBM MQ, Autosys/Control-M.

## 15. Positioning this on a resume & in interviews

- **Lead with domain + scale + regulation:** "Built the firmwide risk-data-aggregation pipeline (BCBS 239-aligned) assembling positions across N asset classes nightly, with full column-level lineage for audit." That sentence says *finance engineer*, not generic.
- **Name the hard concepts you owned:** bitemporal/point-in-time stores, front-to-back reconciliation, securities master, regulatory reporting (MiFID II / CAT), market-data normalization, tick-data platforms.
- **Pair legacy with modern:** "Migrated a Sybase/Autosys EOD batch to a Spark + Iceberg lakehouse, preserving point-in-time reproducibility and WORM retention." Shows depth *and* currency.
- **Speak to the constraints fluently:** reproducibility, immutability, segregation of duties, market-hours criticality. Interviewers test whether you understand *why* finance is different.
- **Interview signal:** when asked a system-design question, proactively raise lineage, point-in-time correctness, reconciliation, and audit — in finance, an answer that ignores these reads as "hasn't actually worked in a bank."

---

*The through-line: in capital markets, the data engineer's hardest problems aren't throughput — they're correctness, reproducibility, lineage, and reconciliation under regulatory scrutiny. Master that framing and 20 years of fintech experience reads as exactly what JPMorgan or Goldman wants.*