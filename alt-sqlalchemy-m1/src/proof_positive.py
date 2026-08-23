"""POSITIVE evidence: every claim C1..C8 that must TYPE-CHECK and reveal the
type the design promises.

Run `pyright --project pyrightconfig.positive.json`. Expected: 0 errors, and
one `information: Type of ... is "..."` line per `reveal_type` below.

The probes live inside functions that are never called, so pyright evaluates
them statically while the module stays importable (and cheap) at runtime --
`assert_no_sqlalchemy_leakage()` is meant to actually run.
"""

from __future__ import annotations

import datetime as _dt
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import reveal_type
from uuid import UUID

from facade import (
    ORDER_ITEMS,
    ORDERS,
    Col,
    Conn,
    Expr,
    OrderStatus,
    OrderSummaryRow,
    Pred,
    Select,
    UserStatus,
    all_of,
    any_of,
    from_orders,
)

# ===========================================================================
# C1 -- operand type checking (the SQLAlchemy `== Any` hole does not exist)
# ===========================================================================


def c1_operand_checking() -> None:
    # enum column vs. the matching generated enum member: OK
    reveal_type(ORDERS.status.eq(OrderStatus.PAID))  # Pred

    # column vs. column, SAME value type, DIFFERENT tables: OK.
    # (Decimal on both sides -- `orders.total` and `order_items.unit_price`.)
    reveal_type(ORDERS.total.eq(ORDER_ITEMS.unit_price))  # Pred

    # a plain literal of the right type is fine too
    reveal_type(ORDERS.total.gte(Decimal("10.00")))  # Pred
    reveal_type(ORDERS.version.eq(3))  # Pred
    reveal_type(ORDERS.created_at.lt(_dt.datetime(2026, 1, 1)))  # Pred

    # `in_` constrains the element type of the sequence
    reveal_type(ORDERS.status.in_([OrderStatus.PAID, OrderStatus.SHIPPED]))  # Pred

    # users.status is a DIFFERENT generated enum; matching it is fine
    q = from_orders().join_users()
    reveal_type(q.users.status.eq(UserStatus.ACTIVE))  # Pred


# ===========================================================================
# C2 -- arithmetic: result type is fixed by the COLUMN, operand order is moot
# ===========================================================================


def c2_arithmetic() -> None:
    # int literal is accepted as a multiplier for a Decimal column, and the
    # result stays Decimal -- the literal never widens the result type.
    reveal_type(ORDERS.total.mul(3))  # Expr[Decimal]
    reveal_type(ORDERS.total.add(Decimal("1.50")))  # Expr[Decimal]
    reveal_type(ORDERS.total.sub(1))  # Expr[Decimal]

    # an int column yields Expr[int]
    reveal_type(ORDERS.version.mul(2))  # Expr[int]

    # chaining keeps the column's type: Expr[Decimal] fed back as the operand
    reveal_type(ORDERS.total.mul(ORDER_ITEMS.unit_price))  # Expr[Decimal]

    # There is NO expression form in which the literal comes first: arithmetic
    # exists only as a method on a NumCol, so `3 * ORDERS.total` does not parse
    # as a query at all (see proof_negative N6). jOOQ's operand-order problem
    # is therefore structurally absent rather than merely handled.


# ===========================================================================
# C3 -- join nullability, precomputed per table alias
# ===========================================================================


def c3_join_nullability() -> None:
    base = from_orders()
    reveal_type(base)  # QOrders     -- has NO `.users` (see proof_negative N2)

    # LEFT JOIN: the users alias switches to its `| None` variant wholesale
    ln = base.left_join_users()
    reveal_type(ln)  # QOrdersUsersN
    reveal_type(ln.users.email)  # Col[Literal['users'], str | None]
    reveal_type(ln.users.id)  # Col[Literal['users'], UUID | None]

    # ... and that flows into the row tuple
    sel_n = ln.select(ORDERS.id, ln.users.email)
    reveal_type(sel_n)  # Select[UUID, str | None]

    # INNER JOIN: same column, still non-optional
    li = base.join_users()
    reveal_type(li)  # QOrdersUsersI
    reveal_type(li.users.email)  # Col[Literal['users'], str]
    sel_i = li.select(ORDERS.id, li.users.email)
    reveal_type(sel_i)  # Select[UUID, str]

    # `where()` preserves the join shape (so `.users` survives chaining)
    reveal_type(ln.where(ORDERS.status.eq(OrderStatus.PAID)))  # QOrdersUsersN

    # two joins compose, each contributing its own nullability state
    mixed = base.join_users().left_join_order_items()
    reveal_type(mixed.users.email)  # Col[Literal['users'], str]
    reveal_type(mixed.order_items.quantity)  # NumCol[Literal['order_items'], int|None]


# ===========================================================================
# C4 -- wide projection: 12 columns, no Any collapse
# ===========================================================================


def c4_wide_projection() -> None:
    q = from_orders().left_join_users().left_join_order_items()
    wide = q.select(
        ORDERS.id,
        ORDERS.user_id,
        ORDERS.status,
        ORDERS.total,
        ORDERS.version,
        ORDERS.created_at,
        q.users.id,
        q.users.email,
        q.users.status,
        q.users.created_at,
        q.order_items.quantity,
        q.order_items.unit_price,
    )
    # 12 distinct element types, all precise
    reveal_type(wide)

    # and the fetched row keeps every position
    conn = Conn("sqlite://")
    rows = wide.fetch(conn)
    reveal_type(rows)
    first = rows[0]
    reveal_type(first[0])  # UUID
    reveal_type(first[7])  # str | None
    reveal_type(first[11])  # Decimal | None

    # 13 columns -- the highest emitted overload -- still precise
    thirteen = q.select(
        ORDERS.id,
        ORDERS.user_id,
        ORDERS.status,
        ORDERS.total,
        ORDERS.version,
        ORDERS.created_at,
        q.users.id,
        q.users.email,
        q.users.status,
        q.users.created_at,
        q.order_items.order_id,
        q.order_items.quantity,
        q.order_items.unit_price,
    )
    reveal_type(thirteen)


# ===========================================================================
# C5 -- named dataclass projection
# ===========================================================================


def c5_named_projection() -> None:
    q = from_orders().left_join_users()
    proj = q.select_order_summary()
    reveal_type(proj)  # Projection[OrderSummaryRow]

    conn = Conn("sqlite://")
    rows = proj.fetch(conn)
    reveal_type(rows)  # list[OrderSummaryRow]

    row = rows[0]
    reveal_type(row.order_id)  # UUID
    reveal_type(row.user_email)  # str | None  <- LEFT JOIN provenance
    reveal_type(row.total)  # Decimal


# ===========================================================================
# C6 -- predicate composition
# ===========================================================================


@dataclass(frozen=True, slots=True)
class OrderFilter:
    status: OrderStatus | None = None
    since: _dt.datetime | None = None
    min_total: Decimal | None = None


def search_orders(f: OrderFilter) -> Pred:
    """The dynamic-search shape from the design doc, verbatim."""
    preds: list[Pred] = []
    if f.status is not None:
        preds.append(ORDERS.status.eq(f.status))
    if f.since is not None:
        preds.append(ORDERS.created_at.gte(f.since))
    if f.min_total is not None:
        preds.append(ORDERS.total.gte(f.min_total))
    return all_of(*preds)


def c6_composition() -> None:
    reveal_type(search_orders(OrderFilter()))  # Pred

    # empty all_of() is legal and means "no-op" (jOOQ noCondition)
    reveal_type(all_of())  # Pred
    reveal_type(any_of())  # Pred

    # preds nest arbitrarily and stay first-class values
    combined = all_of(
        search_orders(OrderFilter(status=OrderStatus.PAID)),
        any_of(
            ORDERS.total.gte(Decimal("100")),
            ORDERS.version.gt(1),
        ),
    )
    reveal_type(combined)  # Pred

    # and feed straight back into a query, in any quantity
    q = from_orders().left_join_users().where(combined, ORDERS.version.gte(1))
    reveal_type(q)  # QOrdersUsersN

    # a list of Pred is a perfectly ordinary value to pass around
    bag: list[Pred] = [ORDERS.status.ne(OrderStatus.CANCELLED)]
    reveal_type(all_of(*bag))  # Pred


# ===========================================================================
# C7 -- not_null() escape hatch via a self-type
# ===========================================================================


def c7_not_null() -> None:
    q = from_orders().left_join_users()

    nullable_email = q.users.email
    reveal_type(nullable_email)  # Col[Literal['users'], str | None]

    # the self-type `Col[_TTn, _TN | None]` binds _TN := str
    unnulled = nullable_email.not_null()
    reveal_type(unnulled)  # Col[Literal['users'], str]

    # non-str types work the same way
    reveal_type(q.users.id.not_null())  # Col[Literal['users'], UUID]
    reveal_type(q.users.status.not_null())  # Col[Literal['users'], UserStatus]

    # and it propagates into the row type: position 1 is `str`, not `str | None`
    sel = q.select(ORDERS.id, unnulled)
    reveal_type(sel)  # Select[UUID, str]

    # having un-nulled it, `eq` now rejects None-typed operands and the value
    # type is the bare one
    reveal_type(unnulled.eq("a@b.example"))  # Pred


# ===========================================================================
# C8 -- zero type leakage
# ===========================================================================


def c8_no_leakage() -> None:
    conn = Conn("sqlite://")
    q = from_orders().left_join_users()

    # fetch() is exactly list[tuple[...]] with per-position types
    s2 = q.select(ORDERS.id, q.users.email)
    reveal_type(s2.fetch(conn))  # list[tuple[UUID, str | None]]

    s1 = q.select(ORDERS.total)
    reveal_type(s1.fetch(conn))  # list[tuple[Decimal]]

    # predicate results, query results, and rendered SQL are all concrete
    reveal_type(ORDERS.status.eq(OrderStatus.PAID))  # Pred
    reveal_type(q.where(all_of()))  # QOrdersUsersN
    reveal_type(s2.sql(conn))  # str
    reveal_type(conn.statement_cache_stats())  # tuple[int, int]

    # the generic containers themselves carry no Any
    sel: Select[UUID, str | None] = s2
    reveal_type(sel)
    col: Col[str, Decimal] = ORDERS.total
    reveal_type(col)
    e: Expr[Decimal] = ORDERS.total.mul(2)
    reveal_type(e)
    summary: list[OrderSummaryRow] = q.select_order_summary().fetch(conn)
    reveal_type(summary)


# ---------------------------------------------------------------------------
# C8 (runtime half): the public modules must not re-export SQLAlchemy.
# Called from demo_runtime.py so it actually executes.
# ---------------------------------------------------------------------------


def assert_no_sqlalchemy_leakage() -> list[str]:
    """Fail if any public module hands out a SQLAlchemy object.

    Checks two things per public module:
      1. no attribute is a class/module/function defined under `sqlalchemy`
      2. `sqlalchemy` is not itself bound as a module attribute
    """
    import importlib
    import inspect

    public_modules = ["facade", "_catalog", "_ir"]
    findings: list[str] = []

    for modname in public_modules:
        mod = importlib.import_module(modname)
        for attr in dir(mod):
            if attr.startswith("__"):
                continue
            value = getattr(mod, attr)
            origin: str | None = None
            if inspect.ismodule(value):
                origin = getattr(value, "__name__", None)
            elif inspect.isclass(value) or inspect.isfunction(value):
                origin = getattr(value, "__module__", None)
            if origin is not None and origin.split(".")[0] == "sqlalchemy":
                findings.append(f"{modname}.{attr} -> {origin}")

    # `_runtime` is the designated quarantine and is EXPECTED to hold them.
    runtime = importlib.import_module("_runtime")
    quarantined = sum(
        1
        for a in dir(runtime)
        if not a.startswith("__")
        and getattr(
            getattr(runtime, a), "__module__", getattr(getattr(runtime, a), "__name__", "")
        )
        .split(".")[0]
        == "sqlalchemy"
    )
    print(
        f"[leakage] public modules {public_modules}: {len(findings)} SQLAlchemy "
        f"names found (want 0); _runtime quarantine holds {quarantined}",
        file=sys.stderr,
    )
    for f in findings:
        print(f"[leakage] LEAK {f}", file=sys.stderr)
    return findings


def assert_no_any_in_public_signatures() -> list[str]:
    """Fail if a PUBLIC callable in `facade` mentions `Any` in its signature.

    Overload *implementation* signatures are exempt: pyright never resolves a
    call against them, and `typing.get_type_hints` sees only the last def.
    """
    import inspect

    import facade

    offenders: list[str] = []
    public = [n for n in facade.__all__]
    for name in public:
        obj = getattr(facade, name)
        targets: list[tuple[str, object]] = []
        if inspect.isclass(obj):
            for mname, m in inspect.getmembers(obj, inspect.isfunction):
                if not mname.startswith("_"):
                    targets.append((f"{name}.{mname}", m))
        elif inspect.isfunction(obj):
            targets.append((name, obj))
        for label, fn in targets:
            if label.endswith(".select"):
                continue  # overload implementation, unreachable from callers
            try:
                sig = str(inspect.signature(fn))  # pyright: ignore[reportArgumentType]
            except (TypeError, ValueError):  # pragma: no cover
                continue
            if "Any" in sig:
                offenders.append(f"{label}{sig}")
    print(
        f"[any-check] {len(offenders)} public signatures mention Any (want 0)",
        file=sys.stderr,
    )
    for o in offenders:
        print(f"[any-check] ANY {o}", file=sys.stderr)
    return offenders
