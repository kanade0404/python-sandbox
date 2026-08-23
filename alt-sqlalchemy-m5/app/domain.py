"""Application-level types shared by both layers.

Small on purpose. Almost everything the service passes around is already a
generated type -- Layer A's `OrderStatus`/`UserStatus` enums and projection
rows, Layer B's per-query row dataclasses. What is left is the handful of
things NEITHER generator can produce:

  * `OrderLine` -- a request shape, not a table shape.
  * `PaymentMethod` -- `payments.method` is `text` with a CHECK constraint, and
    sqlacodegen's CHECK-to-enum detection does not fire for PostgreSQL, so both
    layers type it `str`. The `Literal` here is the application saying what the
    CHECK already says (friction F10).
  * the two failure signals, which both layers spell as "returned None".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, final

#: `payments_method_check`: method IN ('card','bank','wallet').
PaymentMethod = Literal["card", "bank", "wallet"]


@final
@dataclass(frozen=True, slots=True)
class OrderLine:
    """One requested line. `sku`, not `product_id`: the caller does not know
    the identity column, and resolving it is `create_order`'s job."""

    sku: str
    quantity: int


class OrderError(Exception):
    """Base for the command side's expected failures."""


@final
class UnknownSku(OrderError):
    def __init__(self, sku: str) -> None:
        super().__init__(f"no product with sku {sku!r}")
        self.sku: str = sku


@final
class OutOfStock(OrderError):
    """The conditional decrement matched no row.

    Layer B spells this as `decrement_stock(...) -> None`; the difference
    between "no such product" and "not enough stock" is resolved by the caller,
    which has already locked the product row and therefore knows it exists.
    """

    def __init__(self, sku: str, wanted: int, available: int) -> None:
        super().__init__(f"{sku}: wanted {wanted}, only {available} in stock")
        self.sku: str = sku
        self.wanted: int = wanted
        self.available: int = available


@final
class VersionConflict(OrderError):
    """The optimistic UPDATE matched no row: someone else moved first."""

    def __init__(self, order_id: str, expected_version: int) -> None:
        super().__init__(
            f"order {order_id} is no longer at version {expected_version}"
        )
        self.order_id: str = order_id
        self.expected_version: int = expected_version
