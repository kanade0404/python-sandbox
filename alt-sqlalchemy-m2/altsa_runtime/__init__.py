"""alt-SQLAlchemy M2 runtime -- the small, STABLE, HAND-WRITTEN half.

Decision rule for what lives here vs. what a generator emits:

    schema-INDEPENDENT  -> runtime (this package)
    schema-DEPENDENT    -> generated

So `Col`, `Expr`, `Pred`, `all_of`, aggregates, `Conn`, the shape-memoised
execution path, and the 13 `select()` overloads (pure ARITY machinery -- they
never mention a table) are here. Table classes, `Cols`/`ColsN` namespaces,
enums, per-shape query classes, join combinators and typed writers are emitted.

A generated facade re-exports the public names from this package so that
application code has ONE import.
"""

from __future__ import annotations

from .catalog import (
    ColumnSpec,
    EdgeSpec,
    TableSpec,
    register_edge,
    register_enum,
    register_table,
)
from .conn import Conn
from .expr import (
    Assign,
    Col,
    Expr,
    NumCol,
    Order,
    Pred,
    all_of,
    any_of,
    coalesce,
    count_,
    count_star,
    max_,
    min_,
    not_,
    sum_,
)
from .ir import Plan
from .query import Projection, QueryBase, Raw, Select

__all__ = [
    "Assign",
    "Col",
    "ColumnSpec",
    "Conn",
    "EdgeSpec",
    "Expr",
    "NumCol",
    "Order",
    "Plan",
    "Pred",
    "Projection",
    "QueryBase",
    "Raw",
    "Select",
    "TableSpec",
    "all_of",
    "any_of",
    "coalesce",
    "count_",
    "count_star",
    "max_",
    "min_",
    "not_",
    "register_edge",
    "register_enum",
    "register_table",
    "sum_",
]
