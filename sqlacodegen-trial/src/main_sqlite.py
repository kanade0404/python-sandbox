"""Exercise repo_sqlite against the live trial.db, with echo=True."""

from __future__ import annotations

import decimal
import pathlib
import uuid

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

import repo_sqlite as repo
from models_sqlite import (
    OrderItems,
    Orders,
    OrdersStatus,
    Payments,
    PaymentsMethod,
    Products,
    Users,
    UsersStatus,
)

DB = pathlib.Path(__file__).resolve().parent.parent / "trial.db"


def banner(msg: str) -> None:
    print(f"\n\n########## {msg} ##########", flush=True)


def main() -> None:
    engine = create_engine(f"sqlite:///{DB}", echo=True)
    repo.install_begin_immediate(engine)

    quiet = create_engine(f"sqlite:///{DB}")
    repo.install_begin_immediate(quiet)
    alice_id = str(uuid.uuid4())
    with Session(quiet) as s:
        s.execute(delete(Payments))
        s.execute(delete(OrderItems))
        s.execute(delete(Orders))
        s.execute(delete(Products))
        s.execute(delete(Users))
        s.add_all(
            [
                Users(id=alice_id, email="alice@example.com",
                      status=UsersStatus.ACTIVE, metadata_='{"plan": "pro"}'),
                Users(id=str(uuid.uuid4()), email="bob@example.com",
                      status=UsersStatus.SUSPENDED),
            ]
        )
        s.add_all(
            [
                Products(sku="SKU-1", name="Widget",
                         price=decimal.Decimal("9.99"), stock=100,
                         tags='["tools","sale"]'),
                Products(sku="SKU-2", name="Gadget",
                         price=decimal.Decimal("24.50"), stock=50),
            ]
        )
        s.commit()
        p1, p2 = s.scalars(select(Products.id).order_by(Products.sku)).all()
    print(f"seeded alice={alice_id} products={p1},{p2}")

    with Session(engine) as s:
        banner("1) get_user_by_email")
        alice = repo.get_user_by_email(s, "alice@example.com")
        print("->", alice and alice.id, alice and alice.status,
              alice and alice.created_at, type(alice and alice.created_at).__name__)

        banner("3) upsert_product insert then conflict (SQLite ON CONFLICT)")
        a = repo.upsert_product(s, "SKU-3", "Doohickey", decimal.Decimal("5.00"), 10)
        print("-> insert:", a.id, a.price, a.stock)
        b = repo.upsert_product(s, "SKU-3", "Doohickey MkII", decimal.Decimal("6.50"), 7)
        print("-> upsert:", b.id, b.price, b.stock)
        s.commit()

        banner("2) create_order -- note NO 'FOR UPDATE' in the emitted SQL")
        order = repo.create_order(s, alice_id, [(p1, 2), (p2, 1)])
        s.commit()
        print("->", order.id, order.total, order.status, order.created_at)

        banner("2c) second order")
        order2 = repo.create_order(s, alice_id, [(p2, 3)])
        s.commit()
        print("->", order2.id, order2.total)

        banner("4) list_orders_for_user keyset page 1 (limit=1)")
        page1 = repo.list_orders_for_user(s, alice_id, None, 1)
        for o in page1:
            print("->", o.id, o.created_at,
                  [(i.product_id, i.quantity) for i in o.order_items])

        banner("4b) page 2 via cursor")
        for o in repo.list_orders_for_user(
            s, alice_id, (page1[-1].created_at, page1[-1].id), 10
        ):
            print("->", o.id, o.created_at,
                  [(i.product_id, i.quantity) for i in o.order_items])

        banner("7) transition_order_status")
        print("-> ok:", repo.transition_order_status(s, order.id, 1, OrdersStatus.PAID))
        print("-> stale:", repo.transition_order_status(s, order.id, 1, OrdersStatus.SHIPPED))
        s.commit()

        banner("8) record_payment (method is a typed Enum here, unlike PG)")
        pay = repo.record_payment(s, order.id, order.total, PaymentsMethod.CARD,
                                  card_last4="4242")
        s.commit()
        print("->", pay.id, repr(pay.method), pay.card_last4, pay.paid_at)

    banner("DONE")


if __name__ == "__main__":
    main()
