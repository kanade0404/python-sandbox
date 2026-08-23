"""SQLite counterparts of the repo functions -- only where they differ from PG.

Differences driven by the generated ``models_sqlite`` module:
  * ``users.id`` / ``orders.id`` are ``Mapped[str]`` (TEXT), not ``uuid.UUID``
  * ``created_at`` / ``paid_at`` are ``Mapped[str]`` (TEXT), not ``datetime``
  * ``payments.method`` became a real ``Mapped[PaymentsMethod]`` enum because
    sqlacodegen reflected the ``CHECK (method IN (...))`` constraint -- the PG
    model kept it as plain ``str``.
  * ``SELECT ... FOR UPDATE`` does not exist; the SQLite dialect silently drops
    it. Write-transaction isolation comes from ``BEGIN IMMEDIATE`` instead.
"""

from __future__ import annotations

import decimal
import uuid
from typing import TYPE_CHECKING, Any, Sequence, cast

from sqlalchemy import (
    Connection,
    CursorResult,
    Engine,
    event,
    literal,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload

from models_sqlite import (
    OrderItems,
    Orders,
    OrdersStatus,
    PaymentsMethod,
    Payments,
    Products,
    Users,
)

if TYPE_CHECKING:
    from typing import reveal_type


# --------------------------------------------------------------------------- 0
def install_begin_immediate(engine: Engine) -> None:
    """Make every SQLAlchemy transaction start as ``BEGIN IMMEDIATE``.

    This is the SQLite replacement for ``SELECT ... FOR UPDATE``: it takes the
    RESERVED write lock at transaction start, so the read-check-write sequence
    in :func:`create_order` cannot interleave with another writer and hit
    ``SQLITE_BUSY`` mid-transaction. pysqlite's default is a deferred BEGIN
    emitted lazily, which is exactly the wrong thing for OLTP writes.
    """

    event.listen(engine, "connect", _on_connect)
    event.listen(engine, "begin", _on_begin)


def _on_connect(dbapi_conn: Any, _rec: Any) -> None:
    dbapi_conn.isolation_level = None  # stop pysqlite emitting its own BEGIN
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


def _on_begin(conn: Connection) -> None:
    conn.exec_driver_sql("BEGIN IMMEDIATE")


# --------------------------------------------------------------------------- 1
def get_user_by_email(session: Session, email: str) -> Users | None:
    user = session.execute(
        select(Users).where(Users.email == email)
    ).scalar_one_or_none()
    if TYPE_CHECKING:
        reveal_type(user)  # Users | None
    return user


# --------------------------------------------------------------------------- 2
def create_order(
    session: Session,
    user_id: str,
    items: Sequence[tuple[int, int]],
) -> Orders:
    """Same shape as the PG version.

    ``with_for_update()`` is still written here on purpose: the SQLite dialect
    accepts it and emits *no* FOR UPDATE clause (see the echoed SQL). The real
    lock came from ``BEGIN IMMEDIATE`` at transaction start.
    """
    product_ids = sorted({pid for pid, _ in items})
    locked = session.scalars(
        select(Products).where(Products.id.in_(product_ids)).with_for_update()
    ).all()
    by_id = {p.id: p for p in locked}
    if len(by_id) != len(product_ids):
        raise LookupError(f"unknown product ids: {sorted(set(product_ids) - by_id.keys())}")

    order = Orders(id=str(uuid.uuid4()), user_id=user_id, status=OrdersStatus.PENDING)
    session.add(order)
    total = decimal.Decimal("0")
    for product_id, qty in items:
        product = by_id[product_id]
        if product.stock < qty:
            raise RuntimeError(f"product {product_id} out of stock")
        product.stock -= qty
        total += product.price * qty
        session.add(
            OrderItems(
                order_id=order.id,
                product_id=product_id,
                quantity=qty,
                unit_price=product.price,
            )
        )
    order.total = total
    session.flush()
    if TYPE_CHECKING:
        reveal_type(order.id)  # str   (PG model: uuid.UUID)
        reveal_type(order.created_at)  # str   (PG model: datetime.datetime)
    return order


# --------------------------------------------------------------------------- 3
def upsert_product(
    session: Session,
    sku: str,
    name: str,
    price: decimal.Decimal,
    stock_delta: int = 0,
) -> Products:
    """SQLite INSERT ... ON CONFLICT DO UPDATE -- same API shape as postgresql."""
    stmt = sqlite_insert(Products).values(
        sku=sku, name=name, price=price, stock=stock_delta
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Products.sku],
        set_={
            "name": stmt.excluded.name,
            "price": stmt.excluded.price,
            "stock": Products.stock + stmt.excluded.stock,
        },
    ).returning(Products)
    # see repo_pg.upsert_product -- populate_existing is required for the
    # RETURNING values to overwrite an identity-mapped instance.
    product = session.scalars(
        stmt, execution_options={"populate_existing": True}
    ).one()
    if TYPE_CHECKING:
        reveal_type(stmt)  # ReturningInsert[Products]
        reveal_type(product)  # Products
    return product


# --------------------------------------------------------------------------- 4
def list_orders_for_user(
    session: Session,
    user_id: str,
    cursor: tuple[str, str] | None = None,
    limit: int = 20,
) -> Sequence[Orders]:
    """Keyset pagination -- cursor is (created_at TEXT, id TEXT) here."""
    stmt = (
        select(Orders)
        .where(Orders.user_id == user_id)
        .options(selectinload(Orders.order_items))
    )
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Orders.created_at, Orders.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    stmt = stmt.order_by(Orders.created_at.desc(), Orders.id.desc()).limit(limit)
    return session.scalars(stmt).unique().all()


# --------------------------------------------------------------------------- 7
def transition_order_status(
    session: Session,
    order_id: str,
    expected_version: int,
    new_status: OrdersStatus,
) -> bool:
    result = cast(
        "CursorResult[Any]",
        session.execute(
            update(Orders)
            .where(Orders.id == order_id, Orders.version == expected_version)
            .values(status=new_status, version=Orders.version + 1)
        ),
    )
    return result.rowcount == 1


# --------------------------------------------------------------------------- 8
def record_payment(
    session: Session,
    order_id: str,
    amount: decimal.Decimal,
    method: PaymentsMethod,
    *,
    card_last4: str | None = None,
    bank_account: str | None = None,
    wallet_provider: str | None = None,
) -> Payments:
    """Here the discriminator IS typed: sqlacodegen turned the SQLite CHECK
    constraint into ``PaymentsMethod``, so an invalid method is a type error --
    unlike the PostgreSQL model where ``method`` stayed ``str``."""
    payment = Payments(
        order_id=order_id,
        method=method,
        amount=amount,
        card_last4=card_last4,
        bank_account=bank_account,
        wallet_provider=wallet_provider,
    )
    session.add(payment)
    session.flush()
    if TYPE_CHECKING:
        reveal_type(payment.method)  # PaymentsMethod  (PG: str)
    return payment
