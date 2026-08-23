# alt-SQLAlchemy M2 -- the code generator

M1 hand-wrote a typed SQL facade "as if generated" and proved 8 claims about it.
M2 writes the generator that actually produces it, and re-runs M1's proofs
against the GENERATED code.

```
                 joins.toml  (which multi-edge shapes exist; named projections)
                      |
  live database  -->  altsa_gen  -->  generated/<dialect>/facade.py
                      |                       |
                      |                       v
        sqlacodegen normalisation        altsa_runtime  (hand-written, stable)
        (fix_column_types, enums)             |
                                              v
                                     SQLAlchemy Core (quarantined in _backend.py)
```

## Reproduce

```sh
docker run -d --name altsa-m2-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=ec -p 55432:5432 postgres:16
docker exec -i altsa-m2-pg psql -U postgres -d ec < ../sqlacodegen-trial/ddl/postgres.sql

uv sync
./verify.sh          # everything below, in order
```

## Gate results

| gate | verdict | evidence |
|------|---------|----------|
| G1 generation works | PASS | `evidence/gen_pg_report.txt`, `evidence/gen_sqlite_report.txt`, `evidence/g1_dialect_differences.md`, `evidence/run_sqlite_smoke.log` |
| G2 type-equivalence with M1 | PASS | `evidence/pyright_positive.txt` (0 errors, 76 reveal_types), `evidence/g2_m1_equivalence.txt` (53/53 shared claims reveal the identical type), `evidence/leakage_check.txt` |
| G3 negatives hold | PASS | `evidence/check_negative.txt` (31/31, two-way) |
| G4 the 8 OLTP patterns | PASS-WITH-CAVEAT | `evidence/g4_comparison.md`, `evidence/run_patterns_pg.log`, `evidence/pyright_patterns.txt` |
| G5 shape strategy | PASS | `proof_positive.c11_declared_shapes`, `proof_negative.n24`/`n25` |
| G6 fidelity | PASS | `evidence/fidelity_pg.txt` (36 columns, 0 problems) |
| G7 determinism | PASS | `evidence/determinism.txt` |

## The runtime / generated split

Decision rule: **schema-INDEPENDENT goes in `altsa_runtime/`, schema-DEPENDENT
is generated.**

| in `altsa_runtime/` (hand-written, 1664 LOC) | generated per schema (1182 LOC for the EC schema) |
|---|---|
| `Expr` / `Pred` / `Col` / `NumCol` and every operator method | one `_XxxCols` + one `_XxxColsN` namespace per table |
| `all_of` / `any_of` / `not_` / aggregates / `coalesce` | one Python enum per database enum |
| the 13 `select()` overloads on `QueryBase` | one `Q...` class per reachable join shape |
| `where` / `group_by` / `order_by` / `limit` / `offset` / `for_update` | one join combinator per (edge, kind) whose result shape exists |
| `Select` / `Projection` / `Raw` result containers | the projection row dataclasses and their `select_*` methods |
| `Conn` and the shape-memoised execution path | `from_<table>()` entry points |
| the SQL IR (`Plan`, `Node`) | `insert_/update_/delete_<table>` typed writers |
| the catalog REGISTRY *machinery* | the catalog registration *calls* |

The one non-obvious call is the **13 `select()` overloads**: they look like the
most "generated-looking" code in M1's facade, but `Select[*Ts]` never mentions a
table -- they are pure arity machinery. Emitting them per shape would have cost
about 140 lines x 31 shapes = ~4,300 lines of generated output for zero extra
type information. They live on `QueryBase` instead, and the generated file is
1182 lines rather than ~5,500.

The mirror-image call is the **`ColsN` nullable variants**, which look
schema-independent (they are "just" `Nullable<Cols>`) but are not: Python has no
mapped types, so the `| None` variant has to be a real class, and its members
are exactly the schema's columns. Kysely recomputes this on every type-check;
here the generator writes it once.

## Generator pipeline

Reused from sqlacodegen (`>=4.0`, PyPI, not forked):

* `TablesGenerator.fix_column_types()` -- CHECK -> synthetic `Enum`,
  `IN (0,1)` -> `Boolean`, native PG ENUM registration, PG sequence detection
* `TablesGenerator.get_adapted_type()` -- dialect types -> generic SQLAlchemy
* `TablesGenerator.enum_classes` / `.enum_values` -- `(table, column)` ->
  Python enum class name, with sqlacodegen's de-duplication
* `_enum_name_to_class_name()`, `should_ignore_table()`

`altsa_gen/frontend.py` subclasses `TablesGenerator` as `AltsaFrontend` and adds
one method, `normalize()`, which runs exactly the first half of
`TablesGenerator.generate()` and then stops. Nothing downstream of that is
sqlacodegen's: none of its `render_*` methods are called (they emit SQLAlchemy
models, which is the thing this project exists not to hand to callers).

Written here:

* `altsa_gen/gir.py` -- the generator's own IR (`GTable`/`GColumn`/`GEdge`/
  `GShape`/`GProjection`), kept separate from the runtime registry
* `altsa_gen/frontend.py` -- type classification, FK -> edge derivation (both
  directions), shape enumeration and naming
* `altsa_gen/config.py` -- `joins.toml`
* `altsa_gen/render.py` -- the renderer
* `altsa_gen/__main__.py` -- the CLI

Invocation is direct (`python -m altsa_gen`), not through sqlacodegen's
`[project.entry-points."sqlacodegen.generators"]`: the entry point contract is
`generate() -> str` for ONE module, and it gives no place to pass `joins.toml`.

## `joins.toml`

```toml
[options]
module     = "facade"      # generated module name
auto_edges = true          # derive both directions of every FK

[[edge]]                   # optional: an edge the FKs do not give you
id = "...", from = "...", to = "...", on = ["a.x = b.y"], method = "..."

[[shape]]                  # multi-edge shapes must be DECLARED
tables = ["orders", "users:left", "order_items:left"]

[[projection]]
name = "OrderSummaryRow", method = "select_order_summary"
shape = ["orders", "users:left"]
columns = [{ field = "order_id", expr = "orders.id" }, ...]
```

Shape entries are `<table>[:<kind>][#<edge-id>]`; `kind` defaults to `inner`.
`#edge-id` is only needed when more than one declared edge reaches the same
target from the tables already in the shape. Prefixes of a declared shape are
added automatically.

For the EC schema: 5 FKs -> 10 edges -> 7 root shapes + 20 single-edge shapes,
plus 4 declared multi-edge shapes = 31.

## LOC

| part | lines |
|------|-------|
| `altsa_runtime/` (hand-written, stable, schema-independent) | 1664 |
| `altsa_gen/` (the generator) | 1280 |
| `joins.toml` (the only hand-written input per schema) | 61 |
| `generated/pg/facade.py` (7 tables, 10 edges, 31 shapes) | 1182 |
| `generated/sqlite/facade.py` | 1185 |
| `proofs/` | 931 |
| `patterns/` | 744 |

For comparison, M1's hand-written `facade.py` covered 3 tables / 2 edges /
9 shapes in 824 lines. The generator produces 31 shapes over 7 tables in 1182,
i.e. ~38 lines per shape instead of ~92 -- the difference is the `select()`
overloads moving to the runtime.

## Known limitations

* **One schema per process.** Both generated facades register into the single
  global `altsa_runtime.catalog.REGISTRY`; importing two facades for two schemas
  in the same process makes the second win. A registry handle per facade module
  is the fix.
* **No table aliases.** A shape cannot contain the same table twice, so
  self-joins are unreachable. The shape key is `(root, [(edge, kind)])` and the
  column namespace is keyed by table name.
* **`Pred` is not table-tagged.** `update_orders(conn, ..., where=USERS.email.eq(x))`
  type-checks. Only the SET side of an UPDATE carries a table tag.
* **No HAVING / subqueries / window functions / DISTINCT / UNION / RETURNING.**
  All of these fall to `Raw`.
* **`Raw` matches its row dataclass POSITIONALLY** against the select list at
  runtime; a wrong column order is a runtime error, not a type error.
* **Shape count is exponential in declared edges.** 27 auto shapes here; a
  schema with 40 FKs would emit 80 single-edge shapes before anything is
  declared. `include_tables`/`exclude_tables` is the only lever today.
* **The statement memo does not parameterise `limit`/`offset`**, so
  `limit(10)` and `limit(20)` are separate cache entries.
* **PostgreSQL CHECK-based enums are not detected** (sqlacodegen frontend
  behaviour; see `evidence/g1_dialect_differences.md`).
* **`insert_*` treats every server-defaulted column as optional**, including
  ones an application should always set explicitly. There is no way to say
  "defaulted in the DB but required here".
* The generated facade re-exports the runtime's public names so applications
  have one import; that means `Raw`, `Order`, `coalesce` etc. appear in the
  generated `__all__` even when the schema does not use them.

## File inventory

```
alt-sqlalchemy-m2/
  pyproject.toml            deps: sqlacodegen>=4.0, sqlalchemy>=2.0,<2.1, psycopg[binary]; dev: pyright
  joins.toml                the PG config (4 shapes, 2 projections)
  joins.sqlite.toml         the same declarations for the SQLite run
  verify.sh                 re-runs every gate
  altsa_runtime/
    __init__.py             public surface + the split rationale
    ir.py                   Plan / Node / JoinStep / OrderStep / Assignment
    expr.py                 Expr, Pred, Col, NumCol, Assign, all_of/any_of, aggregates
    query.py                QueryBase (13 select overloads + clauses), Select, Projection, Raw
    catalog.py              the registry generated code populates
    conn.py                 Conn -- the public connection facade
    sentinel.py             Unset / UNSET for INSERT "omitted" vs "NULL"
    _backend.py             THE ONLY MODULE THAT IMPORTS SQLAlchemy
  altsa_gen/
    __init__.py  __main__.py (CLI)  config.py  gir.py  frontend.py  render.py
  generated/
    pg/facade.py            committed output, byte-identical to a fresh run
    sqlite/facade.py
  proofs/
    proof_positive.py       C1..C11, 76 reveal_types
    proof_negative.py       N1..N31, every line marked # EXPECT-ERROR
    check_negative.py       two-way marker/diagnostic match
    run_checks.py           runtime half of C8 (leakage, no-Any)
    compare_m1_reveals.py   G2: M1's revealed types vs M2's, expression by expression
    pyrightconfig.core.json / .positive.json / .negative.json / .patterns.json
  patterns/
    patterns_pg.py          the 8 OLTP patterns on the generated facade
    run_patterns.py         live PG runner, echoes every statement
    smoke_sqlite.py         the SQLite facade executing
    fidelity_check.py       G6, information_schema as an independent oracle
  evidence/
    gen_pg_report.txt  gen_sqlite_report.txt  g1_dialect_differences.md
    pyright_core.txt  pyright_positive.txt  pyright_negative.json
    check_negative.txt  pyright_patterns.txt  leakage_check.txt
    determinism.txt  fidelity_pg.txt  run_patterns_pg.log  run_sqlite_smoke.log
    g4_comparison.md  ec_sqlite.db
```
