# alt-SQLAlchemy M3 -- Layer B Phase 1: static queries, inferred nullability

M2 generated a typed *facade* over a reflected schema: you compose queries in
Python and the generator guarantees the types line up. M3 goes the other way.
You write SQL, in a `.sql` file, and the generator asks a live PostgreSQL what
that SQL actually returns -- including, for every result column, whether it can
be NULL. Nothing about nullability is declared; it is inferred, and the whole
milestone is about making that inference **sound**.

```
  queries/*.sql          annotated SQL: -- QUERY <name> :one|:many|:exec
        |                                ${param} markers, AS "col!" / "col?"
        v
  annotations.py         pure parser -- finds ${var} OUTSIDE strings/comments
        |
        v                      +-- pass 1: PREPARE + DESCRIBE (never executes)
  describe.py  <---- live PG --+       ftable/ftablecol -> pg_attribute.attnotnull
        |                      +-- pass 2: EXPLAIN (VERBOSE, FORMAT JSON) EXECUTE
        |                              walk the plan for outer joins
        v
  nullability.py         combine: EXPLAIN may only UPGRADE to nullable
        |                unknown -> nullable (the safe direction)
        v
  emit.py                one module per .sql: frozen dataclass + typed function
        |
        v
  generated/*.py         pyright --strict clean, no Any, no psycopg beyond Connection
```

The inference is a port of sqlx's
(`sqlx-postgres/src/connection/describe.rs`), with **one deliberate soundness
fix** -- see "The sqlx deviation" below, which turned out to be the most
interesting result of the milestone.

## Reproduce

```sh
docker run -d --name altsa-m3-pg -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=altsa -p 55434:5432 postgres:16
docker exec -i altsa-m3-pg psql -U postgres -d altsa \
  < ../sqlacodegen-trial/ddl/postgres.sql
docker exec altsa-m3-pg psql -U postgres -c "CREATE DATABASE corpus;"

uv sync
./verify.sh
```

`verify.sh` runs every gate and tees each artifact into `evidence/`. The corpus
uses a **separate database**, because every case drops and recreates `public`.

## Gate results

| gate | verdict | evidence |
|------|---------|----------|
| H1 annotation parsing | PASS | `evidence/h1_pytest.txt` (57 tests: block splitting, `${var}` extraction incl. all six literal/comment exclusions, `:one/:many/:exec`, marker stripping, rendering) |
| H2 generation + typecheck | PASS | `evidence/h2_generation.txt` (10 queries, 3 modules, 42 columns), `evidence/h2_pyright_core.txt` (0 errors), `evidence/h2_pyright_positive.txt` (0 errors, 34 `reveal_type`s), `evidence/h2_leakage.txt` (0 `Any`, 0 psycopg leaks in 10 public signatures), `evidence/h2_check_negative.txt` (16/16 two-way) |
| H3 corpus soundness | PASS | `evidence/h3_corpus_runner.txt` -- 26 cases, 65 columns, **0 UNSOUND**, 0 mismatch, 8 safe-false-positive. Controls: `evidence/h3_control_sqlx_verbatim.txt` (4 UNSOUND), `evidence/h3_control_always_nullable.txt` (0 UNSOUND, 36 safe-FP) |
| H4 live-DB oracle | PASS | `evidence/h4_oracle.txt` -- 21 seeded cases, 43 result rows, 48 NOT NULL assertions checked against real data, 0 unsound observations. Control: `evidence/h4_oracle_control.txt` (4 real NULLs observed under sqlx's rule) |
| H5 overrides both ways | PASS | `queries/overrides.sql`, `proof_positive.c4_overrides`, `proof_negative.n9`/`n15`, `evidence/h5_bad_override.txt` (unknown column -> clean error, exit 2) |
| H6 determinism | PASS | `evidence/h6_determinism.txt` (two runs byte-identical, and identical to the checked-in tree) |
| H7 end-to-end | PASS | `evidence/h7_e2e.txt` -- 34 runtime checks, 0 failures, incl. upsert, FOR UPDATE (lock actually held), `:exec`, and the LEFT JOIN returning None fields for the order-less user |

## The sqlx deviation -- the headline result

sqlx's `visit_plan` marks a plan node's outputs nullable when

```rust
plan.join_type == Some("Full") || plan.parent_relation == Some("Inner")
```

and recurses only through `Left`/`Right` nodes. The rule assumes the plan's join
direction matches the SQL's. **PostgreSQL 16 does not guarantee that.** The
planner freely flips a `LEFT JOIN` into a `"Join Type": "Right"` node to put the
smaller relation on the hash's build side -- and for a Right join PostgreSQL
preserves the INNER input and null-extends the OUTER one, the exact opposite of
what the rule assumes.

Observed for the plainest possible LEFT JOIN, `corpus/cases/own/left_join_basic`:

```
SELECT a.id, a.label, b.id AS b_id, b.amount FROM a LEFT JOIN b ON b.a_id = a.id

  Hash Join  "Join Type": "Right"     Output: [a.id, a.label, b.id, b.amount]
    -> Seq Scan b   "Parent Relationship": "Outer"    <- the null-extended side
    -> Hash         "Parent Relationship": "Inner"    <- the PRESERVED side
         -> Seq Scan a
```

sqlx's rule marks the `Inner` child, so it reports `a.id`/`a.label` nullable (a
harmless false positive) and leaves `b_id`/`b.amount` NOT NULL -- **unsound**.
Seeded with one `a` row that has no `b`, the query really does return
`(2, 'no-b', NULL, NULL)`, and `evidence/h4_oracle_control.txt` catches it as a
runtime counterexample rather than an argument.

This is not a hypothetical. It fires on the most common join shape there is. It
does not show up in sqlx's own test suite because those tests join against
tiny/empty tables where the planner happens to keep the Left ordering -- which
makes the bug **statistics-dependent**: the same query can be typed soundly on
an empty dev database and unsoundly in production.

The port therefore uses direction-aware rules and inherits nullability down the
whole subtree rather than applying it to one child's `Output`:

| plan node | null-extended side |
|---|---|
| `"Join Type": "Full"` | both children, and the node's own outputs |
| `"Join Type": "Left"` | the child with `"Parent Relationship": "Inner"` |
| `"Join Type": "Right"` | the child with `"Parent Relationship": "Outer"` |
| anything else | nothing (inherited marking still propagates down) |

Both walkers ship. `nullables_from_explain` is what generates code;
`nullables_from_explain_sqlx_verbatim` is a transliteration kept purely so the
corpus can score them side by side and turn the claim into a measurement. The
delta is exactly the 4 UNSOUND columns in
`evidence/h3_control_sqlx_verbatim.txt`, and `tests/test_nullability.py` pins
both behaviours against the real plan JSON.

## Which psycopg describe mechanism

**libpq describe-without-execute, via `Connection.pgconn`:**

```python
pgconn.prepare(name, sql)         # Parse   -- server plans, does not run
res = pgconn.describe_prepared(name)   # Describe(statement)
res.ftype(i) / res.ftable(i) / res.ftablecol(i) / res.param_type(i)
```

`ftable`/`ftablecol` are precisely sqlx's `relation_id` /
`relation_attribute_no`, and `param_type(i)` gives the server's inferred
parameter OIDs.

**Why not the psycopg-level API.** `Cursor.description` exists only *after*
`execute()`, and psycopg's `prepare_threshold` machinery decides on its own when
to Parse. `pgconn.describe_prepared` is the only path that yields
`ftable`/`ftablecol` with a hard guarantee that the statement never ran -- and
that guarantee is the whole point, because `queries/products.sql` contains an
`INSERT ... ON CONFLICT ... RETURNING`.

**EXPLAIN does not execute either**, for DML as well as SELECT -- only EXPLAIN
ANALYZE does, and it is never used. Verified rather than assumed: after a full
generation run that EXPLAINs both an upsert and an `INSERT INTO payments`, both
tables still had 0 rows.

Prepared statements are `DEALLOCATE`d after each describe, so a 26-case corpus
run leaves no session state behind.

## The Phase1 -> Phase2 delta (the M4 motivation)

Phase 1 is sound and imprecise in exactly one way: **it can only reason about
columns the server attributes to a base table.** Any column with `ftable = 0` --
every expression, every aggregate, every set-operation output -- gets UNKNOWN
from pass 1, and pass 2 only ever speaks about outer joins. UNKNOWN degrades to
nullable.

All 8 safe false positives across 26 corpus cases are that one cause:

| case | column | why Phase 1 gives up | what M4 needs |
|---|---|---|---|
| `own/aggregate_no_groupby` | `n` | `count(*)` is an expression | COUNT is never NULL |
| `own/aggregate_groupby` | `total`, `n` | aggregates are expressions | a non-empty group + NOT NULL input => SUM is NOT NULL |
| `own/coalesce_not_null` | `amount`, `note` | `coalesce(...)` is an expression | COALESCE with a non-null final argument is NOT NULL |
| `sqlx/bare_expression` | `v` | `1::int8 + 10` is an expression | constant folding through strict operators |
| `sqlx/coalesce_aggregate_notnull` | `total` | `coalesce(sum(..), 0)` | as above |
| `sqlx/union_all_merges_nullability` | `a` | set-operation output | merge branch nullability |

So the Phase 2 shopping list is short and concrete:

1. **Expression-level nullability.** Strict functions and operators propagate;
   `COALESCE`/`CASE` with a non-null arm terminate; `COUNT` is a constant.
2. **Aggregate reasoning.** Distinguish grouped (>= 1 row per group) from
   ungrouped (may see zero rows).
3. **Set operations.** A `UNION` column is nullable iff some branch's is.

Everything else Phase 1 already gets precisely, including the derived-table,
LATERAL and nested-right join shapes that were expected to degrade -- see the
"expected: some derived-table/CTE shapes" note in the milestone brief.
`own/left_join_derived_table`, `own/left_join_lateral`,
`sqlx/cte_preserves_nullability` and
`sqlx/cte_materialized_preserves_nullability` all resolve exactly. That was a
surprise; the subtree-inheritance change is what buys it.

## Corpus stats

| group | cases | columns | PASS | safe-FP | UNSOUND |
|---|---|---|---|---|---|
| `own` (hand-written) | 10 | 32 | 27 | 5 | 0 |
| `sqlx` (harvested) | 16 | 33 | 30 | 3 | 0 |
| **total** | **26** | **65** | **57** | **8** | **0** |

21 of the 26 carry a `seed.sql` and are additionally checked by the live-DB
oracle. The harvested cases cite their upstream file and test function in
`provenance`; sqlx is dual-licensed MIT OR Apache-2.0, Copyright (C) SQLx
Contributors, portions Copyright (C) LaunchBadge, LLC.

Two controls keep the corpus honest:

* **`always-nullable-control`** must score 0 UNSOUND on every case. If it ever
  does not, the runner or a fixture is wrong, not the engine. (It scores 36
  safe-FP -- the whole precision budget.)
* **`sqlx-verbatim-control`** is the upstream rule, and is what the deviation is
  measured against.

## LOC

| part | lines |
|---|---|
| generator (`altsa_sqlgen/`) | 1659 |
| &nbsp;&nbsp;of which `nullability.py` (the ported algorithm) | 334 |
| &nbsp;&nbsp;of which `annotations.py` (the parser) | 405 |
| corpus infrastructure (`corpus/*.py`) | 683 |
| corpus fixtures | 26 case dirs, 99 files |
| proofs (`proofs/*.py`) | 810 |
| unit tests (`tests/`, 57 tests) | 514 |
| **generated output** (`generated/*.py`, 10 queries) | **545** |
| input SQL (`queries/*.sql`) | 143 |

The generator is ~1.6k lines to sqlx's ~300 for the same algorithm, because
this one also carries the annotation parser, the type map and the emitter --
`nullability.py` alone, the actual port, is 334 lines against `describe.rs`'s
302, and about a third of that is the deviation write-up.

## Known limitations

* **Enums are `str`.** A PostgreSQL `ENUM` maps to `str`, not a generated Python
  enum. Getting the label set is easy; deciding what an *unknown* label should
  do at runtime is a design question, deferred to M4. Noted at
  `typemap.py:python_type`.
* **Array elements are `T | None`.** `text[]` becomes `list[str | None]`, which
  is the honest type -- PostgreSQL has no way to declare an array's elements
  NOT NULL. It is also annoying, and there is no override for it yet.
* **Parameters are non-optional by default.** `${v}` is `T`, `${v?}` is
  `T | None`. Parameter nullability genuinely is not inferable, so it is
  declared. sqlx has the same split.
* **`:one` returns the first row when several match.** It does not raise. The
  `| None` covers "no rows"; "too many rows" is not modelled.
* **No type overrides.** `AS "col!"` and `AS "col?"` are supported; sqlx's
  `AS "col: Type"` is rejected with a clear message rather than ignored.
* **Unmapped SQL types are a generation error**, not an `Any` fallback. That is
  deliberate -- an `Any` fallback would reintroduce the exact hole this
  experiment exists to close -- but it does mean an exotic column type stops the
  build until `typemap.py` learns it.
* **One statement per block.** No multi-statement blocks; PREPARE cannot take
  them anyway.
* **The EXPLAIN pass needs `plan_cache_mode = force_generic_plan`** (PG >= 12).
  On a server without it the pass still runs but may reflect a plan specialised
  on the NULL arguments bound for EXPLAIN. sqlx has the same caveat (PR #3541).

## Surprises and deviations

1. **sqlx's rule is unsound on a plain LEFT JOIN.** Documented above. This was
   meant to be a port; it became a bug report with a fix and a runtime
   counterexample.
2. **psycopg types `execute()` to demand a `LiteralString`.** Dynamically built
   SQL is rejected by pyright -- a genuine injection guard worth keeping. Every
   dynamic statement in this project is therefore passed as **bytes**, which is
   psycopg's escape hatch and makes each such site greppable. There are five,
   all building SQL from integers or from fixture files.
3. **The catalog query inlines its integers instead of binding them.** sqlx
   binds three parameters per column, which is what forces its
   `columns.len() * 3 > 65535` warning. The values come from libpq's own
   `ftable`/`ftablecol` and are `int` by construction, so inlining is both
   injection-free and free of the ceiling. `_assert_int` keeps the invariant
   checked rather than assumed.
4. **Bare `psycopg.Connection` would leak `Any`.** Via PEP 696 it resolves to
   psycopg's `TupleRow` default, which *is* `tuple[Any, ...]`. The generated
   signature uses `psycopg.Connection[tuple[object, ...]]`; because psycopg's
   `Row` TypeVar is covariant an ordinary `psycopg.connect()` result is still
   assignable. The cost is one `cast` per field -- and that turns out to be a
   feature, since the casts are exactly the set of claims the server backs.
5. **`jsonb` has an `Any`-free type.** A recursive `type JsonValue = ...` alias
   is emitted into any module that needs one. pyright handles it under strict.
6. **Derived tables, LATERAL and CTEs did not degrade.** Expected to be Phase 1
   casualties; the subtree-inheritance change handles all of them.

## File inventory

```
alt-sqlalchemy-m3/
  README.md                     this file
  verify.sh                     re-runs every gate into evidence/
  pyproject.toml                own uv workspace root (SETUP GUARD)

  altsa_sqlgen/                 the generator + CLI
    __main__.py                 uv run python -m altsa_sqlgen --url ... --queries ... --out ...
    annotations.py              QUERY blocks, ${var} scanning, col!/col? markers  [pure]
    describe.py                 PREPARE + describe_prepared + EXPLAIN, per query
    nullability.py              THE ALGORITHM: catalog query, both plan walkers, combine  [pure]
    typemap.py                  OID -> Python type; total, no Any fallback
    emit.py                     code rendering; deterministic by construction
    generate.py                 .sql dir -> Python package
    errors.py                   GenerationError

  queries/                      EC-schema input, 10 queries in 3 files
    orders.sql                  JOIN + "email?", LEFT JOIN, aggregate, UPDATE ... RETURNING
    products.sql                upsert, FOR UPDATE, :exec
    overrides.sql               H5: `!` and `?` and the OVERRIDE directive

  generated/                    the output -- checked in, byte-reproducible
    orders.py  products.py  overrides.py  __init__.py

  corpus/                       the shared test infrastructure (outlives M3)
    FORMAT.md                   the fixture format and the scoring rules
    cases.py                    loading, validation, schema application
    engines.py                  the Engine protocol + phase1 + two controls
    runner.py                   scores an engine; exits non-zero on UNSOUND
    oracle.py                   runs the SQL for real; asymmetric soundness check
    cases/own/                  10 hand-written cases
    cases/sqlx/                 16 harvested from the sqlx suite, with provenance

  proofs/
    proof_positive.py           C1..C7, 34 reveal_types + the runtime leakage checks
    proof_negative.py           N1..N16, each marked # EXPECT-ERROR
    check_negative.py           two-way marker check (M1/M2 pattern, verbatim)
    run_checks.py               the runtime half of H2
    e2e.py                      H7: 34 checks against live, seeded PostgreSQL
    bad_queries/                H5 negative fixture
    pyrightconfig.{core,positive,negative}.json

  tests/                        H1 + the pure half of the algorithm, no DB
    test_annotations.py         41 tests (39 defs, 2 parametrised)
    test_nullability.py         16 tests, incl. both walkers pinned

  evidence/                     everything verify.sh produces
    h1_pytest.txt  h2_generation.txt  h2_pyright_core.txt
    h2_pyright_positive.txt  h2_leakage.txt  h2_check_negative.txt
    h2_pyright_negative.json  h3_corpus_runner.txt  h3_corpus.json
    h3_control_sqlx_verbatim.txt  h3_control_always_nullable.txt
    h4_oracle.txt  h4_oracle_control.txt  h5_bad_override.txt
    h6_determinism.txt  h7_e2e.txt  emitted_example.md
```
