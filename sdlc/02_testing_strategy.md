# 02 — Testing Strategy at Scale

> **Audience:** Staff/principal engineers who own test strategy across teams. You already know how to write a unit test. This chapter is about *which* tests to write, *how many*, why your suite is slow and flaky, and how to make a test pipeline that thousands of engineers can lean on without fear.

This is the second chapter of the SDLC reference. It follows [01 — Engineering Workflow & Version Control at Scale](01_engineering_workflow_vcs.md) and feeds directly into [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md).

---

## 1. What tests are for (and what they cost)

A test exists to **let you change code without fear**. That is the whole job. A test that does not increase your confidence to refactor is dead weight.

Every test is simultaneously an **asset** (it catches regressions, documents intent) and a **liability** (it must be maintained, it runs on every CI build, it can break on legitimate refactors). The asset depreciates; the liability compounds. A test that breaks every time you rename a private method is *almost all liability*.

The single most important rule:

> **Test behavior, not implementation.**

```python
# WRONG — asserts implementation. Breaks the moment you refactor internals,
# even when the behavior is identical. False signal.
def test_checkout():
    cart = Cart()
    cart._apply_discount = Mock()          # reaching into internals
    cart.checkout()
    cart._apply_discount.assert_called_once()   # tests HOW, not WHAT

# RIGHT — asserts observable behavior through the public surface.
def test_checkout_applies_member_discount():
    cart = Cart(items=[item(price=100)], customer=member())
    receipt = cart.checkout()
    assert receipt.total == 90             # tests the OUTCOME
```

If you can rewrite the internals and a behavior-correct test still passes, it is a good test.

---

## 2. The pyramid vs the trophy

The classic **test pyramid** (Mike Cohn): many unit tests, fewer integration, very few E2E. The point is *economic* — cheaper, faster tests at the bottom; expensive, slow ones at the top.

The modern **testing trophy** (Kent C. Dodds) reweights toward integration: a fat integration layer, a static-analysis base, and thin unit/E2E caps. The argument: with good types and linters catching trivial bugs, integration tests give the **most confidence per dollar** for service code, because units that pass in isolation routinely fail when wired together.

Both are right in their domain. Pyramid for libraries and algorithmic code; trophy for service/glue code that is mostly orchestration. The wrong answer is the **ice-cream cone**: E2E-heavy, slow, flaky, and impossible to debug.

| Layer | Speed | Cost to write/maintain | Confidence per test | Flakiness risk |
|---|---|---|---|---|
| Static (types/lint) | instant | ~0 | low (trivial bugs) | none |
| Unit | ms | low | narrow | very low |
| Integration | 10ms–1s | medium | high (real wiring) | low–medium |
| Contract | ms–10s | medium | high (cross-service) | low |
| E2E | seconds–minutes | high | broad but coarse | high |

**Why E2E-heavy suites kill you at scale:** they are slow (serial browser/network round-trips), flaky (timing, shared env, real third parties), and give terrible localization — a red E2E tells you *something* broke across ten services but not *what*. Keep E2E to a handful of revenue-critical journeys.

---

## 3. Test types in depth

### 3.1 Unit — fast, isolated, deterministic
Pure logic, no I/O, no clock, no network. Thousands run in seconds. If a "unit" test needs a database, it is misclassified.

### 3.2 Integration — real dependencies
Test your code against *real* collaborators: a real Postgres, a real message broker — spun up hermetically. **Testcontainers** is the standard answer; in-memory substitutes (H2, SQLite, embedded Kafka) are faster but lie about behavior (different SQL dialects, different ordering).

```java
// Real Postgres in a throwaway container — no shared test DB, no fakes lying.
@Testcontainers
class OrderRepositoryTest {
    @Container
    static PostgreSQLContainer<?> db = new PostgreSQLContainer<>("postgres:16");

    @Test void persistsAndReloadsOrder() {
        var repo = new OrderRepository(dataSource(db));
        var saved = repo.save(anOrder());
        assertThat(repo.findById(saved.id())).contains(saved);  // real SQL, real constraints
    }
}
```

### 3.3 Contract tests — the microservice answer
The brittle-E2E trap: to test "service A talks to service B correctly," teams stand up both in an E2E. **Consumer-driven contract testing** (Pact) breaks the dependency:

- The **consumer** writes a test against a *mock* of the provider and publishes the expected interactions as a **contract**.
- The **provider** replays that contract against itself in *its own* CI.
- Neither service is ever deployed together to verify integration.

```javascript
// Consumer side (Pact): declare what you need, generate the contract.
provider.given('user 42 exists')
        .uponReceiving('a request for user 42')
        .withRequest({ method: 'GET', path: '/users/42' })
        .willRespondWith({ status: 200, body: { id: 42, name: like('Ada') } });
```
The provider's pipeline fails the moment it would break any registered consumer — integration confidence with zero cross-service E2E.

### 3.4 End-to-end — few, critical journeys
Real browser/client through the real stack: *login → add to cart → pay*. Five to twenty of these, not five hundred. Treat each as a production asset with an owner.

### 3.5 Smoke / canary in prod
Tests are not done at deploy. **Smoke tests** hit a handful of endpoints post-deploy; **canary/synthetic** monitoring runs critical journeys continuously against prod. See [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md) for gating these into the release.

---

## 4. Test doubles — and the over-mocking trap

| Double | What it does | Use when |
|---|---|---|
| **Dummy** | passed but unused (fills a param) | satisfying a signature |
| **Stub** | returns canned answers | you need an input from a collaborator |
| **Spy** | records how it was called | verifying a side-effect happened |
| **Mock** | pre-programmed with *expectations*, fails if unmet | rarely — verifying interactions |
| **Fake** | working lightweight impl (in-memory repo) | replacing real I/O cheaply |

Prefer **fakes and stubs** over **mocks**. A mock asserts *interactions* ("you called `save()` once"), which couples the test to implementation. Fakes assert *state* ("the record is now in the store"), which survives refactors.

The **over-mocking anti-pattern**: mock everything the unit touches, then assert the mocks were called in a particular order. The result is a test that (a) breaks on every refactor, (b) tests your *assumptions* about collaborators rather than reality, and (c) gives **false confidence** — the mocks can drift from the real behavior and the test stays green while prod breaks.

```python
# WRONG — mock soup. Tests the wiring diagram, not the behavior.
def test_notify():
    repo, mailer, fmt = Mock(), Mock(), Mock()
    svc = Notifier(repo, mailer, fmt)
    svc.notify(7)
    fmt.render.assert_called_once_with(repo.get.return_value)  # asserts plumbing
    mailer.send.assert_called_once()

# RIGHT — fake the boundary, assert the outcome.
def test_notify_sends_rendered_email():
    mailer = FakeMailer()                       # in-memory, real-ish
    svc = Notifier(repo=FakeRepo({7: user()}), mailer=mailer)
    svc.notify(7)
    assert mailer.sent[0].subject == "Welcome, Ada"   # observable result
```

Mock at **architectural boundaries** (network, payment gateway), not at every internal seam.

---

## 5. Flaky tests — the scale killer

A flaky test passes and fails on the *same* code. At scale this is catastrophic: a 0.1%-flaky test in a 10,000-test suite fails ~10 builds a day, engineers learn to hit "retry," and a *real* failure gets retried into a green checkmark. **A flaky suite trains people to ignore red.**

**Causes (memorize these):**
- **Timing / `sleep()`** — racing an async operation. Never `sleep`; poll with a deadline or await an event.
- **Order-dependence** — test B passes only if test A ran first (leaked state).
- **Shared mutable state** — a global, a shared DB row, a singleton cache.
- **Real network / time / randomness** — third-party flakiness, `now()`, unseeded RNG.
- **Resource leaks** — unclosed connections, ports, file handles exhausting the runner.

**Detection:** run the suite under retry, and tag any test that passes on retry as a flake candidate. Feed results to a **flake dashboard** (pass/fail history per test) so flakiness is *measured*, not anecdotal.

**Policy:** **quarantine, then fix.** Quarantine moves a flaky test out of the blocking gate (it still runs and reports) so it stops blocking everyone — *with a tracked ticket and an owner and an SLA*. Quarantine is a hospital, not a graveyard. **Never** add a blanket `@retry(3)` to the whole suite — that hides flakiness instead of fixing it.

```python
# WRONG — sleep-and-pray. Flaky by construction.
job.start(); time.sleep(2); assert job.status == "done"

# RIGHT — bounded polling, deterministic.
wait_until(lambda: job.status == "done", timeout=5)
```

---

## 6. Hermetic, deterministic tests & test data

A **hermetic** test depends on nothing outside its own declared inputs — no shared DB, no clock, no network, no ambient env vars. Same inputs, same result, anywhere, forever.

**Determinism discipline:**
- **Fake the clock** — inject a `Clock`; never call `System.currentTimeMillis()` / `time.time()` in code under test.
- **Seed randomness** — every RNG gets a fixed seed in tests.
- **Hermetic env** — pin timezone, locale, and dependency versions.

**Test data management:**
- **Factories / builders** over hand-built fixtures — `aUser().withMembership(GOLD).build()`. Readable, override only what matters.
- **Fixtures** for shared setup, but beware the "mystery fixture" that 200 tests depend on.
- **Golden files** (snapshot tests) for large structured output — but review every snapshot change; blind `--update-snapshots` is how garbage gets blessed.
- **The DB-state problem:** tests that mutate a shared database create order-dependence and flakiness. Fix with per-test transactions rolled back at teardown, or a fresh Testcontainer per test class. Never rely on data another test created.

---

## 7. Coverage is a floor, not a goal — use mutation testing

Line coverage measures which lines *ran*, not whether anything was *checked*. It is trivially gamed:

```python
# 100% line coverage, ZERO assertions. The metric says "covered." It is a lie.
def test_calculate():
    calculate(5, 3)   # runs every line; verifies nothing
```

**Mutation testing** measures test *quality*. The tool (PIT for Java, Stryker for JS/TS/C#, mutmut/cosmic-ray for Python) makes tiny changes to your code — flip `<` to `<=`, replace `+` with `-`, return `null` — and reruns your tests. If a mutant **survives** (tests still pass), your tests would not catch that bug.

| Metric | What it answers | Gameable? |
|---|---|---|
| Line coverage | Did this line execute? | Trivially (no asserts) |
| Branch coverage | Did both branches run? | Mostly |
| **Mutation score** | Would my tests *catch a bug* here? | Very hard |

Use coverage as a **floor** (e.g., new code must not drop below X%) to catch the egregiously untested. Use mutation testing on your *critical* modules (billing, auth) to find tests that run code but assert nothing. Mutation testing is expensive — scope it to high-value code and run it nightly, not on every PR.

---

## 8. Property-based testing

Instead of hand-picking examples, **declare a property** and let the framework generate hundreds of inputs, then **shrink** any failure to a minimal counterexample. Tools: Hypothesis (Python), QuickCheck (Haskell/many ports), jqwik (Java), fast-check (JS).

```python
from hypothesis import given, strategies as st

# Property: round-tripping through serialize/deserialize is identity.
@given(st.text())
def test_serialize_roundtrip(s):
    assert deserialize(serialize(s)) == s   # finds the empty string, the emoji, the "\0"
```
Properties excel at: round-trips, invariants ("output is always sorted"), commutativity, and "never throws on valid input." They routinely surface the edge cases example-based tests never imagine.

---

## 9. Performance & load testing

Functional green does not mean fast. Add **performance gates** alongside correctness gates.

- **Load/throughput:** k6, Gatling, Locust for services.
- **Micro-benchmarks:** JMH (Java), pytest-benchmark, criterion — for hot-path code.
- **Regression-perf gates:** capture a baseline (p50/p95/p99); fail the build if a PR regresses p99 latency beyond a threshold. Catch the 30% slowdown before prod.

**The coordinated-omission trap:** most naive load tools send a request, *wait* for the response, then send the next. When the server stalls, the tool stalls too — so the slow period is *under-sampled* and your latency percentiles look far better than reality. Use tools/modes that maintain a constant arrival rate (open-model load, e.g. k6's constant-arrival-rate executor, or `wrk2`) and report **corrected** percentiles. A p99.9 that ignores coordinated omission is fiction.

Resilience-oriented testing (fault injection, latency injection, dependency failure) is its own discipline — see [07 — Chaos & Resilience Engineering](07_chaos_resilience_engineering.md).

---

## 10. Test infrastructure at scale

The governing rule: **tests must be fast, or they won't be run.** A suite engineers can't run locally is a suite they'll work around.

- **Parallelism & sharding** — split the suite across N runners. Requires hermetic tests (§6) — order-dependence makes sharding non-deterministic.
- **Test selection** — only run tests *affected* by a change, computed from the build/dependency graph. At monorepo scale you cannot run everything on every PR; run the affected subset on PR, the full suite nightly.
- **Hermetic build & test** — **Bazel** (or similar) hashes inputs and caches results: unchanged tests don't rerun, and a remote cache shares results across the org. This is what makes "test selection" trustworthy.
- **CI gating policy** — define what's *blocking* (unit + affected integration + contract) vs *non-blocking/nightly* (full E2E, mutation, perf). Keep the blocking gate under ~10 minutes.

See [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md) for how these gates compose into the pipeline.

---

## 11. Symptom / Cause / Fix

**"The suite takes 45 minutes, so nobody runs it locally."**
- *Symptom:* engineers push speculative commits to let CI run tests; local TDD is dead.
- *Cause:* too many slow tests (E2E/integration where unit would do); no parallelism; no test selection.
- *Fix:* rebalance toward the pyramid/trophy; shard across runners; add affected-test selection so a one-file change runs seconds of tests, not 45 minutes.

**"We have 100% coverage but bugs still ship."**
- *Symptom:* dashboard is green, prod is on fire.
- *Cause:* coverage measures execution, not assertion; tests run code without verifying outcomes; over-mocked tests assert plumbing.
- *Fix:* run mutation testing on critical modules to find assertion-free tests; ban mock-interaction-only tests; test behavior at boundaries (§4).

**"Tests pass locally but fail in CI" (or vice versa).**
- *Symptom:* "works on my machine."
- *Cause:* non-hermetic tests — dependence on local timezone, installed services, ambient env, leftover DB state, or test ordering.
- *Fix:* make tests hermetic (§6) — fake the clock, pin TZ/locale, use Testcontainers instead of a shared DB, randomize test order locally to surface order-dependence *before* CI does.

---

For language-specific testing idioms, frameworks, and tooling, see the language books:
[../python_book/24_testing/](../python_book/24_testing/README.md) ·
[../java_book/25_testing/](../java_book/25_testing/README.md) ·
[../cpp_book/22_tooling_testing/](../cpp_book/22_tooling_testing/README.md).

---

> Next: [03 — CI/CD & Release Engineering](03_cicd_release_engineering.md) — how these test gates compose into a pipeline that ships safely many times a day: build graphs, progressive delivery, canaries, and rollback.
