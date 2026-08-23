"""Unit tests for the plan walker and the combine step -- no database needed.

The plan JSON fixtures below are real `EXPLAIN (VERBOSE, FORMAT JSON)` output
from PostgreSQL 16, trimmed to the keys the walker reads.
"""

from __future__ import annotations

from altsa_sqlgen.nullability import (
    ColumnSource,
    catalog_query,
    combine,
    nullables_from_explain,
    nullables_from_explain_sqlx_verbatim,
    resolve,
    top_level_outputs,
)


def plan(node: dict[str, object]) -> list[dict[str, object]]:
    return [{"Plan": node}]


# --------------------------------------------------------------------------
# 1. the join-direction rules
# --------------------------------------------------------------------------

# `SELECT a.id, a.label, b.id, b.amount FROM a LEFT JOIN b ON b.a_id = a.id`
# as PostgreSQL 16 actually plans it: the planner FLIPS the join so the small
# side becomes the hash's build input, and the node comes out as Right.
LEFT_JOIN_PLANNED_AS_RIGHT: dict[str, object] = {
    "Node Type": "Hash Join",
    "Join Type": "Right",
    "Output": ["a.id", "a.label", "b.id", "b.amount"],
    "Plans": [
        {
            "Node Type": "Seq Scan",
            "Parent Relationship": "Outer",
            "Alias": "b",
            "Output": ["b.id", "b.a_id", "b.amount"],
        },
        {
            "Node Type": "Hash",
            "Parent Relationship": "Inner",
            "Output": ["a.id", "a.label"],
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Parent Relationship": "Outer",
                    "Alias": "a",
                    "Output": ["a.id", "a.label"],
                }
            ],
        },
    ],
}

OUTPUTS = ["a.id", "a.label", "b.id", "b.amount"]


def test_right_join_nulls_the_outer_child() -> None:
    got = nullables_from_explain(plan(LEFT_JOIN_PLANNED_AS_RIGHT), OUTPUTS)
    assert got == [None, None, True, True]


def test_sqlx_verbatim_gets_this_backwards() -> None:
    """The control, pinned. This is the deviation, expressed as a test.

    sqlx marks the child whose Parent Relationship is "Inner" -- which under a
    Right join is the PRESERVED side -- so it nulls `a` and leaves `b` alone.
    Both halves are wrong; the `b` half is the unsound one.
    """
    got = nullables_from_explain_sqlx_verbatim(plan(LEFT_JOIN_PLANNED_AS_RIGHT), OUTPUTS)
    assert got == [True, True, None, None]


def test_left_join_nulls_the_inner_child() -> None:
    node: dict[str, object] = {
        "Node Type": "Nested Loop",
        "Join Type": "Left",
        "Output": ["o.id", "u.email"],
        "Plans": [
            {"Parent Relationship": "Outer", "Output": ["o.id", "o.user_id"]},
            {"Parent Relationship": "Inner", "Output": ["u.id", "u.email"]},
        ],
    }
    assert nullables_from_explain(plan(node), ["o.id", "u.email"]) == [None, True]


def test_full_join_nulls_everything() -> None:
    node: dict[str, object] = {
        "Node Type": "Hash Full Join",
        "Join Type": "Full",
        "Output": ["x.id", "y.id"],
        "Plans": [
            {"Parent Relationship": "Outer", "Output": ["x.id"]},
            {"Parent Relationship": "Inner", "Output": ["y.id"]},
        ],
    }
    assert nullables_from_explain(plan(node), ["x.id", "y.id"]) == [True, True]


def test_inner_join_marks_nothing() -> None:
    node: dict[str, object] = {
        "Node Type": "Hash Join",
        "Join Type": "Inner",
        "Output": ["a.id", "b.id"],
        "Plans": [
            {"Parent Relationship": "Outer", "Output": ["a.id"]},
            {"Parent Relationship": "Inner", "Output": ["b.id"]},
        ],
    }
    assert nullables_from_explain(plan(node), ["a.id", "b.id"]) == [None, None]


def test_nullability_is_inherited_through_intermediate_nodes() -> None:
    """A Sort/Materialize between the join and the scan must not lose it."""
    node: dict[str, object] = {
        "Join Type": "Left",
        "Output": ["a.id", "b.amount"],
        "Plans": [
            {"Parent Relationship": "Outer", "Output": ["a.id"]},
            {
                "Node Type": "Materialize",
                "Parent Relationship": "Inner",
                "Output": ["b.other"],
                "Plans": [
                    {
                        "Node Type": "Sort",
                        "Parent Relationship": "Outer",
                        "Output": ["b.amount"],
                    }
                ],
            },
        ],
    }
    assert nullables_from_explain(plan(node), ["a.id", "b.amount"]) == [None, True]


def test_a_join_below_a_join_is_reached() -> None:
    """`a LEFT JOIN (b JOIN c)` -- the whole nullable subtree gets marked."""
    node: dict[str, object] = {
        "Join Type": "Right",
        "Output": ["a.id", "b.id", "c.id"],
        "Plans": [
            {
                "Node Type": "Hash Join",
                "Join Type": "Inner",
                "Parent Relationship": "Outer",
                "Output": ["b.id", "c.id"],
            },
            {"Node Type": "Hash", "Parent Relationship": "Inner", "Output": ["a.id"]},
        ],
    }
    assert nullables_from_explain(plan(node), ["a.id", "b.id", "c.id"]) == [None, True, True]


# --------------------------------------------------------------------------
# 2. degenerate plans -- must learn nothing rather than guess
# --------------------------------------------------------------------------


def test_utility_statement_yields_all_unknown() -> None:
    assert nullables_from_explain(["Utility Statement"], ["x"]) == [None]


def test_object_without_a_plan_yields_all_unknown() -> None:
    assert nullables_from_explain([{"Query Identifier": 1}], ["x", "y"]) == [None, None]


def test_empty_explain_yields_all_unknown() -> None:
    assert nullables_from_explain([], ["x"]) == [None]


def test_unrelated_extra_fields_are_ignored() -> None:
    # sqlx issue #2622: the JSON may carry keys we do not model.
    payload = [{"Plan": {"Node Type": "Result", "Output": ["1"]}, "Query Identifier": 7}]
    assert top_level_outputs(payload) == ("1",)
    assert nullables_from_explain(payload, ["1"]) == [None]


# --------------------------------------------------------------------------
# 3. combining, and the safe default
# --------------------------------------------------------------------------


def test_explain_may_upgrade_to_nullable() -> None:
    assert combine([False, False], [True, None]) == [True, False]


def test_explain_may_not_downgrade() -> None:
    """The walker never returns False, but the combine rule must be `or` anyway."""
    assert combine([True, True], [None, None]) == [True, True]


def test_unknown_degrades_to_nullable() -> None:
    assert resolve(None) is True
    assert resolve(False) is False
    assert resolve(True) is True


# --------------------------------------------------------------------------
# 4. the catalog query
# --------------------------------------------------------------------------


def test_catalog_query_has_one_arm_per_column() -> None:
    sql = catalog_query(
        [ColumnSource(16469, 1), ColumnSource(0, 0), ColumnSource(16445, 2)]
    )
    assert sql.count("UNION ALL") == 2
    assert "NOT attnotnull" in sql
    assert "ORDER BY idx" in sql
    # An expression column keeps its slot so the result still lines up.
    assert "0::oid AS table_id" in sql


def test_expression_columns_are_not_table_columns() -> None:
    assert ColumnSource(0, 0).is_table_column is False
    assert ColumnSource(16469, 1).is_table_column is True
