# 05 — Databases: Fundamentals

> A from-scratch tour of how databases store, protect, and serve data. We cover the
> relational vs. NoSQL split, ACID and transactions, isolation levels and the anomalies
> they prevent, normalization, indexing, keys, the four NoSQL families, OLTP vs. OLAP, and
> connection pooling. Code is in SQL and Python where it clarifies the idea.

---

## 1. Introduction — what problem does a database solve?

At its core a database answers one question reliably: **"Where is my data, and is it
correct when I read it back?"** You could store rows in flat files, but you would then
have to hand-write everything a database gives you for free:

- **Durability** — data survives a crash or power loss.
- **Concurrency** — thousands of clients read and write the same data without corrupting it.
- **Query** — ask arbitrary questions ("all orders over $100 from last week") efficiently.
- **Integrity** — enforce rules ("an order must reference a real customer").

A *Database Management System* (DBMS) packages these guarantees. The two broad worldviews
are **relational** (tables with a fixed schema, joined by keys, queried with SQL) and
**NoSQL** (a family of non-tabular models optimized for scale, flexibility, or a specific
access pattern).

---

## 2. Relational vs. NoSQL

### 2.1 The relational model

Data lives in **tables** (relations). Each row is a **tuple**; each column has a declared
**type**. Tables relate to each other through **keys**. A declarative language, **SQL**,
describes *what* you want; the query planner decides *how* to get it.

```
customers                          orders
+----+----------+-----------+      +----+-------------+--------+----------+
| id | name     | email     |      | id | customer_id | total  | status   |
+----+----------+-----------+      +----+-------------+--------+----------+
| 1  | Aisha    | a@ex.com  |◄─────┤ 91 | 1           | 240.00 | shipped  |
| 2  | Bao      | b@ex.com  |   │  │ 92 | 1           |  18.50 | pending  |
+----+----------+-----------+   └──┤ 93 | 2           | 999.00 | shipped  |
                                   +----+-------------+--------+----------+
       (orders.customer_id is a FOREIGN KEY → customers.id)
```

### 2.2 The NoSQL umbrella

"NoSQL" is a marketing term covering anything that abandons the strict relational
table-with-fixed-schema model, usually to gain **horizontal scalability** or **schema
flexibility**. We dig into the four families in §9.

### 2.3 Side-by-side

| Dimension              | Relational (SQL)                          | NoSQL (varies by family)                       |
|------------------------|-------------------------------------------|------------------------------------------------|
| Schema                 | Fixed, enforced up front                  | Flexible / schema-on-read                      |
| Data model             | Tables + relations                        | KV, document, wide-column, graph               |
| Query language         | SQL (declarative, joins)                  | Per-product APIs; limited/no joins             |
| Consistency default    | Strong (ACID)                             | Often eventual (tunable)                       |
| Scaling                | Vertical first; sharding is manual work   | Horizontal scale-out is a first-class feature  |
| Best for               | Complex queries, transactions, integrity  | Huge scale, simple access patterns, flexible data |
| Examples               | PostgreSQL, MySQL, Oracle, SQL Server     | Redis, MongoDB, Cassandra, DynamoDB, Neo4j     |

**Rule of thumb:** start relational. Reach for NoSQL when a *specific* pressure
(scale, write throughput, flexible shape, graph traversal) outgrows what one well-tuned
relational node can offer. "We might get big someday" is not a reason.

---

## 3. ACID

ACID is the set of guarantees that make a **transaction** — a group of operations — behave
as a single, reliable unit.

| Property         | Promise                                                                 | Failure it prevents                         |
|------------------|-------------------------------------------------------------------------|---------------------------------------------|
| **Atomicity**    | All operations commit, or none do.                                      | A money transfer debits but never credits.  |
| **Consistency**  | A transaction moves the DB from one valid state to another (constraints hold). | A balance going negative when disallowed.   |
| **Isolation**    | Concurrent transactions don't corrupt each other.                       | Two buyers purchasing the last item.        |
| **Durability**   | Once committed, data survives crashes.                                  | "Order confirmed" then lost on power cut.    |

### The classic example — a bank transfer

```sql
BEGIN;
  UPDATE accounts SET balance = balance - 100 WHERE id = 1;  -- debit Aisha
  UPDATE accounts SET balance = balance + 100 WHERE id = 2;  -- credit Bao
COMMIT;
```

If the server crashes between the two `UPDATE`s, **Atomicity** rolls the debit back —
$100 never vanishes. **Durability** ensures that once `COMMIT` returns, the transfer holds
even if the machine dies a millisecond later (the change was written to the *write-ahead
log* and `fsync`'d to disk).

---

## 4. Transactions & isolation levels

When transactions run concurrently, the DBMS must decide how much they can "see" of each
other's uncommitted or in-flight work. Stronger isolation = more correctness, less
concurrency. The SQL standard defines four levels, characterized by which **anomalies**
they permit.

### 4.1 The anomalies

| Anomaly               | What happens                                                                                       |
|-----------------------|----------------------------------------------------------------------------------------------------|
| **Dirty read**        | T1 reads a row T2 wrote but has **not committed**; T2 then rolls back, so T1 read a value that never existed. |
| **Non-repeatable read** | T1 reads a row, T2 **updates & commits** it, T1 reads again and gets a *different* value.         |
| **Phantom read**      | T1 runs a range query, T2 **inserts/deletes** a matching row & commits, T1 re-runs and the row set changed. |
| **Lost update**       | T1 and T2 read the same value, both update based on it; one update silently overwrites the other.  |

### 4.2 The four levels

| Isolation level       | Dirty read | Non-repeatable read | Phantom read | Typical cost        |
|-----------------------|:----------:|:-------------------:|:------------:|---------------------|
| **READ UNCOMMITTED**  | possible   | possible            | possible     | cheapest, unsafe    |
| **READ COMMITTED**    | prevented  | possible            | possible     | common default (PG, Oracle) |
| **REPEATABLE READ**   | prevented  | prevented           | possible*    | MySQL/InnoDB default |
| **SERIALIZABLE**      | prevented  | prevented           | prevented    | most expensive, safest |

\* In PostgreSQL's MVCC-based REPEATABLE READ, phantoms are also prevented in practice;
the SQL standard merely *allows* them at this level. MySQL/InnoDB prevents phantoms at
REPEATABLE READ using next-key locks.

### 4.3 Showing a dirty read vs. read committed

```sql
-- Session A
BEGIN;
UPDATE accounts SET balance = 0 WHERE id = 1;   -- not committed yet
-- (no COMMIT)

-- Session B at READ UNCOMMITTED
SELECT balance FROM accounts WHERE id = 1;        -- sees 0  (DIRTY READ)

-- Session B at READ COMMITTED
SELECT balance FROM accounts WHERE id = 1;        -- sees the OLD committed value
```

### 4.4 Setting the level

```sql
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
BEGIN;
  -- ... your statements ...
COMMIT;
```

### 4.5 How databases enforce isolation

- **Locking (2-phase locking):** acquire shared/exclusive locks; SQL Server and MySQL
  lean here. Strong but can deadlock and block.
- **MVCC (Multi-Version Concurrency Control):** keep multiple versions of a row; readers
  see a consistent snapshot without blocking writers. PostgreSQL and Oracle lean here.
  *"Readers don't block writers, writers don't block readers."*

```
MVCC snapshot view (PostgreSQL):

  row id=1 ──► version (txid 100, balance 500) ──► version (txid 140, balance 400)
                         ▲                                    ▲
            T-old snapshot reads this            T-new snapshot reads this
            (no lock needed; each sees its own consistent version)
```

---

## 5. Normalization vs. denormalization

### 5.1 Normalization

Organizing data to **eliminate redundancy** so each fact is stored once. The normal forms:

- **1NF** — atomic columns (no repeating groups or arrays in a cell).
- **2NF** — 1NF + no partial dependency on part of a composite key.
- **3NF** — 2NF + no transitive dependency (non-key columns depend only on the key).

**Un-normalized (redundant):**

```
orders
+----+----------+-----------------+--------+
| id | customer | customer_email  | total  |
+----+----------+-----------------+--------+
| 91 | Aisha    | a@ex.com        | 240.00 |
| 92 | Aisha    | a@ex.com        |  18.50 |   ◄ email duplicated; update anomaly risk
+----+----------+-----------------+--------+
```

**Normalized (3NF):** split into `customers` and `orders` (see §2.1). Now Aisha's email
lives in exactly one place. Update it once and every order reflects it.

### 5.2 Denormalization

Deliberately reintroducing redundancy to **speed up reads** by avoiding joins. Trades
write complexity (keep copies in sync) for read speed. Common in OLAP, caches, and at
scale where joins across nodes are expensive.

| Aspect             | Normalized            | Denormalized                  |
|--------------------|-----------------------|-------------------------------|
| Redundancy         | Minimal               | Intentional                   |
| Write speed        | Fast (write once)     | Slower (write copies)         |
| Read speed         | Slower (joins)        | Fast (data pre-joined)        |
| Update anomalies   | Avoided               | Possible (must sync copies)   |
| Storage            | Less                  | More                          |
| Fits               | OLTP, write-heavy     | OLAP, read-heavy, reporting   |

---

## 6. Indexing

### 6.1 The problem

Without an index, finding rows means a **full table scan** — read every row. On 50 million
rows that is millions of page reads. An index is an auxiliary, sorted data structure that
turns an O(n) scan into roughly **O(log n)** lookups.

> **Analogy:** the index at the back of a textbook. Instead of reading all 900 pages to
> find "MVCC", you jump to the alphabetized entry and go straight to page 211.

### 6.2 Index types

| Type          | Structure        | Great for                                  | Weak at                          |
|---------------|------------------|--------------------------------------------|----------------------------------|
| **B-tree**    | Balanced tree    | Equality **and** range (`=`, `<`, `>`, `BETWEEN`), `ORDER BY`, prefix `LIKE 'abc%'` | nothing in particular; the default |
| **Hash**      | Hash table       | Exact equality (`=`) only, very fast       | ranges, sorting (can't)          |
| **Composite** | B-tree on (a,b,c)| Multi-column filters; respects left-prefix | querying on non-leading columns alone |
| **Covering**  | Index includes all needed columns | "index-only scan" — never touch the table | extra storage, slower writes     |

```
B-tree (range-friendly):                 Hash index (equality only):

          [ 50 ]                          hash("a@ex.com") -> bucket 7 -> rowid
         /      \                          hash("b@ex.com") -> bucket 2 -> rowid
   [20,35]      [70,90]                     (no ordering -> no range scans)
   /  |  \      /  |  \
 leaf leaf ... (sorted row pointers)
```

### 6.3 How an index speeds reads but costs writes

- **Reads:** the planner navigates the B-tree to the matching leaf, then follows pointers
  to the rows. Range queries walk the sorted leaves sequentially.
- **Writes:** every `INSERT`/`UPDATE`/`DELETE` must also update **every** index on the
  table — extra page writes, possible node splits, and more WAL. More indexes = slower
  writes and more storage.

> **Trade-off:** index columns you filter, join, or sort on frequently. Don't index every
> column "just in case" — you pay on every write and rarely benefit on rarely-queried columns.

### 6.4 SQL examples

```sql
-- Single-column B-tree (default in PostgreSQL/MySQL)
CREATE INDEX idx_orders_customer ON orders (customer_id);

-- Composite index: order matters. Supports filters on (status),
-- (status, created_at), but NOT (created_at) alone — the "left-prefix" rule.
CREATE INDEX idx_orders_status_date ON orders (status, created_at);

-- Covering index (PostgreSQL INCLUDE): the index carries `total` too,
-- so a query selecting only status/total never reads the heap.
CREATE INDEX idx_orders_cover ON orders (status) INCLUDE (total);

-- Hash index (PostgreSQL) — equality only
CREATE INDEX idx_cust_email_hash ON customers USING hash (email);

-- Unique index also enforces a constraint
CREATE UNIQUE INDEX idx_cust_email ON customers (email);
```

---

## 7. Primary keys & foreign keys

- **Primary key (PK):** uniquely identifies each row. Implies `NOT NULL` + `UNIQUE`, and is
  backed by an index. Prefer a stable surrogate key (e.g., auto-increment / UUID) over a
  natural key that might change.
- **Foreign key (FK):** a column that references a PK in another table, enforcing
  **referential integrity** — you cannot insert an order for a customer that doesn't exist,
  and (depending on the rule) deleting a customer can cascade or be blocked.

```sql
CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL
                REFERENCES customers (id) ON DELETE RESTRICT,
    total       NUMERIC(10,2) NOT NULL CHECK (total >= 0),
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`ON DELETE RESTRICT` blocks deleting a customer who still has orders; `ON DELETE CASCADE`
would delete their orders too; `ON DELETE SET NULL` would orphan them.

---

## 8. Full worked example — schema + EXPLAIN

### 8.1 Schema

```sql
CREATE TABLE customers (
    id     BIGSERIAL PRIMARY KEY,
    name   TEXT NOT NULL,
    email  TEXT NOT NULL UNIQUE
);

CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers (id),
    total       NUMERIC(10,2) NOT NULL CHECK (total >= 0),
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Say orders has 50,000,000 rows and we frequently filter by status + date.
CREATE INDEX idx_orders_status_date ON orders (status, created_at);
```

### 8.2 EXPLAIN — before the index

```sql
EXPLAIN ANALYZE
SELECT id, total
FROM orders
WHERE status = 'pending'
  AND created_at >= DATE '2026-06-01';
```

```
Seq Scan on orders  (cost=0.00..1043210.0 rows=24500 width=22)
  Filter: (status = 'pending' AND created_at >= '2026-06-01')
  Rows Removed by Filter: 49975500
Planning Time: 0.18 ms
Execution Time: 8421.6 ms          <-- reads all 50M rows
```

### 8.3 EXPLAIN — with the composite index

```
Index Scan using idx_orders_status_date on orders
      (cost=0.56..1180.4 rows=24500 width=22)
  Index Cond: (status = 'pending' AND created_at >= '2026-06-01')
Planning Time: 0.21 ms
Execution Time: 12.3 ms            <-- ~680x faster
```

The planner now seeks directly to the `status='pending'` section of the B-tree and walks
the date-sorted leaves. Reading EXPLAIN: watch for **Seq Scan** on large tables (often a
missing index), the **cost** estimate (startup..total), estimated vs. actual **rows** (big
gaps mean stale statistics — run `ANALYZE`), and **Execution Time**.

---

## 9. The NoSQL families

| Family          | Data model                              | Read/write pattern                   | Examples              | Fits when…                                   |
|-----------------|-----------------------------------------|--------------------------------------|-----------------------|----------------------------------------------|
| **Key-Value**   | `key -> opaque blob`                    | O(1) get/put by key                  | Redis, DynamoDB, Riak | Sessions, caches, feature flags, simple lookups |
| **Document**    | `key -> JSON/BSON document`             | Query/index within the document      | MongoDB, Couchbase    | Flexible/evolving shapes, content, catalogs   |
| **Wide-Column** | rows of `(row key -> column families)`  | Massive write throughput, range scans | Cassandra, HBase, Bigtable | Time series, event logs, huge write volume |
| **Graph**       | nodes + edges with properties           | Traversals over relationships        | Neo4j, Neptune        | Social graphs, fraud rings, recommendations   |

### Mental models

```
KEY-VALUE                      DOCUMENT
"user:42" -> "{...blob...}"    "user:42" -> { name:"Aisha",
                                              orders:[{id:91,total:240}],
(fast, dumb, exact-key)                       prefs:{theme:"dark"} }
                                             (query INTO the structure)

WIDE-COLUMN (Cassandra)        GRAPH (Neo4j)
rowkey | col1 col2 col3 ...      (Aisha)-[:FRIEND]->(Bao)
sensorA| t1=.. t2=.. t3=..          │
sensorB| t1=.. t5=..                └─[:BOUGHT]->(Item#9)
(sparse, wide, write-optimized)  (relationships are first-class)
```

**When NOT to use NoSQL:** when you need rich multi-table transactions, ad-hoc joins, and
strong consistency on modest data — a relational DB is simpler and safer.

---

## 10. OLTP vs. OLAP

| Dimension       | **OLTP** (Transactional)              | **OLAP** (Analytical)                       |
|-----------------|---------------------------------------|---------------------------------------------|
| Purpose         | Run the business (orders, payments)   | Analyze the business (reports, BI)          |
| Query shape     | Many small reads/writes by key        | Few huge aggregations over millions of rows |
| Example         | "Insert this order"                   | "Revenue by region, by month, last 3 years" |
| Rows touched    | One / a few                           | Millions                                    |
| Schema          | Normalized                            | Denormalized (star/snowflake schema)        |
| Storage         | Row-oriented                          | Column-oriented (read only needed columns)  |
| Latency target  | milliseconds                          | seconds to minutes                          |
| Systems         | PostgreSQL, MySQL                     | Snowflake, BigQuery, Redshift, ClickHouse   |

You typically run OLTP for live traffic, then **ETL/ELT** the data into an OLAP warehouse
so heavy analytics don't compete with customer transactions.

```
[ App ] --writes--> [ OLTP DB ] --ETL nightly/streaming--> [ OLAP Warehouse ] --> [ BI/Dashboards ]
                    (row store)                            (column store)
```

---

## 11. Connection pooling

### The problem

Opening a database connection is **expensive**: TCP handshake, TLS, authentication, backend
process/thread setup — often 5–50 ms and meaningful memory per connection (PostgreSQL
spawns a backend process per connection). If every web request opens and closes its own
connection, you waste most of your time on setup and can exhaust the DB's connection limit
under load.

### The fix

A **connection pool** keeps a set of warm, reusable connections. Requests *borrow* a
connection, use it, and *return* it.

```
[req] [req] [req] [req]      <- many short-lived requests
   \    |    |    /
   ┌──────────────────┐
   │  Connection Pool │  (e.g. 20 warm connections)
   └──────────────────┘
        | | | |
   ┌──────────────────┐
   │     Database     │  (caps at, say, 100 backends)
   └──────────────────┘
```

```python
# Python: psycopg2 + a simple threaded pool
from psycopg2.pool import ThreadedConnectionPool

pool = ThreadedConnectionPool(
    minconn=5, maxconn=20,
    host="db.internal", dbname="shop", user="app", password="***",
)

def get_customer(cust_id: int):
    conn = pool.getconn()                       # borrow
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, email FROM customers WHERE id = %s", (cust_id,))
            return cur.fetchone()
    finally:
        pool.putconn(conn)                      # return (NOT close)
```

**Sizing:** more connections is not better. A common starting formula is
`pool_size ≈ ((core_count * 2) + effective_spindle_count)`. Too large a pool causes
context-switching and lock contention inside the DB. For many app fleets, an external pooler
like **PgBouncer** multiplexes thousands of client connections onto a few dozen real ones.

---

## 12. Key Takeaways

- **Start relational.** Tables + SQL + ACID solve most problems with the least surprise.
- **ACID** = Atomicity, Consistency, Isolation, Durability — the guarantees that make
  transactions trustworthy.
- **Isolation levels** trade safety for concurrency: READ UNCOMMITTED → READ COMMITTED →
  REPEATABLE READ → SERIALIZABLE, each preventing more anomalies (dirty/non-repeatable/
  phantom reads, lost updates). MVCC lets readers and writers avoid blocking each other.
- **Normalize for writes, denormalize for reads.** Redundancy is a deliberate trade, not a mistake.
- **Indexes** turn scans into log-time lookups but tax every write; index what you filter,
  join, and sort on. B-tree is the versatile default; hash is equality-only; composite
  indexes obey the left-prefix rule; covering indexes enable index-only scans.
- **PK/FK** give you identity and referential integrity for free.
- **NoSQL is four different things** (key-value, document, wide-column, graph) — pick the
  one whose access pattern matches yours; don't adopt it just for hypothetical scale.
- **OLTP ≠ OLAP:** row-store transactions vs. column-store analytics; separate them.
- **Pool your connections** — opening them per request is a silent performance killer.
- **Read EXPLAIN.** It tells you whether the planner is scanning or seeking, and where your
  statistics or indexes are wrong.
