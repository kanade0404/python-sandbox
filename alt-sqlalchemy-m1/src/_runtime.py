"""SQLAlchemy Core backend. THE ONLY MODULE THAT IMPORTS SQLAlchemy.

`facade.py` reaches this through three names -- `RuntimeConn`, `execute_plan`,
`register_enum` -- none of which mention a SQLAlchemy type in their signatures.
`proof_positive.assert_no_sqlalchemy_leakage()` checks that at runtime.

Statement memoisation (design doc fig. 2): a `Plan` + output-node list is
reduced to a shape key with literals elided, and the built Core `Select` is
cached under it. Repeating a query with different bound values is a cache HIT,
so the ~54us statement-construction cost is paid once per shape, not per call.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, cast, final
from uuid import UUID

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    and_,
    bindparam,
    create_engine,
    or_,
    select,
)
from sqlalchemy.sql import ColumnElement, FromClause, Select
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.types import TypeEngine

from _catalog import JOIN_BY_TARGET, TABLES
from _ir import (
    BinOpNode,
    BoolNode,
    ColRefNode,
    InNode,
    Node,
    ParamNode,
    Plan,
    UnaryNode,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

# ---------------------------------------------------------------------------
# catalog -> Core tables
# ---------------------------------------------------------------------------

_METADATA: Final[MetaData] = MetaData()

_KIND_TO_SA: Final[dict[str, TypeEngine[Any]]] = {
    "uuid": String(36),
    "text": String(),
    "enum": String(32),
    "timestamptz": DateTime(),
    "numeric": Numeric(12, 2, asdecimal=True),
    "int": Integer(),
}


def _build_tables() -> dict[str, Table]:
    out: dict[str, Table] = {}
    for tdef in TABLES.values():
        cols = [
            Column(
                c.name,
                _KIND_TO_SA[c.kind],
                primary_key=c.primary_key,
                nullable=not c.not_null,
            )
            for c in tdef.columns
        ]
        out[tdef.name] = Table(tdef.name, _METADATA, *cols)
    return out


_TABLES_SA: Final[dict[str, Table]] = {}


def _tables() -> dict[str, Table]:
    if not _TABLES_SA:
        _TABLES_SA.update(_build_tables())
    return _TABLES_SA


_COLUMN_KIND: Final[dict[tuple[str, str], str]] = {
    (t.name, c.name): c.kind for t in TABLES.values() for c in t.columns
}

_ENUM_TYPES: Final[dict[tuple[str, str], type[enum.Enum]]] = {}


def register_enum(table: str, column: str, cls: type[enum.Enum]) -> None:
    """Called by the generated facade for each enum-typed column."""
    _ENUM_TYPES[table, column] = cls


# ---------------------------------------------------------------------------
# IR -> Core expression, with params collected in traversal order
# ---------------------------------------------------------------------------


@final
class _Builder:
    """Walks the IR once, emitting Core elements and numbering bind params.

    Param names are assigned in traversal order, so the same shape always
    yields the same names -- that is what makes the memo safe to reuse with a
    fresh value dict.
    """

    def __init__(self) -> None:
        self.n: int = 0

    def _next_param(self) -> str:
        key = f"p{self.n}"
        self.n += 1
        return key

    def _bind(self, value: object) -> BindParameter[object]:
        return bindparam(self._next_param(), value=_encode(value))

    def expr(self, node: Node) -> ColumnElement[Any]:
        tabs = _tables()
        match node:
            case ColRefNode(table=t, name=n):
                return cast("ColumnElement[Any]", tabs[t].c[n])
            case ParamNode(value=v):
                return self._bind(v)
            case BinOpNode(op=op, left=lhs, right=rhs):
                left = self.expr(lhs)
                right = self.expr(rhs)
                match op:
                    case "=":
                        return left == right
                    case "!=":
                        return left != right
                    case ">":
                        return left > right
                    case ">=":
                        return left >= right
                    case "<":
                        return left < right
                    case "<=":
                        return left <= right
                    case "+":
                        return left + right
                    case "-":
                        return left - right
                    case "*":
                        return left * right
                    case _:  # pragma: no cover
                        raise ValueError(f"unknown binary op {op!r}")
            case BoolNode(op=op, parts=parts):
                if not parts:
                    # Empty all_of() is a no-op; empty any_of() matches nothing.
                    return and_(True) if op == "AND" else or_(False)
                built = [self.expr(p) for p in parts]
                return and_(*built) if op == "AND" else or_(*built)
            case UnaryNode(op=op, operand=inner):
                built = self.expr(inner)
                return built.is_(None) if op == "ISNULL" else built.is_not(None)
            case InNode(operand=inner, values=values):
                built = self.expr(inner)
                binds = [self._bind(v) for v in values]
                return built.in_(binds)


def _encode(value: object) -> object:
    """Python value -> storage value."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return cast("object", value.value)
    return value


def _decoder_for(node: Node) -> Callable[[object], object]:
    """Pick the read-side conversion for one output expression.

    A bare column uses its catalog kind. An arithmetic expression inherits the
    decoder of its left operand -- which is exactly the facade's static rule
    that `NumCol[T].mul(...)` yields `Expr[T]`, so the two agree by
    construction rather than by coincidence.
    """
    match node:
        case ColRefNode(table=t, name=n):
            kind = _COLUMN_KIND.get((t, n))
            if kind == "uuid":
                return lambda v: None if v is None else UUID(str(v))
            if kind == "enum":
                cls = _ENUM_TYPES.get((t, n))
                if cls is not None:
                    return lambda v: None if v is None else cls(v)
                return lambda v: v
            if kind == "numeric":
                return lambda v: None if v is None else Decimal(str(v))
            return lambda v: v
        case BinOpNode(left=lhs):
            return _decoder_for(lhs)
        case _:
            return lambda v: v


# ---------------------------------------------------------------------------
# plan -> Core Select (memoised by shape)
# ---------------------------------------------------------------------------


def _shape_key(plan: Plan, outs: tuple[Node, ...]) -> str:
    return plan.shape() + "||" + ",".join(o.shape() for o in outs)


def _build_select(plan: Plan, outs: tuple[Node, ...]) -> tuple[Select[Any], int]:
    tabs = _tables()
    b = _Builder()

    out_cols = [b.expr(o) for o in outs]

    frm: FromClause = tabs[plan.root]
    for step in plan.joins:
        edge = JOIN_BY_TARGET[step.target]
        lt, lc, rt, rc = edge.on
        onclause = tabs[lt].c[lc] == tabs[rt].c[rc]
        frm = frm.join(tabs[step.target], onclause, isouter=(step.kind == "left"))

    stmt = select(*out_cols).select_from(frm)
    for w in plan.wheres:
        stmt = stmt.where(b.expr(w))
    return stmt, b.n


def _collect_values(plan: Plan, outs: tuple[Node, ...]) -> dict[str, object]:
    """Re-walk in the SAME order the builder used, harvesting literals."""
    n = 0
    vals: dict[str, object] = {}

    def walk(node: Node) -> None:
        nonlocal n
        match node:
            case ColRefNode():
                return
            case ParamNode(value=v):
                vals[f"p{n}"] = _encode(v)
                n += 1
            case BinOpNode(left=lhs, right=rhs):
                walk(lhs)
                walk(rhs)
            case BoolNode(parts=parts):
                for p in parts:
                    walk(p)
            case UnaryNode(operand=inner):
                walk(inner)
            case InNode(operand=inner, values=values):
                walk(inner)
                for v in values:
                    vals[f"p{n}"] = _encode(v)
                    n += 1

    for o in outs:
        walk(o)
    for w in plan.wheres:
        walk(w)
    return vals


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------


@final
class RuntimeConn:
    __slots__ = ("_conn", "_echo", "_engine", "_hits", "_memo", "_misses")

    def __init__(self, dsn: str, *, echo_to: list[str] | None = None) -> None:
        self._engine: Engine = create_engine(dsn)
        self._conn: Connection | None = None
        self._memo: dict[str, Select[Any]] = {}
        self._hits: int = 0
        self._misses: int = 0
        self._echo: list[str] | None = echo_to

    def open(self) -> None:
        self._conn = self._engine.connect()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require(self) -> Connection:
        if self._conn is None:
            raise RuntimeError("connection is not open")
        return self._conn

    def cache_stats(self) -> tuple[int, int]:
        return self._hits, self._misses

    def _stmt(self, plan: Plan, outs: tuple[Node, ...]) -> Select[Any]:
        key = _shape_key(plan, outs)
        cached = self._memo.get(key)
        if cached is not None:
            self._hits += 1
            return cached
        self._misses += 1
        stmt, _ = _build_select(plan, outs)
        self._memo[key] = stmt
        return stmt

    def render(self, plan: Plan, outs: tuple[Node, ...]) -> str:
        stmt = self._stmt(plan, outs)
        return str(stmt.compile(self._engine)).replace("\n", " ")

    def run(self, plan: Plan, outs: tuple[Node, ...]) -> list[tuple[object, ...]]:
        stmt = self._stmt(plan, outs)
        values = _collect_values(plan, outs)
        decoders = [_decoder_for(o) for o in outs]
        if self._echo is not None:
            self._echo.append(
                str(stmt.compile(self._engine)).replace("\n", " ") + f"  -- {values}"
            )
        result = self._require().execute(stmt, values)
        return [
            tuple(d(v) for d, v in zip(decoders, row, strict=True))
            for row in result.all()
        ]

    def execute_ddl(self, statement: str) -> None:
        from sqlalchemy import text

        self._require().execute(text(statement))
        self._require().commit()

    def insert_row(self, table: str, values: dict[str, object]) -> None:
        tab = _tables()[table]
        encoded = {k: _encode(v) for k, v in values.items()}
        self._require().execute(tab.insert().values(**encoded))
        self._require().commit()

    def create_all(self) -> None:
        _tables()
        _METADATA.create_all(self._engine)


def execute_plan(
    conn: RuntimeConn, plan: Plan, outs: tuple[Node, ...]
) -> list[tuple[object, ...]]:
    return conn.run(plan, outs)
