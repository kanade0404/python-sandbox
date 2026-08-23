# Divergence ledger — `altsa-analyze` (M4) vs sqlc

Gate R3. For every case where the two engines disagree: what we answer, what
sqlc answers, what is correct, and the line of sqlc source responsible.

**Method.** sqlc was *not executed* — `which go` finds no Go toolchain on this
machine and the brief forbids installing one, so building it was out of scope.
Every sqlc answer below is a static trace of
`/Users/kanade0404/work/sqlc` at `HEAD 99a7d7d0c`, with file:line citations, and
the two behaviours most at risk of a mis-trace are corroborated against sqlc's
own committed golden files (noted inline). Our answers are the actual output of
`altsa-analyze`, reproduced at the bottom of this file.

## 0. Which sqlc is being compared

sqlc has three paths, and it matters which one a user gets.

| path | how it is reached | source |
|---|---|---|
| **legacy compiler** (the default) | nothing; this is what plain `sqlc generate` runs for PostgreSQL | `internal/compiler/engine.go:55-116`, nullability in `internal/compiler/output_columns.go` |
| **managed-database analyzer** | `database:` + `analyzer: database:` in `sqlc.yaml` | `engine.go:103-111` |
| **core analyzer** (experimental) | `SQLCEXPERIMENT=coreanalyzer sqlc generate` | `internal/cmd/generate.go:257-260`, `internal/opts/experiment.go:8-37` |

The managed-database analyzer does **not** change any answer here:
`internal/compiler/analyze.go:80-89` copies only `DataType` / `IsArray` /
`ArrayDims` back from the database and keeps `NotNull` from the legacy
computation whenever the column counts match. Corroborated by sqlc's own
managed-db golden file
`internal/endtoend/testdata/func_aggregate/pganalyze/go/query.sql.go:17-19`,
where `percentile_disc(...)` comes out as a bare `string`, not `sql.NullString`.

The experimental core analyzer is *worse* on joins, not better:
`internal/core/analyzer/scope.go:54-58` flattens `JoinExpr` into a flat relation
list and `Jointype` is never read anywhere under `internal/core/analyzer/`, so
even a plain `a LEFT JOIN b` comes back all-NOT-NULL. That path is the source of
the expression lattice this milestone ported (`internal/core/analyzer/expr.go`),
not of its join semantics.

**The rows below quote the legacy compiler**, i.e. what a user of `sqlc generate`
actually gets, and note where the other two paths differ.

## 1. The three bug cases (R3)

### `own/left_join_nested_right`

```sql
SELECT a.id, b.id AS b_id, c.id AS c_id
FROM a LEFT JOIN (b JOIN c ON c.b_id = b.id) ON b.a_id = a.id
```

| column | altsa-analyze | sqlc | correct | verdict |
|---|---|---|---|---|
| `id` | NOT NULL | NOT NULL | NOT NULL | both right |
| `b_id` | **nullable** | **NOT NULL** | nullable | **sqlc UNSOUND** |
| `c_id` | **nullable** | **NOT NULL** | nullable | **sqlc UNSOUND** |

**Why sqlc is wrong.** `internal/compiler/output_columns.go:456-457`:

```go
		case ast.JoinTypeInner:
			return helper(tableRequired, tableRequired)
```

`isTableRequired` re-walks the FROM tree once per output column carrying a
`prior` flag, and `prior` is read in exactly one place — the `*ast.RangeVar` arm
at `:436-437`. All four `JoinExpr` arms (`:451`, `:453`, `:455`, `:457`)
hard-code both of `helper`'s arguments and drop `prior` on the floor. The trace
for `b_id`:

1. `:410` `isTableRequired(JoinExpr_outer, col_b, tableRequired)`
2. `:451` LEFT → `helper(tableRequired, tableOptional)`
3. `:441` larg `RangeVar(a)` — name mismatch → `tableNotFound`
4. `:444` rarg `JoinExpr_inner` with **`prior = tableOptional`**
5. `:457` INNER → `helper(tableRequired, tableRequired)` — **`tableOptional` discarded**
6. `:441` `RangeVar(b)` matches → returns `tableRequired`
7. `:412` `col.NotNull = res == tableRequired` → stays `true`

Only one level of join nesting survives: whatever the immediately-enclosing
`JoinExpr` says. This is invisible in a *left-deep* chain, because there the
nested join sits in `Larg` where the hard-coded `tableRequired` happens to be the
right answer — sqlc's own golden file
`internal/endtoend/testdata/join_left/postgresql/go/query.sql.go:301-304, 338-341`
gets a `LEFT JOIN … LEFT JOIN` chain right for exactly that reason. Putting the
inner join in `Rarg`, as parenthesising it does, is what exposes the bug.

**What we do instead.** `src/scope.rs`, `bind_from`:

```rust
JoinType::JoinLeft  => (outer_nullable, true),
JoinType::JoinRight => (true, outer_nullable),
JoinType::JoinFull  => (true, true),
// *** The sqlc fix ***: propagate, do not reset.
JoinType::JoinInner => (outer_nullable, outer_nullable),
```

The flag is carried *down* once at bind time and stamped onto each relation,
rather than reconstructed *upwards* once per column. Nesting therefore composes
by construction: there is nowhere for a `prior` to be dropped, because there is
no `prior` — there is a relation that already knows.

### `own/left_join_derived_table`

```sql
SELECT a.id, s.total, s.n
FROM a
LEFT JOIN (SELECT a_id, sum(amount) AS total, count(*) AS n
           FROM b GROUP BY a_id) s ON s.a_id = a.id
```

| column | altsa-analyze | sqlc | correct | verdict |
|---|---|---|---|---|
| `id` | NOT NULL | NOT NULL | NOT NULL | both right |
| `total` | **nullable** | **NOT NULL** | nullable | **sqlc UNSOUND** |
| `n` | **nullable** | **NOT NULL** | nullable | **sqlc UNSOUND** |

**Why sqlc is wrong** — three independent faults stacked:

1. `output_columns.go:328` — `NotNull: !fun.ReturnTypeNullable` for a `FuncCall`.
   `ReturnTypeNullable` is read from
   `internal/engine/postgresql/dialect/functions.jsonl` (via
   `internal/core/seed/seed.go:277-282`), and
   `grep -c '"nullable":true'` over that file returns **0**. No PostgreSQL
   builtin is annotated nullable, so *every* resolvable function call is
   NOT NULL. `sum` is at `functions.jsonl:2337-2344`, `count` at `:420-421`.
   This alone makes bare `SELECT sum(x) FROM t` (no GROUP BY, empty table → NULL
   in reality) come back NOT NULL.
2. `output_columns.go:406` — `if !col.NotNull || col.Table == nil || col.skipTableRequiredCheck { continue }`.
   The `FuncCall` branch leaves `Table` nil, so aggregate columns never reach the
   join check at all.
3. Latent: even with a non-nil `Table`, `isTableRequired`'s type switch
   (`:429-465`) has arms for `*ast.RangeVar`, `*ast.JoinExpr` and `*ast.List`
   only. **There is no `*ast.RangeSubselect` arm.** A derived table falls
   through to `return tableNotFound` at `:467`, and `:411`'s
   `if res != tableNotFound` treats that as "no change" — a **fail-open**: an
   unrecognised FROM item preserves NOT NULL rather than clearing it.

Demonstrable independently: selecting `s.a_id` instead (which *does* carry
`Table = {Name:"b"}` and so reaches the post-loop) still comes out NOT NULL,
because neither `RangeVar(a)` nor the `RangeSubselect` matches.

**What we do instead.** A derived table is not a special case. `src/scope.rs`
binds `RangeSubselect` by recursing into `analyze_query_node`, getting a real
column list with real per-column nullability, and then stamping the incoming
`outer_nullable` on the resulting relation. The aggregate answer comes from
`src/expr.rs` + `src/functions.toml`: `sum` under a plain `GROUP BY` is the
nullability of its argument (`b.amount` is NOT NULL → NOT NULL *inside* the
subquery), and the LEFT JOIN then makes both columns nullable *outside* it. Two
different mechanisms, composed — which is why the answer is right for the right
reason rather than by cancellation.

### `own/left_join_lateral`

```sql
SELECT a.id, l.id AS b_id, l.amount
FROM a
LEFT JOIN LATERAL (SELECT b.id, b.amount FROM b
                   WHERE b.a_id = a.id
                   ORDER BY b.amount DESC LIMIT 1) l ON true
```

| column | altsa-analyze | sqlc | correct | verdict |
|---|---|---|---|---|
| `id` | NOT NULL | NOT NULL | NOT NULL | both right |
| `b_id` | **nullable** | **NOT NULL** | nullable | **sqlc UNSOUND** |
| `amount` | **nullable** | **NOT NULL** | nullable | **sqlc UNSOUND** |

**Why sqlc is wrong.** `RangeSubselect.Lateral` exists
(`internal/sql/ast/range_subselect.go:6`) and the PostgreSQL converter populates
it (`internal/engine/postgresql/convert.go:2215/2229/2240/2305`), but the field is
read **only** by the AST `Format` methods (`range_subselect.go:19`,
`range_function.go:22`). Nothing in the compiler consults it, so LATERAL is a
plain `RangeSubselect` — and lands in fault 3 above:
`isTableRequired` has no arm for it (`output_columns.go:428-468`), returns
`tableNotFound` at `:467`, and `:411` fails open, leaving the catalog's
NOT NULL in place.

**What we do instead.** LATERAL is also not a special case. It is the same
`RangeSubselect` binding, and the correlation works because `bind_from` appends
to the *current* frame as it goes, so by the time the lateral subquery is
analysed the relations bound to its left are already visible; `resolve_column`
then walks frames innermost-first and finds `a.id` in the enclosing one
(`src/scope.rs`).

## 2. Divergences beyond the three bug cases

These are corpus-ext cases (gate R4) and general lattice deviations. `sqlc*`
marks a claim traced in the newer `internal/core/analyzer/expr.go`, which is
where the lattice was ported from and therefore where a *port* would inherit the
fault if it did not correct it.

| case / construct | altsa-analyze | sqlc | correct | sqlc source |
|---|---|---|---|---|
| `union_nullability` col `a` (`NOT NULL ∪ nullable`) | nullable | **NOT NULL** | nullable | `output_columns.go:120-124` — `if isUnion { return c.outputColumns(qc, n.Larg) }`; `Rarg` is never analysed. Same in the core analyzer: `analyzer.go:210-223` sets `a.columns = left.columns`. |
| `cast_preserves` col `b` (`nullable_col::text`) | nullable | **NOT NULL** | nullable | `internal/core/analyzer/expr.go:728-742` — `typeTypeCast` returns `a.namedType(name)`, whose `nullable` is left at its zero value (false). |
| `aggregate_sum_no_groupby` col `total` | nullable | **NOT NULL** | nullable | `output_columns.go:328` + zero `"nullable"` entries in `functions.jsonl`. |
| `aggregate_sum_groupby_notnull_arg` col `total` | **NOT NULL** | NOT NULL | NOT NULL | agrees, but for the wrong reason: sqlc has no GROUP BY awareness anywhere in the compiler, so it would say NOT NULL for the nullable-argument case too. |
| `aggregate_sum_groupby_nullable_arg` col `total` | nullable | **NOT NULL** | nullable | same line, and this is where the missing GROUP BY reasoning bites. |
| `binary_op_mixed` col `d` (`nullable_col > 0`) | nullable | nullable | nullable | agrees — `expr.go:288` gets binary operators right (`leftT.nullable \|\| rightT.nullable`). |
| `x IN (…)`, `x BETWEEN a AND b`, `x = ANY($1)` | nullable if any operand is | **NOT NULL** | nullable if any operand is | `expr.go:302, 318, 323` — `typeQuantifiedExpr` / `typePredicateList` return `a.boolType(false)`. `NULL IN (1,2)` is NULL, not false. Not exercised by any corpus case; recorded as a lattice deviation. |
| `a AND b`, `NOT a` with a nullable operand | nullable | **NOT NULL** | nullable | `expr.go:607-614` — `typeBoolExpr` returns `a.boolType(false)`. `NULL AND true` is NULL. Also not corpus-exercised. |
| `GREATEST(a, b)` with one nullable arg | **NOT NULL** | nullable | NOT NULL | `expr.go:72` sends `MinMaxExpr` through `typeFirstOf(..., false)`, which ORs. PostgreSQL's GREATEST/LEAST skip NULLs and are NULL only when every argument is. sqlc is *sound* here, just imprecise; we are precise. |
| aggregate with a `FILTER (WHERE …)` clause | nullable | NOT NULL | nullable | `output_columns.go:317-337` ignores `AggFilter`; a filter can empty a group, and an aggregate over zero rows is NULL. Our `src/expr.rs` forces nullable whenever `agg_filter` is present. |
| aggregate used as a window function (`sum(x) OVER (…)`) | nullable (COUNT stays NOT NULL) | NOT NULL | nullable | same line; a window frame can be empty. |
| `GROUP BY ROLLUP/CUBE/GROUPING SETS` | treated as ungrouped → nullable | NOT NULL | nullable | super-aggregate rows summarise zero rows of the rolled-up columns. |

## 3. Score

Over the 26-case / 65-column M3 corpus plus the 11-case / 29-column corpus-ext,
the three R3 bug cases account for **6 columns** where sqlc's legacy compiler is
UNSOUND (`b_id`, `c_id`, `total`, `n`, `b_id`, `amount`) and `altsa-analyze` is
exactly correct. Counting the corpus-ext divergences above adds at least 5 more
UNSOUND columns for sqlc (`union_nullability.a`, `cast_preserves.b`,
`aggregate_sum_no_groupby.total`, `aggregate_sum_no_groupby.hi`,
`aggregate_sum_groupby_nullable_arg.total`).

`altsa-analyze`'s own measured score on both corpora is **0 UNSOUND, 0 MISMATCH,
0 ERROR, 0 safe-false-positive across 94 columns** — see
`scores_rust_m3corpus.txt` and `scores_rust_corpusext.txt`.

## 4. Reproducing our side

```
cargo build --release
./target/release/altsa-analyze \
    --schema ../alt-sqlalchemy-m3/corpus/cases/own/left_join_nested_right/schema.sql \
    --query  ../alt-sqlalchemy-m3/corpus/cases/own/left_join_nested_right/query.sql
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
