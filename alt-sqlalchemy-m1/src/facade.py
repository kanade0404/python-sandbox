"""alt-SQLAlchemy M1 -- the typed SQL facade, written as-if emitted by codegen.

This file is deliberately REPETITIVE and MECHANICAL. Every construct here is
something a template could emit from a schema catalog + a declared join graph:

  * one `_XxxCols` class and one `_XxxColsN` class per table
    (`N` = "this alias sits on the nullable side of a LEFT JOIN" -- Kysely's
    `Nullable<T>` mapped type, materialised at generation time)
  * one `Q...` class per REACHABLE JOIN SHAPE, carrying exactly the column
    namespaces that shape makes visible, in exactly the nullability state that
    shape implies
  * one typed combinator method per DECLARED edge of the join graph
  * N `select()` overloads (N is a generation parameter; here 13)

Nothing in the public surface mentions SQLAlchemy. All SQLAlchemy imports live
in `_runtime.py`, which this module touches only through `_runtime.execute`.

Schema this file was "generated" from
-------------------------------------
  users(id uuid PK, email text NOT NULL, status UserStatus NOT NULL,
        created_at timestamptz NOT NULL)
  orders(id uuid PK, user_id uuid FK->users.id NOT NULL,
         status OrderStatus NOT NULL, total numeric(12,2) NOT NULL,
         version int NOT NULL, created_at timestamptz NOT NULL)
  order_items(order_id uuid, product_id uuid  -- composite PK,
              quantity int NOT NULL, unit_price numeric(10,2) NOT NULL)

Declared join graph (joins.toml):
  orders -> users        ON users.id = orders.user_id
  orders -> order_items  ON order_items.order_id = orders.id

Only these two edges get typed combinators. An undeclared join has no method,
so it is not a "runtime failure" -- it does not exist in the API at all.
"""

from __future__ import annotations

import datetime as _dt
import enum
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import TracebackType
from typing import (
    Any,
    Generic,
    Literal,
    Self,
    TypeVar,
    TypeVarTuple,
    Unpack,
    cast,
    final,
    overload,
)
from uuid import UUID

from _ir import (
    BinOpNode as _BinOpNode,
)
from _ir import (
    BoolNode as _BoolNode,
)
from _ir import (
    ColRefNode as _ColRefNode,
)
from _ir import (
    InNode as _InNode,
)
from _ir import (
    Node as _Node,
)
from _ir import (
    ParamNode as _ParamNode,
)
from _ir import (
    Plan as _Plan,
)
from _ir import (
    UnaryNode as _UnaryNode,
)
from _runtime import RuntimeConn, execute_plan, register_enum

__all__ = [
    "ORDERS",
    "ORDER_ITEMS",
    "USERS",
    "Col",
    "Conn",
    "Expr",
    "NumCol",
    "OrderStatus",
    "OrderSummaryRow",
    "Pred",
    "Projection",
    "Select",
    "UserStatus",
    "all_of",
    "any_of",
    "from_orders",
    "from_users",
]


# ---------------------------------------------------------------------------
# 1. generated enum types (PG ENUM -> Python enum)
# ---------------------------------------------------------------------------


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"


# The backend stores enums as text; these registrations tell it how to decode
# a given (table, column) back into the generated Python enum.
register_enum("users", "status", UserStatus)
register_enum("orders", "status", OrderStatus)


# ---------------------------------------------------------------------------
# 3. expression / predicate values
# ---------------------------------------------------------------------------

T = TypeVar("T")

# The table tag is COVARIANT so that a user-written helper can be annotated
# `Col[str, Decimal]` and accept a column from any table. With it invariant,
# every such helper would have to name one exact table literal, which makes
# table-agnostic utilities unwritable. `TT` appears in no method parameter, so
# covariance is sound here.
TT = TypeVar("TT", bound=str, covariant=True)

# Separate, invariant type vars for the `not_null()` self-type (C7). They must
# not reuse the class-scoped ones.
_TTn = TypeVar("_TTn", bound=str)
_TN = TypeVar("_TN")


class _ExprBase:
    """Non-generic root of the expression tree.

    Exists so that overload *implementation* signatures can accept "any
    expression" without reaching for `Any`.
    """

    __slots__ = ()

    def sql_ir(self) -> _Node:  # pragma: no cover - overridden
        raise NotImplementedError


class Expr(_ExprBase, Generic[T]):
    """A SQL expression whose value type is statically known to be `T`."""

    __slots__ = ("_n",)

    def __init__(self, node: _Node) -> None:
        self._n: _Node = node

    def sql_ir(self) -> _Node:
        return self._n


@final
class Pred:
    """An opaque boolean SQL fragment. Composable, not an `Expr`."""

    __slots__ = ("_n",)

    def __init__(self, node: _Node) -> None:
        self._n: _Node = node

    def sql_ir(self) -> _Node:
        return self._n


def all_of(*preds: Pred) -> Pred:
    """AND-combine. Empty call is a no-op (jOOQ `noCondition` semantics)."""
    return Pred(_BoolNode("AND", tuple(p.sql_ir() for p in preds)))


def any_of(*preds: Pred) -> Pred:
    """OR-combine. Empty call is a no-op that matches nothing."""
    return Pred(_BoolNode("OR", tuple(p.sql_ir() for p in preds)))


def _operand(value: object) -> _Node:
    if isinstance(value, _ExprBase):
        return value.sql_ir()
    return _ParamNode(value)


def _nodes_of(exprs: tuple[_ExprBase, ...]) -> tuple[_Node, ...]:
    return tuple(e.sql_ir() for e in exprs)


class Col(Expr[T], Generic[TT, T]):
    """A column reference on table alias `TT` holding values of type `T`.

    Operators are METHODS, never dunders: `__eq__` cannot constrain its right
    operand (Python forces `object`), so `==` can never be type-checked. A
    method can, and that is the whole point of this design.
    """

    __slots__ = ("_name", "_table")

    def __init__(self, table: str, name: str) -> None:
        self._table: str = table
        self._name: str = name
        super().__init__(_ColRefNode(table, name))

    # -- comparison: right operand must be `T` or an expression yielding `T` --

    def eq(self, other: T | Expr[T]) -> Pred:
        return Pred(_BinOpNode("=", self.sql_ir(), _operand(other)))

    def ne(self, other: T | Expr[T]) -> Pred:
        return Pred(_BinOpNode("!=", self.sql_ir(), _operand(other)))

    def gt(self, other: T | Expr[T]) -> Pred:
        return Pred(_BinOpNode(">", self.sql_ir(), _operand(other)))

    def gte(self, other: T | Expr[T]) -> Pred:
        return Pred(_BinOpNode(">=", self.sql_ir(), _operand(other)))

    def lt(self, other: T | Expr[T]) -> Pred:
        return Pred(_BinOpNode("<", self.sql_ir(), _operand(other)))

    def lte(self, other: T | Expr[T]) -> Pred:
        return Pred(_BinOpNode("<=", self.sql_ir(), _operand(other)))

    def in_(self, values: Sequence[T]) -> Pred:
        return Pred(_InNode(self.sql_ir(), tuple(values)))

    def is_null(self) -> Pred:
        return Pred(_UnaryNode("ISNULL", self.sql_ir()))

    def is_not_null(self) -> Pred:
        return Pred(_UnaryNode("NOTNULL", self.sql_ir()))

    # -- escape hatch: drop the `| None` a LEFT JOIN put there (Kysely $notNull)

    def not_null(self: Col[_TTn, _TN | None]) -> Col[_TTn, _TN]:
        """Assert this column is non-NULL despite the join shape.

        Unsound by construction -- that is the point of an escape hatch. The
        SQL is unchanged; only the static type narrows.
        """
        return cast("Col[_TTn, _TN]", self)


class NumCol(Col[TT, T]):
    """A numeric column. Arithmetic combinators exist only here.

    The result type is FIXED by the column's own type, so "operand order"
    (jOOQ's `Field<T>.add` asymmetry) cannot arise -- there is no expression
    form in which the literal comes first.
    """

    __slots__ = ()

    def add(self, other: T | int | Expr[T]) -> Expr[T]:
        return Expr(_BinOpNode("+", self.sql_ir(), _operand(other)))

    def sub(self, other: T | int | Expr[T]) -> Expr[T]:
        return Expr(_BinOpNode("-", self.sql_ir(), _operand(other)))

    def mul(self, other: T | int | Expr[T]) -> Expr[T]:
        return Expr(_BinOpNode("*", self.sql_ir(), _operand(other)))


# ---------------------------------------------------------------------------
# 4. generated column namespaces -- normal and Nullable variants
#
# `_XxxColsN` is the pre-computed `Nullable<_XxxCols>`. Kysely recomputes this
# on every type-check with a mapped type; here the generator wrote it out once.
# The classes are stateless (table+column names are baked into each `Col`), so
# a single module-level instance is shared by every query.
# ---------------------------------------------------------------------------


_Users = Literal["users"]
_Orders = Literal["orders"]
_OrderItems = Literal["order_items"]


@final
class _UsersCols:
    __slots__ = ()
    id: Col[_Users, UUID] = Col("users", "id")
    email: Col[_Users, str] = Col("users", "email")
    status: Col[_Users, UserStatus] = Col("users", "status")
    created_at: Col[_Users, _dt.datetime] = Col("users", "created_at")


@final
class _UsersColsN:
    __slots__ = ()
    id: Col[_Users, UUID | None] = Col("users", "id")
    email: Col[_Users, str | None] = Col("users", "email")
    status: Col[_Users, UserStatus | None] = Col("users", "status")
    created_at: Col[_Users, _dt.datetime | None] = Col("users", "created_at")


@final
class _OrdersCols:
    __slots__ = ()
    id: Col[_Orders, UUID] = Col("orders", "id")
    user_id: Col[_Orders, UUID] = Col("orders", "user_id")
    status: Col[_Orders, OrderStatus] = Col("orders", "status")
    total: NumCol[_Orders, Decimal] = NumCol("orders", "total")
    version: NumCol[_Orders, int] = NumCol("orders", "version")
    created_at: Col[_Orders, _dt.datetime] = Col("orders", "created_at")


@final
class _OrdersColsN:
    __slots__ = ()
    id: Col[_Orders, UUID | None] = Col("orders", "id")
    user_id: Col[_Orders, UUID | None] = Col("orders", "user_id")
    status: Col[_Orders, OrderStatus | None] = Col("orders", "status")
    total: NumCol[_Orders, Decimal | None] = NumCol("orders", "total")
    version: NumCol[_Orders, int | None] = NumCol("orders", "version")
    created_at: Col[_Orders, _dt.datetime | None] = Col("orders", "created_at")


@final
class _OrderItemsCols:
    __slots__ = ()
    order_id: Col[_OrderItems, UUID] = Col("order_items", "order_id")
    product_id: Col[_OrderItems, UUID] = Col("order_items", "product_id")
    quantity: NumCol[_OrderItems, int] = NumCol("order_items", "quantity")
    unit_price: NumCol[_OrderItems, Decimal] = NumCol("order_items", "unit_price")


@final
class _OrderItemsColsN:
    __slots__ = ()
    order_id: Col[_OrderItems, UUID | None] = Col("order_items", "order_id")
    product_id: Col[_OrderItems, UUID | None] = Col("order_items", "product_id")
    quantity: NumCol[_OrderItems, int | None] = NumCol("order_items", "quantity")
    unit_price: NumCol[_OrderItems, Decimal | None] = NumCol(
        "order_items", "unit_price"
    )


USERS = _UsersCols()
ORDERS = _OrdersCols()
ORDER_ITEMS = _OrderItemsCols()

_USERS_N = _UsersColsN()
_ORDERS_N = _OrdersColsN()
_ORDER_ITEMS_N = _OrderItemsColsN()


# ---------------------------------------------------------------------------
# 5. row types
# ---------------------------------------------------------------------------

Ts = TypeVarTuple("Ts")
R = TypeVar("R")

T1 = TypeVar("T1")
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T4 = TypeVar("T4")
T5 = TypeVar("T5")
T6 = TypeVar("T6")
T7 = TypeVar("T7")
T8 = TypeVar("T8")
T9 = TypeVar("T9")
T10 = TypeVar("T10")
T11 = TypeVar("T11")
T12 = TypeVar("T12")
T13 = TypeVar("T13")


@final
class Select(Generic[Unpack[Ts]]):
    """A positional projection. `fetch` yields `list[tuple[*Ts]]` exactly."""

    __slots__ = ("_exprs", "_plan")

    def __init__(self, plan: _Plan, exprs: tuple[_ExprBase, ...]) -> None:
        self._plan: _Plan = plan
        self._exprs: tuple[_ExprBase, ...] = exprs

    def fetch(self, conn: Conn) -> list[tuple[Unpack[Ts]]]:
        rows = conn.run_plan(self._plan, _nodes_of(self._exprs))
        # The type/runtime seam. Above it everything is statically typed;
        # below it the driver hands back untyped rows. The generator is what
        # makes this cast trustworthy -- it wrote both sides from one catalog.
        return cast("list[tuple[Unpack[Ts]]]", rows)

    def sql(self, conn: Conn) -> str:
        return conn.render_plan(self._plan, _nodes_of(self._exprs))


@dataclass(frozen=True, slots=True)
class OrderSummaryRow:
    """Declared projection (projections.toml). `user_email` is `str | None`
    because it was declared against the orders LEFT JOIN users shape."""

    order_id: UUID
    user_email: str | None
    total: Decimal


@final
class Projection(Generic[R]):
    """A named projection bound to a row dataclass."""

    __slots__ = ("_ctor", "_exprs", "_plan")

    def __init__(
        self,
        plan: _Plan,
        exprs: tuple[_ExprBase, ...],
        ctor: type[R],
    ) -> None:
        self._plan: _Plan = plan
        self._exprs: tuple[_ExprBase, ...] = exprs
        self._ctor: type[R] = ctor

    def fetch(self, conn: Conn) -> list[R]:
        rows = conn.run_plan(self._plan, _nodes_of(self._exprs))
        ctor = cast("Any", self._ctor)
        return [cast("R", ctor(*row)) for row in rows]

    def sql(self, conn: Conn) -> str:
        return conn.render_plan(self._plan, _nodes_of(self._exprs))


# ---------------------------------------------------------------------------
# 6. connection -- a facade type; no SQLAlchemy object is ever handed out
# ---------------------------------------------------------------------------


@final
class Conn:
    __slots__ = ("_impl",)

    def __init__(self, dsn: str, *, echo_to: list[str] | None = None) -> None:
        self._impl: RuntimeConn = RuntimeConn(dsn, echo_to=echo_to)

    def __enter__(self) -> Conn:
        self._impl.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._impl.close()

    # The runtime seam. `Select`/`Projection` go through these instead of
    # touching `_impl`, so the SQLAlchemy-bearing object never leaves this
    # class. Both take private `_ir` types and return plain builtins.
    def run_plan(self, plan: _Plan, outs: tuple[_Node, ...]) -> list[tuple[object, ...]]:
        return execute_plan(self._impl, plan, outs)

    def render_plan(self, plan: _Plan, outs: tuple[_Node, ...]) -> str:
        return self._impl.render(plan, outs)

    def create_all(self) -> None:
        self._impl.create_all()

    def execute_ddl(self, statement: str) -> None:
        """Raw DDL, for tests/fixtures only. Deliberately not part of the
        typed query surface."""
        self._impl.execute_ddl(statement)

    def insert_row(self, table: str, values: dict[str, object]) -> None:
        """Minimal typed-ish writer for the demo. M1 is a read-path proof."""
        self._impl.insert_row(table, values)

    def statement_cache_stats(self) -> tuple[int, int]:
        """(hits, misses) of the shape-keyed statement memo."""
        return self._impl.cache_stats()


# ---------------------------------------------------------------------------
# 7. query shapes
#
# One class per reachable join shape. Naming: Q<root>[<Table><I|N>]...
#   I = joined INNER  -> columns keep their catalog nullability
#   N = joined LEFT   -> columns are the pre-computed `| None` variant
#
# With 2 declared edges this is 1 + 2*2 + 2*2 = 9 classes. The count is
# 3^E-ish in the number of declared edges (absent / inner / left), which is
# precisely the price of the design doc's bet that "real apps' joins are
# finite and declarable".
# ---------------------------------------------------------------------------


class _QueryBase:
    """Shared machinery. `select()`'s 13 overloads are written ONCE here
    because `Select[*Ts]` does not depend on the query shape."""

    __slots__ = ("_plan",)

    def __init__(self, plan: _Plan) -> None:
        self._plan: _Plan = plan

    # NOTE: no column namespaces are declared here on purpose. Each concrete
    # shape below lists exactly the aliases it makes visible -- putting
    # `orders` on the base would make `from_users().orders` type-check against
    # a query with no orders table in it.

    def where(self, *preds: Pred) -> Self:
        return type(self)(self._plan.with_wheres(tuple(p.sql_ir() for p in preds)))

    # -- generated projection overloads (arity 1..13) ------------------------

    @overload
    def select(self, e1: Expr[T1], /) -> Select[T1]: ...
    @overload
    def select(self, e1: Expr[T1], e2: Expr[T2], /) -> Select[T1, T2]: ...
    @overload
    def select(
        self, e1: Expr[T1], e2: Expr[T2], e3: Expr[T3], /
    ) -> Select[T1, T2, T3]: ...
    @overload
    def select(
        self, e1: Expr[T1], e2: Expr[T2], e3: Expr[T3], e4: Expr[T4], /
    ) -> Select[T1, T2, T3, T4]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        /,
    ) -> Select[T1, T2, T3, T4, T5]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        e8: Expr[T8],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7, T8]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        e8: Expr[T8],
        e9: Expr[T9],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7, T8, T9]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        e8: Expr[T8],
        e9: Expr[T9],
        e10: Expr[T10],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        e8: Expr[T8],
        e9: Expr[T9],
        e10: Expr[T10],
        e11: Expr[T11],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        e8: Expr[T8],
        e9: Expr[T9],
        e10: Expr[T10],
        e11: Expr[T11],
        e12: Expr[T12],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12]: ...
    @overload
    def select(
        self,
        e1: Expr[T1],
        e2: Expr[T2],
        e3: Expr[T3],
        e4: Expr[T4],
        e5: Expr[T5],
        e6: Expr[T6],
        e7: Expr[T7],
        e8: Expr[T8],
        e9: Expr[T9],
        e10: Expr[T10],
        e11: Expr[T11],
        e12: Expr[T12],
        e13: Expr[T13],
        /,
    ) -> Select[T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T12, T13]: ...

    def select(self, *exprs: _ExprBase) -> Select[Unpack[tuple[Any, ...]]]:
        # Implementation signature. Callers never see it -- pyright resolves
        # every call against the overloads above. Arity 14+ matches no overload
        # and is a type error (documented cap, no `Any` fallback rung).
        return Select(self._plan, exprs)


@final
class QOrders(_QueryBase):
    """FROM orders. Note the absence of `.users` / `.order_items`: an
    un-joined table is not merely nullable here, it is not addressable."""

    __slots__ = ()
    orders: _OrdersCols = ORDERS

    def join_users(self) -> QOrdersUsersI:
        return QOrdersUsersI(self._plan.with_join("users", "inner"))

    def left_join_users(self) -> QOrdersUsersN:
        return QOrdersUsersN(self._plan.with_join("users", "left"))

    def join_order_items(self) -> QOrdersItemsI:
        return QOrdersItemsI(self._plan.with_join("order_items", "inner"))

    def left_join_order_items(self) -> QOrdersItemsN:
        return QOrdersItemsN(self._plan.with_join("order_items", "left"))


@final
class QOrdersUsersI(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    users: _UsersCols = USERS

    def join_order_items(self) -> QOrdersUsersIItemsI:
        return QOrdersUsersIItemsI(self._plan.with_join("order_items", "inner"))

    def left_join_order_items(self) -> QOrdersUsersIItemsN:
        return QOrdersUsersIItemsN(self._plan.with_join("order_items", "left"))


@final
class QOrdersUsersN(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    users: _UsersColsN = _USERS_N

    def join_order_items(self) -> QOrdersUsersNItemsI:
        return QOrdersUsersNItemsI(self._plan.with_join("order_items", "inner"))

    def left_join_order_items(self) -> QOrdersUsersNItemsN:
        return QOrdersUsersNItemsN(self._plan.with_join("order_items", "left"))

    def select_order_summary(self) -> Projection[OrderSummaryRow]:
        """Declared projection, emitted only on the shape it was declared for."""
        return Projection(
            self._plan,
            (ORDERS.id, self.users.email, ORDERS.total),
            OrderSummaryRow,
        )


@final
class QOrdersItemsI(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    order_items: _OrderItemsCols = ORDER_ITEMS

    def join_users(self) -> QOrdersUsersIItemsI:
        return QOrdersUsersIItemsI(self._plan.with_join("users", "inner"))

    def left_join_users(self) -> QOrdersUsersNItemsI:
        return QOrdersUsersNItemsI(self._plan.with_join("users", "left"))


@final
class QOrdersItemsN(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    order_items: _OrderItemsColsN = _ORDER_ITEMS_N

    def join_users(self) -> QOrdersUsersIItemsN:
        return QOrdersUsersIItemsN(self._plan.with_join("users", "inner"))

    def left_join_users(self) -> QOrdersUsersNItemsN:
        return QOrdersUsersNItemsN(self._plan.with_join("users", "left"))


@final
class QOrdersUsersIItemsI(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    users: _UsersCols = USERS
    order_items: _OrderItemsCols = ORDER_ITEMS


@final
class QOrdersUsersIItemsN(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    users: _UsersCols = USERS
    order_items: _OrderItemsColsN = _ORDER_ITEMS_N


@final
class QOrdersUsersNItemsI(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    users: _UsersColsN = _USERS_N
    order_items: _OrderItemsCols = ORDER_ITEMS


@final
class QOrdersUsersNItemsN(_QueryBase):
    __slots__ = ()
    orders: _OrdersCols = ORDERS
    users: _UsersColsN = _USERS_N
    order_items: _OrderItemsColsN = _ORDER_ITEMS_N


# -- shapes rooted at `users` -----------------------------------------------
# The FK orders.user_id -> users.id is traversable in both directions, so the
# generator emits a driving-table entry point for each end of it. This is the
# direction that can produce a preserved row with nothing on the other side.


@final
class QUsers(_QueryBase):
    """FROM users. No `.orders` here -- see `QUsersOrdersN`."""

    __slots__ = ()
    users: _UsersCols = USERS

    def join_orders(self) -> QUsersOrdersI:
        return QUsersOrdersI(self._plan.with_join("orders", "inner"))

    def left_join_orders(self) -> QUsersOrdersN:
        return QUsersOrdersN(self._plan.with_join("orders", "left"))


@final
class QUsersOrdersI(_QueryBase):
    __slots__ = ()
    users: _UsersCols = USERS
    orders: _OrdersCols = ORDERS


@final
class QUsersOrdersN(_QueryBase):
    __slots__ = ()
    users: _UsersCols = USERS
    # every orders column is `| None`: a user may have no orders at all
    orders: _OrdersColsN = _ORDERS_N


def from_orders() -> QOrders:
    return QOrders(_Plan("orders"))


def from_users() -> QUsers:
    return QUsers(_Plan("users"))
