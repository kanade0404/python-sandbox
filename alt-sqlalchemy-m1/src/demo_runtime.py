"""RUNTIME demo: the facade is not type fiction.

Executes against in-memory SQLite through the private SQLAlchemy Core backend
and echoes the emitted SQL. Also runs the two leakage assertions from
`proof_positive` so the "no SQLAlchemy in public modules" claim is checked by
execution, not just by inspection.

    uv run python src/demo_runtime.py
"""

from __future__ import annotations

import datetime as _dt
import sys
from decimal import Decimal
from uuid import UUID

from facade import (
    ORDERS,
    USERS,
    Conn,
    OrderStatus,
    UserStatus,
    all_of,
    any_of,
    from_orders,
    from_users,
)
from proof_positive import (
    OrderFilter,
    assert_no_any_in_public_signatures,
    assert_no_sqlalchemy_leakage,
    search_orders,
)

U_ALICE = UUID("11111111-1111-1111-1111-111111111111")
U_BOB = UUID("22222222-2222-2222-2222-222222222222")
U_CAROL = UUID("33333333-3333-3333-3333-333333333333")  # deliberately order-less

O_1 = UUID("aaaaaaaa-0000-0000-0000-000000000001")
O_2 = UUID("aaaaaaaa-0000-0000-0000-000000000002")
O_3 = UUID("aaaaaaaa-0000-0000-0000-000000000003")

P_X = UUID("bbbbbbbb-0000-0000-0000-00000000000x".replace("x", "1"))
P_Y = UUID("bbbbbbbb-0000-0000-0000-00000000000x".replace("x", "2"))

TS = _dt.datetime(2026, 8, 1, 12, 0, 0)


def _seed(conn: Conn) -> None:
    conn.create_all()
    for uid, email, status in [
        (U_ALICE, "alice@example.com", UserStatus.ACTIVE),
        (U_BOB, "bob@example.com", UserStatus.ACTIVE),
        (U_CAROL, "carol@example.com", UserStatus.SUSPENDED),
    ]:
        conn.insert_row(
            "users",
            {"id": uid, "email": email, "status": status, "created_at": TS},
        )
    for oid, uid, ostatus, total, version in [
        (O_1, U_ALICE, OrderStatus.PAID, Decimal("120.50"), 1),
        (O_2, U_ALICE, OrderStatus.PENDING, Decimal("30.00"), 1),
        (O_3, U_BOB, OrderStatus.PAID, Decimal("99.99"), 2),
    ]:
        conn.insert_row(
            "orders",
            {
                "id": oid,
                "user_id": uid,
                "status": ostatus,
                "total": total,
                "version": version,
                "created_at": TS,
            },
        )
    for oid, pid, qty, price in [
        (O_1, P_X, 2, Decimal("60.25")),
        (O_3, P_Y, 1, Decimal("99.99")),
    ]:
        conn.insert_row(
            "order_items",
            {
                "order_id": oid,
                "product_id": pid,
                "quantity": qty,
                "unit_price": price,
            },
        )


def _tname(v: object) -> str:
    """Runtime class name, reached through an `object` parameter.

    Taking `object` deliberately erases the static type: pyright rejects a
    direct `isinstance(row[1], Decimal)` as *unnecessary* (it already knows the
    element is a Decimal), but that confidence is exactly what the runtime is
    supposed to corroborate. Going through `object` keeps the check real.
    """
    return type(v).__name__


def _hr(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def main() -> int:
    sql_log: list[str] = []
    failures: list[str] = []

    with Conn("sqlite://", echo_to=sql_log) as conn:
        _seed(conn)

        # ------------------------------------------------------------------
        # Q1 -- LEFT JOIN whose nullability the type system predicted.
        # Carol has no orders, so her row must come back with NULLs on the
        # orders side. The static type of position 1 is `UUID | None` and the
        # runtime delivers exactly that.
        # ------------------------------------------------------------------
        _hr("Q1  users LEFT JOIN orders  (C3: nullability is real at runtime)")
        q1 = from_users().left_join_orders()
        sel1 = q1.select(USERS.email, q1.orders.id, q1.orders.total)
        print("SQL:", sel1.sql(conn))
        rows1 = sel1.fetch(conn)
        for r in rows1:
            print("   ", r)
        orderless = [r for r in rows1 if r[1] is None]
        print(f"    -> {len(orderless)} row(s) with NULL on the orders side")
        if not orderless:
            failures.append("Q1: expected at least one NULL orders row")
        if orderless and orderless[0][0] != "carol@example.com":
            failures.append("Q1: the order-less user should be carol")
        # position 1 is typed `UUID | None`; narrowing is required to use it
        for r in rows1:
            oid = r[1]
            if oid is not None and _tname(oid) != "UUID":
                failures.append(f"Q1: expected UUID, got {_tname(oid)}")

        # ------------------------------------------------------------------
        # Q2 -- the operand-checked WHERE. `eq(OrderStatus.PAID)` is the call
        # that `eq("paid")` cannot be (proof_negative N1).
        # ------------------------------------------------------------------
        _hr("Q2  orders WHERE status = PAID  (C1: operand-checked predicate)")
        sel2 = (
            from_orders()
            .where(ORDERS.status.eq(OrderStatus.PAID))
            .select(ORDERS.id, ORDERS.status, ORDERS.total)
        )
        print("SQL:", sel2.sql(conn))
        rows2 = sel2.fetch(conn)
        for r in rows2:
            print("   ", r)
        if len(rows2) != 2:
            failures.append(f"Q2: expected 2 PAID orders, got {len(rows2)}")
        if any(r[1] is not OrderStatus.PAID for r in rows2):
            failures.append("Q2: a non-PAID row came back")

        # ------------------------------------------------------------------
        # Q3 -- the declared dataclass projection.
        # ------------------------------------------------------------------
        _hr("Q3  OrderSummaryRow projection  (C5: named row type)")
        proj = from_orders().left_join_users().select_order_summary()
        print("SQL:", proj.sql(conn))
        rows3 = proj.fetch(conn)
        for row in rows3:
            print(
                f"    OrderSummaryRow(order_id={row.order_id}, "
                f"user_email={row.user_email!r}, total={row.total!r})"
            )
        if len(rows3) != 3:
            failures.append(f"Q3: expected 3 summary rows, got {len(rows3)}")
        kinds3 = {(_tname(r.total), _tname(r.order_id)) for r in rows3}
        print(f"    runtime types (total, order_id): {sorted(kinds3)}")
        if kinds3 != {("Decimal", "UUID")}:
            failures.append(f"Q3: unexpected runtime decode {kinds3}")

        # ------------------------------------------------------------------
        # Q4 -- composed predicates (C6) + arithmetic (C2) through to SQL.
        # ------------------------------------------------------------------
        _hr("Q4  composed predicate + arithmetic  (C2/C6)")
        pred = all_of(
            search_orders(OrderFilter(status=OrderStatus.PAID)),
            any_of(ORDERS.total.gte(Decimal("100")), ORDERS.version.gt(1)),
        )
        sel4 = (
            from_orders().where(pred).select(ORDERS.id, ORDERS.total.mul(3))
        )
        print("SQL:", sel4.sql(conn))
        rows4 = sel4.fetch(conn)
        for r in rows4:
            print("   ", r)
        if len(rows4) != 2:
            failures.append(f"Q4: expected 2 rows, got {len(rows4)}")
        # `mul` returns Expr[Decimal]; the backend decodes position 1 as Decimal
        kinds4 = {_tname(r[1]) for r in rows4}
        print(f"    runtime type of total*3: {sorted(kinds4)}")
        if kinds4 != {"Decimal"}:
            failures.append(f"Q4: mul(3) decoded as {kinds4}, want Decimal")

        # empty all_of() really is a no-op
        n_all = len(from_orders().where(all_of()).select(ORDERS.id).fetch(conn))
        print(f"    empty all_of() matched {n_all} row(s) (want 3 = no-op)")
        if n_all != 3:
            failures.append(f"Q4: empty all_of() is not a no-op ({n_all})")

        # ------------------------------------------------------------------
        # Q5 -- statement memoisation: same SHAPE, different VALUES.
        # ------------------------------------------------------------------
        _hr("Q5  statement memoisation by query shape")
        before = conn.statement_cache_stats()
        for status in (
            OrderStatus.PAID,
            OrderStatus.PENDING,
            OrderStatus.CANCELLED,
        ):
            from_orders().where(ORDERS.status.eq(status)).select(
                ORDERS.id
            ).fetch(conn)
        after = conn.statement_cache_stats()
        hits = after[0] - before[0]
        misses = after[1] - before[1]
        print(f"    3 queries, same shape, different values -> "
              f"{misses} miss / {hits} hit")
        if misses != 1 or hits != 2:
            failures.append(f"Q5: expected 1 miss / 2 hits, got {misses}/{hits}")

        # ------------------------------------------------------------------
        # Q6 -- 12-column wide projection actually round-trips.
        # ------------------------------------------------------------------
        _hr("Q6  12-column projection round-trip  (C4)")
        q6 = from_orders().left_join_users().left_join_order_items()
        sel6 = q6.select(
            ORDERS.id,
            ORDERS.user_id,
            ORDERS.status,
            ORDERS.total,
            ORDERS.version,
            ORDERS.created_at,
            q6.users.id,
            q6.users.email,
            q6.users.status,
            q6.users.created_at,
            q6.order_items.quantity,
            q6.order_items.unit_price,
        )
        print("SQL:", sel6.sql(conn))
        rows6 = sel6.fetch(conn)
        for r in rows6:
            print("   ", r)
        if not rows6 or len(rows6[0]) != 12:
            failures.append("Q6: wide row did not have 12 positions")
        # O_2 has no order_items -> positions 10/11 must be None there
        nulls = [r for r in rows6 if r[10] is None]
        print(f"    -> {len(nulls)} row(s) with NULL order_items "
              f"(orders LEFT JOIN order_items)")
        if not nulls:
            failures.append("Q6: expected a NULL order_items row")

    # ----------------------------------------------------------------------
    _hr("C8  leakage assertions (executed, not merely asserted in prose)")
    leaks = assert_no_sqlalchemy_leakage()
    anys = assert_no_any_in_public_signatures()
    if leaks:
        failures.append(f"{len(leaks)} SQLAlchemy names leaked into public modules")
    if anys:
        failures.append(f"{len(anys)} public signatures mention Any")

    _hr("emitted SQL (all statements, in order)")
    for i, line in enumerate(sql_log, 1):
        print(f"  [{i}] {line}")

    _hr("RESULT")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  all runtime assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
