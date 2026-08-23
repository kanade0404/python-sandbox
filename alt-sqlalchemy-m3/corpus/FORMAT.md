# The nullability corpus -- fixture format

A shared, engine-agnostic test corpus for **result-column nullability**. It
outlives M3: the Phase 1 engine (`altsa_sqlgen`'s catalog + EXPLAIN inference)
is the first thing plugged into it, and the M4 static analyser is meant to be
the second, scored on exactly the same cases.

## Layout

```
corpus/
  FORMAT.md          <- this file
  runner.py          <- runs an engine over every case, scores it
  oracle.py          <- runs the SQL for real, checks the scoring was honest
  cases/
    own/<case>/      <- hand-written for this project
    sqlx/<case>/     <- harvested from the sqlx test suite
```

Every case is a directory:

| file | required | contents |
|------|----------|----------|
| `schema.sql` | yes | DDL. Applied into a freshly emptied `public` schema, so it must be self-contained. |
| `query.sql` | yes | One SQL statement. Parameters are written `${name}`, the same spelling `altsa_sqlgen` uses. No `-- QUERY` header -- the runner supplies one. |
| `expected.json` | yes | The *correct* answer (see below). |
| `seed.sql` | no | `INSERT`s. Only cases with a seed are checked by `oracle.py`. |

## `expected.json`

```json
{
  "columns": [
    {"name": "id",     "nullable": false},
    {"name": "b_id",   "nullable": true},
    {"name": "weird",  "nullable": "unknown"}
  ],
  "provenance": "hand-written for altsa-sqlalchemy M3",
  "notes": "a LEFT JOIN (b JOIN c) -- PostgreSQL replans this as a Right join"
}
```

* `columns` -- in result order. `name` must match what the server returns.
* `nullable` -- **what a perfect analyser would say**, not what any particular
  engine currently says:
  * `false` -- this column can never be NULL for any database state.
  * `true` -- there exists a database state that makes it NULL.
  * `"unknown"` -- genuinely undecidable, or out of scope. Scored like `true`:
    an engine that claims NOT NULL here is wrong.
* `provenance` -- required. For harvested cases, name the upstream file and
  test function.
* `notes` -- free text. Say *why* the expectation is what it is, especially
  when it is counter-intuitive.

Expectations are deliberately **not** a snapshot of engine output. A case where
the engine disagrees is the interesting kind; the runner classifies rather than
fails.

## Scoring (`runner.py`)

Per column, comparing engine output against `expected`:

| expected | engine says | verdict | meaning |
|----------|-------------|---------|---------|
| `false` | not-null | **PASS** | precise |
| `true` / `"unknown"` | nullable | **PASS** | precise |
| `false` | nullable | **SAFE-FALSE-POSITIVE** | over-approximation; a `T \| None` that is never None. Costs ergonomics, never correctness. |
| `true` / `"unknown"` | not-null | **UNSOUND** | the engine promises a value that can actually be NULL. **This is the only failure mode that matters.** |

`runner.py` exits non-zero if any column is UNSOUND. Safe false positives are
reported and counted but never fail the run -- they are the precision budget,
and enumerating them is how the Phase 1 -> Phase 2 delta gets written down.

## Soundness direction

The asymmetry is the whole design. Nullability inference is used to decide
whether a generated field is `T` or `T | None`. Getting it wrong in the
nullable direction produces a `None` check the caller did not need. Getting it
wrong in the other direction produces a type that lies, and the lie surfaces as
an `AttributeError` on `None` at 3am. So: **never trade soundness for
precision**, and when in doubt say nullable.

## The oracle (`oracle.py`)

The runner compares an engine to a hand-written expectation, so a wrong
expectation hides a real bug. `oracle.py` closes that loop with the database
itself: for every case that has a `seed.sql`, it applies the schema and the
seed, runs the query for real, and asserts the **asymmetric** property

> no column the engine called NOT NULL may ever contain NULL in an actual
> result row.

Observing a NULL under a not-null claim is an UNSOUND finding regardless of
what `expected.json` says. The converse is not checked: a column the engine
called nullable is free to contain no NULLs at all in the seeded data --
absence of evidence is not evidence of non-nullability.

## Adding a case

1. `mkdir corpus/cases/own/<name>` and write `schema.sql`, `query.sql`,
   `expected.json`.
2. Work out the expectation *from the semantics*, not by running an engine.
3. Add a `seed.sql` that actually exercises it -- for a nullable column, seed a
   row that makes it NULL.
4. `uv run python -m corpus.runner --url <dsn>` then
   `uv run python -m corpus.oracle --url <dsn>`.
