# Phase 1 vs Phase 2, column by column

Both columns are produced by M3's `corpus.runner.score` over the same
fixtures; the only thing that differs is the engine.

* **Phase 1** = `altsa_sqlgen-phase1` (M3): libpq DESCRIBE for names and
  base-table attribution, `pg_attribute.attnotnull` for the catalog answer,
  `EXPLAIN (VERBOSE, FORMAT JSON)` walked for outer-join upgrades. Needs a
  live server.
* **Phase 2** = `altsa-analyze` (M4): DDL + parse tree, no server.

## corpus-ext (11 cases, 29 columns)

| case | column | correct | Phase 1 | Phase 2 | delta |
|---|---|---|---|---|---|
| `phase2/aggregate_sum_groupby_notnull_arg` | `grp` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `phase2/aggregate_sum_groupby_notnull_arg` | `total` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/aggregate_sum_groupby_notnull_arg` | `n` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/aggregate_sum_groupby_nullable_arg` | `grp` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `phase2/aggregate_sum_groupby_nullable_arg` | `total` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/aggregate_sum_no_groupby` | `total` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/aggregate_sum_no_groupby` | `n` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/aggregate_sum_no_groupby` | `hi` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/binary_op_mixed` | `a` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/binary_op_mixed` | `b` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/binary_op_mixed` | `c` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/binary_op_mixed` | `d` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/case_no_else` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `phase2/case_no_else` | `v` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/case_with_else` | `v` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/case_with_else` | `w` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/cast_preserves` | `a` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/cast_preserves` | `b` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/cast_preserves` | `c` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/coalesce_chain` | `a` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/coalesce_chain` | `b` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/coalesce_chain` | `c` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/nullif_case` | `raw` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `phase2/nullif_case` | `v` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/scalar_subquery` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `phase2/scalar_subquery` | `note` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/scalar_subquery` | `has_n` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `phase2/union_nullability` | `a` | NULL | NULL (PASS) | NULL (PASS) |  |
| `phase2/union_nullability` | `b` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |

**corpus-ext (11 cases, 29 columns)**: 29 columns. Phase 1 12 safe-FP / 0 unsound; Phase 2 0 safe-FP / 0 unsound. 12 columns move from safe-FP to PASS.

## M3 corpus (26 cases, 65 columns)

| case | column | correct | Phase 1 | Phase 2 | delta |
|---|---|---|---|---|---|
| `own/aggregate_groupby` | `a_id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/aggregate_groupby` | `total` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `own/aggregate_groupby` | `n` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `own/aggregate_no_groupby` | `total` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/aggregate_no_groupby` | `n` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `own/aggregate_no_groupby` | `hi` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/coalesce_not_null` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/coalesce_not_null` | `amount` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `own/coalesce_not_null` | `note` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `own/full_join` | `x_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/full_join` | `x_label` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/full_join` | `y_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/inner_join` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/inner_join` | `label` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/inner_join` | `b_id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/inner_join` | `amount` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/left_join_basic` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/left_join_basic` | `label` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/left_join_basic` | `b_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_basic` | `amount` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_derived_table` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/left_join_derived_table` | `total` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_derived_table` | `n` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_lateral` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/left_join_lateral` | `b_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_lateral` | `amount` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_nested_right` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/left_join_nested_right` | `b_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/left_join_nested_right` | `c_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `own/passthrough_notnull` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/passthrough_notnull` | `label` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `own/passthrough_notnull` | `note` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/bare_expression` | `v` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `sqlx/coalesce_aggregate_notnull` | `total` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `sqlx/cte_materialized_preserves_nullability` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/cte_materialized_preserves_nullability` | `text` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/cte_materialized_preserves_nullability` | `owner_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/cte_preserves_nullability` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/cte_preserves_nullability` | `text` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/cte_preserves_nullability` | `owner_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/full_join_both_nullable` | `id1` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/full_join_both_nullable` | `id2` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/inner_join_preserves_notnull` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/left_join_makes_nullable` | `id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/left_join_outer_half_preserved` | `id1` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/left_join_outer_half_preserved` | `id2` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/nullable_base_column` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/nullable_base_column` | `text` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/nullable_base_column` | `owner_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/order_by_limit_preserves` | `text` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/order_by_limit_preserves` | `owner_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/right_join_inverts` | `id1` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/right_join_inverts` | `id2` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/simple_select_star` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/simple_select_star` | `created_at` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/simple_select_star` | `text` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/simple_select_star` | `owner_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/subquery_preserves_nullability` | `id` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/subquery_preserves_nullability` | `text` | NOT NULL | NOT NULL (PASS) | NOT NULL (PASS) |  |
| `sqlx/subquery_preserves_nullability` | `owner_id` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/ungrouped_aggregate_nullable` | `total` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/union_all_merges_nullability` | `a` | NOT NULL | NULL (safe-FP) | NOT NULL (PASS) | **gained** |
| `sqlx/union_all_merges_nullability` | `b` | NULL | NULL (PASS) | NULL (PASS) |  |
| `sqlx/values_derived_all_nullable` | `id` | unknown | NULL (PASS) | NULL (PASS) |  |
| `sqlx/values_derived_all_nullable` | `name` | unknown | NULL (PASS) | NULL (PASS) |  |

**M3 corpus (26 cases, 65 columns)**: 65 columns. Phase 1 8 safe-FP / 0 unsound; Phase 2 0 safe-FP / 0 unsound. 8 columns move from safe-FP to PASS.

