# alt-SQLAlchemy M4 — `altsa-analyze`

A Rust static analyser on `pg_query.rs` that computes **per-column result
nullability** for a PostgreSQL query from DDL + SQL text alone. No database
connection, ever. This is the "Phase 2" engine that replaces M3's
DESCRIBE + EXPLAIN inference.

```
altsa-analyze --schema schema.sql --query query.sql
```

```json
{
  "columns": [
    { "name": "id",   "nullable": false },
    { "name": "b_id", "nullable": true  },
    { "name": "c_id", "nullable": true  }
  ],
  "params": [],
  "warnings": []
}
```

## Score

| corpus | cases | columns | PASS | safe-FP | UNSOUND | mismatch | error |
|---|---|---|---|---|---|---|---|
| M3 corpus (`../alt-sqlalchemy-m3/corpus/cases`) | 26 | 65 | **65** | **0** | **0** | 0 | 0 |
| corpus-ext (`corpus-ext/cases`) | 11 | 29 | **29** | **0** | **0** | 0 | 0 |

The same corpus, same scoring code, scored against M3's Phase 1 engine: 8
safe-false-positives on the M3 corpus and 12 on corpus-ext. See
`evidence/` and the milestone report.

## Layout

```
Cargo.toml
build-musl.sh            static x86_64-musl build (docker)
src/
  main.rs                CLI, JSON output, integration tests
  catalog.rs             DDL -> {table -> [(column, not_null)]}
  scope.rs               bind_from: the FROM/JOIN walk that stamps outer_nullable
  expr.rs                the expression nullability lattice
  output.rs              projection, star expansion, CTEs, set operations
  funcs.rs               loader for the function table
  functions.toml         the hand-curated function table (embedded)
  params.rs              `${name}` -> `$n`, ported from M3's annotation front-end
corpus-ext/
  build_cases.py         regenerates the fixtures below
  cases/phase2/*         11 new cases: the expression half of nullability
harness/
  m3link.py              puts M3's corpus package on sys.path, read-only
  engine_rust.py         the Rust engine wrapped in M3's Engine protocol
  score.py               runner (reuses corpus.runner.score/report verbatim)
  oracle.py              live-DB oracle (reuses corpus.oracle.observe verbatim)
  determinism_and_perf.py
  *.sh                   build / evidence / probe drivers
evidence/                everything the gates are judged on
```

## Running it

Build:

```sh
cargo build --release          # native
sh build-musl.sh               # static x86_64 musl, in docker
```

Score, with no database at all:

```sh
PYTHONPATH=harness uv run --project harness python harness/score.py --engine rust
PYTHONPATH=harness uv run --project harness python harness/score.py --engine rust \
    --cases corpus-ext/cases
```

Score M3's Phase 1 engine on the same cases (needs a PostgreSQL):

```sh
PYTHONPATH=harness uv run --project harness python harness/score.py \
    --engine altsa_sqlgen-phase1 --url "$DSN" --cases corpus-ext/cases
```

Oracle (needs a PostgreSQL; checks the Rust engine's NOT NULL claims against
real rows):

```sh
PYTHONPATH=harness uv run --project harness python harness/oracle.py --url "$DSN"
```

Everything at once:

```sh
sh harness/collect_evidence.sh "$DSN"
sh harness/run_r6.sh
sh harness/run_ddl_probe.sh
```

### How the harness avoids touching M3

`harness/m3link.py` inserts `../alt-sqlalchemy-m3` into `sys.path` and imports
`corpus.cases`, `corpus.runner`, `corpus.oracle` and `corpus.engines`. The
verdict table, the report format and the oracle's asymmetric property are
therefore *M3's code*, unmodified — a number printed here means exactly what the
same number means in M3's evidence. Only the engine is new, and M3's `Engine`
protocol is structural, so `corpus.runner.run` and `corpus.oracle.observe`
accept it without M3 knowing it exists.

The Python environment is M4's own (`harness/pyproject.toml`, one dependency:
psycopg), not M3's, so no `.venv` or lockfile under M3 is created or updated.

## The algorithm in three sentences

1. **Bind time, not query time.** The FROM/JOIN tree is walked once, carrying an
   `outer_nullable` flag downward: LEFT sets it on the right side, RIGHT on the
   left, FULL on both, and INNER *propagates whatever it received*. Each relation
   is stamped with the flag as it is bound, so resolving a column is
   `catalog_nullable || relation.outer_nullable` — one OR, no re-walk.
2. **Derived tables are not special.** A `RangeSubselect` (which is also what
   LATERAL is) is analysed recursively to get a real column list with real
   nullability, and the incoming flag is applied on top. CTEs and set operations
   go through the same path.
3. **Expressions have their own lattice.** COALESCE is NULL only if every
   argument is; CASE is nullable if any branch is or if there is no ELSE; a cast
   preserves its operand; an aggregate is the nullability of its argument under a
   plain GROUP BY and nullable without one, except COUNT which is never NULL.

## Where it deviates from its sources

The four `DEVIATION` comments in `src/expr.rs` mark corrections to the sqlc
lattice this was ported from:

1. `IN` / `BETWEEN` / `ANY` / `ALL` OR their operands rather than returning a
   non-null boolean (`NULL IN (1,2)` is NULL, not false).
2. `AND` / `OR` / `NOT` likewise (`NULL AND true` is NULL). The design brief
   listed bool connectives as non-null, following sqlc; that is unsound and
   soundness is the harder constraint.
3. `GREATEST` / `LEAST` are nullable only when *every* argument is, matching
   PostgreSQL. sqlc ORs them — safe, but imprecise.
4. A cast preserves its operand's nullability. sqlc's `typeTypeCast` returns the
   target type with `nullable` at its zero value, i.e. false, which is unsound.

Two more, in `src/scope.rs` and `src/output.rs`: INNER joins propagate the
incoming `outer_nullable` instead of resetting it, and a set operation ORs both
branches instead of reading only the left. Both are traced to sqlc source lines
in `evidence/divergence_ledger.md`.

## Limitations

* **No type information.** Names + nullability only. Anything that needs a SQL
  or Python type still needs a catalog or a DESCRIBE.
* **Views and `CREATE TABLE AS` are not modelled** — a query over one fails with
  "relation … is not in the schema"; a warning is emitted at DDL load time.
* **Function scans** (`FROM generate_series(…)`) become an all-nullable relation
  of unknown shape.
* **`NATURAL` / `USING` joins do not de-duplicate merged columns**, so a bare
  `*` over one expands to both copies. Warned. Named columns are still right.
* **No schema qualification**: `public.t` and `t` are the same table.
* **Quoted identifiers are lower-cased**, so `"Foo"` and `foo` collide.
* **Parameters are always nullable in expressions** — the call site is invisible.
  The `${name?}` marker is reported but not used to sharpen the result.
* **`WITH RECURSIVE` reports every column of the CTE nullable**; only the anchor
  term is analysed, for the column list. Warned.
* **`GROUP BY ROLLUP / CUBE / GROUPING SETS` is treated as ungrouped**, because
  super-aggregate rows summarise zero rows of the rolled-up columns. Warned.
* **`VALUES`-derived relations are all-nullable** rather than constant-folded,
  matching the corpus' `"unknown"` expectation for them.

Every one of these is on the *sound* side: the engine says "nullable" where it
cannot prove otherwise, and warns when the imprecision was a choice.
