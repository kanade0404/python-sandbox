"""Run all 8 G4 patterns against the live PostgreSQL 16 container and echo SQL."""

from __future__ import annotations

import datetime as _dt
import sys
import uuid
from decimal import Decimal

from generated.pg.facade import Conn, OrderStatus

from patterns import patterns_pg as p

DSN = "postgresql+psycopg://postgres:postgres@localhost:55432/ec"


def main() -> int:
    echo: list[str] = []
    with Conn(DSN, echo_to=echo) as conn:
        print("=" * 74)
        print("SEED")
        user_id, product_ids = p.seed(conn)
        print(f"  user={user_id} products={product_ids}")

        print("=" * 74)
        print("1. get_user_by_email                                     [LAYER A]")
        print(f"  {p.get_user_by_email(conn, 'a@example.test')}")
        print(f"  missing -> {p.get_user_by_email(conn, 'nope@example.test')}")

        print("=" * 74)
        print("2. create_order (FOR UPDATE + typed writes)        [LAYER A (ext)]")
        order_id = p.create_order(conn, user_id, [(product_ids[0], 2), (product_ids[1], 1)])
        print(f"  order={order_id}")
        try:
            p.create_order(conn, user_id, [(product_ids[0], 10_000)])
        except p.OutOfStock as exc:
            conn.rollback()
            print(f"  OutOfStock raised as expected: {exc}")

        print("=" * 74)
        print("3. upsert_product (ON CONFLICT)                    [ESCAPE HATCH]")
        again = p.upsert_product(conn, "SKU-1", "Widget v2", Decimal("11.50"), 5)
        print(f"  {again}")

        print("=" * 74)
        print("4. list_orders_for_user (keyset)                   [LAYER A (ext)]")
        page1 = p.list_orders_for_user(conn, user_id, limit=1)
        print(f"  page1={page1}")
        if page1:
            cur: tuple[_dt.datetime, uuid.UUID] = (page1[-1].created_at, page1[-1].id)
            print(f"  page2={p.list_orders_for_user(conn, user_id, cur, limit=1)}")

        print("=" * 74)
        print("5. list_users_with_orders (LEFT JOIN)                    [LAYER A]")
        for row in p.list_users_with_orders(conn):
            print(f"  {row}")

        print("=" * 74)
        print("6. revenue_by_user (SUM + GROUP BY)                [LAYER A (ext)]")
        print(f"  coalesced: {p.revenue_by_user(conn)}")
        print(f"  honest   : {p.revenue_by_user_honest(conn)}")

        print("=" * 74)
        print("7. transition_order_status (optimistic lock)       [LAYER A (ext)]")
        print(f"  v1 -> paid : {p.transition_order_status(conn, order_id, 1, OrderStatus.PAID)}")
        print(f"  v1 again   : {p.transition_order_status(conn, order_id, 1, OrderStatus.SHIPPED)}")

        print("=" * 74)
        print("8. payments discriminator                    [LAYER A + narrowing]")
        p.record_payment(conn, order_id, Decimal("31.48"), "card", card_last4="4242")
        print(f"  {p.load_payment_view(conn, order_id)}")
        print(f"  missing -> {p.load_payment_view(conn, uuid.uuid4())}")

        hits, misses = conn.statement_cache_stats()
        print("=" * 74)
        print(f"statement cache: {hits} hits / {misses} misses")

    print()
    print("=" * 74)
    print("SQL ISSUED (in order)")
    print("=" * 74)
    for line in echo:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
