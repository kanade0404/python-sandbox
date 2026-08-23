# M5 friction log

Every rough edge hit while building `app/` out of M2's and M3's output. Kept as
it happened, not tidied afterwards. This is the "what would have to exist before
anyone else could use this" list.

Each item says what happened, what it cost, and what the fix would be. The ones
marked **DEFECT** are wrong, not merely missing; the ones marked **KNOWN** were
already written down in M2's or M3's "Known limitations" and are recorded here
because M5 is the first time they were *paid for* rather than predicted.

---

## A. Getting the two layers into one process

### F1 — `uv init` mutates the parent workspace before you can stop it **DEFECT (of the setup, not the code)**

`uv init --python 3.12` in `alt-sqlalchemy-m5/` appended

```toml
[tool.uv.workspace]
members = [".claude/worktrees/replicated-floating-hollerith/alt-sqlalchemy-m5"]
```

to `/Users/kanade0404/work/python-sandbox/pyproject.toml` — a file outside the
worktree, untracked by git, so `git status` in the worktree showed nothing. It
was reverted by hand and the guard (`[tool.uv.workspace] members = []`) added to
M5's own `pyproject.toml` immediately after. Every milestone in this series
carries the same guard comment, which means every milestone hit this.

**Cost:** ~10 minutes and one silent mutation of a file the brief explicitly
forbids touching. **Fix:** `uv init --no-workspace`, or create the pyproject by
hand and never run `uv init` inside a nested checkout.

### F2 — neither layer is a package you can depend on

M2 and M3 are *directories*. `altsa_runtime`, `altsa_gen` and `altsa_sqlgen`
have no distribution name, no version, no `py.typed`, and no way to appear in
M5's `dependencies`. M5 reaches them with `m5link.py`, a 43-line `sys.path`
shim, and pyright needs `extraPaths` pointing at both milestone roots. The shim
also has to set `sys.dont_write_bytecode = True` so that importing them does not
drop `__pycache__` into audited trees.

**Cost:** 43 lines of shim, three `extraPaths` entries, and a rule that
`import m5link` must run before anything imports `generated_a.facade` — which
is why `app/__init__.py` and `bench/__init__.py` both start with it.
**Fix:** publish `altsa-runtime` (and the two generators as build-time tools)
as real distributions. This is not optional for any external user; it is the
first thing that has to exist.

### F3 — two spellings of the same connection string

Layer A takes a SQLAlchemy URL (`postgresql+psycopg://…`), Layer B takes a
libpq DSN (`postgresql://…`). Nothing translates, and neither accepts the
other's form. `app/seam.sqlalchemy_url()` exists solely for this, and
`regen.py` has a second copy because it must not import `app` (it *generates*
what `app` imports).

**Cost:** one function, duplicated once. **Fix:** have `altsa_runtime.Conn`
accept a libpq DSN and add the driver prefix itself.

### F4 — `altsa_gen` has no callable entry point

`altsa_sqlgen.generate(url=…, queries=…, out=…)` is importable and returns a
result object, which is exactly right. `altsa_gen` has only
`altsa_gen/__main__.py`; generating from Python means repeating its
`load_config` → `build_schema` → `render` → `mkdir` → `write_text` sequence.
`regen.py` duplicates ~12 lines of it, and would silently drift if M2's CLI
changed.

**Cost:** 12 duplicated lines. **Fix:** a `altsa_gen.generate()` mirroring
M3's, with the CLI as a thin wrapper.

---

## B. Connections and transactions — the seam

### F5 — the two layers cannot share a transaction, and nothing says so

This is the structural one. Layer A executes on a SQLAlchemy `Connection` built
from a DSN; Layer B executes on a `psycopg.Connection`. Two backends, two
transactions. The scenario demonstrates it rather than asserting it in prose:

```
[ok] Layer A cannot see Layer B's UNCOMMITTED order -- 0 row(s) visible mid-transaction
```

Layer B functions **can** run on their own psycopg connection while Layer A
holds its own — that is exactly what `OrderService` does and it works fine. What
they cannot do is enrol in each other's transaction. Consequences:

* No "insert the order (B) and, in the same transaction, run the search (A)
  that decides whether to keep it". `create_order` therefore does all of its
  reading in Layer B (`lock_products_for_update`), even though the read side is
  supposed to be Layer A's job.
* "POST then immediately GET" is only safe if the POST committed first. The
  rule is enforceable by review, not by types.
* The two connections can deadlock against each other exactly as two processes
  can, and neither layer offers a lock-ordering discipline. M5 dodges this by
  having `lock_products_for_update` carry `ORDER BY sku`.

**Cost:** the whole shape of `app/commands.py`; a 60-line module docstring in
`app/seam.py`; and one architectural claim M5 cannot make.
**Fix:** see F6.

### F6 — `Conn` cannot be given a connection it does not own

`altsa_runtime.conn.Conn.__init__(dsn: str)` calls `create_engine(dsn)` and
`Engine.connect()`. There is no parameter for an existing DBAPI connection, an
existing `Engine`, or an existing `Connection`. That single missing parameter is
what makes F5 structural instead of incidental: SQLAlchemy has supported
`create_engine(..., creator=lambda: existing_dbapi_conn)` and
`Engine.connect()` on an externally-managed pool for years, so a shared psycopg
connection is a small change to *one* constructor.

**Fix:** `Conn(dsn=...)` **or** `Conn.wrapping(dbapi_connection)`. With it, the
shared-transaction bridge the brief put out of scope becomes ~20 lines.

### F7 — `Conn` is one connection wearing a pool's clothes

`RuntimeConn` builds a full `Engine` (default `QueuePool`) and then opens
exactly one `Connection` in `open()` and holds it until `close()`. There is no
per-request checkout, no `with conn.begin()`, and `commit`/`rollback` act on
that one session. A web application would need one `Conn` per request — which
means one `Engine` per request, i.e. a fresh pool per request, which is the
wrong thing. The pool is real and unused.

**Fix:** separate "the engine/pool" from "a checked-out connection"; `Conn`
should be the latter and something else should own the former.

### F8 — the read connection sits idle-in-transaction

SQLAlchemy 2.0 is commit-as-you-go: the first `SELECT` opens a transaction that
stays open until someone commits or rolls back. `Conn` never does either on its
own, and nothing in its API mentions it. Under READ COMMITTED this is invisible
(each statement gets a fresh snapshot, so Layer A does see Layer B's commits);
under REPEATABLE READ the read connection would go stale and never recover. M5
added `OrderService.refresh_read()` and calls it after each write phase.

**Fix:** an explicit read-transaction lifecycle on `Conn`, or autocommit for
reads.

### F9 — Layer B has no transaction composition in the type system

Every generated Layer B function takes a connection and does one statement.
Nothing says that `insert_order`, `decrement_stock`, `insert_order_item` and
`set_order_total` belong to one atomic unit; the caller has to know, and to
remember to wrap them. M5 encodes it with `OrderService.commands()` and a
docstring on `create_order` reading "MUST be called inside
`OrderService.commands()`". A caller that forgets gets four independent
autocommitted statements and a half-built order.

**Cost:** a convention, and a comment where a type should be.
**Fix:** generate a `Tx`-taking variant, or a `@transactional` decorator that
takes the connection and cannot be called without one. sqlc has the same gap;
jOOQ solves it with `DSLContext.transaction`.

---

## C. Where the two vocabularies disagree

### F10 — enums are a Python enum in Layer A and `str` in Layer B

`orders.status` is `OrderStatus` (a generated `str, enum.Enum`) on the read side
and `str` on the write side. Every command in `app/commands.py` converts
(`new_status.value`), and the scenario's cross-layer check has to compare
`b_side.status == a_side[0].status.value`. Two generators looked at the same
`order_status` type and made different, individually defensible choices — M3's
README documents the deliberation and defers it.

**Cost:** three `.value` conversions and one asymmetric assertion.
**Fix:** one type map, shared. This is the clearest argument for the two
generators having a common front end.

The mirror case is `payments.method`: `text` with
`CHECK (method IN ('card','bank','wallet'))`. Here the two layers *agree* — both
say `str` — because sqlacodegen's CHECK-to-enum detection does not fire on
PostgreSQL (M2 `evidence/g1_dialect_differences.md`). So the constraint the
database enforces is invisible to both generators, and `app/domain.py` restates
it as a hand-written `Literal["card", "bank", "wallet"]`. One column typed
twice by two generators, one column typed by neither: same root cause, opposite
symptom.

### F11 — `ORDERS.status.eq(OrderStatus.PAID)` type-checks and then fails **DEFECT**

The single most important finding of M5. Executing a Layer A predicate on an
enum column raises:

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedFunction)
operator does not exist: order_status = character varying
LINE 3: WHERE orders.status = $1::VARCHAR
```

`_Builder._bind` builds every literal as `bindparam(key, value=_encode(v))` with
no explicit type; `_encode` has already turned the enum member into its `str`,
so SQLAlchemy infers `VARCHAR` and the psycopg dialect renders the cast.
PostgreSQL has no `enum = varchar` operator.

M2 already fixed this **for the SET side of an UPDATE** — `_Builder.bind_typed`
exists for exactly this reason — and its docstring asserts "On the WHERE side
the `=` operator already does this." That is not true when the right operand is
already a `BindParameter`: SQLAlchemy propagates a column's type to a bare
Python value, not to a pre-built bind.

Why M2's evidence missed it: every enum comparison in `proofs/` is type-level
(`reveal_type`), and `patterns/patterns_pg.py`, which *does* execute against
live PostgreSQL, never filters on an enum column — `evidence/run_patterns_pg.log`
shows enum values only in `INSERT` and `UPDATE … SET`. So the gap is a *test*
gap with a one-line code fix:

```python
# altsa_runtime/_backend.py, _Builder.expr, case BinOpNode:
# if lhs is a ColRefNode on an enum column, bind the rhs with
#   b.bind_typed(value, _table(t).c[n].type)
# exactly as update_rows() already does.
```

**M5's workaround** (M2 is read-only): `app/compat.enum_operand()` binds the
enum's value as a `str` *subclass*, which SQLAlchemy's exact-match value→type
map resolves to `NullType`, so the bind renders as a bare `%(p0)s` and
PostgreSQL infers `order_status` from context. One `cast()`, one file, one long
comment. **Cost:** 80 lines and an unsound line in application code to work
around a runtime defect that the type system had already blessed.

### F12 — `list[str | None]` is not assignable from `list[str]`

Layer B types `products.tags` as `list[str | None]` (honest: PostgreSQL cannot
declare array elements NOT NULL) and `list` is invariant, so every caller with a
`list[str]` has to rebuild it. `app.commands.upsert` takes `Sequence[str]` and
does `tags_arg: list[str | None] = [t for t in tags]`.

**Fix:** generate `Sequence[str | None]` for array *parameters* (covariant, so
`list[str]` is assignable) while keeping `list[str | None]` for array *results*.
M3's own README already flags the type as "annoying" with no override.

### F13 — `:one` on an `INSERT … RETURNING` is `| None` forever

`insert_order`, `register_user`, `upsert_product`, `record_payment` and
`decrement_stock` all return `Row | None`. For four of the five, `None` is
unreachable — an `INSERT … RETURNING` that inserts always returns its row. So
`app/commands.py` carries five `if row is None: raise RuntimeError(...)`
branches marked `# pragma: no cover`, four of which are dead.

For `decrement_stock` the `None` is load-bearing and beautiful (`WHERE stock >=
n` matching nothing *is* the out-of-stock signal), which is exactly why the
annotation can't just be dropped.

**Fix:** a `:one!` kind ("exactly one row; raise otherwise"), which is sqlc's
`:one` and sqlx's `fetch_one`. M3's README lists "too many rows is not modelled"
as the known gap; "guaranteed one row" is the other half of it.

### F14 — nothing checks the two generators saw the same schema

`joins.toml` is described against the database at generation time; `queries/*.sql`
is described against the database at generation time. They are two independent
runs and there is no shared fingerprint. Generate Layer A against a migrated
database and Layer B against an older one and the composed application type-checks
perfectly and is wrong.

**Fix:** both generators should stamp a schema hash into their output and the
app should assert they match at import.

---

## D. What Layer A cannot say

### F15 — no HAVING, no window functions, no DISTINCT ON, no subqueries **KNOWN**

"Users with their latest order" — a completely ordinary listing — is
`SELECT DISTINCT ON (u.id) … ORDER BY u.id, o.created_at DESC` in SQL and is
inexpressible in Layer A. `app.queries.latest_order_per_user` therefore fetches
every `(user, order)` pair and folds them in Python: correct, and `O(orders)`
over the wire instead of `O(users)`.

`revenue_by_user` has the mirror problem: no `having()`, so "users who spent more
than X" cannot be pushed to the server.

The `Raw` escape hatch exists, but F16 is why it was not used.

**Cost:** one client-side fold, one filter that cannot be pushed down.
**Fix:** `having()` is a two-hour job (it is `where()` with a different clause).
Window functions and `DISTINCT ON` are a real design problem, because both
introduce result columns that no table owns — which is precisely the shape M3's
Phase 1 could not type either.

### F16 — `Raw` matches its row dataclass positionally **KNOWN**

The documented escape hatch for F15 checks the *row type* but not the SQL, and
binds them by position at runtime. Reaching for it to solve F15 would have
replaced a client-side fold with a silent-wrong-column-order hazard, so M5 did
not. A known limitation that removes the escape hatch from the set of things you
can actually reach for.

### F17 — `Pred` is not table-tagged **KNOWN**

`search_orders(conn, criteria)` builds `list[Pred]` and hands it to a query on
`orders JOIN users`. A `Pred` built from `PRODUCTS.sku` would type-check and
produce SQL referencing an unjoined table — a runtime error, not a type error.
The composable-filter API M5 wanted is exactly the API this limitation is most
visible in.

### F18 — Layer A has no `RETURNING` and no conditional writes **KNOWN**

Every write in this application is on Layer B, and that is not a design choice.
Layer A's typed writers work and type-check, but `insert_orders` cannot give
back the generated `id`, `update_orders` cannot report the row it changed, and
neither can express "…AND stock >= n". An order service needs all three on its
first screen. The "Layer A reads, Layer B writes" split M5 presents is therefore
a rationalisation of a limitation.

### F19 — one schema per process **KNOWN**

Both generated facades register into the single global
`altsa_runtime.catalog.REGISTRY`. M5 has one facade so it does not bite, but any
application with two databases cannot use Layer A for both.

### F20 — the generated facade re-exports everything **KNOWN**

`generated_a/facade.py`'s `__all__` includes `Raw`, `Order`, `coalesce`,
`count_star` and the rest of the runtime surface whether the schema uses them or
not. Convenient for one import; means `from generated_a.facade import *` pulls
in the whole query language, and makes "what does this app actually use" hard
to see.

---

## E. Performance ergonomics

### F21 — Layer A decodes every value twice

`altsa_runtime._backend._decoder_for` converts uuid/numeric/enum on the way out
— but SQLAlchemy's own `Uuid`, `Numeric` and `Enum` types have *already* done
uuid and numeric. The second pass is `UUID(str(v))` and `Decimal(str(v))` over
values that are already `UUID` and `Decimal`. Part of the measured
`Layer A over Core = 1.47x` is this.

The enum decode is genuinely needed (SQLAlchemy returns the label as `str`
because no `enum_class` is registered), so the fix is per-kind, not wholesale:
register the Python enum on the SQLAlchemy `Enum` type and drop the other two
decoders. **Fix:** ~15 lines in `_backend.py`; worth doing before anyone
benchmarks this seriously.

### F22 — the statement memo does not parameterise LIMIT/OFFSET **KNOWN**

`limit(10)` and `limit(20)` are separate memo entries *and* separate SQLAlchemy
compile-cache entries. `search_orders`'s default `limit=50` means one entry per
distinct page size a caller ever asks for. Harmless here, unbounded in a real
API with a caller-supplied page size.

---

## What would have to exist first

In the order I would build it, if the point were for someone else to use this:

1. **F2** — real distributions. Nothing else matters until `uv add altsa-runtime`
   works.
2. **F11** — fix the enum bind, and add an executing test that filters on an
   enum column. It is a one-line fix guarding a defect the type system endorsed.
3. **F6 + F9** — `Conn` over a borrowed connection, and a transaction type both
   layers accept. Together these turn the seam from structural into a choice.
4. **F10 + F14** — one type map and one schema fingerprint shared by the two
   generators. Two vocabularies for one column is the thing that will bite an
   application maintainer first.
5. **F18 + F15's `having()`** — `RETURNING` on Layer A writers, and HAVING. With
   those, the layer split can be a design decision instead of a workaround.
6. **F13** — `:one!`.
7. **F21** — stop decoding twice.
