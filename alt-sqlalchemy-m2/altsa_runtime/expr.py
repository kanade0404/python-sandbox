"""Expressions, predicates, columns. SCHEMA-INDEPENDENT.

Everything a generated facade needs in order to *type* a column is here; the
generator only ever writes `Col[<table literal>, <python type>]` annotations
and constructor calls.

Operators are METHODS, never dunders: `__eq__` cannot constrain its right
operand (Python forces `object`), so `==` can never be type-checked. A method
can, and that is the whole point of this design.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar, cast, final

from .ir import (
    Assignment,
    BinOpNode,
    BoolNode,
    ColRefNode,
    FuncNode,
    InNode,
    Node,
    OrderStep,
    ParamNode,
    UnaryNode,
)

T = TypeVar("T")

# The table tag is COVARIANT so that a user-written helper can be annotated
# `Col[str, Decimal]` and accept a column from any table. With it invariant,
# every such helper would have to name one exact table literal, which makes
# table-agnostic utilities unwritable. `TT` appears in no method parameter, so
# covariance is sound here.
TT = TypeVar("TT", bound=str, covariant=True)

# Separate, invariant type vars for the `not_null()` self-type. They must not
# reuse the class-scoped ones.
_TTn = TypeVar("_TTn", bound=str)
_TN = TypeVar("_TN")


class ExprBase:
    """Non-generic root of the expression tree.

    Exists so that overload *implementation* signatures can accept "any
    expression" without reaching for `Any`.
    """

    __slots__ = ()

    def sql_ir(self) -> Node:  # pragma: no cover - overridden
        raise NotImplementedError


class Expr(ExprBase, Generic[T]):
    """A SQL expression whose value type is statically known to be `T`."""

    __slots__ = ("_n",)

    def __init__(self, node: Node) -> None:
        self._n: Node = node

    def sql_ir(self) -> Node:
        return self._n

    def asc(self) -> Order:
        return Order(OrderStep(self.sql_ir(), desc=False))

    def desc(self) -> Order:
        return Order(OrderStep(self.sql_ir(), desc=True))


@final
class Order:
    """One ORDER BY term. Produced by `Expr.asc()` / `Expr.desc()`."""

    __slots__ = ("_s",)

    def __init__(self, step: OrderStep) -> None:
        self._s: OrderStep = step

    def step(self) -> OrderStep:
        return self._s


@final
class Pred:
    """An opaque boolean SQL fragment. Composable, not an `Expr`."""

    __slots__ = ("_n",)

    def __init__(self, node: Node) -> None:
        self._n: Node = node

    def sql_ir(self) -> Node:
        return self._n


@final
class Assign(Generic[TT]):
    """One `column = value` pair of an UPDATE, tagged with its table.

    Tagging is what lets a generated `update_orders()` reject an assignment
    built from a `users` column at type-check time.
    """

    __slots__ = ("_a",)

    def __init__(self, assignment: Assignment) -> None:
        self._a: Assignment = assignment

    def assignment(self) -> Assignment:
        return self._a


def all_of(*preds: Pred) -> Pred:
    """AND-combine. Empty call is a no-op (jOOQ `noCondition` semantics)."""
    return Pred(BoolNode("AND", tuple(p.sql_ir() for p in preds)))


def any_of(*preds: Pred) -> Pred:
    """OR-combine. Empty call is a no-op that matches nothing."""
    return Pred(BoolNode("OR", tuple(p.sql_ir() for p in preds)))


def not_(pred: Pred) -> Pred:
    return Pred(UnaryNode("NOT", pred.sql_ir()))


def _operand(value: object) -> Node:
    if isinstance(value, ExprBase):
        return value.sql_ir()
    return ParamNode(value)


def nodes_of(exprs: tuple[ExprBase, ...]) -> tuple[Node, ...]:
    return tuple(e.sql_ir() for e in exprs)


class Col(Expr[T], Generic[TT, T]):
    """A column reference on table alias `TT` holding values of type `T`."""

    __slots__ = ("_name", "_table")

    def __init__(self, table: str, name: str) -> None:
        self._table: str = table
        self._name: str = name
        super().__init__(ColRefNode(table, name))

    # -- comparison: right operand must be `T` or an expression yielding `T` --

    def eq(self, other: T | Expr[T]) -> Pred:
        return Pred(BinOpNode("=", self.sql_ir(), _operand(other)))

    def ne(self, other: T | Expr[T]) -> Pred:
        return Pred(BinOpNode("!=", self.sql_ir(), _operand(other)))

    def gt(self, other: T | Expr[T]) -> Pred:
        return Pred(BinOpNode(">", self.sql_ir(), _operand(other)))

    def gte(self, other: T | Expr[T]) -> Pred:
        return Pred(BinOpNode(">=", self.sql_ir(), _operand(other)))

    def lt(self, other: T | Expr[T]) -> Pred:
        return Pred(BinOpNode("<", self.sql_ir(), _operand(other)))

    def lte(self, other: T | Expr[T]) -> Pred:
        return Pred(BinOpNode("<=", self.sql_ir(), _operand(other)))

    def in_(self, values: Sequence[T]) -> Pred:
        return Pred(InNode(self.sql_ir(), tuple(values)))

    def is_null(self) -> Pred:
        return Pred(UnaryNode("ISNULL", self.sql_ir()))

    def is_not_null(self) -> Pred:
        return Pred(UnaryNode("NOTNULL", self.sql_ir()))

    # -- UPDATE assignment, tagged with this column's table ------------------

    def set(self, value: T | Expr[T]) -> Assign[TT]:
        return Assign(Assignment(self._table, self._name, _operand(value)))

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

    `Expr[int]` is accepted as a right operand alongside `T`/`int`. That is
    forced by real schemas: `order_items.unit_price * order_items.quantity` is
    `numeric * integer`, and SQL widens it to numeric. Restricting the operand
    to `T` alone (M1's signature, which its 3-table schema never exercised in
    this direction) makes the revenue aggregate inexpressible. Note the result
    is still `Expr[T]` -- the int operand never narrows a Decimal column, which
    is exactly the SQLAlchemy bug this design is contrasted with.
    """

    __slots__ = ()

    def add(self, other: T | int | Expr[T] | Expr[int]) -> Expr[T]:
        return Expr(BinOpNode("+", self.sql_ir(), _operand(other)))

    def sub(self, other: T | int | Expr[T] | Expr[int]) -> Expr[T]:
        return Expr(BinOpNode("-", self.sql_ir(), _operand(other)))

    def mul(self, other: T | int | Expr[T] | Expr[int]) -> Expr[T]:
        return Expr(BinOpNode("*", self.sql_ir(), _operand(other)))


# ---------------------------------------------------------------------------
# aggregates -- schema-independent, so they live here rather than in generated
# code. `sum`/`min`/`max` are HONEST about the empty-group case: they return
# `Expr[T | None]`, which SQLAlchemy's `func.sum(...)` does not.
# ---------------------------------------------------------------------------


def sum_(e: Expr[T]) -> Expr[T | None]:
    return Expr(FuncNode("sum", (e.sql_ir(),)))


def min_(e: Expr[T]) -> Expr[T | None]:
    return Expr(FuncNode("min", (e.sql_ir(),)))


def max_(e: Expr[T]) -> Expr[T | None]:
    return Expr(FuncNode("max", (e.sql_ir(),)))


def count_star() -> Expr[int]:
    return Expr(FuncNode("count", (), star=True))


def count_(e: ExprBase) -> Expr[int]:
    return Expr(FuncNode("count", (e.sql_ir(),)))


def coalesce(e: Expr[T | None], default: T) -> Expr[T]:
    """Turn a nullable expression into a total one, with the type to match."""
    return Expr(FuncNode("coalesce", (e.sql_ir(), ParamNode(default))))
