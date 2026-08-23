"""NEGATIVE evidence: code that MUST NOT type-check, against the GENERATED modules.

Run `pyright --project proofs/pyrightconfig.negative.json`. Every
`# EXPECT-ERROR` line below must produce a pyright error, and every pyright
error must land on such a line -- `check_negative.py` verifies BOTH directions.

Each case is its own function so one failure cannot cascade into the next.
This file is NOT importable-clean by design and is never executed.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import psycopg

from generated.orders import (
    get_order_with_user,
    list_orders_left_join_users,
    revenue_by_user,
)
from generated.overrides import order_stats_for_user, user_email_maybe
from generated.products import insert_payment, upsert_product

# A declaration, not an assignment -- this file is never executed.
_CONN: psycopg.Connection[tuple[object, ...]]


# ===========================================================================
# N1 (claim C1) -- a `str` is not a `UUID`, even though both print the same
# ===========================================================================
def n1_wrong_param_type() -> None:
    get_order_with_user(_CONN, order_id="00000000-0000-0000-0000-000000000000")  # EXPECT-ERROR


# ===========================================================================
# N2 (claim C1) -- numeric(12,2) is Decimal, not float. `0.0` is not a Decimal.
# ===========================================================================
def n2_float_is_not_decimal() -> None:
    list_orders_left_join_users(_CONN, min_total=0.0)  # EXPECT-ERROR


# ===========================================================================
# N3 (claim C3) -- THE INFERENCE BITE. `order_id` is `UUID | None` purely
# because the EXPLAIN pass saw a LEFT JOIN. Using it as a UUID must fail.
# ===========================================================================
def n3_left_join_none_bites() -> None:
    row = list_orders_left_join_users(_CONN, min_total=Decimal("0"))[0]
    u: str = row.order_id.hex  # EXPECT-ERROR
    _ = u


# ===========================================================================
# N4 (claim C2) -- a `:one` result is `Row | None`; attribute access on it
# without a None-check is an error under strict.
# ===========================================================================
def n4_one_result_without_none_check() -> None:
    row = get_order_with_user(_CONN, order_id=uuid4())
    _ = row.total  # EXPECT-ERROR


# ===========================================================================
# N5 -- misspelled Row field
# ===========================================================================
def n5_misspelled_field() -> None:
    row = list_orders_left_join_users(_CONN, min_total=Decimal("0"))[0]
    _ = row.emial  # EXPECT-ERROR


# ===========================================================================
# N6 -- parameters are KEYWORD-ONLY, so positional passing is rejected.
# This is what stops the classic "swapped two same-typed arguments" bug.
# ===========================================================================
def n6_positional_argument() -> None:
    get_order_with_user(_CONN, uuid4())  # EXPECT-ERROR


# ===========================================================================
# N7 -- a required parameter cannot be omitted
# ===========================================================================
def n7_missing_parameter() -> None:
    get_order_with_user(_CONN)  # EXPECT-ERROR


# ===========================================================================
# N8 -- an unknown keyword is not silently ignored
# ===========================================================================
def n8_unknown_parameter() -> None:
    get_order_with_user(_CONN, order_id=uuid4(), tenant_id=1)  # EXPECT-ERROR


# ===========================================================================
# N9 (claim C4) -- the `?` override actually degrades the type. `users.email`
# is NOT NULL in the catalog; `AS "email?"` makes this assignment illegal.
# ===========================================================================
def n9_question_override_bites() -> None:
    e: str = user_email_maybe(_CONN)[0].email  # EXPECT-ERROR
    _ = e


# ===========================================================================
# N10 (claim C5) -- the safe default bites too: `count(*)` degrades to
# `int | None` because Phase 1 cannot attribute it to a base table.
# ===========================================================================
def n10_safe_default_bites() -> None:
    n: int = revenue_by_user(_CONN)[0].order_count  # EXPECT-ERROR
    _ = n


# ===========================================================================
# N11 (claim C2) -- a `:exec` query returns `int`, not a row
# ===========================================================================
def n11_exec_returns_int_not_row() -> None:
    result = insert_payment(
        _CONN, order_id=uuid4(), method="card", amount=Decimal("1"), card_last4=None
    )
    _ = result.id  # EXPECT-ERROR


# ===========================================================================
# N12 -- `:many` returns a list; it is not a single row
# ===========================================================================
def n12_many_is_a_list() -> None:
    _ = list_orders_left_join_users(_CONN, min_total=Decimal("0")).email  # EXPECT-ERROR


# ===========================================================================
# N13 -- Row is frozen: assigning to a field is rejected statically
# ===========================================================================
def n13_row_is_frozen() -> None:
    row = list_orders_left_join_users(_CONN, min_total=Decimal("0"))[0]
    row.email = "x@y.example"  # EXPECT-ERROR


# ===========================================================================
# N14 (claim C1) -- text[] is `list[str | None]`; a bare `str` is not a list
# ===========================================================================
def n14_array_param_type() -> None:
    upsert_product(
        _CONN, sku="s", name="n", price=Decimal("1"), tags="a", stock=1  # EXPECT-ERROR
    )


# ===========================================================================
# N15 (claim C4) -- the `!` override is not a blanket promotion: the aggregate
# WITHOUT a marker is still `Decimal | None`.
# ===========================================================================
def n15_unmarked_aggregate_stays_optional() -> None:
    stats = order_stats_for_user(_CONN, user_id=uuid4())
    if stats is not None:
        d: Decimal = stats.total_spent  # EXPECT-ERROR
        _ = d


# ===========================================================================
# N16 -- the connection parameter is not optional and is not just anything
# ===========================================================================
def n16_connection_type() -> None:
    get_order_with_user("postgresql://", order_id=uuid4())  # EXPECT-ERROR
