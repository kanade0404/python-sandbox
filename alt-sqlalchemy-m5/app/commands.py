"""The COMMAND side -- Layer B (M3), typed functions over hand-written SQL.

Every write in the service is here, and every one of them is a statement whose
exact SQL matters: `RETURNING`, `ON CONFLICT`, `FOR UPDATE`, a conditional
decrement, an optimistic version check. Layer A's typed writers can express
none of those, which is the actual reason the two layers exist.

The one import from the other layer is `OrderStatus` / `UserStatus`: Layer A's
generator turns the PostgreSQL ENUMs into Python enums and Layer B's does not
(it types them `str`). The application takes the richer type in its own
signatures and converts at the call, which is friction F10.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from generated_a.facade import OrderStatus, UserStatus
from generated_b.orders import (
    InsertOrderRow,
    RecordPaymentRow,
    TransitionOrderStatusRow,
    insert_order,
    insert_order_item,
    record_payment,
    set_order_total,
    transition_order_status,
)
from generated_b.products import (
    LockProductsForUpdateRow,
    UpsertProductRow,
    decrement_stock,
    lock_products_for_update,
    upsert_product,
)
from generated_b.users import RegisterUserRow, register_user

from .domain import OrderLine, OutOfStock, PaymentMethod, UnknownSku, VersionConflict
from .seam import CommandConn


def register(
    conn: CommandConn, *, email: str, status: UserStatus = UserStatus.ACTIVE
) -> RegisterUserRow:
    """Register a user. One statement, so the caller may use `raw_commands`."""
    row = register_user(conn, email=email, status=status.value)
    if row is None:  # pragma: no cover - INSERT ... RETURNING always returns
        raise RuntimeError("register_user returned no row")
    return row


def upsert(
    conn: CommandConn,
    *,
    sku: str,
    name: str,
    price: Decimal,
    stock: int,
    tags: Sequence[str] = (),
) -> UpsertProductRow:
    """Insert or top up a product.

    `tags` is `Sequence[str]` here and `list[str | None]` in the generated
    signature -- PostgreSQL cannot declare an array's elements NOT NULL, so
    Layer B's honest type is `list[str | None]`, and `list[str]` is not
    assignable to it (invariance). The rebuild below is friction F12.
    """
    tags_arg: list[str | None] = [t for t in tags]
    row = upsert_product(
        conn, sku=sku, name=name, price=price, tags=tags_arg, stock=stock
    )
    if row is None:  # pragma: no cover
        raise RuntimeError("upsert_product returned no row")
    return row


def create_order(
    conn: CommandConn, *, user_id: UUID, lines: Sequence[OrderLine]
) -> InsertOrderRow:
    """The transaction. MUST be called inside `OrderService.commands()`.

    Sequence:
      1. `SELECT ... FOR UPDATE` every requested product, in SKU order (the
         query's own `ORDER BY sku` is what makes the lock order deterministic
         and therefore deadlock-free between two concurrent create_orders).
      2. conditional `UPDATE ... SET stock = stock - n WHERE stock >= n
         RETURNING` per line. No matching row means out of stock, and raising
         here aborts the whole transaction -- the earlier decrements included.
      3. insert the header (server defaults for status/version/total), then the
         lines, then correct the total from the LOCKED prices.

    Prices come from the locked rows, not from the request: a caller cannot ask
    to be charged its own idea of the price.

    NOTE the shape of the failure. `create_order` does not commit or roll back;
    it raises, and `OrderService.commands()`'s `with conn.transaction()` does
    the rolling back. That works because everything in it is Layer B. If step 2
    needed a Layer A read, that read would be on the other connection and would
    neither see the decrements nor be undone by the rollback -- friction F5.
    """
    if not lines:
        raise ValueError("an order needs at least one line")

    skus: list[str | None] = [line.sku for line in lines]
    locked: list[LockProductsForUpdateRow] = lock_products_for_update(conn, skus=skus)
    by_sku: dict[str, LockProductsForUpdateRow] = {p.sku: p for p in locked}

    for line in lines:
        if line.sku not in by_sku:
            raise UnknownSku(line.sku)

    header = insert_order(conn, user_id=user_id)
    if header is None:  # pragma: no cover
        raise RuntimeError("insert_order returned no row")

    total = Decimal("0.00")
    for line in lines:
        product = by_sku[line.sku]
        moved = decrement_stock(
            conn, quantity=line.quantity, product_id=product.id
        )
        if moved is None:
            raise OutOfStock(line.sku, line.quantity, product.stock)
        insert_order_item(
            conn,
            order_id=header.id,
            product_id=product.id,
            quantity=line.quantity,
            unit_price=product.price,
        )
        total += product.price * line.quantity

    set_order_total(conn, total=total, order_id=header.id)
    return InsertOrderRow(
        id=header.id,
        user_id=header.user_id,
        status=header.status,
        version=header.version,
        total=total,
        created_at=header.created_at,
    )


def pay(
    conn: CommandConn,
    *,
    order_id: UUID,
    method: PaymentMethod,
    amount: Decimal,
    card_last4: str | None = None,
) -> RecordPaymentRow:
    """Record the payment. `payments.order_id` is UNIQUE, so a second call for
    the same order is a `UniqueViolation`, which is the correct answer."""
    row = record_payment(
        conn,
        order_id=order_id,
        method=method,
        amount=amount,
        card_last4=card_last4,
    )
    if row is None:  # pragma: no cover
        raise RuntimeError("record_payment returned no row")
    return row


def transition(
    conn: CommandConn,
    *,
    order_id: UUID,
    expected_version: int,
    new_status: OrderStatus,
) -> TransitionOrderStatusRow:
    """Optimistic status transition. Raises `VersionConflict` if it lost."""
    row = transition_order_status(
        conn,
        new_status=new_status.value,
        order_id=order_id,
        expected_version=expected_version,
    )
    if row is None:
        raise VersionConflict(str(order_id), expected_version)
    return row
