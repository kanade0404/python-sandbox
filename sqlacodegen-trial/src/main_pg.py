"""Exercise every repo_pg function against the live PostgreSQL database.

Run with:  uv run python src/main_pg.py
The engine is created with echo=True so the emitted SQL lands on stderr/stdout.
"""

from __future__ import annotations

import decimal
import uuid
from typing import Any, cast

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

import repo_pg as repo
from models_pg import (
    OrderItems,
    Orders,
    OrderStatus,
    Payments,
    Products,
    Users,
    UserStatus,
)

DSN = "postgresql+psycopg://postgres:postgres@localhost:55432/postgres"


def banner(msg: str) -> None:
    print(f"\n\n########## {msg} ##########", flush=True)


def main() -> None:
    engine = create_engine(DSN, echo=True)

    banner("SEED (echo off)")
    quiet = create_engine(DSN)
    with Session(quiet) as s:
        s.execute(delete(Payments))
        s.execute(delete(OrderItems))
        s.execute(delete(Orders))
        s.execute(delete(Products))
        s.execute(delete(Users))
        s.add_all(
            [
                Users(
                    email="alice@example.com",
                    status=UserStatus.ACTIVE,
                    metadata_={"plan": "pro", "seats": 3},
                ),
                Users(email="bob@example.com", status=UserStatus.SUSPENDED),
                Users(email="carol-no-orders@example.com"),
            ]
        )
        s.add_all(
            [
                Products(sku="SKU-1", name="Widget", price=decimal.Decimal("9.99"),
                         stock=100, tags=["tools", "sale"]),
                Products(sku="SKU-2", name="Gadget", price=decimal.Decimal("24.50"),
                         stock=50, tags=None),
            ]
        )
        s.commit()
        alice_id = s.scalars(
            select(Users.id).where(Users.email == "alice@example.com")
        ).one()
        p1, p2 = s.scalars(select(Products.id).order_by(Products.sku)).all()
    print(f"seeded alice={alice_id} products={p1},{p2}")

    with Session(engine) as s:
        banner("1) get_user_by_email")
        alice = repo.get_user_by_email(s, "alice@example.com")
        # metadata_ is generated as Mapped[Optional[dict]] (bare `dict`), so a
        # strict checker sees dict[Unknown, Unknown]; cast at the boundary.
        meta = (
            cast("dict[str, Any] | None", alice.metadata_)  # pyright: ignore[reportUnknownMemberType]
            if alice
            else None
        )
        print("->", alice, alice and alice.status, meta)
        print("-> miss:", repo.get_user_by_email(s, "nobody@example.com"))

        banner("3) upsert_product (insert path)")
        p3 = repo.upsert_product(s, "SKU-3", "Doohickey",
                                 decimal.Decimal("5.00"), 10, ["new"])
        print("->", p3.id, p3.sku, p3.price, p3.stock, p3.tags)

        banner("3b) upsert_product (conflict path, stock accumulates)")
        p3b = repo.upsert_product(s, "SKU-3", "Doohickey MkII",
                                  decimal.Decimal("6.50"), 7, ["new", "hot"])
        print("->", p3b.id, p3b.sku, p3b.price, p3b.stock, p3b.tags)
        s.commit()

        banner("2) create_order (SELECT ... FOR UPDATE + stock decrement)")
        order = repo.create_order(s, alice_id, [(p1, 2), (p2, 1)])
        s.commit()
        print("->", order.id, order.total, order.status, order.version)

        banner("2b) create_order -> OutOfStock")
        try:
            repo.create_order(s, alice_id, [(p1, 10_000)])
        except repo.OutOfStock as exc:
            s.rollback()
            print("-> raised OutOfStock:", exc)

        banner("2c) second order (for pagination)")
        order2 = repo.create_order(s, alice_id, [(p2, 3)])
        s.commit()
        print("->", order2.id, order2.total)

        banner("4) list_orders_for_user (page 1, limit=1, selectinload)")
        page1 = repo.list_orders_for_user(s, alice_id, None, 1)
        for o in page1:
            print("->", o.id, o.created_at, [(i.product_id, i.quantity) for i in o.order_items])

        banner("4b) list_orders_for_user (page 2 via keyset cursor)")
        cur = (page1[-1].created_at, page1[-1].id)
        page2 = repo.list_orders_for_user(s, alice_id, cur, 10)
        for o in page2:
            print("->", o.id, o.created_at, [(i.product_id, i.quantity) for i in o.order_items])

        banner("5) list_users_with_latest_order_unsafe (no Nullable)")
        for row in repo.list_users_with_latest_order_unsafe(s):
            print("->", row[0].email, "| order:", row[1] and row[1].id, "| runtime type:", type(row[1]).__name__)

        banner("5b) list_users_with_latest_order_safe (with Nullable)")
        for row2 in repo.list_users_with_latest_order_safe(s):
            print("->", row2[0].email, "| order:", row2[1].id if row2[1] else None)

        banner("6) revenue_by_user")
        for email, rev in repo.revenue_by_user(s):
            print("->", email, rev, type(rev).__name__)

        banner("7) transition_order_status (optimistic locking, success)")
        ok = repo.transition_order_status(s, order.id, 1, OrderStatus.PAID)
        s.commit()
        print("-> rowcount==1:", ok)

        banner("7b) transition_order_status (stale version, fails)")
        stale = repo.transition_order_status(s, order.id, 1, OrderStatus.SHIPPED)
        s.commit()
        print("-> stale update applied:", stale)

        banner("8) record_payment + load_payment_view (card)")
        pay = repo.record_payment(s, order.id, order.total, "card", card_last4="4242")
        s.commit()
        print("->", pay.id, pay.method, pay.card_last4)
        view = repo.load_payment_view(s, order.id)
        print("-> narrowed:", view)

        banner("8b) record_payment (wallet) + narrowing")
        repo.record_payment(s, order2.id, order2.total, "wallet",
                            wallet_provider="paypay")
        s.commit()
        print("-> narrowed:", repo.load_payment_view(s, order2.id))

        banner("8c) record_payment validation error")
        try:
            repo.record_payment(s, uuid.uuid4(), decimal.Decimal("1"), "card")
        except ValueError as exc:
            print("-> raised:", exc)

    banner("DONE")


if __name__ == "__main__":
    main()
