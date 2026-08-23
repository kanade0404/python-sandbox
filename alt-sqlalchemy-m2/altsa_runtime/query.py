"""Query shapes' shared machinery + result containers. SCHEMA-INDEPENDENT.

`QueryBase` holds everything that does NOT depend on which tables are in the
shape: `where`, `group_by`, `order_by`, `limit`, `offset`, `for_update`, and the
13 `select()` overloads. The 13 overloads are pure ARITY machinery -- `Select[*Ts]`
does not mention a table -- so they are written ONCE here instead of being
emitted per shape. That single decision removes ~26 lines x (number of shapes)
from the generated output.

The generated code contributes only the parts that DO depend on the schema: the
concrete shape subclasses, their column namespaces, and their join combinators.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    Self,
    TypeVar,
    TypeVarTuple,
    Unpack,
    cast,
    final,
    overload,
)

from .expr import Expr, ExprBase, Order, Pred, nodes_of
from .ir import Plan

if TYPE_CHECKING:
    from .conn import Conn

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

    def __init__(self, plan: Plan, exprs: tuple[ExprBase, ...]) -> None:
        self._plan: Plan = plan
        self._exprs: tuple[ExprBase, ...] = exprs

    def fetch(self, conn: Conn) -> list[tuple[Unpack[Ts]]]:
        rows = conn.run_plan(self._plan, nodes_of(self._exprs))
        # The type/runtime seam. Above it everything is statically typed; below
        # it the driver hands back untyped rows. The generator is what makes
        # this cast trustworthy -- it wrote both sides from one catalog.
        return cast("list[tuple[Unpack[Ts]]]", rows)

    def fetch_one(self, conn: Conn) -> tuple[Unpack[Ts]] | None:
        rows = self.fetch(conn)
        return rows[0] if rows else None

    def sql(self, conn: Conn) -> str:
        return conn.render_plan(self._plan, nodes_of(self._exprs))


@final
class Projection(Generic[R]):
    """A named projection bound to a row dataclass."""

    __slots__ = ("_ctor", "_exprs", "_plan")

    def __init__(
        self,
        plan: Plan,
        exprs: tuple[ExprBase, ...],
        ctor: type[R],
    ) -> None:
        self._plan: Plan = plan
        self._exprs: tuple[ExprBase, ...] = exprs
        self._ctor: type[R] = ctor

    def fetch(self, conn: Conn) -> list[R]:
        rows = conn.run_plan(self._plan, nodes_of(self._exprs))
        ctor = cast("Any", self._ctor)
        return [cast("R", ctor(*row)) for row in rows]

    def fetch_one(self, conn: Conn) -> R | None:
        rows = self.fetch(conn)
        return rows[0] if rows else None

    def sql(self, conn: Conn) -> str:
        return conn.render_plan(self._plan, nodes_of(self._exprs))


@final
class Raw(Generic[R]):
    """LAYER B ESCAPE HATCH: verbatim SQL, typed only at its boundary.

    The SQL text is NOT checked (that is the point -- `ON CONFLICT`, CTEs,
    window functions and vendor syntax live here). What IS checked is the row
    type: `Raw[R].fetch()` returns `list[R]`, and `R` is a dataclass whose
    fields must line up positionally with the statement's select list.

    Marking is deliberate: every use of `Raw` in application code is a place
    where the generator's guarantees stop.
    """

    __slots__ = ("_ctor", "_params", "_sql")

    def __init__(
        self,
        sql: str,
        row: type[R],
        params: Mapping[str, object] | None = None,
    ) -> None:
        self._sql: str = sql
        self._ctor: type[R] = row
        self._params: Mapping[str, object] = params if params is not None else {}

    def fetch(self, conn: Conn) -> list[R]:
        rows = conn.run_raw(self._sql, self._params)
        ctor = cast("Any", self._ctor)
        return [cast("R", ctor(*row)) for row in rows]

    def fetch_one(self, conn: Conn) -> R | None:
        rows = self.fetch(conn)
        return rows[0] if rows else None

    def sql(self) -> str:
        return self._sql


class QueryBase:
    """Shared machinery for every generated query shape."""

    __slots__ = ("_plan",)

    def __init__(self, plan: Plan) -> None:
        self._plan: Plan = plan

    # NOTE: no column namespaces are declared here on purpose. Each concrete
    # generated shape lists exactly the aliases it makes visible -- putting
    # `orders` on the base would make `from_users().orders` type-check against
    # a query with no orders table in it.

    def where(self, *preds: Pred) -> Self:
        return type(self)(self._plan.with_wheres(tuple(p.sql_ir() for p in preds)))

    def group_by(self, *exprs: ExprBase) -> Self:
        # `ExprBase`, not `Expr[Any]`: GROUP BY does not care about the value
        # type, and `Expr[Any]` would put an `Any` in a PUBLIC signature --
        # which `proof_positive.assert_no_any_in_public_signatures()` rejects.
        # `Expr[object]` is not an option either: `Expr` is invariant in `T`.
        return type(self)(self._plan.with_groups(tuple(e.sql_ir() for e in exprs)))

    def order_by(self, *orders: Order) -> Self:
        return type(self)(self._plan.with_orders(tuple(o.step() for o in orders)))

    def limit(self, n: int) -> Self:
        return type(self)(self._plan.with_limit(n))

    def offset(self, n: int) -> Self:
        return type(self)(self._plan.with_offset(n))

    def for_update(self) -> Self:
        """SELECT ... FOR UPDATE. A no-op on backends that lack it."""
        return type(self)(self._plan.with_for_update())

    def plan(self) -> Plan:
        """Escape hatch for generated writers (DELETE/UPDATE reuse the WHERE)."""
        return self._plan

    # -- projection overloads (arity 1..13) ---------------------------------

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

    def select(self, *exprs: ExprBase) -> Select[Unpack[tuple[Any, ...]]]:
        # Implementation signature. Callers never see it -- pyright resolves
        # every call against the overloads above. Arity 14+ matches no overload
        # and is a type error (documented cap, no `Any` fallback rung).
        return Select(self._plan, exprs)
