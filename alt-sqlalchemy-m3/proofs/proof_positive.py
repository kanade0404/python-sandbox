"""POSITIVE evidence against the GENERATED query modules (M3).

Where M1/M2 proved things about a hand-written and then generated *facade*,
M3's surface is much smaller: one function and one frozen dataclass per query.
The claims are correspondingly sharper -- every one of them is about a type
that came out of a live PostgreSQL 16 DESCRIBE, not out of a declaration
anyone wrote by hand.

Run `pyright --project proofs/pyrightconfig.positive.json`. Expected: 0 errors,
and one `information: Type of ... is "..."` line per `reveal_type` below.

The probes live inside functions that are never called, so pyright evaluates
them statically while the module stays importable (and cheap) at runtime --
`assert_no_leakage()` is meant to actually run.
"""

from __future__ import annotations

import datetime as _dt
import sys
from decimal import Decimal
from typing import reveal_type
from uuid import uuid4

import psycopg

from generated.orders import (
    GetOrderWithUserRow,
    ListOrdersLeftJoinUsersRow,
    RevenueByUserRow,
    get_order_with_user,
    list_orders_left_join_users,
    revenue_by_user,
    transition_order_status,
)
from generated.overrides import order_stats_for_user, user_email_maybe
from generated.products import insert_payment, lock_products_for_update, upsert_product

# A DECLARATION, not an assignment. pyright gives it the type; at runtime the
# name never exists, which is exactly right -- the probe functions below are
# never called, and importing this module must not open a connection (the
# runtime halves at the bottom DO get called, by proofs/run_checks.py).
_CONN: psycopg.Connection[tuple[object, ...]]


# ===========================================================================
# C1 -- parameter types come from the server's inferred parameter OIDs
# ===========================================================================


def c1_param_types() -> None:
    # `WHERE o.id = ${order_id}` against `orders.id uuid` -> UUID, keyword-only.
    reveal_type(get_order_with_user)  # (conn, *, order_id: UUID) -> ... | None

    # numeric(12,2) -> Decimal, not float. Nothing declared this; PostgreSQL
    # inferred $1 as numeric from the comparison and the OID mapped to Decimal.
    reveal_type(list_orders_left_join_users)  # (conn, *, min_total: Decimal) -> list[...]

    # `${card_last4?}` -- the `?` makes just that one parameter optional.
    reveal_type(insert_payment)  # (..., card_last4: str | None) -> int

    # text[] -> list[str | None]: PostgreSQL arrays can always hold NULL
    # elements, so the honest element type is optional.
    reveal_type(upsert_product)  # (..., tags: list[str | None], ...) -> ... | None


# ===========================================================================
# C2 -- `:one` vs `:many` vs `:exec` produce three different return types
# ===========================================================================


def c2_result_kinds() -> None:
    reveal_type(get_order_with_user(_CONN, order_id=uuid4()))  # GetOrderWithUserRow | None
    reveal_type(
        list_orders_left_join_users(_CONN, min_total=Decimal("0"))
    )  # list[ListOrdersLeftJoinUsersRow]
    reveal_type(
        insert_payment(
            _CONN,
            order_id=uuid4(),
            method="card",
            amount=Decimal("1"),
            card_last4=None,
        )
    )  # int


# ===========================================================================
# C3 -- THE INFERENCE CLAIM. A LEFT JOIN's null-extended side is `| None`,
# and the preserved side is NOT -- with nothing declared either way.
# ===========================================================================


def c3_left_join_nullability() -> None:
    rows = list_orders_left_join_users(_CONN, min_total=Decimal("0"))
    row = rows[0]

    # users is the PRESERVED side of `users LEFT JOIN orders`. Both columns are
    # NOT NULL in pg_attribute and the EXPLAIN pass leaves them alone.
    reveal_type(row.user_id)  # UUID
    reveal_type(row.email)  # str
    reveal_type(row.user_status)  # str

    # orders is the NULL-EXTENDED side. Every one of these is NOT NULL in
    # pg_attribute -- `orders.id` is the primary key -- and the EXPLAIN pass
    # upgraded all four. This is the entire point of pass 2.
    reveal_type(row.order_id)  # UUID | None
    reveal_type(row.total)  # Decimal | None
    reveal_type(row.order_status)  # str | None
    reveal_type(row.created_at)  # datetime | None

    # ... and the `| None` is load-bearing downstream: see proof_negative N3.
    if row.total is not None:
        reveal_type(row.total)  # Decimal


# ===========================================================================
# C4 -- overrides beat inference, in both directions (H5)
# ===========================================================================


def c4_overrides() -> None:
    # `?` DOWNGRADES a provably-NOT NULL column. `users.email` is NOT NULL and
    # unjoined; inference gets that right, and `AS "email?"` overrides it.
    emails = user_email_maybe(_CONN)
    reveal_type(emails[0].id)  # UUID
    reveal_type(emails[0].email)  # str | None

    # The same column WITHOUT the marker, in a different query, stays `str` --
    # so the `| None` above is the override talking, not a blanket fallback.
    order = get_order_with_user(_CONN, order_id=uuid4())
    if order is not None:
        reveal_type(order.user_id)  # UUID
        reveal_type(order.status)  # str
        # this one DOES carry `AS "email?"`
        reveal_type(order.email)  # str | None

    # `!` UPGRADES an expression column that inference had to give up on.
    # `count(*)` has no base-table attribution, so pass 1 says UNKNOWN and the
    # safe default is nullable; `AS "order_count!"` asserts what is true.
    stats = order_stats_for_user(_CONN, user_id=uuid4())
    if stats is not None:
        reveal_type(stats.order_count)  # int
        # ... while the un-marked aggregate keeps the honest safe default:
        # SUM over no rows really is NULL.
        reveal_type(stats.total_spent)  # Decimal | None
        reveal_type(stats.last_order_at)  # datetime | None


# ===========================================================================
# C5 -- the Phase 1 safe default, stated positively
# ===========================================================================


def c5_safe_default() -> None:
    rows = revenue_by_user(_CONN)
    # `u.email` is a plain column on the preserved side -> precise.
    reveal_type(rows[0].email)  # str
    # `sum(o.total)` genuinely can be NULL -> correct, and for the right reason.
    reveal_type(rows[0].revenue)  # Decimal | None
    # `count(*)` can NOT be NULL, but Phase 1 cannot prove it, so it degrades
    # to nullable. A SAFE FALSE POSITIVE: costs an `is not None`, never
    # correctness. This is the M4 delta, visible in the type system.
    reveal_type(rows[0].order_count)  # int | None


# ===========================================================================
# C6 -- rows are frozen, slotted dataclasses with concrete field types
# ===========================================================================


def c6_row_shape() -> None:
    row = GetOrderWithUserRow(
        id=uuid4(),
        status="paid",
        total=Decimal("10.00"),
        version=1,
        created_at=_dt.datetime.now(_dt.UTC),
        user_id=uuid4(),
        email=None,
        metadata=None,
    )
    reveal_type(row)  # GetOrderWithUserRow
    reveal_type(row.metadata)  # JsonValue | None  -- Any-free recursive alias

    # `list[Row]` is exactly that, with no widening
    rows: list[ListOrdersLeftJoinUsersRow] = list_orders_left_join_users(
        _CONN, min_total=Decimal("0")
    )
    reveal_type(rows)  # list[ListOrdersLeftJoinUsersRow]
    revenue: list[RevenueByUserRow] = revenue_by_user(_CONN)
    reveal_type(revenue)  # list[RevenueByUserRow]


# ===========================================================================
# C7 -- writers: RETURNING is typed like any other result
# ===========================================================================


def c7_writers() -> None:
    product = upsert_product(
        _CONN, sku="SKU-1", name="Widget", price=Decimal("9.99"), tags=["a"], stock=3
    )
    if product is not None:
        reveal_type(product.id)  # int         -- identity column, NOT NULL
        reveal_type(product.tags)  # list[str | None] | None  -- nullable column

    # UPDATE ... RETURNING under `:one`: None means the optimistic-lock check
    # lost, which is a value the type system makes you handle.
    reveal_type(
        transition_order_status(
            _CONN, new_status="paid", order_id=uuid4(), expected_version=1
        )
    )  # TransitionOrderStatusRow | None

    reveal_type(lock_products_for_update(_CONN, skus=["SKU-1"]))  # list[...]


# ---------------------------------------------------------------------------
# Runtime half: the generated surface must not leak psycopg or Any.
# Called from proofs/run_checks.py so it actually executes.
# ---------------------------------------------------------------------------

GENERATED_MODULES = ["generated.orders", "generated.products", "generated.overrides"]


def assert_no_leakage() -> list[str]:
    """Fail if a generated public signature mentions `Any`, or any psycopg type
    other than `Connection`."""
    import importlib
    import inspect

    findings: list[str] = []
    checked = 0
    for modname in GENERATED_MODULES:
        mod = importlib.import_module(modname)
        for attr, value in vars(mod).items():
            if attr.startswith("_") or not inspect.isfunction(value):
                continue
            if value.__module__ != modname:
                continue
            checked += 1
            sig = str(inspect.signature(value))
            if "Any" in sig:
                findings.append(f"{modname}.{attr}: Any in {sig}")
            for token in ("Cursor", "AsyncConnection", "psycopg.rows", "TupleRow"):
                if token in sig:
                    findings.append(f"{modname}.{attr}: psycopg leak {token} in {sig}")

    print(
        f"[leakage] {checked} generated public function(s) across "
        f"{len(GENERATED_MODULES)} module(s): {len(findings)} finding(s) (want 0)",
        file=sys.stderr,
    )
    for f in findings:
        print(f"[leakage] LEAK {f}", file=sys.stderr)
    return findings


def assert_no_any_in_source() -> list[str]:
    """Belt and braces: the generated TEXT must not contain the token `Any`.

    `inspect.signature` renders `from __future__ import annotations` strings
    back out, so the check above is real -- but grepping the source catches an
    `Any` hiding in a dataclass field annotation, which has no signature.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "generated"
    findings: list[str] = []
    for path in sorted(root.glob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\bAny\b", line):
                findings.append(f"{path.name}:{n}: {line.strip()}")
    print(
        f"[any-check] {len(findings)} occurrence(s) of `Any` in generated source "
        f"(want 0)",
        file=sys.stderr,
    )
    for f in findings:
        print(f"[any-check] ANY {f}", file=sys.stderr)
    return findings
