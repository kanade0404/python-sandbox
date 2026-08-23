# corpus-ext — the Phase 2 half of the corpus

Same fixture format as M3's corpus (`../../alt-sqlalchemy-m3/corpus/FORMAT.md`),
same scoring, same oracle. What is different is what the cases are *about*.

M3's corpus is dominated by **join shape**: which side of which join a
base-table column sits on. That is the half an EXPLAIN-driven engine can see,
because the plan still attributes those columns to a relation. These 11 cases
are the other half — **expression structure** — where PostgreSQL's wire protocol
reports a computed column with `attrelid = 0` and an engine that reasons from
attribution has nothing to look up and must fall back to "nullable".

| case | what it pins down |
|---|---|
| `aggregate_sum_no_groupby` | an ungrouped aggregate emits one row over zero input rows: SUM/MAX are NULL, COUNT is 0 |
| `aggregate_sum_groupby_notnull_arg` | with a plain GROUP BY every group is non-empty, so SUM of a NOT NULL column is NOT NULL |
| `aggregate_sum_groupby_nullable_arg` | …and is nullable as soon as the argument is |
| `coalesce_chain` | COALESCE is NULL only when *every* argument is |
| `case_no_else` | a CASE with no ELSE is nullable however non-null its branches are |
| `case_with_else` | with an ELSE it is nullable iff some branch is |
| `nullif_case` | NULLIF manufactures a NULL out of two NOT NULL operands |
| `binary_op_mixed` | operators are strict — including comparisons: `NULL > 0` is NULL, not false |
| `union_nullability` | a set operation ORs the branches; the left branch alone is not the answer |
| `scalar_subquery` | a scalar subquery matching no row is NULL; EXISTS never is |
| `cast_preserves` | `NULL::text` is NULL — a cast passes nullability through |

Every case carries a `seed.sql` that makes each nullable column actually produce
a NULL, so `harness/oracle.py` has real evidence to check the NOT NULL claims
against rather than an empty result set.

Regenerate with:

```sh
python3 corpus-ext/build_cases.py
```
