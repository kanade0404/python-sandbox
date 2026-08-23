"""G1 (second half): the SQLite-generated facade actually executes.

Run in its own process: both generated facades register into the SAME global
`altsa_runtime.catalog.REGISTRY`, so importing `generated.pg.facade` and
`generated.sqlite.facade` together would have the second one overwrite the
first. That is a documented M2 limitation (one schema per process).
"""

from __future__ import annotations

import sys
from decimal import Decimal

from generated.sqlite.facade import (
    ORDERS,
    USERS,
    Conn,
    OrdersStatus,
    PaymentsMethod,
    UsersStatus,
    from_orders,
    insert_orders,
    insert_users,
    update_orders,
)

DB = (
    "sqlite:////Users/kanade0404/work/python-sandbox/.claude/worktrees/"
    "replicated-floating-hollerith/alt-sqlalchemy-m2/evidence/ec_sqlite.db"
)


def main() -> int:
    echo: list[str] = []
    with Conn(DB, echo_to=echo) as conn:
        conn.exec_raw("DELETE FROM order_items")
        conn.exec_raw("DELETE FROM orders")
        conn.exec_raw("DELETE FROM users")
        conn.commit()
        insert_users(
            conn,
            id="11111111-1111-1111-1111-111111111111",
            email="s@example.test",
            status=UsersStatus.ACTIVE,
            created_at="2026-01-01T00:00:00",
        )
        insert_orders(
            conn,
            id="22222222-2222-2222-2222-222222222222",
            user_id="11111111-1111-1111-1111-111111111111",
            status=OrdersStatus.PENDING,
            total=Decimal("12.50"),
            created_at="2026-01-01T00:00:00",
        )
        conn.commit()

        q = from_orders().left_join_users()
        rows = q.select(ORDERS.id, q.users.email, ORDERS.status, ORDERS.total).fetch(
            conn
        )
        print("rows      :", rows)
        print("projection:", q.select_order_summary().fetch(conn))
        n = update_orders(
            conn,
            ORDERS.status.set(OrdersStatus.PAID),
            ORDERS.version.set(ORDERS.version.add(1)),
            where=ORDERS.id.eq("22222222-2222-2222-2222-222222222222"),
        )
        conn.commit()
        print("updated   :", n)
        print("users cols:", USERS.status, PaymentsMethod.CARD)
    print()
    print("SQL ISSUED")
    for line in echo:
        print(" ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
