# alt-SQLAlchemy M5 -- integration and cost

M2 built a typed query-builder facade over a reflected schema (Layer A). M3
built a typed-function generator from hand-written `.sql` (Layer B). M4 built a
static analyser for the second one. M5 asks the only question left: **do they
compose into one application, and what do they cost?**

```
   joins.toml ──▶ altsa_gen (M2) ──▶ generated_a/facade.py ──▶ altsa_runtime
                       ▲                                            │
                  live PostgreSQL 16                        SQLAlchemy Core
                       ▼                                            │
   queries/*.sql ─▶ altsa_sqlgen (M3) ─▶ generated_b/*.py ──▶ psycopg ──┴──▶ the same DB
                                                                 ▲
                                              app/ (order service) uses BOTH
```

Neither generator is copied. Both are imported from their own milestone trees
by `m5link.py`; `alt-sqlalchemy-m0..m4` are byte-for-byte unchanged by this
milestone (verified: 0 files outside `alt-sqlalchemy-m5/` have a newer mtime).

## Reproduce

```sh
docker run -d --name altsa-m5-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=altsa -p 55438:5432 postgres:16
docker exec -i altsa-m5-pg psql -U postgres -d altsa \
  < ../sqlacodegen-trial/ddl/postgres.sql

uv sync
./verify.sh            # every gate, teed into evidence/
```

## Gate results

| gate | verdict | evidence |
|---|---|---|
| **E1** integrated end-to-end demo | **PASS** | `evidence/scenario.log` -- 54 checks, 0 failures; `evidence/pyright.txt` (0 errors, strict, over `app/` + `generated_a/` + `generated_b/` + `bench/`); `evidence/no_any.txt` (18 app signatures, 0 `Any`, 0 unexpected foreign types); `evidence/generation.txt`; `evidence/determinism.txt` |
| **E2** benchmarks | **PASS** | `evidence/bench.txt`, `evidence/bench.json`, `evidence/gen_timings.json` |
| **E3** friction log | **PASS** | [`friction.md`](friction.md) -- 22 items, 2 defects, 7 previously-known limitations now costed |

E1 came with a **finding**: `ORDERS.status.eq(OrderStatus.PAID)` type-checks
under M2's proofs and fails at runtime against PostgreSQL. See friction F11 and
`app/compat.py`. Integration found a defect that neither milestone's own
evidence could.

---

# E1 -- the order service

`app/` is a small order service. The command side is Layer B, the query side is
Layer A, and the scenario runs the whole flow against live PostgreSQL.

### Command side (Layer B -- `app/commands.py`)

| operation | SQL feature that puts it here |
|---|---|
| `register` | `INSERT … RETURNING` (Layer A cannot return the generated uuid) |
| `upsert` | `INSERT … ON CONFLICT … DO UPDATE … RETURNING` |
| `create_order` | a **transaction**: `SELECT … FOR UPDATE` → conditional `UPDATE … WHERE stock >= n RETURNING` per line → header + lines → total |
| `pay` | `INSERT … RETURNING`, with a nullable parameter |
| `transition` | optimistic lock: `UPDATE … WHERE version = $n RETURNING`; no row = someone else won |

`create_order` takes prices from the **locked** rows, so a caller cannot ask to
be charged its own idea of the price, and the lock order is the query's own
`ORDER BY sku`, which makes two concurrent create_orders deadlock-free.

### Query side (Layer A -- `app/queries.py`)

| operation | why it is here |
|---|---|
| `search_orders(OrderSearch)` | three optional criteria, **any subset** -- 8 WHERE shapes from one function, `all_of()` with no arguments being the no-op |
| `list_users_with_orders` | `users LEFT JOIN orders`; the generator types all four order fields `\| None` from the join shape alone |
| `latest_order_per_user` | the same listing, folded in Python -- Layer A has no `DISTINCT ON`/window functions (friction F15) |
| `revenue` | `sum_` is typed `Expr[T] -> Expr[T \| None]`, so the honest `Decimal \| None` survives to the app signature |
| `revenue_by_user` | `GROUP BY`; no `HAVING` available |

### The scenario's assertions

`evidence/scenario.log`, 54 checks, 0 failures. The load-bearing ones:

```
[ok] Layer A cannot see Layer B's UNCOMMITTED order -- 0 row(s) visible mid-transaction
[ok] WIDGET stock decremented by 2 -- 10 -> 8
[ok] the SUCCESSFUL line's decrement was rolled back too -- 8 -> 8
[ok] the STALE transition is detected
[ok] all four order fields are None -- order_id=None total=None
[ok] revenue equals the hand-computed Decimal -- 59.48 vs 59.48
[ok] SUM over an empty set really is None -- hence `Decimal | None` -- None
[ok] repeating a search shape is a facade memo HIT -- +3 hits, +0 misses
[ok] same status, spelled differently by design -- Layer B 'paid' (str) vs Layer A <OrderStatus.PAID: 'paid'> (enum)
```

The rollback check is the sharp one: the failing order asks for `WIDGET × 1`
(which succeeds) and then `GIZMO × 99` (which cannot), and the assertion is that
the *successful* decrement is gone too.

### Types

* `pyright --strict`: **0 errors** over `app/`, `generated_a/`, `generated_b/`,
  `bench/`, `m5link.py`, `regen.py`.
* `proofs/no_any.py`: 18 app-level signatures, **0 `Any`**. The only foreign
  types reachable from an app signature are `altsa_runtime.conn.Conn`,
  `psycopg.Connection`, and `altsa_runtime`'s `Pred`/`Expr` (which M2's facade
  re-exports as its own public surface). **No SQLAlchemy type appears anywhere
  in `app/`** -- the property M2 exists to provide, consumed here by an
  application rather than by a proof.
* Two checks in the scenario had to be *rewritten* because pyright rejected them
  as unnecessary (`isinstance(buyer.id, UUID)`, `isinstance(r.status,
  OrderStatus)`). That is the type system doing its job at the expense of the
  test, and it is noted where it happens.

---

## The connection / transaction seam

**Layer A and Layer B do not share a connection.** Full write-up in
`app/seam.py`; the short version:

```
Layer A   generated_a.facade → altsa_runtime.Conn → SQLAlchemy Engine → psycopg (as DBAPI)
Layer B   generated_b.*      → psycopg.Connection (its own socket)
```

Two backend sessions against the same PostgreSQL, same credentials.

**Can Layer B functions run on a psycopg connection while Layer A uses its
own?** Yes — that is exactly what `OrderService` does, and it works. Both
layers operate on the same data concurrently without interference beyond
ordinary PostgreSQL locking.

**What that means for atomicity:**

* **Atomicity does not cross the seam.** A Layer B transaction and a Layer A
  read are two different transactions. Uncommitted Layer B work is invisible to
  Layer A — asserted, not assumed, in the scenario.
* There is no way to write "insert the order (B) and, in the same transaction,
  run the Layer A query (A) that decides whether to keep it". `create_order`
  therefore does all of its reading *in Layer B*, even though reading is
  supposed to be Layer A's job.
* "Write then immediately read back" is only correct if the write committed
  first. That rule is enforceable by review, not by types.
* The two connections can deadlock against each other exactly as two processes
  can, and neither layer offers a lock-ordering discipline.

**Why the split exists:** it is an artefact, not a design. M2 chose SQLAlchemy
Core because "a typed facade over the incumbent" was its thesis; M3 chose raw
psycopg because it needed `pgconn.describe_prepared`. Nothing about the
application wanted two pools.

**How a real application would manage it** — three options, increasing cost:

1. **Two pools, no shared transactions** (what M5 does). A SQLAlchemy
   `QueuePool` for reads, a `psycopg_pool.ConnectionPool` for writes; every
   Layer B unit of work self-contained inside one `with conn.transaction()`;
   Layer A treated as a read replica that happens to be the primary. Isolation
   note: under PostgreSQL's default READ COMMITTED, Layer A sees a Layer B
   commit on its very next statement even with its own transaction still open.
   Under REPEATABLE READ it would not — hence `OrderService.refresh_read()`.
2. **Layer A on Layer B's connection.** `Conn.__init__` builds its own `Engine`
   from a DSN and takes no existing connection. SQLAlchemy has supported
   `create_engine(..., creator=...)` for years, so this is a change to *one
   constructor* (friction F6). It is the fix worth building.
3. **A shared transaction bridge** — a `UnitOfWork` owning one psycopg
   connection, handing Layer A a `Conn` bound to it and Layer B the raw handle,
   with one `commit()`. Out of scope for M5 by the brief, and it is option 2
   plus an API.

---

# E2 -- benchmarks

**Environment caveats, up front.** macOS laptop (Darwin 25.6, Apple silicon),
PostgreSQL 16 in Docker on localhost, Python 3.12.12, SQLAlchemy 2.0.52,
psycopg 3.3.4. Single process, single thread, no isolation from the rest of the
machine. n = 2000 timed iterations after 200 warmup. **Medians, relative
numbers only** — the absolute microseconds mean nothing off this machine, and
no confidence interval is claimed.

Methodology note: the five variants are timed **interleaved**, with a rotating
start index, not one after another. The first draft ran them back to back and
reported the generated Layer B function as *faster* than the hand-written
psycopg call it wraps, which cannot be true; back-to-back blocks measure when a
variant ran as much as what it did.

## The results table

One statement, `SELECT id, sku, name, price, stock FROM products WHERE sku = $1`,
measured five ways (`evidence/bench.txt`):

| # | variant | p10 µs | **median µs** | p90 µs | vs. raw psycopg |
|---|---|---:|---:|---:|---:|
| a | **Layer A facade** (rebuilt per call → shape memo → SQLAlchemy Core) | 164.9 | **177.3** | 208.5 | 1.78× |
| b | SQLAlchemy Core, prebuilt `Select` + `bindparam` | 113.0 | **120.2** | 137.9 | 1.21× |
| c | raw psycopg, separate connection | 92.2 | **99.4** | 115.0 | 1.00× |
| d | **Layer B generated** `get_product_by_sku()` | 93.2 | **99.8** | 114.3 | 1.00× |
| e | hand-written psycopg, same SQL, same connection as (d) | 90.7 | **97.9** | 112.3 | 0.98× |

| delta | µs | ratio |
|---|---:|---:|
| **Layer A over SQLAlchemy Core** | +57.0 | **1.47×** |
| SQLAlchemy Core over raw psycopg | +20.9 | 1.21× |
| **Layer A over raw psycopg** | +77.9 | **1.78×** |
| **Layer B over hand-written psycopg** | **+1.9** | **1.02×** |

Run-to-run variance on this machine is about ±3% on the medians (an earlier
full run of the same harness gave 183.0 / 123.9 / 102.4 / 103.4 / 101.0 µs). The
*ratios* moved by less than 0.02×, which is the reason to quote ratios and
deltas rather than absolutes.

### Reading it

* **Layer B is free.** +1.9 µs on a ~100 µs round trip — inside the run-to-run
  noise — and the +1.9 buys a frozen dataclass with five typed fields instead of
  a bare tuple. Everything Layer B costs is paid at generation time.
* **Layer A costs ~78 µs per query on top of psycopg, ~57 µs of it on top of
  SQLAlchemy Core.** On a local loopback that is 78% overhead; against a
  database one network hop away (~1 ms) it is under 10%. The number to quote is
  the *absolute* ~78 µs, not the ratio.
* **Part of that 57 µs is redundant work, not inherent cost.** `_decoder_for`
  re-decodes uuid and numeric values that SQLAlchemy's own `Uuid`/`Numeric`
  types already decoded (friction F21). The enum decode is genuinely needed;
  the other two are not.
* **The 21 µs from Core over raw psycopg is SQLAlchemy's, not this
  project's** — that is the incumbent's own overhead on an already-compiled,
  cache-hitting statement.
* (b) returns SQLAlchemy `Row`s and (c)/(e) return raw tuples; (a) returns
  decoded values and (d) a frozen dataclass. Compare shapes, not only numbers.

## Cache evidence (E2.1's other half)

From `evidence/bench.txt`, via a class-level `after_cursor_execute` listener
reading `ExecutionContext.cache_hit` (a `sqlalchemy.engine.interfaces.CacheStats`
member) — the documented introspection point, no private state touched:

```
Layer A   1st execution: CACHE_MISS, 2nd: CACHE_HIT, 3rd: CACHE_HIT
Layer A   compile-cache HIT on the hot path: YES
Core      2nd execution of the prebuilt statement: CACHE_HIT
```

And the facade's own shape memo (`altsa_runtime`'s, not SQLAlchemy's):

```
over 2200 Layer A calls: +2200 hits, +0 misses
memo hit rate on the hot path: 100.00%
```

Two independent caches both hit: the facade memo saves rebuilding the Core
`Select`, SQLAlchemy's compiled cache saves re-compiling it to SQL. The 183 µs
median is the **fully warm** number; a cold shape pays both misses once.

## Generation costs (E2.3)

`evidence/generation.txt`, median of 3 full runs each, against the live database:

| generator | input | output | median wall |
|---|---|---|---|
| `altsa_gen` (M2, Layer A) | `joins.toml`, 85 lines | `generated_a/facade.py`, **1223 lines** (7 tables, 2 enums, 10 edges, 31 shapes, 4 projections) | **26 ms** |
| `altsa_sqlgen` (M3, Layer B) | `queries/*.sql`, 123 lines | `generated_b/*.py`, **606 lines** (12 queries, 52 result columns) | **16 ms** |

Both include the round trips to PostgreSQL (reflection for A; `PREPARE` +
`describe_prepared` + `EXPLAIN` per query for B). Both are byte-reproducible:
`evidence/determinism.txt` -- `DETERMINISM: PASS -- both layers byte-identical
across runs`.

**208 lines of hand-written input produce 1829 lines of typed code**, 8.8× —
and the 208 lines are the only part a schema change requires a human to look at.

## The full summary table

| what | measurement | source |
|---|---|---|
| Layer A facade, hot path | **177 µs** median/query | E2.1 (a) |
| SQLAlchemy Core, prebuilt statement | 120 µs median/query | E2.1 (b) |
| raw psycopg | 99 µs median/query | E2.1 (c) |
| Layer A overhead over Core | **+57 µs (1.47×)** | E2.1 |
| Layer A overhead over raw psycopg | **+78 µs (1.78×)** | E2.1 |
| Layer B generated function | 100 µs median/query | E2.2 (d) |
| hand-written psycopg, same SQL | 98 µs median/query | E2.2 (e) |
| **Layer B overhead** | **+1.9 µs (1.02×)** | E2.2 |
| SQLAlchemy compile-cache on the hot path | CACHE_HIT from execution 2 | E2.1 |
| facade shape memo on the hot path | 2200 hits / 0 misses (100%) | E2.1 |
| `altsa_gen` full run | 26 ms (31 shapes → 1223 lines) | E2.3 |
| `altsa_sqlgen` full run | 16 ms (12 queries → 606 lines) | E2.3 |
| **`altsa-analyze` (M4), per query** | **0.065 ms analysis** (+0.115 ms catalog; 2.16 ms process wall, best-of-20) | M4 `evidence/determinism_and_perf.txt` — cited, not re-measured |

M4's analyser is three orders of magnitude cheaper per query than either
generator's per-query cost, which is what makes "run the analyser on every save"
plausible and "run the generator on every save" not.

---

# E3 -- friction log

The complete log is [`friction.md`](friction.md): 22 items in five groups
(getting both layers into one process; connections and transactions; where the
two vocabularies disagree; what Layer A cannot say; performance ergonomics).
Two are marked **DEFECT**, seven are limitations M2 or M3 already documented and
that M5 is the first to actually pay for.

The headline items:

| id | what | severity |
|---|---|---|
| **F11** | `ORDERS.status.eq(OrderStatus.PAID)` type-checks, then fails with `operator does not exist: order_status = character varying`. One-line fix in M2; M5 works around it in `app/compat.py` with an unsound `cast`. M2's evidence missed it because every enum comparison in its proofs is type-level and its executing patterns never filter on an enum column. | **DEFECT** |
| **F2** | Neither layer is a distributable package. M5 reaches them with a 43-line `sys.path` shim and three pyright `extraPaths`. Nothing else matters until `uv add altsa-runtime` works. | blocking |
| **F5/F6** | No shared transaction, because `Conn` cannot be given a connection it does not own. One missing constructor parameter is the difference between "architectural" and "incidental". | blocking |
| **F9** | Layer B has no transaction composition in the type system: four functions belong to one atomic unit and only a docstring says so. | high |
| **F10** | The same enum column is a Python enum in Layer A and `str` in Layer B; the mirror case (`payments.method`'s CHECK) is untyped by *both*. Two generators, two vocabularies, no shared type map. | high |
| **F18** | Layer A has no `RETURNING` and no conditional writes, so 100% of this app's writes are on Layer B. The tidy "Layer A reads, Layer B writes" story is a rationalisation of a limitation. | high |
| **F15** | No `HAVING`, no window functions, no `DISTINCT ON`: "each user's latest order" is folded in Python. | medium |
| **F1** | `uv init` mutated the *parent* repo's `pyproject.toml` before the workspace guard could be added. Reverted by hand. Every milestone in this series hit it. | setup |

The ordered "what would have to exist first" list is at the end of
`friction.md`.

---

# LOC

| part | lines |
|---|---:|
| **hand-written in M5** | **1988** |
|  `app/` (7 modules: seam, domain, commands, queries, compat, scenario, `__init__`) | 1043 |
|  `bench/` | 385 |
|  `proofs/no_any.py` | 173 |
|  `regen.py` + `m5link.py` | 179 |
|  `joins.toml` + `queries/*.sql` (the two generator inputs) | 208 |
| **generated into M5** | **1830** |
|  `generated_a/facade.py` (7 tables, 10 edges, 31 shapes, 4 projections) | 1223 |
|  `generated_b/*.py` (12 queries, 3 modules, 52 result columns) | 606 |
| **imported, not copied** (M2 + M3, unchanged) | **4603** |
|  `altsa_runtime/` (M2, hand-written, schema-independent) | 1664 |
|  `altsa_gen/` (M2, the Layer A generator) | 1280 |
|  `altsa_sqlgen/` (M3, the Layer B generator) | 1659 |

Full per-file breakdown: `evidence/loc.txt`.

The ratio worth noting: **208 lines of declaration → 1830 lines of typed code**,
and the 4603 lines of machinery are amortised across every schema that ever uses
them.

---

# File inventory

```
alt-sqlalchemy-m5/
  README.md                 this file
  friction.md               E3 -- the complete friction log (22 items)
  verify.sh                 re-runs every gate into evidence/
  pyproject.toml            SETUP GUARD: own uv workspace root, members = []
  pyrightconfig.json        strict; extraPaths to M2 and M3
  m5link.py                 the sys.path shim -- how M2 and M3 are reused
  regen.py                  runs BOTH generators; also E2.3's timings
  joins.toml                Layer A input: 4 declared shapes, 4 projections
                            (M2's two, plus OrderSearchRow and UserOrderRow)
  queries/                  Layer B input, 12 queries in 3 files
    users.sql               register_user, get_user_by_email
    products.sql            upsert_product, lock_products_for_update,
                            decrement_stock, get_product_by_sku
    orders.sql              insert_order, insert_order_item, set_order_total,
                            record_payment, transition_order_status,
                            get_order_with_user
  generated_a/facade.py     Layer A output -- byte-reproducible
  generated_b/*.py          Layer B output -- byte-reproducible
  app/
    __init__.py             imports m5link before anything else
    seam.py                 THE SEAM: OrderService, two connections, the
                            atomicity write-up
    domain.py               OrderLine, PaymentMethod, the three failures
    commands.py             Layer B: register / upsert / create_order / pay /
                            transition
    queries.py              Layer A: search_orders / list_users_with_orders /
                            latest_order_per_user / revenue / revenue_by_user
    compat.py               the F11 workaround, and why it exists
    scenario.py             E1: 54 assertions against live PostgreSQL
  bench/
    __init__.py  hotpath.py E2.1 + E2.2, interleaved, with the cache probe
  proofs/no_any.py          0 Any / no leaked types in app signatures
  evidence/
    scenario.log            E1: 54 checks, 0 failures
    pyright.txt/.json       0 errors, strict
    no_any.txt              18 signatures, 0 Any
    generation.txt          E2.3 timings + shape/query counts
    gen_timings.json        the same, machine-readable
    determinism.txt         both layers byte-identical across runs
    bench.txt / bench.json  E2.1 + E2.2
    loc.txt                 per-file line counts
```

## Verdict

**E1 PASS.** The two layers do compose. 54 live assertions, 0 failures, pyright
strict clean, no `Any` and no SQLAlchemy type in any application signature. The
seam is real and documented rather than papered over, and the one place the
composition *broke* (F11) is a defect in M2 that neither M2's nor M3's own
evidence could have found, because it only appears when a generated enum meets a
generated predicate meets a live server.

**E2 PASS.** Layer B is free (+1.9 µs, 1.02×). Layer A costs +78 µs/query over
raw psycopg (1.78×) and +57 µs over SQLAlchemy Core (1.47×) on a local loopback,
with both caches confirmed hitting — and a measurable fraction of that is
double-decoding that a 15-line fix would remove. Generation is 26 ms + 16 ms for
the whole schema; M4's analyser is 0.065 ms per query.

**E3 PASS.** 22 items. The honest summary of the list is that the *type* story
holds up under integration and the *plumbing* story does not yet exist:
packaging, connection ownership, transaction composition and a shared type map
are all still missing, and each of them is a small, specific piece of work.
