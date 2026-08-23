"""NEGATIVE evidence: code that MUST NOT type-check, against the GENERATED facade.

M1's proof_negative.py, re-pointed at `generated.pg.facade` and extended with
the cases M2 introduces (undeclared join shapes, table-tagged UPDATE
assignments, INSERT completeness).

Run `pyright --project proofs/pyrightconfig.negative.json`. Every
`# EXPECT-ERROR` line below must produce a pyright error, and every pyright
error must land on such a line -- `check_negative.py` verifies BOTH directions.

Each case is its own function so one failure cannot cascade into the next.
This file is NOT importable-clean by design and is never executed.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from generated.pg.facade import (
    ORDER_ITEMS,
    ORDERS,
    USERS,
    Col,
    Conn,
    OrderStatus,
    Pred,
    from_orders,
    from_users,
    insert_orders,
    sum_,
    update_orders,
)

_CONN = Conn("sqlite://")


# ===========================================================================
# N1 (claim C1) -- a raw string is not the generated enum
# ===========================================================================
def n1_wrong_literal_type() -> None:
    ORDERS.status.eq("paid")  # EXPECT-ERROR


# ===========================================================================
# N2 (claim C1) -- column vs. column with MISMATCHED value types
# ===========================================================================
def n2_column_vs_column_mismatch() -> None:
    ORDERS.total.eq(ORDERS.status)  # EXPECT-ERROR


# ===========================================================================
# N3 (claim C1) -- a numerically-plausible but wrongly-typed literal
# ===========================================================================
def n3_wrong_scalar_type() -> None:
    ORDERS.version.eq("3")  # EXPECT-ERROR


# ===========================================================================
# N4 (claim C3) -- `.users` does not exist before the join
# ===========================================================================
def n4_users_before_join() -> None:
    q = from_orders()
    q.users.email  # EXPECT-ERROR


# ===========================================================================
# N5 (claim C3) -- the LEFT JOIN's `| None` actually bites downstream
# ===========================================================================
def n5_left_join_none_bites() -> None:
    q = from_orders().left_join_users()
    rows = q.select(ORDERS.id, q.users.email).fetch(_CONN)
    rows[0][1].upper()  # EXPECT-ERROR


# ===========================================================================
# N6 (claim C8) -- tuple index out of range on a fetched row
# ===========================================================================
def n6_row_index_out_of_range() -> None:
    q = from_orders().left_join_users()
    rows = q.select(ORDERS.id, q.users.email).fetch(_CONN)
    rows[0][2]  # EXPECT-ERROR


# ===========================================================================
# N7 -- misspelled column attribute
# ===========================================================================
def n7_misspelled_column() -> None:
    ORDERS.totl  # EXPECT-ERROR


# ===========================================================================
# N8 -- a Pred is not an Expr; it cannot be projected
# ===========================================================================
def n8_pred_where_col_expected() -> None:
    p: Pred = ORDERS.status.eq(OrderStatus.PAID)
    from_orders().select(p)  # EXPECT-ERROR


# ===========================================================================
# N9 -- a Pred is not a valid operand either
# ===========================================================================
def n9_pred_as_operand() -> None:
    p: Pred = ORDERS.status.eq(OrderStatus.PAID)
    ORDERS.total.eq(p)  # EXPECT-ERROR


# ===========================================================================
# N10 (claim C2) -- there is no dunder, so `3 * col` is not an expression
# ===========================================================================
def n10_no_reflected_operator() -> None:
    3 * ORDERS.total  # EXPECT-ERROR


# ===========================================================================
# N11 (claim C2) -- arithmetic is not offered on non-numeric columns
# ===========================================================================
def n11_arithmetic_on_text() -> None:
    q = from_orders().join_users()
    q.users.email.mul(2)  # EXPECT-ERROR


# ===========================================================================
# N12 (claim C4) -- 14 columns exceeds the 13 emitted overloads.
# DECISION: the cap is a hard error, NOT an `Any` fallback rung. Silently
# degrading to Any at arity 14 would reintroduce exactly the hole this design
# exists to close. Raising the cap is a generation parameter, not a redesign.
# ===========================================================================
def n12_overload_cap() -> None:
    q = from_orders().left_join_users().left_join_order_items()
    q.select(  # EXPECT-ERROR
        ORDERS.id,
        ORDERS.user_id,
        ORDERS.status,
        ORDERS.total,
        ORDERS.version,
        ORDERS.created_at,
        q.users.id,
        q.users.email,
        q.users.status,
        q.users.created_at,
        q.order_items.order_id,
        q.order_items.product_id,
        q.order_items.quantity,
        q.order_items.unit_price,
    )


# ===========================================================================
# N13 (claim C7) -- not_null() is unavailable on an already-non-null column
# ===========================================================================
def n13_not_null_on_non_null() -> None:
    ORDERS.total.not_null()  # EXPECT-ERROR


# ===========================================================================
# N14 (claim C3) -- an UNDECLARED join has no combinator at all.
# There is no FK between orders and products, so no edge was derived.
# ===========================================================================
def n14_undeclared_join() -> None:
    from_orders().left_join_products()  # EXPECT-ERROR


# ===========================================================================
# N15 (claim C3) -- an inner join does NOT give you the nullable namespace,
# so code written against `| None` is rejected rather than silently accepted
# ===========================================================================
def n15_inner_join_is_not_nullable() -> None:
    q = from_orders().join_users()
    c: Col[str, str | None] = q.users.email  # EXPECT-ERROR
    _ = c


# ===========================================================================
# N16 (claim C5) -- the declared projection's row type is enforced
# ===========================================================================
def n16_projection_row_field_type() -> None:
    q = from_orders().left_join_users()
    row = q.select_order_summary().fetch(_CONN)[0]
    e: str = row.user_email  # EXPECT-ERROR
    _ = e


# ===========================================================================
# N17 (claim C5) -- a projection is emitted only for the shape it was
# declared against; the inner-join shape does not have it
# ===========================================================================
def n17_projection_wrong_shape() -> None:
    from_orders().join_users().select_order_summary()  # EXPECT-ERROR


# ===========================================================================
# N18 (claim C1) -- `in_` constrains the element type of the sequence
# ===========================================================================
def n18_in_wrong_element_type() -> None:
    ORDERS.status.in_(["paid", "shipped"])  # EXPECT-ERROR


# ===========================================================================
# N19 (claim C8) -- the row tuple's element types are enforced positionally
# ===========================================================================
def n19_row_position_type() -> None:
    q = from_orders().join_users()
    rows = q.select(ORDERS.total, q.users.email).fetch(_CONN)
    d: Decimal = rows[0][1]  # EXPECT-ERROR
    _ = d


# ===========================================================================
# N20 (claim C1) -- cross-table comparison still checks the VALUE type
# ===========================================================================
def n20_cross_table_mismatch() -> None:
    ORDERS.id.eq(ORDER_ITEMS.quantity)  # EXPECT-ERROR


# ===========================================================================
# N21 -- `where()` takes Pred, not a bare bool
# ===========================================================================
def n21_where_takes_pred() -> None:
    from_orders().where(True)  # EXPECT-ERROR


# ===========================================================================
# N22 (claim C7) -- after not_null(), None is no longer an acceptable operand
# ===========================================================================
def n22_not_null_narrows_operand() -> None:
    q = from_orders().left_join_users()
    u: UUID | None = None
    q.users.id.not_null().eq(u)  # EXPECT-ERROR


# ===========================================================================
# N23 (claim C3) -- alias visibility is per-shape, not global. `orders` is not
# reachable from a users-rooted query that has not joined it.
# ===========================================================================
def n23_alias_not_in_shape() -> None:
    from_users().orders.total  # EXPECT-ERROR


# ===========================================================================
# N24 (G5) -- THE SHAPE-STRATEGY LINE. joins.toml declares
#   orders + users:left  + order_items:left
#   orders + users:inner + order_items:left
# but NOT orders + users:inner + order_items:inner. The combinator therefore
# does not exist on QOrdersUsersI; an undeclared shape is an attribute error,
# not a runtime surprise.
# ===========================================================================
def n24_undeclared_shape_combination() -> None:
    from_orders().join_users().join_order_items()  # EXPECT-ERROR


# ===========================================================================
# N25 (G5) -- likewise for a 2-edge shape whose PREFIX exists but whose
# extension was never declared: users LEFT JOIN orders JOIN order_items.
# ===========================================================================
def n25_undeclared_shape_prefix_only() -> None:
    from_users().left_join_orders().join_order_items()  # EXPECT-ERROR


# ===========================================================================
# N26 (C10) -- UPDATE assignments are TABLE-TAGGED
# ===========================================================================
def n26_assignment_wrong_table() -> None:
    update_orders(_CONN, USERS.email.set("x@y.example"))  # EXPECT-ERROR


# ===========================================================================
# N27 (C10) -- INSERT completeness: `user_id` is NOT NULL with no server
# default, so omitting it is a type error, not a runtime IntegrityError
# ===========================================================================
def n27_insert_missing_required() -> None:
    insert_orders(_CONN)  # EXPECT-ERROR


# ===========================================================================
# N28 (C10) -- INSERT operand types are checked too
# ===========================================================================
def n28_insert_wrong_type() -> None:
    insert_orders(_CONN, user_id="00000000-0000-0000-0000-000000000000")  # EXPECT-ERROR


# ===========================================================================
# N29 (C10) -- a column that does not exist is not an INSERT keyword
# ===========================================================================
def n29_insert_unknown_column() -> None:
    insert_orders(_CONN, user_id=uuid4(), nope=1)  # EXPECT-ERROR


# ===========================================================================
# N30 (C9) -- SUM is nullable and the type says so
# ===========================================================================
def n30_aggregate_is_nullable() -> None:
    q = from_users().join_orders().join_order_items().group_by(USERS.email)
    rows = q.select(USERS.email, sum_(ORDER_ITEMS.unit_price)).fetch(_CONN)
    d: Decimal = rows[0][1]  # EXPECT-ERROR
    _ = d


# ===========================================================================
# N31 (C9) -- order_by takes an Order term, not a bare column
# ===========================================================================
def n31_order_by_takes_order() -> None:
    from_orders().order_by(ORDERS.created_at)  # EXPECT-ERROR
