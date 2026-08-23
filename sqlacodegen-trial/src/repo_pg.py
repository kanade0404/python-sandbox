"""Typed OLTP repository against the sqlacodegen-generated PostgreSQL models.

Everything here is SQLAlchemy 2.0 style: ``select()`` / ``insert()`` / ``update()``
constructs executed through ``Session.execute`` / ``Session.scalars``.

``reveal_type(...)`` calls live inside ``if TYPE_CHECKING:`` blocks so pyright
reports the inferred static type without any runtime cost.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Sequence, cast

from sqlalchemy import (
    CursorResult,
    Nullable,
    Row,
    func,
    literal,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from models_pg import OrderItems, Orders, OrderStatus, Payments, Products, Users

if TYPE_CHECKING:
    from typing import reveal_type


# --------------------------------------------------------------------------- 1
def get_user_by_email(session: Session, email: str) -> Users | None:
    """Simple indexed lookup on the UNIQUE email column."""
    stmt = select(Users).where(Users.email == email)
    user = session.execute(stmt).scalar_one_or_none()
    if TYPE_CHECKING:
        reveal_type(stmt)  # Select[Users]
        reveal_type(session.execute(stmt))  # Result[Users]
        reveal_type(user)  # Users | None
        reveal_type(Users.email == email)  # ColumnElement[bool]
    return user


# --------------------------------------------------------------------------- 2
class OutOfStock(RuntimeError):
    def __init__(self, product_id: int, requested: int, available: int) -> None:
        super().__init__(
            f"product {product_id}: requested {requested}, available {available}"
        )
        self.product_id = product_id
        self.requested = requested
        self.available = available


def create_order(
    session: Session,
    user_id: uuid.UUID,
    items: Sequence[tuple[int, int]],
) -> Orders:
    """Classic OLTP write transaction.

    SELECT ... FOR UPDATE on the touched product rows, check + decrement stock,
    then insert the order and its items. Caller owns commit/rollback.

    On SQLite ``with_for_update()`` is silently a no-op (see repo_sqlite.py) --
    there the equivalent guarantee comes from ``BEGIN IMMEDIATE``.
    """
    product_ids = sorted({pid for pid, _ in items})  # sorted -> deadlock-free
    lock_stmt = (
        select(Products).where(Products.id.in_(product_ids)).with_for_update()
    )
    locked = session.scalars(lock_stmt).all()
    if TYPE_CHECKING:
        reveal_type(lock_stmt)  # Select[Products]
        reveal_type(locked)  # Sequence[Products]

    by_id = {p.id: p for p in locked}
    if len(by_id) != len(product_ids):
        missing = set(product_ids) - by_id.keys()
        raise LookupError(f"unknown product ids: {sorted(missing)}")

    order = Orders(id=uuid.uuid4(), user_id=user_id, status=OrderStatus.PENDING)
    total = decimal.Decimal("0")
    session.add(order)

    for product_id, qty in items:
        product = by_id[product_id]
        if product.stock < qty:
            raise OutOfStock(product_id, qty, product.stock)
        product.stock -= qty
        line_total = product.price * qty
        total += line_total
        session.add(
            OrderItems(
                order_id=order.id,
                product_id=product_id,
                quantity=qty,
                unit_price=product.price,
            )
        )
        if TYPE_CHECKING:
            reveal_type(product.price)  # Decimal
            reveal_type(line_total)  # Decimal

    order.total = total
    session.flush()
    return order


# --------------------------------------------------------------------------- 3
def upsert_product(
    session: Session,
    sku: str,
    name: str,
    price: decimal.Decimal,
    stock_delta: int = 0,
    tags: list[str] | None = None,
) -> Products:
    """PostgreSQL INSERT ... ON CONFLICT (sku) DO UPDATE ... RETURNING *."""
    ins = pg_insert(Products).values(
        sku=sku, name=name, price=price, stock=stock_delta, tags=tags
    )
    excluded = ins.excluded
    stmt = ins.on_conflict_do_update(
        index_elements=[Products.sku],
        set_={
            "name": excluded.name,
            "price": excluded.price,
            "tags": excluded.tags,
            # accumulate rather than overwrite -> shows a server-side expression
            "stock": Products.stock + excluded.stock,
        },
    ).returning(Products)

    # populate_existing is REQUIRED: without it an already-identity-mapped
    # Products row wins over the RETURNING values and you silently read back
    # the pre-upsert state even though the UPDATE really happened.
    product = session.scalars(
        stmt, execution_options={"populate_existing": True}
    ).one()
    if TYPE_CHECKING:
        reveal_type(stmt)  # ReturningInsert[Tuple[Products]]
        reveal_type(excluded)  # the EXCLUDED pseudo-table
        reveal_type(excluded.price)  # untyped hole: no per-column typing
        reveal_type(product)  # Products
    return product


# --------------------------------------------------------------------------- 4
Cursor = tuple[datetime.datetime, uuid.UUID]


def list_orders_for_user(
    session: Session,
    user_id: uuid.UUID,
    cursor: Cursor | None = None,
    limit: int = 20,
) -> Sequence[Orders]:
    """Keyset pagination on (created_at DESC, id DESC) with eager-loaded items.

    sqlacodegen DID generate the ``Orders.order_items`` relationship from the FK,
    so ``selectinload`` is usable straight off the generated models.
    """
    stmt = (
        select(Orders)
        .where(Orders.user_id == user_id)
        .options(selectinload(Orders.order_items))
    )
    if cursor is not None:
        # NOTE: tuple_() only accepts ColumnElements, so the cursor *values*
        # must be wrapped in literal(); passing the raw datetime/UUID works at
        # runtime but is a pyright error (reportArgumentType).
        stmt = stmt.where(
            tuple_(Orders.created_at, Orders.id)
            < tuple_(literal(cursor[0]), literal(cursor[1]))
        )
    stmt = stmt.order_by(Orders.created_at.desc(), Orders.id.desc()).limit(limit)

    orders = session.scalars(stmt).unique().all()
    if TYPE_CHECKING:
        reveal_type(orders)  # Sequence[Orders]
        reveal_type(orders[0].order_items)  # list[OrderItems]
        reveal_type(Orders.order_items)  # InstrumentedAttribute[list[OrderItems]]
    return orders


# --------------------------------------------------------------------------- 5
def list_users_with_latest_order_unsafe(
    session: Session,
) -> Sequence[Row[tuple[Users, Orders]]]:
    """LEFT OUTER JOIN *without* Nullable() -- the join-nullability type hole.

    Statically ``row[1]`` is ``Orders``; at runtime it is ``None`` for users
    with no orders. pyright will happily let you write ``row[1].id``.
    """
    stmt = (
        select(Users, Orders)
        .outerjoin(Orders, Orders.user_id == Users.id)
        .order_by(Users.email)
    )
    rows = session.execute(stmt).all()
    if TYPE_CHECKING:
        reveal_type(stmt)  # Select[Tuple[Users, Orders]]
        reveal_type(rows)  # Sequence[Row[Tuple[Users, Orders]]]
        reveal_type(rows[0][1])  # Any  <-- Row.__getitem__ is untyped
        _u, _o = session.execute(stmt).tuples().all()[0]
        reveal_type(_o)  # Orders   <-- LIES, may be None at runtime
    return rows


def list_users_with_latest_order_safe(
    session: Session,
) -> Sequence[Row[tuple[Users, Orders | None]]]:
    """Same query with ``sqlalchemy.Nullable`` (added in 2.0.20) -- the fix."""
    stmt = (
        select(Users, Nullable(Orders))
        .outerjoin(Orders, Orders.user_id == Users.id)
        .order_by(Users.email)
    )
    rows = session.execute(stmt).all()
    if TYPE_CHECKING:
        reveal_type(stmt)  # Select[Tuple[Users, Orders | None]]
        reveal_type(rows)  # Sequence[Row[Tuple[Users, Orders | None]]]
        _u2, _o2 = session.execute(stmt).tuples().all()[0]
        reveal_type(_o2)  # Orders | None  <-- honest; `_o2.id` is now an error
    return rows


# --------------------------------------------------------------------------- 6
def revenue_by_user(session: Session) -> Sequence[tuple[str, decimal.Decimal]]:
    """Aggregate: SUM(quantity * unit_price) grouped by user."""
    # Operand ORDER decides the static type: SQLAlchemy's operator stubs take
    # the type from the left-hand column, so `quantity * unit_price` is
    # ColumnElement[int] while `unit_price * quantity` is
    # ColumnElement[Decimal]. Both produce identical SQL and a Decimal at
    # runtime -- only one of them type-checks honestly.
    revenue = func.sum(OrderItems.unit_price * OrderItems.quantity)
    stmt = (
        select(Users.email, revenue.label("revenue"))
        .join(Orders, Orders.user_id == Users.id)
        .join(OrderItems, OrderItems.order_id == Orders.id)
        .group_by(Users.email)
        .order_by(revenue.desc())
    )
    rows = session.execute(stmt).tuples().all()
    if TYPE_CHECKING:
        reveal_type(OrderItems.quantity * OrderItems.unit_price)  # int (!)
        reveal_type(OrderItems.unit_price * OrderItems.quantity)  # Decimal
        reveal_type(func.sum(OrderItems.quantity * OrderItems.unit_price))
        reveal_type(revenue)
        reveal_type(stmt)
        reveal_type(rows)
        reveal_type(rows[0][1])
    return rows


# --------------------------------------------------------------------------- 7
def transition_order_status(
    session: Session,
    order_id: uuid.UUID,
    expected_version: int,
    new_status: OrderStatus,
) -> bool:
    """Optimistic locking: single UPDATE guarded by the version column."""
    stmt = (
        update(Orders)
        .where(Orders.id == order_id, Orders.version == expected_version)
        .values(status=new_status, version=Orders.version + 1)
    )
    # Session.execute() is typed as returning Result[Any], which has no
    # .rowcount -- the cast is required purely to satisfy pyright.
    result = cast("CursorResult[Any]", session.execute(stmt))
    if TYPE_CHECKING:
        reveal_type(stmt)  # Update
        reveal_type(session.execute(stmt))  # Result[Any]  <-- no .rowcount
        reveal_type(result.rowcount)  # int
    return result.rowcount == 1


# --------------------------------------------------------------------------- 8
PaymentMethod = Literal["card", "bank", "wallet"]


@dataclass(frozen=True, slots=True)
class CardPayment:
    id: int
    order_id: uuid.UUID
    amount: decimal.Decimal
    last4: str


@dataclass(frozen=True, slots=True)
class BankPayment:
    id: int
    order_id: uuid.UUID
    amount: decimal.Decimal
    account: str


@dataclass(frozen=True, slots=True)
class WalletPayment:
    id: int
    order_id: uuid.UUID
    amount: decimal.Decimal
    provider: str


PaymentView = CardPayment | BankPayment | WalletPayment


def record_payment(
    session: Session,
    order_id: uuid.UUID,
    amount: decimal.Decimal,
    method: PaymentMethod,
    *,
    card_last4: str | None = None,
    bank_account: str | None = None,
    wallet_provider: str | None = None,
) -> Payments:
    """Write to the discriminator table.

    Note what the *types* do NOT enforce: nothing stops you passing
    ``method="card"`` with ``card_last4=None``. The generated model types
    ``method`` as plain ``str`` (the CHECK constraint is not reflected into a
    Python Enum on PostgreSQL), so the consistency rule is ours to write.
    """
    if method == "card" and card_last4 is None:
        raise ValueError("card payments require card_last4")
    if method == "bank" and bank_account is None:
        raise ValueError("bank payments require bank_account")
    if method == "wallet" and wallet_provider is None:
        raise ValueError("wallet payments require wallet_provider")

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
        reveal_type(payment.method)  # str  <-- not Literal, not an Enum
    return payment


def load_payment_view(session: Session, order_id: uuid.UUID) -> PaymentView | None:
    """Hand-written discriminated union.

    The ORM will not do this for us: sqlacodegen emitted a plain ``Payments``
    class with no ``__mapper_args__`` polymorphic wiring, so narrowing on the
    discriminator is manual and the per-method columns stay ``str | None``.
    """
    row = session.execute(
        select(Payments).where(Payments.order_id == order_id)
    ).scalar_one_or_none()
    if row is None:
        return None
    if TYPE_CHECKING:
        reveal_type(row.method)  # str
        reveal_type(row.card_last4)  # str | None

    match row.method:
        case "card":
            assert row.card_last4 is not None
            return CardPayment(row.id, row.order_id, row.amount, row.card_last4)
        case "bank":
            assert row.bank_account is not None
            return BankPayment(row.id, row.order_id, row.amount, row.bank_account)
        case "wallet":
            assert row.wallet_provider is not None
            return WalletPayment(
                row.id, row.order_id, row.amount, row.wallet_provider
            )
        case other:
            raise ValueError(f"unknown payment method {other!r}")
