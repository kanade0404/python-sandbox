"""SQLAlchemy Core backend. THE ONLY MODULE IN THE PROJECT THAT IMPORTS SQLAlchemy.

`conn.py` reaches this through `RuntimeConn` alone, and no method of
`RuntimeConn` mentions a SQLAlchemy type in its signature.

SCHEMA-INDEPENDENT: everything is driven off `catalog.REGISTRY`, which the
GENERATED facade populates at import time.

Statement memoisation: a `Plan` + output-node list is reduced to a shape key
with literals elided, and the built Core `Select` is cached under it. Repeating
a query with different bound values is a cache HIT, so statement-construction
cost is paid once per shape, not per call.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, cast, final
from uuid import UUID

import sqlalchemy
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Time,
    Uuid,
    and_,
    bindparam,
    create_engine,
    delete,
    func,
    insert,
    not_,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.sql import ColumnElement, FromClause, Select
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.types import TypeEngine

from .catalog import REGISTRY, ColumnSpec
from .ir import (
    Assignment,
    BinOpNode,
    BoolNode,
    ColRefNode,
    FuncNode,
    InNode,
    Node,
    ParamNode,
    Plan,
    UnaryNode,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

# ---------------------------------------------------------------------------
# registry -> Core tables
# ---------------------------------------------------------------------------

_METADATA: Final[MetaData] = MetaData()
_TABLES_SA: Final[dict[str, Table]] = {}


def _sa_type(spec: ColumnSpec) -> TypeEngine[Any]:
    match spec.kind:
        case "uuid":
            return Uuid(as_uuid=True)
        case "text":
            return String(spec.length) if spec.length else String()
        case "enum":
            if spec.enum_name:
                return Enum(*spec.enum_values, name=spec.enum_name)
            return Enum(*spec.enum_values, native_enum=False)
        case "timestamptz":
            return DateTime(timezone=True)
        case "timestamp":
            return DateTime()
        case "date":
            return Date()
        case "time":
            return Time()
        case "numeric":
            return Numeric(spec.precision or 38, spec.scale or 0, asdecimal=True)
        case "int":
            return Integer()
        case "float":
            return sqlalchemy.Float()
        case "bool":
            return Boolean()
        case "json":
            return JSON()
        case "bytes":
            return LargeBinary()
        case "array":
            item = ColumnSpec(
                name=spec.name, kind=spec.item_kind or "text", not_null=True
            )
            return ARRAY(_sa_type(item))
        case _:
            return String()


def _table(name: str) -> Table:
    cached = _TABLES_SA.get(name)
    if cached is not None:
        return cached
    tdef = REGISTRY.tables.get(name)
    if tdef is None:
        raise KeyError(f"table {name!r} is not registered; import the facade first")
    cols = [
        Column(
            c.name,
            _sa_type(c),
            primary_key=c.primary_key,
            nullable=not c.not_null,
        )
        for c in tdef.columns
    ]
    tab = Table(tdef.name, _METADATA, *cols)
    _TABLES_SA[name] = tab
    return tab


# ---------------------------------------------------------------------------
# encode / decode
# ---------------------------------------------------------------------------


def _encode(value: object) -> object:
    """Python value -> storage value."""
    if isinstance(value, enum.Enum):
        return cast("object", value.value)
    return value


_IDENTITY: Final[Callable[[object], object]] = lambda v: v  # noqa: E731


def _decoder_for_kind(kind: str, table: str, column: str) -> Callable[[object], object]:
    if kind == "uuid":
        return lambda v: None if v is None else (v if isinstance(v, UUID) else UUID(str(v)))
    if kind == "enum":
        cls = REGISTRY.enums.get((table, column))
        if cls is None:
            return _IDENTITY
        return lambda v: None if v is None else cls(v)
    if kind == "numeric":
        return lambda v: None if v is None else Decimal(str(v))
    return _IDENTITY


def _decoder_for(node: Node) -> Callable[[object], object]:
    """Pick the read-side conversion for one output expression.

    A bare column uses its catalog kind. An arithmetic expression inherits the
    decoder of its left operand -- which is exactly the facade's static rule
    that `NumCol[T].mul(...)` yields `Expr[T]`, so the two agree by
    construction rather than by coincidence. Aggregates likewise inherit from
    their argument, except COUNT which is always an int.
    """
    match node:
        case ColRefNode(table=t, name=n):
            tdef = REGISTRY.tables.get(t)
            if tdef is None:
                return _IDENTITY
            for c in tdef.columns:
                if c.name == n:
                    return _decoder_for_kind(c.kind, t, n)
            return _IDENTITY
        case BinOpNode(left=lhs):
            return _decoder_for(lhs)
        case FuncNode(name=fname, args=args):
            if fname == "count" or not args:
                return _IDENTITY
            return _decoder_for(args[0])
        case _:
            return _IDENTITY


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

    def bind_typed(
        self, value: object, type_: TypeEngine[Any]
    ) -> BindParameter[object]:
        """A bind param that carries the TARGET COLUMN's SQL type.

        Needed on the SET side of an UPDATE: there is no comparison operator to
        propagate the column's type, so `SET status = $1` would otherwise be
        emitted as `$1::VARCHAR` and PostgreSQL rejects assigning varchar to an
        ENUM column. On the WHERE side the `=` operator already does this.
        """
        return bindparam(self._next_param(), value=_encode(value), type_=type_)

    def expr(self, node: Node) -> ColumnElement[Any]:
        match node:
            case ColRefNode(table=t, name=n):
                return cast("ColumnElement[Any]", _table(t).c[n])
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
                    case "/":
                        return left / right
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
                if op == "ISNULL":
                    return built.is_(None)
                if op == "NOTNULL":
                    return built.is_not(None)
                return not_(built)
            case InNode(operand=inner, values=values):
                built = self.expr(inner)
                binds = [self._bind(v) for v in values]
                return built.in_(binds)
            case FuncNode(name=fname, args=args, star=star):
                fn = getattr(func, fname)
                if star:
                    return cast("ColumnElement[Any]", fn())
                built_args = [self.expr(a) for a in args]
                return cast("ColumnElement[Any]", fn(*built_args))


def _walk_values(node: Node, vals: dict[str, object], counter: list[int]) -> None:
    """Re-walk in the SAME order the builder used, harvesting literals."""
    match node:
        case ColRefNode():
            return
        case ParamNode(value=v):
            vals[f"p{counter[0]}"] = _encode(v)
            counter[0] += 1
        case BinOpNode(left=lhs, right=rhs):
            _walk_values(lhs, vals, counter)
            _walk_values(rhs, vals, counter)
        case BoolNode(parts=parts):
            for p in parts:
                _walk_values(p, vals, counter)
        case UnaryNode(operand=inner):
            _walk_values(inner, vals, counter)
        case InNode(operand=inner, values=values):
            _walk_values(inner, vals, counter)
            for v in values:
                vals[f"p{counter[0]}"] = _encode(v)
                counter[0] += 1
        case FuncNode(args=args):
            for a in args:
                _walk_values(a, vals, counter)


# ---------------------------------------------------------------------------
# plan -> Core Select (memoised by shape)
# ---------------------------------------------------------------------------


def _shape_key(plan: Plan, outs: tuple[Node, ...]) -> str:
    return plan.shape() + "||" + ",".join(o.shape() for o in outs)


def _plan_nodes(plan: Plan, outs: tuple[Node, ...]) -> list[Node]:
    """Every node that can carry a literal, in builder traversal order."""
    nodes: list[Node] = [*outs, *plan.wheres, *plan.groups]
    nodes.extend(s.node for s in plan.orders)
    return nodes


def _build_select(plan: Plan, outs: tuple[Node, ...]) -> Select[Any]:
    b = _Builder()
    out_cols = [b.expr(o) for o in outs]

    frm: FromClause = _table(plan.root)
    for step in plan.joins:
        edge = REGISTRY.edges.get(step.edge)
        if edge is None:
            raise KeyError(f"edge {step.edge!r} is not registered")
        conds = [
            _table(lt).c[lc] == _table(rt).c[rc] for (lt, lc, rt, rc) in edge.on
        ]
        frm = frm.join(
            _table(step.target), and_(*conds), isouter=(step.kind == "left")
        )

    stmt = select(*out_cols).select_from(frm)
    for w in plan.wheres:
        stmt = stmt.where(b.expr(w))
    if plan.groups:
        stmt = stmt.group_by(*[b.expr(g) for g in plan.groups])
    if plan.orders:
        terms = [
            b.expr(s.node).desc() if s.desc else b.expr(s.node).asc()
            for s in plan.orders
        ]
        stmt = stmt.order_by(*terms)
    if plan.limit is not None:
        stmt = stmt.limit(plan.limit)
    if plan.offset is not None:
        stmt = stmt.offset(plan.offset)
    if plan.for_update:
        stmt = stmt.with_for_update()
    return stmt


def _collect_values(plan: Plan, outs: tuple[Node, ...]) -> dict[str, object]:
    vals: dict[str, object] = {}
    counter = [0]
    for node in _plan_nodes(plan, outs):
        _walk_values(node, vals, counter)
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

    def commit(self) -> None:
        self._require().commit()

    def rollback(self) -> None:
        self._require().rollback()

    def _stmt(self, plan: Plan, outs: tuple[Node, ...]) -> Select[Any]:
        key = _shape_key(plan, outs)
        cached = self._memo.get(key)
        if cached is not None:
            self._hits += 1
            return cached
        self._misses += 1
        stmt = _build_select(plan, outs)
        self._memo[key] = stmt
        return stmt

    def _echo_line(self, line: str) -> None:
        if self._echo is not None:
            self._echo.append(line)

    def render(self, plan: Plan, outs: tuple[Node, ...]) -> str:
        stmt = self._stmt(plan, outs)
        return str(stmt.compile(self._engine)).replace("\n", " ")

    def run(self, plan: Plan, outs: tuple[Node, ...]) -> list[tuple[object, ...]]:
        stmt = self._stmt(plan, outs)
        values = _collect_values(plan, outs)
        decoders = [_decoder_for(o) for o in outs]
        self._echo_line(
            str(stmt.compile(self._engine)).replace("\n", " ") + f"  -- {values}"
        )
        result = self._require().execute(stmt, values)
        return [
            tuple(d(v) for d, v in zip(decoders, row, strict=True))
            for row in result.all()
        ]

    # -- writers -------------------------------------------------------------

    def insert_values(self, table: str, values: Mapping[str, object]) -> int:
        tab = _table(table)
        encoded = {k: _encode(v) for k, v in values.items()}
        stmt = insert(tab).values(**encoded)
        self._echo_line(str(stmt.compile(self._engine)).replace("\n", " ") + f"  -- {encoded}")
        result = self._require().execute(stmt)
        return result.rowcount

    def update_rows(
        self, table: str, assignments: Sequence[Assignment], where: Node | None
    ) -> int:
        tab = _table(table)
        b = _Builder()
        sets: dict[str, ColumnElement[Any]] = {}
        for a in assignments:
            if isinstance(a.value, ParamNode):
                sets[a.column] = b.bind_typed(a.value.value, tab.c[a.column].type)
            else:
                sets[a.column] = b.expr(a.value)
        stmt = update(tab).values(**sets)
        if where is not None:
            stmt = stmt.where(b.expr(where))
        vals: dict[str, object] = {}
        counter = [0]
        for a in assignments:
            _walk_values(a.value, vals, counter)
        if where is not None:
            _walk_values(where, vals, counter)
        self._echo_line(str(stmt.compile(self._engine)).replace("\n", " ") + f"  -- {vals}")
        result = self._require().execute(stmt, vals)
        return result.rowcount

    def delete_rows(self, table: str, where: Node | None) -> int:
        tab = _table(table)
        b = _Builder()
        stmt = delete(tab)
        vals: dict[str, object] = {}
        if where is not None:
            stmt = stmt.where(b.expr(where))
            _walk_values(where, vals, [0])
        self._echo_line(str(stmt.compile(self._engine)).replace("\n", " ") + f"  -- {vals}")
        result = self._require().execute(stmt, vals)
        return result.rowcount

    # -- layer B -------------------------------------------------------------

    def run_raw(
        self, sql: str, params: Mapping[str, object]
    ) -> list[tuple[object, ...]]:
        encoded = {k: _encode(v) for k, v in params.items()}
        self._echo_line(sql.replace("\n", " ") + f"  -- RAW {encoded}")
        result = self._require().execute(text(sql), encoded)
        return [tuple(row) for row in result.all()]

    def exec_raw(self, sql: str, params: Mapping[str, object]) -> int:
        encoded = {k: _encode(v) for k, v in params.items()}
        self._echo_line(sql.replace("\n", " ") + f"  -- RAW {encoded}")
        result = self._require().execute(text(sql), encoded)
        return result.rowcount

    # -- fixtures ------------------------------------------------------------

    def execute_ddl(self, statement: str) -> None:
        self._require().execute(text(statement))
        self._require().commit()

    def create_all(self) -> None:
        for name in sorted(REGISTRY.tables):
            _table(name)
        _METADATA.create_all(self._engine)
