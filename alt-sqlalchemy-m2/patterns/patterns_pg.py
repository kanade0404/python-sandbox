"""G4: the 8 OLTP patterns from sqlacodegen-trial/src/repo_pg.py, rewritten
against the GENERATED facade.

Each function carries a LAYER tag:

  LAYER A            expressible in the typed query surface, end to end
  LAYER A (ext)      expressible, but only after M2 added a schema-INDEPENDENT
                     runtime combinator M1 did not have (for_update, order_by,
                     limit, group_by, aggregates, typed writers)
  ESCAPE HATCH       `Raw[R]` -- verbatim SQL, typed only at its row boundary
  LAYER B            verbatim SQL with no typed result at all (`exec_raw`)

The point of the tags is that they are VISIBLE. In the SQLAlchemy version every
pattern looks equally typed while three of them silently were not.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from altsa_runtime import Raw
from generated.pg.facade import (
    ORDER_ITEMS,
    ORDERS,
    PAYMENTS,
    PRODUCTS,
    USERS,
    Conn,
    OrderStatus,
    UserStatus,
    all_of,
    any_of,
    coalesce,
    from_orders,
    from_payments,
    from_products,
    from_users,
    insert_order_items,
    insert_orders,
    insert_payments,
    insert_users,
    sum_,
    update_orders,
    update_products,
)

# --------------------------------------------------------------------------- 1
# LAYER A -- simple indexed lookup on the UNIQUE email column.


@dataclass(frozen=True, slots=True)
class UserRow:
    id: uuid.UUID
    email: str
    status: UserStatus
    created_at: _dt.datetime


def get_user_by_email(conn: Conn, email: str) -> UserRow | None:
    row = (
        from_users()
        .where(USERS.email.eq(email))
        .select(USERS.id, USERS.email, USERS.status, USERS.created_at)
        .fetch_one(conn)
    )
    return None if row is None else UserRow(*row)


# --------------------------------------------------------------------------- 2
# LAYER A (ext) -- classic OLTP write transaction.
# SELECT ... FOR UPDATE on the touched product rows, check + decrement stock,
# then insert the order and its items. `for_update()` is a runtime combinator
# added in M2; every write goes through a GENERATED typed helper.


class OutOfStock(RuntimeError):
    def __init__(self, product_id: int, requested: int, available: int) -> None:
        super().__init__(
            f"product {product_id}: requested {requested}, available {available}"
        )
        self.product_id = product_id
        self.requested = requested
        self.available = available


def create_order(
    conn: Conn,
    user_id: uuid.UUID,
    items: list[tuple[int, int]],
) -> uuid.UUID:
    product_ids = sorted({pid for pid, _ in items})  # sorted -> deadlock-free
    locked = (
        from_products()
        .where(PRODUCTS.id.in_(product_ids))
        .order_by(PRODUCTS.id.asc())
        .for_update()
        .select(PRODUCTS.id, PRODUCTS.stock, PRODUCTS.price)
        .fetch(conn)
    )
    by_id = {pid: (stock, price) for pid, stock, price in locked}
    if len(by_id) != len(product_ids):
        missing = set(product_ids) - by_id.keys()
        raise LookupError(f"unknown product ids: {sorted(missing)}")

    order_id = uuid.uuid4()
    total = Decimal("0")
    lines: list[tuple[int, int, Decimal]] = []
    for product_id, qty in items:
        stock, price = by_id[product_id]
        if stock < qty:
            raise OutOfStock(product_id, qty, stock)
        total += price * qty
        lines.append((product_id, qty, price))

    insert_orders(
        conn,
        id=order_id,
        user_id=user_id,
        status=OrderStatus.PENDING,
        total=total,
    )
    for product_id, qty, price in lines:
        insert_order_items(
            conn,
            order_id=order_id,
            product_id=product_id,
            quantity=qty,
            unit_price=price,
        )
        update_products(
            conn,
            PRODUCTS.stock.set(PRODUCTS.stock.sub(qty)),
            where=PRODUCTS.id.eq(product_id),
        )
    conn.commit()
    return order_id


# --------------------------------------------------------------------------- 3
# ESCAPE HATCH -- INSERT ... ON CONFLICT DO UPDATE ... RETURNING.
# There is no typed construct for PostgreSQL's upsert in Layer A, and inventing
# one would be inventing a dialect. `Raw[ProductRow]` keeps the ROW type
# checked; the statement text is on the caller.


@dataclass(frozen=True, slots=True)
class ProductRow:
    id: int
    sku: str
    name: str
    price: Decimal
    stock: int


_UPSERT_PRODUCT = """
INSERT INTO products (sku, name, price, stock, tags)
VALUES (:sku, :name, :price, :stock, :tags)
ON CONFLICT (sku) DO UPDATE
SET name  = EXCLUDED.name,
    price = EXCLUDED.price,
    tags  = EXCLUDED.tags,
    stock = products.stock + EXCLUDED.stock
RETURNING id, sku, name, price, stock
"""


def upsert_product(
    conn: Conn,
    sku: str,
    name: str,
    price: Decimal,
    stock_delta: int = 0,
    tags: list[str] | None = None,
) -> ProductRow:
    row = Raw(
        _UPSERT_PRODUCT,
        ProductRow,
        {
            "sku": sku,
            "name": name,
            "price": price,
            "stock": stock_delta,
            "tags": tags,
        },
    ).fetch_one(conn)
    conn.commit()
    if row is None:  # pragma: no cover - RETURNING always yields a row
        raise RuntimeError("upsert returned nothing")
    return row


# --------------------------------------------------------------------------- 4
# LAYER A (ext) -- keyset pagination on (created_at DESC, id DESC).
# The row-value comparison `(a, b) < (x, y)` is spelled out as an OR of ANDs,
# which is exactly what the planner sees anyway, and unlike SQLAlchemy's
# `tuple_()` it needs no `literal()` wrapper to type-check.

Cursor = tuple[_dt.datetime, uuid.UUID]


@dataclass(frozen=True, slots=True)
class OrderRow:
    id: uuid.UUID
    created_at: _dt.datetime
    status: OrderStatus
    total: Decimal


def list_orders_for_user(
    conn: Conn,
    user_id: uuid.UUID,
    cursor: Cursor | None = None,
    limit: int = 20,
) -> list[OrderRow]:
    q = from_orders().where(ORDERS.user_id.eq(user_id))
    if cursor is not None:
        q = q.where(
            any_of(
                ORDERS.created_at.lt(cursor[0]),
                all_of(
                    ORDERS.created_at.eq(cursor[0]),
                    ORDERS.id.lt(cursor[1]),
                ),
            )
        )
    rows = (
        q.order_by(ORDERS.created_at.desc(), ORDERS.id.desc())
        .limit(limit)
        .select(ORDERS.id, ORDERS.created_at, ORDERS.status, ORDERS.total)
        .fetch(conn)
    )
    return [OrderRow(*r) for r in rows]


# --------------------------------------------------------------------------- 5
# LAYER A -- LEFT OUTER JOIN listing. THE flagship case: in repo_pg.py this
# needed `sqlalchemy.Nullable(Orders)` to be honest, and the un-annotated
# version type-checked while lying. Here there is no un-annotated version --
# `left_join_orders()` returns a shape whose `orders` namespace is the `| None`
# variant, so `row[1]` is `UUID | None` and nothing else.


def list_users_with_orders(conn: Conn) -> list[tuple[str, uuid.UUID | None, Decimal | None]]:
    q = from_users().left_join_orders()
    return (
        q.order_by(USERS.email.asc(), q.orders.created_at.asc())
        .select(USERS.email, q.orders.id, q.orders.total)
        .fetch(conn)
    )


# --------------------------------------------------------------------------- 6
# LAYER A (ext) -- SUM(unit_price * quantity) grouped by user.
# Two holes from repo_pg.py are closed at once:
#   * operand order no longer decides the static type (there is no reflected
#     operator, so `quantity * unit_price` cannot be written "backwards")
#   * SUM is `Decimal | None`, because SUM over an empty group is NULL


def revenue_by_user(conn: Conn) -> list[tuple[str, Decimal]]:
    revenue = coalesce(
        sum_(ORDER_ITEMS.unit_price.mul(ORDER_ITEMS.quantity)), Decimal("0")
    )
    return (
        from_users()
        .join_orders()
        .join_order_items()
        .group_by(USERS.email)
        .order_by(USERS.email.asc())
        .select(USERS.email, revenue)
        .fetch(conn)
    )


def revenue_by_user_honest(conn: Conn) -> list[tuple[str, Decimal | None]]:
    """The same query without `coalesce`, to show the honest nullable type."""
    return (
        from_users()
        .join_orders()
        .join_order_items()
        .group_by(USERS.email)
        .order_by(USERS.email.asc())
        .select(USERS.email, sum_(ORDER_ITEMS.unit_price.mul(ORDER_ITEMS.quantity)))
        .fetch(conn)
    )


# --------------------------------------------------------------------------- 7
# LAYER A (ext) -- optimistic locking: one UPDATE guarded by the version column.
# `update_orders` returns `int` directly; repo_pg.py needed
# `cast("CursorResult[Any]", session.execute(stmt)).rowcount` because
# `Session.execute()` is typed `Result[Any]`.


def transition_order_status(
    conn: Conn,
    order_id: uuid.UUID,
    expected_version: int,
    new_status: OrderStatus,
) -> bool:
    n = update_orders(
        conn,
        ORDERS.status.set(new_status),
        ORDERS.version.set(ORDERS.version.add(1)),
        where=all_of(
            ORDERS.id.eq(order_id),
            ORDERS.version.eq(expected_version),
        ),
    )
    conn.commit()
    return n == 1


# --------------------------------------------------------------------------- 8
# LAYER A + hand-written narrowing -- the discriminator table.
# On PostgreSQL sqlacodegen does NOT turn `CHECK (method IN (...))` into an
# enum (the constraint compiles to `method = ANY (ARRAY[...])`, which its regex
# does not match), so `payments.method` is `str` and the discriminated union is
# ours to write -- exactly as in repo_pg.py. On SQLite the same DDL DOES yield
# a generated `PaymentsMethod` enum; see evidence/gen_sqlite_report.txt.

PaymentMethod = Literal["card", "bank", "wallet"]


@dataclass(frozen=True, slots=True)
class CardPayment:
    id: int
    order_id: uuid.UUID
    amount: Decimal
    last4: str


@dataclass(frozen=True, slots=True)
class BankPayment:
    id: int
    order_id: uuid.UUID
    amount: Decimal
    account: str


@dataclass(frozen=True, slots=True)
class WalletPayment:
    id: int
    order_id: uuid.UUID
    amount: Decimal
    provider: str


PaymentView = CardPayment | BankPayment | WalletPayment


def record_payment(
    conn: Conn,
    order_id: uuid.UUID,
    amount: Decimal,
    method: PaymentMethod,
    *,
    card_last4: str | None = None,
    bank_account: str | None = None,
    wallet_provider: str | None = None,
) -> None:
    if method == "card" and card_last4 is None:
        raise ValueError("card payments require card_last4")
    if method == "bank" and bank_account is None:
        raise ValueError("bank payments require bank_account")
    if method == "wallet" and wallet_provider is None:
        raise ValueError("wallet payments require wallet_provider")
    insert_payments(
        conn,
        order_id=order_id,
        method=method,
        amount=amount,
        card_last4=card_last4,
        bank_account=bank_account,
        wallet_provider=wallet_provider,
    )
    conn.commit()


def load_payment_view(conn: Conn, order_id: uuid.UUID) -> PaymentView | None:
    row = (
        from_payments()
        .where(PAYMENTS.order_id.eq(order_id))
        .select(
            PAYMENTS.id,
            PAYMENTS.order_id,
            PAYMENTS.amount,
            PAYMENTS.method,
            PAYMENTS.card_last4,
            PAYMENTS.bank_account,
            PAYMENTS.wallet_provider,
        )
        .fetch_one(conn)
    )
    if row is None:
        return None
    pid, oid, amount, method, last4, account, provider = row
    match method:
        case "card":
            assert last4 is not None
            return CardPayment(pid, oid, amount, last4)
        case "bank":
            assert account is not None
            return BankPayment(pid, oid, amount, account)
        case "wallet":
            assert provider is not None
            return WalletPayment(pid, oid, amount, provider)
        case other:
            raise ValueError(f"unknown payment method {other!r}")


# --------------------------------------------------------------------------- 0
# fixture helper (typed writers all the way down)


def seed(conn: Conn) -> tuple[uuid.UUID, list[int]]:
    conn.exec_raw(
        "TRUNCATE payments, order_items, orders, products, users "
        "RESTART IDENTITY CASCADE"
    )
    conn.commit()
    user_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    insert_users(conn, id=user_id, email="a@example.test", status=UserStatus.ACTIVE)
    insert_users(
        conn,
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        email="b@example.test",
    )
    conn.commit()
    p1 = upsert_product(conn, "SKU-1", "Widget", Decimal("9.99"), 100, ["a", "b"])
    p2 = upsert_product(conn, "SKU-2", "Gadget", Decimal("24.50"), 50, None)
    return user_id, [p1.id, p2.id]
