"""M5-owned workaround for ONE defect M5's integration found in M2's runtime.

The defect
----------
`ORDERS.status.eq(OrderStatus.PAID)` type-checks, and M2's proofs assert its
type -- but executing it against PostgreSQL fails:

    sqlalchemy.exc.ProgrammingError: (psycopg.errors.UndefinedFunction)
    operator does not exist: order_status = character varying
    LINE 3: WHERE orders.status = $1::VARCHAR

`altsa_runtime._backend._Builder._bind` builds every literal as
`bindparam(key, value=_encode(v))` with no explicit type. `_encode` turns an
enum member into its `str` value first, so SQLAlchemy infers `VARCHAR` from the
value and the psycopg dialect renders the cast. PostgreSQL has no
`enum = varchar` operator, so the statement is rejected at plan time.

M2 already solved this for the SET side of an UPDATE -- `_Builder.bind_typed`
exists precisely because `SET status = $1::VARCHAR` failed the same way -- and
its docstring says "On the WHERE side the `=` operator already does this". That
is not true when the right operand is ALREADY a `BindParameter`: SQLAlchemy
propagates a column's type to a bare Python value, not to a pre-built bind.

M2's evidence does not catch it because every enum comparison in `proofs/` is
type-level (`reveal_type`) and `patterns/patterns_pg.py`, which does execute,
never filters on an enum column -- see `evidence/run_patterns_pg.log`.

The fix in M2 is one branch in `_Builder.expr`: for a `BinOpNode` whose left
side is a `ColRefNode` on an enum column, bind the right side with
`bind_typed(..., _table(t).c[n].type)`, exactly as the UPDATE path does.
M2 is audited and read-only for M5, so M5 works around it instead. Friction F11.

The workaround
--------------
Bind the enum's value as a `str` SUBCLASS. SQLAlchemy's `_resolve_value_to_type`
looks the Python type up in an exact-match dict, so a subclass of `str` resolves
to `NullType`; a `NullType` bind is rendered as a bare `%(p0)s` with no cast,
and psycopg sends `str` with an unspecified OID, which lets PostgreSQL infer
`order_status` from the comparison. That is the same reason M3's generated
`SET status = %(new_status)s` has always worked.

Nothing is monkey-patched. `Expr` is part of `altsa_runtime`'s public surface
and `ParamNode` is its IR's literal node; this module only builds one.

The `cast` below is the honest cost: the returned expression is NOT an
`Expr[OrderStatus]` at runtime (it holds a `str`), and the assertion is what
lets it be passed to `Col[_TOrders, OrderStatus].eq`. It is one unsound line,
in one file, with this comment on it -- which is the shape an escape hatch is
supposed to have.
"""

from __future__ import annotations

import enum
from typing import cast, final

from altsa_runtime import Expr
from altsa_runtime.ir import ParamNode


@final
class _UntypedText(str):
    """A `str` SQLAlchemy's value->type map does not contain, so a bind built
    from it gets `NullType` and is rendered without a `::VARCHAR` cast."""

    __slots__ = ()


def enum_operand[E: enum.Enum](member: E) -> Expr[E]:
    """An enum literal that survives the trip to PostgreSQL.

    Use instead of passing the member directly:

        ORDERS.status.eq(enum_operand(OrderStatus.PAID))   # works
        ORDERS.status.eq(OrderStatus.PAID)                 # ProgrammingError
    """
    value = member.value
    if not isinstance(value, str):  # pragma: no cover - EC schema enums are str
        raise TypeError(f"{member!r} does not have a str value")
    return cast("Expr[E]", Expr(ParamNode(_UntypedText(value))))
